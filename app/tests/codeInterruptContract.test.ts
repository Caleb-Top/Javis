import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
const indexSource = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");

test("Code uses the shared session and keeps input enabled while running", () => {
  assert.match(appSource, /session_id/);
  assert.match(appSource, /conversation\.attach/);
  assert.match(appSource, /conversation\.message/);
  assert.doesNotMatch(appSource, /if \(!t \|\| isProcessing\) return/);
  assert.doesNotMatch(appSource, /recent_cards/);
});

test("Code exposes an explicit stop control and loads the activity controller", () => {
  assert.match(indexSource, /conversationActivity\.js/);
  assert.match(indexSource, /class="stop-btn"/);
  assert.match(appSource, /conversation\.cancel/);
});

test("Code consumes an approval token after either confirmation decision", () => {
  const approveBody = appSource.match(/function approveConfirm\(\)\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";
  const rejectBody = appSource.match(/function rejectConfirm\(\)\s*\{([\s\S]*?)\n\}/)?.[1] ?? "";

  assert.match(approveBody, /pendingApprovalId\s*=\s*null/);
  assert.match(rejectBody, /pendingApprovalId\s*=\s*null/);
});
