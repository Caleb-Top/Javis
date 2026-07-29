import { RequestQueue, createRequestId } from "./requestQueue";
import { resolveBackendEndpoints } from "./backendEndpoints.ts";
import { runtimeStateCoordinator } from "../state/RuntimeStateCoordinator";

export type BackendClientOptions = {
  onConnection?(snapshot: ConnectionSnapshot): void;
  onEvent?(event: BackendEvent): void;
};

export type BackendEvent = Record<string, unknown> & { type?: string; request_id?: string; text?: string };

export type ConnectionSnapshot = {
  http: boolean;
  websocket: boolean;
};

export type BackendClient = {
  connect(): void;
  send(text: string): string | null;
  sendVoice(audioBase64: string): string | null;
  checkBackendHealth(): Promise<boolean>;
  get<T>(path: string, signal?: AbortSignal): Promise<T>;
  post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T>;
  confirm(confirmed: boolean): void;
  queueSize(): number;
  connectionSnapshot(): ConnectionSnapshot;
};

type Card = {
  role: string;
  text: string;
};

export function createBackendClient(options: BackendClientOptions): BackendClient {
  const endpoints = resolveBackendEndpoints();
  let ws: WebSocket | null = null;
  const cards: Card[] = [];
  const queue = new RequestQueue();
  const RECONNECT_DELAYS = [1000, 2000, 3000, 5000, 8000, 15000];
  let reconnectAttempt = 0;
  let reconnectTimer = 0;
  let connection: ConnectionSnapshot = { http: false, websocket: false };

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
        runtimeStateCoordinator.signal({ source: "websocket", state: "offline", timestamp: Date.now(), detail: "后端离线" });
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

  function flushQueue(): void {
    queue.drain().forEach((request) => sendWire(request.payload));
  }

  function connect(): void {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    ws = new WebSocket(`${endpoints.websocket}/ws`);
    ws.onopen = () => {
      reconnectAttempt = 0;
      publishConnection({ websocket: true });
      runtimeStateCoordinator.signal({ source: "websocket", state: "idle", timestamp: Date.now(), detail: "Javis 已待命" });
      flushQueue();
    };
    ws.onclose = () => {
      publishConnection({ websocket: false });
      runtimeStateCoordinator.signal({ source: "websocket", state: "offline", timestamp: Date.now(), detail: "连接已断开" });
      scheduleReconnect();
    };
    ws.onerror = () => runtimeStateCoordinator.signal({ source: "websocket", state: "error", timestamp: Date.now(), detail: "连接发生错误" });
    ws.onmessage = (event) => {
      let msg: BackendEvent;
      try { msg = JSON.parse(event.data) as BackendEvent; } catch { return; }
      const requestId = String(msg.request_id || "");
      const state = msg.type === "tool_start" ? "executing"
        : msg.type === "confirm_required" ? "blocked"
          : msg.type === "error" ? "error"
            : msg.type === "text_delta" ? "speaking"
              : "thinking";
      if (msg.type === "thinking" || msg.type === "tool_start" || msg.type === "confirm_required" || msg.type === "error" || msg.type === "text_delta") {
        const detail = msg.text || msg.detail || msg.tool || msg.name || msg.type;
        runtimeStateCoordinator.signal({
          source: "websocket",
          state,
          timestamp: Date.now(),
          requestId,
          detail: String(detail || ""),
        });
      }
      if (msg.type === "done") {
        runtimeStateCoordinator.signal({
          source: "websocket",
          state: "idle",
          timestamp: Date.now(),
          requestId,
          detail: String(msg.detail || msg.text || "任务完成"),
          terminal: true,
        });
      }
      options.onEvent?.(msg);
    };
  }

  function send(text: string): string | null {
    const clean = text.trim();
    if (!clean) return null;
    const requestId = createRequestId();
    cards.push({ role: "user", text: clean });
    runtimeStateCoordinator.signal({ source: "ui", state: "thinking", timestamp: Date.now(), requestId, detail: "正在理解" });
    const payload = {
      type: "message",
      payload: {
        text: clean,
        request_id: requestId,
        session_id: "javis-app-live",
        interaction_mode: "live",
        recent_cards: cards.slice(-80)
      }
    };
    if (!sendWire(payload)) {
      queue.enqueue({ requestId, dedupeKey: requestId, payload, risk: "normal", createdAt: Date.now() });
      connect();
    }
    return requestId;
  }

  function sendVoice(audioBase64: string): string | null {
    const clean = audioBase64.trim();
    if (!clean) return null;
    const requestId = createRequestId();
    runtimeStateCoordinator.signal({ source: "voice", state: "thinking", timestamp: Date.now(), requestId, detail: "正在转写" });
    const payload = {
      "type": "voice",
      payload: {
        audio: clean,
        request_id: requestId,
        session_id: "javis-app-live",
        interaction_mode: "live",
        recent_cards: cards.slice(-80)
      }
    };
    if (!sendWire(payload)) {
      runtimeStateCoordinator.signal({ source: "voice", state: "offline", timestamp: Date.now(), requestId, detail: "语音不会在断线后自动重发" });
      return null;
    }
    return requestId;
  }

  function confirm(confirmed: boolean): void {
    sendWire({ type: "confirm", payload: { confirmed } });
  }

  return {
    connect,
    send,
    sendVoice,
    checkBackendHealth,
    get,
    post,
    confirm,
    queueSize: () => queue.size(),
    connectionSnapshot: () => ({ ...connection }),
  };
}
