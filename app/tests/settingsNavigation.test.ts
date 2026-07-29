import assert from "node:assert/strict";
import test from "node:test";

import {
  getSettingsReturnMode,
  type SettingsSourceMode,
} from "../src/settings/settingsNavigation.ts";

for (const mode of ["live", "pet", "code"] as const) {
  test(`returns from Settings to ${mode}`, () => {
    assert.equal(getSettingsReturnMode(mode), mode);
  });
}

test("falls back to Live for an unknown source mode", () => {
  assert.equal(getSettingsReturnMode("settings" as SettingsSourceMode), "live");
});
