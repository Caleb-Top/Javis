import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const rustMainSource = readFileSync(
  new URL("../src-tauri/src/main.rs", import.meta.url),
  "utf8",
);
const settingsSource = readFileSync(
  new URL("../src/settings/SettingsSurface.ts", import.meta.url),
  "utf8",
);

test("Settings uses the native Tauri dialog for file and directory targets", () => {
  assert.match(
    mainSource,
    /from "@tauri-apps\/plugin-dialog"/,
  );
  assert.match(
    mainSource,
    /onPickTarget: async \(kind\)[\s\S]*directory: kind === "directory"/,
  );
  assert.match(settingsSource, /shortcut-browse-file/);
  assert.match(settingsSource, /shortcut-browse-directory/);
  assert.match(settingsSource, /onPickTarget: \(kind: "file" \| "directory"\)/);
});

test("the native dialog plugin is initialized in the Tauri runtime", () => {
  assert.match(rustMainSource, /tauri_plugin_dialog::init\(\)/);
});
