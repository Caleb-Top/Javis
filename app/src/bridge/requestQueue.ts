export const MAX_QUEUE_SIZE = 32;

export type QueueRisk = "normal" | "high";

export type QueuedRequest = {
  requestId: string;
  dedupeKey: string;
  payload: unknown;
  risk: QueueRisk;
  createdAt: number;
};

export class RequestQueue {
  private items: QueuedRequest[] = [];

  enqueue(request: QueuedRequest): boolean {
    if (request.risk === "high") return false;
    const existing = this.items.findIndex((item) => item.dedupeKey === request.dedupeKey);
    if (existing >= 0) this.items.splice(existing, 1);
    this.items.push(request);
    if (this.items.length > MAX_QUEUE_SIZE) this.items.shift();
    return true;
  }

  cancel(requestId: string): boolean {
    const before = this.items.length;
    this.items = this.items.filter((item) => item.requestId !== requestId);
    return this.items.length !== before;
  }

  drain(): QueuedRequest[] {
    const pending = [...this.items];
    this.items = [];
    return pending;
  }

  size(): number {
    return this.items.length;
  }
}

export function createRequestId(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `javis-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
