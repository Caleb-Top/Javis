import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settingsSource = readFileSync(
  new URL("../src/settings/SettingsSurface.ts", import.meta.url),
  "utf8",
);
const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const backendClientSource = readFileSync(
  new URL("../src/bridge/backendClient.ts", import.meta.url),
  "utf8",
);
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
  assert.match(settingsSource, /data-model-route="live"/);
  assert.match(settingsSource, /data-model-route="code"/);
  assert.match(settingsSource, /Code 与 Live 共用此配置/);
  assert.match(settingsSource, /安装或导入模型/);
  assert.match(settingsSource, /Hugging Face GGUF/);
  assert.match(settingsSource, /install-local-model/);
  assert.match(settingsSource, /模型安装位置（不能选附加包目录）/);
  assert.match(settingsSource, /R1 附加包来源目录/);
  assert.match(settingsSource, /外部 Ollama 模型存储（高级）/);
  assert.match(settingsSource, /not_installed/);
  assert.match(settingsSource, /导入已有 GGUF 文件/);
  assert.match(settingsSource, /从 Hugging Face 自动安装/);
  assert.match(settingsSource, /model-installer-progress/);
  assert.match(settingsSource, /onGetModelInstallProgress/);
  assert.match(settingsSource, /remote-model-refresh/);
  assert.match(settingsSource, /onRefreshRemoteModels/);
  assert.match(settingsSource, /自定义模型 ID/);
  assert.match(settingsSource, /已列出 \$\{models\.length\} 个兼容模型/);
  assert.match(settingsSource, /<select class="remote-model-name"/);
  assert.doesNotMatch(settingsSource, /id="remote-model-options"/);
  assert.doesNotMatch(settingsSource, /11434/);
});

test("model settings are backed by safe Python configuration endpoints", () => {
  assert.match(mainSource, /\/api\/config\/models/);
  assert.match(mainSource, /\/api\/config\/models\/local/);
  assert.match(backendSource, /@app\.get\("\/api\/config\/models"\)/);
  assert.match(backendSource, /@app\.post\("\/api\/config\/models"\)/);
  assert.match(backendSource, /@app\.post\("\/api\/config\/models\/local"\)/);
  assert.match(backendSource, /@app\.post\("\/api\/config\/models\/remote"\)/);
  assert.match(backendSource, /@app\.post\("\/api\/config\/models\/install\/plan"\)/);
  assert.match(backendSource, /@app\.post\("\/api\/config\/models\/install\/detect"\)/);
  assert.match(backendSource, /@app\.get\("\/api\/config\/models\/install\/progress"\)/);
  assert.match(backendSource, /@app\.post\("\/api\/config\/models\/install"\)/);
});

test("diagnostics label local and remote model channels separately", () => {
  assert.match(diagnosticsSource, /local_model_connection/);
  assert.match(diagnosticsSource, /remote_model_connection/);
});

test("startup opens model setup when a configured local model is absent from Ollama", () => {
  assert.match(mainSource, /findMissingConfiguredLocalRoutes/);
  assert.match(mainSource, /\/api\/config\/models\/local/);
  assert.match(mainSource, /catalog\.models/);
  assert.match(mainSource, /selected_local_model_not_installed/);
  assert.match(mainSource, /missingConfiguredLocalRoutes/);
  assert.match(mainSource, /ollama_startup === "ready"/);
  assert.match(mainSource, /ollama_startup === "starting"/);
});

test("actionable model failures open the same unified model settings surface", () => {
  assert.match(mainSource, /payload\?\.recovery_action/);
  assert.match(mainSource, /javis:open-model-settings/);
  assert.match(mainSource, /payload\?\.route/);
  assert.match(mainSource, /settingsSurface\?\.open\(routeName, "storage"\)/);
  assert.match(mainSource, /liveCaption\.setText\([\s\S]*event\.payload\?\.error/);
  assert.match(mainSource, /sidecar\.restart\(\)/);
  assert.match(mainSource, /restart_local_runtime/);
  assert.match(mainSource, /snapshot\.ollama_startup === "ready"/);
  assert.match(mainSource, /for \(let attempt = 0; attempt < 60; attempt \+= 1\)/);
  assert.match(mainSource, /restart failed[\s\S]*openModelSettingsForRoute/);
  assert.match(mainSource, /acceptedServerFailure = event\.type === "request\.failed"[\s\S]*eventAccepted[\s\S]*previous\.activeRequestId === event\.request_id/);
  assert.match(mainSource, /if \(acceptedServerFailure\)[\s\S]*restartLocalRuntime/);
  assert.match(backendClientSource, /payload\.error \|\| payload\.detail/);
});
