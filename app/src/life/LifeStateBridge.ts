import type { BackendEvent } from "../bridge/backendClient.ts";
import type { LiveState } from "../live/liveState.ts";
import type { StateSignal } from "../state/runtimeStateTypes.ts";
import type {
  AttentionMode,
  AttentionSnapshot,
  ExpressionBaseState,
  ExpressionIntent,
  ExpressionListener,
  FunctionalAffect,
  FunctionalAffectKind,
  GazeTarget,
  HealthSummary,
  HomeostasisSnapshot,
  IdentitySummary,
  InnerStateReasonCode,
  InnerStateSnapshot,
  InstanceSummary,
  LifeActivity,
  LifeBridgeSnapshot,
  LifeCycleState,
  LifeSnapshot,
  PresenceSnapshot,
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
const ATTENTION_MODES = new Set<AttentionMode>([
  "idle",
  "present",
  "listening",
  "engaged",
  "speaking",
  "awaiting_approval",
  "blocked",
  "recovering",
]);
const AFFECT_KINDS = new Set<FunctionalAffectKind>([
  "curious",
  "cautious",
  "blocked",
  "relieved",
  "satisfied",
]);
const INNER_STATE_REASON_CODES = new Set<InnerStateReasonCode>([
  "quiet_baseline",
  "user_invoked",
  "voice_listening_started",
  "voice_listening_stopped",
  "request_started",
  "request_activity",
  "approval_required",
  "approval_approved",
  "approval_denied",
  "tool_started",
  "tool_risk_observed",
  "tool_completed",
  "request_completed",
  "request_failed",
  "request_cancelled",
  "speech_started",
  "speech_stopped",
  "interaction_interrupted",
  "goal_verified",
  "runtime_degraded",
  "runtime_recovered",
  "load_reduced",
  "blocked_by_failure",
]);
const RFC3339_MILLISECONDS = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const MACHINE_CODE = /^[a-z0-9][a-z0-9._-]{0,127}$/;
const CONTROL_CHARACTER = /[\u0000-\u001f\u007f-\u009f]/;
const MAX_INNER_STATE_AFFECTS = 32;
const MAX_AFFECT_EVIDENCE_IDS = 32;

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
const INNER_STATE_KEYS = [
  "schema_version",
  "source_life_snapshot_revision",
  "identity_id",
  "instance_id",
  "generated_at_utc",
  "phase",
  "attention",
  "homeostasis",
  "affects",
  "presence",
  "last_observation_id",
  "degraded",
] as const;
const ATTENTION_KEYS = [
  "mode",
  "target_kind",
  "target_id",
  "priority",
  "since_utc",
  "expires_at_utc",
  "source_observation_id",
] as const;
const HOMEOSTASIS_KEYS = [
  "updated_at_utc",
  "activation",
  "cognitive_load",
  "certainty",
  "caution",
  "curiosity",
  "blockedness",
  "social_presence",
] as const;
const AFFECT_KEYS = [
  "kind",
  "intensity",
  "confidence",
  "reason_code",
  "evidence_ids",
  "valid_until_utc",
] as const;
const PRESENCE_KEYS = [
  "mode",
  "intensity",
  "session_id",
  "source_observation_id",
  "reason_code",
  "since_utc",
  "expires_at_utc",
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

function isUnitNumber(value: unknown): value is number {
  return typeof value === "number"
    && Number.isFinite(value)
    && value >= 0
    && value <= 1;
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

function isMachineCode(value: unknown): value is string {
  return typeof value === "string" && MACHINE_CODE.test(value);
}

function isOptionalMachineCode(value: unknown): value is string | null {
  return value === null || isMachineCode(value);
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

function readAttentionSnapshot(value: unknown): AttentionSnapshot | null {
  if (!isRecord(value) || !hasExactKeys(value, ATTENTION_KEYS)) return null;
  const sinceAt = parseTimestamp(value.since_utc);
  const expiresAt = value.expires_at_utc === null ? null : parseTimestamp(value.expires_at_utc);
  const targetFields = [value.target_kind, value.target_id, value.source_observation_id];
  const hasNoTarget = targetFields.every((item) => item === null);
  const hasCompleteTarget = targetFields.every((item) => item !== null);
  if (
    typeof value.mode !== "string"
    || !ATTENTION_MODES.has(value.mode as AttentionMode)
    || !isOptionalMachineCode(value.target_kind)
    || !isOptionalId(value.target_id)
    || !Number.isSafeInteger(value.priority)
    || (value.priority as number) < 0
    || (value.priority as number) > 100
    || sinceAt === null
    || (value.expires_at_utc !== null && expiresAt === null)
    || (expiresAt !== null && expiresAt <= sinceAt)
    || !isOptionalId(value.source_observation_id)
    || (!hasNoTarget && !hasCompleteTarget)
  ) return null;
  return {
    mode: value.mode as AttentionMode,
    target_kind: value.target_kind,
    target_id: value.target_id,
    priority: value.priority as number,
    since_utc: value.since_utc as string,
    expires_at_utc: value.expires_at_utc as string | null,
    source_observation_id: value.source_observation_id,
  };
}

function readHomeostasisSnapshot(value: unknown): HomeostasisSnapshot | null {
  if (!isRecord(value) || !hasExactKeys(value, HOMEOSTASIS_KEYS)) return null;
  if (
    parseTimestamp(value.updated_at_utc) === null
    || !isUnitNumber(value.activation)
    || !isUnitNumber(value.cognitive_load)
    || !isUnitNumber(value.certainty)
    || !isUnitNumber(value.caution)
    || !isUnitNumber(value.curiosity)
    || !isUnitNumber(value.blockedness)
    || !isUnitNumber(value.social_presence)
  ) return null;
  return {
    updated_at_utc: value.updated_at_utc as string,
    activation: value.activation,
    cognitive_load: value.cognitive_load,
    certainty: value.certainty,
    caution: value.caution,
    curiosity: value.curiosity,
    blockedness: value.blockedness,
    social_presence: value.social_presence,
  };
}

function readFunctionalAffect(value: unknown): FunctionalAffect | null {
  if (!isRecord(value) || !hasExactKeys(value, AFFECT_KEYS)) return null;
  if (
    typeof value.kind !== "string"
    || !AFFECT_KINDS.has(value.kind as FunctionalAffectKind)
    || !isUnitNumber(value.intensity)
    || !isUnitNumber(value.confidence)
    || typeof value.reason_code !== "string"
    || !INNER_STATE_REASON_CODES.has(value.reason_code as InnerStateReasonCode)
    || !Array.isArray(value.evidence_ids)
    || value.evidence_ids.length === 0
    || value.evidence_ids.length > MAX_AFFECT_EVIDENCE_IDS
    || !value.evidence_ids.every((item) => isBoundedText(item, 256))
    || new Set(value.evidence_ids).size !== value.evidence_ids.length
    || parseTimestamp(value.valid_until_utc) === null
    || (value.kind === "satisfied" && value.reason_code !== "goal_verified")
  ) return null;
  return {
    kind: value.kind as FunctionalAffectKind,
    intensity: value.intensity,
    confidence: value.confidence,
    reason_code: value.reason_code as InnerStateReasonCode,
    evidence_ids: [...value.evidence_ids] as string[],
    valid_until_utc: value.valid_until_utc as string,
  };
}

function readPresenceSnapshot(value: unknown): PresenceSnapshot | null {
  if (!isRecord(value) || !hasExactKeys(value, PRESENCE_KEYS)) return null;
  const sinceAt = parseTimestamp(value.since_utc);
  const expiresAt = value.expires_at_utc === null ? null : parseTimestamp(value.expires_at_utc);
  if (
    typeof value.mode !== "string"
    || !ATTENTION_MODES.has(value.mode as AttentionMode)
    || !isUnitNumber(value.intensity)
    || !isOptionalId(value.session_id)
    || !isOptionalId(value.source_observation_id)
    || typeof value.reason_code !== "string"
    || !INNER_STATE_REASON_CODES.has(value.reason_code as InnerStateReasonCode)
    || sinceAt === null
    || (value.expires_at_utc !== null && expiresAt === null)
    || (expiresAt !== null && expiresAt <= sinceAt)
  ) return null;
  return {
    mode: value.mode as AttentionMode,
    intensity: value.intensity,
    session_id: value.session_id,
    source_observation_id: value.source_observation_id,
    reason_code: value.reason_code as InnerStateReasonCode,
    since_utc: value.since_utc as string,
    expires_at_utc: value.expires_at_utc as string | null,
  };
}

function readInnerStateSnapshot(value: unknown): InnerStateSnapshot | null {
  if (!isRecord(value) || !hasExactKeys(value, INNER_STATE_KEYS)) return null;
  const attention = readAttentionSnapshot(value.attention);
  const homeostasis = readHomeostasisSnapshot(value.homeostasis);
  const presence = readPresenceSnapshot(value.presence);
  if (
    value.schema_version !== 1
    || !isNonNegativeInteger(value.source_life_snapshot_revision)
    || !isBoundedText(value.identity_id, 256)
    || !isBoundedText(value.instance_id, 256)
    || parseTimestamp(value.generated_at_utc) === null
    || !isMachineCode(value.phase)
    || !attention
    || !homeostasis
    || !Array.isArray(value.affects)
    || value.affects.length > MAX_INNER_STATE_AFFECTS
    || !presence
    || !isOptionalId(value.last_observation_id)
    || typeof value.degraded !== "boolean"
  ) return null;
  const affects = value.affects.map(readFunctionalAffect);
  if (affects.some((affect) => affect === null)) return null;
  return {
    schema_version: 1,
    source_life_snapshot_revision: value.source_life_snapshot_revision,
    identity_id: value.identity_id,
    instance_id: value.instance_id,
    generated_at_utc: value.generated_at_utc as string,
    phase: value.phase,
    attention,
    homeostasis,
    affects: affects as FunctionalAffect[],
    presence,
    last_observation_id: value.last_observation_id,
    degraded: value.degraded,
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

function cloneInnerState(value: InnerStateSnapshot): InnerStateSnapshot {
  const attention = Object.freeze({ ...value.attention });
  const homeostasis = Object.freeze({ ...value.homeostasis });
  const affects = Object.freeze(value.affects.map((affect) => Object.freeze({
    ...affect,
    evidence_ids: Object.freeze([...affect.evidence_ids]),
  })));
  const presence = Object.freeze({ ...value.presence });
  return Object.freeze({
    ...value,
    attention,
    homeostasis,
    affects,
    presence,
  });
}

export function createLifeStateBridge(options: LifeStateBridgeOptions): LifeStateBridge {
  const now = options.now ?? Date.now;
  const expressionListeners = new Set<ExpressionListener>();
  let currentSnapshot: LifeSnapshot | null = null;
  let currentExpression: ExpressionIntent | null = null;
  let currentInnerState: InnerStateSnapshot | null = null;
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
    if (currentInnerState?.source_life_snapshot_revision !== candidate.revision) {
      currentInnerState = null;
    }
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

  function applyInnerState(payload: unknown): boolean {
    const candidate = readInnerStateSnapshot(payload);
    if (
      !candidate
      || !currentSnapshot
      || candidate.source_life_snapshot_revision !== currentSnapshot.revision
      || candidate.identity_id !== currentSnapshot.identity.identity_id
      || candidate.instance_id !== currentSnapshot.instance.instance_id
      || candidate.source_life_snapshot_revision
        <= (currentInnerState?.source_life_snapshot_revision ?? -1)
    ) return false;
    currentInnerState = cloneInnerState(candidate);
    return true;
  }

  function handle(event: BackendEvent): boolean {
    if (event.type === "life.snapshot") return applySnapshot(event.payload);
    if (event.type === "life.expression") return applyExpression(event.payload);
    if (event.type === "life.inner_state.changed") return applyInnerState(event.payload);
    return false;
  }

  function projectOffline(reason = "\u540e\u7aef\u79bb\u7ebf"): void {
    state = "offline";
    detail = reason.trim() || "\u540e\u7aef\u79bb\u7ebf";
    currentInnerState = null;
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
      innerStateMode: currentInnerState ? "authoritative" : "compatibility",
      snapshot: currentSnapshot ? cloneLifeSnapshot(currentSnapshot) : null,
      expression: currentExpression ? cloneExpression(currentExpression) : null,
      innerState: currentInnerState ? cloneInnerState(currentInnerState) : null,
    };
  }

  function subscribeExpression(listener: ExpressionListener): () => void {
    expressionListeners.add(listener);
    return () => expressionListeners.delete(listener);
  }

  return { handle, projectOffline, snapshot, subscribeExpression };
}
