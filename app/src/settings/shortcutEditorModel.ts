import {
  isValidPetShortcut,
  type PetShortcutKind,
  type PetShortcutSlot,
} from "../pet/petPreferences.ts";

export type ShortcutEditorControl =
  | "none"
  | "javis-select"
  | "target-path"
  | "url-input"
  | "hotkey-recorder";

export type ShortcutEditorDescriptor = {
  control: ShortcutEditorControl;
  inputLabel: string;
  placeholder: string;
  help: string;
  browse: boolean;
};

export type ShortcutSlotStatus =
  | "empty"
  | "incomplete"
  | "invalid"
  | "missing-target"
  | "saved";

export type RecordedHotkeyInput = {
  key: string;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  metaKey: boolean;
};

const DESCRIPTORS: Record<PetShortcutKind, ShortcutEditorDescriptor> = {
  "": {
    control: "none",
    inputLabel: "目标",
    placeholder: "先选择快捷项类型",
    help: "选择类型后会显示对应的配置方式。",
    browse: false,
  },
  javis: {
    control: "javis-select",
    inputLabel: "Javis 功能",
    placeholder: "",
    help: "选择右键菜单需要打开的 Javis 内置界面。",
    browse: false,
  },
  target: {
    control: "target-path",
    inputLabel: "应用、文件或目录",
    placeholder: "C:\\Program Files\\Example\\Example.exe",
    help: "可填写 .exe、快捷方式、普通文件或目录，推荐使用系统浏览按钮。",
    browse: true,
  },
  url: {
    control: "url-input",
    inputLabel: "网页地址",
    placeholder: "https://example.com",
    help: "只支持 http:// 和 https:// 地址。",
    browse: false,
  },
  hotkey: {
    control: "hotkey-recorder",
    inputLabel: "组合键",
    placeholder: "Ctrl+Shift+P",
    help: "点击录制后，同时按下至少一个修饰键和一个普通按键。",
    browse: false,
  },
};

const PRIMARY_KEY_NAMES = new Map<string, string>([
  [" ", "Space"],
  ["Esc", "Escape"],
  ["Escape", "Escape"],
  ["Enter", "Enter"],
  ["Tab", "Tab"],
  ["Backspace", "Backspace"],
  ["Delete", "Delete"],
  ["Home", "Home"],
  ["End", "End"],
  ["PageUp", "PageUp"],
  ["PageDown", "PageDown"],
  ["ArrowUp", "ArrowUp"],
  ["ArrowDown", "ArrowDown"],
  ["ArrowLeft", "ArrowLeft"],
  ["ArrowRight", "ArrowRight"],
]);

const MODIFIER_KEYS = new Set(["Control", "Ctrl", "Alt", "Shift", "Meta", "OS", "Win"]);

export function getShortcutEditorDescriptor(
  kind: PetShortcutKind,
): ShortcutEditorDescriptor {
  return DESCRIPTORS[kind] || DESCRIPTORS[""];
}

export function getShortcutSlotStatus(
  slot: PetShortcutSlot,
  targetExists?: boolean,
): ShortcutSlotStatus {
  const normalized: PetShortcutSlot = {
    label: slot.label.trim(),
    kind: slot.kind,
    value: slot.value.trim(),
  };
  if (!normalized.label && !normalized.kind && !normalized.value) return "empty";
  if (!normalized.label || !normalized.kind || !normalized.value) return "incomplete";
  if (!isValidPetShortcut(normalized)) return "invalid";
  if (normalized.kind === "target" && targetExists === false) return "missing-target";
  return "saved";
}

export function normalizeRecordedHotkey(input: RecordedHotkeyInput): string | null {
  if (MODIFIER_KEYS.has(input.key)) return null;
  const modifiers = [
    input.ctrlKey ? "Ctrl" : "",
    input.altKey ? "Alt" : "",
    input.shiftKey ? "Shift" : "",
    input.metaKey ? "Win" : "",
  ].filter(Boolean);
  if (modifiers.length === 0) return null;

  let primary = PRIMARY_KEY_NAMES.get(input.key) || "";
  if (!primary && input.key.length === 1 && /^[a-z0-9]$/i.test(input.key)) {
    primary = input.key.toUpperCase();
  }
  if (!primary && /^F(?:[1-9]|1[0-9]|2[0-4])$/i.test(input.key)) {
    primary = input.key.toUpperCase();
  }
  if (!primary) return null;
  return [...modifiers, primary].join("+");
}
