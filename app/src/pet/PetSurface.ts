import type { ExpressionIntent } from "../life/lifeTypes.ts";
import type { LiveState } from "../live/liveState";
import { setPetScale } from "../desktop/windowMode";
import { installPointerDrag } from "../desktop/windowDrag";
import {
  formatSurfaceStatus,
  type RuntimeSnapshot,
} from "../state/runtimeStateTypes.ts";
import { avatarAssetRegistry } from "./avatar/AvatarAssetRegistry.ts";
import {
  createAvatarController,
  type AvatarController,
} from "./avatar/AvatarController.ts";
import { SpeakingEnvelope } from "./avatar/SpeakingEnvelope.ts";
import type { AvatarSurfaceController } from "./avatar/AvatarSurface.ts";
import {
  parsePetPreferences,
  readPetPreferences,
  type PetPreferences,
} from "./petPreferences.ts";
import { getDefaultPetSkin, getPetSkin, petSkinRegistry } from "./PetSkinRegistry";
import {
  PetSkinLoadGeneration,
  type AvatarLoader,
  type PetSkin,
  type PetSurfaceController,
  type PetSurfaceOptions,
  type PetThreeDimensionalSkin,
} from "./petTypes.ts";

const defaultAvatarLoader: AvatarLoader = async (avatarOptions) => {
  const { createAvatarSurface } = await import("./avatar/AvatarSurface.ts");
  return createAvatarSurface(avatarOptions);
};

function saveSkin(skinId: string): void {
  try {
    localStorage.setItem("javis.app.petSkin", skinId);
  } catch {
    // A locked-down WebView must still keep the pet usable.
  }
}

function isThreeDimensionalSkin(skin: PetSkin): skin is PetThreeDimensionalSkin {
  return skin.kind === "procedural3d" || skin.kind === "vrm" || skin.kind === "gltf";
}

function isUnexpiredIntent(intent: ExpressionIntent, now = Date.now()): boolean {
  const expiry = Date.parse(intent.expires_at);
  return Number.isFinite(expiry) && expiry > now;
}

