import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { BackendClient } from "../src/bridge/backendClient.ts";
import { createVoiceCapture } from "../src/live/VoiceCapture.ts";

class FakeVoiceSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readyState = FakeVoiceSocket.CONNECTING;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  open(): void {
    this.readyState = FakeVoiceSocket.OPEN;
    this.onopen?.();
  }

  send(value: string): void {
    this.sent.push(JSON.parse(value) as Record<string, unknown>);
  }

  emit(payload: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }

  close(): void {
    this.readyState = FakeVoiceSocket.CLOSED;
    this.onclose?.();
  }
}

test("continuous native voice stays open across turns and barges in before final submit", async () => {
  const socket = new FakeVoiceSocket();
  const order: string[] = [];
  const finals: string[] = [];
  const partials: string[] = [];
  const client = {
    sessionId: () => "session-voice",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => socket as unknown as WebSocket,
    noiseProfile: () => "strong",
    onBargeIn: async () => { order.push("barge-in"); },
    onPartial: (text) => { partials.push(text); },
    onTranscript: (text) => { order.push(`final:${text}`); finals.push(text); },
    onAudio: () => undefined,
    onState: (state) => { order.push(`state:${state}`); },
    onError: (message) => { throw new Error(message); },
  });

  const started = capture.startContinuous();
  socket.open();
  socket.emit({ type: "audio.stream.ready" });
  await started;

  assert.equal((socket.sent[0].payload as Record<string, unknown>).session_id, "session-voice");
  assert.equal((socket.sent[0].payload as Record<string, unknown>).noise_profile, "strong");
  socket.emit({ type: "speech.start" });
  socket.emit({ type: "transcript.partial", text: "first par" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  socket.emit({ type: "transcript.final", text: "first turn", turn: 1 });
  socket.emit({ type: "speech.start" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  socket.emit({ type: "transcript.final", text: "second turn", turn: 2 });

  assert.deepEqual(partials, ["first par"]);
  assert.deepEqual(finals, ["first turn", "second turn"]);
  assert.ok(order.indexOf("barge-in") < order.indexOf("final:first turn"));
  assert.equal(capture.isContinuous(), true);
  assert.equal(socket.readyState, FakeVoiceSocket.OPEN);

  await capture.pauseContinuous();
  assert.equal((socket.sent.at(-1)?.payload as Record<string, unknown>).session_id, "session-voice");
  assert.equal(capture.isContinuous(), false);
});

test("the App sends only final transcripts and resumes listening after request terminals", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");

  assert.match(main, /onPartial:\s*\(text\)[\s\S]*?liveCaption\.setText\(text\)/);
  assert.match(main, /onTranscript:\s*\(text\)[\s\S]*?client\.send\(text\)/);
  assert.match(main, /request\.(completed|cancelled|failed)[\s\S]*?resumeListeningState/);
});

test("voice turns use native playback and barge-in stops it before cancellation", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");

  assert.match(main, /voiceRequestIds\.add\(requestId\)/);
  assert.match(main, /request\.completed[\s\S]*?\/api\/voice\/playback\/speak/);
  assert.match(
    main,
    /onBargeIn:\s*async[\s\S]*?\/api\/voice\/playback\/stop[\s\S]*?client\.cancel/,
  );
});
