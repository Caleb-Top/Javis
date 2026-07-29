export function renderLiveStage(root: HTMLElement): void {
  root.innerHTML = `
    <main class="live-stage" data-state="idle">
      <header class="status-rail window-drag-region" data-tauri-drag-region data-expanded="false" aria-label="运行状态">
        <div class="status-summary" data-tauri-drag-region>
          <strong class="brand-mark" data-tauri-drag-region>JAVIS</strong>
          <span class="status-dot" data-tauri-drag-region aria-hidden="true"></span>
          <span class="status-state" data-tauri-drag-region>正在连接</span>
          <span class="status-connection" data-tauri-drag-region data-online="false">后端离线</span>
          <span class="permission-badge" data-tauri-drag-region>SAFE</span>
          <button class="icon-button status-toggle" type="button" aria-label="展开运行状态" aria-expanded="false">⌄</button>
        </div>
        <div class="status-detail" aria-hidden="true"></div>
      </header>
      <div class="ambient-grid" aria-hidden="true"></div>
       <section class="live-center" aria-label="Javis Live">
         <canvas id="live-orb-canvas" class="live-orb-canvas" aria-label="Javis Live 交流球"></canvas>
       </section>
       <div class="live-surface-status" aria-live="polite">待命</div>
       <div id="pet-surface-root"></div>
       <div id="surface-menu-root"></div>
       <button class="live-caption line-clamp" type="button" aria-label="当前回答，点击展开">Javis 已待命</button>
      <section class="command-area">
        <form class="live-input">
          <button type="button" class="icon-button voice-control" aria-label="开始或停止语音" title="语音">●</button>
          <textarea rows="1" aria-label="输入任务" placeholder="说话，或输入一句指令..."></textarea>
          <button type="submit" class="icon-button send-btn" aria-label="发送" title="发送">↑</button>
        </form>
        <nav class="live-dock" aria-label="Javis controls">
          <button type="button" class="dock-action voice-control" data-icon="●"><span>收听</span></button>
          <button type="button" class="dock-action code-control" data-icon="⌘"><span>Code</span></button>
          <button type="button" class="dock-action perception-control" data-icon="▣"><span>屏幕</span></button>
          <button type="button" class="dock-action control-panel-control" data-icon="◇"><span>权限</span></button>
          <button type="button" class="dock-action more-control" data-icon="•••"><span>更多</span></button>
          <button type="button" class="dock-action pet-mode-control" data-icon="◇" title="缩到桌面宠物"><span>桌宠</span></button>
        </nav>
      </section>
       <div id="drawer-root"></div>
       <div id="code-root"></div>
       <div id="settings-root"></div>
       <div id="runtime-announcer" class="sr-only" aria-live="polite"></div>
    </main>`;
}

export function getLiveInput(): HTMLTextAreaElement | null {
  return document.querySelector<HTMLTextAreaElement>(".live-input textarea");
}
