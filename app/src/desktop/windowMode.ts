import {
  currentMonitor,
  getCurrentWindow,
  LogicalPosition,
  LogicalSize,
} from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { isTauriRuntime } from "../bridge/sidecarClient";
import {
  chooseMenuPlacement,
  getCompactPetSize,
  PET_MENU_EXTRA_WIDTH,
  type PetMenuPlacement,
} from "../pet/petWindowLayout";
import {
  getSurfaceMenuWindowTransition,
  type MenuSource,
} from "../menu/surfaceMenuState.ts";
import {
  getCenteredWindowPosition,
  getClampedWindowPosition,
  type Rect,
} from "./windowGeometry.ts";
import {
  getLiveSurfaceSize,
  LIVE_SURFACE_MIN_HEIGHT,
  LIVE_SURFACE_MIN_SIZE,
} from "./surfaceDimensions.ts";

export type DesktopMode = "live" | "pet" | "settings" | "code";

const ORB_MIN_SIZE = new LogicalSize(
  LIVE_SURFACE_MIN_SIZE,
  LIVE_SURFACE_MIN_HEIGHT,
);
const CODE_SIZE = new LogicalSize(1280, 820);
const SETTINGS_SIZE = new LogicalSize(980, 720);
const APP_MIN_SIZE = new LogicalSize(760, 560);
const PET_MIN_SIZE = new LogicalSize(118, 118);
let petScale = 1;
let surfaceMenuExpanded = false;
let surfaceMenuPlacement: PetMenuPlacement = "right";
let surfaceMenuSource: MenuSource = "pet";

async function getLogicalMonitorBounds(): Promise<Rect | null> {
  const monitor = await currentMonitor();
  if (!monitor) return null;
  const position = monitor.position.toLogical(monitor.scaleFactor);
  const size = monitor.size.toLogical(monitor.scaleFactor);
  return { x: position.x, y: position.y, width: size.width, height: size.height };
}

async function positionWindow(
  size: { width: number; height: number },
  centered: boolean,
  margin = 8,
): Promise<void> {
  const window = getCurrentWindow();
  const [monitor, physicalPosition, scaleFactor] = await Promise.all([
    getLogicalMonitorBounds(),
    window.outerPosition(),
    window.scaleFactor(),
  ]);
  if (!monitor) return;
  const currentPosition = physicalPosition.toLogical(scaleFactor);
  const position = centered
    ? getCenteredWindowPosition(monitor, size)
    : getClampedWindowPosition(currentPosition, size, monitor, margin);
  await window.setPosition(new LogicalPosition(position.x, position.y));
}

async function expandFullSurface(
  size: LogicalSize,
): Promise<void> {
  const window = getCurrentWindow();
  await window.setSize(size);
  await Promise.all([
    window.setAlwaysOnTop(false),
    window.setSkipTaskbar(false),
    window.setResizable(true),
    window.setMinSize(APP_MIN_SIZE),
    positionWindow({ width: size.width, height: size.height }, true),
  ]);
  await window.setFocus();
}

function applySurfaceMenuCss(
  source = surfaceMenuSource,
  placement = surfaceMenuPlacement,
): void {
  const compact = getCompactPetSize(petScale);
  const live = getLiveSurfaceSize(petScale);
  const layout = getSurfaceMenuWindowTransition(
    source,
    petScale,
    surfaceMenuExpanded,
    surfaceMenuExpanded,
    placement,
  );
  document.documentElement.style.setProperty("--pet-boundary-size", `${compact.boundary}px`);
  document.documentElement.style.setProperty("--pet-scale", String(petScale));
  document.documentElement.style.setProperty("--live-surface-visual-size", `${live.visual}px`);
  document.documentElement.style.setProperty("--pet-offset-x", `${source === "pet" ? layout.surfaceOffsetX : 0}px`);
  document.documentElement.style.setProperty("--surface-base-width", `${layout.baseWidth}px`);
  document.documentElement.style.setProperty(
    "--surface-visual-height",
    `${source === "live" ? live.visual : compact.width}px`,
  );
  document.documentElement.style.setProperty("--surface-menu-offset-x", `${layout.surfaceOffsetX}px`);
  document.body.dataset.surfaceMenuSource = source;
  document.body.dataset.surfaceMenuPlacement = placement;
  document.body.dataset.surfaceMenuExpanded = String(surfaceMenuExpanded);
}

