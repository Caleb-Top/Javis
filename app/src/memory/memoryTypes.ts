export const MEMORY_ITEM_KINDS = [
  "experience_episode",
  "journal_entry",
  "shared_memory",
] as const;

export type MemoryItemKind = typeof MEMORY_ITEM_KINDS[number];
export type MemorySourceKind = Exclude<MemoryItemKind, "shared_memory">;
export type SharedMemoryStatus =
  | "proposed"
  | "confirmed"
  | "rejected"
  | "revoked"
  | "deletion_fenced";
export type MemoryItemStatus =
  | "quarantined"
  | "candidate"
  | "active"
  | "superseded"
  | "deletion_fenced";
export type SourceHandling = "derived_only" | "source_and_derived";
export type DeletionScope =
  | "item"
  | "episode"
  | "shared_memory"
  | "session_derived"
  | "time_range"
  | "all_memory";
export type DeletionState =
  | "accepted"
  | "fenced"
  | "source_pending"
  | "source_retained"
  | "primary_rows_deleted"
  | "derivations_deleted"
  | "fts_deleted"
  | "caches_invalidated"
  | "prompt_invalidated"
  | "verified";

export type MemorySource = Readonly<{
  id: string;
  kind: MemorySourceKind;
  revision: number;
  title: string;
  summary: string;
  detail: string;
  startedAtUtc: string;
  endedAtUtc: string;
  status: MemoryItemStatus;
  evidenceCount: number;
  sourceEvidenceIds: readonly string[];
}>;

export type SharedMemoryView = Readonly<{
  id: string;
  revision: number;
  proposalRevision: number;
  text: string;
  status: SharedMemoryStatus;
  sourceEpisodeIds: readonly string[];
  confirmedAtUtc: string | null;
  revokedAtUtc: string | null;
  updatedAtUtc: string;
}>;

export type LegacyMemoryStatus = Readonly<{
  state: "absent" | "read_only" | "quarantined" | "migration_pending" | "degraded";
  quarantineCount: number;
  candidateCount: number;
  lastScanAtUtc: string | null;
  detail: string;
}>;

export type MemoryStatus = Readonly<{
  serviceState: "ready" | "degraded" | "unavailable";
  legacy: LegacyMemoryStatus;
  sharedMemories: readonly SharedMemoryView[];
}>;

export type MemoryCommandStatus = "accepted" | "queued" | "completed";
export type MemoryCommandReceipt = Readonly<{
  commandId: string;
  status: MemoryCommandStatus;
  resourceId: string | null;
}>;

export type DeletionProgress = Readonly<{
  deletionRequestId: string;
  state: DeletionState;
  scope: DeletionScope;
  sourceHandling: SourceHandling;
  attempt: number;
  lastReasonCode: string | null;
  updatedAtUtc: string;
  completedAtUtc: string | null;
}>;

export type MemoryListPage<T> = Readonly<{
  items: readonly T[];
  nextCursor: string | null;
}>;

const MEMORY_STATUSES = new Set<string>([
  "quarantined",
  "candidate",
  "active",
  "superseded",
  "deletion_fenced",
]);
const SHARED_STATUSES = new Set<string>([
  "proposed",
  "confirmed",
  "rejected",
  "revoked",
  "deletion_fenced",
]);
const DELETION_SCOPES = new Set<string>([
  "item",
  "episode",
  "shared_memory",
  "session_derived",
  "time_range",
  "all_memory",
]);
const SOURCE_HANDLING = new Set<string>(["derived_only", "source_and_derived"]);
const DELETION_STATES = new Set<string>([
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
]);
const COMMAND_STATUSES = new Set<string>(["accepted", "queued", "completed"]);
const LEGACY_STATES = new Set<string>([
  "absent",
  "read_only",
  "quarantined",
  "migration_pending",
  "degraded",
]);
const SERVICE_STATES = new Set<string>(["ready", "degraded", "unavailable"]);
const IDENTIFIER_PATTERN = /^[^\u0000-\u001f\u007f]{1,256}$/;
const RFC3339_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/;

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, label: string, max = 32_768, allowEmpty = false): string {
  if (typeof value !== "string" || value.length > max || /[\0]/.test(value)) {
    throw new TypeError(`${label} must be bounded text`);
  }
  const normalized = value.trim();
  if (!allowEmpty && !normalized) throw new TypeError(`${label} is required`);
  return normalized;
}

function identifier(value: unknown, label: string): string {
  const normalized = text(value, label, 256);
  if (!IDENTIFIER_PATTERN.test(normalized)) throw new TypeError(`${label} is invalid`);
  return normalized;
}

