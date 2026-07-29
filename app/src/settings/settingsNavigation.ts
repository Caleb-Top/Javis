export type SettingsSourceMode = "live" | "pet" | "code";

const SETTINGS_RETURN_MODES = new Set<SettingsSourceMode>(["live", "pet", "code"]);

export function getSettingsReturnMode(source: SettingsSourceMode): SettingsSourceMode {
  return SETTINGS_RETURN_MODES.has(source) ? source : "live";
}
