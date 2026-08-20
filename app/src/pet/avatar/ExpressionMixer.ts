import type {
  AvatarBlinkRate,
  AvatarCapability,
  AvatarColorIntent,
  AvatarGesture,
  AvatarPosture,
  ExpressionTarget,
} from "./avatarTypes.ts";
import type { ExpressionBaseState, GazeTarget } from "../../life/lifeTypes.ts";

export type ExpressionMixerSnapshot = Readonly<{
  state: ExpressionBaseState;
  gaze: GazeTarget;
  mouth: number;
  blinkRate: AvatarBlinkRate;
  blinkEnabled: boolean;
  posture: AvatarPosture;
  color: AvatarColorIntent;
  gesture: AvatarGesture;
  interrupt: boolean;
}>;

const NEUTRAL: ExpressionMixerSnapshot = {
  state: "idle",
  gaze: "none",
  mouth: 0,
  blinkRate: "normal",
  blinkEnabled: true,
  posture: "neutral",
  color: "cyan",
  gesture: "none",
  interrupt: false,
};

function capabilitySet(capabilities: readonly string[] | Readonly<Partial<Record<AvatarCapability, boolean>>>): Set<string> {
  if (Array.isArray(capabilities)) return new Set(capabilities);
  return new Set(Object.entries(capabilities).filter(([, enabled]) => enabled).map(([name]) => name));
}

export class ExpressionMixer {
  readonly #capabilities: Set<string>;
  #current: ExpressionMixerSnapshot = { ...NEUTRAL };
  #target: ExpressionMixerSnapshot = { ...NEUTRAL };
  #transitionFromMouth = 0;
  #transitionStartedAt = 0;
  #interrupted = false;

  constructor(capabilities: readonly string[] | Readonly<Partial<Record<AvatarCapability, boolean>>>) {
    this.#capabilities = capabilitySet(capabilities);
  }

  submit(target: ExpressionTarget): void {
    if (target.interrupt) this.interrupt();
    this.#interrupted = false;
    this.#transitionFromMouth = this.#current.mouth;
    this.#transitionStartedAt = 0;
    this.#target = {
      state: target.state,
      gaze: this.#capabilities.has("gaze") ? target.gaze : "none",
      mouth: this.#capabilities.has("mouth") ? target.mouth : 0,
      blinkRate: target.blinkRate,
      blinkEnabled: this.#capabilities.has("blink") && target.blinkRate !== "off",
      posture: target.posture,
      color: target.color,
      gesture: target.gesture,
      interrupt: target.interrupt,
    };
    this.#current = {
      ...this.#target,
      mouth: this.#target.mouth > 0 ? Math.min(this.#target.mouth, 0.01) : 0,
    };
  }

  tick(now: number): ExpressionMixerSnapshot {
    if (this.#interrupted) return this.snapshot();
    const safeNow = Number.isFinite(now) ? Math.max(0, now) : 0;
    if (this.#transitionStartedAt === 0) this.#transitionStartedAt = Math.max(0, safeNow - 16);
    const progress = Math.min(1, Math.max(0, (safeNow - this.#transitionStartedAt) / 180));
    const mouth = this.#transitionFromMouth + (this.#target.mouth - this.#transitionFromMouth) * progress;
    this.#current = { ...this.#target, mouth: Math.min(0.65, Math.max(0, mouth)) };
    return this.snapshot();
  }

  interrupt(): void {
    this.#interrupted = true;
    this.#target = { ...this.#target, mouth: 0, gesture: "none", interrupt: true };
    this.#current = { ...this.#current, mouth: 0, gesture: "none", interrupt: true };
    this.#transitionFromMouth = 0;
  }

  setSpeakingLevel(level: number): void {
    if (this.#interrupted || this.#target.state !== "speaking") {
      this.#current = { ...this.#current, mouth: 0 };
      return;
    }
    const safeLevel = Number.isFinite(level) ? Math.min(0.65, Math.max(0, level)) : 0;
    this.#current = { ...this.#current, mouth: safeLevel };
  }

  snapshot(): ExpressionMixerSnapshot {
    return { ...this.#current };
  }
}
