const STORAGE_KEY = "javis.runtime.client-instance.v1";
const CLIENT_ID_PATTERN = /^desktop-main-[0-9a-f]{32}$/;

export type RuntimeIdentityStorage = Readonly<{
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}>;

function defaultStorage(): RuntimeIdentityStorage | null {
  try {
    return typeof globalThis.localStorage === "undefined" ? null : globalThis.localStorage;
  } catch {
    return null;
  }
}

function randomSuffix(): string {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function getOrCreateRuntimeClientInstanceId(
  storage: RuntimeIdentityStorage | null = defaultStorage(),
  createSuffix: () => string = randomSuffix,
): string {
  try {
    const existing = storage?.getItem(STORAGE_KEY) ?? "";
    if (CLIENT_ID_PATTERN.test(existing)) return existing;
  } catch {
    // A denied WebView storage read must not prevent local startup.
  }

  const suffix = String(createSuffix() || "").toLowerCase();
  if (!/^[0-9a-f]{32}$/.test(suffix)) {
    throw new Error("runtime identity source returned invalid bytes");
  }
  const created = `desktop-main-${suffix}`;
  try {
    storage?.setItem(STORAGE_KEY, created);
  } catch {
    // Capability tokens remain ephemeral; persistence is continuity metadata only.
  }
  return created;
}
