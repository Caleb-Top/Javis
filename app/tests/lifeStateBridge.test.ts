import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createLifeStateBridge } from "../src/life/LifeStateBridge.ts";


type LifeSurfaceState =
  | "idle"
  | "attention"
  | "listening"
  | "thinking"
  | "speaking"
  | "executing"
  | "blocked"
  | "error"
  | "offline";


function lifeSnapshotEvent(revision: number, activity: LifeSurfaceState | "quiet") {
  return {
    type: "life.snapshot",
    payload: {
      schema_version: 1,
      revision,
      identity: {
        identity_id: "identity-1",
        name: "Javis",
        kind: "local_digital_life",
        relationship_role: "partner",
        version: 1,
        content_hash: "a".repeat(64),
      },
      instance: {
        instance_id: "instance-1",
        lineage_id: "lineage-1",
        parent_instance_id: null,
        generation: 0,
        fork_pending_review: false,
      },
      lifecycle_state: activity === "offline" ? "degraded" : "awake",
      active_session_id: "session-1",
      active_request_id: null,
      activity,
      health: {
        status: activity === "error" ? "degraded" : "healthy",
        degraded_components: activity === "error" ? ["test"] : [],
        reason_codes: activity === "error" ? ["test_error"] : [],
      },
      degradation_level: activity === "offline" ? 1 : 0,
      recovery_required: activity === "offline",
      last_event_id: `life-${revision}`,
      last_sequence: revision,
      updated_at: `2026-08-12T00:00:0${revision}.000Z`,
      explanation: "test-fixture",
    },
  } as const;
}


function lifeExpressionEvent(
  revision: number,
  overrides: Record<string, unknown> = {},
) {
  return {
    type: "life.expression",
    payload: {
      schema_version: 1,
      revision,
      base_state: "thinking",
      intensity: 0.6,
      gaze_target: "content",
      voice_activity: "silent",
      transition_ms: 180,
      interrupt: false,
      source_snapshot_revision: 3,
      generated_at: "2026-08-12T00:00:03.000Z",
      expires_at: "2026-08-12T00:00:05.000Z",
      explanation_code: "activity.thinking",
      ...overrides,
    },
  };
}


function innerStateEvent(
  revision: number,
  overrides: Record<string, unknown> = {},
) {
  return {
    type: "life.inner_state.changed",
    payload: {
      schema_version: 1,
      source_life_snapshot_revision: revision,
      identity_id: "identity-1",
      instance_id: "instance-1",
      generated_at_utc: "2026-08-12T00:00:03.000Z",
      phase: "engaged",
      attention: {
        mode: "engaged",
        target_kind: "request",
        target_id: "request-1",
        priority: 60,
        since_utc: "2026-08-12T00:00:02.000Z",
        expires_at_utc: "2026-08-12T00:00:10.000Z",
        source_observation_id: "observation-1",
      },
      homeostasis: {
        updated_at_utc: "2026-08-12T00:00:03.000Z",
        activation: 0.4,
        cognitive_load: 0.25,
        certainty: 0.5,
        caution: 0.1,
        curiosity: 0.25,
        blockedness: 0,
        social_presence: 0.3,
      },
      affects: [{
        kind: "cautious",
        intensity: 0.4,
        confidence: 0.9,
        reason_code: "tool_risk_observed",
        evidence_ids: ["evidence-1"],
        valid_until_utc: "2026-08-12T00:00:10.000Z",
      }],
      presence: {
        mode: "engaged",
        intensity: 0.6,
        session_id: "session-1",
        source_observation_id: "observation-1",
        reason_code: "request_started",
        since_utc: "2026-08-12T00:00:02.000Z",
        expires_at_utc: "2026-08-12T00:00:10.000Z",
      },
      last_observation_id: "observation-1",
      degraded: false,
      ...overrides,
    },
  };
}


test("rejects malformed life events and ignores stale revisions", () => {
  const seen: string[] = [];
  const bridge = createLifeStateBridge({
    signal: (signal) => seen.push(signal.state),
    now: () => Date.parse("2026-08-12T00:00:03.500Z"),
  });

  assert.equal(
    bridge.handle({ type: "life.snapshot", payload: { revision: "bad" } }),
    false,
  );
  assert.equal(bridge.handle(lifeSnapshotEvent(3, "thinking")), true);
  assert.equal(bridge.handle(lifeSnapshotEvent(2, "speaking")), false);
  assert.equal(
    bridge.handle({
      ...lifeSnapshotEvent(4, "speaking"),
      payload: { ...lifeSnapshotEvent(4, "speaking").payload, extra: true },
    }),
    false,
  );
  assert.deepEqual(seen, ["thinking"]);
  assert.equal(bridge.snapshot().snapshotRevision, 3);
});


