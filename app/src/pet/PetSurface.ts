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
import { AvatarFallbackCoordinator } from "./avatar/AvatarFallbackCoordinator.ts";
import { PerformanceGovernor } from "./avatar/PerformanceGovernor.ts";
import { SpeakingEnvelope } from "./avatar/SpeakingEnvelope.ts";
import type {
  AvatarSurfaceController,
  AvatarSurfaceTier,
} from "./avatar/AvatarSurface.ts";
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

function initialTierForSkin(skin: PetThreeDimensionalSkin): AvatarSurfaceTier {
  return skin.kind === "procedural3d" ? "procedural3d" : "3d-high";
}

export function createPetSurface(options: PetSurfaceOptions): PetSurfaceController {
  let currentSkin = getDefaultPetSkin();
  let currentState: LiveState = "idle";
  let preferences = readPetPreferences();
  let disposed = false;
  let avatarSurface: AvatarSurfaceController | null = null;
  let avatarController: AvatarController | null = null;
  let latestIntent: ExpressionIntent | null = null;
  let visualFallbackSkin: PetSkin | null = null;
  let performanceGovernor: PerformanceGovernor | null = null;
  let fallbackCoordinator: AvatarFallbackCoordinator | null = null;
  let avatarTier: AvatarSurfaceTier = "procedural3d";
  let avatarFrameId: number | null = null;
  let recoveryTimerId: number | null = null;
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

  function cancelRecoveryTimer(): void {
    if (recoveryTimerId === null) return;
    window.clearTimeout(recoveryTimerId);
    recoveryTimerId = null;
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
    cancelRecoveryTimer();
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
    const displaySkin = visualFallbackSkin || currentSkin;
    if (displaySkin.kind !== "sprite2d") {
      sprite.dataset.assetMode = displaySkin.kind;
      sprite.style.backgroundImage = "none";
      return;
    }
    const animate = currentState !== "idle"
      && currentState !== "offline"
      && Boolean(displaySkin.sourceAtlasAsset);
    const asset = animate ? displaySkin.sourceAtlasAsset : displaySkin.idleAsset;
    sprite.dataset.assetMode = animate ? "atlas" : "idle";
    sprite.style.backgroundImage = asset ? `url("${asset}")` : "none";
  }

  function renderSkin(skin: PetSkin, persist: boolean): void {
    currentSkin = skin;
    visualFallbackSkin = null;
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

  function fallbackSkinFor(skin: PetThreeDimensionalSkin): PetSkin {
    let fallbackId = "javis-orb";
    try {
      fallbackId = avatarAssetRegistry.resolveFallback(skin.manifestId)?.id || fallbackId;
    } catch {
      // A broken custom fallback chain must still terminate at the local orb.
    }
    const fallbackSkin = petSkinRegistry.find((candidate) =>
      candidate.id === fallbackId || candidate.manifestId === fallbackId
    );
    return fallbackSkin && fallbackSkin.id !== skin.id
      ? fallbackSkin
      : petSkinRegistry.find((candidate) => candidate.kind === "orb") || petSkinRegistry[0];
  }

  function showVisualFallback(skin: PetThreeDimensionalSkin): void {
    const fallback = fallbackSkinFor(skin);
    visualFallbackSkin = fallback;
    surface.dataset.petRenderer = fallback.kind;
    sprite.className = `pet-sprite ${fallback.fallbackClass}${fallback.kind === "sprite2d" ? " has-asset" : ""}`;
    sprite.style.setProperty("--pet-accent", fallback.accent);
    sprite.setAttribute("aria-label", fallback.name);
    sprite.textContent = fallback.kind === "sprite2d" ? "" : "J";
    renderAsset();
    avatarHost.hidden = true;
  }

  function fallbackFrom(skin: PetThreeDimensionalSkin): void {
    applySkin(fallbackSkinFor(skin), false);
  }

  function handleAvatarFrame(
    skin: PetThreeDimensionalSkin,
    generation: number,
    durationMs: number,
  ): void {
    if (!skinLoads.isCurrent(generation) || !performanceGovernor) return;
    const decision = performanceGovernor.recordFrame(durationMs);
    surface.dataset.avatarTier = decision.tier;
    if (
      decision.action === "downgrade"
      && (decision.tier === "3d-high" || decision.tier === "3d-low" || decision.tier === "procedural3d")
    ) {
      const nextTier = decision.tier;
      queueMicrotask(() => {
        if (!disposed && skinLoads.isCurrent(generation)) applySkin(skin, false, nextTier);
      });
    }
  }

  function handleAvatarContextLost(
    skin: PetThreeDimensionalSkin,
    generation: number,
  ): void {
    if (!skinLoads.isCurrent(generation) || !fallbackCoordinator) return;
    if (latestIntent) fallbackCoordinator.acceptIntent(latestIntent);
    performanceGovernor?.reportFailure("context");
    const decision = fallbackCoordinator.onContextLost();
    surface.dataset.avatarFallbackStatus = decision.status;
    avatarSurface?.setVisible(false);
    showVisualFallback(skin);
  }

  function handleAvatarContextRestored(
    skin: PetThreeDimensionalSkin,
    generation: number,
  ): void {
    const coordinator = fallbackCoordinator;
    if (!skinLoads.isCurrent(generation) || !coordinator) return;
    coordinator.requestContextRecovery();
    const decision = coordinator.onContextRestored(latestIntent || undefined);
    surface.dataset.avatarFallbackStatus = decision.status;
    if (!decision.reloadAssets) return;

    const targetTier = decision.recoveryTarget;
    if (targetTier !== "3d-high" && targetTier !== "3d-low" && targetTier !== "procedural3d") return;
    avatarTier = targetTier;
    const nextGeneration = skinLoads.next();
    releaseAvatar();
    showVisualFallback(skin);
    void mountAvatarSkin(skin, nextGeneration, true);
  }

  function scheduleRecoveryRetry(
    skin: PetThreeDimensionalSkin,
    generation: number,
  ): void {
    cancelRecoveryTimer();
    recoveryTimerId = window.setTimeout(() => {
      recoveryTimerId = null;
      const coordinator = fallbackCoordinator;
      if (disposed || !skinLoads.isCurrent(generation) || !coordinator) return;
      const attempt = coordinator.requestContextRecovery();
      if (attempt.action !== "attempt-context-recovery") return;
      const decision = coordinator.onContextRestored(latestIntent || undefined);
      surface.dataset.avatarFallbackStatus = decision.status;
      if (!decision.reloadAssets) return;
      const nextGeneration = skinLoads.next();
      releaseAvatar();
      showVisualFallback(skin);
      void mountAvatarSkin(skin, nextGeneration, true);
    }, 250);
  }

  async function mountAvatarSkin(
    skin: PetThreeDimensionalSkin,
    generation: number,
    recovering = false,
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
        tier: avatarTier,
        onFrame: (durationMs) => handleAvatarFrame(skin, generation, durationMs),
        onContextLost: () => handleAvatarContextLost(skin, generation),
        onContextRestored: () => handleAvatarContextRestored(skin, generation),
      });
      if (!skinLoads.accept(generation, mountedSurface)) return;

      avatarSurface = mountedSurface;
      avatarController = createAvatarController({
        sink: mountedSurface,
        capabilities: manifest.capabilities,
      });
      renderSkin(skin, false);
      avatarHost.hidden = false;
      surface.dataset.petRenderer = "3d";
      surface.dataset.avatarTier = avatarTier;
      if (recovering && fallbackCoordinator) {
        const recovery = fallbackCoordinator.onRecoverySucceeded(latestIntent || undefined);
        surface.dataset.avatarFallbackStatus = recovery.status;
        if (recovery.expressionIntent) latestIntent = recovery.expressionIntent;
      }
      applyLatestIntent();

      if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
        mountedSurface.setVisible(false);
        mountedSurface.renderOnce();
      } else {
        driveAvatarController();
      }
    } catch {
      if (!disposed && skinLoads.isCurrent(generation)) {
        performanceGovernor?.reportFailure("load");
        if (recovering && fallbackCoordinator) {
          const recovery = fallbackCoordinator.onRecoveryFailed();
          surface.dataset.avatarFallbackStatus = recovery.status;
          showVisualFallback(skin);
          if (recovery.canAttemptRecovery) scheduleRecoveryRetry(skin, generation);
        } else {
          fallbackCoordinator?.onLoadFailure();
          fallbackFrom(skin);
        }
      }
    }
  }

  function applySkin(
    skin: PetSkin,
    persist = true,
    requestedTier?: AvatarSurfaceTier,
  ): void {
    const generation = skinLoads.next();
    releaseAvatar();
    renderSkin(skin, persist);
    if (isThreeDimensionalSkin(skin)) {
      const reducedMotion = Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
      performanceGovernor = new PerformanceGovernor({
        initialTier: requestedTier || initialTierForSkin(skin),
        reducedMotion,
      });
      const governedTier = performanceGovernor.tier();
      avatarTier = governedTier === "3d-high" || governedTier === "3d-low" || governedTier === "procedural3d"
        ? governedTier
        : "procedural3d";
      fallbackCoordinator = new AvatarFallbackCoordinator({ initialTier: avatarTier });
      surface.dataset.avatarTier = avatarTier;
      surface.dataset.avatarFallbackStatus = "ready";
      void mountAvatarSkin(skin, generation);
      return;
    }
    performanceGovernor = null;
    fallbackCoordinator = null;
    delete surface.dataset.avatarTier;
    delete surface.dataset.avatarFallbackStatus;
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
