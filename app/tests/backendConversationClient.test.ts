import assert from "node:assert/strict";
import test from "node:test";

import { createBackendClient } from "../src/bridge/backendClient.ts";
import { runtimeStateCoordinator } from "../src/state/RuntimeStateCoordinator.ts";

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  closeCalls = 0;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  readonly url: string;
  readonly protocols: string[];

  constructor(url: string, protocols: string[] = []) {
    this.url = url;
    this.protocols = protocols;
    FakeWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  close(): void {
    this.closeCalls += 1;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  remoteClose(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  fail(): void {
    this.onerror?.();
  }

  send(value: string): void {
    this.sent.push(JSON.parse(value) as Record<string, unknown>);
  }

  emit(payload: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

test("backend client sends scoped runtime access on WebSocket and HTTP", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const originalFetch = globalThis.fetch;
  const requests: Array<{ input: string; headers: Headers }> = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    requests.push({
      input: String(input),
      headers: new Headers(init?.headers),
    });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
  const client = createBackendClient({
    sessionId: "authorized-session",
    runtimeAccessToken: (scope) => `token-for-${scope}`,
  });

  try {
    client.connect();
    assert.deepEqual(FakeWebSocket.instances[0].protocols, [
      "javis-runtime-v1",
      "javis-capability.token-for-conversation",
    ]);
    await client.get("/api/voice/diagnostics");
    await client.post("/api/voice/playback/stop", {});
    assert.equal(
      requests[0].headers.get("X-Javis-Runtime-Capability"),
      "token-for-diagnostics.read",
    );
    assert.equal(
      requests[1].headers.get("X-Javis-Runtime-Capability"),
      "token-for-playback",
    );
  } finally {
    client.dispose();
    globalThis.fetch = originalFetch;
  }
});

function installBrowserFakes(): void {
  Object.assign(globalThis, {
    WebSocket: FakeWebSocket,
    document: { querySelector: () => null },
    window: {
      clearTimeout,
      setTimeout,
    },
  });
}

function canonicalEvent(
  sessionId: string,
  type: string,
  requestId: string,
  sequence: number,
  payload: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: 2,
    session_id: sessionId,
    request_id: requestId,
    sequence,
    timestamp: "2026-08-08T12:00:00.000Z",
    type,
    payload,
  };
}

function voiceProvenance(sessionId: string): Record<string, unknown> {
  return {
    runtime_boot_id: "boot-voice-1",
    session_id: sessionId,
    owner_generation: 4,
    voice_sequence: 9,
    voice_turn: 2,
    nonce: "voice-nonce-00000001",
    proof: "a".repeat(64),
  };
}

test("the native client attaches and uses one canonical conversation", () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const client = createBackendClient({
    sessionId: "session-1",
    afterSequence: () => 7,
  });

  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();
  const requestId = client.send("continue the same task");

  assert.deepEqual(socket.sent[0], {
    type: "conversation.attach",
    payload: {
      session_id: "session-1",
      after_sequence: 7,
      protocol_version: 2,
    },
  });
  assert.equal(socket.sent[1].type, "conversation.message");
  const payload = socket.sent[1].payload as Record<string, unknown>;
  assert.equal(payload.session_id, "session-1");
  assert.equal(payload.request_id, requestId);
  assert.equal(payload.idempotency_key, requestId);
  assert.equal(payload.text, "continue the same task");
  assert.equal("recent_cards" in payload, false);
  assert.equal(client.sessionId(), "session-1");
  assert.equal(client.activeRequestId(), requestId);
  client.cancel(requestId as string, "test cleanup");
});

test("cancel targets the active request and prevents queued replay", () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const client = createBackendClient({ sessionId: "session-1" });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();
  const requestId = client.send("a long task");

  assert.equal(client.cancel(requestId as string, "new voice input"), true);
  assert.deepEqual(socket.sent.at(-1), {
    type: "conversation.cancel",
    payload: {
      session_id: "session-1",
      request_id: requestId,
      reason: "new voice input",
      protocol_version: 2,
    },
  });
});

