import { resolveBackendEndpoints } from "../bridge/backendEndpoints.ts";

const LIVE_SURFACE_MARKER = 'data-surface="live"';

let legacyFrame: HTMLIFrameElement | null = null;

function loadLegacyWeb(force = false): void {
  if (!legacyFrame) return;
  if (!force && legacyFrame.dataset.loaded === "true") return;
  legacyFrame.dataset.loaded = "true";
  const url = new URL("/", resolveBackendEndpoints().http);
  url.searchParams.set("app_embed", "1");
  if (force) url.searchParams.set("reload", String(Date.now()));
  legacyFrame.src = url.toString();
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
  root.querySelector<HTMLButtonElement>(".code-close")!.addEventListener("click", () => {
    document.dispatchEvent(new CustomEvent("javis:return-live"));
  });
  root.querySelector<HTMLButtonElement>(".code-web-refresh")!.addEventListener("click", () => loadLegacyWeb(true));
}

export function openCodeSurface(): void {
  document.body.dataset.surface = "code";
  loadLegacyWeb();
}

export function closeCodeSurface(): void {
  void LIVE_SURFACE_MARKER;
  document.body.setAttribute("data-surface", "live");
  document.body.dataset.surface = "live";
}
