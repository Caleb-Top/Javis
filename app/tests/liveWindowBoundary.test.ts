import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  LIVE_ORB_RENDER_SCALE,
  LIVE_SURFACE_MIN_HEIGHT,
  LIVE_SURFACE_MIN_SIZE,
  LIVE_SURFACE_SIZE,
} from "../src/desktop/surfaceDimensions.ts";
import { getSurfaceMenuWindowTransition } from "../src/menu/surfaceMenuState.ts";

const tauriConfig = JSON.parse(
  readFileSync(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"),
) as {
  app: {
    windows: Array<{
      width: number;
      height: number;
      minWidth: number;
      minHeight: number;
    }>;
  };
};
const windowModeSource = readFileSync(
  new URL("../src/desktop/windowMode.ts", import.meta.url),
  "utf8",
);

test("Live uses a compact native footprint around the rendered orb", () => {
  assert.equal(LIVE_SURFACE_SIZE, 200);
  assert.equal(LIVE_SURFACE_MIN_SIZE, 132);
  assert.equal(LIVE_SURFACE_MIN_HEIGHT, 158);
  assert.equal(LIVE_ORB_RENDER_SCALE, 1.18);

  const window = tauriConfig.app.windows[0];
  assert.deepEqual(
    {
      width: window.width,
      height: window.height,
      minWidth: window.minWidth,
      minHeight: window.minHeight,
    },
    { width: 200, height: 218, minWidth: 132, minHeight: 158 },
  );
});

test("the Live menu expands beside the compact orb without moving it", () => {
  const opening = getSurfaceMenuWindowTransition(
    "live",
    1,
    false,
    true,
    "right",
  );

  assert.deepEqual(
    {
      width: opening.width,
      height: opening.height,
      baseWidth: opening.baseWidth,
      surfaceOffsetX: opening.surfaceOffsetX,
    },
    { width: 316, height: 218, baseWidth: 200, surfaceOffsetX: 0 },
  );
});

test("compact surfaces may sit flush against monitor edges", () => {
  assert.match(
    windowModeSource,
    /positionWindow\(live, false, 0\)/,
  );
  assert.match(windowModeSource, /positionWindow\(compact, false, 0\)/);
});
