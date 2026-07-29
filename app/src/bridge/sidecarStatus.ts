export type SidecarStatus =
  | "unknown"
  | "probing"
  | "starting"
  | "healthy"
  | "attached"
  | "degraded"
  | "restarting"
  | "stopping"
  | "stopped"
  | "failed"
  | "port-conflict"
  | "offline";

export function setSidecarStatus(status: SidecarStatus): void {
  document.body.dataset.sidecar = status;
}
