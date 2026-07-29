import assert from "node:assert/strict";
import test from "node:test";
import {
  isLocalSurfaceCommand,
  parseLocalSurfaceCommand,
} from "../src/live/localSurfaceCommand.ts";

test("explicit native surface commands are handled locally", () => {
  assert.equal(parseLocalSurfaceCommand("打开 Code 页面"), "code");
  assert.equal(parseLocalSurfaceCommand("进入设置"), "settings");
  assert.equal(parseLocalSurfaceCommand("运行诊断"), "diagnostics");
  assert.equal(parseLocalSurfaceCommand("回到交流球"), "live");
});

test("real coding tasks still go to the agent", () => {
  assert.equal(parseLocalSurfaceCommand("帮我写一段 Python 代码"), null);
  assert.equal(parseLocalSurfaceCommand("检查项目文件并修复问题"), null);
  assert.equal(parseLocalSurfaceCommand("在终端运行全部测试"), null);
});

test("only allowlisted backend app actions can control native surfaces", () => {
  assert.equal(isLocalSurfaceCommand("code"), true);
  assert.equal(isLocalSurfaceCommand("diagnostics"), true);
  assert.equal(isLocalSurfaceCommand("delete_system"), false);
  assert.equal(isLocalSurfaceCommand({ action: "code" }), false);
});
