import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { BackendClient } from "../src/bridge/backendClient.ts";
import { createVoiceCapture } from "../src/live/VoiceCapture.ts";

test("native capture interrupts playback and the active request before listening", async () => {
  const order: string[] = [];
  const client = {
    post: async (path: string) => {
      order.push(`post:${path}`);
      return { ok: true, source: "microphone", status: "ready", message: "ready" };
    },
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    onBargeIn: async () => { order.push("barge-in"); },
    onAudio: () => undefined,
    onState: (state) => { order.push(`state:${state}`); },
    onError: (message) => { throw new Error(message); },
  });

  await capture.toggle();

  assert.deepEqual(order, [
    "barge-in",
    "state:listening",
    "post:/api/voice/capture/start",
  ]);
});

test("web voice entry stops audio and cancels the old request before microphone capture", () => {
  const source = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const startBody = source.match(/async function startVoiceCall\(\)\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  const pttBody = source.match(/function pttStartStream\([^)]*\)\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";

  assert.match(source, /function stopAudioPlayback\(\)/);
  assert.match(startBody, /stopAudioPlayback\(\)/);
  assert.match(startBody, /stopActiveRequest\(\)/);
  assert.ok(startBody.indexOf("stopAudioPlayback()") < startBody.indexOf("getUserMedia"));
  assert.ok(startBody.indexOf("stopActiveRequest()") < startBody.indexOf("getUserMedia"));
  assert.ok(pttBody.indexOf("stopAudioPlayback()") < pttBody.indexOf("getUserMedia"));
  assert.ok(pttBody.indexOf("stopActiveRequest()") < pttBody.indexOf("getUserMedia"));
});
