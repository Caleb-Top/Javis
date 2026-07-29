import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_PET_PREFERENCES,
  getConfiguredShortcuts,
  parsePetPreferences,
  type PetPreferences,
} from "../src/pet/petPreferences.ts";

test("uses defaults for missing or malformed preferences", () => {
  assert.deepEqual(parsePetPreferences(null), DEFAULT_PET_PREFERENCES);
  assert.deepEqual(parsePetPreferences("{broken"), DEFAULT_PET_PREFERENCES);
});

test("clamps pet scale to the supported range and step", () => {
  assert.equal(parsePetPreferences({ version: 1, scale: 0.2, shortcuts: [] }).scale, 0.7);
  assert.equal(parsePetPreferences({ version: 1, scale: 1.8, shortcuts: [] }).scale, 1.3);
  assert.equal(parsePetPreferences({ version: 1, scale: 1.127, shortcuts: [] }).scale, 1.15);
});

test("hides incomplete shortcuts and caps configured shortcuts at three", () => {
  const input: PetPreferences = {
    version: 1,
    scale: 1,
    shortcuts: [
      { label: "Live", kind: "javis", value: "live" },
      { label: "", kind: "url", value: "https://example.com" },
      { label: "Bad URL", kind: "url", value: "file:///tmp/a" },
      { label: "Docs", kind: "url", value: "https://example.com/docs" },
      { label: "Hotkey", kind: "hotkey", value: "Ctrl+Shift+K" },
      { label: "Extra", kind: "target", value: "C:\\extra.exe" },
    ],
  };

  assert.deepEqual(getConfiguredShortcuts(input), [
    { label: "Live", kind: "javis", value: "live" },
    { label: "Docs", kind: "url", value: "https://example.com/docs" },
    { label: "Hotkey", kind: "hotkey", value: "Ctrl+Shift+K" },
  ]);
});

test("accepts only allowlisted Javis actions and normalized hotkeys", () => {
  const input: PetPreferences = {
    version: 1,
    scale: 1,
    shortcuts: [
      { label: "Settings", kind: "javis", value: "settings" },
      { label: "Unknown", kind: "javis", value: "shutdown" },
      { label: "Invalid key", kind: "hotkey", value: "Ctrl + ???" },
    ],
  };

  assert.deepEqual(getConfiguredShortcuts(input), [
    { label: "Settings", kind: "javis", value: "settings" },
  ]);
});
