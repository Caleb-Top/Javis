import {
  currentMonitor,
  getCurrentWindow,
  LogicalPosition,
} from "@tauri-apps/api/window";
import { isTauriRuntime } from "../bridge/sidecarClient";
import {
  getPartiallyVisibleWindowPosition,
  type Point,
  type Rect,
  type Size,
} from "./windowGeometry.ts";

const INTERACTIVE_SELECTOR = "button, input, textarea, select, option, a, [role='button']";
const DRAG_THRESHOLD_PX = 4;
const MINIMUM_VISIBLE_PX = 32;

type CompactDragContext = {
  pointerStart: Point;
  windowStart: Point;
  windowSize: Size;
  monitor: Rect;
};

export function installPointerDrag(element: HTMLElement, onDragStart?: () => void): () => void {
  let pointerId: number | null = null;
  let startX = 0;
  let startY = 0;
  let dragging = false;
  let dragContext: Promise<CompactDragContext | null> | null = null;
  let pendingPosition: Point | null = null;
  let positioning = false;

  const queuePosition = (position: Point): void => {
    pendingPosition = position;
    if (positioning) return;
    positioning = true;
    const window = getCurrentWindow();
    void (async () => {
      while (pendingPosition) {
        const next = pendingPosition;
        pendingPosition = null;
        await window.setPosition(new LogicalPosition(next.x, next.y));
      }
    })()
      .catch(() => {
        pendingPosition = null;
      })
      .finally(() => {
        positioning = false;
        if (pendingPosition) queuePosition(pendingPosition);
      });
  };
  const reset = (): void => {
    const activePointerId = pointerId;
    pointerId = null;
    if (activePointerId !== null && element.hasPointerCapture?.(activePointerId)) {
      element.releasePointerCapture?.(activePointerId);
    }
    dragging = false;
    dragContext = null;
  };
  const pointerDown = (event: PointerEvent): void => {
    if (event.button !== 0 || !isTauriRuntime()) return;
    const window = getCurrentWindow();
    const activePointerId = event.pointerId;
    pointerId = event.pointerId;
    startX = event.screenX;
    startY = event.screenY;
    dragging = false;
    dragContext = Promise.all([
      window.outerPosition(),
      window.outerSize(),
      window.scaleFactor(),
      currentMonitor(),
    ]).then(([position, size, scaleFactor, monitor]) => {
      if (pointerId !== activePointerId || !monitor) return null;
      const logicalPosition = position.toLogical(scaleFactor);
      const logicalSize = size.toLogical(scaleFactor);
      const monitorPosition = monitor.position.toLogical(monitor.scaleFactor);
      const monitorSize = monitor.size.toLogical(monitor.scaleFactor);
      return {
        pointerStart: { x: startX, y: startY },
        windowStart: { x: logicalPosition.x, y: logicalPosition.y },
        windowSize: { width: logicalSize.width, height: logicalSize.height },
        monitor: {
          x: monitorPosition.x,
          y: monitorPosition.y,
          width: monitorSize.width,
          height: monitorSize.height,
        },
      };
    }).catch(() => null);
    element.setPointerCapture?.(event.pointerId);
  };
  const pointerMove = (event: PointerEvent): void => {
    if (pointerId !== event.pointerId || (event.buttons & 1) === 0) return;
    const pointer = { x: event.screenX, y: event.screenY };
    if (!dragging && Math.hypot(pointer.x - startX, pointer.y - startY) < DRAG_THRESHOLD_PX) return;
    if (!dragging) {
      dragging = true;
      onDragStart?.();
    }
    event.preventDefault();
    const activePointerId = event.pointerId;
    const context = dragContext;
    if (!context) return;
    void context.then((snapshot) => {
      if (!snapshot || pointerId !== activePointerId) return;
      const desired = {
        x: snapshot.windowStart.x + pointer.x - snapshot.pointerStart.x,
        y: snapshot.windowStart.y + pointer.y - snapshot.pointerStart.y,
      };
      queuePosition(getPartiallyVisibleWindowPosition(
        desired,
        snapshot.windowSize,
        snapshot.monitor,
        MINIMUM_VISIBLE_PX,
      ));
    });
  };

  element.addEventListener("pointerdown", pointerDown);
  element.addEventListener("pointermove", pointerMove);
  element.addEventListener("pointerup", reset);
  element.addEventListener("pointercancel", reset);
  element.addEventListener("lostpointercapture", reset);
  return () => {
    element.removeEventListener("pointerdown", pointerDown);
    element.removeEventListener("pointermove", pointerMove);
    element.removeEventListener("pointerup", reset);
    element.removeEventListener("pointercancel", reset);
    element.removeEventListener("lostpointercapture", reset);
  };
}

export function installWindowDragRegions(root: ParentNode = document): () => void {
  const disposers = Array.from(root.querySelectorAll<HTMLElement>(".window-drag-region")).map((region) => {
    const pointerDown = (event: PointerEvent): void => {
      if (event.button !== 0 || !isTauriRuntime()) return;
      if ((event.target as HTMLElement).closest(INTERACTIVE_SELECTOR)) return;
      event.preventDefault();
      void getCurrentWindow().startDragging();
    };
    region.addEventListener("pointerdown", pointerDown);
    return () => region.removeEventListener("pointerdown", pointerDown);
  });
  return () => disposers.forEach((dispose) => dispose());
}