test("maps attention and quiet into existing LiveState without widening it", () => {
  const seen: string[] = [];
  const bridge = createLifeStateBridge({
    signal: (signal) => seen.push(signal.state),
  });

  assert.equal(bridge.handle(lifeSnapshotEvent(1, "attention")), true);
  assert.equal(bridge.handle(lifeSnapshotEvent(2, "quiet")), true);

  assert.deepEqual(seen, ["thinking", "idle"]);
  assert.equal(bridge.snapshot().state, "idle");
});


test("offline remains a client projection and reconnect accepts a newer snapshot", () => {
  let now = Date.parse("2026-08-12T00:00:04.000Z");
  const timestamps: number[] = [];
  const bridge = createLifeStateBridge({
    now: () => now,
    signal: (signal) => timestamps.push(signal.timestamp),
  });

  bridge.projectOffline("backend unavailable");
  assert.equal(bridge.snapshot().state, "offline");
  assert.equal(bridge.handle(lifeSnapshotEvent(4, "idle")), true);
  assert.equal(bridge.snapshot().state, "idle");
  assert.ok(timestamps[1] > timestamps[0]);

  now += 1_000;
  bridge.projectOffline("disconnected again");
  assert.equal(bridge.handle(lifeSnapshotEvent(4, "thinking")), false);
  assert.equal(bridge.snapshot().state, "offline");
});


test("validates expression intent, rejects stale or expired values, and unsubscribes", () => {
  const seen: number[] = [];
  const bridge = createLifeStateBridge({
    signal: () => undefined,
    now: () => Date.parse("2026-08-12T00:00:04.000Z"),
  });
  const unsubscribe = bridge.subscribeExpression((intent) => {
    seen.push(intent.revision);
  });

  assert.equal(bridge.handle(lifeExpressionEvent(1)), true);
  assert.equal(bridge.handle(lifeExpressionEvent(1)), false);
  assert.equal(
    bridge.handle(lifeExpressionEvent(2, { gaze_target: "somewhere" })),
    false,
  );
  assert.equal(
    bridge.handle(lifeExpressionEvent(2, {
      generated_at: "2026-08-12T00:00:05.000Z",
      expires_at: "2026-08-12T00:00:04.000Z",
    })),
    false,
  );
  assert.equal(
    bridge.handle(lifeExpressionEvent(2, {
      expires_at: "2026-08-12T00:00:03.999Z",
    })),
    false,
  );
  assert.equal(bridge.handle(lifeExpressionEvent(2)), true);
  unsubscribe();
  assert.equal(bridge.handle(lifeExpressionEvent(3)), true);

  assert.deepEqual(seen, [1, 2]);
  assert.equal(bridge.snapshot().expressionRevision, 3);
});


test("accepts read-only inner state only when it matches the authoritative L0 snapshot", () => {
  const bridge = createLifeStateBridge({ signal: () => undefined });

  assert.equal(bridge.snapshot().innerStateMode, "compatibility");
  assert.equal(bridge.handle(innerStateEvent(3)), false);
  assert.equal(bridge.handle(lifeSnapshotEvent(3, "thinking")), true);
  assert.equal(bridge.handle(innerStateEvent(3)), true);

  const projected = bridge.snapshot();
  assert.equal(projected.snapshotRevision, 3);
  assert.equal(projected.innerStateMode, "authoritative");
  assert.equal(projected.innerState?.source_life_snapshot_revision, 3);
  assert.equal(projected.innerState?.identity_id, projected.snapshot?.identity.identity_id);
  assert.equal("innerStateRevision" in projected, false);
});


test("new L0 revisions and disconnects return L1 projection to compatibility mode", () => {
  const bridge = createLifeStateBridge({ signal: () => undefined });

  bridge.handle(lifeSnapshotEvent(3, "thinking"));
  bridge.handle(innerStateEvent(3));
  assert.equal(bridge.handle(lifeSnapshotEvent(4, "speaking")), true);
  assert.equal(bridge.snapshot().innerStateMode, "compatibility");
  assert.equal(bridge.snapshot().innerState, null);
  assert.equal(bridge.handle(innerStateEvent(3)), false);
  assert.equal(bridge.handle(innerStateEvent(5)), false);
  assert.equal(bridge.handle(innerStateEvent(4)), true);

  bridge.projectOffline();
  assert.equal(bridge.snapshot().snapshotRevision, 4);
  assert.equal(bridge.snapshot().innerStateMode, "compatibility");
  assert.equal(bridge.snapshot().innerState, null);
});


