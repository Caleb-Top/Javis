import assert from "node:assert/strict";
import test from "node:test";

import { createAvatarController } from "../src/pet/avatar/AvatarController.ts";
import type { ExpressionMixerSnapshot } from "../src/pet/avatar/ExpressionMixer.ts";
import { capabilitiesFixture, intentFixture } from "./avatarTestFakes.ts";

test("rejects stale revisions and expired speaking intents before reaching handles", () => {
  const frames: ExpressionMixerSnapshot[] = [];
  const now = Date.parse("2026-08-20T10:00:00Z");
  const controller = createAvatarController({
    sink: { setExpressionTarget: (frame) => frames.push(frame) },
    capabilities: capabilitiesFixture(),
    now: () => now,
  });

  assert.equal(controller.applyIntent(intentFixture("listening", { revision: 4 })), true);
  assert.equal(controller.applyIntent(intentFixture("speaking", { revision: 3 })), false);
  assert.equal(controller.applyIntent(intentFixture("speaking", {
    revision: 5,
    expires_at: "2026-08-20T09:59:59Z",
  })), false);
  assert.equal(frames.length, 1);
  assert.equal(frames[0].state, "listening");
});

test("interrupt and dispose immediately neutralize active mouth output", () => {
  const frames: ExpressionMixerSnapshot[] = [];
  const now = Date.parse("2026-08-20T10:00:00Z");
  const controller = createAvatarController({
    sink: { setExpressionTarget: (frame) => frames.push(frame) },
    capabilities: capabilitiesFixture(),
    now: () => now,
  });
  controller.applyIntent(intentFixture("speaking", { revision: 1 }));
  controller.interrupt();
  assert.equal(frames.at(-1)?.mouth, 0);
  controller.dispose();
  const count = frames.length;
  controller.tick(now + 100);
  assert.equal(frames.length, count);
});
