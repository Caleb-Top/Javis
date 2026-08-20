import assert from "node:assert/strict";
import test from "node:test";

import {
  createAvatarPreviewModel,
} from "../src/pet/avatar/AvatarPreview.ts";
import { isLocalAvatarPreviewLocation } from "../src/pet/avatar/AvatarPreviewRoute.ts";

test("preview exposes every state, background, scale and visual profile", () => {
  const model = createAvatarPreviewModel();

  assert.deepEqual(model.states, [
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
  assert.deepEqual(model.backgrounds, ["light", "dark", "wallpaper", "checker"]);
  assert.deepEqual(model.scales, [1, 1.25, 1.5, 1.75]);
  assert.deepEqual(model.profiles, ["light-tech-human", "neon-pilot", "quiet-lifeform"]);
  assert.deepEqual(model.sizes.map(({ width, height }) => [width, height]), [
    [132, 158],
    [200, 218],
    [320, 360],
    [420, 500],
  ]);
});

test("preview creates deterministic, bounded and expiring expression intents", () => {
  const model = createAvatarPreviewModel({ state: "speaking", intensity: 4, speaking: true });
  const intent = model.createIntent(7.8, Date.parse("2026-08-20T12:00:00.000Z"));

  assert.equal(intent.revision, 7);
  assert.equal(intent.source_snapshot_revision, 7);
  assert.equal(intent.intensity, 1);
  assert.equal(intent.voice_activity, "speaking");
  assert.equal(intent.generated_at, "2026-08-20T12:00:00.000Z");
  assert.equal(intent.expires_at, "2026-08-20T12:00:10.000Z");
});

test("interrupt and context-loss controls remain local preview state", () => {
  const model = createAvatarPreviewModel();
  const snapshot = model.update({ interrupt: true, contextLost: true, intensity: Number.NaN });
  const intent = model.createIntent(2, 0);

  assert.equal(snapshot.contextLost, true);
  assert.equal(snapshot.intensity, 0);
  assert.equal(intent.interrupt, true);
  assert.equal(intent.transition_ms, 0);
  assert.equal(intent.explanation_code, "avatar_preview");
});

test("avatar preview routing is local-only and explicitly opted in", () => {
  assert.equal(isLocalAvatarPreviewLocation({ hostname: "localhost", search: "?avatar-preview=1" }), true);
  assert.equal(isLocalAvatarPreviewLocation({ hostname: "127.0.0.1", search: "?avatar-preview=1" }), true);
  assert.equal(isLocalAvatarPreviewLocation({ hostname: "localhost", search: "?avatar-preview=0" }), false);
  assert.equal(isLocalAvatarPreviewLocation({ hostname: "javis.example", search: "?avatar-preview=1" }), false);
});
