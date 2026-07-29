import {
  getConfiguredShortcuts,
  type PetPreferences,
  type PetShortcut,
} from "./petPreferences.ts";

export type PetMenuItem =
  | {
      id: "settings" | "code";
      label: string;
      icon: "settings" | "code";
      shortcut?: never;
    }
  | {
      id: string;
      label: string;
      icon: "shortcut";
      shortcut: PetShortcut;
    };

export function buildPetMenuItems(preferences: PetPreferences): PetMenuItem[] {
  const fixed: PetMenuItem[] = [
    { id: "settings", label: "\u8bbe\u7f6e", icon: "settings" },
    { id: "code", label: "Code", icon: "code" },
  ];
  const custom: PetMenuItem[] = getConfiguredShortcuts(preferences).map((shortcut, index) => ({
    id: `shortcut-${index}`,
    label: shortcut.label,
    icon: "shortcut",
    shortcut,
  }));
  return [...fixed, ...custom].slice(0, 5);
}
