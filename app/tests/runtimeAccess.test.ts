import assert from "node:assert/strict";
import test from "node:test";

import {
  createRuntimeAccessProvider,
  type RuntimeAccessIssueRequest,
} from "../src/bridge/runtimeAccess.ts";

function issued(
  request: RuntimeAccessIssueRequest,
  token: string,
  expiresAtEpoch: number,
): string {
  return JSON.stringify({
    ok: true,
    token,
    runtime_boot_id: "boot-1",
    client_instance_id: request.clientInstanceId,
    scopes: request.scopes,
    issued_at_epoch: 100,
    expires_at_epoch: expiresAtEpoch,
  });
}

test("runtime access keeps the bearer out of snapshots and projects exact scopes", async () => {
  const requests: RuntimeAccessIssueRequest[] = [];
  const provider = createRuntimeAccessProvider({
    clientInstanceId: "desktop-main",
    now: () => 100,
    issue: async (request) => {
      requests.push(request);
      return issued(request, "a".repeat(43), 160);
    },
  });

  await provider.ensure();

  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].scopes, [
    "conversation",
    "diagnostics.read",
    "life.read",
    "playback",
    "voice.capture",
  ]);
  assert.deepEqual(provider.webSocketProtocols("conversation"), [
    "javis-runtime-v1",
    `javis-capability.${"a".repeat(43)}`,
  ]);
  assert.deepEqual(provider.headers("playback"), {
    "X-Javis-Runtime-Capability": "a".repeat(43),
  });
  assert.equal(JSON.stringify(provider.snapshot()).includes("a".repeat(43)), false);
  provider.dispose();
});

test("runtime access coalesces refreshes and rejects malformed grants", async () => {
  let calls = 0;
  let release!: (value: string) => void;
  const pending = new Promise<string>((resolve) => { release = resolve; });
  const provider = createRuntimeAccessProvider({
    clientInstanceId: "desktop-main",
    now: () => 100,
    issue: async () => {
      calls += 1;
      return pending;
    },
  });

  const first = provider.ensure();
  const second = provider.ensure();
  assert.equal(calls, 1);
  release(JSON.stringify({ ok: true, token: "short" }));
  await assert.rejects(first, /runtime capability/i);
  await assert.rejects(second, /runtime capability/i);
  assert.deepEqual(provider.webSocketProtocols("conversation"), []);
  provider.dispose();
});

test("runtime access drops an expired bearer and publishes successful rotation", async () => {
  let now = 100;
  let generation = 0;
  const snapshots: Array<{ ready: boolean; revision: number }> = [];
  const provider = createRuntimeAccessProvider({
    clientInstanceId: "desktop-main",
    now: () => now,
    refreshSkewSeconds: 0,
    issue: async (request) => {
      generation += 1;
      return issued(request, String(generation).repeat(43), now + 10);
    },
  });
  const unsubscribe = provider.subscribe((snapshot) => {
    snapshots.push({ ready: snapshot.ready, revision: snapshot.revision });
  });

  await provider.ensure();
  assert.equal(provider.tokenForScope("life.read"), "1".repeat(43));
  now = 110;
  assert.equal(provider.tokenForScope("life.read"), "");
  await provider.ensure();
  assert.equal(provider.tokenForScope("life.read"), "2".repeat(43));
  assert.deepEqual(snapshots.map((item) => item.ready), [false, true, true]);

  unsubscribe();
  provider.dispose();
});
