import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settingsSource = readFileSync(
  new URL("../src/settings/SettingsSurface.ts", import.meta.url),
  "utf8",
);
const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const backendSource = readFileSync(
  new URL("../../main.py", import.meta.url),
  "utf8",
);
const diagnosticsSource = readFileSync(
  new URL("../../utils/system_diagnostics.py", import.meta.url),
  "utf8",
);

test("Live settings expose independent local and remote model profiles", () => {
  assert.match(settingsSource, /本地模型/);
  assert.match(settingsSource, /远端 API/);
  assert.match(settingsSource, /model-source-segment/);
  assert.match(settingsSource, /remote-api-key/);
  assert.match(settingsSource, /仅在修改时填写/);
  assert.match(settingsSource, /onLoadModelSettings/);
  assert.match(settingsSource, /onSaveModelSettings/);
  assert.match(settingsSource, /onTestModelConnections/);
});

test("model settings are backed by safe Python configuration endpoints", () => {
  assert.match(mainSource, /\/api\/config\/models/);
  assert.match(mainSource, /\/api\/config\/models\/local/);
  assert.match(backendSource, /@app\.get\("\/api\/config\/models"\)/);
  assert.match(backendSource, /@app\.post\("\/api\/config\/models"\)/);
  assert.match(backendSource, /@app\.post\("\/api\/config\/models\/local"\)/);
});

test("diagnostics label local and remote model channels separately", () => {
  assert.match(diagnosticsSource, /local_model_connection/);
  assert.match(diagnosticsSource, /remote_model_connection/);
});
