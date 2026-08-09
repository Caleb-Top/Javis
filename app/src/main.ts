import "./styles.css";
import { listen } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { installAppErrorBoundary } from "./app/AppErrorBoundary";
import { createFirstRunPanel } from "./app/FirstRunPanel";
import { getStartupDesktopMode } from "./app/startupMode.ts";
import { AppLogger } from "./app/AppLogger";
import { readStringPreference, writeStringPreference } from "./app/AppPreferences.ts";
import { createBackendClient, type ConnectionSnapshot } from "./bridge/backendClient";
import { createSidecarClient, isTauriRuntime, type SidecarSnapshot } from "./bridge/sidecarClient";
import { openCodeSurface, closeCodeSurface, mountCodeSurface } from "./code/CodeSurface";
import {
  ConversationEventReducer,
  isConversationEvent,
} from "./conversation/ConversationEventReducer.ts";
import { getOrCreateConversationId } from "./conversation/conversationSession.ts";
import { activateLiveOrb } from "./live/LiveOrb";
import { createCommandComposer } from "./live/CommandComposer";
import { createLiveCaption } from "./live/LiveCaption";
import { renderLiveStage } from "./live/LiveStage";
import { createSurfaceStatus } from "./live/SurfaceStatus.ts";
import {
  isLocalSurfaceCommand,
  parseLocalSurfaceCommand,
  type LocalSurfaceCommand,
} from "./live/localSurfaceCommand.ts";
import { createVoiceCapture } from "./live/VoiceCapture";
import type { LiveState } from "./live/liveState";
import { createSurfaceContextMenu } from "./menu/SurfaceContextMenu";
import { createPetSurface } from "./pet/PetSurface";
import type { PetShortcut } from "./pet/petPreferences.ts";
import { setDesktopMode, type DesktopMode } from "./desktop/windowMode";
import { installPointerDrag, installWindowDragRegions } from "./desktop/windowDrag";
import { createStatusRail } from "./panels/StatusRail";
import { createDrawerManager } from "./panels/DrawerManager";
import { createConversationDrawer } from "./panels/ConversationDrawer";
import { createControlDrawer } from "./panels/ControlDrawer";
import { createDiagnosticsPanel } from "./panels/DiagnosticsPanel";
import {
  createSettingsSurface,
  type LocalModelCatalogResponse,
  type ModelAddonDetectionResponse,
  type ModelConnectionSettingsResponse,
  type ModelDiagnosticReport,
  type ModelInstallPlan,
  type ModelInstallProgress,
  type RemoteModelCatalogResponse,
  type HuggingFaceSearchResponse,
  type PathSettingKey,
  type PathSettingsResponse,
} from "./settings/SettingsSurface";
import type { SettingsSourceMode } from "./settings/settingsNavigation.ts";
import { RateLimiter, VOICE_ACTION_WINDOW_MS } from "./security/RateLimiter";
import { runtimeStateCoordinator } from "./state/RuntimeStateCoordinator";

installAppErrorBoundary();
const app = document.querySelector<HTMLDivElement>("#app")!;
renderLiveStage(app);

document.body.setAttribute("data-surface", "live");
const previewOrbStates = new Set<LiveState>([
  "idle",
  "listening",
  "thinking",
  "speaking",
  "executing",
]);
const isLocalPreview =
  window.location.hostname === "127.0.0.1" ||
  window.location.hostname === "localhost";
const requestedPreviewState = isLocalPreview
  ? new URLSearchParams(window.location.search).get("orb-preview")
  : null;
const previewOrbState = requestedPreviewState && previewOrbStates.has(requestedPreviewState as LiveState)
  ? requestedPreviewState as LiveState
  : null;
const liveOrb = activateLiveOrb(previewOrbState ?? "idle");

