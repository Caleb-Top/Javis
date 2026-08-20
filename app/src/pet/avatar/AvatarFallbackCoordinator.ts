import type { ExpressionIntent } from "../../life/lifeTypes.ts";
import type { AvatarPerformanceTier } from "./avatarTypes.ts";

export const DEFAULT_AVATAR_FALLBACK_ORDER = [
  "3d-high",
  "3d-low",
  "procedural3d",
  "sprite2d",
  "orb",
] as const satisfies readonly AvatarPerformanceTier[];

export type AvatarFallbackFailure =
  | "context-lost"
  | "context-recovery-failed"
  | "load-failed"
  | "forced-fallback";

export type AvatarFallbackStatus =
  | "ready"
  | "degraded"
  | "context-lost"
  | "recovering"
  | "reloading"
  | "recovery-exhausted";

export type AvatarFallbackAction =
  | "none"
  | "degrade"
  | "attempt-context-recovery"
  | "reload-assets"
  | "restore"
  | "stay-degraded"
  | "manual-retry";

export type AvatarFallbackCoordinatorOptions = Readonly<{
  initialTier: AvatarPerformanceTier;
  fallbackOrder?: readonly AvatarPerformanceTier[];
  maxRecoveryAttempts?: number;
  now?: () => number;
}>;

export type AvatarFallbackDecision = Readonly<{
  action: AvatarFallbackAction;
  reason: AvatarFallbackFailure | "context-restored" | "recovery-succeeded" | "stable";
  tier: AvatarPerformanceTier;
  previousTier: AvatarPerformanceTier;
  recoveryTarget: AvatarPerformanceTier;
  status: AvatarFallbackStatus;
  recoveryAttempt: number;
  maxRecoveryAttempts: number;
  canAttemptRecovery: boolean;
  manualRetryRequired: boolean;
  shouldRender3d: boolean;
  fallbackVisible: boolean;
  reloadAssets: boolean;
  expressionIntent: ExpressionIntent | null;
}>;

export type AvatarFallbackDiagnostics = Readonly<{
  tier: AvatarPerformanceTier;
  recoveryTarget: AvatarPerformanceTier;
  status: AvatarFallbackStatus;
  recoveryAttempt: number;
  maxRecoveryAttempts: number;
  latestIntentRevision: number;
  latestIntentExpiresAt: string | null;
}>;

