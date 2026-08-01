import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { resolveBackendEndpoints } from "../src/bridge/backendEndpoints.ts";

test("production backend defaults to the packaged runtime port", () => {
  assert.deepEqual(resolveBackendEndpoints(""), {
    http: "http://127.0.0.1:8080",
    websocket: "ws://127.0.0.1:8080",
  });
});

test("development can target an independent source backend", () => {
  assert.deepEqual(resolveBackendEndpoints("http://127.0.0.1:8091/ignored"), {
    http: "http://127.0.0.1:8091",
    websocket: "ws://127.0.0.1:8091",
  });
});

test("non-http backend origins are rejected", () => {
  assert.throws(
    () => resolveBackendEndpoints("file:///Z:/Portable/Javis"),
    /http or https/,
  );
});

test("Code Workbench uses the same configured backend instead of a hard-coded port", () => {
  const source = readFileSync(
    new URL("../src/code/CodeSurface.ts", import.meta.url),
    "utf8",
  );
  assert.match(source, /resolveBackendEndpoints/);
  assert.doesNotMatch(source, /const LEGACY_WEB_URL = "http:\/\/127\.0\.0\.1:8080/);
});
