export type Rect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type Point = {
  x: number;
  y: number;
};

export type Size = {
  width: number;
  height: number;
};

export function getCenteredWindowPosition(monitor: Rect, window: Size): Point {
  return {
    x: Math.round(monitor.x + (monitor.width - window.width) / 2),
    y: Math.round(monitor.y + (monitor.height - window.height) / 2),
  };
}

export function getClampedWindowPosition(
  position: Point,
  window: Size,
  monitor: Rect,
  padding = 0,
): Point {
  const minX = monitor.x + padding;
  const minY = monitor.y + padding;
  const maxX = Math.max(minX, monitor.x + monitor.width - window.width - padding);
  const maxY = Math.max(minY, monitor.y + monitor.height - window.height - padding);
  return {
    x: Math.min(maxX, Math.max(minX, position.x)),
    y: Math.min(maxY, Math.max(minY, position.y)),
  };
}

export function getPartiallyVisibleWindowPosition(
  position: Point,
  window: Size,
  monitor: Rect,
  minimumVisible = 32,
): Point {
  const visibleWidth = Math.min(window.width, Math.max(1, minimumVisible));
  const visibleHeight = Math.min(window.height, Math.max(1, minimumVisible));
  const minX = monitor.x - window.width + visibleWidth;
  const minY = monitor.y - window.height + visibleHeight;
  const maxX = Math.max(minX, monitor.x + monitor.width - visibleWidth);
  const maxY = Math.max(minY, monitor.y + monitor.height - visibleHeight);
  return {
    x: Math.min(maxX, Math.max(minX, position.x)),
    y: Math.min(maxY, Math.max(minY, position.y)),
  };
}
