import { AppLogger } from "./AppLogger";
import { toUserFacingError } from "./userFacingError.ts";

export function installAppErrorBoundary(): void {
  const boundary = document.createElement("section");
  boundary.className = "app-error-boundary";
  boundary.hidden = true;
  boundary.setAttribute("role", "alert");
  boundary.innerHTML = `
    <div class="app-error-content">
      <small>JAVIS APP</small>
      <h1>界面遇到错误</h1>
      <p class="app-error-message">Live 界面可以恢复，运行时不会被强制终止。</p>
      <button class="text-command app-error-recover" type="button">恢复 Live</button>
    </div>`;
  document.body.append(boundary);

  function reveal(reason: unknown): void {
    const raw = reason instanceof Error ? reason.stack || reason.message : String(reason || "Unknown interface error");
    boundary.querySelector<HTMLElement>(".app-error-message")!.textContent = toUserFacingError(reason);
    boundary.hidden = false;
    AppLogger.write("error", "frontend", raw);
  }

  window.addEventListener("error", (event) => {
    reveal(event.error instanceof Error ? event.error : event.message || "Unknown interface error");
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    reveal(reason instanceof Error ? reason : String(reason || "Unhandled promise rejection"));
  });
  boundary.querySelector<HTMLButtonElement>(".app-error-recover")!.addEventListener("click", () => {
    document.body.dataset.surface = "live";
    boundary.hidden = true;
  });
}
