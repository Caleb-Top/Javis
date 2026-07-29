import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const windowDragSource = readFileSync(
  new URL("../src/desktop/windowDrag.ts", import.meta.url),
  "utf8",
);
const compactDragSource = windowDragSource.split(
  "export function installWindowDragRegions",
)[0];

test("compact Live and Pet surfaces use symmetric controlled dragging", () => {
  assert.match(compactDragSource, /getPartiallyVisibleWindowPosition/);
  assert.match(compactDragSource, /window\.setPosition\(new LogicalPosition\(/);
  assert.doesNotMatch(compactDragSource, /\.startDragging\(\)/);
});

test("pointer capture is cleared before its release event can re-enter reset", () => {
  assert.match(
    compactDragSource,
    /const activePointerId = pointerId;\s+pointerId = null;\s+if \(activePointerId !== null/,
  );
});
