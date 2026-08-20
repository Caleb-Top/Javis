import assert from "node:assert/strict";
import test from "node:test";

import {
  AVATAR_PERFORMANCE_TIER_ORDER,
  HIGH_FRAME_BUDGET_MS,
  LOW_FRAME_BUDGET_MS,
  PerformanceGovernor,
} from "../src/pet/avatar/PerformanceGovernor.ts";

function record(governor: PerformanceGovernor, count: number, durationMs: number): void {
  for (let index = 0; index < count; index += 1) governor.recordFrame(durationMs);
}

test("declares every governed tier in safe fallback order", () => {
  assert.deepEqual(AVATAR_PERFORMANCE_TIER_ORDER, [
    "3d-high",
    "3d-low",
    "procedural3d",
    "sprite2d",
    "orb",
  ]);
});

test("uses strict high and low frame-budget boundaries", () => {
  const atHighBoundary = new PerformanceGovernor({ initialTier: "3d-high" });
  record(atHighBoundary, 120, HIGH_FRAME_BUDGET_MS);
  assert.equal(atHighBoundary.tier(), "3d-high");

  const high = new PerformanceGovernor({ initialTier: "3d-high" });
  record(high, 89, HIGH_FRAME_BUDGET_MS + 0.001);
  assert.equal(high.tier(), "3d-high");
  const highDecision = high.recordFrame(HIGH_FRAME_BUDGET_MS + 0.001);
  assert.equal(highDecision.action, "downgrade");
  assert.equal(highDecision.tier, "3d-low");

  const atLowBoundary = new PerformanceGovernor({ initialTier: "3d-low" });
  record(atLowBoundary, 120, LOW_FRAME_BUDGET_MS);
  assert.equal(atLowBoundary.tier(), "3d-low");

  const low = new PerformanceGovernor({ initialTier: "3d-low" });
  record(low, 59, LOW_FRAME_BUDGET_MS + 0.001);
  assert.equal(low.tier(), "3d-low");
  const lowDecision = low.recordFrame(LOW_FRAME_BUDGET_MS + 0.001);
  assert.equal(lowDecision.action, "downgrade");
  assert.equal(lowDecision.tier, "procedural3d");
});

test("computes nearest-rank p95 over only the latest 120 samples", () => {
  const governor = new PerformanceGovernor({ initialTier: "procedural3d" });
  record(governor, 114, 10);
  record(governor, 6, 100);
  assert.equal(governor.decision().sampleCount, 120);
  assert.equal(governor.p95(), 10);

  governor.recordFrame(100);
  assert.equal(governor.decision().sampleCount, 120);
  assert.equal(governor.p95(), 100);
});

test("promotes only after 300 qualifying samples with 20 percent headroom", () => {
  const governor = new PerformanceGovernor({ initialTier: "3d-low" });
  const promotionBoundary = HIGH_FRAME_BUDGET_MS * 0.8;
  record(governor, 299, promotionBoundary);
  assert.equal(governor.tier(), "3d-low");
  const decision = governor.recordFrame(promotionBoundary);
  assert.equal(decision.action, "promote");
  assert.equal(decision.tier, "3d-high");

  const outsideBoundary = new PerformanceGovernor({ initialTier: "3d-low" });
  record(outsideBoundary, 400, promotionBoundary + 0.001);
  assert.equal(outsideBoundary.tier(), "3d-low");
});

test("recent failures, reduced motion and forced fallback override promotion", () => {
  const failed = new PerformanceGovernor({ initialTier: "3d-low" });
  failed.reportFailure("context");
  record(failed, 299, 10);
  assert.equal(failed.tier(), "3d-low");
  assert.equal(failed.decision().recentFailure, "context");
  failed.recordFrame(10);
  assert.equal(failed.tier(), "3d-high");

  const loadFailed = new PerformanceGovernor({ initialTier: "3d-low" });
  loadFailed.reportFailure("load");
  record(loadFailed, 299, 10);
  assert.equal(loadFailed.tier(), "3d-low");
  assert.equal(loadFailed.decision().recentFailure, "load");
  loadFailed.recordFrame(10);
  assert.equal(loadFailed.tier(), "3d-high");

  const reduced = new PerformanceGovernor({ initialTier: "3d-high" });
  assert.equal(reduced.setReducedMotion(true).tier, "3d-low");
  record(reduced, 400, 10);
  assert.equal(reduced.tier(), "3d-low");
  reduced.setReducedMotion(false);
  record(reduced, 299, 10);
  assert.equal(reduced.tier(), "3d-low");
  reduced.recordFrame(10);
  assert.equal(reduced.tier(), "3d-high");

  const forced = new PerformanceGovernor({ initialTier: "3d-high" });
  const forcedDecision = forced.forceFallback("sprite2d");
  assert.equal(forcedDecision.action, "forced-fallback");
  assert.equal(forced.tier(), "sprite2d");
  record(forced, 400, 10);
  assert.equal(forced.tier(), "sprite2d");
});

test("returns explicit render suspension decisions and ignores suspended frames", () => {
  const governor = new PerformanceGovernor({ initialTier: "3d-high" });
  const codeOnly = governor.setRenderState({ surface: "code" });
  assert.equal(codeOnly.action, "suspend");
  assert.equal(codeOnly.shouldRender, false);
  assert.equal(codeOnly.renderSuspensionReason, "inactive-surface");
  governor.recordFrame(100);
  assert.equal(governor.decision().sampleCount, 0);

  const live = governor.setRenderState({ surface: "live" });
  assert.equal(live.action, "resume");
  assert.equal(live.shouldRender, true);
  assert.equal(governor.setRenderState({ minimized: true }).renderSuspensionReason, "minimized");
  assert.equal(governor.setRenderState({ minimized: false, visible: false }).renderSuspensionReason, "hidden");
});
