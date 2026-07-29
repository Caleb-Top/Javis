import {
  PET_MENU_EXTRA_WIDTH,
  getCompactPetSize,
  type PetMenuPlacement,
} from "../pet/petWindowLayout.ts";
import { getLiveSurfaceSize } from "../desktop/surfaceDimensions.ts";

export const SURFACE_MENU_MIN_HEIGHT = 200;

export type MenuSource = "live" | "pet";

export type SurfaceMenuState = {
  open: boolean;
  source: MenuSource;
  placement: PetMenuPlacement;
};

export type SurfaceMenuWindowTransition = {
  width: number;
  height: number;
  baseWidth: number;
  surfaceOffsetX: number;
  windowDeltaX: number;
};

export function createClosedMenuState(): SurfaceMenuState {
  return {
    open: false,
    source: "live",
    placement: "right",
  };
}

export function openSurfaceMenu(
  state: SurfaceMenuState,
  source: MenuSource,
  placement: PetMenuPlacement,
): SurfaceMenuState {
  if (state.open) return state;
  return { open: true, source, placement };
}

export function closeSurfaceMenu(state: SurfaceMenuState): SurfaceMenuState {
  return { ...state, open: false };
}

export function getSurfaceMenuWindowTransition(
  source: MenuSource,
  scale: number,
  fromExpanded: boolean,
  toExpanded: boolean,
  placement: PetMenuPlacement,
): SurfaceMenuWindowTransition {
  const base = source === "live"
    ? getLiveSurfaceSize(scale)
    : getCompactPetSize(scale);
  const expandsLeft = placement === "left";
  const openingLeft = !fromExpanded && toExpanded && expandsLeft;
  const closingLeft = fromExpanded && !toExpanded && expandsLeft;

  return {
    width: base.width + (toExpanded ? PET_MENU_EXTRA_WIDTH : 0),
    height: toExpanded ? Math.max(base.height, SURFACE_MENU_MIN_HEIGHT) : base.height,
    baseWidth: base.width,
    surfaceOffsetX: toExpanded && expandsLeft ? PET_MENU_EXTRA_WIDTH : 0,
    windowDeltaX: openingLeft
      ? -PET_MENU_EXTRA_WIDTH
      : closingLeft
        ? PET_MENU_EXTRA_WIDTH
        : 0,
  };
}
