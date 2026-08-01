import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { BackendClient } from "../src/bridge/backendClient.ts";
import { createVoiceCapture } from "../src/live/VoiceCapture.ts";

class BargeInSocket {
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  send(): void {}
  close(): void { this.readyState = 3; }
  open(): void { this.readyState = 1; this.onopen?.(); }
  emit(payload: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

test("native capture interrupts playback and the active request before listening", async () => {
  const order: string[] = [];
  const socket = new BargeInSocket();
  const client = {
    sessionId: () => "session-barge-in",
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => socket as unknown as WebSocket,
    onBargeIn: async () => { order.push("barge-in"); },
    onAudio: () => undefined,
    onState: (state) => { order.push(`state:${state}`); },
    onError: (message) => { throw new Error(message); },
  });

  const started = capture.startContinuous();
  socket.open();
  socket.emit({ type: "audio.stream.ready" });
  await started;
  order.length = 0;
  socket.emit({ type: "speech.start" });
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(order, [
    "barge-in",
    "state:listening",
  ]);
  await capture.pauseContinuous();
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
