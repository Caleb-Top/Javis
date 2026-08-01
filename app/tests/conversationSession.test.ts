import assert from "node:assert/strict";
import test from "node:test";

import {
  CONVERSATION_ID_PREFERENCE,
  getOrCreateConversationId,
} from "../src/conversation/conversationSession.ts";

class MapStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

test("the conversation id survives Live and Code surface changes", () => {
  const storage = new MapStorage();
  const first = getOrCreateConversationId(storage, () => "session-1");
  const second = getOrCreateConversationId(storage, () => "session-2");

  assert.equal(first, "session-1");
  assert.equal(second, "session-1");
  assert.equal(storage.getItem(CONVERSATION_ID_PREFERENCE), "session-1");
});

test("invalid stored conversation ids are replaced", () => {
  const storage = new MapStorage();
  storage.setItem(CONVERSATION_ID_PREFERENCE, "../not-a-session");

  assert.equal(getOrCreateConversationId(storage, () => "session-safe"), "session-safe");
});
