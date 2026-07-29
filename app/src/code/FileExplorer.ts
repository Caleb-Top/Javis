import type { BackendClient } from "../bridge/backendClient";

export type WorkspaceEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number;
  modified?: number;
};

export type OpenedWorkspaceFile = WorkspaceEntry & {
  content?: string;
  binary?: boolean;
};

export type FileExplorer = {
  load(path?: string): Promise<void>;
  currentPath(): string;
};

export function createFileExplorer(
  client: BackendClient,
  root: HTMLElement,
  onOpen: (file: OpenedWorkspaceFile) => void,
): FileExplorer {
  let current = ".";
  const pathLabel = root.querySelector<HTMLElement>(".explorer-path")!;
  const list = root.querySelector<HTMLElement>(".file-list")!;

  async function openFile(entry: WorkspaceEntry): Promise<void> {
    list.dataset.state = "loading";
    try {
      const response = await client.get<{ ok: boolean; content?: string; binary?: boolean; size?: number; modified?: number; error?: string }>(
        `/api/workspace/read?${new URLSearchParams({ path: entry.path })}`,
      );
      if (!response.ok) throw new Error(response.error || "文件读取失败");
      onOpen({ ...entry, content: response.content, binary: response.binary, size: response.size ?? entry.size, modified: response.modified ?? entry.modified });
      list.dataset.state = "ready";
    } catch (error) {
      list.dataset.state = "error";
      list.title = error instanceof Error ? error.message : "文件读取失败";
    }
  }

  function render(entries: WorkspaceEntry[]): void {
    list.replaceChildren(...entries.map((entry) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "file-entry";
      button.dataset.kind = entry.is_dir ? "directory" : "file";
      button.title = entry.path;
      const icon = document.createElement("span");
      icon.textContent = entry.is_dir ? "▸" : "·";
      const name = document.createElement("span");
      name.textContent = entry.name;
      button.append(icon, name);
      button.addEventListener("click", () => entry.is_dir ? void load(entry.path) : void openFile(entry));
      return button;
    }));
  }

  async function load(path = current): Promise<void> {
    list.dataset.state = "loading";
    try {
      const response = await client.get<{ ok: boolean; path: string; entries: WorkspaceEntry[]; error?: string }>(
        `/api/workspace/explore?${new URLSearchParams({ path })}`,
      );
      if (!response.ok) throw new Error(response.error || "目录读取失败");
      current = path;
      pathLabel.textContent = path;
      render(response.entries || []);
      list.dataset.state = response.entries?.length ? "ready" : "empty";
      if (!response.entries?.length) list.textContent = "空目录";
    } catch (error) {
      list.dataset.state = "error";
      list.textContent = error instanceof Error ? error.message : "目录读取失败";
    }
  }

  root.querySelector<HTMLButtonElement>(".explorer-refresh")!.addEventListener("click", () => void load());
  root.querySelector<HTMLButtonElement>(".explorer-up")!.addEventListener("click", () => {
    const parts = current.replace(/\\/g, "/").split("/").filter(Boolean);
    parts.pop();
    void load(parts.join("/") || ".");
  });
  return { load, currentPath: () => current };
}
