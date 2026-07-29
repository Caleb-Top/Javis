import type { BackendClient } from "../bridge/backendClient";

type CommandTask = { task_id?: string; command?: string; status?: string; risk?: string; output?: string; exit_code?: number };

export type TaskStream = { refresh(): Promise<void> };

export function createTaskStream(client: BackendClient, root: HTMLElement): TaskStream {
  async function refresh(): Promise<void> {
    root.dataset.state = "loading";
    try {
      const response = await client.get<{ ok: boolean; tasks: CommandTask[] }>("/api/control/commands?limit=30");
      const tasks = response.tasks || [];
      root.replaceChildren(...tasks.slice().reverse().map((task) => {
        const item = document.createElement("article");
        item.className = "task-row";
        const head = document.createElement("strong");
        head.textContent = task.command || task.task_id || "命令任务";
        const meta = document.createElement("small");
        meta.textContent = `${task.status || "unknown"} · ${task.risk || "safe"} · exit ${task.exit_code ?? "-"}`;
        const output = document.createElement("pre");
        output.textContent = task.output || "等待输出";
        item.append(head, meta, output);
        return item;
      }));
      root.dataset.state = tasks.length ? "ready" : "empty";
      if (!tasks.length) root.textContent = "暂无受审计任务";
    } catch (error) {
      root.dataset.state = "error";
      root.textContent = error instanceof Error ? error.message : "任务读取失败";
    }
  }
  return { refresh };
}
