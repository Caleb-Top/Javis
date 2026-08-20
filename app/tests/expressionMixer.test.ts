import assert from "node:assert/strict";
import test from "node:test";

import { ExpressionMixer } from "../src/pet/avatar/ExpressionMixer.ts";
import { capabilitiesFixture, speakingTargetFixture } from "./avatarTestFakes.ts";

test("speaking keeps blink but interrupt clears mouth and gestures", () => {
  const mixer = new ExpressionMixer(capabilitiesFixture());
  mixer.submit(speakingTargetFixture());
  mixer.tick(100);
  assert.ok(mixer.snapshot().mouth > 0);
  assert.equal(mixer.snapshot().blinkEnabled, true);
  mixer.interrupt();
  assert.equal(mixer.snapshot().mouth, 0);
  assert.equal(mixer.snapshot().gesture, "none");
});

test("missing capabilities fail closed to neutral channels", () => {
  const mixer = new ExpressionMixer(["idle"]);
  mixer.submit(speakingTargetFixture());
  mixer.tick(200);
  assert.equal(mixer.snapshot().mouth, 0);
  assert.equal(mixer.snapshot().gaze, "none");
  assert.equal(mixer.snapshot().blinkEnabled, false);
});
