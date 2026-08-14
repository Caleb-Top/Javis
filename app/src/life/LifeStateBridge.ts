import type { BackendEvent } from "../bridge/backendClient.ts";
import type { LiveState } from "../live/liveState.ts";
import type { StateSignal } from "../state/runtimeStateTypes.ts";
import type {
  ExpressionBaseState,
  ExpressionIntent,
  ExpressionListener,
  GazeTarget,
  HealthSummary,
  IdentitySummary,
  InstanceSummary,
  LifeActivity,
  LifeBridgeSnapshot,
  LifeCycleState,
  LifeSnapshot,
  VoiceActivity,
} from "./lifeTypes.ts";

export type LifeStateBridgeOptions = {
  signal(signal: StateSignal): unknown;
  now?(): number;
};

export type LifeStateBridge = {
  handle(event: BackendEvent): boolean;
  projectOffline(reason?: string): void;
  snapshot(): LifeBridgeSnapshot;
  subscribeExpression(listener: ExpressionListener): () => void;
};

const LIFE_CYCLE_STATES = new Set<LifeCycleState>([
  "booting",
  "awake",
  "quiet",
  "engaged",
  "degraded",
  "recovering",
  "stopping",
  "offline",
]);
const LIFE_ACTIVITIES = new Set<LifeActivity>([
  "idle",
  "attention",
  "quiet",
  "listening",
  "thinking",
  "speaking",
  "executing",
  "blocked",
  "error",
  "offline",
]);
const EXPRESSION_STATES = new Set<ExpressionBaseState>([
  "idle",
  "attention",
  "listening",
  "thinking",
  "speaking",
  "executing",
  "blocked",
  "error",
  "offline",
]);
const GAZE_TARGETS = new Set<GazeTarget>(["none", "user", "content", "task"]);
const VOICE_ACTIVITIES = new Set<VoiceActivity>(["silent", "listening", "speaking"]);
const RFC3339_MILLISECONDS = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f]/;

const SNAPSHOT_KEYS = [
  "schema_version",
  "revision",
  "identity",
  "instance",
  "lifecycle_state",
  "active_session_id",
  "active_request_id",
  "activity",
  "health",
  "degradation_level",
  "recovery_required",
  "last_event_id",
  "last_sequence",
  "updated_at",
  "explanation",
] as const;
const IDENTITY_KEYS = [
  "identity_id",
  "name",
  "kind",
  "relationship_role",
  "version",
  "content_hash",
] as const;
const INSTANCE_KEYS = [
  "instance_id",
  "lineage_id",
  "parent_instance_id",
  "generation",
  "fork_pending_review",
] as const;
const HEALTH_KEYS = ["status", "degraded_components", "reason_codes"] as const;
const EXPRESSION_KEYS = [
  "schema_version",
  "revision",
  "base_state",
  "intensity",
  "gaze_target",
  "voice_activity",
  "transition_ms",
  "interrupt",
  "source_snapshot_revision",
  "generated_at",
  "expires_at",
  "explanation_code",
] as const;

const STATE_DETAILS: Record<LifeActivity, string> = {
  idle: "Javis \u5df2\u5f85\u547d",
  attention: "\u6b63\u5728\u5173\u6ce8",
  quiet: "Javis \u5df2\u5f85\u547d",
  listening: "\u6b63\u5728\u8046\u542c",
  thinking: "\u6b63\u5728\u601d\u8003",
  speaking: "\u6b63\u5728\u56de\u7b54",
  executing: "\u6b63\u5728\u6267\u884c",
  blocked: "\u7b49\u5f85\u6388\u6743",
  error: "\u9700\u8981\u68c0\u67e5",
  offline: "\u540e\u7aef\u79bb\u7ebf",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === expected.length
    && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isBoundedText(value: unknown, maxLength = 4096): value is string {
  return typeof value === "string"
    && value.length > 0
    && value.length <= maxLength
    && !CONTROL_CHARACTER.test(value);
}

function isOptionalId(value: unknown): value is string | null {
  return value === null || isBoundedText(value, 256);
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value)
    && value.length <= 128
    && value.every((item) => isBoundedText(item, 256));
}

function parseTimestamp(value: unknown): number | null {
  if (typeof value !== "string" || !RFC3339_MILLISECONDS.test(value)) return null;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString() !== value) return null;
  return timestamp;
}

