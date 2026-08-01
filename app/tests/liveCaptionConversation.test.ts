import assert from "node:assert/strict";
import test from "node:test";

import { createLiveCaption } from "../src/live/LiveCaption.ts";

class FakeElement {
  textContent = "";
  title = "";
  dataset: Record<string, string> = {};
  classList = { add: () => undefined };
  addEventListener(): void {}
}

test("Live caption never accepts stale deltas after replacement", () => {
  const element = new FakeElement();
  const caption = createLiveCaption(element as unknown as HTMLElement, () => undefined);

  caption.begin("r1", "Understanding");
  caption.append("old", "r1");
  caption.begin("r2", "Understanding");
  caption.append(" late", "r1");
  caption.append("new", "r2");

  assert.equal(element.textContent, "new");
});
