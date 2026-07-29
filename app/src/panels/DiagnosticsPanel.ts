import type { BackendClient } from "../bridge/backendClient";
import type { SidecarClient } from "../bridge/sidecarClient";
import type { VoiceCapture } from "../live/VoiceCapture";

type DiagnosticScope = "full" | "model" | "data";
type DiagnosticStatus = "pass" | "warn" | "fail";
type DiagnosticCheck = {
  id: string;
  label: string;
  status: DiagnosticStatus;
  message: string;
  action?: "restart_runtime" | "open_storage";
};
type DiagnosticReport = {
  ok: boolean;
  scope: DiagnosticScope;
  overall: DiagnosticStatus;
  counts: Record<DiagnosticStatus, number>;
  summary: string;
  checks: DiagnosticCheck[];
};
type VoiceCapabilities = {
  capture?: { available?: boolean; backend?: string; native?: boolean; webview_permission_required?: boolean };
  stt?: { available?: boolean; model?: string; offline?: boolean };
  tts?: { available?: boolean; voice?: string; network_required?: boolean; backend?: string; offline?: boolean };
};
type VoiceTestResponse = {
  ok: boolean;
  transcript?: string;
  audio?: string;
  mime?: string;
  bytes?: number;
  error?: string;
};

type DiagnosticsPanelOptions = {
  onClose?: () => void;
  onOpenStorage?: () => void;
  onOpenLogs?: () => void | Promise<void>;
};

const SCOPE_LABELS: Record<DiagnosticScope, string> = {
  full: "完整自检",
  model: "模型检查",
  data: "记忆与存储",
};

