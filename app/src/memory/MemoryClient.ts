import {
  LIFE_MEMORY_API_ROOT,
  resolveBackendEndpoints,
} from "../bridge/backendEndpoints.ts";
import type { RuntimeAccessProvider, RuntimeAccessScope } from "../bridge/runtimeAccess.ts";
import {
  parseCommandReceipt,
  parseDeletionProgress,
  parseEpisodePage,
  parseJournalPage,
  parseMemoryStatus,
  type DeletionProgress,
  type DeletionScope,
  type MemoryCommandReceipt,
  type MemoryItemKind,
  type MemoryListPage,
  type MemorySource,
  type MemoryStatus,
  type SourceHandling,
} from "./memoryTypes.ts";

export type SharedProposalInput = Readonly<{
  sourceEpisodeIds: readonly string[];
  proposedText: string;
  idempotencyKey: string;
}>;

export type SharedTransitionInput = Readonly<{
  sharedMemoryId: string;
  revision: number;
  idempotencyKey: string;
}>;

export type CorrectionInput = Readonly<{
  targetKind: MemoryItemKind;
  targetId: string;
  expectedRevision: number;
  correctedText: string;
  sourceEvidenceIds: readonly string[];
  idempotencyKey: string;
}>;

export type ForgetInput = Readonly<{
  scope: DeletionScope;
  targetKind?: MemoryItemKind;
  targetId?: string;
  sourceHandling: SourceHandling;
  rangeStartedAtUtc?: string;
  rangeEndedAtUtc?: string;
  reasonCode: string;
  idempotencyKey: string;
}>;

export type MemoryClient = Readonly<{
  listEpisodes(signal?: AbortSignal): Promise<MemoryListPage<MemorySource>>;
  listJournal(signal?: AbortSignal): Promise<MemoryListPage<MemorySource>>;
  status(signal?: AbortSignal): Promise<MemoryStatus>;
  proposeShared(input: SharedProposalInput, signal?: AbortSignal): Promise<MemoryCommandReceipt>;
  confirmShared(input: SharedTransitionInput, signal?: AbortSignal): Promise<MemoryCommandReceipt>;
  rejectShared(input: SharedTransitionInput, signal?: AbortSignal): Promise<MemoryCommandReceipt>;
  revokeShared(input: SharedTransitionInput, signal?: AbortSignal): Promise<MemoryCommandReceipt>;
  correct(input: CorrectionInput, signal?: AbortSignal): Promise<MemoryCommandReceipt>;
  forget(input: ForgetInput, signal?: AbortSignal): Promise<MemoryCommandReceipt>;
  deletion(deletionRequestId: string, signal?: AbortSignal): Promise<DeletionProgress>;
}>;

type FetchLike = (
  input: string | URL | Request,
  init?: RequestInit,
) => Promise<Pick<Response, "ok" | "status" | "json">>;

export class MemoryClientError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status = 0, code = "memory_request_failed") {
    super(message);
    this.name = "MemoryClientError";
    this.status = status;
    this.code = code;
  }
}

type MemoryClientOptions = Readonly<{
  runtimeAccess: RuntimeAccessProvider;
  sessionId(): string;
  fetch?: FetchLike;
  backendOrigin?: string;
}>;

const SAFE_IDENTIFIER = /^[^\u0000-\u001f\u007f]{1,256}$/;
const SAFE_ERROR_CODE = /^[a-z0-9_.-]{1,96}$/;
const SENSITIVE_ERROR_TEXT = /\b(?:acl|audience|bearer|capability|client[_ -]?id|nonce|owner|subject|token)\b|[A-Za-z0-9_-]{43,}/i;
const FORBIDDEN_BODY_KEYS = new Set([
  "owner",
  "owner_id",
  "owner_subject_id",
  "audience",
  "audience_subject_ids",
  "subject",
  "subject_id",
  "actor_subject_id",
  "access_context",
  "token",
  "acl",
]);

function boundedIdentifier(value: string, label: string): string {
  const normalized = String(value || "").trim();
  if (!SAFE_IDENTIFIER.test(normalized)) throw new TypeError(`${label} is invalid`);
  return normalized;
}

