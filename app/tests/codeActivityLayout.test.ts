import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../../web/css/style.css", import.meta.url), "utf8");

test("activity timeline is responsive and the stop targets are usable", () => {
  assert.match(css, /\.conversation-activity\s*\{/);
  assert.match(css, /\.activity-summary\s*\{/);
  assert.match(css, /\.activity-timeline\s*\{/);
  assert.match(css, /\.conversation-activity\.collapsed \.activity-timeline/);
  assert.match(css, /\.stop-btn[\s\S]*?width:\s*40px/);
  assert.match(css, /\.activity-stop[\s\S]*?min-width:\s*36px/);
  assert.match(css, /@media \(max-width: 700px\)[\s\S]*?\.conversation-activity/);
});
