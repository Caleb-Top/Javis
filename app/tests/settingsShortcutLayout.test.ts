import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const settingsSource = readFileSync(
  new URL("../src/settings/SettingsSurface.ts", import.meta.url),
  "utf8",
);
const cssSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("Settings has a visible return action with an explicit destination", () => {
  assert.match(settingsSource, /settings-back-button/);
  assert.match(settingsSource, /settings-back-label[^>]*>返回</);
  assert.match(settingsSource, /返回 Code/);
  assert.match(settingsSource, /aria-current/);
});

test("legacy shortcut grid rules cannot move controls inside the new cards", () => {
  assert.doesNotMatch(cssSource, /(?:^|\n)\.shortcut-kind\s*\{[^}]*grid-column:/);
  assert.doesNotMatch(cssSource, /(?:^|\n)\.shortcut-clear\s*\{[^}]*grid-column:/);
  assert.match(cssSource, /\.settings-shortcut-card \.shortcut-kind/);
  assert.match(cssSource, /\.settings-shortcut-footer \.shortcut-clear/);
});

test("the text clear action stays aligned in the narrow Settings layout", () => {
  assert.doesNotMatch(cssSource, /\.settings-shortcut-footer \.settings-icon-button/);
  assert.match(
    cssSource,
    /@media \(max-width: 640px\)\s*\{[\s\S]*?\.settings-shortcut-footer \.shortcut-help\s*\{[^}]*grid-column:\s*1\s*\/\s*-1;/,
  );
  assert.match(
    cssSource,
    /@media \(max-width: 640px\)\s*\{[\s\S]*?\.settings-shortcut-footer \.shortcut-status\s*\{[^}]*grid-row:\s*2;/,
  );
  assert.match(
    cssSource,
    /@media \(max-width: 640px\)\s*\{[\s\S]*?\.settings-shortcut-footer \.shortcut-clear\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*2;/,
  );
});

test("an empty fixed shortcut slot does not offer a meaningless clear action", () => {
  assert.match(settingsSource, /shortcut-clear[\s\S]*?disabled\s*=\s*status\s*===\s*"empty"/);
});
