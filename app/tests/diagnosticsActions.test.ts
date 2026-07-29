import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const diagnosticsSource = readFileSync(
  new URL("../src/panels/DiagnosticsPanel.ts", import.meta.url),
  "utf8",
);
const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const rustSource = readFileSync(
  new URL("../src-tauri/src/main.rs", import.meta.url),
  "utf8",
);

test("diagnostics offers scoped, actionable checks instead of raw JSON", () => {
  for (const label of [
    "一键完整自检",
    "检查模型连接",
    "检查记忆与存储",
    "打开日志目录",
    "打开存储设置",
  ]) {
    assert.match(diagnosticsSource, new RegExp(label));
  }
  assert.match(diagnosticsSource, /\/api\/diagnostics\/self-test/);
  assert.match(diagnosticsSource, /renderChecks/);
  assert.doesNotMatch(diagnosticsSource, /JSON\.stringify\(value, null, 2\)/);
});

test("native shell can open logs and diagnostics can route into storage settings", () => {
  assert.match(mainSource, /invoke\("open_logs_directory"\)/);
  assert.match(mainSource, /onOpenStorage/);
  assert.match(rustSource, /fn open_logs_directory/);
  assert.match(rustSource, /open_logs_directory,/);
});
