import type { BackendClient } from "../bridge/backendClient";

type CommandTask = { task_id: string; status: string; output?: string; exit_code?: number; risk?: string };
const TERMINAL_STATES = new Set(["completed", "failed", "blocked", "cancelled"]);

export type TerminalPane = {
  run(): Promise<void>;
  cancel(): Promise<void>;
  dispose(): void;
};

export function createTerminalPane(
  client: BackendClient,
  root: HTMLElement,
  getRootToken: () => string,
): TerminalPane {
  const form = root.querySelector<HTMLFormElement>(".terminal-command")!;
  const command = root.querySelector<HTMLInputElement>(".terminal-input")!;
  const shell = root.querySelector<HTMLSelectElement>(".terminal-shell")!;
  const cwd = root.querySelector<HTMLInputElement>(".terminal-cwd")!;
  const output = root.querySelector<HTMLElement>(".terminal-output")!;
  const status = root.querySelector<HTMLElement>(".terminal-status")!;
  const runButton = root.querySelector<HTMLButtonElement>(".terminal-run")!;
  const stopButton = root.querySelector<HTMLButtonElement>(".terminal-stop")!;
  let activeTaskId = "";
  let pollTimer = 0;

  function render(task: CommandTask): void {
    status.textContent = `${task.status} · ${task.risk || "safe"} · exit ${task.exit_code ?? "-"}`;
    output.textContent = task.output || "等待输出";
    const terminal = TERMINAL_STATES.has(task.status);
    runButton.disabled = !terminal;
    stopButton.disabled = terminal;
  }

  async function poll(): Promise<void> {
    if (!activeTaskId) return;
    try {
      const response = await client.get<{ ok: boolean; task: CommandTask; error?: string }>(`/api/control/commands/${encodeURIComponent(activeTaskId)}`);
      if (!response.ok) throw new Error(response.error || "任务读取失败");
      render(response.task);
      if (TERMINAL_STATES.has(response.task.status)) {
        activeTaskId = "";
        return;
      }
      pollTimer = window.setTimeout(() => void poll(), 500);
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : "任务读取失败";
      pollTimer = window.setTimeout(() => void poll(), 1500);
    }
  }

  async function run(): Promise<void> {
    const clean = command.value.trim();
    if (!clean || activeTaskId) return;
    runButton.disabled = true;
    stopButton.disabled = false;
    status.textContent = "正在创建受审计任务";
    try {
      const response = await client.post<{ ok: boolean; task?: CommandTask; error?: string }>("/api/control/commands/start", {
        command: clean,
        shell: shell.value,
        cwd: cwd.value.trim() || ".",
        timeout: 60,
        root_token: getRootToken(),
      });
      if (!response.ok || !response.task) throw new Error(response.error || "任务创建失败");
      activeTaskId = response.task.task_id;
      render(response.task);
      void poll();
    } catch (error) {
      runButton.disabled = false;
      stopButton.disabled = true;
      status.textContent = error instanceof Error ? error.message : "任务创建失败";
    }
  }

  async function cancel(): Promise<void> {
    if (!activeTaskId) return;
    stopButton.disabled = true;
    try {
      await client.post(`/api/control/commands/${encodeURIComponent(activeTaskId)}/cancel`, {});
      status.textContent = "正在停止进程树";
      void poll();
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : "停止失败";
    }
  }

  form.addEventListener("submit", (event) => { event.preventDefault(); void run(); });
  stopButton.addEventListener("click", () => void cancel());
  return { run, cancel, dispose: () => window.clearTimeout(pollTimer) };
}