const caption = document.querySelector<HTMLElement>(".live-caption")!;
const form = document.querySelector<HTMLFormElement>(".live-input")!;
const input = document.querySelector<HTMLTextAreaElement>(".live-input textarea")!;
const orbCanvas = document.querySelector<HTMLCanvasElement>("#live-orb-canvas")!;
if (previewOrbState) {
  const previewOutput = document.createElement("output");
  previewOutput.id = "orb-preview-data";
  previewOutput.hidden = true;
  document.body.append(previewOutput);
  window.setTimeout(() => {
    previewOutput.textContent = orbCanvas.toDataURL("image/png");
  }, 160);
}
const voiceButtons = document.querySelectorAll<HTMLButtonElement>(".voice-control");
const codeButton = document.querySelector<HTMLButtonElement>(".code-control")!;
const petModeButton = document.querySelector<HTMLButtonElement>(".pet-mode-control")!;
const drawerManager = createDrawerManager(document.querySelector<HTMLElement>("#drawer-root")!);
const statusRail = createStatusRail(document.querySelector<HTMLElement>(".status-rail")!);
const liveCaption = createLiveCaption(caption, () => document.dispatchEvent(new CustomEvent("javis:open-conversations")));
const conversationId = getOrCreateConversationId(localStorage);
const conversationCursorKey = `conversation.${conversationId}.cursor`;
const storedConversationCursor = Number.parseInt(
  readStringPreference(conversationCursorKey, "0"),
  10,
);
const conversationEvents = new ConversationEventReducer(
  conversationId,
  Number.isFinite(storedConversationCursor) ? storedConversationCursor : 0,
);
const liveSurfaceStatus = createSurfaceStatus(
  document.querySelector<HTMLElement>(".live-surface-status")!,
);
const limiter = new RateLimiter();
let composer: ReturnType<typeof createCommandComposer> | null = null;
let controlDrawer: ReturnType<typeof createControlDrawer> | null = null;
let settingsSurface: ReturnType<typeof createSettingsSurface> | null = null;
let diagnostics: ReturnType<typeof createDiagnosticsPanel>;
let diagnosticsReturnMode: DesktopMode = "live";
let voiceCapture!: ReturnType<typeof createVoiceCapture>;
const voiceRequestIds = new Set<string>();
const sidecar = createSidecarClient();
let backendConnection: ConnectionSnapshot = { http: false, websocket: false };
let desktopSidecar: SidecarSnapshot = { state: isTauriRuntime() ? "unknown" : "offline" };

function mergeConnectionDetails(): void {
  const online = backendConnection.http || backendConnection.websocket;
  statusRail.setConnection(online);
  composer?.setConnected(backendConnection.websocket);
  statusRail.setDetails({
    Provider: backendConnection.http ? "Ready" : "Offline",
    WebSocket: backendConnection.websocket ? "在线" : "离线",
    队列: client.queueSize(),
    Kernel: backendConnection.http ? "Running" : "Unavailable",
    Sidecar: isTauriRuntime() ? desktopSidecar.state : "浏览器预览",
    Control: "Audited",
  });
}

