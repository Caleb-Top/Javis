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
  mixer.setSpeakingLevel(0.65);
  mixer.tick(300);
  assert.equal(mixer.snapshot().mouth, 0);
});

test("audio envelope including final zero survives animation ticks until a new intent", () => {
  const mixer = new ExpressionMixer(capabilitiesFixture());
  mixer.submit(speakingTargetFixture());
  mixer.setSpeakingLevel(0.5);
  assert.equal(mixer.tick(100).mouth, 0.5);
  mixer.setSpeakingLevel(0);
  assert.equal(mixer.tick(200).mouth, 0);
  assert.equal(mixer.tick(2000).mouth, 0);
  mixer.submit(speakingTargetFixture());
  assert.ok(mixer.tick(2100).mouth > 0);
  mixer.setSpeakingLevel(0.4);
  mixer.interrupt();
  mixer.setSpeakingLevel(0.6);
  assert.equal(mixer.tick(2200).mouth, 0);
});
