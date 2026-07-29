import assert from "node:assert/strict";
import test from "node:test";

import {
  getShortcutEditorDescriptor,
  getShortcutSlotStatus,
  normalizeRecordedHotkey,
} from "../src/settings/shortcutEditorModel.ts";

test("uses a fixed action selector for Javis shortcuts", () => {
  const descriptor = getShortcutEditorDescriptor("javis");

  assert.equal(descriptor.control, "javis-select");
  assert.equal(descriptor.inputLabel, "Javis 功能");
});

test("uses explicit controls and examples for target, URL, and hotkey shortcuts", () => {
  const target = getShortcutEditorDescriptor("target");
  const url = getShortcutEditorDescriptor("url");
  const hotkey = getShortcutEditorDescriptor("hotkey");

  assert.equal(target.control, "target-path");
  assert.equal(target.browse, true);
  assert.match(target.placeholder, /C:\\/);
  assert.equal(url.control, "url-input");
  assert.equal(url.placeholder, "https://example.com");
  assert.equal(hotkey.control, "hotkey-recorder");
  assert.match(hotkey.placeholder, /Ctrl\+Shift\+P/);
});

test("reports incomplete, invalid, missing-target, and saved states", () => {
  assert.equal(
    getShortcutSlotStatus({ label: "", kind: "", value: "" }),
    "empty",
  );
  assert.equal(
    getShortcutSlotStatus({ label: "官网", kind: "url", value: "" }),
    "incomplete",
  );
  assert.equal(
    getShortcutSlotStatus({ label: "危险", kind: "url", value: "javascript:alert(1)" }),
    "invalid",
  );
  assert.equal(
    getShortcutSlotStatus(
      { label: "编辑器", kind: "target", value: "C:\\Apps\\Editor.exe" },
      false,
    ),
    "missing-target",
  );
  assert.equal(
    getShortcutSlotStatus({ label: "命令面板", kind: "hotkey", value: "Ctrl+Shift+P" }),
    "saved",
  );
});

test("normalizes recorded hotkeys and ignores modifier-only input", () => {
  assert.equal(normalizeRecordedHotkey({
    key: "p",
    ctrlKey: true,
    altKey: false,
    shiftKey: true,
    metaKey: false,
  }), "Ctrl+Shift+P");
  assert.equal(normalizeRecordedHotkey({
    key: "Control",
    ctrlKey: true,
    altKey: false,
    shiftKey: false,
    metaKey: false,
  }), null);
  assert.equal(normalizeRecordedHotkey({
    key: "F4",
    ctrlKey: false,
    altKey: true,
    shiftKey: false,
    metaKey: false,
  }), "Alt+F4");
  assert.equal(normalizeRecordedHotkey({
    key: "k",
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    metaKey: false,
  }), null);
});
