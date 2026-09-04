import assert from "node:assert/strict";
import test from "node:test";

import type {
  RuntimeAccessProvider,
  RuntimeAccessScope,
} from "../src/bridge/runtimeAccess.ts";
import {
  createMemoryClient,
  MemoryClientError,
} from "../src/memory/MemoryClient.ts";

const TOKEN = "m".repeat(43);
const NOW = "2026-08-20T10:00:00.000Z";
const ENDED = "2026-08-20T10:05:00.000Z";

function runtimeAccess(tokens: Partial<Record<RuntimeAccessScope, string>> = {}): RuntimeAccessProvider {
  const available = new Map<RuntimeAccessScope, string>([
    ["memory.read", TOKEN],
    ["memory.manage", TOKEN],
    ["memory.delete", TOKEN],
    ...Object.entries(tokens) as Array<[RuntimeAccessScope, string]>,
  ]);
  return {
    async ensure() {
      return {
        schemaVersion: 1,
        revision: 1,
        ready: true,
        runtimeBootId: "boot-memory-client",
        clientInstanceId: "desktop-main",
        scopes: [...available.keys()],
        expiresAtEpoch: 9_999_999_999,
      };
    },
    tokenForScope(scope) { return available.get(scope) ?? ""; },
    headers(scope) {
      const token = available.get(scope);
      return token ? { "X-Javis-Runtime-Capability": token } : {};
    },
    webSocketProtocols() { return []; },
    snapshot() {
      return {
        schemaVersion: 1,
        revision: 1,
        ready: true,
        runtimeBootId: "boot-memory-client",
        clientInstanceId: "desktop-main",
        scopes: [...available.keys()],
        expiresAtEpoch: 9_999_999_999,
      };
    },
    subscribe() { return () => {}; },
    dispose() {},
  };
}

function response(value: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return value; },
  };
}

function episodePage() {
  return {
    schema_version: 1,
    items: [{
      schema_version: 1,
      episode_id: "episode-client-1",
      revision: 1,
      started_at_utc: NOW,
      ended_at_utc: ENDED,
      what_happened: "A verified local memory was retained.",
      intent_summary: "Retain evidence",
      action_summary: "Stored governed memory",
      verified_result_summary: "Evidence verified",
      source_event_ids: ["event-client-1"],
      source_message_ids: ["message-client-1"],
      status: "active",
    }],
    next_cursor: null,
  };
}

test("memory client sends capability and session headers and parses source evidence", async () => {
  const requests: Array<{ url: string; init: RequestInit }> = [];
  const client = createMemoryClient({
    runtimeAccess: runtimeAccess(),
    sessionId: () => "session-client-1",
    backendOrigin: "http://127.0.0.1:8080/path-is-removed",
    fetch: async (input, init = {}) => {
      requests.push({ url: String(input), init });
      return response(episodePage());
    },
  });

  const page = await client.listEpisodes();

  assert.equal(requests[0].url, "http://127.0.0.1:8080/api/life/memory/episodes?limit=100");
  const headers = new Headers(requests[0].init.headers);
  assert.equal(headers.get("X-Javis-Runtime-Capability"), TOKEN);
  assert.equal(headers.get("X-Javis-Session-Id"), "session-client-1");
  assert.deepEqual(page.items[0].sourceEvidenceIds, ["event-client-1", "message-client-1"]);
  assert.equal(JSON.stringify(page).includes("subject"), false);
});

test("memory mutations preserve idempotency keys and never add authority fields", async () => {
  const bodies: Array<Record<string, unknown>> = [];
  const client = createMemoryClient({
    runtimeAccess: runtimeAccess(),
    sessionId: () => "session-client-2",
    fetch: async (_input, init = {}) => {
      bodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return response({
        schema_version: 1,
        command_id: "memory-command-client-2",
        status: "completed",
        resource_id: "shared-client-2",
      });
    },
  });
  const input = {
    sourceEpisodeIds: ["episode-client-2"],
    proposedText: "A shared memory pending explicit confirmation.",
    idempotencyKey: "proposal-client-2",
  };

  const first = await client.proposeShared(input);
  const replay = await client.proposeShared(input);

  assert.deepEqual(first, replay);
  assert.deepEqual(bodies[0], bodies[1]);
  assert.equal(bodies[0].idempotency_key, "proposal-client-2");
  assert.deepEqual(
    Object.keys(bodies[0]).sort(),
    ["idempotency_key", "proposed_text", "source_episode_ids"],
  );
});

test("memory client surfaces nested FastAPI conflicts without leaking sensitive details", async () => {
  const client = createMemoryClient({
    runtimeAccess: runtimeAccess(),
    sessionId: () => "session-client-3",
    fetch: async () => response({
      detail: {
        code: "memory_revision_conflict",
        message: `owner subject token ${TOKEN}`,
      },
    }, 409),
  });

  await assert.rejects(client.status(), (error: unknown) => {
    assert.ok(error instanceof MemoryClientError);
    assert.equal(error.status, 409);
    assert.equal(error.code, "memory_revision_conflict");
    assert.equal(error.message, "记忆已发生变化，请刷新后重试");
    assert.equal(error.message.includes(TOKEN), false);
    return true;
  });
});

test("memory client keeps read, manage, and delete scopes separate", async () => {
  const readOnly = runtimeAccess({
    "memory.manage": "",
    "memory.delete": "",
  });
  let fetches = 0;
  const client = createMemoryClient({
    runtimeAccess: readOnly,
    sessionId: () => "session-client-4",
    fetch: async () => {
      fetches += 1;
      return response({});
    },
  });

  await assert.rejects(
    client.confirmShared({
      sharedMemoryId: "shared-client-4",
      revision: 1,
      idempotencyKey: "confirm-client-4",
    }),
    (error: unknown) => error instanceof MemoryClientError && error.code === "memory_capability_missing",
  );
  assert.equal(fetches, 0);
});

test("time-range deletion serializes both explicit source handling modes", async () => {
  const bodies: Array<Record<string, unknown>> = [];
  const client = createMemoryClient({
    runtimeAccess: runtimeAccess(),
    sessionId: () => "session-client-5",
    fetch: async (_input, init = {}) => {
      bodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return response({
        schema_version: 1,
        command_id: "memory-command-delete",
        status: "completed",
        resource_id: "deletion-client-5",
      });
    },
  });

  await client.forget({
    scope: "time_range",
    sourceHandling: "source_and_derived",
    rangeStartedAtUtc: NOW,
    rangeEndedAtUtc: ENDED,
    reasonCode: "user_requested",
    idempotencyKey: "delete-client-5",
  });

  assert.equal(bodies[0].source_handling, "source_and_derived");
  assert.equal(bodies[0].range_started_at_utc, NOW);
  assert.equal(bodies[0].range_ended_at_utc, ENDED);
  assert.equal("owner_subject_id" in bodies[0], false);
});
