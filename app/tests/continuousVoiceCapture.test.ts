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

  remoteClose(): void {
    this.readyState = FakeVoiceSocket.CLOSED;
    this.onclose?.();
  }
}

async function waitFor(predicate: () => boolean, timeoutMs = 200): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate() && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
  assert.equal(predicate(), true, `condition was not met within ${timeoutMs} ms`);
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

test("empty transcripts return continuous capture to listening without submitting text", async () => {
  const socket = new FakeVoiceSocket();
  const states: string[] = [];
  const emptyMessages: string[] = [];
  const transcripts: string[] = [];
  const client = {
    sessionId: () => "session-empty",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => socket as unknown as WebSocket,
    onBargeIn: () => undefined,
    onEmptyTranscript: (message) => { emptyMessages.push(message); },
    onTranscript: (text) => { transcripts.push(text); },
    onAudio: () => undefined,
    onState: (state) => { states.push(state); },
    onError: (message) => { throw new Error(message); },
  });

  const started = capture.startContinuous();
  socket.open();
  socket.emit({ type: "audio.stream.ready" });
  await started;
  const statesBeforeEmpty = states.length;

  socket.emit({
    type: "transcript.empty",
    turn: 1,
    audio_ms: 260,
    input_rms: 0.12,
    input_peak: 0.42,
  });

  assert.equal(states.length, statesBeforeEmpty + 1);
  assert.equal(states.at(-1), "listening");
  assert.deepEqual(emptyMessages, ["没有识别到语音，请再说一次"]);
  assert.deepEqual(transcripts, []);
  assert.equal(capture.isContinuous(), true);
  assert.equal(socket.readyState, FakeVoiceSocket.OPEN);
});

test("native stream connection errors are reported without escaping the Live action", async () => {
  const socket = new FakeVoiceSocket();
  const errors: string[] = [];
  const client = {
    sessionId: () => "session-error",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => socket as unknown as WebSocket,
    reconnectDelaysMs: [100],
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: (message) => { errors.push(message); },
  });

  const started = capture.startContinuous();
  socket.onerror?.();

  await assert.rejects(started, /native audio stream connection failed/);
  assert.deepEqual(errors, ["语音服务尚未就绪，正在等待本地运行时。"]);
  await capture.pauseContinuous();

  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  assert.match(main, /const toggleVoice[\s\S]*?try \{[\s\S]*?voiceCapture\.toggle\(\)[\s\S]*?catch/);
});

test("continuous voice reconnects through ten forced disconnects and resets backoff after ready", async () => {
  const sockets: FakeVoiceSocket[] = [];
  const transcripts: string[] = [];
  const errors: string[] = [];
  const client = {
    sessionId: () => "session-reconnect-ten",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => {
      const socket = new FakeVoiceSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    reconnectDelaysMs: [0, 1000],
    onBargeIn: () => undefined,
    onTranscript: (text) => { transcripts.push(text); },
    onAudio: () => undefined,
    onState: () => undefined,
    onError: (message) => { errors.push(message); },
  });

  const started = capture.startContinuous();
  sockets[0].open();
  sockets[0].emit({ type: "audio.stream.ready" });
  await started;

  for (let turn = 0; turn < 10; turn += 1) {
    const retired = sockets.at(-1)!;
    retired.remoteClose();
    await waitFor(() => sockets.length === turn + 2, 150);
    const recovered = sockets.at(-1)!;
    recovered.open();
    recovered.emit({ type: "audio.stream.ready" });
    assert.equal(capture.isContinuous(), true);

    if (turn === 0) {
      const socketCount = sockets.length;
      const errorCount = errors.length;
      retired.emit({ type: "transcript.final", text: "stale transcript" });
      retired.onerror?.();
      retired.onclose?.();
      await new Promise((resolve) => setTimeout(resolve, 5));
      assert.equal(sockets.length, socketCount);
      assert.equal(errors.length, errorCount);
      assert.deepEqual(transcripts, []);
      assert.equal(capture.isContinuous(), true);
    }
  }

  assert.equal(sockets.length, 11);
  await capture.pauseContinuous();
});

test("a disconnect before ready rejects startup once while recovery continues", async () => {
  const sockets: FakeVoiceSocket[] = [];
  const errors: string[] = [];
  const client = {
    sessionId: () => "session-startup-recovery",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => {
      const socket = new FakeVoiceSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    reconnectDelaysMs: [0, 1],
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: (message) => { errors.push(message); },
  });

  const started = capture.startContinuous();
  sockets[0].open();
  sockets[0].remoteClose();
  await assert.rejects(started, /native audio stream disconnected/);
  await waitFor(() => sockets.length === 2);
  sockets[1].open();
  sockets[1].emit({ type: "audio.stream.ready" });

  assert.equal(capture.isContinuous(), true);
  assert.deepEqual(errors, ["native audio stream disconnected"]);
  await capture.stop();
});

test("pause cancels pending reconnects and rejects an unsettled startup", async () => {
  const sockets: FakeVoiceSocket[] = [];
  const client = {
    sessionId: () => "session-user-pause",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => {
      const socket = new FakeVoiceSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    reconnectDelaysMs: [40],
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: () => undefined,
  });

  const started = capture.startContinuous();
  sockets[0].open();
  sockets[0].emit({ type: "audio.stream.ready" });
  await started;
  sockets[0].remoteClose();
  await capture.pauseContinuous();
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(sockets.length, 1);
  assert.equal(capture.isContinuous(), false);

  const pendingStart = capture.startContinuous();
  const outcome = pendingStart.then(
    () => "resolved",
    (error: Error) => `rejected:${error.message}`,
  );
  await capture.stop();
  const settled = await Promise.race([
    outcome,
    new Promise<string>((resolve) => setTimeout(() => resolve("timeout"), 80)),
  ]);
  assert.match(settled, /^rejected:.*cancelled/);
  await new Promise((resolve) => setTimeout(resolve, 60));
  assert.equal(sockets.length, 2);
  assert.equal(capture.isContinuous(), false);
});