const client = createBackendClient({
  sessionId: conversationId,
  afterSequence: () => conversationEvents.current().lastSequence,
  onConnection: (snapshot) => {
    backendConnection = snapshot;
    mergeConnectionDetails();
  },
  onEvent: (event) => {
    if (event.type === "request.failed" && event.local === true) {
      if (event.request_id) voiceRequestIds.delete(event.request_id);
      liveCaption.setText(
        String(event.payload?.error || "Request failed"),
        event.request_id,
      );
      queueMicrotask(() => voiceCapture.resumeListeningState());
    } else if (isConversationEvent(event)) {
      const previous = conversationEvents.current();
      const snapshot = conversationEvents.accept(event);
      if (snapshot.lastSequence !== previous.lastSequence) {
        writeStringPreference(conversationCursorKey, String(snapshot.lastSequence));
      }
      if (event.type === "request.accepted" && snapshot.activeRequestId === event.request_id) {
        liveCaption.begin(event.request_id, "正在理解");
      } else if (
        event.type === "response.delta"
        && previous.activeRequestId === event.request_id
        && snapshot.response !== previous.response
      ) {
        liveCaption.setText(snapshot.response, event.request_id);
      } else if (event.type === "request.cancelled" && previous.activeRequestId === event.request_id) {
        liveCaption.setText("已中断", event.request_id);
      }
      if (
        event.type === "request.completed"
        || event.type === "request.cancelled"
        || event.type === "request.failed"
      ) {
        const voiceTurn = voiceRequestIds.delete(event.request_id);
        if (event.type === "request.completed" && voiceTurn && snapshot.response.trim()) {
          void client.post<{
            ok: boolean;
            active: boolean;
            duration_ms?: number;
          }>("/api/voice/playback/speak", { text: snapshot.response.trim() })
            .then((playback) => {
              if (!playback.ok || !playback.active) return;
              runtimeStateCoordinator.signal({
                source: "voice",
                state: "speaking",
                timestamp: Date.now(),
                detail: "正在回答",
              });
              window.setTimeout(() => voiceCapture.resumeListeningState(), playback.duration_ms ?? 0);
            })
            .catch((error) => {
              liveCaption.setText(error instanceof Error ? error.message : "语音播报失败");
            });
        }
        queueMicrotask(() => voiceCapture.resumeListeningState());
      }
    }
    if (event.type === "app_action") {
      document.dispatchEvent(new CustomEvent("javis:surface-command", {
        detail: event.action,
      }));
    }
    controlDrawer?.handleEvent(event);
  },
});
const conversationDrawer = createConversationDrawer(client, drawerManager);
controlDrawer = createControlDrawer(client, drawerManager);
let firstRunReturnMode: DesktopMode = "live";
const firstRun = createFirstRunPanel({
  onOpen: () => {
    const currentMode = document.body.dataset.desktopMode;
    firstRunReturnMode =
      currentMode === "code" || currentMode === "pet" || currentMode === "settings"
        ? currentMode
        : "live";
    void setDesktopMode("code");
  },
  onClose: () => void setDesktopMode(firstRunReturnMode),
});
const showCodeSurface = (): void => {
  const transition = setDesktopMode("code");
  openCodeSurface(conversationId);
  void transition;
};
const showSettingsSurface = (): void => {
  const currentMode = document.body.dataset.desktopMode;
  const sourceMode: SettingsSourceMode =
    currentMode === "pet" || currentMode === "code" ? currentMode : "live";
  void setDesktopMode("settings").then(() => {
    settingsSurface?.open(sourceMode);
  });
};
const showDiagnosticsSurface = (): void => {
  const currentMode = document.body.dataset.desktopMode;
  diagnosticsReturnMode =
    currentMode === "code" || currentMode === "pet" || currentMode === "settings"
      ? currentMode
      : "live";
  void setDesktopMode("settings").then(() => diagnostics.open());
};
const executeLocalSurfaceCommand = (command: LocalSurfaceCommand): void => {
  if (command === "code") {
    showCodeSurface();
    return;
  }
  if (command === "settings") {
    showSettingsSurface();
    return;
  }
  if (command === "diagnostics") {
    showDiagnosticsSurface();
    return;
  }
  document.body.setAttribute("data-surface", "live");
  void setDesktopMode("live");
};
const executePetShortcut = async (shortcut: PetShortcut): Promise<void> => {
  if (shortcut.kind === "javis") {
    if (shortcut.value === "live") {
      document.body.setAttribute("data-surface", "live");
      await setDesktopMode("live");
    } else if (shortcut.value === "code") {
      showCodeSurface();
    } else if (shortcut.value === "settings") {
      showSettingsSurface();
    } else if (shortcut.value === "diagnostics") {
      document.dispatchEvent(new CustomEvent("javis:open-diagnostics"));
    }
    return;
  }
  try {
    await invoke("execute_pet_shortcut", { kind: shortcut.kind, value: shortcut.value });
  } catch (error) {
    liveCaption.setText(error instanceof Error ? error.message : "\u5feb\u6377\u64cd\u4f5c\u6267\u884c\u5931\u8d25");
  }
};
const surfaceMenu = createSurfaceContextMenu({
  root: document.querySelector<HTMLElement>("#surface-menu-root")!,
  onOpenCode: showCodeSurface,
  onOpenSettings: showSettingsSurface,
  onExecuteShortcut: executePetShortcut,
});
const petSurface = createPetSurface({
  root: document.querySelector<HTMLElement>("#pet-surface-root")!,
  onOpenLive: () => {
    document.body.setAttribute("data-surface", "live");
    void setDesktopMode("live");
  },
  onOpenSettings: showSettingsSurface,
  onContextMenu: () => surfaceMenu.open("pet"),
});
mountCodeSurface(document.querySelector<HTMLElement>("#code-root")!);
installWindowDragRegions(document);
composer = createCommandComposer(form, input, (text) => {
  const localCommand = parseLocalSurfaceCommand(text);
  if (localCommand) {
    executeLocalSurfaceCommand(localCommand);
    return true;
  }
  if (/code|代码|编程|项目|文件|终端/i.test(text)) showCodeSurface();
  return Boolean(client.send(text));
});
const stopAudioPlayback = (): void => {
  document.querySelectorAll<HTMLAudioElement>("audio").forEach((audio) => {
    audio.pause();
    audio.currentTime = 0;
  });
  window.speechSynthesis?.cancel();
  document.dispatchEvent(new CustomEvent("javis:stop-audio"));
};
voiceCapture = createVoiceCapture(client, {
  noiseProfile: () => {
    const profile = readStringPreference("voice.noiseProfile", "standard");
    return profile === "off" || profile === "strong" ? profile : "standard";
  },
  deviceIndex: () => {
    const stored = readStringPreference("voice.inputDevice", "");
    return stored ? Number(stored) : undefined;
  },
  onBargeIn: async () => {
    stopAudioPlayback();
    await client.post("/api/voice/playback/stop", {}).catch(() => undefined);
    client.cancel("voice barge-in");
  },
  onPartial: (text) => {
    liveCaption.setText(text);
  },
  onEmptyTranscript: (message) => {
    liveCaption.setText(message);
  },
  onLevel: (level) => {
    liveOrb.setAudioLevel(level);
  },
  onTranscript: (text) => {
    liveCaption.setText(text);
    const requestId = client.send(text);
    if (requestId) voiceRequestIds.add(requestId);
  },
  onAudio: (audioBase64) => client.sendVoice(audioBase64),
  onState: (state) => runtimeStateCoordinator.signal({ source: "voice", state, timestamp: Date.now(), detail: state === "listening" ? "我在听" : "正在理解" }),
  onError: (message) => {
    liveCaption.setText(message);
  }
});
diagnostics = createDiagnosticsPanel(client, sidecar, voiceCapture, {
  onClose: () => {
    if (diagnosticsReturnMode === "code") {
      void setDesktopMode("code").then(() => openCodeSurface(conversationId));
      return;
    }
    if (diagnosticsReturnMode !== "settings") {
      closeCodeSurface();
    }
    void setDesktopMode(diagnosticsReturnMode);
  },
  onOpenStorage: () => {
    const sourceMode: SettingsSourceMode =
      diagnosticsReturnMode === "pet" || diagnosticsReturnMode === "code"
        ? diagnosticsReturnMode
        : "live";
    settingsSurface?.open(sourceMode, "storage");
  },
  onOpenLogs: () => invoke("open_logs_directory"),
});
settingsSurface = createSettingsSurface({
  root: document.querySelector<HTMLElement>("#settings-root")!,
  onClose: (mode) => {
    if (mode === "code") {
      void setDesktopMode("code").then(() => openCodeSurface(conversationId));
      return;
    }
    closeCodeSurface();
    void setDesktopMode(mode);
  },
  onPickTarget: async (kind) => {
    if (!isTauriRuntime()) {
      return null;
    }

    const selected = await open({
      title: kind === "directory" ? "选择目录" : "选择应用或文件",
      multiple: false,
      directory: kind === "directory",
    });
    return typeof selected === "string" ? selected : null;
  },
  onOpenDiagnostics: showDiagnosticsSurface,
  onOpenPrivacy: () => firstRun.open(),
  onOrbPalette: (palette) => liveOrb.setPalette(palette),
  onLoadPathSettings: () =>
    client.get<PathSettingsResponse>("/api/config/paths"),
  onSavePathSetting: (key: PathSettingKey, path: string) =>
    client.post<PathSettingsResponse>("/api/config/paths", {
      paths: { [key]: path },
    }),
  onLoadModelSettings: () =>
    client.get<ModelConnectionSettingsResponse>("/api/config/models"),
  onSaveModelSettings: (settings) =>
    client.post<ModelConnectionSettingsResponse>("/api/config/models", settings),
  onLoadVoiceDevices: async () => {
    const diagnostics = await client.get<{
      capture?: { input_devices?: Array<{ index: number; name: string }> };
    }>("/api/voice/diagnostics");
    return { input_devices: diagnostics.capture?.input_devices ?? [] };
  },
  onRefreshLocalModels: (baseUrl) =>
    client.post<LocalModelCatalogResponse>("/api/config/models/local", {
      base_url: baseUrl,
    }),
  onRefreshRemoteModels: (provider, baseUrl, apiKey) =>
    client.post<RemoteModelCatalogResponse>("/api/config/models/remote", {
      provider,
      base_url: baseUrl,
      api_key: apiKey,
    }),
  onDetectModelAddon: (path) =>
    client.post<ModelAddonDetectionResponse>("/api/config/models/install/detect", { path }),
  onGetModelInstallProgress: () =>
    client.get<ModelInstallProgress>("/api/config/models/install/progress"),
  onTestModelConnections: () =>
    client.post<ModelDiagnosticReport>("/api/diagnostics/self-test", {
      scope: "model",
    }),
  onSearchHuggingFace: (query) =>
    client.get<HuggingFaceSearchResponse>(`/api/config/models/install/search?q=${encodeURIComponent(query)}`),
  onPlanModelInstall: (settings) =>
    client.post<ModelInstallPlan>("/api/config/models/install/plan", settings),
  onInstallModel: (settings) =>
    client.post<ModelInstallPlan>("/api/config/models/install", settings),
});