const EXPRESSION_STATES = new Set([
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
const GAZE_TARGETS = new Set(["none", "user", "content", "task"]);
const VOICE_ACTIVITIES = new Set(["silent", "listening", "speaking"]);

function cloneIntent(intent: ExpressionIntent): ExpressionIntent {
  return { ...intent };
}

function isNonNegativeInteger(value: number): boolean {
  return Number.isInteger(value) && value >= 0;
}

function isIntentShapeValid(intent: ExpressionIntent): boolean {
  const generatedAt = Date.parse(intent.generated_at);
  const expiresAt = Date.parse(intent.expires_at);
  return intent.schema_version === 1
    && isNonNegativeInteger(intent.revision)
    && EXPRESSION_STATES.has(intent.base_state)
    && Number.isFinite(intent.intensity)
    && intent.intensity >= 0
    && intent.intensity <= 1
    && GAZE_TARGETS.has(intent.gaze_target)
    && VOICE_ACTIVITIES.has(intent.voice_activity)
    && isNonNegativeInteger(intent.transition_ms)
    && typeof intent.interrupt === "boolean"
    && isNonNegativeInteger(intent.source_snapshot_revision)
    && Number.isFinite(generatedAt)
    && Number.isFinite(expiresAt)
    && expiresAt > generatedAt
    && typeof intent.explanation_code === "string";
}

function is3dTier(tier: AvatarPerformanceTier): boolean {
  return tier === "3d-high" || tier === "3d-low" || tier === "procedural3d";
}

export class AvatarFallbackCoordinator {
  private current: AvatarPerformanceTier;
  private recoveryTarget: AvatarPerformanceTier;
  private readonly fallbackOrder: readonly AvatarPerformanceTier[];
  private readonly maxRecoveryAttempts: number;
  private readonly now: () => number;
  private status: AvatarFallbackStatus = "ready";
  private recoveryAttempt = 0;
  private latestIntent: ExpressionIntent | null = null;
  private latestIntentRevision = -1;

  constructor(options: AvatarFallbackCoordinatorOptions) {
    this.fallbackOrder = Object.freeze([
      ...(options.fallbackOrder ?? DEFAULT_AVATAR_FALLBACK_ORDER),
    ]);
    this.assertFallbackOrder();
    if (!this.fallbackOrder.includes(options.initialTier)) {
      throw new RangeError(`Initial tier is absent from fallback order: ${options.initialTier}`);
    }

    const attempts = options.maxRecoveryAttempts ?? 2;
    if (!Number.isInteger(attempts) || attempts < 1) {
      throw new RangeError("maxRecoveryAttempts must be a positive integer");
    }

    this.current = options.initialTier;
    this.recoveryTarget = options.initialTier;
    this.maxRecoveryAttempts = attempts;
    this.now = options.now ?? Date.now;
  }

  currentTier(): AvatarPerformanceTier {
    return this.current;
  }

  expression(): ExpressionIntent | null {
    if (this.latestIntent === null) return null;
    const expiresAt = Date.parse(this.latestIntent.expires_at);
    if (!Number.isFinite(expiresAt) || expiresAt <= this.now()) return null;
    return cloneIntent(this.latestIntent);
  }

  acceptIntent(intent: ExpressionIntent): boolean {
    if (!isIntentShapeValid(intent) || intent.revision <= this.latestIntentRevision) return false;
    this.latestIntent = cloneIntent(intent);
    this.latestIntentRevision = intent.revision;
    return true;
  }

  onContextLost(intent?: ExpressionIntent): AvatarFallbackDecision {
    if (intent) this.acceptIntent(intent);
    const previousTier = this.current;
    const wasHandlingContext = this.status === "context-lost"
      || this.status === "recovering"
      || this.status === "reloading"
      || this.status === "recovery-exhausted";
    if (!wasHandlingContext) this.recoveryAttempt = 0;

    if (is3dTier(this.current)) {
      this.recoveryTarget = this.current;
      this.current = this.firstNon3dFallback();
    }
    this.status = this.recoveryAttempt >= this.maxRecoveryAttempts
      ? "recovery-exhausted"
      : "context-lost";
    const action = this.current === previousTier ? "stay-degraded" : "degrade";
    return this.createDecision(action, "context-lost", previousTier);
  }

  onLoadFailure(): AvatarFallbackDecision {
    const previousTier = this.current;
    if (is3dTier(this.current) && this.status === "ready") this.recoveryTarget = this.current;
    this.current = this.nextFallbackTier();
    if (!this.isHandlingContext()) this.status = "degraded";
    const action = this.current === previousTier ? "stay-degraded" : "degrade";
    return this.createDecision(action, "load-failed", previousTier);
  }

  forceFallback(tier: AvatarPerformanceTier): AvatarFallbackDecision {
    const previousTier = this.current;
    const currentIndex = this.fallbackOrder.indexOf(this.current);
    const requestedIndex = this.fallbackOrder.indexOf(tier);
    if (requestedIndex < 0) throw new RangeError(`Tier is absent from fallback order: ${tier}`);
    if (requestedIndex > currentIndex) {
      this.current = tier;
      if (!this.isHandlingContext()) this.status = "degraded";
      return this.createDecision("degrade", "forced-fallback", previousTier);
    }
    return this.createDecision("stay-degraded", "forced-fallback", previousTier);
  }

  requestContextRecovery(): AvatarFallbackDecision {
    const previousTier = this.current;
    if (this.status === "recovery-exhausted" || this.recoveryAttempt >= this.maxRecoveryAttempts) {
      this.status = "recovery-exhausted";
      return this.createDecision("stay-degraded", "context-recovery-failed", previousTier);
    }
    if (this.status === "recovering" || this.status === "reloading" || this.status === "ready") {
      return this.createDecision("none", "stable", previousTier);
    }

    this.recoveryAttempt += 1;
    this.status = "recovering";
    return this.createDecision("attempt-context-recovery", "context-lost", previousTier);
  }

  onContextRestored(intent?: ExpressionIntent): AvatarFallbackDecision {
    if (intent) this.acceptIntent(intent);
    const previousTier = this.current;
    if (this.status === "recovery-exhausted") {
      return this.createDecision("stay-degraded", "context-recovery-failed", previousTier);
    }
    if (this.status === "ready" || this.status === "reloading") {
      return this.createDecision("none", "stable", previousTier);
    }
    if (this.status !== "recovering") {
      if (this.recoveryAttempt >= this.maxRecoveryAttempts) {
        this.status = "recovery-exhausted";
        return this.createDecision("stay-degraded", "context-recovery-failed", previousTier);
      }
      this.recoveryAttempt += 1;
    }

    this.status = "reloading";
    return this.createDecision("reload-assets", "context-restored", previousTier);
  }

  onRecoveryFailed(): AvatarFallbackDecision {
    const previousTier = this.current;
    this.status = this.recoveryAttempt >= this.maxRecoveryAttempts
      ? "recovery-exhausted"
      : "context-lost";
    return this.createDecision("stay-degraded", "context-recovery-failed", previousTier);
  }

  onRecoverySucceeded(intent?: ExpressionIntent): AvatarFallbackDecision {
    if (intent) this.acceptIntent(intent);
    const previousTier = this.current;
    if (this.status !== "recovering" && this.status !== "reloading") {
      return this.createDecision("none", "stable", previousTier);
    }

    const targetIndex = this.fallbackOrder.indexOf(this.recoveryTarget);
    const currentIndex = this.fallbackOrder.indexOf(this.current);
    if (targetIndex <= currentIndex) this.current = this.recoveryTarget;
    this.status = "ready";
    return this.createDecision("restore", "recovery-succeeded", previousTier, this.expression());
  }

  manualRetry(target: AvatarPerformanceTier = this.recoveryTarget): AvatarFallbackDecision {
    const previousTier = this.current;
    const targetIndex = this.fallbackOrder.indexOf(target);
    const currentIndex = this.fallbackOrder.indexOf(this.current);
    if (targetIndex < 0) throw new RangeError(`Tier is absent from fallback order: ${target}`);
    if (targetIndex >= currentIndex) {
      return this.createDecision("stay-degraded", "context-recovery-failed", previousTier);
    }

    this.recoveryTarget = target;
    this.recoveryAttempt = 1;
    this.status = "recovering";
    return this.createDecision("manual-retry", "context-lost", previousTier);
  }

  diagnostics(): AvatarFallbackDiagnostics {
    return Object.freeze({
      tier: this.current,
      recoveryTarget: this.recoveryTarget,
      status: this.status,
      recoveryAttempt: this.recoveryAttempt,
      maxRecoveryAttempts: this.maxRecoveryAttempts,
      latestIntentRevision: this.latestIntentRevision,
      latestIntentExpiresAt: this.latestIntent?.expires_at ?? null,
    });
  }

  private assertFallbackOrder(): void {
    if (
      this.fallbackOrder.length !== DEFAULT_AVATAR_FALLBACK_ORDER.length
      || new Set(this.fallbackOrder).size !== DEFAULT_AVATAR_FALLBACK_ORDER.length
      || DEFAULT_AVATAR_FALLBACK_ORDER.some((tier) => !this.fallbackOrder.includes(tier))
    ) {
      throw new RangeError("fallbackOrder must contain each avatar performance tier exactly once");
    }
  }

  private firstNon3dFallback(): AvatarPerformanceTier {
    const currentIndex = this.fallbackOrder.indexOf(this.current);
    return this.fallbackOrder.find((tier, index) => index > currentIndex && !is3dTier(tier))
      ?? this.fallbackOrder[this.fallbackOrder.length - 1];
  }

  private nextFallbackTier(): AvatarPerformanceTier {
    const currentIndex = this.fallbackOrder.indexOf(this.current);
    return this.fallbackOrder[Math.min(currentIndex + 1, this.fallbackOrder.length - 1)];
  }

  private isHandlingContext(): boolean {
    return this.status === "context-lost"
      || this.status === "recovering"
      || this.status === "reloading"
      || this.status === "recovery-exhausted";
  }

  private createDecision(
    action: AvatarFallbackAction,
    reason: AvatarFallbackDecision["reason"],
    previousTier: AvatarPerformanceTier,
    expressionIntent: ExpressionIntent | null = null,
  ): AvatarFallbackDecision {
    const exhausted = this.status === "recovery-exhausted";
    return Object.freeze({
      action,
      reason,
      tier: this.current,
      previousTier,
      recoveryTarget: this.recoveryTarget,
      status: this.status,
      recoveryAttempt: this.recoveryAttempt,
      maxRecoveryAttempts: this.maxRecoveryAttempts,
      canAttemptRecovery: this.status === "context-lost"
        && this.recoveryAttempt < this.maxRecoveryAttempts,
      manualRetryRequired: exhausted,
      shouldRender3d: is3dTier(this.current) && this.status === "ready",
      fallbackVisible: !is3dTier(this.current) || this.status !== "ready",
      reloadAssets: action === "reload-assets",
      expressionIntent: expressionIntent === null ? null : cloneIntent(expressionIntent),
    });
  }
}
