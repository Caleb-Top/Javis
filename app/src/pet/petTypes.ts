import type { LiveState } from "../live/liveState";
import type { RuntimeSnapshot } from "../state/runtimeStateTypes.ts";

export type PetSkin = {
  id: string;
  name: string;
  description: string;
  idleAsset: string;
  sourceAtlasAsset?: string;
  accent: string;
  fallbackClass: string;
};

export type PetSurfaceOptions = {
  root: HTMLElement;
  onOpenLive: () => void;
  onOpenSettings: () => void;
  onContextMenu: () => void | Promise<void>;
};

export type PetSurfaceController = {
  setState(state: LiveState): void;
  setSnapshot(snapshot: RuntimeSnapshot): void;
  setSkin(skinId: string): void;
  toggleSkin(): void;
  openSkinPicker(): void;
  dispose(): void;
};
