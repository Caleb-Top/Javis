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