function setSurfaceOffsetCss(source: MenuSource, offsetX: number): void {
  const roundedOffset = Math.round(offsetX * 10) / 10;
  document.documentElement.style.setProperty(
    "--pet-offset-x",
    `${source === "pet" ? roundedOffset : 0}px`,
  );
  document.documentElement.style.setProperty(
    "--surface-menu-offset-x",
    `${roundedOffset}px`,
  );
}

async function applyNativeMenuBounds(
  logicalX: number,
  logicalY: number,
  width: number,
  height: number,
  scaleFactor: number,
): Promise<void> {
  const window = getCurrentWindow();
  try {
    await invoke<void>("set_main_window_bounds", {
      x: Math.round(logicalX * scaleFactor),
      y: Math.round(logicalY * scaleFactor),
      width: Math.round(width * scaleFactor),
      height: Math.round(height * scaleFactor),
    });
  } catch {
    await window.setPosition(new LogicalPosition(logicalX, logicalY));
    await window.setSize(new LogicalSize(width, height));
  }
}

async function preserveSurfaceScreenPosition(
  source: MenuSource,
  targetScreenX: number,
  finalOffsetX: number,
  operation: () => Promise<void>,
): Promise<void> {
  let active = true;
  let frame = 0;
  const stabilize = (): void => {
    if (!active) return;
    const correction = Math.max(
      -2,
      Math.min(PET_MENU_EXTRA_WIDTH + 2, targetScreenX - window.screenX),
    );
    setSurfaceOffsetCss(source, correction);
    frame = window.requestAnimationFrame(stabilize);
  };
  stabilize();
  try {
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    await operation();
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
    await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve()));
  } finally {
    active = false;
    window.cancelAnimationFrame(frame);
    setSurfaceOffsetCss(source, finalOffsetX);
  }
}

async function resolveSurfaceMenuPlacement(source: MenuSource): Promise<PetMenuPlacement> {
  if (!isTauriRuntime()) return "right";
  const window = getCurrentWindow();
  const [position, scaleFactor, monitor] = await Promise.all([
    window.outerPosition(),
    window.scaleFactor(),
    currentMonitor(),
  ]);
  if (!monitor) return "right";
  const logicalWindowPosition = position.toLogical(scaleFactor);
  const logicalMonitorPosition = monitor.position.toLogical(monitor.scaleFactor);
  const logicalMonitorSize = monitor.size.toLogical(monitor.scaleFactor);
  const baseWidth = source === "live"
    ? getLiveSurfaceSize(petScale).width
    : getCompactPetSize(petScale).width;
  return chooseMenuPlacement(
    logicalWindowPosition.x,
    baseWidth,
    logicalMonitorPosition.x,
    logicalMonitorSize.width,
  );
}

export async function setPetScale(scale: number): Promise<void> {
  petScale = Math.min(1.3, Math.max(0.7, scale));
  applySurfaceMenuCss();
  if (!isTauriRuntime()) return;
  const window = getCurrentWindow();
  if (document.body.dataset.desktopMode === "pet") {
    const petMenuOpen = surfaceMenuExpanded && surfaceMenuSource === "pet";
    const layout = getSurfaceMenuWindowTransition(
      "pet",
      petScale,
      petMenuOpen,
      petMenuOpen,
      surfaceMenuPlacement,
    );
    await window.setSize(new LogicalSize(layout.width, layout.height));
    return;
  }
  if (document.body.dataset.desktopMode === "live") {
    const liveMenuOpen = surfaceMenuExpanded && surfaceMenuSource === "live";
    const layout = getSurfaceMenuWindowTransition(
      "live",
      petScale,
      liveMenuOpen,
      liveMenuOpen,
      surfaceMenuPlacement,
    );
    await window.setSize(new LogicalSize(layout.width, layout.height));
  }
}

