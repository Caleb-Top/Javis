export const AVATAR_KINDS = [
  "vrm",
  "gltf",
  "procedural3d",
  "sprite2d",
  "orb",
] as const;

export type AvatarKind = (typeof AVATAR_KINDS)[number];

export const AVATAR_CAPABILITIES = [
  "idle",
  "blink",
  "gaze",
  "mouth",
  "head",
  "body",
  "coreLight",
  "expression",
  "gesture",
] as const;

export type AvatarCapability = (typeof AVATAR_CAPABILITIES)[number];

export type AvatarLicense = Readonly<{
  id: string;
  author: string;
  source: string;
  attributionFile: string;
}>;

export type AvatarAssetBudget = Readonly<{
  maxBytes: number;
  maxTriangles: number;
  maxMaterials: number;
  maxTextureSize: number;
}>;

type AvatarManifestBase = Readonly<{
  schemaVersion: 1;
  id: string;
  version: string;
  name: string;
  description: string;
  accent: string;
  license: AvatarLicense;
  capabilities: readonly AvatarCapability[];
  assetBudget: AvatarAssetBudget;
  fallbackId: string;
}>;

export type VrmAvatarManifest = AvatarManifestBase & Readonly<{
  kind: "vrm";
  model: string;
  modelSha256: string;
  asset?: never;
  assetSha256?: never;
}>;

export type GltfAvatarManifest = AvatarManifestBase & Readonly<{
  kind: "gltf";
  model: string;
  modelSha256: string;
  asset?: never;
  assetSha256?: never;
}>;

export type ProceduralAvatarManifest = AvatarManifestBase & Readonly<{
  kind: "procedural3d";
  model?: never;
  modelSha256?: never;
  asset?: never;
  assetSha256?: never;
}>;

export type SpriteAvatarManifest = AvatarManifestBase & Readonly<{
  kind: "sprite2d";
  asset: string;
  assetSha256: string;
  model?: never;
  modelSha256?: never;
}>;

export type OrbAvatarManifest = AvatarManifestBase & Readonly<{
  kind: "orb";
  model?: never;
  modelSha256?: never;
  asset?: never;
  assetSha256?: never;
}>;

export type AvatarManifest =
  | VrmAvatarManifest
  | GltfAvatarManifest
  | ProceduralAvatarManifest
  | SpriteAvatarManifest
  | OrbAvatarManifest;

export type AvatarExpressionState =
  | "idle"
  | "attention"
  | "listening"
  | "thinking"
  | "speaking"
  | "executing"
  | "blocked"
  | "error"
  | "offline";

export type AvatarGazeTarget = "none" | "user" | "content" | "task";
export type AvatarBlinkRate = "off" | "slow" | "normal" | "fast";
export type AvatarPosture =
  | "neutral"
  | "attentive"
  | "focused"
  | "active"
  | "cautious"
  | "retracted";
export type AvatarColorIntent = "cyan" | "violet" | "amber" | "red" | "dim";
export type AvatarGesture = "none" | "working";

export type ExpressionTarget = Readonly<{
  state: AvatarExpressionState;
  gaze: AvatarGazeTarget;
  mouth: number;
  blinkRate: AvatarBlinkRate;
  posture: AvatarPosture;
  color: AvatarColorIntent;
  gesture: AvatarGesture;
  interrupt: boolean;
}>;

export type AvatarPerformanceTier =
  | "3d-high"
  | "3d-low"
  | "procedural3d"
  | "sprite2d"
  | "orb";
