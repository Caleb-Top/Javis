import type { LiveState } from "./liveState";
import { setLiveState } from "./liveState";
import { createLiveOrbRenderer, type LiveOrbController } from "./LiveOrbRenderer";

export function activateLiveOrb(state: LiveState = "idle"): LiveOrbController {
  const canvas = document.querySelector<HTMLCanvasElement>("#live-orb-canvas");
  if (!canvas) throw new Error("Javis Live orb canvas is missing");
  const controller = createLiveOrbRenderer(canvas);
  controller.setState(state);
  setLiveState(state);
  return controller;
}
