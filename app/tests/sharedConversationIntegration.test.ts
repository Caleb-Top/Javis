import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");

test("the native shell owns one conversation and one event reducer", () => {
  assert.match(mainSource, /getOrCreateConversationId/);
  assert.match(mainSource, /new ConversationEventReducer\(\s*conversationId/);
  assert.match(mainSource, /createBackendClient\(\{[\s\S]*sessionId: conversationId/);
  assert.match(mainSource, /openCodeSurface\(conversationId\)/);
  assert.doesNotMatch(mainSource, /event\.type === "text_delta"/);
});
