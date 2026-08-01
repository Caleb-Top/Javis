import type { LiveState } from "../live/liveState.ts";
import type { RuntimeSnapshot, RuntimeStateListener, StateSignal } from "./runtimeStateTypes.ts";

export const STATE_PRIORITY: Record<LiveState, number> = {
  idle: 0,
  speaking: 1,
  thinking: 2,
  listening: 3,
  executing: 4,
  offline: 5,
  blocked: 6,
  error: 7,
};

const DEFAULT_SNAPSHOT: RuntimeSnapshot = {
  state: "idle",
  source: "ui",
  detail: "Javis 已待命",
  updatedAt: 0,
};

export class RuntimeStateCoordinator {
  private current: RuntimeSnapshot = { ...DEFAULT_SNAPSHOT };
  private listeners = new Set<RuntimeStateListener>();

  signal(signal: StateSignal): RuntimeSnapshot {
    const requestMatches = !signal.requestId || !this.current.requestId || signal.requestId === this.current.requestId;
    if (signal.timestamp < this.current.updatedAt && STATE_PRIORITY[signal.state] <= STATE_PRIORITY[this.current.state]) {
      return this.snapshot();
    }
    if (signal.terminal && (!requestMatches || this.current.state === "listening")) {
      return this.snapshot();
    }
    if (this.current.state === "error" && signal.state !== "error" && signal.state !== "idle" && signal.source !== "ui") {
      return this.snapshot();
    }

    this.current = {
      state: signal.terminal ? "idle" : signal.state,
      source: signal.source,
      detail: signal.detail || this.current.detail || signal.state,
      requestId: signal.terminal ? undefined : signal.requestId || this.current.requestId,
      updatedAt: signal.timestamp,
    };
    this.render(this.current);
    this.listeners.forEach((listener) => listener(this.snapshot()));
    return this.snapshot();
  }

  subscribe(listener: RuntimeStateListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => this.listeners.delete(listener);
  }

  snapshot(): RuntimeSnapshot {
    return { ...this.current };
  }

  private render(snapshot: RuntimeSnapshot): void {
    const stage = document.querySelector<HTMLElement>(".live-stage");
    if (stage) stage.dataset.state = snapshot.state;
    const liveRegion = document.querySelector<HTMLElement>("#runtime-announcer");
    if (liveRegion) liveRegion.textContent = snapshot.detail;
  }
}

export const runtimeStateCoordinator = new RuntimeStateCoordinator();
