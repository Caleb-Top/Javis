import type {
  ExpressionBaseState,
  ExpressionIntent,
  GazeTarget,
} from "../../life/lifeTypes.ts";
import type { ExpressionTarget } from "./avatarTypes.ts";

const STATE_TARGETS: Record<ExpressionBaseState, Omit<ExpressionTarget, "state" | "gaze" | "mouth" | "interrupt">> = {
  idle: { blinkRate: "normal", posture: "neutral", color: "cyan", gesture: "none" },
  attention: { blinkRate: "normal", posture: "attentive", color: "cyan", gesture: "none" },
  listening: { blinkRate: "normal", posture: "attentive", color: "cyan", gesture: "none" },
  thinking: { blinkRate: "slow", posture: "focused", color: "violet", gesture: "none" },
  speaking: { blinkRate: "normal", posture: "attentive", color: "cyan", gesture: "none" },
  executing: { blinkRate: "normal", posture: "active", color: "cyan", gesture: "working" },
  blocked: { blinkRate: "slow", posture: "cautious", color: "amber", gesture: "none" },
  error: { blinkRate: "slow", posture: "retracted", color: "red", gesture: "none" },
  offline: { blinkRate: "off", posture: "neutral", color: "dim", gesture: "none" },
};

function stateGaze(intent: ExpressionIntent): GazeTarget {
  if (intent.base_state === "listening" || intent.base_state === "speaking") return "user";
  if (intent.base_state === "thinking" && intent.gaze_target === "none") return "content";
  if (intent.base_state === "executing" && intent.gaze_target === "none") return "task";
  return intent.gaze_target;
}

export function mapExpressionIntent(intent: ExpressionIntent): ExpressionTarget {
  const state = intent.base_state;
  const base = STATE_TARGETS[state] ?? STATE_TARGETS.idle;
  const speaking = state === "speaking" && intent.voice_activity === "speaking";
  const mouth = speaking ? Math.min(0.25, Math.max(0.08, intent.intensity * 0.25)) : 0;

  return {
    state,
    gaze: stateGaze(intent),
    mouth,
    blinkRate: base.blinkRate,
    posture: base.posture,
    color: base.color,
    gesture: intent.interrupt ? "none" : base.gesture,
    interrupt: intent.interrupt,
  };
}