runtimeStateCoordinator.subscribe((snapshot) => statusRail.update(snapshot));
runtimeStateCoordinator.subscribe((snapshot) => liveSurfaceStatus.setSnapshot(snapshot));
runtimeStateCoordinator.subscribe((snapshot) => petSurface.setSnapshot(snapshot));
runtimeStateCoordinator.subscribe((snapshot) => liveOrb.setState(snapshot.state));
statusRail.setDetails({ Provider: "检测中", WebSocket: "连接中", 队列: 0, Kernel: "待同步", Sidecar: isTauriRuntime() ? "探测中" : "浏览器预览", Control: "待同步" });

sidecar.subscribe((snapshot) => {
  desktopSidecar = snapshot;
  if (!isTauriRuntime()) return;
  const online = snapshot.state === "healthy" || snapshot.state === "attached";
  void online;
  mergeConnectionDetails();
  if (snapshot.state === "failed" || snapshot.state === "port-conflict") {
    runtimeStateCoordinator.signal({ source: "sidecar", state: "offline", timestamp: Date.now(), detail: snapshot.last_error || "Javis 运行时不可用" });
  }
});

async function bootRuntime(): Promise<void> {
  const snapshot = isTauriRuntime()
    ? await sidecar.status()
    : { state: "healthy" as const };
  const canConnect = !isTauriRuntime() || snapshot.state === "healthy" || snapshot.state === "attached";
  AppLogger.write("info", "startup", `Sidecar state: ${snapshot.state}`);
  if (!canConnect) {
    statusRail.setConnection(false);
    composer?.setConnected(false);
    return;
  }
  client.connect();
  await client.checkBackendHealth();
}

