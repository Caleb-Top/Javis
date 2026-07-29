import { invoke } from "@tauri-apps/api/core";
import { setSidecarStatus, type SidecarStatus } from "./sidecarStatus";

export type SidecarSnapshot = {
  state: SidecarStatus;
  owned?: boolean;
  owned_pid?: number | null;
  restart_attempts?: number;
  last_error?: string;
};

type SidecarListener = (snapshot: SidecarSnapshot) => void;

export function isTauriRuntime(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

function parseSnapshot(raw: string): SidecarSnapshot {
  const snapshot = JSON.parse(raw) as SidecarSnapshot;
  return snapshot && typeof snapshot.state === "string" ? snapshot : { state: "failed", last_error: "Invalid Sidecar response" };
}

export function createSidecarClient() {
  const listeners = new Set<SidecarListener>();
  let current: SidecarSnapshot = { state: "unknown" };

  function publish(snapshot: SidecarSnapshot): SidecarSnapshot {
    current = snapshot;
    setSidecarStatus(snapshot.state);
    listeners.forEach((listener) => listener({ ...snapshot }));
    return snapshot;
  }

  async function call(command: "sidecar_status" | "sidecar_start" | "sidecar_stop" | "sidecar_restart"): Promise<SidecarSnapshot> {
    if (!isTauriRuntime()) {
      return publish({ state: "offline", last_error: "Desktop Sidecar is unavailable in browser preview" });
    }
    try {
      const raw = await invoke<string>(command);
      return publish(parseSnapshot(raw));
    } catch (error) {
      return publish({ state: "failed", last_error: error instanceof Error ? error.message : String(error) });
    }
  }

  async function ensureStarted(): Promise<SidecarSnapshot> {
    publish({ state: "probing" });
    let snapshot = await call("sidecar_status");
    if (snapshot.state === "healthy" || snapshot.state === "attached" || snapshot.state === "port-conflict") return snapshot;
    publish({ ...snapshot, state: "starting" });
    snapshot = await call("sidecar_start");
    if (snapshot.state === "healthy" || snapshot.state === "attached") return snapshot;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 400 * (attempt + 1)));
      snapshot = await call("sidecar_status");
      if (snapshot.state === "healthy" || snapshot.state === "attached") return snapshot;
    }
    return snapshot;
  }

  return {
    status: () => call("sidecar_status"),
    start: () => call("sidecar_start"),
    stop: () => call("sidecar_stop"),
    restart: () => call("sidecar_restart"),
    ensureStarted,
    snapshot: () => ({ ...current }),
    subscribe(listener: SidecarListener) {
      listeners.add(listener);
      listener({ ...current });
      return () => listeners.delete(listener);
    },
  };
}

export type SidecarClient = ReturnType<typeof createSidecarClient>;
