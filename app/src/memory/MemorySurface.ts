import { MemoryClientError, type MemoryClient } from "./MemoryClient.ts";
import {
  beginMemoryAction,
  createMemorySurfaceState,
  deletionProgressPercent,
  describeSourceHandling,
  memoryActionKey,
  reduceMemorySurfaceState,
  selectedMemorySource,
  type MemorySurfaceState,
  type MemoryTab,
} from "./memoryState.ts";
import type {
  DeletionScope,
  MemorySource,
  SharedMemoryView,
  SourceHandling,
} from "./memoryTypes.ts";
import type { DrawerManager } from "../panels/DrawerManager.ts";

export type MemorySurface = Readonly<{
  root: HTMLElement;
  open(trigger?: HTMLElement | null): void;
  refresh(): Promise<void>;
  snapshot(): MemorySurfaceState;
  dispose(): void;
}>;

type MemorySurfaceOptions = Readonly<{
  pollDelayMs?: number;
}>;

function formatUtc(value: string | null): string {
  if (!value) return "尚无记录";
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date)
    : "时间不可用";
}

function visibleStatus(value: string): string {
  const labels: Record<string, string> = {
    active: "有效",
    candidate: "候选",
    quarantined: "已隔离",
    superseded: "已纠正",
    proposed: "待确认",
    confirmed: "已确认",
    rejected: "已拒绝",
    revoked: "已撤销",
    deletion_fenced: "正在遗忘",
  };
  return labels[value] ?? "状态未知";
}

function visibleDeletionState(value: string): string {
  const labels: Record<string, string> = {
    accepted: "请求已接受",
    fenced: "已停止召回",
    source_pending: "正在处理来源",
    source_retained: "来源已保留",
    primary_rows_deleted: "主体记录已清理",
    derivations_deleted: "派生记忆已清理",
    fts_deleted: "搜索索引已清理",
    caches_invalidated: "缓存已清理",
    prompt_invalidated: "对话引用已清理",
    verified: "已完成复核",
  };
  return labels[value] ?? "正在处理";
}

function uiError(error: unknown): string {
  if (error instanceof MemoryClientError) return error.message;
  if (error instanceof Error && error.message.length <= 300) return error.message;
  return "记忆操作未完成，请稍后重试";
}

function contentFingerprint(value: string): string {
  let hash = 2_166_136_261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16_777_619);
  }
  return (hash >>> 0).toString(36);
}

function createIdempotencyKey(): string {
  const randomUuid = globalThis.crypto?.randomUUID?.();
  if (randomUuid) return `app-memory-${randomUuid}`;
  const bytes = new Uint8Array(16);
  globalThis.crypto?.getRandomValues?.(bytes);
  const suffix = bytes.some(Boolean)
    ? Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `app-memory-${suffix}`;
}

function makeButton(label: string, className: string, action: () => void): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

