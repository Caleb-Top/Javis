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
  share_live_code: boolean;
  routes: Record<"live" | "code", {
    source: ModelSource;
    local: { model: string; base_url: string };
    remote: { provider: string; model: string; base_url: string };
  }>;
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
  state?: "connected" | "not_installed" | "offline";
  models: string[];
  message: string;
};

export type RemoteModelCatalogResponse = {
  connected: boolean;
  models: RemoteProviderOption["models"];
  discovered_count?: number;
  message: string;
};

export type ModelAddonDetectionResponse = {
  ok: boolean;
  detected: boolean;
  manifest_path?: string;
  source_dir?: string;
  suggested_install_dir?: string;
  title?: string;
  model?: string;
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

export type ModelInstallPlan = {
  ok: boolean;
  source?: "offline" | "local_gguf" | "huggingface";
  title?: string;
  model?: string;
  base_url?: string;
  repo_id?: string;
  filename?: string;
  gguf_path?: string;
  available_files?: Array<{ filename: string; size: number }>;
  install_dir?: string;
  download_bytes?: number;
  required_bytes?: number;
  free_bytes?: number;
  license?: string;
  gated?: boolean;
  runtime_ready?: boolean;
  restart_required?: boolean;
  configuration_applied?: boolean;
  message?: string;
  error?: string;
};

export type ModelInstallProgress = {
  state: "idle" | "running" | "completed" | "failed";
  phase: string;
  percent: number;
  completed_bytes: number;
  total_bytes: number;
};

export type HuggingFaceSearchResponse = {
  ok: boolean;
  models: Array<{ repo_id: string; downloads: number; likes: number; gated: boolean }>;
  error?: string;
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
  onLoadVoiceDevices: () => Promise<{ input_devices: Array<{ index: number; name: string }> }>;
  onRefreshLocalModels: (baseUrl: string) => Promise<LocalModelCatalogResponse>;
  onRefreshRemoteModels: (
    provider: string,
    baseUrl: string,
    apiKey: string,
  ) => Promise<RemoteModelCatalogResponse>;
  onDetectModelAddon: (path: string) => Promise<ModelAddonDetectionResponse>;
  onGetModelInstallProgress: () => Promise<ModelInstallProgress>;
  onTestModelConnections: () => Promise<ModelDiagnosticReport>;
  onSearchHuggingFace: (query: string) => Promise<HuggingFaceSearchResponse>;
  onPlanModelInstall: (settings: Record<string, unknown>) => Promise<ModelInstallPlan>;
  onInstallModel: (settings: Record<string, unknown>) => Promise<ModelInstallPlan>;
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
  let activeModelRoute: "live" | "code" = "live";
  let modelRouteDrafts: ModelConnectionSettingsResponse["routes"] | null = null;
  let modelSettings: ModelConnectionSettingsResponse | null = null;
  const remoteModelCatalogs = new Map<string, RemoteProviderOption["models"]>();
  let modelInstallPlan: ModelInstallPlan | null = null;
  let modelInstallInProgress = false;
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
              <label class="settings-field">
                <span>麦克风设备</span>
                <div class="settings-input-action">
                  <select class="voice-input-device">
                    <option value="">默认麦克风</option>
                  </select>
                  <button class="settings-secondary-button voice-device-refresh" type="button">刷新</button>
                </div>
                <small>选择用于连续语音识别的输入设备，留空则使用系统默认麦克风。</small>
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
            <div class="settings-title"><small>MODEL ROUTING</small><h1>模型与存储</h1><span>从 Live 或 Code 打开的都是这一个设置中心；两边可共享，也可分别使用本地模型或云端 API。</span></div>
            <div class="settings-panel settings-model-panel">
              <div class="settings-panel-heading">
                <div><strong>推理来源</strong><small>本地优先低延迟，远端用于更强推理。</small></div>
                <span class="settings-active-model">正在读取…</span>
              </div>
              <div class="model-route-toolbar">
                <div class="model-route-segment" role="tablist" aria-label="配置目标">
                  <button type="button" role="tab" data-model-route="live">Live</button>
                  <button type="button" role="tab" data-model-route="code">Code</button>
                </div>
                <label class="model-route-share"><input class="model-share-routes" type="checkbox"> Code 与 Live 共用此配置</label>
              </div>
              <div class="model-source-segment" role="tablist" aria-label="推理来源">
                <button type="button" role="tab" data-model-source="local">本地模型</button>
                <button type="button" role="tab" data-model-source="remote">远端 API</button>
              </div>
              <div class="settings-model-profile" data-model-profile="local">
                <label class="settings-field">
                  <span>Ollama 地址</span>
                  <input class="local-model-base-url" type="url" maxlength="2048" placeholder="http://127.0.0.1:11435/v1">
                </label>
                <label class="settings-field">
                  <span>本地模型</span>
                  <div class="settings-input-action settings-model-input-action">
                    <input class="local-model-name" type="text" list="local-model-options" maxlength="240" placeholder="正在读取或选择模型…">
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
                    <div class="settings-input-action settings-remote-model-action">
                      <select class="remote-model-name" aria-label="远端模型完整列表"></select>
                      <button class="settings-secondary-button remote-model-refresh" type="button">同步供应商</button>
                    </div>
                    <input class="remote-model-custom-name" type="text" maxlength="240" placeholder="输入供应商发布的新模型 ID" hidden>
                    <small class="remote-model-catalog-summary">正在读取供应商模型目录…</small>
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
              <div class="settings-local-installer">
                <div><strong>本地模型安装向导</strong><small>无需云 API。先选附加包来源，再另选模型安装位置；确认计划前不会写入或下载。</small></div>
                <button class="settings-secondary-button model-open-installer" type="button">安装或导入模型</button>
              </div>
              <div class="model-installer-panel" hidden>
                <ol class="model-installer-steps" aria-label="本地模型安装步骤">
                  <li data-installer-step="source" data-active="true"><b>1</b><span>选择来源</span></li>
                  <li data-installer-step="plan"><b>2</b><span>自动检查</span></li>
                  <li data-installer-step="consent"><b>3</b><span>确认安装</span></li>
                  <li data-installer-step="progress"><b>4</b><span>自动适配</span></li>
                </ol>
                <div class="settings-model-grid">
                  <label class="settings-field"><span>安装来源</span><select class="model-installer-source"><option value="offline">R1 离线附加包（首次推荐）</option><option value="local_gguf">导入已有 GGUF 文件</option><option value="huggingface">从 Hugging Face 自动安装</option></select></label>
                  <label class="settings-field"><span>模型安装位置（不能选附加包目录）</span><div class="settings-input-action"><input class="model-installer-directory" type="text" readonly placeholder="例如 G:\\Javis-Local-Models"><button class="settings-secondary-button model-installer-pick-directory" type="button">选择</button></div></label>
                </div>
                <div class="model-installer-source-panel" data-installer-source="offline">
                  <label class="settings-field"><span>R1 附加包来源目录</span><div class="settings-input-action"><input class="model-addon-path" type="text" readonly placeholder="包含 Javis-R1-8B-Addon.manifest.json 的目录"><button class="settings-secondary-button model-addon-browse" type="button">选择目录</button></div></label>
                </div>
                <div class="model-installer-source-panel" data-installer-source="local_gguf" hidden>
                  <label class="settings-field"><span>现有 GGUF 模型文件</span><div class="settings-input-action"><input class="local-gguf-path" type="text" readonly placeholder="选择电脑上已有的 .gguf 文件"><button class="settings-secondary-button local-gguf-browse" type="button">选择文件</button></div></label>
                  <small class="model-installer-hint">向导会复制、校验并自动生成 Ollama 适配配置；安装位置须已通过 R1 附加包安装运行时。</small>
                </div>
                <div class="model-installer-source-panel" data-installer-source="huggingface" hidden>
                  <label class="settings-field"><span>搜索 Hugging Face</span><div class="settings-input-action"><input class="hf-search-query" type="search" placeholder="例如 Qwen GGUF"><button class="settings-secondary-button hf-search" type="button">自动搜索</button></div></label>
                  <label class="settings-field"><span>模型仓库</span><input class="hf-repo-id" type="text" list="hf-repo-options" placeholder="owner/model"><datalist id="hf-repo-options"></datalist></label>
                  <label class="settings-field"><span>GGUF 文件（留空则自动推荐量化版本）</span><input class="hf-filename" type="text" list="hf-file-options" placeholder="自动优先 Q4_K_M"><datalist id="hf-file-options"></datalist></label>
                  <label class="settings-field"><span>Hugging Face Token（仅受限模型需要）</span><input class="hf-token" type="password" autocomplete="off" placeholder="只用于本次请求，不保存"></label>
                  <small class="model-installer-hint">确认后由 Javis 自动下载、校验 SHA-256、生成 Modelfile 并适配 Ollama，无需手动访问网页。</small>
                </div>
                <div class="model-installer-plan"><output class="model-installer-status" aria-live="polite">先选择来源与目录，再生成安装计划。</output></div>
                <div class="model-installer-progress" hidden><progress max="100" value="0"></progress><span>0%</span></div>
                <label class="model-installer-consent"><input type="checkbox" class="model-installer-approved"> 我确认安装位置、下载大小与模型许可，并允许 Javis 写入所选目录</label>
                <div class="settings-model-actions"><span></span><button class="settings-secondary-button model-installer-plan-button" type="button">生成安装计划</button><button class="settings-primary-button model-installer-install" type="button" disabled>确认安装</button></div>
              </div>
              <div class="settings-model-actions">
                <output class="settings-model-status" aria-live="polite">配置尚未加载</output>
                <button class="settings-secondary-button model-test-connections" type="button">检测当前连接</button>
                <button class="settings-primary-button model-save" type="button">保存并应用</button>
              </div>
            </div>
            <div class="settings-subheading"><strong>数据目录</strong><small>使用 Windows 原生目录选择器授权。</small></div>
            <div class="settings-panel settings-path-list">
              ${pathSettingTemplate("model_dir", "外部 Ollama 模型存储（高级）", "仅供已有外部 Ollama 使用；不是 R1 附加包来源，也不是安装向导目标")}
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

  async function recognizeAddonPath(path: string, announce = true): Promise<boolean> {
    if (!path) return false;
    const response = await options.onDetectModelAddon(path);
    if (!response.ok || !response.detected) return false;
    const panel = options.root.querySelector<HTMLElement>(".model-installer-panel")!;
    const source = options.root.querySelector<HTMLSelectElement>(".model-installer-source")!;
    panel.hidden = false;
    source.value = "offline";
    options.root.querySelectorAll<HTMLElement>("[data-installer-source]").forEach((item) => {
      item.hidden = item.dataset.installerSource !== "offline";
    });
    options.root.querySelector<HTMLInputElement>(".model-addon-path")!.value =
      response.source_dir || response.manifest_path || path;
    const target = options.root.querySelector<HTMLInputElement>(".model-installer-directory")!;
    if (!target.value || target.value === path) target.value = response.suggested_install_dir || "";
    modelInstallPlan = null;
    options.root.querySelector<HTMLButtonElement>(".model-installer-install")!.disabled = true;
    setInstallerStatus(
      `已识别 ${response.title || "R1 附加包"}；请确认另一个模型安装位置后生成计划`,
      "saved",
    );
    if (announce) {
      setPathStatus("这里是 R1 附加包来源，不是模型存储目录；已转到安装向导，尚未修改配置", "saved");
    }
    return true;
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
      const recognized = await recognizeAddonPath(response.paths.model_dir, false);
      setPathStatus(
        recognized
          ? "检测到旧配置把 R1 附加包当成模型目录；已为你转到安装向导，尚未写入任何文件"
          : "目录配置已就绪",
        recognized ? "saved" : "idle",
      );
    } catch (error) {
      setPathStatus(error instanceof Error ? error.message : "无法读取目录配置", "error");
    }
  }

  async function selectPathSetting(row: HTMLElement): Promise<void> {
    const key = row.dataset.pathKey as PathSettingKey;
    const selected = await options.onPickTarget("directory");
    if (!selected) return;
    if (key === "model_dir" && await recognizeAddonPath(selected)) return;
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

  function getRemoteModelValue(): string {
    const select = options.root.querySelector<HTMLSelectElement>(".remote-model-name")!;
    if (select.value !== "__custom__") return select.value.trim();
    return options.root.querySelector<HTMLInputElement>(".remote-model-custom-name")!.value.trim();
  }

  function renderRemoteModelOptions(
    provider: RemoteProviderOption,
    selectedModel: string,
  ): void {
    const models = remoteModelCatalogs.get(provider.id) || provider.models;
    const select = options.root.querySelector<HTMLSelectElement>(".remote-model-name")!;
    const customInput = options.root.querySelector<HTMLInputElement>(".remote-model-custom-name")!;
    const optionsList = models.map((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = `${model.label} · ${model.id}${model.description ? ` — ${model.description}` : ""}`;
      return option;
    });
    const known = models.some((model) => model.id === selectedModel);
    if (selectedModel && !known) {
      const current = document.createElement("option");
      current.value = selectedModel;
      current.textContent = `当前自定义 · ${selectedModel}`;
      optionsList.push(current);
    }
    const custom = document.createElement("option");
    custom.value = "__custom__";
    custom.textContent = "自定义模型 ID…";
    optionsList.push(custom);
    select.replaceChildren(...optionsList);
    select.value = selectedModel || models[0]?.id || "__custom__";
    customInput.hidden = select.value !== "__custom__";
    if (!customInput.hidden && selectedModel) customInput.value = selectedModel;
    options.root.querySelector<HTMLElement>(".remote-model-catalog-summary")!.textContent =
      `${provider.label}：已列出 ${models.length} 个兼容模型；也可同步账户目录或自定义模型 ID`;
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
    renderRemoteModelOptions(provider, profile.model);
    options.root.querySelector<HTMLInputElement>(".remote-model-base-url")!.value = profile.base_url;

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
    modelRouteDrafts = structuredClone(settings.routes);
    const providerSelect = options.root.querySelector<HTMLSelectElement>(".remote-provider")!;
    providerSelect.replaceChildren(...settings.providers.map((provider) => {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.label;
      return option;
    }));
    options.root.querySelector<HTMLInputElement>(".model-share-routes")!.checked = settings.share_live_code;
    activeModelRoute = sourceMode === "code" ? "code" : "live";
    renderModelRoute(activeModelRoute);
    options.root.querySelector<HTMLElement>(".settings-active-model")!.textContent =
      settings.share_live_code
        ? `Live + Code 共用 · ${settings.routes.live.source === "local" ? "本地" : "云端"}`
        : "Live / Code 独立路由";
  }

  function snapshotActiveModelRoute(): void {
    if (!modelRouteDrafts) return;
    modelRouteDrafts[activeModelRoute] = {
      source: activeModelSource,
      local: {
        model: options.root.querySelector<HTMLInputElement>(".local-model-name")!.value.trim(),
        base_url: options.root.querySelector<HTMLInputElement>(".local-model-base-url")!.value.trim(),
      },
      remote: {
        provider: options.root.querySelector<HTMLSelectElement>(".remote-provider")!.value,
        model: getRemoteModelValue(),
        base_url: options.root.querySelector<HTMLInputElement>(".remote-model-base-url")!.value.trim(),
      },
    };
  }

  function renderModelRoute(routeName: "live" | "code"): void {
    if (!modelSettings || !modelRouteDrafts) return;
    activeModelRoute = routeName;
    const route = modelRouteDrafts[routeName];
    options.root.querySelectorAll<HTMLButtonElement>("[data-model-route]").forEach((button) => {
      const selected = button.dataset.modelRoute === routeName;
      button.dataset.active = String(selected);
      button.setAttribute("aria-selected", String(selected));
    });
    options.root.querySelector<HTMLInputElement>(".local-model-base-url")!.value = route.local.base_url;
    options.root.querySelector<HTMLInputElement>(".local-model-name")!.value = route.local.model;
    renderRemoteProvider(route.remote.provider);
    const provider = modelSettings.providers.find((candidate) => candidate.id === route.remote.provider)
      || modelSettings.providers[0];
    if (provider) renderRemoteModelOptions(provider, route.remote.model);
    options.root.querySelector<HTMLInputElement>(".remote-model-base-url")!.value = route.remote.base_url;
    selectModelSource(route.source);
  }

  function modelInstallValues(): Record<string, unknown> {
    const source = options.root.querySelector<HTMLSelectElement>(".model-installer-source")!.value;
    return {
      source,
      install_dir: options.root.querySelector<HTMLInputElement>(".model-installer-directory")!.value,
      addon_path: options.root.querySelector<HTMLInputElement>(".model-addon-path")!.value,
      gguf_path: options.root.querySelector<HTMLInputElement>(".local-gguf-path")!.value,
      repo_id: options.root.querySelector<HTMLInputElement>(".hf-repo-id")!.value.trim(),
      filename: options.root.querySelector<HTMLInputElement>(".hf-filename")!.value.trim(),
      token: options.root.querySelector<HTMLInputElement>(".hf-token")!.value,
    };
  }

  function formatBytes(value = 0): string {
    if (!Number.isFinite(value) || value <= 0) return "未知";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = value;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return `${size.toFixed(unit >= 3 ? 2 : 1)} ${units[unit]}`;
  }

  function setInstallerStatus(message: string, state: "idle" | "saving" | "saved" | "error"): void {
    const output = options.root.querySelector<HTMLOutputElement>(".model-installer-status")!;
    output.value = message;
    output.dataset.state = state;
  }

  function setInstallerStep(step: "source" | "plan" | "consent" | "progress"): void {
    const order = ["source", "plan", "consent", "progress"];
    const activeIndex = order.indexOf(step);
    options.root.querySelectorAll<HTMLElement>("[data-installer-step]").forEach((item) => {
      const index = order.indexOf(item.dataset.installerStep || "");
      item.dataset.active = String(index === activeIndex);
      item.dataset.complete = String(index < activeIndex);
    });
  }

  async function planModelInstall(): Promise<void> {
    modelInstallPlan = null;
    options.root.querySelector<HTMLButtonElement>(".model-installer-install")!.disabled = true;
    setInstallerStep("plan");
    setInstallerStatus("正在检查文件、空间与模型信息…", "saving");
    try {
      const response = await options.onPlanModelInstall(modelInstallValues());
      if (!response.ok) throw new Error(response.error || "无法生成安装计划");
      modelInstallPlan = response;
      if (response.available_files?.length) {
        replaceDataList("#hf-file-options", response.available_files.map((item) => item.filename));
        const filename = options.root.querySelector<HTMLInputElement>(".hf-filename")!;
        if (!filename.value && response.filename) filename.value = response.filename;
      }
      const details = response.source === "offline"
        ? `${response.title}；需写入约 ${formatBytes(response.required_bytes)}；离线校验并安装 Ollama + R1`
        : response.source === "local_gguf"
          ? `${response.filename}；导入约 ${formatBytes(response.required_bytes)}${response.runtime_ready === false ? "；该位置缺少 Javis Ollama，请先安装 R1 附加包" : "；可自动适配"}`
          : `${response.title} / ${response.filename || "请选择 GGUF"}；下载约 ${formatBytes(response.download_bytes)}；许可：${response.license || "未声明"}${response.gated ? "；需要访问授权" : ""}${response.runtime_ready === false ? "；该位置缺少 Javis Ollama，请先安装 R1 附加包" : "；将自动下载、校验并适配"}`;
      setInstallerStatus(details, "saved");
      setInstallerStep("consent");
      const approved = options.root.querySelector<HTMLInputElement>(".model-installer-approved")!.checked;
      options.root.querySelector<HTMLButtonElement>(".model-installer-install")!.disabled =
        !approved || (response.source !== "offline" && response.runtime_ready === false);
    } catch (error) {
      setInstallerStatus(error instanceof Error ? error.message : "无法生成安装计划", "error");
    }
  }

  async function installModel(): Promise<void> {
    if (modelInstallInProgress) {
      setInstallerStatus("已有安装任务正在进行，请勿重复提交", "idle");
      return;
    }
    if (!modelInstallPlan) {
      setInstallerStatus("请先生成并核对安装计划", "error");
      return;
    }
    const approved = options.root.querySelector<HTMLInputElement>(".model-installer-approved")!.checked;
    if (!approved) {
      setInstallerStatus("需要勾选用户确认后才能安装", "error");
      return;
    }
    modelInstallInProgress = true;
    const installButton = options.root.querySelector<HTMLButtonElement>(".model-installer-install")!;
    const planButton = options.root.querySelector<HTMLButtonElement>(".model-installer-plan-button")!;
    installButton.disabled = true;
    planButton.disabled = true;
    setInstallerStep("progress");
    const progressPanel = options.root.querySelector<HTMLElement>(".model-installer-progress")!;
    const progressBar = progressPanel.querySelector<HTMLProgressElement>("progress")!;
    const progressLabel = progressPanel.querySelector<HTMLElement>("span")!;
    progressPanel.hidden = false;
    progressBar.value = 0;
    progressLabel.textContent = "0%";
    const startedAt = Date.now();
    let progressRequestPending = false;
    const refreshProgress = async (): Promise<void> => {
      if (progressRequestPending) return;
      progressRequestPending = true;
      try {
        const progress = await options.onGetModelInstallProgress();
        progressBar.value = progress.percent;
        progressLabel.textContent = `${Math.round(progress.percent)}% · ${progress.phase}`;
        if (progress.state === "running") setInstallerStatus(progress.phase, "saving");
      } catch {
        // The install request remains authoritative if one progress poll is missed.
      } finally {
        progressRequestPending = false;
      }
    };
    const progressTimer = window.setInterval(() => void refreshProgress(), 750);
    void refreshProgress();
    const elapsedTimer = window.setInterval(() => {
      const elapsedMinutes = Math.max(1, Math.floor((Date.now() - startedAt) / 60_000));
      if (progressBar.value === 0) setInstallerStatus(`安装任务已提交（${elapsedMinutes} 分钟）；正在等待进度…`, "saving");
    }, 15_000);
    setInstallerStatus("自动安装已开始；正在读取真实进度…", "saving");
    try {
      const response = await options.onInstallModel({
        ...modelInstallValues(),
        approved: true,
        confirmation: "install-local-model",
      });
      if (!response.ok) throw new Error(response.error || "模型安装失败");
      if (response.model) options.root.querySelector<HTMLInputElement>(".local-model-name")!.value = response.model;
      if (response.base_url) options.root.querySelector<HTMLInputElement>(".local-model-base-url")!.value = response.base_url;
      if (modelRouteDrafts && response.model && response.base_url) {
        for (const route of Object.values(modelRouteDrafts)) {
          route.local = { model: response.model, base_url: response.base_url };
        }
        await saveModelSettings();
      }
      modelInstallPlan = null;
      progressBar.value = 100;
      progressLabel.textContent = "100% · 安装与自动适配已完成";
      setInstallerStatus(response.message || "安装完成；重启 Javis 后启用", "saved");
    } catch (error) {
      setInstallerStatus(error instanceof Error ? error.message : "模型安装失败", "error");
    } finally {
      window.clearInterval(progressTimer);
      window.clearInterval(elapsedTimer);
      modelInstallInProgress = false;
      planButton.disabled = false;
      installButton.disabled = !modelInstallPlan
        || !options.root.querySelector<HTMLInputElement>(".model-installer-approved")!.checked;
    }
  }

  async function refreshLocalModels(announce = true): Promise<void> {
    const baseUrl = options.root.querySelector<HTMLInputElement>(".local-model-base-url")!.value;
    if (announce) setModelStatus("正在读取 Ollama 模型列表…", "saving");
    try {
      const response = await options.onRefreshLocalModels(baseUrl);
      replaceDataList("#local-model-options", response.models);
      const channel = options.root.querySelector<HTMLElement>('[data-model-channel="local"]')!;
      channel.dataset.state = response.connected ? "pass" : response.state === "not_installed" ? "idle" : "fail";
      channel.querySelector("small")!.textContent = response.message;
      if (announce) {
        setModelStatus(
          response.message,
          response.connected ? "saved" : response.state === "not_installed" ? "idle" : "error",
        );
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

  async function refreshRemoteModels(announce = true): Promise<void> {
    if (!modelSettings) return;
    const providerId = options.root.querySelector<HTMLSelectElement>(".remote-provider")!.value;
    const provider = modelSettings.providers.find((candidate) => candidate.id === providerId);
    if (!provider) return;
    const selectedModel = getRemoteModelValue();
    const baseUrl = options.root.querySelector<HTMLInputElement>(".remote-model-base-url")!.value;
    const apiKey = options.root.querySelector<HTMLInputElement>(".remote-api-key")!.value.trim();
    if (announce) setModelStatus(`正在同步 ${provider.label} 的账户模型目录…`, "saving");
    try {
      const response = await options.onRefreshRemoteModels(provider.id, baseUrl, apiKey);
      if (response.models.length) remoteModelCatalogs.set(provider.id, response.models);
      renderRemoteModelOptions(provider, selectedModel);
      options.root.querySelector<HTMLElement>(".remote-model-catalog-summary")!.textContent = response.message;
      const channel = options.root.querySelector<HTMLElement>('[data-model-channel="remote"]')!;
      channel.dataset.state = response.connected ? "pass" : "idle";
      channel.querySelector("small")!.textContent = response.message;
      if (announce) setModelStatus(response.message, response.connected ? "saved" : "idle");
    } catch (error) {
      const message = error instanceof Error ? error.message : "无法同步供应商模型";
      options.root.querySelector<HTMLElement>(".remote-model-catalog-summary")!.textContent =
        `${message}；已保留内置兼容模型目录`;
      if (announce) setModelStatus(message, "error");
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
      void refreshRemoteModels(false);
    } catch (error) {
      setModelStatus(
        error instanceof Error ? error.message : "无法读取模型配置",
        "error",
      );
    }
  }

  function collectModelSettings(): Record<string, unknown> {
    snapshotActiveModelRoute();
    const apiKey = options.root.querySelector<HTMLInputElement>(".remote-api-key")!.value.trim();
    const remote: Record<string, unknown> = {
      provider: options.root.querySelector<HTMLSelectElement>(".remote-provider")!.value,
      model: getRemoteModelValue(),
      base_url: options.root.querySelector<HTMLInputElement>(".remote-model-base-url")!.value.trim(),
    };
    if (apiKey) remote.api_key = apiKey;
    if (modelRouteDrafts) {
      modelRouteDrafts[activeModelRoute].remote = {
        ...(modelRouteDrafts[activeModelRoute].remote || {}),
        ...remote,
      } as ModelConnectionSettingsResponse["routes"]["live"]["remote"];
    }
    return {
      source: activeModelSource,
      local: {
        model: options.root.querySelector<HTMLInputElement>(".local-model-name")!.value.trim(),
        base_url: options.root.querySelector<HTMLInputElement>(".local-model-base-url")!.value.trim(),
      },
      remote,
      share_live_code: options.root.querySelector<HTMLInputElement>(".model-share-routes")!.checked,
      routes: modelRouteDrafts,
    };
  }

  async function saveModelSettings(): Promise<boolean> {
    setModelStatus("正在应用模型配置…", "saving");
    try {
      const response = await options.onSaveModelSettings(collectModelSettings());
      if (!response.applied) throw new Error(response.error || "模型配置保存失败");
      renderModelSettings(response);
      setModelStatus("Live 与 Code 模型路由已保存并立即生效", "saved");
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
    snapshotActiveModelRoute();
    const shared = options.root.querySelector<HTMLInputElement>(".model-share-routes")!.checked;
    const routes = modelRouteDrafts
      ? (shared ? [modelRouteDrafts.live] : [modelRouteDrafts.live, modelRouteDrafts.code])
      : [];
    const localEnabled = routes.some((route) => route.source === "local");
    const remoteEnabled = routes.some((route) => route.source === "remote");
    setModelStatus("正在检测当前启用的模型连接…", "saving");
    try {
      const report = await options.onTestModelConnections();
      const localCheck = report.checks.find((check) => check.id === "local_model_connection");
      const remoteCheck = report.checks.find((check) => check.id === "remote_model_connection");
      if (localEnabled) renderModelConnectionCheck("local", localCheck);
      else {
        const channel = options.root.querySelector<HTMLElement>('[data-model-channel="local"]')!;
        channel.dataset.state = "idle";
        channel.querySelector("small")!.textContent = "当前未启用（不影响云端模式）";
      }
      if (remoteEnabled) renderModelConnectionCheck("remote", remoteCheck);
      else {
        const channel = options.root.querySelector<HTMLElement>('[data-model-channel="remote"]')!;
        channel.dataset.state = "idle";
        channel.querySelector("small")!.textContent = "当前未启用（不影响本地模式）";
      }
      const hasFailure = report.checks.some((check) =>
        ((check.id === "local_model_connection" && localEnabled)
          || (check.id === "remote_model_connection" && remoteEnabled))
        && check.status === "fail"
      );
      setModelStatus(hasFailure ? report.summary : "当前启用的模型连接已检测", hasFailure ? "error" : "saved");
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

  function loadVoiceDevices(select: HTMLSelectElement): void {
    select.disabled = true;
    void options.onLoadVoiceDevices().then(({ input_devices }) => {
      const stored = readStringPreference("voice.inputDevice", "");
      select.innerHTML = '<option value="">默认麦克风</option>';
      for (const device of input_devices || []) {
        const option = document.createElement("option");
        option.value = String(device.index);
        option.textContent = device.name || `设备 ${device.index}`;
        select.append(option);
      }
      if (stored && [...select.options].some((option) => option.value === stored)) {
        select.value = stored;
      }
    }).catch(() => {
      select.innerHTML = '<option value="">默认麦克风</option>';
    }).finally(() => {
      select.disabled = false;
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
    const deviceSelect = options.root.querySelector<HTMLSelectElement>(".voice-input-device")!;
    loadVoiceDevices(deviceSelect);
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
  options.root.querySelector<HTMLSelectElement>(".voice-input-device")!.addEventListener("change", (event) => {
    const device = (event.currentTarget as HTMLSelectElement).value;
    writeStringPreference("voice.inputDevice", device);
    document.dispatchEvent(new CustomEvent("javis:voice-device-changed", { detail: device }));
    saveState.textContent = "已保存";
    saveState.dataset.state = "saved";
  });
  options.root.querySelector<HTMLButtonElement>(".voice-device-refresh")!.addEventListener("click", (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    const select = button.previousElementSibling as HTMLSelectElement;
    loadVoiceDevices(select);
  });
  options.root.querySelectorAll<HTMLButtonElement>("[data-model-source]").forEach((button) => {
    button.addEventListener("click", () => {
      selectModelSource(button.dataset.modelSource as ModelSource);
      setModelStatus("来源已选择，点击“保存并应用”后生效", "idle");
    });
  });
  options.root.querySelectorAll<HTMLButtonElement>("[data-model-route]").forEach((button) => {
    button.addEventListener("click", () => {
      snapshotActiveModelRoute();
      renderModelRoute(button.dataset.modelRoute === "code" ? "code" : "live");
      setModelStatus(`正在编辑 ${button.dataset.modelRoute === "code" ? "Code" : "Live"} 路由`, "idle");
    });
  });
  options.root.querySelector<HTMLInputElement>(".model-share-routes")!.addEventListener("change", (event) => {
    const shared = (event.currentTarget as HTMLInputElement).checked;
    setModelStatus(shared ? "保存后 Code 将使用 Live 的同一配置" : "保存后 Live 与 Code 可独立配置", "idle");
  });
  options.root.querySelector<HTMLSelectElement>(".remote-provider")!.addEventListener("change", (event) => {
    renderRemoteProvider((event.currentTarget as HTMLSelectElement).value);
    setModelStatus("已载入该提供商的独立配置", "idle");
    void refreshRemoteModels(false);
  });
  options.root.querySelector<HTMLSelectElement>(".remote-model-name")!.addEventListener("change", (event) => {
    const customInput = options.root.querySelector<HTMLInputElement>(".remote-model-custom-name")!;
    customInput.hidden = (event.currentTarget as HTMLSelectElement).value !== "__custom__";
    if (!customInput.hidden) customInput.focus();
  });
  options.root.querySelector<HTMLButtonElement>(".remote-model-refresh")!.addEventListener(
    "click",
    () => void refreshRemoteModels(),
  );
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
  options.root.querySelector<HTMLButtonElement>(".model-open-installer")!.addEventListener("click", () => {
    const panel = options.root.querySelector<HTMLElement>(".model-installer-panel")!;
    panel.hidden = !panel.hidden;
  });
  options.root.querySelector<HTMLSelectElement>(".model-installer-source")!.addEventListener("change", (event) => {
    const source = (event.currentTarget as HTMLSelectElement).value;
    options.root.querySelectorAll<HTMLElement>("[data-installer-source]").forEach((panel) => {
      panel.hidden = panel.dataset.installerSource !== source;
    });
    modelInstallPlan = null;
    options.root.querySelector<HTMLButtonElement>(".model-installer-install")!.disabled = true;
    setInstallerStatus("来源已更改，请重新生成安装计划", "idle");
    setInstallerStep("source");
  });
  options.root.querySelector<HTMLButtonElement>(".model-installer-pick-directory")!.addEventListener("click", async () => {
    const selected = await options.onPickTarget("directory");
    if (selected) options.root.querySelector<HTMLInputElement>(".model-installer-directory")!.value = selected;
  });
  options.root.querySelector<HTMLButtonElement>(".model-addon-browse")!.addEventListener("click", async () => {
    const selected = await options.onPickTarget("directory");
    if (!selected) return;
    if (!await recognizeAddonPath(selected, false)) {
      setInstallerStatus("所选目录中没有有效的 Javis-R1-8B-Addon.manifest.json", "error");
    }
  });
  options.root.querySelector<HTMLButtonElement>(".local-gguf-browse")!.addEventListener("click", async () => {
    const selected = await options.onPickTarget("file");
    if (selected) {
      options.root.querySelector<HTMLInputElement>(".local-gguf-path")!.value = selected;
      setInstallerStatus("已选择本地 GGUF；请选择已安装 Javis Ollama 的位置并生成计划", "idle");
    }
  });
  options.root.querySelector<HTMLButtonElement>(".hf-search")!.addEventListener("click", async () => {
    setInstallerStatus("正在搜索 Hugging Face GGUF 模型…", "saving");
    try {
      const response = await options.onSearchHuggingFace(options.root.querySelector<HTMLInputElement>(".hf-search-query")!.value);
      if (!response.ok) throw new Error(response.error || "搜索失败");
      replaceDataList("#hf-repo-options", response.models.map((item) => item.repo_id));
      if (response.models.length) options.root.querySelector<HTMLInputElement>(".hf-repo-id")!.value = response.models[0].repo_id;
      setInstallerStatus(`找到 ${response.models.length} 个 GGUF 模型仓库；请选择后生成安装计划`, "saved");
    } catch (error) {
      setInstallerStatus(error instanceof Error ? error.message : "搜索失败", "error");
    }
  });
  options.root.querySelector<HTMLButtonElement>(".model-installer-plan-button")!.addEventListener("click", () => void planModelInstall());
  options.root.querySelector<HTMLInputElement>(".model-installer-approved")!.addEventListener("change", (event) => {
    options.root.querySelector<HTMLButtonElement>(".model-installer-install")!.disabled =
      !(event.currentTarget as HTMLInputElement).checked
      || !modelInstallPlan
      || (modelInstallPlan.source !== "offline" && modelInstallPlan.runtime_ready === false);
  });
  options.root.querySelector<HTMLButtonElement>(".model-installer-install")!.addEventListener("click", () => void installModel());
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