function integer(value: unknown, label: string, minimum = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum) {
    throw new TypeError(`${label} must be an integer >= ${minimum}`);
  }
  return value as number;
}

function timestamp(value: unknown, label: string, optional = false): string | null {
  if (value === null && optional) return null;
  const normalized = text(value, label, 64);
  if (!RFC3339_PATTERN.test(normalized) || !Number.isFinite(Date.parse(normalized))) {
    throw new TypeError(`${label} must be an RFC3339 UTC timestamp`);
  }
  return normalized;
}

function stringArray(value: unknown, label: string, maximum = 64): readonly string[] {
  if (!Array.isArray(value) || value.length > maximum) {
    throw new TypeError(`${label} must be a bounded array`);
  }
  const result = value.map((item, index) => identifier(item, `${label}[${index}]`));
  if (new Set(result).size !== result.length) throw new TypeError(`${label} has duplicates`);
  return Object.freeze(result);
}

function enumValue<T extends string>(value: unknown, allowed: Set<string>, label: string): T {
  if (typeof value !== "string" || !allowed.has(value)) {
    throw new TypeError(`${label} is unsupported`);
  }
  return value as T;
}

function schema(value: Record<string, unknown>, label: string): void {
  if (value.schema_version !== 1) throw new TypeError(`${label} schema is unsupported`);
}

function pageItems(value: unknown, label: string): {
  root: Record<string, unknown>;
  items: readonly unknown[];
} {
  const root = record(value, label);
  schema(root, label);
  if (!Array.isArray(root.items) || root.items.length > 500) {
    throw new TypeError(`${label}.items must be a bounded array`);
  }
  return { root, items: root.items };
}

function parseNextCursor(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  return identifier(value, "next_cursor");
}

export function parseEpisodePage(value: unknown): MemoryListPage<MemorySource> {
  const { root, items } = pageItems(value, "episode page");
  return Object.freeze({
    items: Object.freeze(items.map((item) => {
      const row = record(item, "episode");
      schema(row, "episode");
      const startedAtUtc = timestamp(row.started_at_utc, "episode.started_at_utc")!;
      const endedAtUtc = timestamp(row.ended_at_utc, "episode.ended_at_utc")!;
      if (Date.parse(endedAtUtc) < Date.parse(startedAtUtc)) {
        throw new TypeError("episode time range is invalid");
      }
      const eventIds = stringArray(row.source_event_ids, "episode.source_event_ids");
      const messageIds = stringArray(row.source_message_ids, "episode.source_message_ids");
      if (eventIds.length + messageIds.length === 0) {
        throw new TypeError("episode requires source evidence");
      }
      const whatHappened = text(row.what_happened, "episode.what_happened", 8_000);
      const intent = text(row.intent_summary, "episode.intent_summary", 2_000, true);
      const result = text(
        row.verified_result_summary,
        "episode.verified_result_summary",
        2_000,
        true,
      );
      return Object.freeze({
        id: identifier(row.episode_id, "episode.episode_id"),
        kind: "experience_episode" as const,
        revision: integer(row.revision, "episode.revision", 1),
        title: intent || "对话经历",
        summary: whatHappened,
        detail: result || text(row.action_summary, "episode.action_summary", 2_000, true),
        startedAtUtc,
        endedAtUtc,
        status: enumValue<MemoryItemStatus>(row.status, MEMORY_STATUSES, "episode.status"),
        evidenceCount: eventIds.length + messageIds.length,
        sourceEvidenceIds: Object.freeze([...eventIds, ...messageIds]),
      });
    })),
    nextCursor: parseNextCursor(root.next_cursor),
  });
}

export function parseJournalPage(value: unknown): MemoryListPage<MemorySource> {
  const { root, items } = pageItems(value, "journal page");
  return Object.freeze({
    items: Object.freeze(items.map((item) => {
      const row = record(item, "journal entry");
      schema(row, "journal entry");
      const startedAtUtc = timestamp(row.range_started_at_utc, "journal.range_started_at_utc")!;
      const endedAtUtc = timestamp(row.range_ended_at_utc, "journal.range_ended_at_utc")!;
      if (Date.parse(endedAtUtc) < Date.parse(startedAtUtc)) {
        throw new TypeError("journal time range is invalid");
      }
      const sourceIds = stringArray(row.source_episode_ids, "journal.source_episode_ids");
      if (!sourceIds.length) throw new TypeError("journal requires source episodes");
      return Object.freeze({
        id: identifier(row.entry_id, "journal.entry_id"),
        kind: "journal_entry" as const,
        revision: integer(row.revision, "journal.revision", 1),
        title: text(row.title, "journal.title", 512),
        summary: text(row.body, "journal.body", 32_768),
        detail: `${sourceIds.length} 条经历来源`,
        startedAtUtc,
        endedAtUtc,
        status: enumValue<MemoryItemStatus>(row.status, MEMORY_STATUSES, "journal.status"),
        evidenceCount: sourceIds.length,
        sourceEvidenceIds: sourceIds,
      });
    })),
    nextCursor: parseNextCursor(root.next_cursor),
  });
}