test("acknowledgement timeout removes a disconnected request and reports one local failure", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const events: Record<string, unknown>[] = [];
  const client = createBackendClient({
    sessionId: "session-timeout",
    requestTimeouts: {
      acceptedMs: 10,
      firstResponseMs: 10,
      overallMs: 10,
    },
    onEvent: (event) => { events.push(event); },
  });

  const requestId = client.send("wait for acknowledgement");
  assert.equal(client.queueSize(), 1);

  await new Promise((resolve) => setTimeout(resolve, 25));

  const localFailures = events.filter(
    (event) => event.type === "request.failed" && event.local === true,
  );
  assert.equal(client.queueSize(), 0);
  assert.equal(client.activeRequestId(), null);
  assert.equal(localFailures.length, 1);
  assert.equal(localFailures[0].request_id, requestId);
  assert.equal((localFailures[0].payload as Record<string, unknown>).phase, "accepted");
  const reliability = client.reliabilitySnapshot();
  assert.deepEqual(reliability.timeoutCounts, {
    accepted: 1,
    "first-response": 0,
    overall: 0,
  });
  assert.equal(reliability.lastTimeoutPhase, "accepted");
  assert.equal(reliability.requestPhase, "timed-out");
});

test("accepted requests fail once when the first response does not arrive", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const events: Record<string, unknown>[] = [];
  const client = createBackendClient({
    sessionId: "session-first-response",
    requestTimeouts: {
      acceptedMs: 40,
      firstResponseMs: 10,
      overallMs: 30,
    },
    onEvent: (event) => { events.push(event); },
  });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();

  const requestId = client.send("start responding");
  socket.emit({ type: "request.accepted", request_id: requestId });
  await new Promise((resolve) => setTimeout(resolve, 45));

  const localFailures = events.filter(
    (event) => event.type === "request.failed" && event.local === true,
  );
  assert.equal(localFailures.length, 1);
  assert.equal(localFailures[0].request_id, requestId);
  assert.equal((localFailures[0].payload as Record<string, unknown>).phase, "first-response");
  assert.equal(client.activeRequestId(), null);
  assert.deepEqual(client.reliabilitySnapshot().timeoutCounts, {
    accepted: 0,
    "first-response": 1,
    overall: 0,
  });
  assert.equal(client.reliabilitySnapshot().lastTimeoutPhase, "first-response");
  assert.equal(client.reliabilitySnapshot().requestPhase, "timed-out");
  assert.deepEqual(socket.sent.at(-1), {
    type: "conversation.cancel",
    payload: {
      session_id: "session-first-response",
      request_id: requestId,
      reason: "first-response timeout",
      protocol_version: 2,
    },
  });
});

test("a responding request fails once at the overall deadline", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const events: Record<string, unknown>[] = [];
  const client = createBackendClient({
    sessionId: "session-overall",
    requestTimeouts: {
      acceptedMs: 10,
      firstResponseMs: 10,
      overallMs: 20,
    },
    onEvent: (event) => { events.push(event); },
  });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();

  const requestId = client.send("finish within the deadline");
  socket.emit({ type: "request.accepted", request_id: requestId });
  socket.emit({ type: "response.delta", request_id: requestId, payload: { text: "working" } });
  await new Promise((resolve) => setTimeout(resolve, 35));

  const localFailures = events.filter(
    (event) => event.type === "request.failed" && event.local === true,
  );
  assert.equal(localFailures.length, 1);
  assert.equal(localFailures[0].request_id, requestId);
  assert.equal((localFailures[0].payload as Record<string, unknown>).phase, "overall");
  assert.equal(client.activeRequestId(), null);
  assert.deepEqual(client.reliabilitySnapshot().timeoutCounts, {
    accepted: 0,
    "first-response": 0,
    overall: 1,
  });
  assert.equal(client.reliabilitySnapshot().lastTimeoutPhase, "overall");
  assert.equal(client.reliabilitySnapshot().requestPhase, "timed-out");
});

