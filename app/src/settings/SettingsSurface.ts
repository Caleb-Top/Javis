import type { OrbPaletteName } from "../live/LiveOrbRenderer.ts";
import { readStringPreference, writeStringPreference } from "../app/AppPreferences.ts";
import {
  readPetPreferences,
  writePetPreferences,
  type PetPreferences,
  type PetShortcutKind,
  type PetShortcutSlot,
} from "../pet/petPreferences.ts";
import { getDefaultPetSkin, getPetSkin, petSkinRegistry } from "../pet/PetSkinRegistry.ts";
import {
  getShortcutEditorDescriptor,
  getShortcutSlotStatus,
  normalizeRecordedHotkey,
  type ShortcutSlotStatus,
} from "./shortcutEditorModel.ts";
import {
  getSettingsReturnMode,
  type SettingsSourceMode,
} from "./settingsNavigation.ts";

const JAVIS_ACTIONS = [
  ["live", "打开 Live"],
  ["code", "打开 Code"],
  ["settings", "打开设置"],
  ["diagnostics", "打开诊断"],
] as const;

const STATUS_LABELS: Record<ShortcutSlotStatus, string> = {
  empty: "未设置",
  incomplete: "配置不完整",
  invalid: "格式错误",
  "missing-target": "目标不存在",
  saved: "已保存",
};

const PALETTES: Array<[OrbPaletteName, string]> = [
  ["cobalt", "钴蓝"],
  ["arctic", "冰青"],
  ["indigo", "靛蓝"],
  ["graphite", "石墨"],
];

export type PathSettingKey =
  | "model_dir"
  | "workspace_dir"
  | "output_dir"
  | "backup_dir";

export type PathSettings = Record<PathSettingKey, string>;

export type PathSettingsResponse = {
  applied: boolean;
  paths: PathSettings;
  error?: string;
  restart_required?: PathSettingKey[];
};

export type ModelSource = "local" | "remote";

export type RemoteProviderOption = {
  id: string;
  label: string;
  default_base_url: string;
  models: Array<{
    id: string;
    label: string;
    description: string;
  }>;
};

export type RemoteModelProfile = {
  model: string;
  base_url: string;
  has_key: boolean;
  key_source: "environment" | "local" | "none";
  env_var: string;
};

export type ModelConnectionSettingsResponse = {
  applied: boolean;
  source: ModelSource;
  active_provider: string;
  active_model: string;
  local: {
    model: string;
    base_url: string;
  };
  remote: RemoteModelProfile & {
    provider: string;
  };
  remote_profiles: Record<string, RemoteModelProfile>;
  providers: RemoteProviderOption[];
  error?: string;
};

export type LocalModelCatalogResponse = {
  connected: boolean;
  models: string[];
  message: string;
};

export type ModelDiagnosticReport = {
  summary: string;
  checks: Array<{
    id: string;
    label: string;
    status: "pass" | "warn" | "fail";
    message: string;
  }>;
};

export type SettingsSurfaceOptions = {
  root: HTMLElement;
  onClose: (mode: SettingsSourceMode) => void;
  onPickTarget: (kind: "file" | "directory") => Promise<string | null>;
  onOpenDiagnostics: () => void;
  onOpenPrivacy: () => void;
  onOrbPalette: (palette: OrbPaletteName) => void;
  onLoadPathSettings: () => Promise<PathSettingsResponse>;
  onSavePathSetting: (
    key: PathSettingKey,
    path: string,
  ) => Promise<PathSettingsResponse>;
  onLoadModelSettings: () => Promise<ModelConnectionSettingsResponse>;
  onSaveModelSettings: (
    settings: Record<string, unknown>,
  ) => Promise<ModelConnectionSettingsResponse>;
  onRefreshLocalModels: (baseUrl: string) => Promise<LocalModelCatalogResponse>;
  onTestModelConnections: () => Promise<ModelDiagnosticReport>;
};

export type SettingsSurfaceController = {
  open(sourceMode: SettingsSourceMode, section?: string): void;
  close(): void;
  sync(): void;
  dispose(): void;
};

