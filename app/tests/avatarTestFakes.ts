import {
  BufferGeometry,
  Mesh,
  MeshBasicMaterial,
  Texture,
  type WebGLRendererParameters,
} from "three";

import type { ExpressionBaseState, ExpressionIntent } from "../src/life/lifeTypes.ts";
import { parseAvatarManifest } from "../src/pet/avatar/AvatarManifest.ts";
import type { AvatarCapability } from "../src/pet/avatar/avatarTypes.ts";
import type {
  AvatarExpressionTarget,
  AvatarLoadedModel,
  AvatarSurfaceResources,
} from "../src/pet/avatar/AvatarSurface.ts";

type Listener = EventListenerOrEventListenerObject;

class FakeClassList {
  private readonly values = new Set<string>();

  add(...tokens: string[]): void {
    for (const token of tokens) this.values.add(token);
  }

  remove(...tokens: string[]): void {
    for (const token of tokens) this.values.delete(token);
  }

  contains(token: string): boolean {
    return this.values.has(token);
  }
}

class FakeCanvas {
  readonly nodeName = "CANVAS";
  readonly classList = new FakeClassList();
  parentElement: FakeHost | null = null;
  private readonly attributes = new Map<string, string>();
  private readonly listeners = new Map<string, Set<Listener>>();

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }

  addEventListener(type: string, listener: Listener): void {
    const registered = this.listeners.get(type) || new Set<Listener>();
    registered.add(listener);
    this.listeners.set(type, registered);
  }

  removeEventListener(type: string, listener: Listener): void {
    this.listeners.get(type)?.delete(listener);
  }

  dispatch(type: string, event: Event): void {
    for (const listener of this.listeners.get(type) || []) {
      if (typeof listener === "function") listener.call(this, event);
      else listener.handleEvent(event);
    }
  }

  listenerCount(type: string): number {
    return this.listeners.get(type)?.size || 0;
  }

  remove(): void {
    this.parentElement?.removeChild(this);
  }
}

class FakeHost {
  readonly clientWidth = 200;
  readonly clientHeight = 218;
  private readonly children: FakeCanvas[] = [];

  appendChild(canvas: FakeCanvas): FakeCanvas {
    if (canvas.parentElement) canvas.parentElement.removeChild(canvas);
    this.children.push(canvas);
    canvas.parentElement = this;
    return canvas;
  }

  removeChild(canvas: FakeCanvas): FakeCanvas {
    const index = this.children.indexOf(canvas);
    if (index >= 0) this.children.splice(index, 1);
    canvas.parentElement = null;
    return canvas;
  }

  querySelectorAll(selector: string): FakeCanvas[] {
    if (selector !== "canvas.avatar-canvas") return [];
    return this.children.filter((child) => child.classList.contains("avatar-canvas"));
  }
}

export function createAvatarHostFixture() {
  return new FakeHost() as unknown as HTMLElement & FakeHost;
}