if (!previewOrbState && !isTauriRuntime()) {
  void bootRuntime();
}
window.setInterval(async () => {
  if (isTauriRuntime()) await sidecar.status();
  await client.checkBackendHealth();
}, 15000);

const toggleVoice = async (): Promise<void> => {
  const rate = limiter.take("voice", VOICE_ACTION_WINDOW_MS);
  if (!rate.allowed) return;
  try {
    await voiceCapture.toggle();
    liveCaption.setText(voiceCapture.isRecording() ? "我在听" : "正在理解");
  } catch {
    liveCaption.setText("语音服务正在准备，请稍后再试");
  }
};

voiceButtons.forEach((button) => button.addEventListener("click", () => void toggleVoice()));

let suppressOrbAction = false;
let orbClickTimer = 0;
installPointerDrag(orbCanvas, () => {
  suppressOrbAction = true;
  window.setTimeout(() => { suppressOrbAction = false; }, 420);
});
orbCanvas.addEventListener("click", () => {
  if (suppressOrbAction) return;
  window.clearTimeout(orbClickTimer);
  orbClickTimer = window.setTimeout(() => void toggleVoice(), 260);
});
orbCanvas.addEventListener("dblclick", (event) => {
  event.preventDefault();
  window.clearTimeout(orbClickTimer);
  showCodeSurface();
});
orbCanvas.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  void surfaceMenu.open("live");
});

