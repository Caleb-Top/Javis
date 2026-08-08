import assert from "node:assert/strict";
import test from "node:test";

import { toUserFacingError } from "../src/app/userFacingError.ts";

test("native audio failures are presented without internal WebView details", () => {
  const error = new Error("native audio stream connection failed");
  error.stack = "Error: native audio stream connection failed at d.onerror (http://tauri.localhost/assets/index.js:1:2)";

  const message = toUserFacingError(error);

  assert.equal(message, "语音服务尚未就绪，Javis 正在恢复连接。");
  assert.doesNotMatch(message, /tauri\.localhost|http:|assets\/|onerror/i);
});

test("unexpected failures stay concise and never expose stack traces", () => {
  const error = new Error("unexpected internal failure");
  error.stack = "unexpected internal failure\n at privateFunction (http://tauri.localhost/private.js:9:1)";

  const message = toUserFacingError(error);

  assert.equal(message, "界面暂时不可用，请点击恢复。");
  assert.doesNotMatch(message, /privateFunction|tauri\.localhost|unexpected internal failure/);
});
