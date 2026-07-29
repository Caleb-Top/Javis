export const LIVE_SURFACE_SIZE = 200;
export const LIVE_SURFACE_STATUS_HEIGHT = 18;
export const LIVE_SURFACE_MIN_SIZE = 132;
export const LIVE_SURFACE_MIN_HEIGHT = 158;
export const LIVE_ORB_RENDER_SCALE = 1.18;

export type LiveSurfaceSize = {
  visual: number;
  width: number;
  height: number;
};

export function getLiveSurfaceSize(scale: number): LiveSurfaceSize {
  const normalizedScale = Math.min(1.3, Math.max(0.7, scale));
  const visual = Math.round(LIVE_SURFACE_SIZE * normalizedScale);
  return {
    visual,
    width: visual,
    height: visual + LIVE_SURFACE_STATUS_HEIGHT,
  };
}
