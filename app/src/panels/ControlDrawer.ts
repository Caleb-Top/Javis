import { invoke } from "@tauri-apps/api/core";
import type { BackendClient, BackendEvent } from "../bridge/backendClient";
import { runtimeStateCoordinator } from "../state/RuntimeStateCoordinator";
import type { DrawerManager } from "./DrawerManager";
import { createTaskStream } from "./TaskStream";

type ControlTab = "status" | "tasks" | "permission" | "perception" | "settings";

export type ControlDrawer = {
  open(tab?: ControlTab, trigger?: HTMLElement | null): void;
  handleEvent(event: BackendEvent): void;
  rootToken(): string;
};

export function createControlDrawer(client: BackendClient, manager: DrawerManager): ControlDrawer {
  const drawer = document.createElement("aside");
  drawer.className = "app-drawer drawer-right control-drawer";
  drawer.setAttribute("aria-label", "状态与控制");
  drawer.innerHTML = `
    <header class="drawer-header"><div><small>CONTROL</small><h2>状态与控制</h2></div><button class="icon-button drawer-close" aria-label="关闭">×</button></header>
    <nav class="drawer-tabs" aria-label="控制分类">
      <button data-tab="status">状态</button><button data-tab="tasks">任务</button><button data-tab="permission">权限</button><button data-tab="perception">感知</button><button data-tab="settings">设置</button>
    </nav>
    <div class="pending-action-confirm" hidden role="alertdialog" aria-label="确认本地操作">
      <strong></strong><p></p>
      <div><button data-action-confirm="accept">确认执行</button><button data-action-confirm="cancel">取消</button></div>
    </div>
    <div class="drawer-pane" data-pane="status"><div class="runtime-grid"></div><button class="text-command refresh-status">刷新状态</button></div>
    <div class="drawer-pane" data-pane="tasks"><div class="task-stream"></div></div>
    <div class="drawer-pane" data-pane="permission">
      <div class="permission-modes"><button data-mode="safe">SAFE</button><button data-mode="trusted">TRUSTED</button><button data-mode="root">ROOT</button></div>
      <p class="permission-note">ROOT 只创建有时限的本地会话令牌，不会静默永久开启。</p>
      <div class="confirm-request" hidden><strong>等待授权</strong><p></p><button data-confirm="once">仅本次允许</button><button data-confirm="session">本会话允许</button><button data-confirm="deny">拒绝</button></div>
    </div>
    <div class="drawer-pane" data-pane="perception">
      <p>仅分析当前屏幕，不保存原始截图。结果只保留 OCR、对象、摘要和必要元数据。</p>
      <button class="text-command screen-analyze">分析当前屏幕</button><output class="perception-output">尚未采集</output>
    </div>`;
  manager.register("control", drawer);
  const taskStream = createTaskStream(client, drawer.querySelector<HTMLElement>(".task-stream")!);
  let activeTab: ControlTab = "status";
  let pendingConfirm = false;
  let pendingApprovalId = "";
  let activeRootToken = "";
  let pendingAction: null | { run: () => Promise<void> } = null;

  function selectTab(tab: ControlTab): void {
    if (tab === "settings") {
      manager.close();
      document.dispatchEvent(new CustomEvent("javis:open-settings"));
      return;
    }
    activeTab = tab;
    drawer.querySelectorAll<HTMLElement>("[data-tab]").forEach((item) => item.dataset.active = String(item.dataset.tab === tab));
    drawer.querySelectorAll<HTMLElement>("[data-pane]").forEach((item) => item.dataset.active = String(item.dataset.pane === tab));
    if (tab === "tasks") void taskStream.refresh();
    if (tab === "status") void refreshStatus();
  }

  async function refreshStatus(): Promise<void> {
    const grid = drawer.querySelector<HTMLElement>(".runtime-grid")!;
    grid.dataset.state = "loading";
    try {
      const [runtime, blueprint, permission] = await Promise.all([
        client.get<Record<string, unknown>>("/api/runtime/status"),
        client.get<Record<string, unknown>>("/api/blueprint/coverage"),
        client.get<Record<string, unknown>>("/api/config/permission"),
      ]);
      const rows: [string, unknown][] = [["Runtime", runtime.status || "online"], ["Blueprint", blueprint.score || "-"], ["权限", permission.label || permission.permission || "-"]];
      grid.replaceChildren(...rows.map(([label, value]) => {
        const row = document.createElement("div");
        const key = document.createElement("small");
        const val = document.createElement("strong");
        key.textContent = label;
        val.textContent = String(value);
        row.append(key, val);
        return row;
      }));
      grid.dataset.state = "ready";
    } catch (error) {
      grid.dataset.state = "error";
      grid.textContent = error instanceof Error ? error.message : "状态不可用";
    }
  }

  function requestActionConfirmation(
    title: string,
    message: string,
    tab: ControlTab,
    run: () => Promise<void>,
  ): void {
    pendingAction = { run };
    const card = drawer.querySelector<HTMLElement>(".pending-action-confirm")!;
    card.querySelector("strong")!.textContent = title;
    card.querySelector("p")!.textContent = message;
    card.hidden = false;
    selectTab(tab);
    manager.open("control");
  }

  async function resolveActionConfirmation(accepted: boolean): Promise<void> {
    const action = pendingAction;
    pendingAction = null;
    drawer.querySelector<HTMLElement>(".pending-action-confirm")!.hidden = true;
    if (!accepted || !action) return;
    await action.run();
  }

  async function setPermission(mode: string): Promise<void> {
    if (mode === "root") {
      requestActionConfirmation(
        "开启 ROOT 会话",
        "ROOT 允许高风险命令。确认后只创建一个 5 分钟本地令牌，并保留审计记录。",
        "permission",
        async () => {
          const result = await client.post<{ ok: boolean; token?: string; error?: string }>("/api/control/root-token", { reason: "Javis App explicit local session", ttl_sec: 300 });
          activeRootToken = result.ok && result.token ? result.token : "";
          await refreshStatus();
        },
      );
      return;
    }
    const permission = mode === "safe" ? "safe_guard" : "quick_auth";
    await client.post("/api/config/permission", { permission });
    await refreshStatus();
  }

  async function analyzeScreen(): Promise<void> {
    const output = drawer.querySelector<HTMLOutputElement>(".perception-output")!;
    output.textContent = "正在分析";
    try {
      let payload: Record<string, unknown> = { retain_media: false };
      if ("__TAURI_INTERNALS__" in window) {
        const raw = await invoke<string>("capture_screen_native");
        const capture = JSON.parse(raw) as { width: number; height: number; bgraBase64: string };
        payload = {
          ...payload,
          width: capture.width,
          height: capture.height,
          image_bgra_base64: capture.bgraBase64,
        };
      }
      const result = await client.post<Record<string, unknown>>("/api/perception/screen/analyze", payload);
      output.textContent = JSON.stringify(result, null, 2).slice(0, 2000);
    } catch (error) {
      output.textContent = error instanceof Error ? error.message : "屏幕分析失败";
    }
  }

  drawer.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach((button) => button.addEventListener("click", () => selectTab(button.dataset.tab as ControlTab)));
  drawer.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach((button) => button.addEventListener("click", () => void setPermission(button.dataset.mode || "safe")));
  drawer.querySelector<HTMLButtonElement>(".refresh-status")!.addEventListener("click", () => void refreshStatus());
  drawer.querySelector<HTMLButtonElement>(".screen-analyze")!.addEventListener("click", () => void analyzeScreen());
  drawer.querySelectorAll<HTMLButtonElement>("[data-action-confirm]").forEach((button) => button.addEventListener("click", () => {
    void resolveActionConfirmation(button.dataset.actionConfirm === "accept");
  }));
  drawer.querySelectorAll<HTMLButtonElement>("[data-confirm]").forEach((button) => button.addEventListener("click", () => {
    const confirm = button.dataset.confirm !== "deny";
    client.confirm(confirm, pendingApprovalId);
    pendingConfirm = false;
    pendingApprovalId = "";
    drawer.querySelector<HTMLElement>(".confirm-request")!.hidden = true;
    runtimeStateCoordinator.signal({ source: "permission", state: confirm ? "executing" : "idle", timestamp: Date.now(), detail: confirm ? "授权后继续执行" : "已拒绝操作" });
  }));
  selectTab("status");

  return {
    open(tab = activeTab, trigger = null) { selectTab(tab); manager.open("control", trigger); },
    handleEvent(event) {
      if (event.type !== "approval.required" && event.type !== "confirm_required") return;
      pendingConfirm = true;
      const payload = event.payload as Record<string, unknown> | undefined;
      pendingApprovalId = String(payload?.approval_id || event.approval_id || "");
      const card = drawer.querySelector<HTMLElement>(".confirm-request")!;
      card.hidden = false;
      card.querySelector("p")!.textContent = String(payload?.reason || event.detail || event.text || "该操作需要你的明确授权");
      void pendingConfirm;
      selectTab("permission");
      manager.open("control");
    },
    rootToken: () => activeRootToken,
  };
}
