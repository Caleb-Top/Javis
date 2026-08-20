import type { AvatarPerformanceTier } from "./avatarTypes.ts";

export const AVATAR_PERFORMANCE_TIER_ORDER = [
  "3d-high",
  "3d-low",
  "procedural3d",
  "sprite2d",
  "orb",
] as const satisfies readonly AvatarPerformanceTier[];

export const PERFORMANCE_SAMPLE_WINDOW = 120;
export const HIGH_FRAME_BUDGET_MS = 33.3;
export const LOW_FRAME_BUDGET_MS = 50;
export const HIGH_DOWNGRADE_SAMPLE_COUNT = 90;
export const LOW_DOWNGRADE_SAMPLE_COUNT = 60;
export const PROMOTION_SAMPLE_COUNT = 300;
export const PROMOTION_HEADROOM = 0.2;

export type PerformanceFailureKind = "context" | "load";
export type AvatarRenderSurface = "pet" | "live" | "code" | "settings" | "other";
export type RenderSuspensionReason = "hidden" | "minimized" | "inactive-surface";

export type PerformanceGovernorOptions = Readonly<{
  initialTier: AvatarPerformanceTier;
  reducedMotion?: boolean;
  forcedFallback?: AvatarPerformanceTier | null;
}>;

export type AvatarRenderState = Readonly<{
  visible: boolean;
  minimized: boolean;
  surface: AvatarRenderSurface;
}>;

export type PerformanceGovernorAction =
  | "none"
  | "downgrade"
  | "promote"
  | "forced-fallback"
  | "suspend"
  | "resume";

export type PerformanceGovernorReason =
  | "stable"
  | "invalid-frame"
  | "render-suspended"
  | "high-frame-budget"
  | "low-frame-budget"
  | "promotion-hysteresis"
  | "recent-context-failure"
  | "recent-load-failure"
  | "reduced-motion"
  | "forced-fallback"
  | "hidden"
  | "minimized"
  | "inactive-surface"
  | "visible-surface";

export type PerformanceGovernorDecision = Readonly<{
  action: PerformanceGovernorAction;
  reason: PerformanceGovernorReason;
  tier: AvatarPerformanceTier;
  previousTier: AvatarPerformanceTier;
  p95Ms: number | null;
  sampleCount: number;
  slowSampleStreak: number;
  promotionSampleStreak: number;
  recentFailure: PerformanceFailureKind | null;
  shouldRender: boolean;
  renderMode: "continuous" | "suspended";
  renderSuspensionReason: RenderSuspensionReason | null;
}>;

function tierIndex(tier: AvatarPerformanceTier): number {
  return AVATAR_PERFORMANCE_TIER_ORDER.indexOf(tier);
}

function assertTier(tier: AvatarPerformanceTier): void {
  if (tierIndex(tier) < 0) throw new RangeError(`Unknown avatar performance tier: ${tier}`);
}

function percentile95(samples: readonly number[]): number | null {
  if (samples.length === 0) return null;
  const ordered = [...samples].sort((left, right) => left - right);
  const nearestRankIndex = Math.max(0, Math.ceil(ordered.length * 0.95) - 1);
  return ordered[nearestRankIndex];
}

function promotionBudgetFor(tier: AvatarPerformanceTier): number | null {
  const index = tierIndex(tier);
  if (index <= 0) return null;
  const targetTier = AVATAR_PERFORMANCE_TIER_ORDER[index - 1];
  return targetTier === "3d-high" ? HIGH_FRAME_BUDGET_MS : LOW_FRAME_BUDGET_MS;
}

export class PerformanceGovernor {
  private currentTier: AvatarPerformanceTier;
  private readonly frameSamples: number[] = [];
  private slowSampleStreak = 0;
  private promotionSampleStreak = 0;
  private recentFailure: PerformanceFailureKind | null = null;
  private samplesSinceFailure = 0;
  private reducedMotion: boolean;
  private forcedFallback: AvatarPerformanceTier | null;
  private renderState: AvatarRenderState = {
    visible: true,
    minimized: false,
    surface: "pet",
  };

