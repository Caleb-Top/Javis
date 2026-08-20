import type {
  ExpressionBaseState,
  ExpressionIntent,
  GazeTarget,
} from "../../life/lifeTypes.ts";
import type { AvatarPerformanceTier } from "./avatarTypes.ts";
import { avatarAssetRegistry } from "./AvatarAssetRegistry.ts";
import { createAvatarController, type AvatarController } from "./AvatarController.ts";
import {
  createAvatarSurface,
  type AvatarSurfaceController,
  type AvatarSurfaceTier,
} from "./AvatarSurface.ts";
import { SpeakingEnvelope } from "./SpeakingEnvelope.ts";

export const AVATAR_PREVIEW_STATES = [
  "idle",
  "attention",
  "listening",
  "thinking",
  "speaking",
  "executing",
  "blocked",
  "error",
  "offline",
] as const satisfies readonly ExpressionBaseState[];

export const AVATAR_PREVIEW_BACKGROUNDS = [
  "light",
  "dark",
  "wallpaper",
  "checker",
] as const;

export const AVATAR_PREVIEW_SCALES = [1, 1.25, 1.5, 1.75] as const;

export const AVATAR_PREVIEW_SIZES = [
  { id: "minimum", width: 132, height: 158 },
  { id: "comparison", width: 200, height: 218 },
  { id: "pet", width: 320, height: 360 },
  { id: "live", width: 420, height: 500 },
] as const;

export const AVATAR_PREVIEW_PROFILES = [
  "light-tech-human",
  "neon-pilot",
  "quiet-lifeform",
] as const;

export type AvatarPreviewBackground = (typeof AVATAR_PREVIEW_BACKGROUNDS)[number];
export type AvatarPreviewScale = (typeof AVATAR_PREVIEW_SCALES)[number];
export type AvatarPreviewSize = (typeof AVATAR_PREVIEW_SIZES)[number]["id"];
export type AvatarPreviewProfile = (typeof AVATAR_PREVIEW_PROFILES)[number];

export type AvatarPreviewSnapshot = Readonly<{
  state: ExpressionBaseState;
  intensity: number;
  gaze: GazeTarget;
  speaking: boolean;
  interrupt: boolean;
  background: AvatarPreviewBackground;
  size: AvatarPreviewSize;
  scale: AvatarPreviewScale;
  tier: AvatarPerformanceTier;
  profile: AvatarPreviewProfile;
  contextLost: boolean;
}>;

export type AvatarPreviewModel = Readonly<{
  states: typeof AVATAR_PREVIEW_STATES;
  backgrounds: typeof AVATAR_PREVIEW_BACKGROUNDS;
  scales: typeof AVATAR_PREVIEW_SCALES;
  sizes: typeof AVATAR_PREVIEW_SIZES;
  profiles: typeof AVATAR_PREVIEW_PROFILES;
  snapshot(): AvatarPreviewSnapshot;
  update(patch: Partial<AvatarPreviewSnapshot>): AvatarPreviewSnapshot;
  createIntent(revision: number, nowMs?: number): ExpressionIntent;
}>;

const DEFAULT_PREVIEW: AvatarPreviewSnapshot = {
  state: "idle",
  intensity: 0.45,
  gaze: "user",
  speaking: false,
  interrupt: false,
  background: "dark",
  size: "comparison",
  scale: 1,
  tier: "procedural3d",
  profile: "quiet-lifeform",
  contextLost: false,
};

