import { readPreference, writePreference } from "../app/AppPreferences";
import type { RuntimeSnapshot } from "../state/runtimeStateTypes";

export type StatusRail = {
  update(snapshot: RuntimeSnapshot): void;
  setConnection(online: boolean): void;
  setDetails(details: Record<string, string | number>): void;
};

export function createStatusRail(root: HTMLElement): StatusRail {
  const toggle = root.querySelector<HTMLButtonElement>(".status-toggle")!;
  const detail = root.querySelector<HTMLElement>(".status-detail")!;
  const stateLabel = root.querySelector<HTMLElement>(".status-state")!;
  const connection = root.querySelector<HTMLElement>(".status-connection")!;
  let expanded = readPreference("statusRailExpanded", false);

  function applyExpanded(): void {
    root.dataset.expanded = String(expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.title = expanded ? "收起状态栏" : "展开状态栏";
  }

  toggle.addEventListener("click", () => {
    expanded = !expanded;
    writePreference("statusRailExpanded", expanded);
    applyExpanded();
  });
  applyExpanded();

  return {
    update(snapshot) {
      stateLabel.textContent = snapshot.detail;
      root.dataset.state = snapshot.state;
    },
    setConnection(online) {
      connection.textContent = online ? "后端在线" : "后端离线";
      connection.dataset.online = String(online);
    },
    setDetails(details) {
      detail.innerHTML = Object.entries(details)
        .map(([label, value]) => `<span><small>${label}</small><strong>${String(value)}</strong></span>`)
        .join("");
    },
  };
}
