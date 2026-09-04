import assert from "node:assert/strict";
import test from "node:test";

import {
  getOrCreateRuntimeClientInstanceId,
  type RuntimeIdentityStorage,
} from "../src/bridge/runtimeIdentity.ts";

function memoryStorage(initial: string | null = null) {
  let value = initial;
  let writes = 0;
  const storage: RuntimeIdentityStorage = {
    getItem() { return value; },
    setItem(_key, next) {
      value = next;
      writes += 1;
    },
  };
  return { storage, value: () => value, writes: () => writes };
}

test("runtime client identity remains stable across desktop reloads", () => {
  const local = memoryStorage();
  const first = getOrCreateRuntimeClientInstanceId(
    local.storage,
    () => "0123456789abcdef0123456789abcdef",
  );
  const second = getOrCreateRuntimeClientInstanceId(
    local.storage,
    () => "ffffffffffffffffffffffffffffffff",
  );

  assert.equal(first, "desktop-main-0123456789abcdef0123456789abcdef");
  assert.equal(second, first);
  assert.equal(local.value(), first);
  assert.equal(local.writes(), 1);
});

test("invalid persisted identity is replaced with a strictly bounded value", () => {
  const local = memoryStorage("desktop-main-forged\nvalue");
  const value = getOrCreateRuntimeClientInstanceId(
    local.storage,
    () => "abcdefabcdefabcdefabcdefabcdefab",
  );

  assert.match(value, /^desktop-main-[0-9a-f]{32}$/);
  assert.equal(local.writes(), 1);
});

test("storage denial degrades to a valid ephemeral identity", () => {
  const denied: RuntimeIdentityStorage = {
    getItem() { throw new Error("denied"); },
    setItem() { throw new Error("denied"); },
  };

  assert.equal(
    getOrCreateRuntimeClientInstanceId(
      denied,
      () => "11111111111111111111111111111111",
    ),
    "desktop-main-11111111111111111111111111111111",
  );
});