function readIdentity(value: unknown): IdentitySummary | null {
  if (!isRecord(value) || !hasExactKeys(value, IDENTITY_KEYS)) return null;
  if (
    !isBoundedText(value.identity_id, 256)
    || !isBoundedText(value.name, 256)
    || !isBoundedText(value.kind, 256)
    || !isBoundedText(value.relationship_role, 256)
    || !isNonNegativeInteger(value.version)
    || typeof value.content_hash !== "string"
    || !SHA256_HEX.test(value.content_hash)
  ) return null;
  return {
    identity_id: value.identity_id,
    name: value.name,
    kind: value.kind,
    relationship_role: value.relationship_role,
    version: value.version,
    content_hash: value.content_hash,
  };
}

function readInstance(value: unknown): InstanceSummary | null {
  if (!isRecord(value) || !hasExactKeys(value, INSTANCE_KEYS)) return null;
  if (
    !isBoundedText(value.instance_id, 256)
    || !isBoundedText(value.lineage_id, 256)
    || !isOptionalId(value.parent_instance_id)
    || !isNonNegativeInteger(value.generation)
    || typeof value.fork_pending_review !== "boolean"
  ) return null;
  return {
    instance_id: value.instance_id,
    lineage_id: value.lineage_id,
    parent_instance_id: value.parent_instance_id,
    generation: value.generation,
    fork_pending_review: value.fork_pending_review,
  };
}

function readHealth(value: unknown): HealthSummary | null {
  if (!isRecord(value) || !hasExactKeys(value, HEALTH_KEYS)) return null;
  if (
    !isBoundedText(value.status, 256)
    || !isStringList(value.degraded_components)
    || !isStringList(value.reason_codes)
  ) return null;
  return {
    status: value.status,
    degraded_components: [...value.degraded_components],
    reason_codes: [...value.reason_codes],
  };
}

function readLifeSnapshot(value: unknown): LifeSnapshot | null {
  if (!isRecord(value) || !hasExactKeys(value, SNAPSHOT_KEYS)) return null;
  const identity = readIdentity(value.identity);
  const instance = readInstance(value.instance);
  const health = readHealth(value.health);
  if (
    value.schema_version !== 1
    || !isNonNegativeInteger(value.revision)
    || !identity
    || !instance
    || typeof value.lifecycle_state !== "string"
    || !LIFE_CYCLE_STATES.has(value.lifecycle_state as LifeCycleState)
    || !isOptionalId(value.active_session_id)
    || !isOptionalId(value.active_request_id)
    || typeof value.activity !== "string"
    || !LIFE_ACTIVITIES.has(value.activity as LifeActivity)
    || !health
    || !isNonNegativeInteger(value.degradation_level)
    || typeof value.recovery_required !== "boolean"
    || !isOptionalId(value.last_event_id)
    || !isNonNegativeInteger(value.last_sequence)
    || parseTimestamp(value.updated_at) === null
    || !isBoundedText(value.explanation)
  ) return null;
  return {
    schema_version: 1,
    revision: value.revision,
    identity,
    instance,
    lifecycle_state: value.lifecycle_state as LifeCycleState,
    active_session_id: value.active_session_id,
    active_request_id: value.active_request_id,
    activity: value.activity as LifeActivity,
    health,
    degradation_level: value.degradation_level,
    recovery_required: value.recovery_required,
    last_event_id: value.last_event_id,
    last_sequence: value.last_sequence,
    updated_at: value.updated_at as string,
    explanation: value.explanation,
  };
}