test("reliability snapshots are redacted copies and publish request phase changes", () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const observed: ReturnType<ReturnType<typeof createBackendClient>["reliabilitySnapshot"]>[] = [];
  const client = createBackendClient({
    sessionId: "private-session-id",
    requestTimeouts: {
      acceptedMs: 100,
      firstResponseMs: 100,
      overallMs: 200,
    },
  });
  const unsubscribe = client.subscribeReliability((snapshot) => observed.push(snapshot));

  assert.equal(observed.length, 1);
  assert.equal(observed[0].schemaVersion, 1);
  assert.equal(observed[0].requestPhase, "idle");
  assert.equal(observed[0].connectionPhase, "idle");

  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();
  const requestId = client.send("private spoken request");
  assert.equal(client.reliabilitySnapshot().requestPhase, "waiting-accepted");

  socket.emit({ type: "request.accepted", request_id: requestId });
  assert.equal(client.reliabilitySnapshot().requestPhase, "waiting-first-response");
  socket.emit({
    type: "response.delta",
    request_id: requestId,
    payload: { text: "private response" },
  });
  assert.equal(client.reliabilitySnapshot().requestPhase, "responding");
  socket.emit({ type: "request.completed", request_id: requestId });
  assert.equal(client.reliabilitySnapshot().requestPhase, "terminal");

  const mutableCopy = client.reliabilitySnapshot();
  mutableCopy.timeoutCounts.accepted = 99;
  assert.equal(client.reliabilitySnapshot().timeoutCounts.accepted, 0);

  const serialized = JSON.stringify(client.reliabilitySnapshot());
  for (const secret of [
    "private-session-id",
    "private spoken request",
    "private response",
    requestId as string,
    "request_id",
    "session_id",
    "audio",
    "api_key",
  ]) {
    assert.equal(serialized.includes(secret), false, `snapshot leaked ${secret}`);
  }

  const observationCount = observed.length;
  unsubscribe();
  const unobservedRequestId = client.send("not observed after unsubscribe");
  assert.equal(observed.length, observationCount);
  client.cancel(unobservedRequestId as string, "test cleanup");
});

test("connection diagnostics count reconnect and recovery once and ignore retired sockets", () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const reconnectCallbacks: Array<() => void> = [];
  Object.assign(window, {
    clearTimeout: () => undefined,
    setTimeout: (callback: () => void) => {
      reconnectCallbacks.push(callback);
      return reconnectCallbacks.length;
    },
  });
  const client = createBackendClient({ sessionId: "connection-diagnostics" });

  client.connect();
  const first = FakeWebSocket.instances[0];
  assert.equal(client.reliabilitySnapshot().connectionPhase, "connecting");
  first.open();
  assert.equal(client.reliabilitySnapshot().connectionCount, 1);
  assert.equal(client.reliabilitySnapshot().recoveryCount, 0);
  assert.equal(client.reliabilitySnapshot().connectionPhase, "connected");

  first.fail();
  assert.equal(client.reliabilitySnapshot().connectionErrorCount, 1);
  first.remoteClose();
  assert.equal(client.reliabilitySnapshot().disconnectCount, 1);
  assert.equal(client.reliabilitySnapshot().reconnectCount, 1);
  assert.equal(client.reliabilitySnapshot().reconnectStreak, 1);
  assert.equal(client.reliabilitySnapshot().connectionPhase, "reconnecting");

  reconnectCallbacks.shift()?.();
  const second = FakeWebSocket.instances[1];
  second.open();
  const recovered = client.reliabilitySnapshot();
  assert.equal(recovered.connectionCount, 2);
  assert.equal(recovered.recoveryCount, 1);
  assert.equal(recovered.reconnectCount, 1);
  assert.equal(recovered.reconnectStreak, 0);
  assert.equal(recovered.connectionPhase, "recovered");

  first.fail();
  first.remoteClose();
  assert.deepEqual(client.reliabilitySnapshot(), recovered);
  assert.deepEqual(client.connectionSnapshot(), { http: false, websocket: true });
  assert.equal(reconnectCallbacks.length, 0);
});

test("dispose clears watchdogs and reconnects, closes sockets, and prevents later work", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  let nextTimerId = 1;
  const timers = new Map<number, () => void>();
  const cancelled = new Set<number>();
  Object.assign(window, {
    clearTimeout: (timerId: number) => { cancelled.add(timerId); },
    setTimeout: (callback: () => void) => {
      const timerId = nextTimerId;
      nextTimerId += 1;
      timers.set(timerId, callback);
      return timerId;
    },
  });
  const events: Record<string, unknown>[] = [];
  const observed: unknown[] = [];
  const client = createBackendClient({
    sessionId: "dispose-with-pending-work",
    backendOrigin: "http://127.0.0.1:49152/path-is-ignored",
    onEvent: (event) => events.push(event),
  });
  client.subscribeReliability((snapshot) => observed.push(snapshot));

  client.connect();
  const first = FakeWebSocket.instances[0];
  assert.equal(first.url, "ws://127.0.0.1:49152/ws");
  first.open();
  const requestId = client.send("must be disposed");
  first.remoteClose();
  assert.equal(client.activeRequestId(), requestId);
  assert.equal(client.reliabilitySnapshot().reconnectCount, 1);

  const observationsBeforeDispose = observed.length;
  client.dispose();
  client.dispose();
  assert.equal(client.activeRequestId(), null);
  assert.equal(client.queueSize(), 0);
  assert.equal(observed.length, observationsBeforeDispose);

  for (const [timerId, callback] of timers) {
    if (!cancelled.has(timerId)) callback();
  }
  await Promise.resolve();
  assert.equal(FakeWebSocket.instances.length, 1);
  assert.equal(events.length, 0);
  assert.equal(client.send("ignored after dispose"), null);
  client.connect();
  assert.equal(FakeWebSocket.instances.length, 1);

  const socketOwner = createBackendClient({ sessionId: "dispose-open-socket" });
  socketOwner.connect();
  const openSocket = FakeWebSocket.instances[1];
  openSocket.open();
  socketOwner.dispose();
  assert.equal(openSocket.closeCalls, 1);
  assert.equal(socketOwner.activeRequestId(), null);
});

