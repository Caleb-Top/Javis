import type { DesktopMode } from "../desktop/windowMode";

export function getStartupDesktopMode(firstRunRequired: boolean): DesktopMode {
  return firstRunRequired ? "code" : "live";
}
