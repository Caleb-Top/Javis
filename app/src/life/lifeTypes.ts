import type { LiveState } from "../live/liveState.ts";

export type LifeCycleState =
  | "booting"
  | "awake"
  | "quiet"
  | "engaged"
  | "degraded"
  | "recovering"
  | "stopping"
  | "offline";

export type LifeActivity =
  | "idle"
  | "attention"
  | "quiet"
  | "listening"
  | "thinking"
  | "speaking"
  | "executing"
  | "blocked"
  | "error"
  | "offline";

export type ExpressionBaseState = Exclude<LifeActivity, "quiet">;
export type GazeTarget = "none" | "user" | "content" | "task";
export type VoiceActivity = "silent" | "listening" | "speaking";

export type AttentionMode =
  | "idle"
  | "present"
  | "listening"
  | "engaged"
  | "speaking"
  | "awaiting_approval"
  | "blocked"
  | "recovering";

export type FunctionalAffectKind =
  | "curious"
  | "cautious"
  | "blocked"
  | "relieved"
  | "satisfied";

export type InnerStateReasonCode =
  | "quiet_baseline"
  | "user_invoked"
  | "voice_listening_started"
  | "voice_listening_stopped"
  | "request_started"
  | "request_activity"
  | "approval_required"
  | "approval_approved"
  | "approval_denied"
  | "tool_started"
  | "tool_risk_observed"
  | "tool_completed"
  | "request_completed"
  | "request_failed"
  | "request_cancelled"
  | "speech_started"
  | "speech_stopped"
  | "interaction_interrupted"
  | "goal_verified"
  | "runtime_degraded"
  | "runtime_recovered"
  | "load_reduced"
  | "blocked_by_failure";

export type AttentionSnapshot = Readonly<{
  mode: AttentionMode;
  target_kind: string | null;
  target_id: string | null;
  priority: number;
  since_utc: string;
  expires_at_utc: string | null;
  source_observation_id: string | null;
}>;

export type HomeostasisSnapshot = Readonly<{
  updated_at_utc: string;
  activation: number;
  cognitive_load: number;
  certainty: number;
  caution: number;
  curiosity: number;
  blockedness: number;
  social_presence: number;
}>;

export type FunctionalAffect = Readonly<{
  kind: FunctionalAffectKind;
  intensity: number;
  confidence: number;
  reason_code: InnerStateReasonCode;
  evidence_ids: readonly string[];
  valid_until_utc: string;
}>;

export type PresenceSnapshot = Readonly<{
  mode: AttentionMode;
  intensity: number;
  session_id: string | null;
  source_observation_id: string | null;
  reason_code: InnerStateReasonCode;
  since_utc: string;
  expires_at_utc: string | null;
}>;

export type InnerStateSnapshot = Readonly<{
  schema_version: 1;
  source_life_snapshot_revision: number;
  identity_id: string;
  instance_id: string;
  generated_at_utc: string;
  phase: string;
  attention: AttentionSnapshot;
  homeostasis: HomeostasisSnapshot;
  affects: readonly FunctionalAffect[];
  presence: PresenceSnapshot;
  last_observation_id: string | null;
  degraded: boolean;
}>;

export type InnerStateMode = "authoritative" | "compatibility";

export type IdentitySummary = {
  identity_id: string;
  name: string;
  kind: string;
  relationship_role: string;
  version: number;
  content_hash: string;
};

export type InstanceSummary = {
  instance_id: string;
  lineage_id: string;
  parent_instance_id: string | null;
  generation: number;
  fork_pending_review: boolean;
};

export type HealthSummary = {
  status: string;
  degraded_components: string[];
  reason_codes: string[];
};

export type LifeSnapshot = {
  schema_version: 1;
  revision: number;
  identity: IdentitySummary;
  instance: InstanceSummary;
  lifecycle_state: LifeCycleState;
  active_session_id: string | null;
  active_request_id: string | null;
  activity: LifeActivity;
  health: HealthSummary;
  degradation_level: number;
  recovery_required: boolean;
  last_event_id: string | null;
  last_sequence: number;
  updated_at: string;
  explanation: string;
};

export type ExpressionIntent = {
  schema_version: 1;
  revision: number;
  base_state: ExpressionBaseState;
  intensity: number;
  gaze_target: GazeTarget;
  voice_activity: VoiceActivity;
  transition_ms: number;
  interrupt: boolean;
  source_snapshot_revision: number;
  generated_at: string;
  expires_at: string;
  explanation_code: string;
};

export type LifeBridgeSnapshot = {
  state: LiveState;
  detail: string;
  snapshotRevision: number;
  expressionRevision: number;
  innerStateMode: InnerStateMode;
  snapshot: LifeSnapshot | null;
  expression: ExpressionIntent | null;
  innerState: InnerStateSnapshot | null;
};

export type ExpressionListener = (intent: ExpressionIntent) => void;