test("a normal terminal event clears every request watchdog", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const events: Record<string, unknown>[] = [];
  const client = createBackendClient({
    sessionId: "session-terminal",
    requestTimeouts: {
      acceptedMs: 10,
      firstResponseMs: 10,
      overallMs: 10,
    },
    onEvent: (event) => { events.push(event); },
  });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();

  const requestId = client.send("complete normally");
  socket.emit({ type: "request.accepted", request_id: requestId });
  socket.emit({ type: "request.completed", request_id: requestId });
  await new Promise((resolve) => setTimeout(resolve, 25));

  const localFailures = events.filter(
    (event) => event.type === "request.failed" && event.local === true,
  );
  assert.equal(localFailures.length, 0);
  assert.equal(client.activeRequestId(), null);
});

test("explicit cancellation clears request watchdogs", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const events: Record<string, unknown>[] = [];
  const client = createBackendClient({
    sessionId: "session-cancel-timeout",
    requestTimeouts: {
      acceptedMs: 10,
      firstResponseMs: 10,
      overallMs: 10,
    },
    onEvent: (event) => { events.push(event); },
  });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();

  const requestId = client.send("cancel before timeout");
  assert.equal(client.cancel(requestId as string, "test cancellation"), true);
  await new Promise((resolve) => setTimeout(resolve, 25));

  assert.equal(
    events.filter((event) => event.type === "request.failed" && event.local === true).length,
    0,
  );
  assert.equal(client.activeRequestId(), null);
});

test("replacement clears old watchdogs and late acceptance cannot reclaim the active request", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const events: Record<string, unknown>[] = [];
  const client = createBackendClient({
    sessionId: "session-replacement",
    requestTimeouts: {
      acceptedMs: 10,
      firstResponseMs: 10,
      overallMs: 20,
    },
    onEvent: (event) => { events.push(event); },
  });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();

  const oldRequestId = client.send("old request");
  const newRequestId = client.send("new request");
  socket.emit({ type: "request.accepted", request_id: oldRequestId });
  assert.equal(client.activeRequestId(), newRequestId);
  socket.emit({ type: "request.accepted", request_id: newRequestId });
  socket.emit({ type: "request.completed", request_id: newRequestId });
  await new Promise((resolve) => setTimeout(resolve, 30));

  assert.equal(
    events.filter((event) => event.type === "request.failed" && event.local === true).length,
    0,
  );
  assert.equal(client.activeRequestId(), null);
});

test("local timeout drops late canonical progress while allowing its terminal event", async () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const events: Record<string, unknown>[] = [];
  const client = createBackendClient({
    sessionId: "session-retired-timeout",
    requestTimeouts: {
      acceptedMs: 10,
      firstResponseMs: 20,
      overallMs: 40,
    },
    onEvent: (event) => { events.push(event); },
  });

  const requestId = client.send("retire after timeout");
  const socket = FakeWebSocket.instances[0];
  await new Promise((resolve) => setTimeout(resolve, 25));
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "request.failed");
  assert.equal(events[0].local, true);

  socket.open();
  const runtimeAfterTimeout = runtimeStateCoordinator.snapshot();
  socket.emit(canonicalEvent(
    "session-retired-timeout",
    "request.accepted",
    requestId as string,
    1,
  ));
  socket.emit(canonicalEvent(
    "session-retired-timeout",
    "response.delta",
    requestId as string,
    2,
    { text: "too late" },
  ));

  assert.equal(events.length, 1);
  assert.deepEqual(runtimeStateCoordinator.snapshot(), runtimeAfterTimeout);

  socket.emit(canonicalEvent(
    "session-retired-timeout",
    "request.completed",
    requestId as string,
    3,
  ));
  assert.equal(events.length, 2);
  assert.equal(events[1].type, "request.completed");
  assert.equal(events[1].request_id, requestId);
});

