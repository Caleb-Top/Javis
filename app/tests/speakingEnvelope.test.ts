import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { SpeakingEnvelope } from "../src/pet/avatar/SpeakingEnvelope.ts";

const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");

test("duration envelope is bounded, deterministic and stops immediately", () => {
  const envelope = new SpeakingEnvelope();
  envelope.start(1000, 0);
  assert.equal(envelope.level(0), 0);
  const first = envelope.level(250);
  assert.ok(first >= 0 && first <= 0.65);
  assert.equal(envelope.level(250), first);
  envelope.stop();
  assert.equal(envelope.level(251), 0);
  assert.equal(envelope.active(), false);
});

test("missing duration uses a short conservative pulse", () => {
  const envelope = new SpeakingEnvelope();
  envelope.start(undefined, 100);
  assert.ok(envelope.level(250) <= 0.25);
  assert.equal(envelope.level(700), 0);
  assert.equal(envelope.active(), false);
});

test("only native playback results start the speaking envelope", () => {
  assert.match(mainSource, /playback\.duration_ms[\s\S]{0,160}javis:playback-envelope-start|javis:playback-envelope-start[\s\S]{0,160}playback\.duration_ms/);
  assert.doesNotMatch(mainSource, /onAudio[^\n]+SpeakingEnvelope/);
  assert.doesNotMatch(mainSource, /onLevel[^\n]+speakingEnvelope/);
});
