import {
  formatSurfaceStatus,
  type RuntimeSnapshot,
} from "../state/runtimeStateTypes.ts";

export type SurfaceStatusController = {
  setSnapshot(snapshot: RuntimeSnapshot): void;
};

export function createSurfaceStatus(
  element: HTMLElement,
): SurfaceStatusController {
  return {
    setSnapshot(snapshot) {
      element.textContent = formatSurfaceStatus(snapshot);
      element.dataset.state = snapshot.state;
      element.title = snapshot.detail || "";
    },
  };
}
