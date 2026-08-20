import assert from "node:assert/strict";
import test from "node:test";

import {
  AvatarFallbackCoordinator,
  DEFAULT_AVATAR_FALLBACK_ORDER,
} from "../src/pet/avatar/AvatarFallbackCoordinator.ts";
import { expiredSpeakingIntentFixture, intentFixture } from "./avatarTestFakes.ts";

const NOW = Date.parse("2026-08-20T10:00:00.000Z");

test("degrades in one safe direction across every fallback tier", () => {
  assert.deepEqual(DEFAULT_AVATAR_FALLBACK_ORDER, [
    "3d-high",
    "3d-low",
    "procedural3d",
    "sprite2d",
    "orb",
  ]);
  const fallback = new AvatarFallbackCoordinator({ initialTier: "3d-high", now: () => NOW });

  assert.equal(fallback.onLoadFailure().tier, "3d-low");
  assert.equal(fallback.onLoadFailure().tier, "procedural3d");
  assert.equal(fallback.onLoadFailure().tier, "sprite2d");
  assert.equal(fallback.onLoadFailure().tier, "orb");
  assert.equal(fallback.onLoadFailure().tier, "orb");
  assert.equal(fallback.forceFallback("3d-high").tier, "orb");
});

test("context loss stops 3d and context restoration requests an asset reload", () => {
  const fallback = new AvatarFallbackCoordinator({
    initialTier: "3d-high",
    fallbackOrder: DEFAULT_AVATAR_FALLBACK_ORDER,
    maxRecoveryAttempts: 2,
    now: () => NOW,
  });
  fallback.acceptIntent(intentFixture("listening", {
    revision: 2,
    generated_at: "2026-08-20T09:59:00.000Z",
    expires_at: "2026-08-20T10:01:00.000Z",
  }));

  const lost = fallback.onContextLost();
  assert.equal(lost.action, "degrade");
  assert.equal(lost.tier, "sprite2d");
  assert.equal(lost.shouldRender3d, false);
  assert.equal(lost.fallbackVisible, true);

  const restored = fallback.onContextRestored(expiredSpeakingIntentFixture({ revision: 3 }));
  assert.equal(restored.action, "reload-assets");
  assert.equal(restored.reloadAssets, true);
  assert.equal(restored.tier, "sprite2d");
  assert.equal(fallback.expression(), null);

  const recovered = fallback.onRecoverySucceeded();
  assert.equal(recovered.action, "restore");
  assert.equal(recovered.tier, "3d-high");
  assert.equal(recovered.expressionIntent, null);
});

test("successful recovery applies only the newest unexpired intent", () => {
  const fallback = new AvatarFallbackCoordinator({ initialTier: "3d-low", now: () => NOW });
  fallback.acceptIntent(intentFixture("thinking", {
    revision: 4,
    generated_at: "2026-08-20T09:59:00.000Z",
    expires_at: "2026-08-20T10:01:00.000Z",
  }));
  assert.equal(fallback.acceptIntent(intentFixture("idle", { revision: 3 })), false);

  fallback.onContextLost();
  fallback.requestContextRecovery();
  fallback.onContextRestored(intentFixture("listening", {
    revision: 5,
    generated_at: "2026-08-20T09:59:30.000Z",
    expires_at: "2026-08-20T10:02:00.000Z",
  }));
  const recovered = fallback.onRecoverySucceeded();
  assert.equal(recovered.tier, "3d-low");
  assert.equal(recovered.expressionIntent?.revision, 5);
  assert.equal(recovered.expressionIntent?.base_state, "listening");
});

test("bounds repeated context recovery and requires manual retry after exhaustion", () => {
  const fallback = new AvatarFallbackCoordinator({
    initialTier: "procedural3d",
    maxRecoveryAttempts: 2,
    now: () => NOW,
  });
  fallback.onContextLost();

  assert.equal(fallback.requestContextRecovery().recoveryAttempt, 1);
  const firstFailure = fallback.onRecoveryFailed();
  assert.equal(firstFailure.canAttemptRecovery, true);
  assert.equal(firstFailure.manualRetryRequired, false);

  assert.equal(fallback.requestContextRecovery().recoveryAttempt, 2);
  const exhausted = fallback.onRecoveryFailed();
  assert.equal(exhausted.status, "recovery-exhausted");
  assert.equal(exhausted.canAttemptRecovery, false);
  assert.equal(exhausted.manualRetryRequired, true);
  assert.equal(fallback.requestContextRecovery().action, "stay-degraded");
  assert.equal(fallback.currentTier(), "sprite2d");

  const retry = fallback.manualRetry();
  assert.equal(retry.action, "manual-retry");
  assert.equal(retry.recoveryAttempt, 1);
  assert.equal(retry.tier, "sprite2d");
  fallback.onContextRestored();
  assert.equal(fallback.onRecoverySucceeded().tier, "procedural3d");
});

test("an intent that expires while degraded is never replayed", () => {
  let now = NOW;
  const fallback = new AvatarFallbackCoordinator({ initialTier: "3d-high", now: () => now });
  fallback.acceptIntent(intentFixture("speaking", {
    revision: 8,
    generated_at: "2026-08-20T09:59:59.000Z",
    expires_at: "2026-08-20T10:00:01.000Z",
  }));
  fallback.onContextLost();
  fallback.requestContextRecovery();
  fallback.onContextRestored();
  now = Date.parse("2026-08-20T10:00:02.000Z");

  const recovered = fallback.onRecoverySucceeded();
  assert.equal(recovered.expressionIntent, null);
  assert.equal(fallback.expression(), null);
  assert.equal(fallback.diagnostics().latestIntentRevision, 8);
});