function clampUnit(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

export function createAvatarPreviewModel(
  initial: Partial<AvatarPreviewSnapshot> = {},
): AvatarPreviewModel {
  let current: AvatarPreviewSnapshot = {
    ...DEFAULT_PREVIEW,
    ...initial,
    intensity: clampUnit(initial.intensity ?? DEFAULT_PREVIEW.intensity),
  };

  const snapshot = (): AvatarPreviewSnapshot => ({ ...current });

  const update = (patch: Partial<AvatarPreviewSnapshot>): AvatarPreviewSnapshot => {
    current = {
      ...current,
      ...patch,
      intensity: clampUnit(patch.intensity ?? current.intensity),
    };
    return snapshot();
  };

  const createIntent = (revision: number, nowMs = Date.now()): ExpressionIntent => {
    const generatedAt = new Date(nowMs).toISOString();
    const voiceActivity = current.state === "listening"
      ? "listening"
      : current.speaking || current.state === "speaking"
        ? "speaking"
        : "silent";
    return {
      schema_version: 1,
      revision: Math.max(0, Math.trunc(revision)),
      base_state: current.state,
      intensity: current.intensity,
      gaze_target: current.gaze,
      voice_activity: voiceActivity,
      transition_ms: current.interrupt ? 0 : 180,
      interrupt: current.interrupt,
      source_snapshot_revision: Math.max(0, Math.trunc(revision)),
      generated_at: generatedAt,
      expires_at: new Date(nowMs + 10_000).toISOString(),
      explanation_code: "avatar_preview",
    };
  };

  return {
    states: AVATAR_PREVIEW_STATES,
    backgrounds: AVATAR_PREVIEW_BACKGROUNDS,
    scales: AVATAR_PREVIEW_SCALES,
    sizes: AVATAR_PREVIEW_SIZES,
    profiles: AVATAR_PREVIEW_PROFILES,
    snapshot,
    update,
    createIntent,
  };
}

function optionsMarkup(values: readonly (string | number)[]): string {
  return values.map((value) => `<option value="${value}">${value}</option>`).join("");
}

export async function mountAvatarPreview(root: HTMLElement): Promise<() => void> {
  const model = createAvatarPreviewModel();
  const manifest = avatarAssetRegistry.get("javis-lightform");
  if (!manifest) throw new Error("javis-lightform manifest is unavailable");

  root.innerHTML = `
    <main class="avatar-preview" data-background="dark" data-profile="quiet-lifeform">
      <aside class="avatar-preview-controls" aria-label="Avatar preview controls">
        <header>
          <strong>Javis Lightform</strong>
          <output class="avatar-preview-status" aria-live="polite">Loading</output>
        </header>
        <label>State<select data-control="state">${optionsMarkup(AVATAR_PREVIEW_STATES)}</select></label>
        <label>Intensity<input data-control="intensity" type="range" min="0" max="1" step="0.05" value="0.45"></label>
        <label>Gaze<select data-control="gaze">${optionsMarkup(["none", "user", "content", "task"])}</select></label>
        <label>Background<select data-control="background">${optionsMarkup(AVATAR_PREVIEW_BACKGROUNDS)}</select></label>
        <label>Window<select data-control="size">${optionsMarkup(AVATAR_PREVIEW_SIZES.map(({ id }) => id))}</select></label>
        <label>Scale<select data-control="scale">${optionsMarkup(AVATAR_PREVIEW_SCALES)}</select></label>
        <label>Tier<select data-control="tier">${optionsMarkup(["3d-high", "3d-low", "procedural3d", "sprite2d", "orb"])}</select></label>
        <label>Profile<select data-control="profile">${optionsMarkup(AVATAR_PREVIEW_PROFILES)}</select></label>
        <label class="avatar-preview-toggle"><input data-control="speaking" type="checkbox">Speaking envelope</label>
        <label class="avatar-preview-toggle"><input data-control="contextLost" type="checkbox">Context lost</label>
        <button class="avatar-preview-interrupt" type="button">Interrupt</button>
      </aside>
      <section class="avatar-preview-workspace">
        <div class="avatar-preview-stage" data-fallback="loading" aria-label="Javis avatar preview">
          <div class="avatar-preview-host"></div>
          <div class="avatar-preview-fallback" role="img" aria-label="Javis fallback body"><span>J</span></div>
        </div>
      </section>
    </main>`;

  const shell = root.querySelector<HTMLElement>(".avatar-preview")!;
  const stage = root.querySelector<HTMLElement>(".avatar-preview-stage")!;
  const host = root.querySelector<HTMLElement>(".avatar-preview-host")!;
  const status = root.querySelector<HTMLOutputElement>(".avatar-preview-status")!;
  const envelope = new SpeakingEnvelope();
  let surface: AvatarSurfaceController | null = null;
  let controller: AvatarController | null = null;
  let revision = 0;
  let generation = 0;
  let frameId = 0;
  let disposed = false;

  const currentSize = () => {
    const selected = model.snapshot().size;
    return AVATAR_PREVIEW_SIZES.find(({ id }) => id === selected) || AVATAR_PREVIEW_SIZES[1];
  };

  const applyIntent = () => {
    if (!controller) return;
    const snapshot = model.snapshot();
    controller.applyIntent(model.createIntent(++revision));
    if (snapshot.speaking || snapshot.state === "speaking") {
      envelope.start(1_600, performance.now());
    } else {
      envelope.stop();
      controller.setSpeakingLevel(0);
    }
  };

  const syncStage = () => {
    const snapshot = model.snapshot();
    const size = currentSize();
    shell.dataset.background = snapshot.background;
    shell.dataset.profile = snapshot.profile;
    stage.style.setProperty("--preview-width", `${size.width}px`);
    stage.style.setProperty("--preview-height", `${size.height}px`);
    stage.style.setProperty("--preview-scale", String(snapshot.scale));
    const fallback = snapshot.contextLost
      ? "context-lost"
      : snapshot.tier === "sprite2d" || snapshot.tier === "orb"
        ? snapshot.tier
        : "none";
    stage.dataset.fallback = fallback;
    surface?.setVisible(fallback === "none");
    status.value = fallback === "none" ? `${snapshot.state} / ${snapshot.tier}` : fallback;
  };

  const rebuildSurface = async () => {
    const rebuildGeneration = ++generation;
    const snapshot = model.snapshot();
    controller?.dispose();
    surface?.dispose();
    controller = null;
    surface = null;
    host.replaceChildren();
    syncStage();
    if (snapshot.tier === "sprite2d" || snapshot.tier === "orb") return;
    stage.dataset.fallback = "loading";
    const size = currentSize();
    try {
      const nextSurface = await createAvatarSurface({
        host,
        manifest,
        tier: snapshot.tier as AvatarSurfaceTier,
        mode: "live",
        width: size.width,
        height: size.height,
        pixelRatio: snapshot.scale,
      });
      if (disposed || rebuildGeneration !== generation) {
        nextSurface.dispose();
        return;
      }
      surface = nextSurface;
      controller = createAvatarController({
        sink: nextSurface,
        capabilities: manifest.capabilities,
      });
      syncStage();
      applyIntent();
    } catch {
      stage.dataset.fallback = "sprite2d";
      status.value = "sprite2d";
    }
  };

  const update = (patch: Partial<AvatarPreviewSnapshot>, rebuild = false) => {
    model.update(patch);
    syncStage();
    if (rebuild) void rebuildSurface();
    else applyIntent();
  };

  const bindSelect = <K extends keyof AvatarPreviewSnapshot>(
    key: K,
    parse: (value: string) => AvatarPreviewSnapshot[K],
    rebuild = false,
  ) => {
    const select = root.querySelector<HTMLSelectElement>(`[data-control="${key}"]`)!;
    select.value = String(model.snapshot()[key]);
    select.addEventListener("change", () => update({ [key]: parse(select.value) } as Partial<AvatarPreviewSnapshot>, rebuild));
  };

  bindSelect("state", (value) => value as ExpressionBaseState);
  bindSelect("gaze", (value) => value as GazeTarget);
  bindSelect("background", (value) => value as AvatarPreviewBackground);
  bindSelect("size", (value) => value as AvatarPreviewSize, true);
  bindSelect("scale", (value) => Number(value) as AvatarPreviewScale, true);
  bindSelect("tier", (value) => value as AvatarPerformanceTier, true);
  bindSelect("profile", (value) => value as AvatarPreviewProfile);

  const intensity = root.querySelector<HTMLInputElement>("[data-control=\"intensity\"]")!;
  intensity.addEventListener("input", () => update({ intensity: Number(intensity.value) }));
  const speaking = root.querySelector<HTMLInputElement>("[data-control=\"speaking\"]")!;
  speaking.addEventListener("change", () => update({ speaking: speaking.checked }));
  const contextLost = root.querySelector<HTMLInputElement>("[data-control=\"contextLost\"]")!;
  contextLost.addEventListener("change", () => update({ contextLost: contextLost.checked }));
  root.querySelector<HTMLButtonElement>(".avatar-preview-interrupt")!.addEventListener("click", () => {
    model.update({ interrupt: true, speaking: false });
    speaking.checked = false;
    envelope.stop();
    applyIntent();
    model.update({ interrupt: false });
  });

  const animate = (now: number) => {
    if (disposed) return;
    controller?.tick(Date.now());
    if (envelope.active()) controller?.setSpeakingLevel(envelope.level(now));
    frameId = requestAnimationFrame(animate);
  };

  syncStage();
  await rebuildSurface();
  frameId = requestAnimationFrame(animate);

  return () => {
    disposed = true;
    generation += 1;
    cancelAnimationFrame(frameId);
    envelope.stop();
    controller?.dispose();
    surface?.dispose();
    root.replaceChildren();
  };
}