codeButton.addEventListener("click", () => {
  showCodeSurface();
});
petModeButton.addEventListener("click", () => void setDesktopMode("pet"));
document.querySelector<HTMLButtonElement>(".control-panel-control")!.addEventListener("click", (event) => controlDrawer?.open("permission", event.currentTarget as HTMLElement));
document.querySelector<HTMLButtonElement>(".perception-control")!.addEventListener("click", (event) => controlDrawer?.open("perception", event.currentTarget as HTMLElement));
document.querySelector<HTMLButtonElement>(".more-control")!.addEventListener("click", (event) => conversationDrawer.open(event.currentTarget as HTMLElement));
document.addEventListener("javis:open-conversations", () => conversationDrawer.open(caption));
document.addEventListener("javis:return-live", () => {
  closeCodeSurface();
  void setDesktopMode("live");
});
document.addEventListener("javis:open-diagnostics", showDiagnosticsSurface);
document.addEventListener("javis:surface-command", (event) => {
  const command = (event as CustomEvent<unknown>).detail;
  if (isLocalSurfaceCommand(command)) executeLocalSurfaceCommand(command);
});
document.addEventListener("javis:open-first-run", () => firstRun.open());
document.addEventListener("javis:open-settings", showSettingsSurface);
document.addEventListener("javis:voice-profile-changed", (event) => {
  const profile = (event as CustomEvent<unknown>).detail;
  if (profile === "off" || profile === "standard" || profile === "strong") {
    void voiceCapture.setNoiseProfile(profile);
  }
});
const firstRunRequired = firstRun.showOnFirstRun();
void setDesktopMode(getStartupDesktopMode(firstRunRequired));

if (isTauriRuntime()) {
  void (async () => {
    await listen("javis://runtime-ready", () => void bootRuntime());
    await listen<string>("javis://runtime-error", (event) => {
      runtimeStateCoordinator.signal({
        source: "sidecar",
        state: "error",
        timestamp: Date.now(),
        detail: event.payload || "Javis 运行时准备失败",
      });
    });
    await listen("javis://open-diagnostics", showDiagnosticsSurface);
    await listen("javis://pause-listening", () => {
      void voiceCapture.stop();
      runtimeStateCoordinator.signal({ source: "voice", state: "idle", timestamp: Date.now(), detail: "已暂停聆听" });
    });
    await bootRuntime();
  })();
}
