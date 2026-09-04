import type {
  DeletionProgress,
  LegacyMemoryStatus,
  MemorySource,
  MemoryStatus,
  SharedMemoryView,
  SourceHandling,
} from "./memoryTypes.ts";

export type MemoryTab = "sources" | "shared" | "manage" | "legacy";
export type MemoryLoadState = "idle" | "loading" | "ready" | "error";

export type MemorySurfaceState = Readonly<{
  revision: number;
  tab: MemoryTab;
  loadState: MemoryLoadState;
  episodes: readonly MemorySource[];
  journal: readonly MemorySource[];
  sharedMemories: readonly SharedMemoryView[];
  legacy: LegacyMemoryStatus | null;
  selectedSourceId: string | null;
  pendingActions: readonly string[];
  deletion: DeletionProgress | null;
  notice: string;
  error: string;
}>;

export type MemoryStateEvent =
  | Readonly<{ type: "tab.selected"; tab: MemoryTab }>
  | Readonly<{ type: "load.started" }>
  | Readonly<{
      type: "load.succeeded";
      episodes: readonly MemorySource[];
      journal: readonly MemorySource[];
      status: MemoryStatus;
    }>
  | Readonly<{ type: "load.failed"; message: string }>
  | Readonly<{ type: "source.selected"; sourceId: string | null }>
  | Readonly<{ type: "action.started"; key: string }>
  | Readonly<{ type: "action.finished"; key: string; notice?: string; error?: string }>
  | Readonly<{ type: "deletion.updated"; deletion: DeletionProgress }>
  | Readonly<{ type: "notice.cleared" }>;

function boundedUiText(value: string): string {
  const normalized = String(value || "").trim();
  return normalized.length <= 500 ? normalized : `${normalized.slice(0, 497)}...`;
}

function freezeState(state: MemorySurfaceState): MemorySurfaceState {
  return Object.freeze({
    ...state,
    episodes: Object.freeze([...state.episodes]),
    journal: Object.freeze([...state.journal]),
    sharedMemories: Object.freeze([...state.sharedMemories]),
    pendingActions: Object.freeze([...state.pendingActions]),
  });
}

export function createMemorySurfaceState(): MemorySurfaceState {
  return freezeState({
    revision: 0,
    tab: "sources",
    loadState: "idle",
    episodes: [],
    journal: [],
    sharedMemories: [],
    legacy: null,
    selectedSourceId: null,
    pendingActions: [],
    deletion: null,
    notice: "",
    error: "",
  });
}

export function reduceMemorySurfaceState(
  state: MemorySurfaceState,
  event: MemoryStateEvent,
): MemorySurfaceState {
  const nextRevision = state.revision + 1;
  if (event.type === "tab.selected") {
    return freezeState({ ...state, revision: nextRevision, tab: event.tab });
  }
  if (event.type === "load.started") {
    return freezeState({
      ...state,
      revision: nextRevision,
      loadState: "loading",
      error: "",
    });
  }
  if (event.type === "load.succeeded") {
    const sources = [...event.episodes, ...event.journal];
    const selectedSourceId = sources.some((item) => item.id === state.selectedSourceId)
      ? state.selectedSourceId
      : sources[0]?.id ?? null;
    return freezeState({
      ...state,
      revision: nextRevision,
      loadState: "ready",
      episodes: event.episodes,
      journal: event.journal,
      sharedMemories: event.status.sharedMemories,
      legacy: event.status.legacy,
      selectedSourceId,
      error: "",
    });
  }
  if (event.type === "load.failed") {
    return freezeState({
      ...state,
      revision: nextRevision,
      loadState: "error",
      error: boundedUiText(event.message) || "记忆加载失败",
    });
  }
  if (event.type === "source.selected") {
    return freezeState({
      ...state,
      revision: nextRevision,
      selectedSourceId: event.sourceId,
      notice: "",
      error: "",
    });
  }
  if (event.type === "action.started") {
    if (state.pendingActions.includes(event.key)) return state;
    return freezeState({
      ...state,
      revision: nextRevision,
      pendingActions: [...state.pendingActions, event.key],
      notice: "",
      error: "",
    });
  }
  if (event.type === "action.finished") {
    return freezeState({
      ...state,
      revision: nextRevision,
      pendingActions: state.pendingActions.filter((key) => key !== event.key),
      notice: boundedUiText(event.notice ?? ""),
      error: boundedUiText(event.error ?? ""),
    });
  }
  if (event.type === "deletion.updated") {
    return freezeState({
      ...state,
      revision: nextRevision,
      deletion: event.deletion,
      notice: event.deletion.state === "verified" ? "遗忘已完成并复核" : state.notice,
      error: "",
    });
  }
  return freezeState({ ...state, revision: nextRevision, notice: "", error: "" });
}

export function beginMemoryAction(
  state: MemorySurfaceState,
  key: string,
): Readonly<{ accepted: boolean; state: MemorySurfaceState }> {
  const normalized = String(key || "").trim();
  if (!normalized || state.pendingActions.includes(normalized)) {
    return Object.freeze({ accepted: false, state });
  }
  return Object.freeze({
    accepted: true,
    state: reduceMemorySurfaceState(state, { type: "action.started", key: normalized }),
  });
}

export function selectedMemorySource(state: MemorySurfaceState): MemorySource | null {
  return [...state.episodes, ...state.journal].find(
    (item) => item.id === state.selectedSourceId,
  ) ?? null;
}

const DELETION_STEPS = [
  "accepted",
  "fenced",
  "source_pending",
  "source_retained",
  "primary_rows_deleted",
  "derivations_deleted",
  "fts_deleted",
  "caches_invalidated",
  "prompt_invalidated",
  "verified",
] as const;

export function deletionProgressPercent(progress: DeletionProgress | null): number {
  if (!progress) return 0;
  const index = DELETION_STEPS.indexOf(progress.state);
  if (index < 0) return 0;
  return Math.round((index / (DELETION_STEPS.length - 1)) * 100);
}

export function describeSourceHandling(sourceHandling: SourceHandling): Readonly<{
  title: string;
  detail: string;
  destructive: boolean;
}> {
  if (sourceHandling === "source_and_derived") {
    return Object.freeze({
      title: "删除来源及派生记忆",
      detail: "同时删除所选对话来源正文与由它形成的记忆；该来源之后无法恢复查看。",
      destructive: true,
    });
  }
  return Object.freeze({
    title: "仅删除派生记忆",
    detail: "删除记忆、索引与提示词引用，但保留原始对话来源；这不等于删除聊天记录。",
    destructive: false,
  });
}

export function memoryActionKey(
  action: string,
  resourceId: string,
  revision: number,
): string {
  const normalizedAction = action.replace(/[^a-z_.-]/gi, "").slice(0, 40);
  const normalizedResource = resourceId.replace(/[^a-z0-9_.-]/gi, "_").slice(0, 96);
  return `${normalizedAction}:${normalizedResource}:${Math.max(0, Math.trunc(revision))}`;
}
