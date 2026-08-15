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

function voiceProvenance(
  sessionId: string,
  turn: number,
  sequence: number,
): Record<string, unknown> {
  return {
    runtime_boot_id: "boot-voice-1",
    session_id: sessionId,
    owner_generation: 7,
    voice_sequence: sequence,
    voice_turn: turn,
    nonce: `voice-nonce-${String(sequence).padStart(8, "0")}`,
    proof: "b".repeat(64),
  };
}

test("continuous native voice stays open across turns and barges in before final submit", async () => {
  const socket = new FakeVoiceSocket();
  let protocols: string[] | undefined;
  const order: string[] = [];
  const finals: string[] = [];
  const partials: string[] = [];
  const client = {
    sessionId: () => "session-voice",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: (_url, requestedProtocols) => {
      protocols = requestedProtocols;
      return socket as unknown as WebSocket;
    },
    runtimeAccessToken: (scope) => `token-for-${scope}`,
    noiseProfile: () => "strong",
    onBargeIn: async () => { order.push("barge-in"); },
    onPartial: (text) => { partials.push(text); },
    onTranscript: (transcript) => {
      order.push(`final:${transcript.text}`);
      finals.push(transcript.text);
    },
    onAudio: () => undefined,
    onState: (state) => { order.push(`state:${state}`); },
    onError: (message) => { throw new Error(message); },
  });

  const started = capture.startContinuous();
  socket.open();
  socket.emit({ type: "audio.stream.ready" });
  await started;

  assert.deepEqual(protocols, [
    "javis-runtime-v1",
    "javis-capability.token-for-voice.capture",
  ]);
  assert.equal((socket.sent[0].payload as Record<string, unknown>).session_id, "session-voice");
  assert.equal((socket.sent[0].payload as Record<string, unknown>).noise_profile, "strong");
  socket.emit({ type: "speech.start", sequence: 2 });
  socket.emit({ type: "transcript.partial", text: "first par" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  socket.emit({
    type: "transcript.final",
    text: "first turn",
    turn: 1,
    sequence: 3,
    voice_provenance: voiceProvenance("session-voice", 1, 3),
  });
  socket.emit({ type: "speech.start", sequence: 4 });
  await new Promise((resolve) => setTimeout(resolve, 0));
  socket.emit({
    type: "transcript.final",
    text: "second turn",
    turn: 2,
    sequence: 5,
    voice_provenance: voiceProvenance("session-voice", 2, 5),
  });
  await waitFor(() => finals.length === 2);

  assert.deepEqual(partials, ["first par"]);
  assert.deepEqual(finals, ["first turn", "second turn"]);
  assert.ok(order.indexOf("barge-in") < order.indexOf("final:first turn"));
  assert.equal(capture.isContinuous(), true);
  assert.equal(socket.readyState, FakeVoiceSocket.OPEN);

  await capture.pauseContinuous();
  assert.equal((socket.sent.at(-1)?.payload as Record<string, unknown>).session_id, "session-voice");
  assert.equal(capture.isContinuous(), false);
});

test("a final transcript waits for its immutable interruption barrier and submits once", async () => {
  const socket = new FakeVoiceSocket();
  let activeRequestId: string | null = "request-old";
  let releaseInterruption!: () => void;
  const delayedStop = new Promise<void>((resolve) => { releaseInterruption = resolve; });
  const cancelled: Array<string | null> = [];
  const submitted: Array<Record<string, unknown>> = [];
  const client = {
    sessionId: () => "session-barrier",
    activeRequestId: () => activeRequestId,
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => socket as unknown as WebSocket,
    onBargeIn: async ({ interruptedRequestId }) => {
      await delayedStop;
      cancelled.push(interruptedRequestId);
    },
    onTranscript: (transcript) => { submitted.push(transcript); },
    onAudio: () => undefined,
    onState: () => undefined,
    onError: (message) => { throw new Error(message); },
  });

  const started = capture.startContinuous();
  socket.open();
  socket.emit({ type: "audio.stream.ready" });
  await started;
  socket.emit({ type: "speech.start", sequence: 20 });
  activeRequestId = "request-new";
  const final = {
    type: "transcript.final",
    text: "new spoken request",
    turn: 4,
    sequence: 21,
    voice_provenance: voiceProvenance("session-barrier", 4, 21),
  };
  socket.emit(final);
  socket.emit(final);
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.deepEqual(cancelled, []);
  assert.deepEqual(submitted, []);
  releaseInterruption();
  await waitFor(() => submitted.length === 1);

  assert.deepEqual(cancelled, ["request-old"]);
  assert.deepEqual(submitted[0], {
    text: "new spoken request",
    turn: 4,
    sequence: 21,
    voiceProvenance: voiceProvenance("session-barrier", 4, 21),
  });
  await capture.stop();
});

