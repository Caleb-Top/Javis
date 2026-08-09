import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import test from "node:test";

import {
  bindEmbeddedCodeMessageListener,
  buildCodeSurfaceUrl,
  isTrustedEmbeddedCodeSettingsMessage,
  unbindEmbeddedCodeMessageListener,
} from "../src/code/CodeSurface.ts";

const webIndexSource = readFileSync(
  new URL("../../web/index.html", import.meta.url),
  "utf8",
);
const webAppSource = readFileSync(
  new URL("../../web/js/app.js", import.meta.url),
  "utf8",
);
const embedBridgeSource = readFileSync(
  new URL("../../web/js/appEmbedBridge.js", import.meta.url),
  "utf8",
);
const nativeMainSource = readFileSync(
  new URL("../src/main.ts", import.meta.url),
  "utf8",
);

type EmbedBridge = {
  isEmbedded(locationLike: { search?: string }): boolean;
  requestModelSettings(
    locationLike: { search?: string },
    parentWindow: { postMessage(message: unknown, targetOrigin: string): void },
  ): boolean;
};

function loadEmbedBridge(): EmbedBridge {
  const context: Record<string, unknown> = { URL, URLSearchParams };
  vm.runInNewContext(embedBridgeSource, context, { filename: "appEmbedBridge.js" });
  return context.JavisAppEmbedBridge as EmbedBridge;
}

test("embedded Code targets the native parent origin when opening model settings", () => {
  const bridge = loadEmbedBridge();
  const sent: Array<{ message: unknown; targetOrigin: string }> = [];
  const locationLike = {
    search: "?app_embed=1&parent_origin=http%3A%2F%2Ftauri.localhost",
  };

  const delegated = bridge.requestModelSettings(locationLike, {
    postMessage(message, targetOrigin) {
      sent.push({ message, targetOrigin });
    },
  });

  assert.equal(bridge.isEmbedded(locationLike), true);
  assert.equal(delegated, true);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].targetOrigin, "http://tauri.localhost");
  assert.deepEqual(
    JSON.parse(JSON.stringify(sent[0].message)),
    { type: "javis.open-model-settings", section: "storage" },
  );
});

test("non-embedded web never delegates settings and invalid parent origins fail closed", () => {
  const bridge = loadEmbedBridge();
  let calls = 0;
  const parentWindow = {
    postMessage() { calls += 1; },
  };

  assert.equal(bridge.requestModelSettings({ search: "" }, parentWindow), false);
  assert.equal(
    bridge.requestModelSettings(
      { search: "?app_embed=1&parent_origin=javascript%3Aalert(1)" },
      parentWindow,
    ),
    false,
  );
  assert.equal(calls, 0);
});

test("native parent accepts the settings request only from its backend iframe", () => {
  const frameWindow = {};
  const valid = {
    origin: "http://127.0.0.1:8080",
    source: frameWindow,
    data: { type: "javis.open-model-settings", section: "storage" },
  };

  assert.equal(
    isTrustedEmbeddedCodeSettingsMessage(
      valid,
      frameWindow,
      "http://127.0.0.1:8080",
    ),
    true,
  );
  assert.equal(
    isTrustedEmbeddedCodeSettingsMessage(
      { ...valid, origin: "http://evil.invalid" },
      frameWindow,
      "http://127.0.0.1:8080",
    ),
    false,
  );
  assert.equal(
    isTrustedEmbeddedCodeSettingsMessage(valid, {}, "http://127.0.0.1:8080"),
    false,
  );
});

test("remounting Code keeps exactly one native message listener", () => {
  const listeners = new Set<(event: MessageEvent) => void>();
  let additions = 0;
  let removals = 0;
  const target = {
    addEventListener(type: string, listener: (event: MessageEvent) => void) {
      assert.equal(type, "message");
      additions += 1;
      listeners.add(listener);
    },
    removeEventListener(type: string, listener: (event: MessageEvent) => void) {
      assert.equal(type, "message");
      removals += 1;
      listeners.delete(listener);
    },
  } as unknown as Window;

  bindEmbeddedCodeMessageListener(target);
  bindEmbeddedCodeMessageListener(target);

  assert.equal(additions, 1);
  assert.equal(removals, 0);
  assert.equal(listeners.size, 1);

  unbindEmbeddedCodeMessageListener();
  assert.equal(removals, 1);
  assert.equal(listeners.size, 0);
});

test("embedded web delegates every legacy model mutation to the unified surface", () => {
  assert.notEqual(webIndexSource.indexOf("/static/js/appEmbedBridge.js"), -1);
  assert.notEqual(webIndexSource.indexOf("/static/js/app.js"), -1);
  assert.ok(
    webIndexSource.indexOf("/static/js/appEmbedBridge.js")
      < webIndexSource.indexOf("/static/js/app.js"),
  );
  assert.match(webAppSource, /requestUnifiedModelSettingsIfEmbedded/);
  assert.match(webAppSource, /legacyModelConfigRequest/);
  assert.doesNotMatch(
    webAppSource,
    /fetch\(\s*["']\/api\/config\/(?:model|provider|apikey)/,
  );
  assert.match(nativeMainSource, /javis:open-model-settings/);
  assert.match(nativeMainSource, /detail\?\.route === "code" \? "code" : "live"/);
  assert.match(nativeMainSource, /settingsSurface\?\.open\(routeName,\s*"storage"\)/);
});

test("Code URL carries the exact parent origin for postMessage", () => {
  const url = new URL(buildCodeSurfaceUrl(
    "session-1",
    false,
    "http://tauri.localhost",
  ));

  assert.equal(url.searchParams.get("app_embed"), "1");
  assert.equal(url.searchParams.get("parent_origin"), "http://tauri.localhost");
});
