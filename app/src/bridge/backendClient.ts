import { RequestQueue, createRequestId } from "./requestQueue.ts";
import { resolveBackendEndpoints } from "./backendEndpoints.ts";
import type { RuntimeAccessScope } from "./runtimeAccess.ts";
import { runtimeStateCoordinator } from "../state/RuntimeStateCoordinator.ts";

export type BackendClientOptions = {
  sessionId: string;
  backendOrigin?: string;
  afterSequence?(): number;
  onConnection?(snapshot: ConnectionSnapshot): void;
  onEvent?(event: BackendEvent): void;
  requestTimeouts?: Partial<RequestTimeouts>;
  runtimeAccessToken?(scope: RuntimeAccessScope): string;
};

export type RequestTimeouts = {
  acceptedMs: number;
  firstResponseMs: number;
  overallMs: number;
};

export type RequestTimeoutPhase = "accepted" | "first-response" | "overall";

export type RequestReliabilityPhase =
  | "idle"
  | "waiting-accepted"
  | "waiting-first-response"
  | "responding"
  | "terminal"
  | "timed-out";

export type ConnectionReliabilityPhase =
  | "idle"
  | "connecting"
  | "connected"
  | "error"
  | "reconnecting"
  | "recovered";

export type BackendReliabilitySnapshot = {
  schemaVersion: 1;
  revision: number;
  processStartedAt: number;
  updatedAt: number;
  timeoutCounts: Record<RequestTimeoutPhase, number>;
  lastTimeoutPhase: RequestTimeoutPhase | null;
  requestPhase: RequestReliabilityPhase;
  connectionPhase: ConnectionReliabilityPhase;
  connectionCount: number;
  disconnectCount: number;
  connectionErrorCount: number;
  reconnectCount: number;
  recoveryCount: number;
  reconnectStreak: number;
};

export type BackendReliabilityListener = (snapshot: BackendReliabilitySnapshot) => void;

export type BackendEvent = Record<string, unknown> & {
  type?: string;
  request_id?: string;
  text?: string;
  sequence?: number;
  payload?: Record<string, unknown>;
};

export type ConnectionSnapshot = {
  http: boolean;
  websocket: boolean;
};

export type BackendClient = {
  connect(): void;
  dispose(): void;
  refreshRuntimeAccess(): void;
  send(text: string): string | null;
  sendVoice(audioBase64: string): string | null;
  cancel(reason?: string): boolean;
  checkBackendHealth(): Promise<boolean>;
  get<T>(path: string, signal?: AbortSignal): Promise<T>;
  post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T>;
  confirm(confirmed: boolean, approvalId?: string): boolean;
  activeRequestId(): string | null;
  sessionId(): string;
  queueSize(): number;
  connectionSnapshot(): ConnectionSnapshot;
  reliabilitySnapshot(): BackendReliabilitySnapshot;
  subscribeReliability(listener: BackendReliabilityListener): () => void;
};

const RECONNECT_DELAYS = [1000, 2000, 3000, 5000, 8000, 15000];
const DEFAULT_REQUEST_TIMEOUTS: RequestTimeouts = {
  acceptedMs: 3000,
  firstResponseMs: 15000,
  overallMs: 60000,
};
const MAX_RETIRED_REQUEST_IDS = 128;

type RequestWatchdogs = {
  acceptedTimer: number | null;
  firstResponseTimer: number | null;
  overallTimer: number | null;
  accepted: boolean;
  firstResponseReceived: boolean;
};

function isRequestTerminal(type: string): boolean {
  return type === "request.completed"
    || type === "request.cancelled"
    || type === "request.failed";
}

