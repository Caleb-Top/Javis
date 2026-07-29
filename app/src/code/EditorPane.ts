import type { BackendClient } from "../bridge/backendClient";
import type { OpenedWorkspaceFile } from "./FileExplorer";

export type EditorPane = {
  open(file: OpenedWorkspaceFile): void;
  save(): Promise<boolean>;
  isDirty(): boolean;
};

export function createEditorPane(client: BackendClient, root: HTMLElement): EditorPane {
  const title = root.querySelector<HTMLElement>(".editor-title")!;
  const state = root.querySelector<HTMLElement>(".editor-state")!;
  const editor = root.querySelector<HTMLTextAreaElement>(".editor-text")!;
  const metadata = root.querySelector<HTMLElement>(".binary-metadata")!;
  const saveButton = root.querySelector<HTMLButtonElement>(".editor-save")!;
  let active: OpenedWorkspaceFile | null = null;
  let dirty = false;

  function setDirty(value: boolean): void {
    dirty = value;
    root.dataset.dirty = String(value);
    state.textContent = value ? "未保存" : "已保存";
    saveButton.disabled = !value || Boolean(active?.binary);
  }

  function open(file: OpenedWorkspaceFile): void {
    if (dirty && !window.confirm("当前文件尚未保存，放弃修改并打开新文件？")) return;
    active = file;
    title.textContent = file.path;
    root.dataset.conflict = "false";
    if (file.binary) {
      editor.hidden = true;
      metadata.hidden = false;
      metadata.textContent = `${file.name} · 二进制文件 · ${file.size || 0} bytes`;
    } else {
      metadata.hidden = true;
      editor.hidden = false;
      editor.value = file.content || "";
    }
    setDirty(false);
  }

  async function save(): Promise<boolean> {
    if (!active || active.binary || !dirty) return false;
    state.textContent = "保存中";
    try {
      const response = await client.post<{ ok: boolean; conflict?: boolean; modified?: number; error?: string }>("/api/workspace/save", {
        path: active.path,
        content: editor.value,
        expected_modified: active.modified,
      });
      if (!response.ok) {
        if (response.conflict) {
          root.dataset.conflict = "true";
          state.textContent = "外部修改冲突";
          return false;
        }
        throw new Error(response.error || "保存失败");
      }
      active.modified = response.modified;
      root.dataset.conflict = "false";
      setDirty(false);
      return true;
    } catch (error) {
      state.textContent = error instanceof Error ? `保存失败: ${error.message}` : "保存失败";
      return false;
    }
  }

  editor.addEventListener("input", () => setDirty(true));
  saveButton.addEventListener("click", () => void save());
  return { open, save, isDirty: () => dirty };
}
