import {
  Clock,
  Material,
  Texture,
  WebGLRenderer,
  type BufferGeometry,
  type Object3D,
  type WebGLRendererParameters,
} from "three";

import type {
  AvatarManifest,
  AvatarPerformanceTier,
  ExpressionTarget,
} from "./avatarTypes.ts";
import { createAvatarScene, type AvatarSceneController } from "./AvatarScene.ts";
import { createProceduralAvatar } from "./ProceduralAvatar.ts";

export type AvatarSurfaceMode = "pet" | "live" | "code" | "settings";
export type AvatarSurfaceTier = Extract<
  AvatarPerformanceTier,
  "3d-high" | "3d-low" | "procedural3d"
>;
export type AvatarExpressionTarget = ExpressionTarget;

type DisposableResource = { dispose(): void };

export type AvatarLoadedModel = Readonly<{
  object: Object3D;
  owned?: boolean;
  sharedResources?: readonly DisposableResource[];
  ownedResources?: readonly DisposableResource[];
  update?(deltaSeconds: number, target: AvatarExpressionTarget | null): void;
  dispose?(): void;
}>;

export type AvatarRenderer = Readonly<{
  domElement: HTMLCanvasElement;
  setClearColor(color: number, alpha?: number): void;
  setPixelRatio?(ratio: number): void;
  setSize(width: number, height: number, updateStyle?: boolean): void;
  render(scene: AvatarSceneController["scene"], camera: AvatarSceneController["camera"]): void;
  dispose(): void;
}>;

export type AvatarClock = Readonly<{
  getDelta(): number;
  start?(): void;
  stop?(): void;
}>;

export type AvatarSurfaceResources = Readonly<{
  createRenderer(parameters: WebGLRendererParameters): AvatarRenderer;
  createClock(): AvatarClock;
  requestAnimationFrame(callback: FrameRequestCallback): number;
  cancelAnimationFrame(frameId: number): void;
  loadModel?(
    manifest: AvatarManifest,
    scene: AvatarSceneController,
  ): AvatarLoadedModel | null | Promise<AvatarLoadedModel | null>;
}>;

export type AvatarSurfaceDiagnostics = Readonly<{
  manifestId: string;
  tier: AvatarSurfaceTier;
  mode: AvatarSurfaceMode;
  width: number;
  height: number;
  visible: boolean;
  rendering: boolean;
  contextLost: boolean;
  disposed: boolean;
  frameCount: number;
  expressionTarget: AvatarExpressionTarget | null;
}>;

export type AvatarSurfaceController = Readonly<{
  setSize(width: number, height: number): void;
  setVisible(visible: boolean): void;
  renderOnce(): void;
  setExpressionTarget(target: AvatarExpressionTarget | null): void;
  diagnostics(): AvatarSurfaceDiagnostics;
  dispose(): void;
}>;

export type CreateAvatarSurfaceOptions = Readonly<{
  host: HTMLElement;
  manifest: AvatarManifest;
  tier?: AvatarSurfaceTier;
  mode?: AvatarSurfaceMode;
  width?: number;
  height?: number;
  pixelRatio?: number;
  resources?: AvatarSurfaceResources;
  onFrame?(durationMs: number): void;
  onContextLost?(): void;
  onContextRestored?(): void;
}>;

function createDefaultResources(): AvatarSurfaceResources {
  return {
    createRenderer: (parameters) => new WebGLRenderer(parameters),
    createClock: () => new Clock(),
    requestAnimationFrame: (callback) => window.requestAnimationFrame(callback),
    cancelAnimationFrame: (frameId) => window.cancelAnimationFrame(frameId),
    loadModel(manifest, scene) {
      if (manifest.kind !== "procedural3d") return null;
      const avatar = createProceduralAvatar(scene.modelAnchor);
      return {
        object: avatar.object,
        owned: false,
        update(_deltaSeconds, target) {
          if (!target) return;
          avatar.handles.mouth.set(target.mouth / 0.65);
          avatar.handles.eyes.set(target.blinkRate === "off" ? 0.08 : 1);
          avatar.handles.head.set(
            target.posture === "cautious" || target.posture === "retracted" ? 0.35
              : target.posture === "active" ? 0.62
                : 0.5,
          );
          avatar.handles.body.set(target.posture === "active" ? 0.7 : 0.5);
          avatar.handles.coreLight.set(
            target.color === "red" ? 0.95
              : target.color === "dim" ? 0.18
                : target.color === "amber" ? 0.62
                  : 0.72,
          );
        },
        dispose: () => avatar.dispose(),
      };
    },
  };
}

function positiveDimension(value: number | undefined, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return fallback;
  return Math.max(1, Math.round(value));
}

function positivePixelRatio(value: number | undefined): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return 1;
  return Math.min(2, value);
}

function collectMaterialResources(
  material: Material,
  resources: Set<DisposableResource>,
): void {
  resources.add(material);
  for (const value of Object.values(material)) {
    if (value instanceof Texture) resources.add(value);
  }
}

