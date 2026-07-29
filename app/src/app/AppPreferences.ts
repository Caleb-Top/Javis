const PREFIX = "javis.app.";

export function readPreference(key: string, fallback: boolean): boolean {
  try {
    const value = localStorage.getItem(PREFIX + key);
    return value === null ? fallback : value === "true";
  } catch {
    return fallback;
  }
}

export function writePreference(key: string, value: boolean): void {
  try {
    localStorage.setItem(PREFIX + key, String(value));
  } catch {
    // Preferences are optional; a locked-down WebView must remain usable.
  }
}
