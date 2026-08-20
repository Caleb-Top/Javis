import { AnimationMixer, Euler, LoadingManager, Material, Quaternion, Texture } from "three";
import type { AnimationClip, Object3D } from "three";
import { VRMLoaderPlugin } from "@pixiv/three-vrm";
import type { VRM } from "@pixiv/three-vrm";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type {
  GLTFLoaderPlugin,
  GLTFParser,
} from "three/examples/jsm/loaders/GLTFLoader.js";

import type { AvatarLoadedModel } from "./AvatarSurface.ts";
import type {
  AvatarManifest,
  ExpressionTarget,
  GltfAvatarManifest,
  VrmAvatarManifest,
} from "./avatarTypes.ts";

type ModelManifest = VrmAvatarManifest | GltfAvatarManifest;
type DisposableResource = { dispose(): void };

export type AvatarGltf = Readonly<{
  scene: Object3D;
  animations?: readonly AnimationClip[];
  userData?: Record<string, unknown>;
}>;

export type AvatarGltfLoader = Readonly<{
  register(factory: (parser: unknown) => unknown): unknown;
  loadAsync(url: string): Promise<AvatarGltf>;
}>;

export type AvatarAnimationMixer = Readonly<{
  clipAction(clip: AnimationClip): { play(): unknown };
  update(deltaSeconds: number): void;
  stopAllAction(): void;
  uncacheRoot(root: Object3D): void;
}>;

export type AvatarLoadingManager = Readonly<{
  setURLModifier(modifier: (url: string) => string): unknown;
}>;

export type AvatarModelLoaderDependencies = Readonly<{
  createLoadingManager?(): AvatarLoadingManager;
  createGltfLoader?(manager: AvatarLoadingManager): AvatarGltfLoader;
  createVrmLoaderPlugin?(parser: unknown): unknown;
  createAnimationMixer?(root: Object3D): AvatarAnimationMixer;
}>;

export type AvatarModelLoader = (
  manifest: AvatarManifest,
  scene?: unknown,
) => Promise<AvatarLoadedModel | null>;

type ExpressionManagerLike = Readonly<{
  expressionMap?: Record<string, unknown>;
  getExpression?(name: string): unknown | null;
  setValue(name: string, weight: number): void;
}>;

type HumanoidLike = Readonly<{
  getNormalizedBoneNode?(name: string): Object3D | null;
}>;

type VrmRuntime = Pick<VRM, "scene" | "update"> & Readonly<{
  expressionManager?: ExpressionManagerLike;
  humanoid?: HumanoidLike;
}>;

type BoneBinding = Readonly<{
  node: Object3D;
  rest: Quaternion;
}>;

const DEFAULT_DEPENDENCIES: Required<AvatarModelLoaderDependencies> = {
  createLoadingManager: () => new LoadingManager(),
  createGltfLoader(manager) {
    const loader = new GLTFLoader(manager as LoadingManager);
    return {
      register(factory) {
        loader.register((parser) => factory(parser) as GLTFLoaderPlugin);
      },
      loadAsync: (url) => loader.loadAsync(url),
    };
  },
  createVrmLoaderPlugin: (parser) => new VRMLoaderPlugin(parser as GLTFParser),
  createAnimationMixer: (root) => new AnimationMixer(root),
};

function decodePath(path: string, label: string): string {
  let decoded = path;
  try {
    for (let pass = 0; pass < 3; pass += 1) {
      const next = decodeURIComponent(decoded);
      if (next === decoded) break;
      decoded = next;
    }
  } catch {
    throw new TypeError(`${label} must be a local asset path within /pets/`);
  }
  return decoded;
}

function assertLocalPetsPath(path: string, label: string): string {
  const decoded = decodePath(path, label);
  const parts = decoded.split("/");
  if (
    path.trim() !== path
    || !decoded.startsWith("/pets/")
    || decoded === "/pets/"
    || decoded.startsWith("//")
    || decoded.includes("\\")
    || decoded.includes("?")
    || decoded.includes("#")
    || decoded.includes("\0")
    || parts.some((part) => part === "." || part === "..")
  ) {
    throw new TypeError(`${label} must be a local asset path within /pets/`);
  }
  return decoded;
}

function assertModelManifest(manifest: AvatarManifest): asserts manifest is ModelManifest {
  if (manifest.kind !== "vrm" && manifest.kind !== "gltf") {
    throw new TypeError("avatar model loader only accepts vrm or gltf manifests");
  }

  const decoded = assertLocalPetsPath(manifest.model, "model").toLowerCase();
  const validExtension = manifest.kind === "vrm"
    ? decoded.endsWith(".vrm")
    : decoded.endsWith(".gltf") || decoded.endsWith(".glb");
  if (!validExtension) {
    throw new TypeError(`model extension does not match ${manifest.kind} manifest kind`);
  }
}