export function createMemorySurface(
  client: MemoryClient,
  manager: DrawerManager,
  options: MemorySurfaceOptions = {},
): MemorySurface {
  const drawer = document.createElement("aside");
  drawer.className = "app-drawer drawer-right memory-drawer";
  drawer.setAttribute("aria-label", "记忆管理");
  drawer.innerHTML = `
    <header class="drawer-header">
      <div><small>MEMORY</small><h2>记忆管理</h2></div>
      <button class="icon-button drawer-close" type="button" aria-label="关闭">×</button>
    </header>
    <nav class="drawer-tabs memory-tabs" aria-label="记忆管理分类" role="tablist">
      <button id="memory-tab-sources" type="button" role="tab" aria-controls="memory-pane-sources" data-memory-tab="sources">来源</button>
      <button id="memory-tab-shared" type="button" role="tab" aria-controls="memory-pane-shared" data-memory-tab="shared">共同记忆</button>
      <button id="memory-tab-manage" type="button" role="tab" aria-controls="memory-pane-manage" data-memory-tab="manage">纠正与遗忘</button>
      <button id="memory-tab-legacy" type="button" role="tab" aria-controls="memory-pane-legacy" data-memory-tab="legacy">旧归档</button>
    </nav>
    <div class="memory-banner" role="status" aria-live="polite"></div>
    <section id="memory-pane-sources" class="drawer-pane memory-pane" role="tabpanel" aria-labelledby="memory-tab-sources" data-memory-pane="sources">
      <div class="memory-pane-heading"><h3>经历与日记</h3><button type="button" class="icon-button memory-refresh" aria-label="刷新记忆" title="刷新">↻</button></div>
      <div class="memory-source-list" aria-live="polite"></div>
      <section class="memory-source-detail" aria-label="来源详情"></section>
    </section>
    <section id="memory-pane-shared" class="drawer-pane memory-pane" role="tabpanel" aria-labelledby="memory-tab-shared" data-memory-pane="shared">
      <form class="memory-proposal-form">
        <h3>提出共同记忆</h3>
        <label>来源经历<select name="source_episode" required></select></label>
        <label>共同记忆内容<textarea name="proposed_text" rows="4" maxlength="8000" required></textarea></label>
        <button type="submit" class="text-command">提交待确认内容</button>
      </form>
      <div class="memory-shared-list" aria-live="polite"></div>
    </section>
    <section id="memory-pane-manage" class="drawer-pane memory-pane" role="tabpanel" aria-labelledby="memory-tab-manage" data-memory-pane="manage">
      <div class="memory-selected-target"></div>
      <form class="memory-correction-form">
        <h3>纠正记忆</h3>
        <label>正确内容<textarea name="corrected_text" rows="4" maxlength="32768" required></textarea></label>
        <button type="submit" class="text-command">提交纠正</button>
      </form>
      <form class="memory-forget-form">
        <h3>遗忘</h3>
        <label>范围<select name="deletion_scope"><option value="selected">当前所选记忆</option><option value="session_derived">当前会话形成的记忆</option><option value="time_range">指定时间范围</option><option value="all_memory">全部记忆</option></select></label>
        <div class="memory-time-range" hidden>
          <label>开始时间<input type="datetime-local" name="range_started_at"></label>
          <label>结束时间<input type="datetime-local" name="range_ended_at"></label>
        </div>
        <fieldset>
          <legend>来源处理方式</legend>
          <label><input type="radio" name="source_handling" value="derived_only" checked> 仅删除派生记忆</label>
          <label><input type="radio" name="source_handling" value="source_and_derived"> 删除来源及派生记忆</label>
        </fieldset>
        <p class="memory-source-handling-copy"></p>
        <button type="submit" class="text-command danger-command">检查删除范围</button>
      </form>
      <div class="memory-deletion-progress" aria-live="polite"></div>
    </section>
    <section id="memory-pane-legacy" class="drawer-pane memory-pane" role="tabpanel" aria-labelledby="memory-tab-legacy" data-memory-pane="legacy">
      <div class="memory-legacy-status"></div>
    </section>
    <div class="memory-delete-confirm" role="alertdialog" aria-modal="true" aria-label="确认遗忘" hidden>
      <strong></strong><p></p><div><button type="button" data-delete-confirm="yes" class="danger-command">确认遗忘</button><button type="button" data-delete-confirm="no">取消</button></div>
    </div>`;
  manager.register("memory", drawer);

  const pollDelayMs = Math.max(250, options.pollDelayMs ?? 900);
  const banner = drawer.querySelector<HTMLElement>(".memory-banner")!;
  const sourceList = drawer.querySelector<HTMLElement>(".memory-source-list")!;
  const sourceDetail = drawer.querySelector<HTMLElement>(".memory-source-detail")!;
  const sharedList = drawer.querySelector<HTMLElement>(".memory-shared-list")!;
  const selectedTarget = drawer.querySelector<HTMLElement>(".memory-selected-target")!;
  const legacyStatus = drawer.querySelector<HTMLElement>(".memory-legacy-status")!;
  const deletionProgress = drawer.querySelector<HTMLElement>(".memory-deletion-progress")!;
  const proposalForm = drawer.querySelector<HTMLFormElement>(".memory-proposal-form")!;
  const correctionForm = drawer.querySelector<HTMLFormElement>(".memory-correction-form")!;
  const forgetForm = drawer.querySelector<HTMLFormElement>(".memory-forget-form")!;
  const deleteConfirm = drawer.querySelector<HTMLElement>(".memory-delete-confirm")!;
  const commandKeys = new Map<string, string>();
  let state = createMemorySurfaceState();
  let loadController: AbortController | null = null;
  let pollTimer: ReturnType<typeof globalThis.setTimeout> | null = null;
  let pendingDelete: (() => Promise<void>) | null = null;
  let deleteConfirmReturnFocus: HTMLElement | null = null;
  let disposed = false;

  function commandKey(actionKey: string): string {
    const existing = commandKeys.get(actionKey);
    if (existing) return existing;
    const created = createIdempotencyKey();
    commandKeys.set(actionKey, created);
    return created;
  }

  function dispatch(event: Parameters<typeof reduceMemorySurfaceState>[1]): void {
    state = reduceMemorySurfaceState(state, event);
    render();
  }

  function selectSource(sourceId: string, targetTab: MemoryTab = "sources"): void {
    dispatch({ type: "source.selected", sourceId });
    if (targetTab !== state.tab) dispatch({ type: "tab.selected", tab: targetTab });
  }

  function selectTab(tab: MemoryTab, focus = false): void {
    dispatch({ type: "tab.selected", tab });
    if (focus) drawer.querySelector<HTMLButtonElement>(`[data-memory-tab="${tab}"]`)?.focus();
  }

  async function runOnce(
    actionKey: string,
    action: () => Promise<void>,
    successNotice: string,
  ): Promise<void> {
    const beginning = beginMemoryAction(state, actionKey);
    if (!beginning.accepted) return;
    state = beginning.state;
    render();
    try {
      await action();
      dispatch({ type: "action.finished", key: actionKey, notice: successNotice });
    } catch (error) {
      dispatch({ type: "action.finished", key: actionKey, error: uiError(error) });
      if (error instanceof MemoryClientError && error.status === 409) await refresh();
    }
  }

  function renderSourceRow(item: MemorySource): HTMLElement {
    const row = makeButton("", "memory-source-row", () => selectSource(item.id));
    row.dataset.selected = String(item.id === state.selectedSourceId);
    row.setAttribute("aria-pressed", String(item.id === state.selectedSourceId));
    const heading = document.createElement("span");
    heading.className = "memory-source-row-heading";
    heading.textContent = item.title;
    const summary = document.createElement("span");
    summary.className = "memory-source-row-summary";
    summary.textContent = item.summary;
    const meta = document.createElement("small");
    meta.textContent = `${item.kind === "experience_episode" ? "经历" : "日记"} · ${formatUtc(item.endedAtUtc)} · ${visibleStatus(item.status)}`;
    row.append(heading, summary, meta);
    return row;
  }

  function renderSources(): void {
    const allSources = [...state.episodes, ...state.journal];
    sourceList.replaceChildren(...allSources.map(renderSourceRow));
    if (!allSources.length) {
      const empty = document.createElement("p");
      empty.className = "memory-empty";
      empty.textContent = state.loadState === "loading" ? "正在读取来源" : "目前没有可查看的记忆来源";
      sourceList.replaceChildren(empty);
    }
    const selected = selectedMemorySource(state);
    sourceDetail.replaceChildren();
    if (!selected) return;
    const heading = document.createElement("h3");
    heading.textContent = selected.title;
    const body = document.createElement("p");
    body.textContent = selected.summary;
    const detail = document.createElement("p");
    detail.className = "memory-detail-secondary";
    detail.textContent = selected.detail || "没有额外结果说明";
    const provenance = document.createElement("small");
    provenance.textContent = `来源证据 ${selected.evidenceCount} 项 · ${formatUtc(selected.startedAtUtc)} 至 ${formatUtc(selected.endedAtUtc)}`;
    const actions = document.createElement("div");
    actions.className = "memory-inline-actions";
    if (selected.kind === "experience_episode") {
      actions.append(makeButton("用于共同记忆", "text-command", () => {
        const select = proposalForm.elements.namedItem("source_episode") as HTMLSelectElement;
        select.value = selected.id;
        selectTab("shared");
      }));
    }
    actions.append(makeButton("纠正或遗忘", "text-command", () => {
      selectTab("manage");
    }));
    sourceDetail.append(heading, body, detail, provenance, actions);
  }

  function sharedAction(
    item: SharedMemoryView,
    action: "confirm" | "reject" | "revoke",
  ): void {
    const revision = action === "revoke" ? item.revision : item.proposalRevision;
    const key = memoryActionKey(`shared.${action}`, item.id, revision);
    const call = action === "confirm"
      ? client.confirmShared.bind(client)
      : action === "reject"
        ? client.rejectShared.bind(client)
        : client.revokeShared.bind(client);
    void runOnce(key, async () => {
      await call({
        sharedMemoryId: item.id,
        revision,
        idempotencyKey: commandKey(key),
      });
      await refresh();
    }, action === "confirm" ? "共同记忆已确认" : action === "reject" ? "共同记忆已拒绝" : "共同记忆已撤销");
  }

  function renderShared(): void {
    const select = proposalForm.elements.namedItem("source_episode") as HTMLSelectElement;
    const previous = select.value;
    select.replaceChildren(...state.episodes.map((episode) => {
      const option = document.createElement("option");
      option.value = episode.id;
      option.textContent = `${episode.title} · ${formatUtc(episode.endedAtUtc)}`;
      return option;
    }));
    if (state.episodes.some((episode) => episode.id === previous)) select.value = previous;
    select.disabled = state.episodes.length === 0;
    proposalForm.querySelector<HTMLButtonElement>("button[type=submit]")!.disabled = state.episodes.length === 0;

    const rows = state.sharedMemories.map((item) => {
      const row = document.createElement("article");
      row.className = "memory-shared-row";
      const meta = document.createElement("small");
      meta.textContent = `${visibleStatus(item.status)} · 更新于 ${formatUtc(item.updatedAtUtc)}`;
      const text = document.createElement("p");
      text.textContent = item.text;
      const source = document.createElement("span");
      source.className = "memory-detail-secondary";
      source.textContent = `${item.sourceEpisodeIds.length} 条经历来源`;
      const actions = document.createElement("div");
      actions.className = "memory-inline-actions";
      if (item.status === "proposed") {
        actions.append(
          makeButton("确认", "text-command", () => sharedAction(item, "confirm")),
          makeButton("拒绝", "text-command", () => sharedAction(item, "reject")),
        );
      } else if (item.status === "confirmed") {
        actions.append(makeButton("撤销共同记忆", "text-command danger-command", () => sharedAction(item, "revoke")));
      }
      actions.querySelectorAll("button").forEach((button) => {
        const actionName = button.textContent === "确认" ? "confirm" : button.textContent === "拒绝" ? "reject" : "revoke";
        const revision = actionName === "revoke" ? item.revision : item.proposalRevision;
        button.toggleAttribute("disabled", state.pendingActions.includes(memoryActionKey(`shared.${actionName}`, item.id, revision)));
      });
      row.append(meta, text, source, actions);
      return row;
    });
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "memory-empty";
      empty.textContent = "目前没有共同记忆提案";
      rows.push(empty);
    }
    sharedList.replaceChildren(...rows);
  }

  function renderManage(): void {
    const selected = selectedMemorySource(state);
    selectedTarget.replaceChildren();
    const targetTitle = document.createElement("strong");
    targetTitle.textContent = selected ? selected.title : "尚未选择记忆";
    const targetDetail = document.createElement("p");
    targetDetail.textContent = selected
      ? `将操作这条${selected.kind === "experience_episode" ? "经历" : "日记"}，当前版本 ${selected.revision}`
      : "先在“来源”页选择一条经历或日记。";
    selectedTarget.append(targetTitle, targetDetail);
    correctionForm.querySelector<HTMLButtonElement>("button[type=submit]")!.disabled = !selected;

    const handling = new FormData(forgetForm).get("source_handling") === "source_and_derived"
      ? "source_and_derived"
      : "derived_only";
    const handlingCopy = describeSourceHandling(handling);
    const copy = forgetForm.querySelector<HTMLElement>(".memory-source-handling-copy")!;
    copy.textContent = handlingCopy.detail;
    copy.dataset.destructive = String(handlingCopy.destructive);
    const scope = (forgetForm.elements.namedItem("deletion_scope") as HTMLSelectElement).value;
    const timeRange = forgetForm.querySelector<HTMLElement>(".memory-time-range")!;
    const timeInputs = Array.from(timeRange.querySelectorAll<HTMLInputElement>("input"));
    timeRange.hidden = scope !== "time_range";
    timeInputs.forEach((input) => { input.required = scope === "time_range"; });
    forgetForm.querySelector<HTMLButtonElement>("button[type=submit]")!.disabled = scope === "selected" && !selected;

    deletionProgress.replaceChildren();
    if (state.deletion) {
      const label = document.createElement("strong");
      const percent = deletionProgressPercent(state.deletion);
      label.textContent = `${visibleDeletionState(state.deletion.state)} · ${percent}%`;
      const progress = document.createElement("progress");
      progress.max = 100;
      progress.value = percent;
      const detail = document.createElement("p");
      detail.textContent = describeSourceHandling(state.deletion.sourceHandling).detail;
      deletionProgress.append(label, progress, detail);
    }
  }

  function renderLegacy(): void {
    legacyStatus.replaceChildren();
    const heading = document.createElement("h3");
    const detail = document.createElement("p");
    const counts = document.createElement("dl");
    if (!state.legacy) {
      heading.textContent = "旧归档状态不可用";
      detail.textContent = "尚未取得旧归档隔离状态。";
      legacyStatus.append(heading, detail);
      return;
    }
    const labels: Record<string, string> = {
      absent: "未发现旧记忆",
      read_only: "旧记忆只读",
      quarantined: "旧记忆已隔离",
      migration_pending: "等待迁移审阅",
      degraded: "旧归档检查异常",
    };
    heading.textContent = labels[state.legacy.state] ?? "旧归档状态未知";
    detail.textContent = state.legacy.detail || "旧归档保持只读，并与当前记忆分开。";
    [["隔离项", state.legacy.quarantineCount], ["候选项", state.legacy.candidateCount]].forEach(([label, value]) => {
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = String(label);
      description.textContent = String(value);
      counts.append(term, description);
    });
    const scan = document.createElement("small");
    scan.textContent = `最近检查：${formatUtc(state.legacy.lastScanAtUtc)}`;
    legacyStatus.append(heading, detail, counts, scan);
  }

  function render(): void {
    drawer.querySelectorAll<HTMLButtonElement>("[data-memory-tab]").forEach((button) => {
      const active = button.dataset.memoryTab === state.tab;
      button.dataset.active = String(active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    drawer.querySelectorAll<HTMLElement>("[data-memory-pane]").forEach((pane) => {
      const active = pane.dataset.memoryPane === state.tab;
      pane.dataset.active = String(active);
      pane.hidden = !active;
    });
    banner.dataset.state = state.error ? "error" : state.notice ? "notice" : state.loadState;
    banner.textContent = state.error || state.notice || (state.loadState === "loading" ? "正在同步本地记忆" : "");
    renderSources();
    renderShared();
    renderManage();
    renderLegacy();
  }

  async function refresh(): Promise<void> {
    if (disposed) return;
    loadController?.abort();
    loadController = new AbortController();
    const signal = loadController.signal;
    dispatch({ type: "load.started" });
    try {
      const [episodes, journal, status] = await Promise.all([
        client.listEpisodes(signal),
        client.listJournal(signal),
        client.status(signal),
      ]);
      if (signal.aborted || disposed) return;
      dispatch({
        type: "load.succeeded",
        episodes: episodes.items,
        journal: journal.items,
        status,
      });
    } catch (error) {
      if (signal.aborted || disposed) return;
      dispatch({ type: "load.failed", message: uiError(error) });
    }
  }

  function pollDeletion(deletionRequestId: string): void {
    if (pollTimer !== null) globalThis.clearTimeout(pollTimer);
    const poll = async (): Promise<void> => {
      try {
        const progress = await client.deletion(deletionRequestId);
        dispatch({ type: "deletion.updated", deletion: progress });
        if (progress.state === "verified") {
          await refresh();
          return;
        }
      } catch (error) {
        dispatch({
          type: "action.finished",
          key: `deletion.poll:${deletionRequestId}`,
          error: uiError(error),
        });
      }
      if (!disposed) pollTimer = globalThis.setTimeout(() => void poll(), pollDelayMs);
    };
    void poll();
  }

  proposalForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const sourceId = (proposalForm.elements.namedItem("source_episode") as HTMLSelectElement).value;
    const proposedText = (proposalForm.elements.namedItem("proposed_text") as HTMLTextAreaElement).value.trim();
    if (!sourceId || !proposedText) return;
    const key = memoryActionKey(`shared.propose.${contentFingerprint(proposedText)}`, sourceId, 1);
    void runOnce(key, async () => {
      await client.proposeShared({
        sourceEpisodeIds: [sourceId],
        proposedText,
        idempotencyKey: commandKey(key),
      });
      (proposalForm.elements.namedItem("proposed_text") as HTMLTextAreaElement).value = "";
      await refresh();
    }, "共同记忆提案已提交，确认前不会进入召回");
  });

  correctionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const source = selectedMemorySource(state);
    const correctedText = (correctionForm.elements.namedItem("corrected_text") as HTMLTextAreaElement).value.trim();
    if (!source || !correctedText) return;
    const key = memoryActionKey(`correct.${contentFingerprint(correctedText)}`, source.id, source.revision);
    void runOnce(key, async () => {
      await client.correct({
        targetKind: source.kind,
        targetId: source.id,
        expectedRevision: source.revision,
        correctedText,
        sourceEvidenceIds: source.sourceEvidenceIds,
        idempotencyKey: commandKey(key),
      });
      (correctionForm.elements.namedItem("corrected_text") as HTMLTextAreaElement).value = "";
      await refresh();
    }, "纠正已提交，旧版本会保留来源链但不再正常召回");
  });

  forgetForm.addEventListener("change", renderManage);
  forgetForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const source = selectedMemorySource(state);
    const scopeValue = (forgetForm.elements.namedItem("deletion_scope") as HTMLSelectElement).value;
    if (scopeValue === "selected" && !source) return;
    const sourceHandling: SourceHandling = new FormData(forgetForm).get("source_handling") === "source_and_derived"
      ? "source_and_derived"
      : "derived_only";
    const scope: DeletionScope = scopeValue === "selected"
      ? source!.kind === "experience_episode" ? "episode" : "item"
      : scopeValue === "session_derived" || scopeValue === "time_range" || scopeValue === "all_memory"
        ? scopeValue
        : "item";
    const targetId = scope === "item" || scope === "episode" ? source!.id : undefined;
    const targetKind = scope === "item" ? source!.kind : undefined;
    const rangeStarted = (forgetForm.elements.namedItem("range_started_at") as HTMLInputElement).value;
    const rangeEnded = (forgetForm.elements.namedItem("range_ended_at") as HTMLInputElement).value;
    const rangeStartedAtUtc = scope === "time_range" && rangeStarted ? new Date(rangeStarted).toISOString() : undefined;
    const rangeEndedAtUtc = scope === "time_range" && rangeEnded ? new Date(rangeEnded).toISOString() : undefined;
    if (scope === "time_range" && (!rangeStartedAtUtc || !rangeEndedAtUtc || rangeEndedAtUtc <= rangeStartedAtUtc)) {
      dispatch({ type: "action.finished", key: "range.validation", error: "结束时间必须晚于开始时间" });
      return;
    }
    const scopeIdentity = targetId ?? `${scope}:${rangeStartedAtUtc ?? ""}:${rangeEndedAtUtc ?? ""}`;
    const key = memoryActionKey(`forget.${scope}.${sourceHandling}`, scopeIdentity, source?.revision ?? 0);
    const copy = describeSourceHandling(sourceHandling);
    deleteConfirm.querySelector("strong")!.textContent = copy.title;
    deleteConfirm.querySelector("p")!.textContent = scope === "all_memory"
      ? `${copy.detail} 范围是此安装中的全部记忆。`
      : copy.detail;
    deleteConfirmReturnFocus = forgetForm.querySelector<HTMLButtonElement>("button[type=submit]");
    deleteConfirm.hidden = false;
    deleteConfirm.querySelector<HTMLButtonElement>("[data-delete-confirm=yes]")!.focus();
    pendingDelete = async () => {
      await runOnce(key, async () => {
        const receipt = await client.forget({
          scope,
          targetKind,
          targetId,
          sourceHandling,
          rangeStartedAtUtc,
          rangeEndedAtUtc,
          reasonCode: "user_requested",
          idempotencyKey: commandKey(key),
        });
        if (receipt.resourceId) pollDeletion(receipt.resourceId);
      }, "遗忘请求已接受，正在清理索引、缓存与提示词引用");
    };
  });

  function closeDeleteConfirm(): void {
    pendingDelete = null;
    deleteConfirm.hidden = true;
    deleteConfirmReturnFocus?.focus();
    deleteConfirmReturnFocus = null;
  }

  deleteConfirm.querySelector<HTMLButtonElement>("[data-delete-confirm=yes]")!.addEventListener("click", () => {
    const action = pendingDelete;
    pendingDelete = null;
    deleteConfirm.hidden = true;
    deleteConfirmReturnFocus?.focus();
    deleteConfirmReturnFocus = null;
    if (action) void action();
  });
  deleteConfirm.querySelector<HTMLButtonElement>("[data-delete-confirm=no]")!.addEventListener("click", () => {
    closeDeleteConfirm();
  });
  deleteConfirm.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeDeleteConfirm();
      return;
    }
    if (event.key !== "Tab") return;
    const buttons = Array.from(deleteConfirm.querySelectorAll<HTMLButtonElement>("button:not([disabled])"));
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
    const next = event.shiftKey
      ? index <= 0 ? buttons.length - 1 : index - 1
      : index < 0 || index === buttons.length - 1 ? 0 : index + 1;
    if (index < 0 || next === 0 || next === buttons.length - 1) {
      event.preventDefault();
      buttons[next]?.focus();
    }
  });
  drawer.querySelectorAll<HTMLButtonElement>("[data-memory-tab]").forEach((button) => {
    button.addEventListener("click", () => selectTab(button.dataset.memoryTab as MemoryTab));
    button.addEventListener("keydown", (event) => {
      const tabs: MemoryTab[] = ["sources", "shared", "manage", "legacy"];
      const current = tabs.indexOf(button.dataset.memoryTab as MemoryTab);
      let next = current;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % tabs.length;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      selectTab(tabs[next], true);
    });
  });
  drawer.querySelector<HTMLButtonElement>(".memory-refresh")!.addEventListener("click", () => void refresh());
  render();

  return Object.freeze({
    root: drawer,
    open(trigger = null) {
      manager.open("memory", trigger);
      void refresh();
    },
    refresh,
    snapshot: () => state,
    dispose() {
      disposed = true;
      loadController?.abort();
      if (pollTimer !== null) globalThis.clearTimeout(pollTimer);
      pollTimer = null;
    },
  });
}
