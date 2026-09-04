import type { BackendClient } from "../bridge/backendClient";
import type { DrawerManager } from "./DrawerManager";

export const MIN_QUERY_LENGTH = 2;
export const SEARCH_DEBOUNCE_MS = 250;
type SearchState = "idle" | "loading" | "empty" | "error" | "ready";
type SearchResult = { type?: string; content?: string; category?: string; domain?: string; confidence?: number };

export type ConversationDrawer = { open(trigger?: HTMLElement | null): void };

export function createConversationDrawer(client: BackendClient, manager: DrawerManager): ConversationDrawer {
  const drawer = document.createElement("aside");
  drawer.className = "app-drawer drawer-left conversation-drawer";
  drawer.setAttribute("aria-label", "会话与记忆");
  drawer.innerHTML = `
    <header class="drawer-header">
      <div><small>MEMORY</small><h2>会话与记忆</h2></div>
      <div class="drawer-header-actions">
        <button class="icon-button open-memory-manager" type="button" aria-label="打开记忆管理" title="记忆管理">◫</button>
        <button class="icon-button drawer-close" type="button" aria-label="关闭">×</button>
      </div>
    </header>
    <label class="search-box"><span class="sr-only">搜索会话和记忆</span><input type="search" placeholder="搜索会话、事实和事件" autocomplete="off"><kbd>FTS</kbd></label>
    <div class="search-status" data-state="idle">输入至少 2 个字符</div>
    <div class="conversation-results" aria-live="polite"></div>`;
  manager.register("conversations", drawer);
  const input = drawer.querySelector<HTMLInputElement>("input")!;
  const status = drawer.querySelector<HTMLElement>(".search-status")!;
  const results = drawer.querySelector<HTMLElement>(".conversation-results")!;
  drawer.querySelector<HTMLButtonElement>(".open-memory-manager")!.addEventListener(
    "click",
    () => document.dispatchEvent(new CustomEvent("javis:open-memory")),
  );
  let timer = 0;
  let controller: AbortController | null = null;

  function setState(state: SearchState, message: string): void {
    status.dataset.state = state;
    status.textContent = message;
  }

  function render(items: SearchResult[]): void {
    results.replaceChildren(...items.map((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "memory-result";
      const meta = document.createElement("small");
      meta.textContent = `${item.type || "memory"} · ${item.category || item.domain || "来源可用"}`;
      const content = document.createElement("span");
      content.textContent = item.content || "无摘要";
      button.append(meta, content);
      return button;
    }));
  }

  async function search(query: string): Promise<void> {
    if (query.length < MIN_QUERY_LENGTH) {
      controller?.abort();
      results.replaceChildren();
      setState("idle", "输入至少 2 个字符");
      return;
    }
    controller?.abort();
    controller = new AbortController();
    setState("loading", "正在搜索本地索引");
    try {
      const params = new URLSearchParams({ q: query, type: "all", limit: "30" });
      const response = await client.get<{ ok: boolean; results: SearchResult[]; error?: string }>(`/api/memory/search?${params}`, controller.signal);
      if (!response.ok) throw new Error(response.error || "搜索失败");
      render(response.results || []);
      setState(response.results?.length ? "ready" : "empty", response.results?.length ? `${response.results.length} 条结果` : "没有匹配结果");
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      results.replaceChildren();
      setState("error", error instanceof Error ? error.message : "索引不可用");
    }
  }

  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    const query = input.value.trim();
    timer = window.setTimeout(() => void search(query), SEARCH_DEBOUNCE_MS);
  });

  return { open: (trigger = null) => manager.open("conversations", trigger) };
}