function shortcutSlotTemplate(index: number): string {
  const number = index + 1;
  return `
    <fieldset class="settings-shortcut-card" data-shortcut-index="${index}">
      <legend>快捷项 ${number}</legend>
      <div class="settings-shortcut-grid">
        <label class="settings-field">
          <span>菜单名称</span>
          <input class="shortcut-label" type="text" maxlength="24" placeholder="例如：打开编辑器" aria-label="快捷项 ${number} 菜单名称">
        </label>
        <label class="settings-field">
          <span>类型</span>
          <select class="shortcut-kind" aria-label="快捷项 ${number} 类型">
            <option value="">未设置</option>
            <option value="javis">Javis 功能</option>
            <option value="target">应用或文件</option>
            <option value="url">网页</option>
            <option value="hotkey">组合键</option>
          </select>
        </label>
      </div>
      <div class="shortcut-control" data-control="none">
        <span>先选择快捷项类型</span>
      </div>
      <label class="settings-field shortcut-control" data-control="javis-select" hidden>
        <span>Javis 功能</span>
        <select class="shortcut-javis-value">
          ${JAVIS_ACTIONS.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
        </select>
      </label>
      <div class="settings-field shortcut-control" data-control="target-path" hidden>
        <span>应用、文件或目录</span>
        <div class="settings-input-action">
          <input class="shortcut-target-value" type="text" maxlength="2048" placeholder="C:\\Program Files\\Example\\Example.exe">
          <button class="settings-secondary-button shortcut-browse-file" type="button">文件</button>
          <button class="settings-secondary-button shortcut-browse-directory" type="button">目录</button>
        </div>
      </div>
      <label class="settings-field shortcut-control" data-control="url-input" hidden>
        <span>网页地址</span>
        <input class="shortcut-url-value" type="url" maxlength="2048" placeholder="https://example.com">
      </label>
      <div class="settings-field shortcut-control" data-control="hotkey-recorder" hidden>
        <span>组合键</span>
        <div class="settings-input-action">
          <input class="shortcut-hotkey-value" type="text" placeholder="Ctrl+Shift+P" readonly>
          <button class="settings-secondary-button shortcut-record" type="button">开始录制</button>
        </div>
      </div>
      <div class="settings-shortcut-footer">
        <small class="shortcut-help"></small>
        <output class="shortcut-status" data-status="empty">未设置</output>
        <button class="settings-icon-button shortcut-clear" type="button" title="清除" aria-label="清除快捷项 ${number}">×</button>
      </div>
    </fieldset>`;
}

function pathSettingTemplate(
  key: PathSettingKey,
  label: string,
  description: string,
): string {
  return `
    <div class="settings-path-row" data-path-key="${key}">
      <div class="settings-path-copy">
        <strong>${label}</strong>
        <small>${description}</small>
      </div>
      <div class="settings-input-action settings-path-action">
        <input class="settings-path-value" type="text" readonly aria-label="${label}">
        <button class="settings-secondary-button settings-path-select" type="button">选择目录</button>
      </div>
    </div>`;
}

