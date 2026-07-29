import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import * as surfaceDimensions from "../src/desktop/surfaceDimensions.ts";
import { getCompactPetSize } from "../src/pet/petWindowLayout.ts";

const windowModeSource = readFileSync(
  new URL("../src/desktop/windowMode.ts", import.meta.url),
  "utf8",
);
const settingsSource = readFileSync(
  new URL("../src/settings/SettingsSurface.ts", import.meta.url),
  "utf8",
);

test("one assistant scale controls both Live and Pet visual footprints", () => {
  const getLiveSurfaceSize = (
    surfaceDimensions as typeof surfaceDimensions & {
      getLiveSurfaceSize: (scale: number) => {
        visual: number;
        width: number;
        height: number;
      };
    }
  ).getLiveSurfaceSize;

  assert.deepEqual(getLiveSurfaceSize(1), { visual: 200, width: 200, height: 218 });
  assert.deepEqual(getLiveSurfaceSize(0.7), { visual: 140, width: 140, height: 158 });
  assert.deepEqual(getLiveSurfaceSize(1.3), { visual: 260, width: 260, height: 278 });
  assert.deepEqual(getCompactPetSize(1), {
    boundary: 148,
    width: 168,
    height: 200,
  });
});

test("window mode applies the selected scale to Live as well as Pet", () => {
  assert.match(windowModeSource, /getLiveSurfaceSize\(petScale\)/);
  assert.match(
    windowModeSource,
    /document\.body\.dataset\.desktopMode === "live"[\s\S]*?getSurfaceMenuWindowTransition\(\s*"live"/,
  );
});

test("Settings provides an immediate assistant-size preview", () => {
  assert.match(settingsSource, /assistant-scale-preview/);
  assert.match(settingsSource, /updateAssistantScalePreview/);
  assert.match(settingsSource, /助手大小/);
});