test("continuous final transcripts fail closed when provenance is missing", async () => {
  const socket = new FakeVoiceSocket();
  const transcripts: Record<string, unknown>[] = [];
  const errors: string[] = [];
  const client = {
    sessionId: () => "session-missing-provenance",
    activeRequestId: () => null,
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => socket as unknown as WebSocket,
    onBargeIn: () => undefined,
    onTranscript: (transcript) => { transcripts.push(transcript); },
    onAudio: () => undefined,
    onState: () => undefined,
    onError: (message) => { errors.push(message); },
  });

  const started = capture.startContinuous();
  socket.open();
  socket.emit({ type: "audio.stream.ready" });
  await started;
  socket.emit({ type: "speech.start", sequence: 30 });
  socket.emit({ type: "transcript.final", text: "unverified", turn: 5, sequence: 31 });
  await waitFor(() => errors.length === 1);

  assert.deepEqual(transcripts, []);
  assert.match(errors[0], /provenance/i);
  await capture.stop();
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
  await waitFor(() => emptyMessages.length === 1);

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
  assert.match(
    main,
    /onTranscript:\s*\(\{\s*text,\s*voiceProvenance\s*\}\)[\s\S]*?client\.send\(text,\s*voiceProvenance\)/,
  );
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
  assert.match(
    main,
    /request\.completed[\s\S]*?\/api\/voice\/playback\/speak[\s\S]*?session_id:\s*conversationId[\s\S]*?request_id:\s*playbackRequestId/,
  );
  assert.match(
    main,
    /onBargeIn:\s*async\s*\(\{\s*interruptedRequestId\s*\}\)[\s\S]*?const capturedPlayback[\s\S]*?\/api\/voice\/playback\/stop[\s\S]*?playback_id:\s*capturedPlayback\.playbackId[\s\S]*?client\.cancel\(interruptedRequestId,\s*"voice barge-in"\)/,
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

test("selected microphone device index is also used by the microphone plus STT probe", async () => {
  const posts: Array<{ path: string; body: Record<string, unknown> }> = [];
  const client = {
    sessionId: () => "session-probe-device",
    post: async (path: string, body: Record<string, unknown>) => {
      posts.push({ path, body });
      return { ok: true, source: "microphone", status: "ready", message: "ok" };
    },
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    deviceIndex: () => 17,
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: () => undefined,
  });

  await capture.probeMicrophone();

  assert.equal(posts.length, 1);
  assert.equal(posts[0].path, "/api/voice/capture/probe");
  assert.equal(posts[0].body.device_index, 17);
  assert.ok(Number(posts[0].body.duration) >= 4);
});

test("microphone diagnostics releases continuous capture before probing and then resumes it", async () => {
  const sockets: FakeVoiceSocket[] = [];
  const order: string[] = [];
  const client = {
    sessionId: () => "session-diagnostic-probe",
    post: async () => {
      order.push("probe");
      return {
        ok: true,
        source: "microphone",
        status: "ready",
        message: "ok",
        signalDetected: true,
      };
    },
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => {
      const socket = new FakeVoiceSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: () => undefined,
  });

  const initial = capture.startContinuous();
  sockets[0].open();
  sockets[0].emit({ type: "audio.stream.ready" });
  await initial;

  const diagnostic = capture.probeMicrophone();
  await waitFor(() => sockets[0].sent.some((message) => message.type === "audio.stream.stop"));
  assert.deepEqual(order, []);
  sockets[0].emit({ type: "audio.stream.stopped" });
  await waitFor(() => order.includes("probe"));
  await waitFor(() => sockets.length === 2);
  sockets[1].open();
  sockets[1].emit({ type: "audio.stream.ready" });

  const result = await diagnostic;
  assert.equal(result.ok, true);
  assert.equal(capture.isContinuous(), true);
  await capture.pauseContinuous();
});

test("changing the selected microphone restarts an active stream with the new device", async () => {
  const sockets: FakeVoiceSocket[] = [];
  let selectedDevice = 1;
  const client = {
    sessionId: () => "session-device-change",
    post: async () => ({ ok: true }),
  } as unknown as BackendClient;
  const capture = createVoiceCapture(client, {
    openStream: () => {
      const socket = new FakeVoiceSocket();
      sockets.push(socket);
      return socket as unknown as WebSocket;
    },
    deviceIndex: () => selectedDevice,
    onBargeIn: () => undefined,
    onAudio: () => undefined,
    onState: () => undefined,
    onError: () => undefined,
  });

  const initial = capture.startContinuous();
  sockets[0].open();
  sockets[0].emit({ type: "audio.stream.ready" });
  await initial;

  selectedDevice = 17;
  const restarted = capture.applyInputDeviceChange();
  await waitFor(() => sockets[0].sent.some((message) => message.type === "audio.stream.stop"));
  assert.equal(sockets.length, 1);
  sockets[0].emit({ type: "audio.stream.stopped" });
  await waitFor(() => sockets.length === 2);
  sockets[1].open();
  sockets[1].emit({ type: "audio.stream.ready" });
  await restarted;

  assert.equal((sockets[0].sent[0].payload as Record<string, unknown>).device_index, 1);
  assert.equal((sockets[1].sent[0].payload as Record<string, unknown>).device_index, 17);
  await capture.pauseContinuous();
});
