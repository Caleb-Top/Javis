export type PetMenuPlacement = "left" | "right";

export const PET_BOUNDARY_BASE = 148;
export const PET_TRANSPARENT_PADDING = 10;
export const PET_STATUS_HEIGHT = 32;
export const PET_MENU_EXTRA_WIDTH = 116;

export type CompactPetSize = {
  boundary: number;
  width: number;
  height: number;
};

export type ExpandedPetLayout = CompactPetSize & {
  placement: PetMenuPlacement;
  petOffsetX: number;
  windowDeltaX: number;
};

export type PetWindowTransition = {
  width: number;
  height: number;
  boundary: number;
  petOffsetX: number;
  windowDeltaX: number;
};

export function getCompactPetSize(scale: number): CompactPetSize {
  const normalizedScale = Math.min(1.3, Math.max(0.7, scale));
  const boundary = Math.round(PET_BOUNDARY_BASE * normalizedScale);
  const windowSize = boundary + PET_TRANSPARENT_PADDING * 2;
  return {
    boundary,
    width: windowSize,
    height: windowSize + PET_STATUS_HEIGHT,
  };
}

export function getExpandedPetLayout(scale: number, placement: PetMenuPlacement): ExpandedPetLayout {
  const compact = getCompactPetSize(scale);
  const expandsLeft = placement === "left";
  return {
    ...compact,
    width: compact.width + PET_MENU_EXTRA_WIDTH,
    placement,
    petOffsetX: expandsLeft ? PET_MENU_EXTRA_WIDTH : 0,
    windowDeltaX: expandsLeft ? -PET_MENU_EXTRA_WIDTH : 0,
  };
}

export function chooseMenuPlacement(
  windowX: number,
  windowWidth: number,
  monitorX: number,
  monitorWidth: number,
): PetMenuPlacement {
  const leftSpace = windowX - monitorX;
  const rightSpace = monitorX + monitorWidth - (windowX + windowWidth);
  if (rightSpace >= PET_MENU_EXTRA_WIDTH) return "right";
  if (leftSpace >= PET_MENU_EXTRA_WIDTH) return "left";
  return rightSpace >= leftSpace ? "right" : "left";
}

export function getPetWindowTransition(
  scale: number,
  fromExpanded: boolean,
  toExpanded: boolean,
  placement: PetMenuPlacement,
): PetWindowTransition {
  const compact = getCompactPetSize(scale);
  const expanded = getExpandedPetLayout(scale, placement);
  const openingLeft = !fromExpanded && toExpanded && placement === "left";
  const closingLeft = fromExpanded && !toExpanded && placement === "left";
  return {
    width: toExpanded ? expanded.width : compact.width,
    height: compact.height,
    boundary: compact.boundary,
    petOffsetX: toExpanded ? expanded.petOffsetX : 0,
    windowDeltaX: openingLeft
      ? -PET_MENU_EXTRA_WIDTH
      : closingLeft
        ? PET_MENU_EXTRA_WIDTH
        : 0,
  };
}