function guardResourceUrl(url: string): string {
  if (url.startsWith("blob:") || url.startsWith("data:")) return url;
  assertLocalPetsPath(url, "model resource");
  return url;
}

function clampUnit(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

function safeDelta(deltaSeconds: number): number {
  if (!Number.isFinite(deltaSeconds) || deltaSeconds <= 0) return 0;
  return Math.min(0.1, deltaSeconds);
}

function isVrmRuntime(value: unknown): value is VrmRuntime {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<VrmRuntime>;
  return typeof candidate.update === "function"
    && typeof candidate.scene === "object"
    && candidate.scene !== null;
}

function disposeObjectResources(object: Object3D): void {
  const resources = new Set<DisposableResource>();
  object.traverse((node) => {
    const renderable = node as Object3D & {
      geometry?: DisposableResource;
      material?: Material | Material[];
    };
    if (renderable.geometry) resources.add(renderable.geometry);
    const materials = Array.isArray(renderable.material)
      ? renderable.material
      : renderable.material
        ? [renderable.material]
        : [];
    for (const material of materials) {
      resources.add(material);
      for (const value of Object.values(material)) {
        if (value instanceof Texture) resources.add(value);
      }
    }
  });
  for (const resource of resources) resource.dispose();
}

function resolveExpression(
  manager: ExpressionManagerLike | undefined,
  candidates: readonly string[],
): string | null {
  if (!manager) return null;
  const expressionNames = Object.keys(manager.expressionMap || {});
  const normalizedNames = new Map(expressionNames.map((name) => [name.toLowerCase(), name]));

  for (const candidate of candidates) {
    const mapped = normalizedNames.get(candidate.toLowerCase());
    if (mapped) return mapped;
    try {
      if (manager.getExpression?.(candidate)) return candidate;
    } catch {
      // A partial or old VRM expression manager is treated as an unsupported channel.
    }
  }
  return null;
}

function resolveBlinkExpressions(manager: ExpressionManagerLike | undefined): readonly string[] {
  const combined = resolveExpression(manager, ["blink"]);
  if (combined) return [combined];
  const split = [
    resolveExpression(manager, ["blinkLeft", "blink_l"]),
    resolveExpression(manager, ["blinkRight", "blink_r"]),
  ].filter((name): name is string => name !== null);
  return [...new Set(split)];
}

function setExpression(
  manager: ExpressionManagerLike | undefined,
  names: readonly string[],
  value: number,
): void {
  if (!manager) return;
  const weight = clampUnit(value);
  for (const name of names) {
    try {
      manager.setValue(name, weight);
    } catch {
      // Missing optional expression channels must not break the render loop.
    }
  }
}

function resolveBone(humanoid: HumanoidLike | undefined, name: string): BoneBinding | null {
  try {
    const node = humanoid?.getNormalizedBoneNode?.(name) || null;
    return node ? { node, rest: node.quaternion.clone() } : null;
  } catch {
    return null;
  }
}

function resolveBodyBones(humanoid: HumanoidLike | undefined): readonly BoneBinding[] {
  const bindings = ["chest", "spine", "hips"]
    .map((name) => resolveBone(humanoid, name))
    .filter((binding): binding is BoneBinding => binding !== null);
  const seen = new Set<Object3D>();
  return bindings.filter(({ node }) => {
    if (seen.has(node)) return false;
    seen.add(node);
    return true;
  });
}

function applyBoneOffset(binding: BoneBinding, x: number, y: number, z: number): void {
  const offset = new Quaternion().setFromEuler(new Euler(
    Math.max(-0.18, Math.min(0.18, x)),
    Math.max(-0.18, Math.min(0.18, y)),
    Math.max(-0.18, Math.min(0.18, z)),
  ));
  binding.node.quaternion.copy(binding.rest).multiply(offset).normalize();
}

function blinkWeight(target: ExpressionTarget | null, elapsedSeconds: number): number {
  if (!target || target.blinkRate === "off") return 0;
  const timing = target.blinkRate === "slow"
    ? { period: 4.2, duration: 0.15 }
    : target.blinkRate === "fast"
      ? { period: 1.8, duration: 0.12 }
      : { period: 3, duration: 0.13 };
  const phase = elapsedSeconds % timing.period;
  return phase < timing.duration ? Math.sin(Math.PI * phase / timing.duration) : 0;
}

function postureOffsets(target: ExpressionTarget | null): Readonly<{
  headPitch: number;
  headYaw: number;
  headRoll: number;
  bodyPitch: number;
}> {
  if (!target) return { headPitch: 0, headYaw: 0, headRoll: 0, bodyPitch: 0 };
  const headPitch = target.posture === "retracted" ? 0.12
    : target.posture === "cautious" ? 0.08
      : target.posture === "active" ? -0.05
        : target.posture === "attentive" || target.posture === "focused" ? -0.025
          : 0;
  const bodyPitch = target.posture === "retracted" ? 0.1
    : target.posture === "cautious" ? 0.065
      : target.posture === "active" ? -0.045
        : 0;
  const headYaw = target.gaze === "content" ? -0.07 : target.gaze === "task" ? 0.07 : 0;
  const headRoll = target.interrupt ? 0.035 : 0;
  return { headPitch, headYaw, headRoll, bodyPitch };
}

function coreWeight(target: ExpressionTarget | null): number {
  if (!target) return 0;
  if (target.color === "red") return 0.95;
  if (target.color === "amber") return 0.7;
  if (target.color === "violet") return 0.62;
  if (target.color === "dim") return 0.15;
  return 0.58;
}

function createLoadedModel(
  manifest: ModelManifest,
  gltf: AvatarGltf,
  dependencies: Required<AvatarModelLoaderDependencies>,
): AvatarLoadedModel {
  const vrmCandidate = manifest.kind === "vrm" ? gltf.userData?.vrm : undefined;
  if (manifest.kind === "vrm" && !isVrmRuntime(vrmCandidate)) {
    disposeObjectResources(gltf.scene);
    throw new Error(`VRM loader did not produce a VRM runtime for ${manifest.model}`);
  }
  const vrm = isVrmRuntime(vrmCandidate) ? vrmCandidate : null;
  const object = vrm?.scene || gltf.scene;
  const animations = gltf.animations || [];
  const mixer = animations.length > 0 ? dependencies.createAnimationMixer(object) : null;
  for (const animation of animations) mixer?.clipAction(animation).play();

  const capabilities = new Set(manifest.capabilities);
  const expressions = vrm?.expressionManager;
  const blinkExpressions = capabilities.has("blink")
    ? resolveBlinkExpressions(expressions)
    : [];
  const mouthExpression = capabilities.has("mouth")
    ? resolveExpression(expressions, ["aa", "oh", "ou", "ih", "ee", "mouthOpen"])
    : null;
  const coreExpression = capabilities.has("coreLight")
    ? resolveExpression(expressions, ["coreLight", "core", "emission", "glow"])
    : null;
  const head = capabilities.has("head") ? resolveBone(vrm?.humanoid, "head") : null;
  const body = capabilities.has("body") ? resolveBodyBones(vrm?.humanoid) : [];
  let elapsedSeconds = 0;
  let disposed = false;

  return {
    object,
    owned: true,
    update(deltaSeconds, target) {
      if (disposed) return;
      const delta = safeDelta(deltaSeconds);
      elapsedSeconds = (elapsedSeconds + delta) % 60;
      mixer?.update(delta);

      if (vrm) {
        setExpression(expressions, blinkExpressions, blinkWeight(target, elapsedSeconds));
        setExpression(expressions, mouthExpression ? [mouthExpression] : [], target?.mouth || 0);
        setExpression(expressions, coreExpression ? [coreExpression] : [], coreWeight(target));
        const offsets = postureOffsets(target);
        if (head) applyBoneOffset(head, offsets.headPitch, offsets.headYaw, offsets.headRoll);
        for (const binding of body) applyBoneOffset(binding, offsets.bodyPitch, 0, 0);
        vrm.update(delta);
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      mixer?.stopAllAction();
      mixer?.uncacheRoot(object);
    },
  };
}

export async function loadAvatarModel(
  manifest: AvatarManifest,
  injected: AvatarModelLoaderDependencies = {},
): Promise<AvatarLoadedModel> {
  assertModelManifest(manifest);
  const dependencies: Required<AvatarModelLoaderDependencies> = {
    ...DEFAULT_DEPENDENCIES,
    ...injected,
  };
  const manager = dependencies.createLoadingManager();
  manager.setURLModifier(guardResourceUrl);
  const loader = dependencies.createGltfLoader(manager);
  if (manifest.kind === "vrm") {
    loader.register((parser) => dependencies.createVrmLoaderPlugin(parser));
  }
  const gltf = await loader.loadAsync(manifest.model);
  return createLoadedModel(manifest, gltf, dependencies);
}

export function createAvatarModelLoader(
  dependencies: AvatarModelLoaderDependencies = {},
): AvatarModelLoader {
  return (manifest) => loadAvatarModel(manifest, dependencies);
}