export function createSettingsSurface(
  options: SettingsSurfaceOptions,
): SettingsSurfaceController {
  let sourceMode: SettingsSourceMode = "live";
  let activeSection = "general";
  let activeModelSource: ModelSource = "local";
  let modelSettings: ModelConnectionSettingsResponse | null = null;
  let recordingSlot: HTMLElement | null = null;

  options.root.innerHTML = `
    <section class="settings-surface" aria-label="Javis 设置">
      <header class="settings-header window-drag-region" data-tauri-drag-region>
        <button class="settings-icon-button settings-close" type="button" aria-label="返回" title="返回">←</button>
        <div data-tauri-drag-region>
          <strong data-tauri-drag-region>Javis 设置</strong>
          <span data-tauri-drag-region>桌面智能体偏好</span>
        </div>
        <span class="settings-save-state" data-tauri-drag-region>自动保存</span>
      </header>
      <div class="settings-layout">
        <nav class="settings-nav" aria-label="设置分类">
          <button type="button" data-settings-section="general">常规</button>
          <button type="button" data-settings-section="pet">桌宠</button>
          <button type="button" data-settings-section="storage">模型与存储</button>
          <button type="button" data-settings-section="shortcuts">快捷项</button>
          <button type="button" data-settings-section="appearance">外观</button>
        </nav>
        <main class="settings-content">
          <section class="settings-section" data-settings-pane="general">
            <div class="settings-title"><small>GENERAL</small><h1>常规</h1></div>
            <div class="settings-panel settings-action-list">
              <button class="settings-row-button settings-diagnostics" type="button"><span><strong>运行诊断</strong><small>检查本地后端与系统组件</small></span><b>›</b></button>
              <button class="settings-row-button settings-privacy" type="button"><span><strong>本地数据与隐私</strong><small>查看首次启动说明与数据位置</small></span><b>›</b></button>
            </div>
            <div class="settings-panel">
              <label class="settings-field">
                <span>连续语音降噪</span>
                <select class="voice-noise-profile">
                  <option value="off">关闭</option>
                  <option value="standard">标准</option>
                  <option value="strong">强降噪</option>
                </select>
                <small>嘈杂环境可选强降噪，切换后会自动重启连续收听。</small>
              </label>
            </div>
          </section>
          <section class="settings-section" data-settings-pane="pet">
            <div class="settings-title"><small>DESKTOP PET</small><h1>桌宠</h1></div>
            <div class="settings-panel">
              <div class="assistant-scale-preview" aria-label="助手大小实时预览">
                <div class="assistant-scale-preview-orb" aria-hidden="true"><span></span></div>
                <div><strong>Live 与桌宠同步缩放</strong><small>拖动滑块即可实时预览</small></div>
              </div>
              <label class="settings-field settings-scale-field">
                <span>助手大小 <output class="pet-scale-output">100%</output></span>
                <input class="pet-scale-input" type="range" min="70" max="130" step="5" value="100">
              </label>
              <label class="settings-field">
                <span>桌宠皮肤</span>
                <select class="pet-skin-select">
                  ${petSkinRegistry.map((skin) => `<option value="${skin.id}">${skin.name}</option>`).join("")}
                </select>
              </label>
            </div>
          </section>
          <section class="settings-section" data-settings-pane="storage">
            <div class="settings-title"><small>MODEL ROUTING</small><h1>模型与存储</h1><span>本地与远端配置独立保留，切换后立即成为 Live 的推理来源。</span></div>
            <div class="settings-panel settings-model-panel">
              <div class="settings-panel-heading">
                <div><strong>推理来源</strong><small>本地优先低延迟，远端用于更强推理。</small></div>
                <span class="settings-active-model">正在读取…</span>
              </div>
              <div class="model-source-segment" role="tablist" aria-label="推理来源">
                <button type="button" role="tab" data-model-source="local">本地模型</button>
                <button type="button" role="tab" data-model-source="remote">远端 API</button>
              </div>
              <div class="settings-model-profile" data-model-profile="local">
                <label class="settings-field">
                  <span>Ollama 地址</span>
                  <input class="local-model-base-url" type="url" maxlength="2048" placeholder="http://127.0.0.1:11434/v1">
                </label>
                <label class="settings-field">
                  <span>本地模型</span>
                  <div class="settings-input-action settings-model-input-action">
                    <input class="local-model-name" type="text" list="local-model-options" maxlength="240" placeholder="选择或输入模型名称">
                    <datalist id="local-model-options"></datalist>
                    <button class="settings-secondary-button local-model-refresh" type="button">刷新模型</button>
                  </div>
                </label>
              </div>
              <div class="settings-model-profile" data-model-profile="remote" hidden>
                <div class="settings-model-grid">
                  <label class="settings-field">
                    <span>API 提供商</span>
                    <select class="remote-provider">
                      <option value="deepseek">DeepSeek</option>
                    </select>
                  </label>
                  <label class="settings-field">
                    <span>远端模型</span>
                    <input class="remote-model-name" type="text" list="remote-model-options" maxlength="240" placeholder="选择或输入模型名称">
                    <datalist id="remote-model-options"></datalist>
                  </label>
                </div>
                <label class="settings-field">
                  <span>API 地址</span>
                  <input class="remote-model-base-url" type="url" maxlength="2048" placeholder="https://api.example.com/v1">
                </label>
                <label class="settings-field">
                  <span>API Key <small class="remote-key-state">未配置</small></span>
                  <input class="remote-api-key" type="password" maxlength="4096" autocomplete="off" placeholder="仅在修改时填写，密钥不会回显">
                </label>
              </div>
              <div class="model-connection-status" aria-label="模型连接状态">
                <span data-model-channel="local" data-state="idle"><i></i><b>本地</b><small>未检测</small></span>
                <span data-model-channel="remote" data-state="idle"><i></i><b>远端</b><small>未检测</small></span>
              </div>
              <div class="settings-model-actions">
                <output class="settings-model-status" aria-live="polite">配置尚未加载</output>
                <button class="settings-secondary-button model-test-connections" type="button">检测两路连接</button>
                <button class="settings-primary-button model-save" type="button">保存并应用</button>
              </div>
            </div>
            <div class="settings-subheading"><strong>数据目录</strong><small>使用 Windows 原生目录选择器授权。</small></div>
            <div class="settings-panel settings-path-list">
              ${pathSettingTemplate("model_dir", "本地模型目录", "扫描 GGUF 与 Ollama 模型；外部 Ollama 可能需要重启")}
              ${pathSettingTemplate("workspace_dir", "项目工作区", "新建项目和授权工作文件的保存位置")}
              ${pathSettingTemplate("output_dir", "导入与输出", "上传文件、导出结果和生成内容的保存位置")}
              ${pathSettingTemplate("backup_dir", "备份目录", "记忆、配置与技能备份的目标位置")}
            </div>
            <output class="settings-path-status" aria-live="polite">正在读取本地配置…</output>
          </section>
          <section class="settings-section" data-settings-pane="shortcuts">
            <div class="settings-title"><small>RIGHT CLICK</small><h1>快捷项</h1><span>设置、Code 固定显示；可再添加 3 项。</span></div>
            <div class="settings-shortcut-list">
              ${[0, 1, 2].map(shortcutSlotTemplate).join("")}
            </div>
          </section>
          <section class="settings-section" data-settings-pane="appearance">
            <div class="settings-title"><small>APPEARANCE</small><h1>外观</h1></div>
            <div class="settings-panel">
              <label class="settings-field">
                <span>交流球颜色</span>
                <select class="orb-palette-select">
                  ${PALETTES.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
                </select>
              </label>
              <div class="settings-theme-preview" aria-label="浅灰界面预览">
                <span></span><strong>浅灰原生界面</strong><small>当前主题</small>
              </div>
            </div>
          </section>
        </main>
      </div>
    </section>`;

  const surface = options.root.querySelector<HTMLElement>(".settings-surface")!;
  const saveState = options.root.querySelector<HTMLElement>(".settings-save-state")!;

  function updateAssistantScalePreview(scale: number): void {
    const normalized = Math.min(1.3, Math.max(0.7, scale));
    const preview = options.root.querySelector<HTMLElement>(".assistant-scale-preview")!;
    preview.style.setProperty("--assistant-preview-size", `${Math.round(86 * normalized)}px`);
    preview.dataset.scale = String(Math.round(normalized * 100));
  }

  function dispatchPetPreferences(preferences: PetPreferences): void {
    document.dispatchEvent(new CustomEvent("javis:pet-preferences-changed", { detail: preferences }));
  }

  function renderPathSettings(paths: PathSettings): void {
    options.root.querySelectorAll<HTMLElement>("[data-path-key]").forEach((row) => {
      const key = row.dataset.pathKey as PathSettingKey;
      const input = row.querySelector<HTMLInputElement>(".settings-path-value")!;
      input.value = paths[key] || "";
      input.title = input.value;
    });
  }

  function setPathStatus(message: string, state: "idle" | "saving" | "saved" | "error"): void {
    const output = options.root.querySelector<HTMLOutputElement>(".settings-path-status")!;
    output.value = message;
    output.dataset.state = state;
  }

  async function loadPathSettings(): Promise<void> {
    setPathStatus("正在读取本地配置…", "saving");
    try {
      const response = await options.onLoadPathSettings();
      if (!response.applied) throw new Error(response.error || "路径配置读取失败");
      renderPathSettings(response.paths);
      setPathStatus("目录配置已就绪", "idle");
    } catch (error) {
      setPathStatus(error instanceof Error ? error.message : "无法读取目录配置", "error");
    }
  }

  async function selectPathSetting(row: HTMLElement): Promise<void> {
    const key = row.dataset.pathKey as PathSettingKey;
    const selected = await options.onPickTarget("directory");
    if (!selected) return;
    setPathStatus("正在保存目录…", "saving");
    try {
      const response = await options.onSavePathSetting(key, selected);
      if (!response.applied) throw new Error(response.error || "目录保存失败");
      renderPathSettings(response.paths);
      const needsRestart = response.restart_required?.includes(key);
      setPathStatus(
        needsRestart ? "已保存；重启外部 Ollama 后应用模型目录" : "目录已保存并生效",
        "saved",
      );
    } catch (error) {
      setPathStatus(error instanceof Error ? error.message : "目录保存失败", "error");
    }
  }

  function setModelStatus(
    message: string,
    state: "idle" | "saving" | "saved" | "error",
  ): void {
    const output = options.root.querySelector<HTMLOutputElement>(".settings-model-status")!;
    output.value = message;
    output.dataset.state = state;
  }

  function selectModelSource(source: ModelSource): void {
    activeModelSource = source;
    options.root.querySelectorAll<HTMLButtonElement>("[data-model-source]").forEach((button) => {
      const selected = button.dataset.modelSource === source;
      button.dataset.active = String(selected);
      button.setAttribute("aria-selected", String(selected));
    });
    options.root.querySelectorAll<HTMLElement>("[data-model-profile]").forEach((profile) => {
      profile.hidden = profile.dataset.modelProfile !== source;
    });
  }

  function replaceDataList(selector: string, values: string[]): void {
    const list = options.root.querySelector<HTMLDataListElement>(selector)!;
    list.replaceChildren(...values.map((value) => {
      const option = document.createElement("option");
      option.value = value;
      return option;
    }));
  }

  function renderRemoteProvider(providerId: string): void {
    if (!modelSettings) return;
    const provider = modelSettings.providers.find((candidate) => candidate.id === providerId)
      || modelSettings.providers[0];
    if (!provider) return;
    const profile = modelSettings.remote_profiles[provider.id] || {
      model: provider.models[0]?.id || "",
      base_url: provider.default_base_url,
      has_key: false,
      key_source: "none" as const,
      env_var: "",
    };
    const select = options.root.querySelector<HTMLSelectElement>(".remote-provider")!;
    select.value = provider.id;
    options.root.querySelector<HTMLInputElement>(".remote-model-name")!.value = profile.model;
    options.root.querySelector<HTMLInputElement>(".remote-model-base-url")!.value = profile.base_url;
    replaceDataList("#remote-model-options", provider.models.map((model) => model.id));

    const keyInput = options.root.querySelector<HTMLInputElement>(".remote-api-key")!;
    keyInput.value = "";
    keyInput.placeholder = profile.has_key
      ? "已配置；仅在修改时填写，留空保持不变"
      : "仅在修改时填写，密钥不会回显";
    const keyState = options.root.querySelector<HTMLElement>(".remote-key-state")!;
    keyState.textContent = profile.key_source === "environment"
      ? `由 ${profile.env_var} 提供`
      : profile.has_key
        ? "已配置"
        : "未配置";
    keyState.dataset.state = profile.has_key ? "configured" : "missing";
  }

  function renderModelSettings(settings: ModelConnectionSettingsResponse): void {
    modelSettings = settings;
    const providerSelect = options.root.querySelector<HTMLSelectElement>(".remote-provider")!;
    providerSelect.replaceChildren(...settings.providers.map((provider) => {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.label;
      return option;
    }));
    options.root.querySelector<HTMLInputElement>(".local-model-base-url")!.value =
      settings.local.base_url;
    options.root.querySelector<HTMLInputElement>(".local-model-name")!.value =
      settings.local.model;
    renderRemoteProvider(settings.remote.provider);
    selectModelSource(settings.source);
    options.root.querySelector<HTMLElement>(".settings-active-model")!.textContent =
      `${settings.source === "local" ? "本地" : "远端"} · ${settings.active_model}`;
  }

  async function refreshLocalModels(announce = true): Promise<void> {
    const baseUrl = options.root.querySelector<HTMLInputElement>(".local-model-base-url")!.value;
    if (announce) setModelStatus("正在读取 Ollama 模型列表…", "saving");
    try {
      const response = await options.onRefreshLocalModels(baseUrl);
      replaceDataList("#local-model-options", response.models);
      const channel = options.root.querySelector<HTMLElement>('[data-model-channel="local"]')!;
      channel.dataset.state = response.connected ? "pass" : "fail";
      channel.querySelector("small")!.textContent = response.message;
      if (announce) {
        setModelStatus(response.message, response.connected ? "saved" : "error");
      }
    } catch (error) {
      if (announce) {
        setModelStatus(
          error instanceof Error ? error.message : "无法读取本地模型",
          "error",
        );
      }
    }
  }

  async function loadModelSettings(): Promise<void> {
    setModelStatus("正在读取模型配置…", "saving");
    try {
      const response = await options.onLoadModelSettings();
      if (!response.applied) throw new Error(response.error || "模型配置读取失败");
      renderModelSettings(response);
      setModelStatus("模型配置已就绪", "idle");
      void refreshLocalModels(false);
    } catch (error) {
      setModelStatus(
        error instanceof Error ? error.message : "无法读取模型配置",
        "error",
      );
    }
  }

  function collectModelSettings(): Record<string, unknown> {
    const apiKey = options.root.querySelector<HTMLInputElement>(".remote-api-key")!.value.trim();
    const remote: Record<string, unknown> = {
      provider: options.root.querySelector<HTMLSelectElement>(".remote-provider")!.value,
      model: options.root.querySelector<HTMLInputElement>(".remote-model-name")!.value.trim(),
      base_url: options.root.querySelector<HTMLInputElement>(".remote-model-base-url")!.value.trim(),
    };
    if (apiKey) remote.api_key = apiKey;
    return {
      source: activeModelSource,
      local: {
        model: options.root.querySelector<HTMLInputElement>(".local-model-name")!.value.trim(),
        base_url: options.root.querySelector<HTMLInputElement>(".local-model-base-url")!.value.trim(),
      },
      remote,
    };
  }

  async function saveModelSettings(): Promise<boolean> {
    setModelStatus("正在应用模型配置…", "saving");
    try {
      const response = await options.onSaveModelSettings(collectModelSettings());
      if (!response.applied) throw new Error(response.error || "模型配置保存失败");
      renderModelSettings(response);
      setModelStatus("已保存并应用到 Live", "saved");
      return true;
    } catch (error) {
      setModelStatus(
        error instanceof Error ? error.message : "模型配置保存失败",
        "error",
      );
      return false;
    }
  }

  function renderModelConnectionCheck(
    channelId: "local" | "remote",
    check: ModelDiagnosticReport["checks"][number] | undefined,
  ): void {
    const channel = options.root.querySelector<HTMLElement>(
      `[data-model-channel="${channelId}"]`,
    )!;
    channel.dataset.state = check?.status || "fail";
    channel.querySelector("small")!.textContent = check?.message || "未返回检测结果";
  }

  async function testModelConnections(): Promise<void> {
    if (!await saveModelSettings()) return;
    setModelStatus("正在连接本地与远端模型…", "saving");
    try {
      const report = await options.onTestModelConnections();
      renderModelConnectionCheck(
        "local",
        report.checks.find((check) => check.id === "local_model_connection"),
      );
      renderModelConnectionCheck(
        "remote",
        report.checks.find((check) => check.id === "remote_model_connection"),
      );
      const hasFailure = report.checks.some((check) =>
        ["local_model_connection", "remote_model_connection"].includes(check.id)
        && check.status === "fail"
      );
      setModelStatus(report.summary, hasFailure ? "error" : "saved");
    } catch (error) {
      setModelStatus(
        error instanceof Error ? error.message : "模型连接检测失败",
        "error",
      );
    }
  }

  function getSlotValue(slot: HTMLElement, kind: PetShortcutKind): string {
    if (kind === "javis") return slot.querySelector<HTMLSelectElement>(".shortcut-javis-value")!.value;
    if (kind === "target") return slot.querySelector<HTMLInputElement>(".shortcut-target-value")!.value;
    if (kind === "url") return slot.querySelector<HTMLInputElement>(".shortcut-url-value")!.value;
    if (kind === "hotkey") return slot.querySelector<HTMLInputElement>(".shortcut-hotkey-value")!.value;
    return "";
  }

  function setSlotValue(slot: HTMLElement, kind: PetShortcutKind, value: string): void {
    if (kind === "javis") slot.querySelector<HTMLSelectElement>(".shortcut-javis-value")!.value = value || "live";
    if (kind === "target") slot.querySelector<HTMLInputElement>(".shortcut-target-value")!.value = value;
    if (kind === "url") slot.querySelector<HTMLInputElement>(".shortcut-url-value")!.value = value;
    if (kind === "hotkey") slot.querySelector<HTMLInputElement>(".shortcut-hotkey-value")!.value = value;
  }

  function readSlot(slot: HTMLElement): PetShortcutSlot {
    const kind = slot.querySelector<HTMLSelectElement>(".shortcut-kind")!.value as PetShortcutKind;
    return {
      label: slot.querySelector<HTMLInputElement>(".shortcut-label")!.value,
      kind,
      value: getSlotValue(slot, kind),
    };
  }

  function updateSlot(slot: HTMLElement, targetExists?: boolean): void {
    const shortcut = readSlot(slot);
    const descriptor = getShortcutEditorDescriptor(shortcut.kind);
    slot.querySelectorAll<HTMLElement>("[data-control]").forEach((control) => {
      control.hidden = control.dataset.control !== descriptor.control;
    });
    slot.querySelector<HTMLElement>(".shortcut-help")!.textContent = descriptor.help;
    const status = getShortcutSlotStatus(shortcut, targetExists);
    const output = slot.querySelector<HTMLOutputElement>(".shortcut-status")!;
    output.dataset.status = status;
    output.value = STATUS_LABELS[status];
  }

  function collectPreferences(): PetPreferences {
    const current = readPetPreferences();
    const scale = Number(options.root.querySelector<HTMLInputElement>(".pet-scale-input")!.value) / 100;
    const shortcuts = Array.from(options.root.querySelectorAll<HTMLElement>(".settings-shortcut-card"))
      .map(readSlot);
    return { ...current, scale, shortcuts };
  }

  function persist(): void {
    const preferences = writePetPreferences(collectPreferences());
    const output = options.root.querySelector<HTMLOutputElement>(".pet-scale-output")!;
    output.value = `${Math.round(preferences.scale * 100)}%`;
    updateAssistantScalePreview(preferences.scale);
    options.root.querySelectorAll<HTMLElement>(".settings-shortcut-card").forEach((slot) => updateSlot(slot));
    saveState.textContent = "已保存";
    saveState.dataset.state = "saved";
    window.setTimeout(() => {
      saveState.textContent = "自动保存";
      saveState.dataset.state = "idle";
    }, 900);
    dispatchPetPreferences(preferences);
  }

  function selectSection(section: string): void {
    activeSection = section;
    options.root.querySelectorAll<HTMLElement>("[data-settings-section]").forEach((item) => {
      item.dataset.active = String(item.dataset.settingsSection === section);
    });
    options.root.querySelectorAll<HTMLElement>("[data-settings-pane]").forEach((item) => {
      item.dataset.active = String(item.dataset.settingsPane === section);
    });
  }

  function sync(): void {
    const preferences = readPetPreferences();
    const range = options.root.querySelector<HTMLInputElement>(".pet-scale-input")!;
    range.value = String(Math.round(preferences.scale * 100));
    options.root.querySelector<HTMLOutputElement>(".pet-scale-output")!.value = `${range.value}%`;
    updateAssistantScalePreview(preferences.scale);
    const storedNoiseProfile = readStringPreference("voice.noiseProfile", "standard");
    options.root.querySelector<HTMLSelectElement>(".voice-noise-profile")!.value =
      ["off", "standard", "strong"].includes(storedNoiseProfile)
        ? storedNoiseProfile
        : "standard";
    options.root.querySelectorAll<HTMLElement>(".settings-shortcut-card").forEach((slot, index) => {
      const shortcut = preferences.shortcuts[index] || { label: "", kind: "", value: "" };
      slot.querySelector<HTMLInputElement>(".shortcut-label")!.value = shortcut.label;
      slot.querySelector<HTMLSelectElement>(".shortcut-kind")!.value = shortcut.kind;
      setSlotValue(slot, shortcut.kind, shortcut.value);
      updateSlot(slot);
    });
    try {
      const skinId = localStorage.getItem("javis.app.petSkin") || getDefaultPetSkin().id;
      options.root.querySelector<HTMLSelectElement>(".pet-skin-select")!.value = getPetSkin(skinId).id;
      const palette = localStorage.getItem("javis.app.orbPalette") as OrbPaletteName | null;
      options.root.querySelector<HTMLSelectElement>(".orb-palette-select")!.value =
        PALETTES.some(([value]) => value === palette) ? palette! : "cobalt";
    } catch {
      options.root.querySelector<HTMLSelectElement>(".pet-skin-select")!.value = getDefaultPetSkin().id;
      options.root.querySelector<HTMLSelectElement>(".orb-palette-select")!.value = "cobalt";
    }
    selectSection(activeSection);
  }

  function close(): void {
    surface.dataset.open = "false";
    options.root.dataset.open = "false";
    recordingSlot = null;
    options.onClose(getSettingsReturnMode(sourceMode));
  }

  options.root.querySelector<HTMLButtonElement>(".settings-close")!.addEventListener("click", close);
  options.root.querySelectorAll<HTMLButtonElement>("[data-settings-section]").forEach((button) => {
    button.addEventListener("click", () => selectSection(button.dataset.settingsSection || "general"));
  });
  options.root.querySelector<HTMLInputElement>(".pet-scale-input")!.addEventListener("input", persist);
  options.root.querySelector<HTMLSelectElement>(".pet-skin-select")!.addEventListener("change", (event) => {
    document.dispatchEvent(new CustomEvent("javis:pet-skin-changed", {
      detail: (event.currentTarget as HTMLSelectElement).value,
    }));
  });
  options.root.querySelector<HTMLSelectElement>(".orb-palette-select")!.addEventListener("change", (event) => {
    options.onOrbPalette((event.currentTarget as HTMLSelectElement).value as OrbPaletteName);
  });
  options.root.querySelector<HTMLButtonElement>(".settings-diagnostics")!.addEventListener("click", options.onOpenDiagnostics);
  options.root.querySelector<HTMLButtonElement>(".settings-privacy")!.addEventListener("click", options.onOpenPrivacy);
  options.root.querySelector<HTMLSelectElement>(".voice-noise-profile")!.addEventListener("change", (event) => {
    const profile = (event.currentTarget as HTMLSelectElement).value;
    writeStringPreference("voice.noiseProfile", profile);
    document.dispatchEvent(new CustomEvent("javis:voice-profile-changed", { detail: profile }));
    saveState.textContent = "已保存";
    saveState.dataset.state = "saved";
  });
  options.root.querySelectorAll<HTMLButtonElement>("[data-model-source]").forEach((button) => {
    button.addEventListener("click", () => {
      selectModelSource(button.dataset.modelSource as ModelSource);
      setModelStatus("来源已选择，点击“保存并应用”后生效", "idle");
    });
  });
  options.root.querySelector<HTMLSelectElement>(".remote-provider")!.addEventListener("change", (event) => {
    renderRemoteProvider((event.currentTarget as HTMLSelectElement).value);
    setModelStatus("已载入该提供商的独立配置", "idle");
  });
  options.root.querySelector<HTMLButtonElement>(".local-model-refresh")!.addEventListener(
    "click",
    () => void refreshLocalModels(),
  );
  options.root.querySelector<HTMLButtonElement>(".model-save")!.addEventListener(
    "click",
    () => void saveModelSettings(),
  );
  options.root.querySelector<HTMLButtonElement>(".model-test-connections")!.addEventListener(
    "click",
    () => void testModelConnections(),
  );
  options.root.querySelectorAll<HTMLElement>("[data-path-key]").forEach((row) => {
    row.querySelector<HTMLButtonElement>(".settings-path-select")!.addEventListener(
      "click",
      () => void selectPathSetting(row),
    );
  });

  options.root.querySelectorAll<HTMLElement>(".settings-shortcut-card").forEach((slot) => {
    slot.querySelector<HTMLInputElement>(".shortcut-label")!.addEventListener("input", persist);
    slot.querySelector<HTMLSelectElement>(".shortcut-kind")!.addEventListener("change", (event) => {
      const kind = (event.currentTarget as HTMLSelectElement).value as PetShortcutKind;
      slot.querySelectorAll<HTMLInputElement>(".shortcut-target-value, .shortcut-url-value, .shortcut-hotkey-value")
        .forEach((input) => { input.value = ""; });
      slot.querySelector<HTMLSelectElement>(".shortcut-javis-value")!.value = "live";
      updateSlot(slot);
      persist();
      if (kind === "hotkey") slot.querySelector<HTMLButtonElement>(".shortcut-record")!.focus();
    });
    slot.querySelectorAll<HTMLInputElement>(".shortcut-target-value, .shortcut-url-value")
      .forEach((input) => input.addEventListener("input", persist));
    slot.querySelector<HTMLSelectElement>(".shortcut-javis-value")!.addEventListener("change", persist);
    const pickTarget = async (kind: "file" | "directory"): Promise<void> => {
      const selected = await options.onPickTarget(kind);
      if (!selected) return;
      slot.querySelector<HTMLInputElement>(".shortcut-target-value")!.value = selected;
      persist();
    };
    slot.querySelector<HTMLButtonElement>(".shortcut-browse-file")!.addEventListener(
      "click",
      () => void pickTarget("file"),
    );
    slot.querySelector<HTMLButtonElement>(".shortcut-browse-directory")!.addEventListener(
      "click",
      () => void pickTarget("directory"),
    );
    const recordButton = slot.querySelector<HTMLButtonElement>(".shortcut-record")!;
    recordButton.addEventListener("click", () => {
      recordingSlot = slot;
      recordButton.textContent = "请按组合键";
      recordButton.dataset.recording = "true";
      recordButton.focus();
    });
    recordButton.addEventListener("keydown", (event) => {
      if (recordingSlot !== slot) return;
      event.preventDefault();
      event.stopPropagation();
      const value = normalizeRecordedHotkey(event);
      if (!value) return;
      slot.querySelector<HTMLInputElement>(".shortcut-hotkey-value")!.value = value;
      recordButton.textContent = "重新录制";
      recordButton.dataset.recording = "false";
      recordingSlot = null;
      persist();
    });
    slot.querySelector<HTMLButtonElement>(".shortcut-clear")!.addEventListener("click", () => {
      slot.querySelector<HTMLInputElement>(".shortcut-label")!.value = "";
      slot.querySelector<HTMLSelectElement>(".shortcut-kind")!.value = "";
      slot.querySelectorAll<HTMLInputElement>(".shortcut-target-value, .shortcut-url-value, .shortcut-hotkey-value")
        .forEach((input) => { input.value = ""; });
      updateSlot(slot);
      persist();
    });
  });

  const handleKeyDown = (event: KeyboardEvent): void => {
    if (event.key === "Escape" && options.root.dataset.open === "true" && !recordingSlot) {
      event.preventDefault();
      close();
    }
  };
  document.addEventListener("keydown", handleKeyDown);
  selectSection("general");

  return {
    open(nextSourceMode, section) {
      sourceMode = getSettingsReturnMode(nextSourceMode);
      if (section) activeSection = section;
      sync();
      void loadPathSettings();
      void loadModelSettings();
      options.root.dataset.open = "true";
      surface.dataset.open = "true";
      document.body.dataset.surface = "settings";
    },
    close,
    sync,
    dispose() {
      document.removeEventListener("keydown", handleKeyDown);
      options.root.innerHTML = "";
    },
  };
}