function boundedText(value: string, label: string, maximum: number): string {
  const normalized = String(value || "").trim();
  if (!normalized || normalized.length > maximum || /\0/.test(normalized)) {
    throw new TypeError(`${label} must be bounded text`);
  }
  return normalized;
}

function assertNoAuthorityFields(value: unknown): void {
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value)) {
    value.forEach(assertNoAuthorityFields);
    return;
  }
  Object.entries(value as Record<string, unknown>).forEach(([key, child]) => {
    if (FORBIDDEN_BODY_KEYS.has(key.toLowerCase())) {
      throw new TypeError(`memory request body cannot set authority field ${key}`);
    }
    assertNoAuthorityFields(child);
  });
}

async function parseFailure(response: Pick<Response, "status" | "json">): Promise<MemoryClientError> {
  let value: unknown = null;
  try {
    value = await response.json();
  } catch {
    // Keep server-controlled HTML and transport details out of the UI.
  }
  const root = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  const nested = root.detail && typeof root.detail === "object" && !Array.isArray(root.detail)
    ? root.detail as Record<string, unknown>
    : null;
  const detail = nested ?? root;
  const rawCode = typeof detail.code === "string" ? detail.code : "memory_request_failed";
  const code = SAFE_ERROR_CODE.test(rawCode) ? rawCode : "memory_request_failed";
  const fallback = response.status === 409
    ? "记忆已发生变化，请刷新后重试"
    : response.status === 403
      ? "当前会话没有执行此记忆操作的权限"
      : response.status === 401
        ? "记忆访问授权已失效"
        : "记忆服务暂时不可用";
  const detailMessage = typeof root.detail === "string" ? root.detail : "";
  const rawMessage = typeof detail.message === "string" ? detail.message.trim() : detailMessage.trim();
  const message = rawMessage
    && rawMessage.length <= 300
    && !/[\0\r\n]/.test(rawMessage)
    && !SENSITIVE_ERROR_TEXT.test(rawMessage)
    ? rawMessage
    : fallback;
  return new MemoryClientError(message, response.status, code);
}

function queryPath(path: string, values: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined) query.set(key, String(value));
  });
  const encoded = query.toString();
  return encoded ? `${path}?${encoded}` : path;
}

