export const PET_PREFERENCES_KEY = "javis.app.petPreferences.v1";

export type JavisShortcutValue = "live" | "code" | "settings" | "diagnostics";
export type PetShortcutKind = "" | "javis" | "target" | "url" | "hotkey";

export type PetShortcutSlot = {
  label: string;
  kind: PetShortcutKind;
  value: string;
};

export type PetShortcut = {
  label: string;
  kind: Exclude<PetShortcutKind, "">;
  value: string;
};

export type PetPreferences = {
  version: 1;
  scale: number;
  shortcuts: PetShortcutSlot[];
};

const EMPTY_SHORTCUT = (): PetShortcutSlot => ({ label: "", kind: "", value: "" });
const JAVIS_ACTIONS = new Set<JavisShortcutValue>(["live", "code", "settings", "diagnostics"]);
const HOTKEY_PATTERN = /^(?:(?:Ctrl|Alt|Shift|Meta|Win)\+)+(?:[A-Z0-9]|F(?:[1-9]|1[0-9]|2[0-4])|Enter|Escape|Space|Tab|Backspace|Delete|Home|End|PageUp|PageDown|Arrow(?:Up|Down|Left|Right))$/i;

export const DEFAULT_PET_PREFERENCES: PetPreferences = {
  version: 1,
  scale: 1,
  shortcuts: [EMPTY_SHORTCUT(), EMPTY_SHORTCUT(), EMPTY_SHORTCUT()],
};

function cloneDefaults(): PetPreferences {
  return {
    version: 1,
    scale: DEFAULT_PET_PREFERENCES.scale,
    shortcuts: DEFAULT_PET_PREFERENCES.shortcuts.map((shortcut) => ({ ...shortcut })),
  };
}

function normalizeScale(value: unknown): number {
  const numeric = typeof value === "number" && Number.isFinite(value) ? value : 1;
  const clamped = Math.min(1.3, Math.max(0.7, numeric));
  return Math.round(clamped * 20) / 20;
}

function normalizeSlot(value: unknown): PetShortcutSlot {
  if (!value || typeof value !== "object") return EMPTY_SHORTCUT();
  const record = value as Record<string, unknown>;
  const kind = typeof record.kind === "string" &&
    ["", "javis", "target", "url", "hotkey"].includes(record.kind)
    ? record.kind as PetShortcutKind
    : "";
  return {
    label: typeof record.label === "string" ? record.label.trim().slice(0, 24) : "",
    kind,
    value: typeof record.value === "string" ? record.value.trim().slice(0, 2048) : "",
  };
}

export function parsePetPreferences(raw: unknown): PetPreferences {
  let value = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw) as unknown;
    } catch {
      return cloneDefaults();
    }
  }
  if (!value || typeof value !== "object") return cloneDefaults();
  const record = value as Record<string, unknown>;
  const shortcuts = Array.isArray(record.shortcuts)
    ? record.shortcuts.slice(0, 3).map(normalizeSlot)
    : [];
  while (shortcuts.length < 3) shortcuts.push(EMPTY_SHORTCUT());
  return {
    version: 1,
    scale: normalizeScale(record.scale),
    shortcuts,
  };
}

export function isValidPetShortcut(shortcut: PetShortcutSlot): shortcut is PetShortcut {
  if (!shortcut.label || !shortcut.kind || !shortcut.value) return false;
  if (shortcut.kind === "javis") return JAVIS_ACTIONS.has(shortcut.value as JavisShortcutValue);
  if (shortcut.kind === "url") {
    try {
      const protocol = new URL(shortcut.value).protocol;
      return protocol === "http:" || protocol === "https:";
    } catch {
      return false;
    }
  }
  if (shortcut.kind === "hotkey") return HOTKEY_PATTERN.test(shortcut.value);
  return shortcut.kind === "target";
}

export function getConfiguredShortcuts(preferences: PetPreferences): PetShortcut[] {
  return preferences.shortcuts
    .map(normalizeSlot)
    .filter(isValidPetShortcut)
    .slice(0, 3)
    .map((shortcut) => ({ ...shortcut }));
}

export function readPetPreferences(): PetPreferences {
  try {
    return parsePetPreferences(localStorage.getItem(PET_PREFERENCES_KEY));
  } catch {
    return cloneDefaults();
  }
}

export function writePetPreferences(preferences: PetPreferences): PetPreferences {
  const normalized = parsePetPreferences(preferences);
  try {
    localStorage.setItem(PET_PREFERENCES_KEY, JSON.stringify(normalized));
  } catch {
    // Locked-down WebViews still keep current-session settings usable.
  }
  return normalized;
}
