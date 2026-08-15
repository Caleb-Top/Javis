(function installJavisAppEmbedBridge(root) {
  "use strict";

  const MESSAGE_TYPE = "javis.open-model-settings";
  const SETTINGS_SECTION = "storage";
  const ALLOWED_PARENT_PROTOCOLS = new Set(["http:", "https:", "tauri:"]);

  function parameters(locationLike) {
    return new URLSearchParams(String((locationLike && locationLike.search) || ""));
  }

  function isEmbedded(locationLike) {
    return parameters(locationLike).get("app_embed") === "1";
  }

  function parentOrigin(locationLike) {
    const raw = String(parameters(locationLike).get("parent_origin") || "").trim();
    if (!raw) return "";
    try {
      const parsed = new URL(raw);
      if (!ALLOWED_PARENT_PROTOCOLS.has(parsed.protocol) || parsed.origin === "null") return "";
      return parsed.origin;
    } catch (_) {
      return "";
    }
  }

  function requestModelSettings(locationLike, parentWindow) {
    if (!isEmbedded(locationLike)) return false;
    const targetOrigin = parentOrigin(locationLike);
    if (!targetOrigin || !parentWindow || typeof parentWindow.postMessage !== "function") return false;
    parentWindow.postMessage({
      type: MESSAGE_TYPE,
      section: SETTINGS_SECTION,
      route: "code",
    }, targetOrigin);
    return true;
  }

  root.JavisAppEmbedBridge = Object.freeze({
    MESSAGE_TYPE,
    SETTINGS_SECTION,
    isEmbedded,
    parentOrigin,
    requestModelSettings,
  });
})(globalThis);
