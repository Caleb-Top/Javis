import { RequestQueue, createRequestId } from "./requestQueue.ts";
import { resolveBackendEndpoints } from "./backendEndpoints.ts";
import { runtimeStateCoordinator } from "../state/RuntimeStateCoordinator.ts";

export type BackendClientOptions = {
  sessionId: string;
  afterSequence?(): number;
  onConnection?(snapshot: ConnectionSnapshot): void;
  onEvent?(event: BackendEvent): void;
  requestTimeouts?: Partial<RequestTimeouts>;
};

export type RequestTimeouts = {
  acceptedMs: number;
  firstResponseMs: number;
  overallMs: number;
};

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
};

const RECONNECT_DELAYS = [1000, 2000, 3000, 5000, 8000, 15000];
const DEFAULT_REQUEST_TIMEOUTS: RequestTimeouts = {
  acceptedMs: 3000,
  firstResponseMs: 15000,
  overallMs: 60000,
};
const MAX_RETIRED_REQUEST_IDS = 128;

type RequestTimeoutPhase = "accepted" | "first-response" | "overall";

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

  const endpoints = resolveBackendEndpoints();
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

  function publishConnection(patch: Partial<ConnectionSnapshot>): void {
    connection = { ...connection, ...patch };
    options.onConnection?.({ ...connection });
  }

  async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${endpoints.http}${path}`, { cache: "no-store", signal });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json() as Promise<T>;
  }

  async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(`${endpoints.http}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json() as Promise<T>;
  }

  async function checkBackendHealth(): Promise<boolean> {
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
    window.clearTimeout(reconnectTimer);
    const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)];
    reconnectAttempt += 1;
    reconnectTimer = window.setTimeout(connect, delay);
  }

  function sendWire(payload: unknown): boolean {
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
    if (watchdogs.firstResponseReceived) return;
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
      const detail = payload.detail || payload.text || payload.tool || msg.text || msg.detail || type;
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
  }

  function connect(): void {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

    ws = new WebSocket(`${endpoints.websocket}/ws`);
    ws.onopen = () => {
      reconnectAttempt = 0;
      publishConnection({ websocket: true });
      runtimeStateCoordinator.signal({
        source: "websocket",
        state: "idle",
        timestamp: Date.now(),
        detail: "Javis 已待命",
      });
      attachConversation();
      flushQueue();
    };
    ws.onclose = () => {
      publishConnection({ websocket: false });
      runtimeStateCoordinator.signal({
        source: "websocket",
        state: "offline",
        timestamp: Date.now(),
        detail: "连接已断开",
      });
      scheduleReconnect();
    };
    ws.onerror = () => runtimeStateCoordinator.signal({
      source: "websocket",
      state: "error",
      timestamp: Date.now(),
      detail: "连接发生错误",
    });
    ws.onmessage = (event) => {
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
    if (!currentRequestId) return false;
    const requestId = currentRequestId;
    retireRequest(requestId);
    clearRequestWatchdogs(requestId);
    currentRequestId = null;
    pendingApprovalId = "";
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

  return {
    connect,
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
  };
}