export function createPetSurface(options: PetSurfaceOptions): PetSurfaceController {
  let currentSkin = getDefaultPetSkin();
  let currentState: LiveState = "idle";
  let preferences = readPetPreferences();
  let disposed = false;
  let avatarSurface: AvatarSurfaceController | null = null;
  let avatarController: AvatarController | null = null;
  let latestIntent: ExpressionIntent | null = null;
  let avatarFrameId: number | null = null;
  const avatarLoader = options.avatarLoader || defaultAvatarLoader;
  const skinLoads = new PetSkinLoadGeneration();
  const speakingEnvelope = new SpeakingEnvelope();

  options.root.innerHTML = `
    <section class="pet-surface" data-pet-state="idle" aria-label="Javis desktop pet">
      <div class="pet-anchor">
        <div class="pet-boundary" aria-hidden="true"></div>
        <div class="pet-avatar-host" aria-hidden="true"></div>
        <button type="button" class="pet-sprite-button" aria-label="Open Javis Live" title="Open Javis Live">
          <span class="pet-sprite" role="img" aria-label="Javis pet"></span>
        </button>
        <div class="pet-status" aria-live="polite"></div>
      </div>
    </section>`;

  const surface = options.root.querySelector<HTMLElement>(".pet-surface")!;
  const avatarHost = options.root.querySelector<HTMLElement>(".pet-avatar-host")!;
  const spriteButton = options.root.querySelector<HTMLButtonElement>(".pet-sprite-button")!;
  const sprite = options.root.querySelector<HTMLElement>(".pet-sprite")!;
  const status = options.root.querySelector<HTMLElement>(".pet-status")!;

  function cancelAvatarFrame(): void {
    if (avatarFrameId === null) return;
    window.cancelAnimationFrame(avatarFrameId);
    avatarFrameId = null;
  }

  function stopSpeakingEnvelope(): void {
    speakingEnvelope.stop();
    avatarController?.setSpeakingLevel(0);
  }

  function driveAvatarController(): void {
    if (avatarFrameId !== null || !avatarController || disposed) return;
    const tick = (): void => {
      avatarFrameId = null;
      if (!avatarController || disposed) return;
      const now = Date.now();
      avatarController.tick(now);
      if (speakingEnvelope.active()) avatarController.setSpeakingLevel(speakingEnvelope.level(now));
      avatarFrameId = window.requestAnimationFrame(tick);
    };
    avatarFrameId = window.requestAnimationFrame(tick);
  }

  function releaseAvatar(): void {
    stopSpeakingEnvelope();
    cancelAvatarFrame();
    const controller = avatarController;
    avatarController = null;
    controller?.dispose();
    const mountedSurface = avatarSurface;
    avatarSurface = null;
    mountedSurface?.dispose();
    avatarHost.replaceChildren();
    avatarHost.hidden = true;
  }

  function renderAsset(): void {
    if (currentSkin.kind !== "sprite2d") {
      sprite.dataset.assetMode = currentSkin.kind;
      sprite.style.backgroundImage = "none";
      return;
    }
    const animate = currentState !== "idle"
      && currentState !== "offline"
      && Boolean(currentSkin.sourceAtlasAsset);
    const asset = animate ? currentSkin.sourceAtlasAsset : currentSkin.idleAsset;
    sprite.dataset.assetMode = animate ? "atlas" : "idle";
    sprite.style.backgroundImage = asset ? `url("${asset}")` : "none";
  }

  function renderSkin(skin: PetSkin, persist: boolean): void {
    currentSkin = skin;
    surface.dataset.skin = skin.id;
    surface.dataset.skinKind = skin.kind;
    surface.dataset.petRenderer = isThreeDimensionalSkin(skin) ? "loading" : skin.kind;
    surface.style.setProperty("--pet-accent", skin.accent);
    sprite.className = `pet-sprite ${skin.fallbackClass}${skin.kind === "sprite2d" ? " has-asset" : ""}`;
    sprite.style.setProperty("--pet-accent", skin.accent);
    sprite.setAttribute("aria-label", skin.name);
    sprite.textContent = skin.kind === "sprite2d" ? "" : "J";
    renderAsset();
    if (persist) saveSkin(skin.id);
  }

  function applyLatestIntent(): void {
    if (!latestIntent || !isUnexpiredIntent(latestIntent)) {
      latestIntent = null;
      return;
    }
    avatarController?.applyIntent(latestIntent);
  }

  function fallbackFrom(skin: PetThreeDimensionalSkin): void {
    let fallbackId = "javis-orb";
    try {
      fallbackId = avatarAssetRegistry.resolveFallback(skin.manifestId)?.id || fallbackId;
    } catch {
      // A broken custom fallback chain must still terminate at the local orb.
    }
    const fallbackSkin = petSkinRegistry.find((candidate) =>
      candidate.id === fallbackId || candidate.manifestId === fallbackId
    );
    const terminalFallback = fallbackSkin && fallbackSkin.id !== skin.id
      ? fallbackSkin
      : petSkinRegistry.find((candidate) => candidate.kind === "orb") || petSkinRegistry[0];
    applySkin(terminalFallback, false);
  }

  async function mountAvatarSkin(
    skin: PetThreeDimensionalSkin,
    generation: number,
  ): Promise<void> {
    const manifest = avatarAssetRegistry.get(skin.manifestId);
    if (!manifest || manifest.kind !== skin.kind) {
      if (!disposed && skinLoads.isCurrent(generation)) fallbackFrom(skin);
      return;
    }

    try {
      const mountedSurface = await avatarLoader({
        host: avatarHost,
        manifest,
        mode: "pet",
      });
      if (!skinLoads.accept(generation, mountedSurface)) return;

      avatarSurface = mountedSurface;
      avatarController = createAvatarController({
        sink: mountedSurface,
        capabilities: manifest.capabilities,
      });
      avatarHost.hidden = false;
      surface.dataset.petRenderer = "3d";
      applyLatestIntent();

      if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
        mountedSurface.setVisible(false);
        mountedSurface.renderOnce();
      } else {
        driveAvatarController();
      }
    } catch {
      if (!disposed && skinLoads.isCurrent(generation)) fallbackFrom(skin);
    }
  }

  function applySkin(skin: PetSkin, persist = true): void {
    const generation = skinLoads.next();
    releaseAvatar();
    renderSkin(skin, persist);
    if (isThreeDimensionalSkin(skin)) void mountAvatarSkin(skin, generation);
  }

  function setPreferences(next: PetPreferences): void {
    preferences = parsePetPreferences(next);
    void setPetScale(preferences.scale);
  }

  function setState(state: LiveState): void {
    setSnapshot({
      state,
      source: "ui",
      detail: "",
      updatedAt: Date.now(),
    });
  }

  function setSnapshot(snapshot: RuntimeSnapshot): void {
    const state = snapshot.state;
    currentState = state;
    surface.dataset.petState = state;
    renderAsset();
    status.textContent = formatSurfaceStatus(snapshot);
    status.title = snapshot.detail || "";
    status.dataset.visible = "true";
  }

  function setSkin(skinId: string): void {
    applySkin(getPetSkin(skinId));
  }

  function toggleSkin(): void {
    const index = petSkinRegistry.findIndex((skin) => skin.id === currentSkin.id);
    setSkin(petSkinRegistry[(index + 1) % petSkinRegistry.length].id);
  }

  let suppressOpen = false;
  const disposeDrag = installPointerDrag(spriteButton, () => {
    suppressOpen = true;
    window.setTimeout(() => { suppressOpen = false; }, 500);
  });

  const handlePreferencesChanged = (event: Event): void => {
    const detail = (event as CustomEvent<PetPreferences>).detail;
    setPreferences(detail || readPetPreferences());
  };
  const handleSkinChanged = (event: Event): void => {
    const skinId = (event as CustomEvent<string>).detail;
    if (skinId) setSkin(skinId);
  };
  const handleExpression = (intent: ExpressionIntent): void => {
    if (!isUnexpiredIntent(intent)) return;
    if (latestIntent && intent.revision <= latestIntent.revision) return;
    latestIntent = intent;
    if (intent.interrupt || intent.base_state === "listening") stopSpeakingEnvelope();
    avatarController?.applyIntent(intent);
  };
  const handleStopAudio = (): void => {
    stopSpeakingEnvelope();
    avatarController?.interrupt();
  };
  const handlePlaybackEnvelopeStart = (event: Event): void => {
    const detail = (event as CustomEvent<{ durationMs?: number }>).detail;
    speakingEnvelope.start(detail?.durationMs, Date.now());
    driveAvatarController();
  };

  spriteButton.addEventListener("click", (event) => {
    if (suppressOpen) {
      event.preventDefault();
      suppressOpen = false;
      return;
    }
    options.onOpenLive();
  });
  spriteButton.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    void options.onContextMenu();
  });
  document.addEventListener("javis:pet-preferences-changed", handlePreferencesChanged);
  document.addEventListener("javis:pet-skin-changed", handleSkinChanged);
  document.addEventListener("javis:stop-audio", handleStopAudio);
  document.addEventListener("javis:playback-envelope-start", handlePlaybackEnvelopeStart);

  let unsubscribeExpression = (): void => undefined;
  try {
    unsubscribeExpression = options.subscribeExpression?.(handleExpression) || unsubscribeExpression;
  } catch {
    // The visual fallback remains usable when a bridge subscription is unavailable.
  }

  applySkin(currentSkin);
  setPreferences(preferences);
  setState(currentState);

  return {
    setState,
    setSnapshot,
    setSkin,
    toggleSkin,
    openSkinPicker: options.onOpenSettings,
    dispose: () => {
      if (disposed) return;
      disposed = true;
      skinLoads.invalidate();
      releaseAvatar();
      try {
        unsubscribeExpression();
      } catch {
        // An injected bridge cannot prevent local visual and event cleanup.
      }
      disposeDrag();
      document.removeEventListener("javis:pet-preferences-changed", handlePreferencesChanged);
      document.removeEventListener("javis:pet-skin-changed", handleSkinChanged);
      document.removeEventListener("javis:stop-audio", handleStopAudio);
      document.removeEventListener("javis:playback-envelope-start", handlePlaybackEnvelopeStart);
      options.root.innerHTML = "";
    },
  };
}