function collectModelResources(model: AvatarLoadedModel): Set<DisposableResource> {
  const resources = new Set<DisposableResource>(model.ownedResources || []);
  model.object.traverse((node) => {
    const renderable = node as Object3D & {
      geometry?: BufferGeometry;
      material?: Material | Material[];
    };
    if (renderable.geometry) resources.add(renderable.geometry);
    const materials = Array.isArray(renderable.material)
      ? renderable.material
      : renderable.material
        ? [renderable.material]
        : [];
    for (const material of materials) collectMaterialResources(material, resources);
  });
  return resources;
}

function disposeModel(model: AvatarLoadedModel | null): void {
  if (!model) return;
  if (model.owned !== false) {
    const shared = new Set<DisposableResource>(model.sharedResources || []);
    for (const resource of collectModelResources(model)) {
      if (!shared.has(resource)) resource.dispose();
    }
  }
  model.dispose?.();
}

export async function createAvatarSurface(
  options: CreateAvatarSurfaceOptions,
): Promise<AvatarSurfaceController> {
  const resources = options.resources || createDefaultResources();
  const tier = options.tier || "3d-high";
  const mode = options.mode || "pet";
  const scene = createAvatarScene();
  const renderer = resources.createRenderer({
    alpha: true,
    antialias: tier === "3d-high",
    premultipliedAlpha: true,
  });
  const canvas = renderer.domElement;
  const clock = resources.createClock();
  const listeners: Array<readonly [string, EventListener]> = [];
  let model: AvatarLoadedModel | null = null;
  let frameId: number | null = null;
  let visible = true;
  let contextLost = false;
  let disposed = false;
  let frameCount = 0;
  let expressionTarget: AvatarExpressionTarget | null = null;
  let width = positiveDimension(options.width, options.host.clientWidth || 200);
  let height = positiveDimension(options.height, options.host.clientHeight || 218);

  const canContinuouslyRender = (): boolean =>
    visible && !contextLost && !disposed && (mode === "pet" || mode === "live");

  const cancelFrame = (): void => {
    if (frameId === null) return;
    resources.cancelAnimationFrame(frameId);
    frameId = null;
  };

  const renderFrame = (): void => {
    if (disposed || contextLost) return;
    const deltaSeconds = clock.getDelta();
    model?.update?.(deltaSeconds, expressionTarget);
    renderer.render(scene.scene, scene.camera);
    frameCount += 1;
    options.onFrame?.(Math.max(0, deltaSeconds * 1_000));
  };

  const scheduleFrame = (): void => {
    if (!canContinuouslyRender() || frameId !== null) return;
    frameId = resources.requestAnimationFrame(() => {
      frameId = null;
      if (!canContinuouslyRender()) return;
      renderFrame();
      scheduleFrame();
    });
  };

  const addCanvasListener = (type: string, listener: EventListener): void => {
    canvas.addEventListener(type, listener);
    listeners.push([type, listener]);
  };

  const handleContextLost: EventListener = (event) => {
    event.preventDefault();
    contextLost = true;
    cancelFrame();
    options.onContextLost?.();
  };
  const handleContextRestored: EventListener = () => {
    if (disposed) return;
    contextLost = false;
    clock.start?.();
    scheduleFrame();
    options.onContextRestored?.();
  };

  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio?.(positivePixelRatio(options.pixelRatio));
  renderer.setSize(width, height, false);
  scene.setSize(width, height);
  canvas.classList.add("avatar-canvas");
  canvas.setAttribute("aria-hidden", "true");
  addCanvasListener("webglcontextlost", handleContextLost);
  addCanvasListener("webglcontextrestored", handleContextRestored);
  options.host.appendChild(canvas);

  try {
    model = await resources.loadModel?.(options.manifest, scene) || null;
    if (model) scene.modelAnchor.add(model.object);
  } catch (error) {
    for (const [type, listener] of listeners) canvas.removeEventListener(type, listener);
    listeners.length = 0;
    scene.dispose();
    renderer.dispose();
    canvas.remove();
    throw error;
  }

  clock.start?.();
  scheduleFrame();

  return {
    setSize(nextWidth, nextHeight) {
      if (disposed) return;
      width = positiveDimension(nextWidth, width);
      height = positiveDimension(nextHeight, height);
      renderer.setSize(width, height, false);
      scene.setSize(width, height);
    },
    setVisible(nextVisible) {
      if (disposed || visible === nextVisible) return;
      visible = nextVisible;
      if (!visible) {
        cancelFrame();
        clock.stop?.();
        return;
      }
      clock.start?.();
      scheduleFrame();
    },
    renderOnce() {
      renderFrame();
    },
    setExpressionTarget(target) {
      if (disposed) return;
      expressionTarget = target;
    },
    diagnostics() {
      return {
        manifestId: options.manifest.id,
        tier,
        mode,
        width,
        height,
        visible,
        rendering: frameId !== null,
        contextLost,
        disposed,
        frameCount,
        expressionTarget,
      };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      visible = false;
      cancelFrame();
      clock.stop?.();
      for (const [type, listener] of listeners) canvas.removeEventListener(type, listener);
      listeners.length = 0;
      if (model) scene.modelAnchor.remove(model.object);
      disposeModel(model);
      model = null;
      scene.dispose();
      renderer.dispose();
      canvas.remove();
      expressionTarget = null;
    },
  };
}