export function parseSharedMemory(value: unknown): SharedMemoryView {
  const row = record(value, "shared memory");
  schema(row, "shared memory");
  return Object.freeze({
    id: identifier(row.shared_memory_id, "shared_memory.shared_memory_id"),
    revision: integer(row.revision, "shared_memory.revision", 1),
    proposalRevision: integer(row.proposal_revision, "shared_memory.proposal_revision", 1),
    text: text(row.proposed_text, "shared_memory.proposed_text", 8_000),
    status: enumValue<SharedMemoryStatus>(row.status, SHARED_STATUSES, "shared_memory.status"),
    sourceEpisodeIds: stringArray(row.source_episode_ids, "shared_memory.source_episode_ids"),
    confirmedAtUtc: timestamp(row.confirmed_at_utc, "shared_memory.confirmed_at_utc", true),
    revokedAtUtc: timestamp(row.revoked_at_utc, "shared_memory.revoked_at_utc", true),
    updatedAtUtc: timestamp(row.updated_at_utc, "shared_memory.updated_at_utc")!,
  });
}

export function parseMemoryStatus(value: unknown): MemoryStatus {
  const root = record(value, "memory status");
  schema(root, "memory status");
  const legacy = record(root.legacy, "memory status.legacy");
  const shared = root.shared_memories ?? [];
  if (!Array.isArray(shared) || shared.length > 500) {
    throw new TypeError("memory status.shared_memories must be a bounded array");
  }
  return Object.freeze({
    serviceState: enumValue<MemoryStatus["serviceState"]>(
      root.service_state,
      SERVICE_STATES,
      "memory status.service_state",
    ),
    legacy: Object.freeze({
      state: enumValue<LegacyMemoryStatus["state"]>(
        legacy.state,
        LEGACY_STATES,
        "memory status.legacy.state",
      ),
      quarantineCount: integer(legacy.quarantine_count, "legacy.quarantine_count"),
      candidateCount: integer(legacy.candidate_count, "legacy.candidate_count"),
      lastScanAtUtc: timestamp(legacy.last_scan_at_utc, "legacy.last_scan_at_utc", true),
      detail: text(legacy.detail ?? "", "legacy.detail", 2_000, true),
    }),
    sharedMemories: Object.freeze(shared.map(parseSharedMemory)),
  });
}

export function parseCommandReceipt(value: unknown): MemoryCommandReceipt {
  const root = record(value, "memory command receipt");
  schema(root, "memory command receipt");
  return Object.freeze({
    commandId: identifier(root.command_id, "memory command receipt.command_id"),
    status: enumValue<MemoryCommandStatus>(
      root.status,
      COMMAND_STATUSES,
      "memory command receipt.status",
    ),
    resourceId: root.resource_id === undefined || root.resource_id === null
      ? null
      : identifier(root.resource_id, "memory command receipt.resource_id"),
  });
}

export function parseDeletionProgress(value: unknown): DeletionProgress {
  const root = record(value, "deletion progress");
  schema(root, "deletion progress");
  return Object.freeze({
    deletionRequestId: identifier(root.deletion_request_id, "deletion.deletion_request_id"),
    state: enumValue<DeletionState>(root.state, DELETION_STATES, "deletion.state"),
    scope: enumValue<DeletionScope>(root.scope, DELETION_SCOPES, "deletion.scope"),
    sourceHandling: enumValue<SourceHandling>(
      root.source_handling,
      SOURCE_HANDLING,
      "deletion.source_handling",
    ),
    attempt: integer(root.attempt, "deletion.attempt"),
    lastReasonCode: root.last_reason_code === undefined || root.last_reason_code === null
      ? null
      : text(root.last_reason_code, "deletion.last_reason_code", 128),
    updatedAtUtc: timestamp(root.updated_at_utc, "deletion.updated_at_utc")!,
    completedAtUtc: timestamp(root.completed_at_utc, "deletion.completed_at_utc", true),
  });
}