export function createAvatarResourceFixture(
  options: { modelOwned?: boolean; sharedTexture?: boolean } = {},
) {
  const canvas = new FakeCanvas();
  const callbacks = new Map<number, FrameRequestCallback>();
  let nextFrameId = 1;
  const counters = {
    requestedFrameCount: 0,
    cancelledFrameCount: 0,
    renderCount: 0,
    rendererDisposeCount: 0,
    textureCount: 1,
    textureDisposeCount: 0,
    materialCount: 1,
    materialDisposeCount: 0,
    geometryCount: 1,
    geometryDisposeCount: 0,
    modelUpdateCount: 0,
    clockStartCount: 0,
    clockStopCount: 0,
    clockDeltaCount: 0,
    clearColor: -1,
    clearAlpha: -1,
    rendererOptions: null as WebGLRendererParameters | null,
    sizes: [] as Array<readonly [number, number, boolean | undefined]>,
  };
  const geometry = new BufferGeometry();
  const texture = new Texture();
  const material = new MeshBasicMaterial({ map: texture, alphaMap: texture });
  const originalGeometryDispose = geometry.dispose.bind(geometry);
  const originalMaterialDispose = material.dispose.bind(material);
  const originalTextureDispose = texture.dispose.bind(texture);
  geometry.dispose = () => {
    counters.geometryDisposeCount += 1;
    originalGeometryDispose();
  };
  material.dispose = () => {
    counters.materialDisposeCount += 1;
    originalMaterialDispose();
  };
  texture.dispose = () => {
    counters.textureDisposeCount += 1;
    originalTextureDispose();
  };
  const model: AvatarLoadedModel = {
    object: new Mesh(geometry, material),
    owned: options.modelOwned !== false,
    sharedResources: options.sharedTexture ? [texture] : [],
    update() {
      counters.modelUpdateCount += 1;
    },
  };
  const resources: AvatarSurfaceResources = {
    createRenderer(rendererOptions) {
      counters.rendererOptions = rendererOptions;
      return {
        domElement: canvas as unknown as HTMLCanvasElement,
        setClearColor(color, alpha) {
          counters.clearColor = color;
          counters.clearAlpha = alpha ?? 1;
        },
        setSize(width, height, updateStyle) {
          counters.sizes.push([width, height, updateStyle]);
        },
        render() {
          counters.renderCount += 1;
        },
        dispose() {
          counters.rendererDisposeCount += 1;
        },
      };
    },
    createClock() {
      return {
        getDelta() {
          counters.clockDeltaCount += 1;
          return 1 / 60;
        },
        start() {
          counters.clockStartCount += 1;
        },
        stop() {
          counters.clockStopCount += 1;
        },
      };
    },
    requestAnimationFrame(callback) {
      const frameId = nextFrameId;
      nextFrameId += 1;
      callbacks.set(frameId, callback);
      counters.requestedFrameCount += 1;
      return frameId;
    },
    cancelAnimationFrame(frameId) {
      callbacks.delete(frameId);
      counters.cancelledFrameCount += 1;
    },
    loadModel() {
      return model;
    },
  };

  return {
    resources,
    counters,
    canvas,
    runNextFrame(now = 0): boolean {
      const entry = callbacks.entries().next();
      if (entry.done) return false;
      const [frameId, callback] = entry.value;
      callbacks.delete(frameId);
      callback(now);
      return true;
    },
  };
}

export function proceduralManifestFixture() {
  return parseAvatarManifest({
    schemaVersion: 1,
    id: "javis-lightform-fixture",
    kind: "procedural3d",
    name: "Javis Lightform Fixture",
    accent: "#61e9f0",
    license: {
      id: "Javis-Test-Only",
      author: "Javis tests",
      source: "local fixture",
    },
    capabilities: ["idle", "blink", "gaze", "mouth"],
    assetBudget: {
      maxBytes: 25 * 1024 * 1024,
      maxTriangles: 80_000,
      maxMaterials: 8,
      maxTextureSize: 2048,
    },
    fallbackId: "javis-anime",
  });
}

export function createDeferredAvatarLoaderFixture<T = void>(defaultValue?: T) {
  const pending: Array<(value: T) => void> = [];
  return {
    load(..._arguments: unknown[]): Promise<T> {
      return new Promise<T>((resolve) => pending.push(resolve));
    },
    resolveAll(value: T = defaultValue as T): void {
      const resolvers = pending.splice(0);
      for (const resolve of resolvers) resolve(value);
    },
    pendingCount(): number {
      return pending.length;
    },
  };
}

export function intentFixture(
  state: ExpressionBaseState = "idle",
  overrides: Partial<ExpressionIntent> = {},
): ExpressionIntent {
  return {
    schema_version: 1,
    revision: 1,
    base_state: state,
    intensity: 0.5,
    gaze_target: "user",
    voice_activity: state === "speaking" ? "speaking" : state === "listening" ? "listening" : "silent",
    transition_ms: 180,
    interrupt: false,
    source_snapshot_revision: 1,
    generated_at: "2026-08-20T00:00:00.000Z",
    expires_at: "2099-08-20T00:00:10.000Z",
    explanation_code: "test_fixture",
    ...overrides,
  };
}

export function capabilitiesFixture(): AvatarCapability[] {
  return ["idle", "blink", "gaze", "mouth", "expression", "gesture"];
}

export function speakingTargetFixture(
  overrides: Partial<AvatarExpressionTarget> = {},
): AvatarExpressionTarget {
  return {
    state: "speaking",
    gaze: "user",
    mouth: 0.6,
    blinkRate: "normal",
    posture: "attentive",
    color: "cyan",
    gesture: "none",
    interrupt: false,
    ...overrides,
  };
}

export function expiredSpeakingIntentFixture(
  overrides: Partial<ExpressionIntent> = {},
): ExpressionIntent {
  return intentFixture("speaking", {
    generated_at: "2020-01-01T00:00:00.000Z",
    expires_at: "2020-01-01T00:00:01.000Z",
    ...overrides,
  });
}
