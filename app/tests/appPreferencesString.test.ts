import assert from "node:assert/strict";
import test from "node:test";

import {
  readStringPreference,
  writeStringPreference,
} from "../src/app/AppPreferences.ts";

test("string preferences persist conversation cursors", () => {
  const values = new Map<string, string>();
  Object.assign(globalThis, {
    localStorage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });

  assert.equal(readStringPreference("conversation.cursor", "0"), "0");
  writeStringPreference("conversation.cursor", "42");
  assert.equal(readStringPreference("conversation.cursor", "0"), "42");
});