export function createMemoryClient(options: MemoryClientOptions): MemoryClient {
  const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  const origin = resolveBackendEndpoints(options.backendOrigin).http;
  const memoryPath = (path: string): string => `${LIFE_MEMORY_API_ROOT}${path}`;

  async function request(
    scope: RuntimeAccessScope,
    path: string,
    init: RequestInit = {},
    signal?: AbortSignal,
  ): Promise<unknown> {
    await options.runtimeAccess.ensure();
    const token = options.runtimeAccess.tokenForScope(scope);
    if (!token) {
      throw new MemoryClientError("当前会话没有可用的记忆访问能力", 403, "memory_capability_missing");
    }
    const sessionId = boundedIdentifier(options.sessionId(), "session id");
    if (init.body) {
      const parsed = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
      assertNoAuthorityFields(parsed);
    }
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    headers.set("X-Javis-Runtime-Capability", token);
    headers.set("X-Javis-Session-Id", sessionId);
    const response = await fetcher(`${origin}${path}`, {
      ...init,
      signal,
      headers,
    });
    if (!response.ok) throw await parseFailure(response);
    try {
      return await response.json();
    } catch {
      throw new MemoryClientError("记忆服务返回了无效响应", response.status, "invalid_memory_response");
    }
  }

  function mutation(
    scope: "memory.manage" | "memory.delete",
    path: string,
    body: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<MemoryCommandReceipt> {
    assertNoAuthorityFields(body);
    return request(scope, path, {
      method: "POST",
      body: JSON.stringify(body),
    }, signal).then(parseCommandReceipt);
  }

  return Object.freeze({
    async listEpisodes(signal) {
      return parseEpisodePage(await request(
        "memory.read",
        queryPath(memoryPath("/episodes"), { limit: 100 }),
        {},
        signal,
      ));
    },
    async listJournal(signal) {
      return parseJournalPage(await request(
        "memory.read",
        queryPath(memoryPath("/journal"), { limit: 100 }),
        {},
        signal,
      ));
    },
    async status(signal) {
      return parseMemoryStatus(await request("memory.read", memoryPath("/status"), {}, signal));
    },
    proposeShared(input, signal) {
      const sourceEpisodeIds = input.sourceEpisodeIds.map((id) => boundedIdentifier(id, "source episode id"));
      if (!sourceEpisodeIds.length || sourceEpisodeIds.length > 64) {
        throw new TypeError("shared proposal requires 1-64 source episodes");
      }
      return mutation("memory.manage", memoryPath("/shared/proposals"), {
        source_episode_ids: sourceEpisodeIds,
        proposed_text: boundedText(input.proposedText, "proposed text", 8_000),
        idempotency_key: boundedIdentifier(input.idempotencyKey, "idempotency key"),
      }, signal);
    },
    confirmShared(input, signal) {
      return mutation(
        "memory.manage",
        memoryPath(`/shared/${encodeURIComponent(boundedIdentifier(input.sharedMemoryId, "shared memory id"))}/confirm`),
        {
          proposal_revision: input.revision,
          idempotency_key: boundedIdentifier(input.idempotencyKey, "idempotency key"),
        },
        signal,
      );
    },
    rejectShared(input, signal) {
      return mutation(
        "memory.manage",
        memoryPath(`/shared/${encodeURIComponent(boundedIdentifier(input.sharedMemoryId, "shared memory id"))}/reject`),
        {
          proposal_revision: input.revision,
          idempotency_key: boundedIdentifier(input.idempotencyKey, "idempotency key"),
        },
        signal,
      );
    },
    revokeShared(input, signal) {
      return mutation(
        "memory.manage",
        memoryPath(`/shared/${encodeURIComponent(boundedIdentifier(input.sharedMemoryId, "shared memory id"))}/revoke`),
        {
          expected_revision: input.revision,
          idempotency_key: boundedIdentifier(input.idempotencyKey, "idempotency key"),
        },
        signal,
      );
    },
    correct(input, signal) {
      if (!Number.isSafeInteger(input.expectedRevision) || input.expectedRevision < 1) {
        throw new TypeError("expected revision is invalid");
      }
      const evidenceIds = input.sourceEvidenceIds.map((id) => boundedIdentifier(id, "source evidence id"));
      if (!evidenceIds.length || evidenceIds.length > 64) {
        throw new TypeError("correction requires source evidence");
      }
      return mutation("memory.manage", memoryPath("/corrections"), {
        target_kind: input.targetKind,
        target_id: boundedIdentifier(input.targetId, "target id"),
        expected_revision: input.expectedRevision,
        corrected_text: boundedText(input.correctedText, "corrected text", 32_768),
        source_evidence_ids: evidenceIds,
        idempotency_key: boundedIdentifier(input.idempotencyKey, "idempotency key"),
      }, signal);
    },
    forget(input, signal) {
      const body: Record<string, unknown> = {
        scope: input.scope,
        source_handling: input.sourceHandling,
        reason_code: boundedText(input.reasonCode, "reason code", 96),
        idempotency_key: boundedIdentifier(input.idempotencyKey, "idempotency key"),
      };
      if (input.targetKind) body.target_kind = input.targetKind;
      if (input.targetId) body.target_id = boundedIdentifier(input.targetId, "target id");
      if (input.scope === "time_range") {
        const startedAt = boundedText(input.rangeStartedAtUtc ?? "", "range start", 64);
        const endedAt = boundedText(input.rangeEndedAtUtc ?? "", "range end", 64);
        if (!Number.isFinite(Date.parse(startedAt)) || !Number.isFinite(Date.parse(endedAt))) {
          throw new TypeError("deletion time range is invalid");
        }
        if (Date.parse(endedAt) <= Date.parse(startedAt)) {
          throw new TypeError("deletion time range must end after it starts");
        }
        body.range_started_at_utc = startedAt;
        body.range_ended_at_utc = endedAt;
      }
      return mutation("memory.delete", memoryPath("/deletions"), body, signal);
    },
    async deletion(deletionRequestId, signal) {
      const id = encodeURIComponent(boundedIdentifier(deletionRequestId, "deletion request id"));
      return parseDeletionProgress(await request(
        "memory.delete",
        memoryPath(`/deletions/${id}`),
        {},
        signal,
      ));
    },
  });
}