  constructor(options: PerformanceGovernorOptions) {
    assertTier(options.initialTier);
    this.currentTier = options.initialTier;
    this.reducedMotion = options.reducedMotion ?? false;
    this.forcedFallback = options.forcedFallback ?? null;

    if (this.forcedFallback !== null) {
      assertTier(this.forcedFallback);
      if (tierIndex(this.forcedFallback) > tierIndex(this.currentTier)) {
        this.currentTier = this.forcedFallback;
      }
    }
    if (this.reducedMotion && this.currentTier === "3d-high") {
      this.currentTier = "3d-low";
    }
  }

  tier(): AvatarPerformanceTier {
    return this.currentTier;
  }

  p95(): number | null {
    return percentile95(this.frameSamples);
  }

  decision(): PerformanceGovernorDecision {
    return this.createDecision("none", "stable", this.currentTier);
  }

  diagnostics(): PerformanceGovernorDecision {
    return this.decision();
  }

  recordFrame(durationMs: number): PerformanceGovernorDecision {
    if (!Number.isFinite(durationMs) || durationMs < 0) {
      return this.createDecision("none", "invalid-frame", this.currentTier);
    }
    if (!this.shouldRender()) {
      return this.createDecision("none", "render-suspended", this.currentTier);
    }

    this.frameSamples.push(durationMs);
    if (this.frameSamples.length > PERFORMANCE_SAMPLE_WINDOW) this.frameSamples.shift();

    if (this.recentFailure !== null) {
      this.samplesSinceFailure += 1;
      if (this.samplesSinceFailure >= PROMOTION_SAMPLE_COUNT) {
        this.recentFailure = null;
      }
    }

    const previousTier = this.currentTier;
    const p95Ms = this.p95();
    if (p95Ms === null) return this.createDecision("none", "stable", previousTier);

    if (this.currentTier === "3d-high") {
      this.promotionSampleStreak = 0;
      this.slowSampleStreak = p95Ms > HIGH_FRAME_BUDGET_MS
        ? this.slowSampleStreak + 1
        : 0;
      if (this.slowSampleStreak >= HIGH_DOWNGRADE_SAMPLE_COUNT) {
        this.currentTier = "3d-low";
        this.resetTierStreaks();
        return this.createDecision("downgrade", "high-frame-budget", previousTier);
      }
      return this.createDecision("none", "stable", previousTier);
    }

    if (this.currentTier === "3d-low") {
      this.slowSampleStreak = p95Ms > LOW_FRAME_BUDGET_MS
        ? this.slowSampleStreak + 1
        : 0;
      if (this.slowSampleStreak >= LOW_DOWNGRADE_SAMPLE_COUNT) {
        this.currentTier = "procedural3d";
        this.resetTierStreaks();
        return this.createDecision("downgrade", "low-frame-budget", previousTier);
      }
    } else {
      this.slowSampleStreak = 0;
    }

    return this.evaluatePromotion(previousTier, p95Ms);
  }

  reportFailure(kind: PerformanceFailureKind): PerformanceGovernorDecision {
    this.recentFailure = kind;
    this.samplesSinceFailure = 0;
    this.promotionSampleStreak = 0;
    const reason = kind === "context" ? "recent-context-failure" : "recent-load-failure";
    return this.createDecision("none", reason, this.currentTier);
  }

  setReducedMotion(enabled: boolean): PerformanceGovernorDecision {
    const previousTier = this.currentTier;
    if (this.reducedMotion === enabled) {
      return this.createDecision("none", enabled ? "reduced-motion" : "stable", previousTier);
    }

    this.reducedMotion = enabled;
    this.promotionSampleStreak = 0;
    if (enabled && this.currentTier === "3d-high") {
      this.currentTier = "3d-low";
      this.resetTierStreaks();
      return this.createDecision("downgrade", "reduced-motion", previousTier);
    }
    return this.createDecision("none", enabled ? "reduced-motion" : "stable", previousTier);
  }

  forceFallback(tier: AvatarPerformanceTier): PerformanceGovernorDecision {
    assertTier(tier);
    const previousTier = this.currentTier;
    this.forcedFallback = tier;
    this.promotionSampleStreak = 0;
    if (tierIndex(tier) > tierIndex(this.currentTier)) {
      this.currentTier = tier;
      this.resetTierStreaks();
      return this.createDecision("forced-fallback", "forced-fallback", previousTier);
    }
    return this.createDecision("none", "forced-fallback", previousTier);
  }