test("replacement drops old progress and old failure cannot poison the new runtime", () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const events: Record<string, unknown>[] = [];
  const client = createBackendClient({
    sessionId: "session-retired-replacement",
    requestTimeouts: {
      acceptedMs: 50,
      firstResponseMs: 50,
      overallMs: 100,
    },
    onEvent: (event) => { events.push(event); },
  });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();

  const oldRequestId = client.send("old request");
  const newRequestId = client.send("new request");
  socket.emit(canonicalEvent(
    "session-retired-replacement",
    "request.accepted",
    oldRequestId as string,
    1,
  ));
  socket.emit(canonicalEvent(
    "session-retired-replacement",
    "response.delta",
    oldRequestId as string,
    2,
    { text: "stale" },
  ));

  assert.equal(events.length, 0);
  assert.equal(runtimeStateCoordinator.snapshot().state, "thinking");
  assert.equal(runtimeStateCoordinator.snapshot().requestId, newRequestId);

  socket.emit(canonicalEvent(
    "session-retired-replacement",
    "request.failed",
    oldRequestId as string,
    3,
    { error: "old request failed" },
  ));
  assert.equal(events.length, 1);
  assert.equal(events[0].request_id, oldRequestId);
  assert.equal(runtimeStateCoordinator.snapshot().state, "thinking");
  assert.equal(runtimeStateCoordinator.snapshot().requestId, newRequestId);

  socket.emit(canonicalEvent(
    "session-retired-replacement",
    "request.completed",
    newRequestId as string,
    4,
  ));
  assert.equal(client.activeRequestId(), null);
});

test("verified continuous voice sends one strict provenance reference", () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const client = createBackendClient({ sessionId: "session-voice-provenance" });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();
  const reference = voiceProvenance("session-voice-provenance");

  const requestId = client.send("verified spoken request", reference as never);
  const payload = socket.sent.at(-1)?.payload as Record<string, unknown>;

  assert.equal(payload.request_id, requestId);
  assert.deepEqual(payload.voice_provenance, reference);
  assert.notEqual(payload.voice_provenance, reference);
  assert.throws(
    () => client.send(
      "tampered spoken request",
      { ...reference, modality: "voice" } as never,
    ),
    /voice provenance/i,
  );
  assert.equal(client.activeRequestId(), requestId);
  client.cancel(requestId as string, "test cleanup");
});

test("late explicit cancellation cannot clear or target the replacement request", () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const client = createBackendClient({ sessionId: "session-scoped-cancel" });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();
  const oldRequestId = client.send("old request") as string;
  const newRequestId = client.send("new request") as string;

  assert.equal(client.cancel(oldRequestId, "delayed voice barge-in"), true);
  assert.equal(client.activeRequestId(), newRequestId);
  assert.deepEqual(socket.sent.at(-1), {
    type: "conversation.cancel",
    payload: {
      session_id: "session-scoped-cancel",
      request_id: oldRequestId,
      reason: "delayed voice barge-in",
      protocol_version: 2,
    },
  });
  client.cancel(newRequestId, "test cleanup");
});

for (const terminalType of ["request.completed", "request.cancelled"] as const) {
  test(`${terminalType} without a request ID is normalized to the active request`, async () => {
    installBrowserFakes();
    FakeWebSocket.instances = [];
    const events: Record<string, unknown>[] = [];
    const sessionId = `session-empty-id-${terminalType}`;
    const client = createBackendClient({
      sessionId,
      requestTimeouts: {
        acceptedMs: 10,
        firstResponseMs: 10,
        overallMs: 10,
      },
      onEvent: (event) => { events.push(event); },
    });
    client.connect();
    const socket = FakeWebSocket.instances[0];
    socket.open();

    const requestId = client.send("finish without echoing the request id");
    socket.emit(canonicalEvent(sessionId, terminalType, "", 1));

    assert.equal(client.activeRequestId(), null);
    assert.equal(events.length, 1);
    assert.equal(events[0].request_id, requestId);
    await new Promise((resolve) => setTimeout(resolve, 25));
    assert.equal(
      events.filter((event) => event.type === "request.failed" && event.local === true).length,
      0,
    );
  });
}
