import { readPreference, writePreference } from "./AppPreferences";

type FirstRunPanelOptions = {
  onOpen?: () => void;
  onClose?: () => void;
};

export function createFirstRunPanel(options: FirstRunPanelOptions = {}) {
  const panel = document.createElement("section");
  panel.className = "first-run-panel";
  panel.hidden = true;
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-label", "Javis 首次启动");
  panel.tabIndex = -1;
  panel.innerHTML = String.raw`
    <header class="first-run-header window-drag-region" data-tauri-drag-region>
      <div><small>FIRST RUN</small><h1>本地数据与隐私</h1></div>
      <button class="icon-button first-run-later" type="button" aria-label="稍后确认">×</button>
    </header>
    <div class="first-run-body">
      <p class="first-run-intro">Javis 在本机运行。确认下面的存储边界后即可进入 Live。</p>
      <dl class="first-run-grid">
        <div><dt>配置、记忆与日志</dt><dd>%APPDATA%\Javis</dd></div>
        <div><dt>本地模型</dt><dd>D:\JavisModels</dd></div>
        <div><dt>授权工作区</dt><dd>D:\JavisWorkspace</dd></div>
        <div><dt>麦克风</dt><dd>默认不保存原始麦克风音频，只处理当前转写请求。</dd></div>
        <div><dt>屏幕与相机</dt><dd>屏幕和相机原始画面默认不落盘，只保留明确授权的结构化结果。</dd></div>
        <div><dt>依赖</dt><dd>不会自动安装或下载依赖、模型或工具链。</dd></div>
      </dl>
      <div class="first-run-actions">
        <button class="text-command first-run-complete" type="button">确认并进入 Live</button>
      </div>
    </div>`;
  document.body.append(panel);
  let restoreFocus: HTMLElement | null = null;

  function close(remember: boolean): void {
    if (remember) writePreference("firstRunComplete", true);
    panel.hidden = true;
    options.onClose?.();
    restoreFocus?.focus();
    restoreFocus = null;
  }

  function open(): void {
    restoreFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    options.onOpen?.();
    panel.hidden = false;
    panel.focus();
  }

  panel.querySelector<HTMLButtonElement>(".first-run-complete")!.addEventListener("click", () => close(true));
  panel.querySelector<HTMLButtonElement>(".first-run-later")!.addEventListener("click", () => close(false));
  document.addEventListener("keydown", (event) => {
    if (panel.hidden) return;
    if (event.key === "Escape") {
      close(false);
      return;
    }
    if (event.key === "Tab") {
      const first = panel.querySelector<HTMLButtonElement>(".first-run-later")!;
      const last = panel.querySelector<HTMLButtonElement>(".first-run-complete")!;
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel)) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  return {
    open,
    close: () => close(false),
    showOnFirstRun(): boolean {
      const required = !readPreference("firstRunComplete", false);
      if (required) open();
      return required;
    },
  };
}
