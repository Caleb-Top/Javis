import assert from "node:assert/strict";
import test from "node:test";

import * as windowGeometry from "../src/desktop/windowGeometry.ts";

const {
  getCenteredWindowPosition,
  getClampedWindowPosition,
} = windowGeometry;

test("centers a large surface inside the current monitor", () => {
  assert.deepEqual(
    getCenteredWindowPosition(
      { x: 0, y: 0, width: 1920, height: 1080 },
      { width: 1280, height: 820 },
    ),
    { x: 320, y: 130 },
  );
});

test("centers correctly on a monitor with a non-zero origin", () => {
  assert.deepEqual(
    getCenteredWindowPosition(
      { x: 1920, y: -200, width: 2560, height: 1440 },
      { width: 1280, height: 820 },
    ),
    { x: 2560, y: 110 },
  );
});

test("clamps a compact surface inside monitor bounds", () => {
  assert.deepEqual(
    getClampedWindowPosition(
      { x: 1850, y: -20 },
      { width: 320, height: 320 },
      { x: 0, y: 0, width: 1920, height: 1080 },
      8,
    ),
    { x: 1592, y: 8 },
  );
});

test("keeps the same visible amount when a compact surface crosses any monitor edge", () => {
  const getPartiallyVisibleWindowPosition = (
    windowGeometry as typeof windowGeometry & {
      getPartiallyVisibleWindowPosition: (
        position: { x: number; y: number },
        window: { width: number; height: number },
        monitor: { x: number; y: number; width: number; height: number },
        minimumVisible: number,
      ) => { x: number; y: number };
    }
  ).getPartiallyVisibleWindowPosition;
  const monitor = { x: 0, y: 0, width: 1920, height: 1080 };
  const compact = { width: 200, height: 200 };

  assert.deepEqual(
    getPartiallyVisibleWindowPosition({ x: -500, y: -500 }, compact, monitor, 32),
    { x: -168, y: -168 },
  );
  assert.deepEqual(
    getPartiallyVisibleWindowPosition({ x: 2500, y: 1500 }, compact, monitor, 32),
    { x: 1888, y: 1048 },
  );
});