export async function setSurfaceMenuExpanded(
  source: MenuSource,
  expanded: boolean,
  placement?: PetMenuPlacement,
): Promise<PetMenuPlacement> {
  const previousExpanded = surfaceMenuExpanded && surfaceMenuSource === source;
  const nextPlacement = expanded
    ? placement || await resolveSurfaceMenuPlacement(source)
    : surfaceMenuPlacement;
  surfaceMenuExpanded = expanded;
  surfaceMenuPlacement = nextPlacement;
  surfaceMenuSource = source;
  const previousOffset = previousExpanded && nextPlacement === "left"
    ? PET_MENU_EXTRA_WIDTH
    : 0;
  applySurfaceMenuCss(source, nextPlacement);
  setSurfaceOffsetCss(source, previousOffset);
  if (!isTauriRuntime() || document.body.dataset.desktopMode !== source) return nextPlacement;

  const window = getCurrentWindow();
  const layout = getSurfaceMenuWindowTransition(
    source,
    petScale,
    previousExpanded,
    expanded,
    nextPlacement,
  );
  const [position, scaleFactor] = await Promise.all([
    window.outerPosition(),
    window.scaleFactor(),
  ]);
  const logicalPosition = position.toLogical(scaleFactor);
  if (layout.windowDeltaX !== 0) {
    const targetScreenX = globalThis.screenX + previousOffset;
    await preserveSurfaceScreenPosition(
      source,
      targetScreenX,
      layout.surfaceOffsetX,
      () => applyNativeMenuBounds(
        logicalPosition.x + layout.windowDeltaX,
        logicalPosition.y,
        layout.width,
        layout.height,
        scaleFactor,
      ),
    );
  } else {
    await window.setSize(new LogicalSize(layout.width, layout.height));
    setSurfaceOffsetCss(source, layout.surfaceOffsetX);
  }
  return nextPlacement;
}

export function setPetMenuExpanded(
  expanded: boolean,
  placement?: PetMenuPlacement,
): Promise<PetMenuPlacement> {
  return setSurfaceMenuExpanded("pet", expanded, placement);
}

export async function setDesktopMode(mode: DesktopMode): Promise<void> {
  document.body.dataset.desktopMode = mode;
  surfaceMenuExpanded = false;
  if (mode === "live" || mode === "pet") {
    surfaceMenuSource = mode;
  }
  applySurfaceMenuCss();
  if (!isTauriRuntime()) return;

  const window = getCurrentWindow();
  try {
    if (mode === "live") {
      const live = getLiveSurfaceSize(petScale);
      await Promise.all([
        window.setAlwaysOnTop(true),
        window.setSkipTaskbar(false),
        window.setResizable(false),
        window.setMinSize(ORB_MIN_SIZE),
      ]);
      await window.setSize(new LogicalSize(live.width, live.height));
      await positionWindow(live, false, 0);
      await window.setFocus();
      return;
    }

    if (mode === "pet") {
      const compact = getCompactPetSize(petScale);
      await Promise.all([
        window.setAlwaysOnTop(true),
        window.setSkipTaskbar(true),
        window.setResizable(false),
        window.setMinSize(PET_MIN_SIZE),
      ]);
      await window.setSize(new LogicalSize(compact.width, compact.height));
      await positionWindow(compact, false, 0);
      await window.setFocus();
      return;
    }

    if (mode === "settings") {
      await expandFullSurface(SETTINGS_SIZE);
      return;
    }

    await expandFullSurface(CODE_SIZE);
  } catch {
    document.body.dataset.desktopMode = mode;
  }
}
