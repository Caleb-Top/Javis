import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const windowModeSource = readFileSync(
  new URL("../src/desktop/windowMode.ts", import.meta.url),
  "utf8",
);
const rustSource = readFileSync(
  new URL("../src-tauri/src/main.rs", import.meta.url),
  "utf8",
);

test("left-side menu uses one native bounds operation instead of visible two-step movement", () => {
  assert.match(windowModeSource, /set_main_window_bounds/);
  assert.match(windowModeSource, /invoke<.*>\("set_main_window_bounds"/s);
  assert.match(rustSource, /fn set_main_window_bounds/);
  assert.match(rustSource, /SetWindowPos/);
  assert.match(rustSource, /set_main_window_bounds,/);
});