  clearForcedFallback(): PerformanceGovernorDecision {
    this.forcedFallback = null;
    this.promotionSampleStreak = 0;
    return this.createDecision("none", "stable", this.currentTier);
  }

  setRenderState(update: Partial<AvatarRenderState>): PerformanceGovernorDecision {
    const renderedBefore = this.shouldRender();
    this.renderState = { ...this.renderState, ...update };
    const renderedAfter = this.shouldRender();
    if (renderedBefore && !renderedAfter) {
      const reason = this.renderSuspensionReason() ?? "inactive-surface";
      return this.createDecision("suspend", reason, this.currentTier);
    }
    if (!renderedBefore && renderedAfter) {
      return this.createDecision("resume", "visible-surface", this.currentTier);
    }
    const reason = renderedAfter ? "stable" : this.renderSuspensionReason() ?? "inactive-surface";
    return this.createDecision("none", reason, this.currentTier);
  }

  private evaluatePromotion(
    previousTier: AvatarPerformanceTier,
    p95Ms: number,
  ): PerformanceGovernorDecision {
    const targetBudget = promotionBudgetFor(this.currentTier);
    const qualifies = targetBudget !== null
      && p95Ms <= targetBudget * (1 - PROMOTION_HEADROOM);

    if (!qualifies) {
      this.promotionSampleStreak = 0;
      return this.createDecision("none", "stable", previousTier);
    }
    if (this.reducedMotion) {
      this.promotionSampleStreak = 0;
      return this.createDecision("none", "reduced-motion", previousTier);
    }
    if (this.forcedFallback !== null) {
      this.promotionSampleStreak = 0;
      return this.createDecision("none", "forced-fallback", previousTier);
    }

    this.promotionSampleStreak += 1;
    if (this.promotionSampleStreak < PROMOTION_SAMPLE_COUNT) {
      const reason = this.recentFailure === "context"
        ? "recent-context-failure"
        : this.recentFailure === "load"
          ? "recent-load-failure"
          : "promotion-hysteresis";
      return this.createDecision("none", reason, previousTier);
    }
    if (this.recentFailure !== null) {
      const reason = this.recentFailure === "context"
        ? "recent-context-failure"
        : "recent-load-failure";
      return this.createDecision("none", reason, previousTier);
    }

    const currentIndex = tierIndex(this.currentTier);
    if (currentIndex <= 0) return this.createDecision("none", "stable", previousTier);
    this.currentTier = AVATAR_PERFORMANCE_TIER_ORDER[currentIndex - 1];
    this.resetTierStreaks();
    return this.createDecision("promote", "promotion-hysteresis", previousTier);
  }

  private resetTierStreaks(): void {
    this.slowSampleStreak = 0;
    this.promotionSampleStreak = 0;
  }

  private shouldRender(): boolean {
    return this.renderSuspensionReason() === null;
  }

  private renderSuspensionReason(): RenderSuspensionReason | null {
    if (!this.renderState.visible) return "hidden";
    if (this.renderState.minimized) return "minimized";
    if (this.renderState.surface !== "pet" && this.renderState.surface !== "live") {
      return "inactive-surface";
    }
    return null;
  }

  private createDecision(
    action: PerformanceGovernorAction,
    reason: PerformanceGovernorReason,
    previousTier: AvatarPerformanceTier,
  ): PerformanceGovernorDecision {
    const suspensionReason = this.renderSuspensionReason();
    return Object.freeze({
      action,
      reason,
      tier: this.currentTier,
      previousTier,
      p95Ms: this.p95(),
      sampleCount: this.frameSamples.length,
      slowSampleStreak: this.slowSampleStreak,
      promotionSampleStreak: this.promotionSampleStreak,
      recentFailure: this.recentFailure,
      shouldRender: suspensionReason === null,
      renderMode: suspensionReason === null ? "continuous" : "suspended",
      renderSuspensionReason: suspensionReason,
    });
  }
}
