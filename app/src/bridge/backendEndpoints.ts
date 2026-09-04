export type BackendEndpoints = {
  http: string;
  websocket: string;
};

const DEFAULT_HTTP_ORIGIN = "http://127.0.0.1:8080";
export const LIFE_MEMORY_API_ROOT = "/api/life/memory";

function configuredBackendOrigin(): string {
  const env = (import.meta as ImportMeta & {
    env?: Record<string, string | undefined>;
  }).env;
  return String(env?.VITE_JAVIS_BACKEND_URL || "");
}

export function resolveBackendEndpoints(
  configuredOrigin = configuredBackendOrigin(),
): BackendEndpoints {
  const raw = configuredOrigin.trim() || DEFAULT_HTTP_ORIGIN;
  const parsed = new URL(raw);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("Javis backend URL must use http or https");
  }
  parsed.pathname = "";
  parsed.search = "";
  parsed.hash = "";
  const http = parsed.origin;
  const websocket = new URL(http);
  websocket.protocol = parsed.protocol === "https:" ? "wss:" : "ws:";
  return {
    http,
    websocket: websocket.origin,
  };
}