function readExpressionIntent(value: unknown, now: number): ExpressionIntent | null {
  if (!isRecord(value) || !hasExactKeys(value, EXPRESSION_KEYS)) return null;
  const generatedAt = parseTimestamp(value.generated_at);
  const expiresAt = parseTimestamp(value.expires_at);
  if (
    value.schema_version !== 1
    || !isNonNegativeInteger(value.revision)
    || typeof value.base_state !== "string"
    || !EXPRESSION_STATES.has(value.base_state as ExpressionBaseState)
    || typeof value.intensity !== "number"
    || !Number.isFinite(value.intensity)
    || value.intensity < 0
    || value.intensity > 1
    || typeof value.gaze_target !== "string"
    || !GAZE_TARGETS.has(value.gaze_target as GazeTarget)
    || typeof value.voice_activity !== "string"
    || !VOICE_ACTIVITIES.has(value.voice_activity as VoiceActivity)
    || !isNonNegativeInteger(value.transition_ms)
    || typeof value.interrupt !== "boolean"
    || !isNonNegativeInteger(value.source_snapshot_revision)
    || generatedAt === null
    || expiresAt === null
    || expiresAt <= generatedAt
    || expiresAt <= now
    || !isBoundedText(value.explanation_code, 256)
  ) return null;
  return {
    schema_version: 1,
    revision: value.revision,
    base_state: value.base_state as ExpressionBaseState,
    intensity: value.intensity,
    gaze_target: value.gaze_target as GazeTarget,
    voice_activity: value.voice_activity as VoiceActivity,
    transition_ms: value.transition_ms,
    interrupt: value.interrupt,
    source_snapshot_revision: value.source_snapshot_revision,
    generated_at: value.generated_at as string,
    expires_at: value.expires_at as string,
    explanation_code: value.explanation_code,
  };
}

function toLiveState(activity: LifeActivity): LiveState {
  if (activity === "attention") return "thinking";
  if (activity === "quiet") return "idle";
  return activity;
}

function cloneLifeSnapshot(value: LifeSnapshot): LifeSnapshot {
  return {
    ...value,
    identity: { ...value.identity },
    instance: { ...value.instance },
    health: {
      ...value.health,
      degraded_components: [...value.health.degraded_components],
      reason_codes: [...value.health.reason_codes],
    },
  };
}

function cloneExpression(value: ExpressionIntent): ExpressionIntent {
  return { ...value };
}

export function createLifeStateBridge(options: LifeStateBridgeOptions): LifeStateBridge {
  const now = options.now ?? Date.now;
  const expressionListeners = new Set<ExpressionListener>();
  let currentSnapshot: LifeSnapshot | null = null;
  let currentExpression: ExpressionIntent | null = null;
  let state: LiveState = "idle";
  let detail = "Javis \u5df2\u5f85\u547d";
  let lastSignalTimestamp = -1;

  function timestamp(): number {
    const current = now();
    const usable = Number.isFinite(current) ? Math.trunc(current) : Date.now();
    lastSignalTimestamp = Math.max(usable, lastSignalTimestamp + 1);
    return lastSignalTimestamp;
  }

  function applySnapshot(payload: unknown): boolean {
    const candidate = readLifeSnapshot(payload);
    if (!candidate || candidate.revision <= (currentSnapshot?.revision ?? -1)) return false;
    currentSnapshot = candidate;
    state = toLiveState(candidate.activity);
    detail = STATE_DETAILS[candidate.activity];
    options.signal({
      source: "websocket",
      state,
      timestamp: timestamp(),
      requestId: candidate.active_request_id ?? undefined,
      detail,
      terminal: state === "idle" && candidate.active_request_id === null,
    });
    return true;
  }

  function applyExpression(payload: unknown): boolean {
    const candidate = readExpressionIntent(payload, now());
    if (!candidate || candidate.revision <= (currentExpression?.revision ?? -1)) return false;
    currentExpression = candidate;
    expressionListeners.forEach((listener) => {
      try {
        listener(cloneExpression(candidate));
      } catch {
        // Presentation subscribers cannot interrupt the canonical event stream.
      }
    });
    return true;
  }

  function handle(event: BackendEvent): boolean {
    if (event.type === "life.snapshot") return applySnapshot(event.payload);
    if (event.type === "life.expression") return applyExpression(event.payload);
    return false;
  }

  function projectOffline(reason = "\u540e\u7aef\u79bb\u7ebf"): void {
    state = "offline";
    detail = reason.trim() || "\u540e\u7aef\u79bb\u7ebf";
    options.signal({
      source: "websocket",
      state,
      timestamp: timestamp(),
      detail,
    });
  }

  function snapshot(): LifeBridgeSnapshot {
    return {
      state,
      detail,
      snapshotRevision: currentSnapshot?.revision ?? -1,
      expressionRevision: currentExpression?.revision ?? -1,
      snapshot: currentSnapshot ? cloneLifeSnapshot(currentSnapshot) : null,
      expression: currentExpression ? cloneExpression(currentExpression) : null,
    };
  }

  function subscribeExpression(listener: ExpressionListener): () => void {
    expressionListeners.add(listener);
    return () => expressionListeners.delete(listener);
  }

  return { handle, projectOffline, snapshot, subscribeExpression };
}
