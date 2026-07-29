import assert from "node:assert/strict";
import test from "node:test";

import {
  closeSurfaceMenu,
  createClosedMenuState,
  getSurfaceMenuWindowTransition,
  openSurfaceMenu,
} from "../src/menu/surfaceMenuState.ts";

test("opens the first Live context menu without changing the source mode", () => {
  const opened = openSurfaceMenu(createClosedMenuState(), "live", "right");

  assert.equal(opened.open, true);
  assert.equal(opened.source, "live");
  assert.equal(opened.placement, "right");
});

test("keeps the first source while the menu is already open", () => {
  const opened = openSurfaceMenu(createClosedMenuState(), "live", "right");
  const repeated = openSurfaceMenu(opened, "pet", "left");

  assert.deepEqual(repeated, opened);
});

test("closes without losing the source needed for geometry restoration", () => {
  const opened = openSurfaceMenu(createClosedMenuState(), "pet", "left");
  const closed = closeSurfaceMenu(opened);

  assert.equal(closed.open, false);
  assert.equal(closed.source, "pet");
  assert.equal(closed.placement, "left");
});

test("restores Live geometry after a left-side menu closes", () => {
  const opening = getSurfaceMenuWindowTransition("live", 1, false, true, "left");
  const closing = getSurfaceMenuWindowTransition("live", 1, true, false, "left");

  assert.deepEqual(
    { width: opening.width, height: opening.height, baseWidth: opening.baseWidth },
    { width: 316, height: 218, baseWidth: 200 },
  );
  assert.equal(opening.surfaceOffsetX, 116);
  assert.equal(opening.windowDeltaX, -116);
  assert.deepEqual(
    { width: closing.width, height: closing.height, baseWidth: closing.baseWidth },
    { width: 200, height: 218, baseWidth: 200 },
  );
  assert.equal(closing.surfaceOffsetX, 0);
  assert.equal(closing.windowDeltaX, 116);
});

test("uses the scaled Pet geometry and restores it after closing", () => {
  const opening = getSurfaceMenuWindowTransition("pet", 0.7, false, true, "right");
  const closing = getSurfaceMenuWindowTransition("pet", 0.7, true, false, "right");

  assert.deepEqual(
    { width: opening.width, height: opening.height, baseWidth: opening.baseWidth },
    { width: 240, height: 200, baseWidth: 124 },
  );
  assert.equal(opening.surfaceOffsetX, 0);
  assert.equal(opening.windowDeltaX, 0);
  assert.deepEqual(
    { width: closing.width, height: closing.height, baseWidth: closing.baseWidth },
    { width: 124, height: 156, baseWidth: 124 },
  );
});