test("strictly rejects malformed, mismatched, or noncanonical inner-state events", () => {
  function accepts(event: ReturnType<typeof innerStateEvent>): boolean {
    const bridge = createLifeStateBridge({ signal: () => undefined });
    bridge.handle(lifeSnapshotEvent(3, "thinking"));
    return bridge.handle(event);
  }

  assert.equal(accepts(innerStateEvent(3, { identity_id: "other" })), false);
  assert.equal(accepts(innerStateEvent(3, { phase: "Not A Code" })), false);
  assert.equal(accepts(innerStateEvent(3, {
    homeostasis: {
      ...innerStateEvent(3).payload.homeostasis,
      cognitive_load: 1.01,
    },
  })), false);
  assert.equal(accepts(innerStateEvent(3, {
    attention: {
      ...innerStateEvent(3).payload.attention,
      target_id: null,
    },
  })), false);
  assert.equal(accepts(innerStateEvent(3, {
    affects: [{
      ...innerStateEvent(3).payload.affects[0],
      evidence_ids: ["duplicate", "duplicate"],
    }],
  })), false);
  assert.equal(accepts(innerStateEvent(3, { unexpected: true })), false);

  const bridge = createLifeStateBridge({ signal: () => undefined });
  bridge.handle(lifeSnapshotEvent(3, "thinking"));
  assert.equal(bridge.handle({ ...innerStateEvent(3), type: "life.inner_state" }), false);
  assert.equal(bridge.snapshot().innerStateMode, "compatibility");
});


test("inner-state snapshots are deeply immutable and expose no write path", () => {
  const bridge = createLifeStateBridge({ signal: () => undefined });
  bridge.handle(lifeSnapshotEvent(3, "thinking"));
  bridge.handle(innerStateEvent(3));

  const innerState = bridge.snapshot().innerState;
  assert.ok(innerState);
  assert.equal(Object.isFrozen(innerState), true);
  assert.equal(Object.isFrozen(innerState.homeostasis), true);
  assert.equal(Object.isFrozen(innerState.affects), true);
  assert.equal(Object.isFrozen(innerState.affects[0].evidence_ids), true);
  assert.equal("setInnerState" in bridge, false);
  assert.throws(() => {
    (innerState.homeostasis as { activation: number }).activation = 1;
  }, TypeError);
  assert.equal(bridge.snapshot().innerState?.homeostasis.activation, 0.4);
});


test("rejects invalid RFC3339 timestamps, enum values, ranges, and unknown events", () => {
  const bridge = createLifeStateBridge({ signal: () => undefined });
  const invalidSnapshot = lifeSnapshotEvent(1, "thinking");

  assert.equal(
    bridge.handle({
      ...invalidSnapshot,
      payload: { ...invalidSnapshot.payload, updated_at: "2026-02-31T00:00:01.000Z" },
    }),
    false,
  );
  assert.equal(
    bridge.handle(lifeExpressionEvent(1, { intensity: 1.2 })),
    false,
  );
  assert.equal(
    bridge.handle(lifeExpressionEvent(1, { voice_activity: "singing" })),
    false,
  );
  assert.equal(bridge.handle({ type: "request.accepted", payload: {} }), false);
  assert.deepEqual(bridge.snapshot(), {
    state: "idle",
    detail: "Javis 已待命",
    snapshotRevision: -1,
    expressionRevision: -1,
    innerStateMode: "compatibility",
    snapshot: null,
    expression: null,
    innerState: null,
  });
});


test("main handles life events before the normal conversation reducer", () => {
  const source = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  const callback = source.indexOf("onEvent: (event) => {");
  const lifeHandle = source.indexOf("lifeStateBridge.handle(event);", callback);
  const conversation = source.indexOf("else if (isConversationEvent(event))", callback);

  assert.ok(callback >= 0);
  assert.ok(lifeHandle > callback);
  assert.ok(conversation > lifeHandle);
  assert.equal(source.match(/createLifeStateBridge\(/g)?.length, 1);
});
