import type { LiveState } from "../live/liveState";
import type { ExpressionListener } from "../life/lifeTypes.ts";
import type { RuntimeSnapshot } from "../state/runtimeStateTypes.ts";
import type { AvatarKind } from "./avatar/avatarTypes.ts";
import type {
  AvatarSurfaceController,
  CreateAvatarSurfaceOptions,
} from "./avatar/AvatarSurface.ts";

type PetSkinBase = Readonly<{
  id: string;
  manifestId: string;
  name: string;
  description: string;
  accent: string;
  fallbackClass: string;
}>;

export type PetThreeDimensionalSkin = PetSkinBase & Readonly<{
  kind: Extract<AvatarKind, "procedural3d" | "vrm" | "gltf">;
  idleAsset?: never;
  sourceAtlasAsset?: never;
}>;

export type PetSpriteSkin = PetSkinBase & Readonly<{
  kind: "sprite2d";
  idleAsset: string;
  sourceAtlasAsset?: string;
}>;

export type PetOrbSkin = PetSkinBase & Readonly<{
  kind: "orb";
  idleAsset?: never;
  sourceAtlasAsset?: never;
}>;

export type PetSkin = PetThreeDimensionalSkin | PetSpriteSkin | PetOrbSkin;

export type AvatarLoader = (
  options: CreateAvatarSurfaceOptions,
) => Promise<AvatarSurfaceController>;

type DisposableAvatarLoad = Readonly<{ dispose(): void }>;

export class PetSkinLoadGeneration {
  #current = 0;

  next(): number {
    this.#current += 1;
    return this.#current;
  }

  invalidate(): void {
    this.#current += 1;
  }

  isCurrent(generation: number): boolean {
    return generation === this.#current;
  }

  accept(generation: number, loaded: DisposableAvatarLoad): boolean {
    if (this.isCurrent(generation)) return true;
    loaded.dispose();
    return false;
  }
}

export type PetSurfaceOptions = {
  root: HTMLElement;
  onOpenLive: () => void;
  onOpenSettings: () => void;
  onContextMenu: () => void | Promise<void>;
  avatarLoader?: AvatarLoader;
  subscribeExpression?: (listener: ExpressionListener) => () => void;
};

export type PetSurfaceController = {
  setState(state: LiveState): void;
  setSnapshot(snapshot: RuntimeSnapshot): void;
  setSkin(skinId: string): void;
  toggleSkin(): void;
  openSkinPicker(): void;
  dispose(): void;
};