test("startup settles when payload or ready-state callbacks throw", async () => {
  const payloadSocket = new FakeVoiceSocket();
  const client = {
    sessionId: () => "session-startup-callbacks",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const payloadCapture = createVoiceCapture(client, {
    openStream: () => payloadSocket as unknown as WebSocket,
    reconnectDelaysMs: [100],
    deviceIndex: () => { throw new Error("device configuration failed"); },
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: () => undefined,
  });

  const payloadStart = payloadCapture.startContinuous();
  assert.doesNotThrow(() => payloadSocket.open());
  await assert.rejects(payloadStart, /device configuration failed/);
  await payloadCapture.stop();

  const readySocket = new FakeVoiceSocket();
  let listeningSignals = 0;
  const readyCapture = createVoiceCapture(client, {
    openStream: () => readySocket as unknown as WebSocket,
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: (state) => {
      if (state === "listening" && ++listeningSignals === 2) {
        throw new Error("ready renderer failed");
      }
    },
    onError: () => undefined,
  });
  const readyStart = readyCapture.startContinuous();
  readySocket.open();
  try {
    readySocket.emit({ type: "audio.stream.ready" });
  } catch {
    // A presentation callback cannot be allowed to strand the lifecycle promise.
  }
  const readyOutcome = await Promise.race([
    readyStart.then(() => "resolved", () => "rejected"),
    new Promise<string>((resolve) => setTimeout(() => resolve("timeout"), 80)),
  ]);
  assert.equal(readyOutcome, "resolved");
  await readyCapture.stop();
});

test("startup ready timeout rejects instead of leaving the Live action pending", async () => {
  const socket = new FakeVoiceSocket();
  const client = {
    sessionId: () => "session-ready-timeout",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => socket as unknown as WebSocket,
    reconnectDelaysMs: [1000],
    streamReadyTimeoutMs: 10,
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: () => undefined,
  });

  const outcome = capture.startContinuous().then(
    () => "resolved",
    (error: Error) => `rejected:${error.message}`,
  );
  const settled = await Promise.race([
    outcome,
    new Promise<string>((resolve) => setTimeout(() => resolve("hung"), 80)),
  ]);
  await capture.stop();
  assert.match(settled, /^rejected:native audio stream ready timed out$/);
});

test("the App sends only final transcripts and resumes listening after request terminals", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");

  assert.match(main, /onPartial:\s*\(text\)[\s\S]*?liveCaption\.setText\(text\)/);
  assert.match(main, /onTranscript:\s*\(text\)[\s\S]*?client\.send\(text\)/);
  assert.match(main, /request\.(completed|cancelled|failed)[\s\S]*?resumeListeningState/);
});

test("the production Live surface renders empty transcripts without submitting a request", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  const compositionStart = main.indexOf("voiceCapture = createVoiceCapture");
  const compositionEnd = main.indexOf("diagnostics =", compositionStart);
  const composition = main.slice(compositionStart, compositionEnd);

  assert.ok(compositionStart >= 0);
  assert.ok(compositionEnd > compositionStart);
  assert.match(
    composition,
    /onEmptyTranscript:\s*\(message\)\s*=>\s*\{[\s\S]*?liveCaption\.setText\(message\)/,
  );
  const callbackStart = composition.indexOf("onEmptyTranscript:");
  const callbackEnd = composition.indexOf("onLevel:", callbackStart);
  assert.doesNotMatch(composition.slice(callbackStart, callbackEnd), /client\.send\(/);
});

test("local request failures bypass the strict reducer and recover Live listening", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  const localFailureBranch = main.indexOf(
    'if (event.type === "request.failed" && event.local === true)',
  );
  const conversationEventBranch = main.indexOf(
    "else if (isConversationEvent(event))",
  );

  assert.ok(localFailureBranch >= 0);
  assert.ok(conversationEventBranch > localFailureBranch);
  const recovery = main.slice(localFailureBranch, conversationEventBranch);
  assert.match(recovery, /voiceRequestIds\.delete\(event\.request_id\)/);
  assert.match(recovery, /event\.payload\?\.error/);
  assert.match(recovery, /liveCaption\.setText/);
  assert.match(recovery, /voiceCapture\.resumeListeningState\(\)/);
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

test("TTS playback failure surfaces the reason and still resumes listening", () => {
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  const start = main.indexOf("/api/voice/playback/speak");
  const end = main.indexOf("request.completed", start);
  const branch = main.slice(start, end < start ? main.length : end);
  // The speak call must not silently swallow failures: a failed playback
  // must render the reason and keep the voice loop recoverable.
  assert.match(branch, /\.catch\(\(error\)\s*=>\s*\{[\s\S]*?liveCaption\.setText/);
});

test("selected microphone device index is included in the continuous stream start payload", async () => {
  const socket = new FakeVoiceSocket();
  const client = {
    sessionId: () => "session-device",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => socket as unknown as WebSocket,
    noiseProfile: () => "standard",
    deviceIndex: () => 3,
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: (message) => { throw new Error(message); },
  });

  const started = capture.startContinuous();
  socket.open();
  socket.emit({ type: "audio.stream.ready" });
  await started;

  const payload = socket.sent[0].payload as Record<string, unknown>;
  assert.equal(payload.device_index, 3);

  await capture.pauseContinuous();
  assert.equal(capture.isContinuous(), false);
});
