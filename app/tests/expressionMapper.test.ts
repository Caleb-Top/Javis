import assert from "node:assert/strict";
import test from "node:test";

import { mapExpressionIntent } from "../src/pet/avatar/ExpressionMapper.ts";
import { intentFixture } from "./avatarTestFakes.ts";

test("maps all life states to restrained model-independent targets", () => {
  assert.deepEqual(mapExpressionIntent(intentFixture("listening")), {
    state: "listening",
    gaze: "user",
    mouth: 0,
    blinkRate: "normal",
    posture: "attentive",
    color: "cyan",
    gesture: "none",
    interrupt: false,
  });
  assert.equal(mapExpressionIntent(intentFixture("blocked")).posture, "cautious");
  assert.equal(mapExpressionIntent(intentFixture("error")).gesture, "none");
  assert.equal(mapExpressionIntent(intentFixture("offline")).blinkRate, "off");
  assert.equal(mapExpressionIntent(intentFixture("speaking", { voice_activity: "silent" })).mouth, 0);
});

test("interrupt always clears gestures", () => {
  const target = mapExpressionIntent(intentFixture("executing", { interrupt: true }));
  assert.equal(target.gesture, "none");
  assert.equal(target.interrupt, true);
});
