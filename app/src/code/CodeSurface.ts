import { resolveBackendEndpoints } from "../bridge/backendEndpoints.ts";

const LIVE_SURFACE_MARKER = 'data-surface="live"';
const OPEN_MODEL_SETTINGS_MESSAGE = "javis.open-model-settings";
const MODEL_SETTINGS_SECTION = "storage";

export type EmbeddedCodeMessageLike = {
  origin: string;
  source: unknown;
  data: unknown;
};

let legacyFrame: HTMLIFrameElement | null = null;
let activeSessionId = "";
let embeddedCodeMessageTarget: Window | null = null;

export function buildCodeSurfaceUrl(
  sessionId: string,
  force = false,
  parentOrigin = typeof window === "undefined" ? "" : window.location.origin,
): string {
  const url = new URL("/", resolveBackendEndpoints().http);
  url.searchParams.set("app_embed", "1");
  url.searchParams.set("session_id", sessionId);
  if (parentOrigin) url.searchParams.set("parent_origin", parentOrigin);
  if (force) url.searchParams.set("reload", String(Date.now()));
  return url.toString();
}

export function isTrustedEmbeddedCodeSettingsMessage(
  event: EmbeddedCodeMessageLike,
  expectedSource: unknown,
  expectedOrigin: string,
): boolean {
  if (event.source !== expectedSource || event.origin !== expectedOrigin) return false;
  if (!event.data || typeof event.data !== "object") return false;
  const data = event.data as { type?: unknown; section?: unknown; route?: unknown };
  return data.type === OPEN_MODEL_SETTINGS_MESSAGE
    && data.section === MODEL_SETTINGS_SECTION
    && data.route === "code";
}

function handleEmbeddedCodeMessage(event: MessageEvent): void {
  if (!legacyFrame?.contentWindow) return;
  const backendOrigin = new URL(resolveBackendEndpoints().http).origin;
  if (!isTrustedEmbeddedCodeSettingsMessage(
    event,
    legacyFrame.contentWindow,
    backendOrigin,
  )) return;
  document.dispatchEvent(new CustomEvent("javis:open-model-settings", {
    detail: { route: "code" },
  }));
}

export function bindEmbeddedCodeMessageListener(target: Window): void {
  if (embeddedCodeMessageTarget === target) return;
  embeddedCodeMessageTarget?.removeEventListener("message", handleEmbeddedCodeMessage);
  target.addEventListener("message", handleEmbeddedCodeMessage);
  embeddedCodeMessageTarget = target;
}

export function unbindEmbeddedCodeMessageListener(): void {
  embeddedCodeMessageTarget?.removeEventListener("message", handleEmbeddedCodeMessage);
  embeddedCodeMessageTarget = null;
}

function loadLegacyWeb(force = false): void {
  if (!legacyFrame || !activeSessionId) return;
  if (!force && legacyFrame.dataset.loaded === "true") return;
  legacyFrame.dataset.loaded = "true";
  legacyFrame.src = buildCodeSurfaceUrl(activeSessionId, force);
}

export function mountCodeSurface(root: HTMLElement): void {
  root.innerHTML = `
    <section class="code-surface" aria-label="Javis 完整功能界面">
      <header class="code-app-toolbar window-drag-region" data-tauri-drag-region>
        <button type="button" class="icon-button code-close" aria-label="返回 Live" title="返回 Live">←</button>
        <div class="code-app-title" data-tauri-drag-region>
          <strong data-tauri-drag-region>Javis Workbench</strong>
          <span data-tauri-drag-region>原 Web 完整功能界面</span>
        </div>
        <button type="button" class="icon-button code-web-refresh" aria-label="刷新完整功能界面" title="刷新">↻</button>
      </header>
      <iframe
        class="legacy-web-frame"
        title="Javis 原 Web 完整功能界面"
        allow="fullscreen"
        referrerpolicy="no-referrer"
      ></iframe>
    </section>`;

  legacyFrame = root.querySelector<HTMLIFrameElement>(".legacy-web-frame")!;
  bindEmbeddedCodeMessageListener(window);
  root.querySelector<HTMLButtonElement>(".code-close")!.addEventListener("click", () => {
    document.dispatchEvent(new CustomEvent("javis:return-live"));
  });
  root.querySelector<HTMLButtonElement>(".code-web-refresh")!.addEventListener("click", () => loadLegacyWeb(true));
}

export function openCodeSurface(sessionId: string): void {
  activeSessionId = sessionId;
  document.body.dataset.surface = "code";
  loadLegacyWeb();
}

export function closeCodeSurface(): void {
  void LIVE_SURFACE_MARKER;
  document.body.setAttribute("data-surface", "live");
  document.body.dataset.surface = "live";
}