export function createBackendClient(options: BackendClientOptions): BackendClient {
  const sessionId = options.sessionId.trim();
  if (!sessionId) throw new Error("BackendClient requires a conversation session id");

  const endpoints = resolveBackendEndpoints(options.backendOrigin);
  const requestTimeouts: RequestTimeouts = {
    ...DEFAULT_REQUEST_TIMEOUTS,
    ...options.requestTimeouts,
  };
  const queue = new RequestQueue();
  const requestWatchdogs = new Map<string, RequestWatchdogs>();
  const retiredRequestIds = new Set<string>();
  let ws: WebSocket | null = null;
  let reconnectAttempt = 0;
  let reconnectTimer = 0;
  let connection: ConnectionSnapshot = { http: false, websocket: false };
  let currentRequestId: string | null = null;
  let pendingApprovalId = "";
  let disposed = false;
  const reliabilityListeners = new Set<BackendReliabilityListener>();
  const processStartedAt = Date.now();
  let reliability: BackendReliabilitySnapshot = {
    schemaVersion: 1,
    revision: 0,
    processStartedAt,
    updatedAt: processStartedAt,
    timeoutCounts: {
      accepted: 0,
      "first-response": 0,
      overall: 0,
    },
    lastTimeoutPhase: null,
    requestPhase: "idle",
    connectionPhase: "idle",
    connectionCount: 0,
    disconnectCount: 0,
    connectionErrorCount: 0,
    reconnectCount: 0,
    recoveryCount: 0,
    reconnectStreak: 0,
  };

  function reliabilitySnapshot(): BackendReliabilitySnapshot {
    return {
      ...reliability,
      timeoutCounts: { ...reliability.timeoutCounts },
    };
  }

  function publishReliability(
    patch: Partial<Omit<BackendReliabilitySnapshot, "schemaVersion" | "revision" | "processStartedAt" | "updatedAt">>,
  ): void {
    reliability = {
      ...reliability,
      ...patch,
      timeoutCounts: patch.timeoutCounts
        ? { ...patch.timeoutCounts }
        : reliability.timeoutCounts,
      revision: reliability.revision + 1,
      updatedAt: Date.now(),
    };
    reliabilityListeners.forEach((listener) => {
      try {
        listener(reliabilitySnapshot());
      } catch {
        // Diagnostics must never interrupt the request or connection lifecycle.
      }
    });
  }

  function subscribeReliability(listener: BackendReliabilityListener): () => void {
    reliabilityListeners.add(listener);
    listener(reliabilitySnapshot());
    return () => reliabilityListeners.delete(listener);
  }

  function publishConnection(patch: Partial<ConnectionSnapshot>): void {
    connection = { ...connection, ...patch };
    options.onConnection?.({ ...connection });
  }

  function runtimeScope(path: string): RuntimeAccessScope {
    if (path.startsWith("/api/voice/playback/")) return "playback";
    if (path === "/api/voice/diagnostics" || path.startsWith("/api/diagnostics/")) {
      return "diagnostics.read";
    }
    if (path.startsWith("/api/life/")) return "life.read";
    if (path.startsWith("/api/voice/")) return "voice.capture";
    return "conversation";
  }

  function runtimeHeaders(path: string): Record<string, string> {
    const token = options.runtimeAccessToken?.(runtimeScope(path)) ?? "";
    return token ? { "X-Javis-Runtime-Capability": token } : {};
  }

  async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${endpoints.http}${path}`, {
      cache: "no-store",
      headers: runtimeHeaders(path),
      signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json() as Promise<T>;
  }

  async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${endpoints.http}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...runtimeHeaders(path),
      },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json() as Promise<T>;
  }

  async function checkBackendHealth(): Promise<boolean> {
    if (disposed) return false;
    try {
      await get<Record<string, unknown>>("/api/status");
      publishConnection({ http: true });
      return true;
    } catch {
      publishConnection({ http: false });
      if (!connection.websocket) {
        runtimeStateCoordinator.signal({
          source: "websocket",
          state: "offline",
          timestamp: Date.now(),
          detail: "后端离线",
        });
      }
      return false;
    }
  }

  function scheduleReconnect(): void {
    if (disposed) return;
    window.clearTimeout(reconnectTimer);
    const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)];
    reconnectAttempt += 1;
    publishReliability({
      connectionPhase: "reconnecting",
      reconnectCount: reliability.reconnectCount + 1,
      reconnectStreak: reconnectAttempt,
    });
    reconnectTimer = window.setTimeout(connect, delay);
  }

  function sendWire(payload: unknown): boolean {
    if (disposed) return false;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(payload));
    return true;
  }

  function clearRequestWatchdogs(requestId: string): void {
    const watchdogs = requestWatchdogs.get(requestId);
    if (!watchdogs) return;
    if (watchdogs.acceptedTimer !== null) window.clearTimeout(watchdogs.acceptedTimer);
    if (watchdogs.firstResponseTimer !== null) window.clearTimeout(watchdogs.firstResponseTimer);
    if (watchdogs.overallTimer !== null) window.clearTimeout(watchdogs.overallTimer);
    requestWatchdogs.delete(requestId);
  }

  function retireRequest(requestId: string): void {
    if (!requestId) return;
    retiredRequestIds.delete(requestId);
    retiredRequestIds.add(requestId);
    if (retiredRequestIds.size <= MAX_RETIRED_REQUEST_IDS) return;
    const oldestRequestId = retiredRequestIds.values().next().value;
    if (oldestRequestId) retiredRequestIds.delete(oldestRequestId);
  }

  function ignoreRetiredEvent(msg: BackendEvent): boolean {
    const requestId = String(msg.request_id || "");
    if (!requestId || !retiredRequestIds.has(requestId)) return false;
    if (isRequestTerminal(String(msg.type || ""))) {
      retiredRequestIds.delete(requestId);
      return false;
    }
    return true;
  }

  function normalizeLifecycleEvent(msg: BackendEvent): BackendEvent {
    if (!isRequestTerminal(String(msg.type || ""))) return msg;
    if (String(msg.request_id || "") || !currentRequestId) return msg;
    return { ...msg, request_id: currentRequestId };
  }

  function timeoutRequest(requestId: string, phase: RequestTimeoutPhase): void {
    if (!requestWatchdogs.has(requestId)) return;
    retireRequest(requestId);
    clearRequestWatchdogs(requestId);
    queue.cancel(requestId);
    publishReliability({
      timeoutCounts: {
        ...reliability.timeoutCounts,
        [phase]: reliability.timeoutCounts[phase] + 1,
      },
      lastTimeoutPhase: phase,
      requestPhase: "timed-out",
    });
    sendWire({
      type: "conversation.cancel",
      payload: {
        session_id: sessionId,
        request_id: requestId,
        reason: `${phase} timeout`,
        protocol_version: 2,
      },
    });
    if (currentRequestId === requestId) {
      currentRequestId = null;
      pendingApprovalId = "";
    }
    const error = phase === "accepted"
      ? "Request acknowledgement timed out"
      : phase === "first-response"
        ? "First response timed out"
        : "Request timed out";
    runtimeStateCoordinator.signal({
      source: "websocket",
      state: "error",
      timestamp: Date.now(),
      requestId,
      detail: error,
      terminal: true,
    });
    options.onEvent?.({
      type: "request.failed",
      local: true,
      request_id: requestId,
      payload: { error, phase },
    });
  }

  function startRequestWatchdogs(requestId: string): void {
    const watchdogs: RequestWatchdogs = {
      acceptedTimer: null,
      firstResponseTimer: null,
      overallTimer: null,
      accepted: false,
      firstResponseReceived: false,
    };
    requestWatchdogs.set(requestId, watchdogs);
    publishReliability({ requestPhase: "waiting-accepted" });
    watchdogs.acceptedTimer = window.setTimeout(
      () => timeoutRequest(requestId, "accepted"),
      requestTimeouts.acceptedMs,
    );
    watchdogs.overallTimer = window.setTimeout(
      () => timeoutRequest(requestId, "overall"),
      requestTimeouts.overallMs,
    );
  }

  function markRequestAccepted(requestId: string): void {
    const watchdogs = requestWatchdogs.get(requestId);
    if (!watchdogs || watchdogs.accepted) return;
    watchdogs.accepted = true;
    if (watchdogs.acceptedTimer !== null) {
      window.clearTimeout(watchdogs.acceptedTimer);
      watchdogs.acceptedTimer = null;
    }
    if (watchdogs.firstResponseReceived) {
      publishReliability({ requestPhase: "responding" });
      return;
    }
    publishReliability({ requestPhase: "waiting-first-response" });
    watchdogs.firstResponseTimer = window.setTimeout(
      () => timeoutRequest(requestId, "first-response"),
      requestTimeouts.firstResponseMs,
    );
  }

  function markFirstResponse(requestId: string): void {
    const watchdogs = requestWatchdogs.get(requestId);
    if (!watchdogs || watchdogs.firstResponseReceived) return;
    watchdogs.firstResponseReceived = true;
    if (watchdogs.firstResponseTimer !== null) {
      window.clearTimeout(watchdogs.firstResponseTimer);
      watchdogs.firstResponseTimer = null;
    }
    publishReliability({ requestPhase: "responding" });
  }

  function flushQueue(): void {
    queue.drain().forEach((request) => sendWire(request.payload));
  }

  function attachConversation(): void {
    sendWire({
      type: "conversation.attach",
      payload: {
        session_id: sessionId,
        after_sequence: Math.max(0, options.afterSequence?.() ?? 0),
        protocol_version: 2,
      },
    });
  }

  function applyRuntimeEvent(msg: BackendEvent): void {
    const type = String(msg.type || "");
    const requestId = String(msg.request_id || "");
    const payload = msg.payload ?? {};
    const activeRequestId = currentRequestId;
    const lifecycleRequestId = requestId || currentRequestId || "";
    if (type === "request.accepted" && requestId === currentRequestId) {
      markRequestAccepted(requestId);
    }
    if (type === "response.delta" && requestId) markFirstResponse(requestId);
    if (type === "approval.required") pendingApprovalId = String(payload.approval_id || "");

    const terminal = isRequestTerminal(type);
    if (terminal && lifecycleRequestId) clearRequestWatchdogs(lifecycleRequestId);
    if (terminal && (!requestId || requestId === currentRequestId)) {
      currentRequestId = null;
      pendingApprovalId = "";
    }

    const state = type === "activity.tool_started" ? "executing"
      : type === "approval.required" ? "blocked"
        : type === "request.failed" ? "error"
          : type === "response.delta" ? "speaking"
            : "thinking";
    const staleFailure = type === "request.failed"
      && Boolean(activeRequestId)
      && Boolean(requestId)
      && requestId !== activeRequestId;
    if (!staleFailure && (type.startsWith("activity.") || type === "approval.required" || type === "request.failed" || type === "response.delta")) {
      const detail = payload.error || payload.detail || payload.text || payload.tool || msg.text || msg.detail || type;
      runtimeStateCoordinator.signal({
        source: "websocket",
        state,
        timestamp: Date.now(),
        requestId,
        detail: String(detail || ""),
      });
    }
    if (type === "request.completed" || type === "request.cancelled") {
      runtimeStateCoordinator.signal({
        source: "websocket",
        state: "idle",
        timestamp: Date.now(),
        requestId,
        detail: String(payload.detail || payload.reason || "任务完成"),
        terminal: true,
      });
    }
    if (terminal && (!requestId || requestId === activeRequestId)) {
      publishReliability({ requestPhase: "terminal" });
    }
  }

  function connect(): void {
    if (disposed) return;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    const token = options.runtimeAccessToken?.("conversation") ?? "";
    const socket = token
      ? new WebSocket(`${endpoints.websocket}/ws`, [
        "javis-runtime-v1",
        `javis-capability.${token}`,
      ])
      : new WebSocket(`${endpoints.websocket}/ws`);
    ws = socket;
    publishReliability({
      connectionPhase: reconnectAttempt > 0 ? "reconnecting" : "connecting",
    });
    socket.onopen = () => {
      if (disposed || ws !== socket) return;
      const recovered = reconnectAttempt > 0;
      reconnectAttempt = 0;
      publishConnection({ websocket: true });
      publishReliability({
        connectionPhase: recovered ? "recovered" : "connected",
        connectionCount: reliability.connectionCount + 1,
        recoveryCount: reliability.recoveryCount + (recovered ? 1 : 0),
        reconnectStreak: 0,
      });
      runtimeStateCoordinator.signal({
        source: "websocket",
        state: "idle",
        timestamp: Date.now(),
        detail: "Javis 已待命",
      });
      attachConversation();
      flushQueue();
    };
    socket.onclose = () => {
      if (disposed || ws !== socket) return;
      publishConnection({ websocket: false });
      publishReliability({
        disconnectCount: reliability.disconnectCount + 1,
      });
      runtimeStateCoordinator.signal({
        source: "websocket",
        state: "offline",
        timestamp: Date.now(),
        detail: "连接已断开",
      });
      scheduleReconnect();
    };
    socket.onerror = () => {
      if (disposed || ws !== socket) return;
      publishReliability({
        connectionPhase: "error",
        connectionErrorCount: reliability.connectionErrorCount + 1,
      });
      runtimeStateCoordinator.signal({
        source: "websocket",
        state: "error",
        timestamp: Date.now(),
        detail: "连接发生错误",
      });
    };
    socket.onmessage = (event) => {
      if (disposed || ws !== socket) return;
      let msg: BackendEvent;
      try {
        msg = JSON.parse(event.data) as BackendEvent;
      } catch {
        return;
      }
      msg = normalizeLifecycleEvent(msg);
      if (ignoreRetiredEvent(msg)) return;
      applyRuntimeEvent(msg);
      options.onEvent?.(msg);
    };
  }

  function enqueueOrSend(requestId: string, payload: unknown): void {
    if (sendWire(payload)) return;
    queue.enqueue({
      requestId,
      dedupeKey: requestId,
      payload,
      risk: "normal",
      createdAt: Date.now(),
    });
    connect();
  }

  function send(text: string): string | null {
    if (disposed) return null;
    const clean = text.trim();
    if (!clean) return null;
    if (currentRequestId) {
      retireRequest(currentRequestId);
      queue.cancel(currentRequestId);
      clearRequestWatchdogs(currentRequestId);
      pendingApprovalId = "";
    }
    const requestId = createRequestId();
    currentRequestId = requestId;
    startRequestWatchdogs(requestId);
    runtimeStateCoordinator.signal({
      source: "ui",
      state: "thinking",
      timestamp: Date.now(),
      requestId,
      detail: "正在理解",
    });
    enqueueOrSend(requestId, {
      type: "conversation.message",
      payload: {
        text: clean,
        request_id: requestId,
        idempotency_key: requestId,
        session_id: sessionId,
        interaction_mode: "live",
        protocol_version: 2,
      },
    });
    return requestId;
  }

  function sendVoice(audioBase64: string): string | null {
    if (disposed) return null;
    const clean = audioBase64.trim();
    if (!clean) return null;
    const requestId = createRequestId();
    currentRequestId = requestId;
    runtimeStateCoordinator.signal({
      source: "voice",
      state: "thinking",
      timestamp: Date.now(),
      requestId,
      detail: "正在转写",
    });
    const sent = sendWire({
      type: "voice",
      payload: {
        audio: clean,
        request_id: requestId,
        idempotency_key: requestId,
        session_id: sessionId,
        interaction_mode: "live",
        protocol_version: 2,
      },
    });
    if (!sent) {
      currentRequestId = null;
      runtimeStateCoordinator.signal({
        source: "voice",
        state: "offline",
        timestamp: Date.now(),
        requestId,
        detail: "语音不会在断线后自动重发",
      });
      return null;
    }
    return requestId;
  }

  function cancel(reason = "user interrupt"): boolean {
    if (disposed) return false;
    if (!currentRequestId) return false;
    const requestId = currentRequestId;
    retireRequest(requestId);
    clearRequestWatchdogs(requestId);
    currentRequestId = null;
    pendingApprovalId = "";
    publishReliability({ requestPhase: "terminal" });
    if (queue.cancel(requestId)) {
      return true;
    }
    const payload = {
      type: "conversation.cancel",
      payload: {
        session_id: sessionId,
        request_id: requestId,
        reason,
        protocol_version: 2,
      },
    };
    if (!sendWire(payload)) {
      queue.enqueue({
        requestId: `cancel-${requestId}`,
        dedupeKey: `cancel-${requestId}`,
        payload,
        risk: "normal",
        createdAt: Date.now(),
      });
      connect();
    }
    return true;
  }

  function confirm(confirmed: boolean, approvalId = pendingApprovalId): boolean {
    if (disposed) return false;
    if (!currentRequestId || !approvalId) return false;
    return sendWire({
      type: "conversation.confirm",
      payload: {
        session_id: sessionId,
        request_id: currentRequestId,
        approval_id: approvalId,
        confirmed,
        protocol_version: 2,
      },
    });
  }

  function dispose(): void {
    if (disposed) return;
    disposed = true;
    window.clearTimeout(reconnectTimer);
    reconnectTimer = 0;
    reconnectAttempt = 0;
    Array.from(requestWatchdogs.keys()).forEach(clearRequestWatchdogs);
    queue.drain();
    retiredRequestIds.clear();
    currentRequestId = null;
    pendingApprovalId = "";
    reliabilityListeners.clear();
    connection = { ...connection, websocket: false };

    const socket = ws;
    ws = null;
    if (!socket) return;
    socket.onopen = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.onmessage = null;
    if (socket.readyState === WebSocket.CLOSED) return;
    try {
      socket.close();
    } catch {
      // Disposal is best-effort and must remain idempotent.
    }
  }

  function refreshRuntimeAccess(): void {
    if (disposed) return;
    window.clearTimeout(reconnectTimer);
    reconnectTimer = 0;
    reconnectAttempt = 0;
    const socket = ws;
    ws = null;
    if (socket) {
      socket.onopen = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      try {
        if (socket.readyState !== WebSocket.CLOSED) socket.close();
      } catch {
        // Reauthorization does not depend on the old close handshake.
      }
    }
    publishConnection({ websocket: false });
    connect();
  }

  return {
    connect,
    dispose,
    refreshRuntimeAccess,
    send,
    sendVoice,
    cancel,
    checkBackendHealth,
    get,
    post,
    confirm,
    activeRequestId: () => currentRequestId,
    sessionId: () => sessionId,
    queueSize: () => queue.size(),
    connectionSnapshot: () => ({ ...connection }),
    reliabilitySnapshot,
    subscribeReliability,
  };
}
