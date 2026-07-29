import assert from "node:assert/strict";
import test from "node:test";

import { buildPetMenuItems } from "../src/pet/petMenu.ts";
import type { PetPreferences } from "../src/pet/petPreferences.ts";

test("always starts with Settings and Code", () => {
  const preferences: PetPreferences = { version: 1, scale: 1, shortcuts: [] };
  assert.deepEqual(
    buildPetMenuItems(preferences).map((item) => item.id),
    ["settings", "code"],
  );
});

test("adds only configured custom shortcuts in slot order", () => {
  const preferences: PetPreferences = {
    version: 1,
    scale: 1,
    shortcuts: [
      { label: "Docs", kind: "url", value: "https://example.com" },
      { label: "", kind: "target", value: "C:\\tool.exe" },
      { label: "Keys", kind: "hotkey", value: "Ctrl+Shift+K" },
    ],
  };

  const items = buildPetMenuItems(preferences);
  assert.deepEqual(items.map((item) => item.id), [
    "settings",
    "code",
    "shortcut-0",
    "shortcut-1",
  ]);
  assert.equal(items[2].label, "Docs");
  assert.equal(items[3].label, "Keys");
});

test("never returns more than five menu items", () => {
  const preferences = {
    version: 1,
    scale: 1,
    shortcuts: Array.from({ length: 8 }, (_, index) => ({
      label: `Item ${index}`,
      kind: "target",
      value: `C:\\item-${index}.exe`,
    })),
  } as PetPreferences;

  assert.equal(buildPetMenuItems(preferences).length, 5);
});
