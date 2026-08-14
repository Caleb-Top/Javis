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
  snapshot: LifeSnapshot | null;
  expression: ExpressionIntent | null;
};

export type ExpressionListener = (intent: ExpressionIntent) => void;
