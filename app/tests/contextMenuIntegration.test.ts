import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const windowModeSource = readFileSync(
  new URL("../src/desktop/windowMode.ts", import.meta.url),
  "utf8",
);

test("the first Live right-click opens the shared menu without entering Pet mode", () => {
  const handler = mainSource.match(
    /orbCanvas\.addEventListener\("contextmenu",[\s\S]*?\n\}\);/,
  )?.[0] || "";

  assert.match(handler, /surfaceMenu\.open\("live"\)/);
  assert.doesNotMatch(handler, /setDesktopMode\("pet"\)/);
});

test("Settings opens its own surface without opening Code first", () => {
  const handler = mainSource.match(
    /const showSettingsSurface[\s\S]*?\n\};/,
  )?.[0] || "";

  assert.match(handler, /setDesktopMode\("settings"\)/);
  assert.doesNotMatch(handler, /openCodeSurface\(\)/);
});

test("Live and Pet modes select their own compact geometry source", () => {
  assert.match(
    windowModeSource,
    /if \(mode === "live" \|\| mode === "pet"\)\s*\{\s*surfaceMenuSource = mode;/,
  );
});
