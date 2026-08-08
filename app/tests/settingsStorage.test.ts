import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settingsSource = readFileSync(
  new URL("../src/settings/SettingsSurface.ts", import.meta.url),
  "utf8",
);
const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");

test("Settings exposes native directory selection for models and user data", () => {
  assert.match(settingsSource, /data-settings-section="storage"/);
  for (const label of ["外部 Ollama 模型存储（高级）", "项目工作区", "导入与输出", "备份目录"]) {
    assert.match(settingsSource, new RegExp(label));
  }
  assert.match(settingsSource, /onLoadPathSettings/);
  assert.match(settingsSource, /onSavePathSetting/);
  assert.match(settingsSource, /selectPathSetting/);
});

test("path settings are loaded and persisted through the Python backend", () => {
  assert.match(mainSource, /\/api\/config\/paths/);
  assert.match(mainSource, /onLoadPathSettings/);
  assert.match(mainSource, /onSavePathSetting/);
});

test("Settings exposes continuous voice noise profiles", () => {
  assert.match(settingsSource, /class="voice-noise-profile"/);
  assert.match(settingsSource, /value="standard"/);
  assert.match(settingsSource, /value="strong"/);
  assert.match(settingsSource, /writeStringPreference\("voice\.noiseProfile"/);
  assert.match(settingsSource, /javis:voice-profile-changed/);
});