export function createDiagnosticsPanel(
  client: BackendClient,
  sidecar: SidecarClient,
  voiceCapture: VoiceCapture,
  options: DiagnosticsPanelOptions = {},
) {
  const panel = document.createElement("section");
  panel.className = "diagnostics-panel";
  panel.hidden = true;
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  panel.setAttribute("aria-label", "Javis 诊断");
  panel.innerHTML = `
    <header class="diagnostics-header window-drag-region" data-tauri-drag-region>
      <div><small>DIAGNOSTICS</small><h2>Javis 诊断与修复</h2></div>
      <button class="icon-button diagnostics-close" type="button" aria-label="关闭">×</button>
    </header>
    <div class="diagnostics-body">
      <p class="diagnostics-policy">所有检查只读取本机状态，未提供自动安装，也不会下载依赖。目录检查只会创建所选的工作目录和短暂写入探针；麦克风、系统音频、STT 和 TTS 只有完成实测后才显示“通过”。</p>
      <section class="diagnostics-overview" data-overall="idle">
        <div>
          <small>当前结果</small>
          <strong class="diagnostics-summary">尚未运行</strong>
        </div>
        <span class="diagnostics-scope-label">准备就绪</span>
      </section>
      <div class="diagnostics-scope-actions" aria-label="选择诊断范围">
        <button class="text-command diagnostics-full" type="button">一键完整自检</button>
        <button class="text-command diagnostics-model" type="button">检查模型连接</button>
        <button class="text-command diagnostics-data" type="button">检查记忆与存储</button>
      </div>
      <div class="diagnostics-grid" data-state="idle" aria-live="polite"></div>
      <section class="audio-diagnostics" aria-label="音频硬件与语音链自检">
        <div class="audio-diagnostic-status">
          <span><small>麦克风自检</small><strong data-audio-status="microphone">未测试</strong></span>
          <span><small>系统音频自检</small><strong data-audio-status="system">未测试</strong></span>
          <span><small>STT</small><strong data-audio-status="stt">未测试</strong></span>
          <span><small>TTS</small><strong data-audio-status="tts">未测试</strong></span>
        </div>
        <p class="audio-diagnostic-note" data-audio-note>麦克风测试时请说“你好 Javis”；系统音频测试直接读取 Windows 回环设备。</p>
        <div class="audio-diagnostic-actions">
          <button class="text-command diagnostics-microphone" type="button">测试麦克风 + STT</button>
          <button class="text-command diagnostics-system-audio" type="button">测试系统音频</button>
          <button class="text-command diagnostics-tts" type="button">测试 TTS 播放</button>
        </div>
      </section>
      <div class="diagnostics-actions">
        <button class="text-command diagnostics-storage" type="button">打开存储设置</button>
        <button class="text-command diagnostics-logs" type="button">打开日志目录</button>
        <button class="text-command diagnostics-restart" type="button">重启运行时</button>
      </div>
    </div>`;
  document.body.append(panel);

  const grid = panel.querySelector<HTMLElement>(".diagnostics-grid")!;
  const summary = panel.querySelector<HTMLElement>(".diagnostics-summary")!;
  const scopeLabel = panel.querySelector<HTMLElement>(".diagnostics-scope-label")!;
  const overview = panel.querySelector<HTMLElement>(".diagnostics-overview")!;
  const audioNote = panel.querySelector<HTMLElement>("[data-audio-note]")!;
  let voiceCapabilities: VoiceCapabilities = {};

  function renderChecks(checks: DiagnosticCheck[]): void {
    grid.replaceChildren(...checks.map((check) => {
      const card = document.createElement("article");
      card.className = "diagnostic-check";
      card.dataset.status = check.status;
      const indicator = document.createElement("span");
      indicator.className = "diagnostic-check-indicator";
      indicator.setAttribute("aria-hidden", "true");
      const copy = document.createElement("div");
      const label = document.createElement("strong");
      const message = document.createElement("small");
      label.textContent = check.label;
      message.textContent = check.message;
      copy.append(label, message);
      card.append(indicator, copy);
      if (check.action) {
        const action = document.createElement("button");
        action.className = "diagnostic-inline-action";
        action.type = "button";
        action.dataset.diagnosticAction = check.action;
        action.textContent = check.action === "restart_runtime" ? "重启" : "设置";
        card.append(action);
      }
      return card;
    }));
  }

  function setDiagnosticBusy(busy: boolean, scope: DiagnosticScope): void {
    grid.dataset.state = busy ? "loading" : "ready";
    panel.querySelectorAll<HTMLButtonElement>(".diagnostics-scope-actions button").forEach((button) => {
      button.disabled = busy;
    });
    scopeLabel.textContent = busy ? `正在运行${SCOPE_LABELS[scope]}…` : SCOPE_LABELS[scope];
  }

  async function refresh(scope: DiagnosticScope = "full"): Promise<void> {
    setDiagnosticBusy(true, scope);
    summary.textContent = "正在检查本机组件…";
    overview.dataset.overall = "loading";
    const [reportResult, sidecarResult, runtimeFallback] = await Promise.allSettled([
      client.post<DiagnosticReport>("/api/diagnostics/self-test", { scope }),
      sidecar.status(),
      client.get<Record<string, unknown>>("/api/runtime/status"),
    ]);
    const checks: DiagnosticCheck[] = [];
    let report: DiagnosticReport | null = null;
    if (reportResult.status === "fulfilled") {
      report = reportResult.value;
      checks.push(...report.checks);
    } else {
      checks.push({
        id: "backend_self_test",
        label: "Python 自检",
        status: "fail",
        message: reportResult.reason instanceof Error
          ? reportResult.reason.message
          : "后端自检不可用",
        action: "restart_runtime",
      });
      if (runtimeFallback.status === "fulfilled") {
        checks.push({
          id: "runtime_fallback",
          label: "基础运行时",
          status: "warn",
          message: "基础状态可读取，但完整自检接口失败",
        });
      }
    }
    if (sidecarResult.status === "fulfilled") {
      const state = sidecarResult.value.state;
      const healthy = state === "healthy" || state === "attached";
      checks.unshift({
        id: "sidecar",
        label: "桌面后端进程",
        status: healthy ? "pass" : "fail",
        message: healthy ? `运行正常 · ${state}` : `状态异常 · ${state}`,
        action: healthy ? undefined : "restart_runtime",
      });
    }
    renderChecks(checks);
    const failCount = checks.filter((check) => check.status === "fail").length;
    const warnCount = checks.filter((check) => check.status === "warn").length;
    const passCount = checks.filter((check) => check.status === "pass").length;
    const overall: DiagnosticStatus = failCount ? "fail" : warnCount ? "warn" : "pass";
    overview.dataset.overall = overall;
    summary.textContent = report?.summary || `${passCount} 项通过，${warnCount} 项需留意，${failCount} 项失败`;
    try {
      voiceCapabilities = await client.get<VoiceCapabilities>("/api/voice/diagnostics");
    } catch {
      voiceCapabilities = {};
    }
    setDiagnosticBusy(false, scope);
  }

  function setAudioStatus(kind: "microphone" | "system" | "stt" | "tts", text: string, ok?: boolean): void {
    const output = panel.querySelector<HTMLElement>(`[data-audio-status="${kind}"]`)!;
    output.textContent = text;
    output.dataset.result = ok === undefined ? "pending" : ok ? "passed" : "failed";
  }

  function setAudioBusy(busy: boolean, note: string): void {
    panel.querySelectorAll<HTMLButtonElement>(".audio-diagnostic-actions button").forEach((button) => {
      button.disabled = busy;
    });
    audioNote.textContent = note;
  }

  async function testMicrophoneAndStt(): Promise<void> {
    setAudioBusy(true, "正在录音，请清楚地说“你好 Javis”…");
    setAudioStatus("microphone", "测试中");
    setAudioStatus("stt", "等待录音");
    const result = await voiceCapture.probeMicrophone();
    setAudioStatus("microphone", result.ok ? "通过" : result.message, result.ok);
    if (!result.ok || !result.audioBase64) {
      setAudioStatus("stt", "未执行");
      setAudioBusy(false, result.message);
      return;
    }
    if (voiceCapabilities.stt?.available === false) {
      setAudioStatus("stt", "组件未安装", false);
      setAudioBusy(false, "麦克风通过，但本地 STT 组件未安装。");
      return;
    }
    setAudioStatus("stt", "转写中");
    try {
      const response = await client.post<VoiceTestResponse>("/api/voice/stt/test", {
        audio: result.audioBase64,
        language: "zh",
      });
      const passed = response.ok && Boolean(response.transcript?.trim());
      setAudioStatus("stt", passed ? `通过：${response.transcript}` : response.error || "未识别到语音", passed);
      setAudioBusy(false, passed ? `完整输入链通过：${response.transcript}` : "录音成功，但没有识别到有效文字。");
    } catch (error) {
      setAudioStatus("stt", "请求失败", false);
      setAudioBusy(false, error instanceof Error ? error.message : "STT 测试失败");
    }
  }

  async function testSystemAudio(): Promise<void> {
    setAudioBusy(true, "正在读取 Windows 系统音频回环设备…");
    setAudioStatus("system", "采集中");
    const result = await voiceCapture.probeSystemAudio();
    setAudioStatus("system", result.ok ? "通过" : result.message, result.ok);
    setAudioBusy(false, result.message);
  }

  async function testTts(): Promise<void> {
    setAudioBusy(true, "正在生成并播放测试语音…");
    setAudioStatus("tts", "测试中");
    if (voiceCapabilities.tts?.available === false) {
      setAudioStatus("tts", "组件未安装", false);
      setAudioBusy(false, "TTS 组件未安装。");
      return;
    }
    try {
      const response = await client.post<VoiceTestResponse>("/api/voice/tts/test", {
        text: "Javis 语音输出正常",
      });
      if (!response.ok || !response.audio) throw new Error(response.error || "没有生成语音");
      const binary = window.atob(response.audio);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      const audioUrl = URL.createObjectURL(new Blob([bytes], {
        type: response.mime || "audio/mpeg",
      }));
      const audio = new Audio(audioUrl);
      try {
        await new Promise<void>((resolve, reject) => {
          audio.onended = () => resolve();
          audio.onerror = () => reject(new Error("当前输出设备无法播放测试语音"));
          void audio.play().catch(reject);
        });
      } finally {
        URL.revokeObjectURL(audioUrl);
      }
      setAudioStatus("tts", `通过 · ${Math.max(1, Math.round((response.bytes || 0) / 1024))} KB`, true);
      setAudioBusy(false, "TTS 已生成并交给当前输出设备播放。");
    } catch (error) {
      setAudioStatus("tts", "失败", false);
      setAudioBusy(false, error instanceof Error ? error.message : "TTS 测试失败");
    }
  }

  function open(): void {
    panel.hidden = false;
    void refresh("full");
  }

  function close(): void {
    panel.hidden = true;
    options.onClose?.();
  }

  async function runAction(action: string): Promise<void> {
    if (action === "open_storage") {
      panel.hidden = true;
      options.onOpenStorage?.();
      return;
    }
    if (action === "restart_runtime") {
      summary.textContent = "正在重启 Python 运行时…";
      await sidecar.restart();
      await refresh("full");
    }
  }

  panel.querySelector<HTMLButtonElement>(".diagnostics-close")!.addEventListener("click", close);
  panel.querySelector<HTMLButtonElement>(".diagnostics-full")!.addEventListener("click", () => void refresh("full"));
  panel.querySelector<HTMLButtonElement>(".diagnostics-model")!.addEventListener("click", () => void refresh("model"));
  panel.querySelector<HTMLButtonElement>(".diagnostics-data")!.addEventListener("click", () => void refresh("data"));
  panel.querySelector<HTMLButtonElement>(".diagnostics-microphone")!.addEventListener("click", () => void testMicrophoneAndStt());
  panel.querySelector<HTMLButtonElement>(".diagnostics-system-audio")!.addEventListener("click", () => void testSystemAudio());
  panel.querySelector<HTMLButtonElement>(".diagnostics-tts")!.addEventListener("click", () => void testTts());
  panel.querySelector<HTMLButtonElement>(".diagnostics-storage")!.addEventListener("click", () => void runAction("open_storage"));
  panel.querySelector<HTMLButtonElement>(".diagnostics-logs")!.addEventListener("click", () => void options.onOpenLogs?.());
  panel.querySelector<HTMLButtonElement>(".diagnostics-restart")!.addEventListener("click", () => void runAction("restart_runtime"));
  grid.addEventListener("click", (event) => {
    const action = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-diagnostic-action]");
    if (action) void runAction(action.dataset.diagnosticAction || "");
  });
  return { open, close, refresh };
}
