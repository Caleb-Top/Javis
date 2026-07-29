import assert from "node:assert/strict";
import test from "node:test";

import {
  PET_MENU_EXTRA_WIDTH,
  chooseMenuPlacement,
  getCompactPetSize,
  getExpandedPetLayout,
  getPetWindowTransition,
} from "../src/pet/petWindowLayout.ts";

test("keeps ten logical pixels of transparent padding around the pet boundary", () => {
  assert.deepEqual(getCompactPetSize(1), {
    boundary: 148,
    width: 168,
    height: 200,
  });
});

test("scales compact pet dimensions at both supported extremes", () => {
  assert.deepEqual(getCompactPetSize(0.7), {
    boundary: 104,
    width: 124,
    height: 156,
  });
  assert.deepEqual(getCompactPetSize(1.3), {
    boundary: 192,
    width: 212,
    height: 244,
  });
});

test("expands right without moving the pet inside the window", () => {
  const layout = getExpandedPetLayout(1, "right");
  assert.equal(layout.width, 168 + PET_MENU_EXTRA_WIDTH);
  assert.equal(layout.petOffsetX, 0);
  assert.equal(layout.windowDeltaX, 0);
});

test("expands left while preserving the pet screen position", () => {
  const layout = getExpandedPetLayout(1, "left");
  assert.equal(layout.width, 168 + PET_MENU_EXTRA_WIDTH);
  assert.equal(layout.petOffsetX, PET_MENU_EXTRA_WIDTH);
  assert.equal(layout.windowDeltaX, -PET_MENU_EXTRA_WIDTH);
});

test("chooses the side with enough monitor space", () => {
  assert.equal(chooseMenuPlacement(20, 168, 0, 1920), "right");
  assert.equal(chooseMenuPlacement(1740, 168, 0, 1920), "left");
});

test("chooses the larger side when neither side has full menu space", () => {
  assert.equal(chooseMenuPlacement(100, 168, 0, 360), "left");
  assert.equal(chooseMenuPlacement(20, 168, 0, 250), "right");
});

test("restores the original window position after a left menu closes", () => {
  const opening = getPetWindowTransition(1, false, true, "left");
  const closing = getPetWindowTransition(1, true, false, "left");
  assert.equal(opening.width, 168 + PET_MENU_EXTRA_WIDTH);
  assert.equal(opening.windowDeltaX, -PET_MENU_EXTRA_WIDTH);
  assert.equal(closing.width, 168);
  assert.equal(closing.windowDeltaX, PET_MENU_EXTRA_WIDTH);
});

test("does not move the window for a right-side menu transition", () => {
  assert.equal(getPetWindowTransition(1, false, true, "right").windowDeltaX, 0);
  assert.equal(getPetWindowTransition(1, true, false, "right").windowDeltaX, 0);
});
