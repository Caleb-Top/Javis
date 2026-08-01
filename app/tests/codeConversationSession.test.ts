import assert from "node:assert/strict";
import test from "node:test";

import { buildCodeSurfaceUrl } from "../src/code/CodeSurface.ts";

test("Code attaches to the native conversation without invoking the model", () => {
  const url = new URL(buildCodeSurfaceUrl("session-1", false));

  assert.equal(url.searchParams.get("app_embed"), "1");
  assert.equal(url.searchParams.get("session_id"), "session-1");
  assert.equal(url.searchParams.has("reload"), false);
});
