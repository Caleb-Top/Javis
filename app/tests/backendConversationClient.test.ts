import assert from "node:assert/strict";
import test from "node:test";

import { createBackendClient } from "../src/bridge/backendClient.ts";

class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState = FakeWebSocket.CONNECTING;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  send(value: string): void {
    this.sent.push(JSON.parse(value) as Record<string, unknown>);
  }

  emit(payload: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

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
  client.cancel("test cleanup");
});

test("cancel targets the active request and prevents queued replay", () => {
  installBrowserFakes();
  FakeWebSocket.instances = [];
  const client = createBackendClient({ sessionId: "session-1" });
  client.connect();
  const socket = FakeWebSocket.instances[0];
  socket.open();
  const requestId = client.send("a long task");

  assert.equal(client.cancel("new voice input"), true);
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

  client.send("cancel before timeout");
  assert.equal(client.cancel("test cancellation"), true);
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
