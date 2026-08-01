export type ConversationEvent = Record<string, unknown> & {
  schema_version: number;
  session_id: string;
  request_id: string;
  sequence: number;
  timestamp: string | number;
  type: string;
  payload?: Record<string, unknown>;
};

export function isConversationEvent(value: unknown): value is ConversationEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as Record<string, unknown>;
  return typeof event.schema_version === "number"
    && typeof event.session_id === "string"
    && typeof event.request_id === "string"
    && Number.isInteger(event.sequence)
    && typeof event.type === "string"
    && (typeof event.timestamp === "string" || typeof event.timestamp === "number")
    && (event.payload === undefined || (typeof event.payload === "object" && event.payload !== null));
}

export type ConversationTerminal = "completed" | "cancelled" | "failed" | null;

export type ConversationUiSnapshot = {
  sessionId: string;
  activeRequestId: string | null;
  lastSequence: number;
  response: string;
  activity: string;
  terminal: ConversationTerminal;
};

const TERMINAL_BY_TYPE: Record<string, Exclude<ConversationTerminal, null>> = {
  "request.completed": "completed",
  "request.cancelled": "cancelled",
  "request.failed": "failed",
};

export class ConversationEventReducer {
  private snapshot: ConversationUiSnapshot;

  constructor(sessionId: string, afterSequence = 0) {
    this.snapshot = {
      sessionId,
      activeRequestId: null,
      lastSequence: Math.max(0, afterSequence),
      response: "",
      activity: "idle",
      terminal: null,
    };
  }

  accept(event: ConversationEvent): ConversationUiSnapshot {
    if (event.session_id !== this.snapshot.sessionId) return this.current();
    if (!Number.isInteger(event.sequence) || event.sequence <= this.snapshot.lastSequence) {
      return this.current();
    }

    this.snapshot.lastSequence = event.sequence;
    const requestId = event.request_id || "";
    if (event.type === "request.accepted") {
      this.snapshot.activeRequestId = requestId;
      this.snapshot.response = "";
      this.snapshot.activity = "understanding";
      this.snapshot.terminal = null;
      return this.current();
    }

    if (event.type === "response.delta") {
      if (requestId === this.snapshot.activeRequestId) {
        this.snapshot.response += String(event.payload?.text ?? event.text ?? "");
        this.snapshot.activity = "speaking";
      }
      return this.current();
    }

    if (event.type.startsWith("activity.") && requestId === this.snapshot.activeRequestId) {
      this.snapshot.activity = event.type.slice("activity.".length);
      return this.current();
    }

    const terminal = TERMINAL_BY_TYPE[event.type];
    if (terminal && requestId === this.snapshot.activeRequestId) {
      this.snapshot.activeRequestId = null;
      this.snapshot.activity = terminal;
      this.snapshot.terminal = terminal;
    }
    return this.current();
  }

  current(): ConversationUiSnapshot {
    return { ...this.snapshot };
  }
}
