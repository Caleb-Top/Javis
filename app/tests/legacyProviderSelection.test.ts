import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const webIndexSource = readFileSync(
  new URL("../../web/index.html", import.meta.url),
  "utf8",
);
const webAppSource = readFileSync(
  new URL("../../web/js/app.js", import.meta.url),
  "utf8",
);

type ChangeListener = (event: { currentTarget: FakeElement }) => unknown;

type FakeElement = {
  value: string;
  textContent: string;
  style: Record<string, string>;
  addEventListener(type: string, listener: ChangeListener): void;
};

function providerInlineHandlerName(): string {
  const tag = webIndexSource.match(/<select\b(?=[^>]*\bid=["']cfg-provider["'])[^>]*>/i)?.[0] || "";
  return tag.match(/\bonchange=["']\s*([A-Za-z_$][\w$]*)\s*\(\s*\)\s*["']/i)?.[1] || "";
}

function createLegacyProviderHarness() {
  const listeners: ChangeListener[] = [];
  const requests: string[] = [];
  let releaseProviderSave: (() => void) | null = null;

  const provider: FakeElement = {
    value: "deepseek",
    textContent: "",
    style: {},
    addEventListener(type, listener) {
      if (type === "change") listeners.push(listener);
    },
  };
  const elements = new Map<string, FakeElement>([
    ["cfg-provider", provider],
    ["apikey-group", { ...provider, value: "", style: {} }],
    ["settings-msg", { ...provider, value: "", style: {} }],
  ]);
  const documentLike = {
    body: { dataset: {}, style: {}, classList: { add() {}, remove() {}, toggle() {} } },
    addEventListener() {},
    getElementById(id: string) { return elements.get(id) || null; },
    createElement() { return { style: {}, classList: { add() {}, remove() {} } }; },
  };
  const windowLike = {
    location: { search: "" },
    parent: {},
    innerWidth: 1280,
    innerHeight: 720,
    addEventListener() {},
  };
  const context: Record<string, unknown> = {
    console,
    document: documentLike,
    window: windowLike,
    location: windowLike.location,
    localStorage: { getItem() { return null; }, setItem() {} },
    requestAnimationFrame() {},
    setTimeout,
    clearTimeout,
    URL,
    URLSearchParams,
    fetch(path: string) {
      requests.push(path);
      if (path === "/api/config/provider") {
        return new Promise((resolve) => {
          releaseProviderSave = () => resolve({
            json: async () => ({ applied: true }),
          });
        });
      }
      if (path === "/api/status") {
        return Promise.resolve({
          json: async () => ({ provider: "deepseek", model: "deepseek-chat", models: [] }),
        });
      }
      throw new Error(`unexpected request: ${path}`);
    },
  };
  vm.createContext(context);
  vm.runInContext(webAppSource, context, { filename: "web/js/app.js" });
  (context.initProviderListener as () => void)();

  return {
    requests,
    dispatchChange(): Promise<void> {
      const tasks: Array<Promise<unknown>> = [];
      const inlineName = providerInlineHandlerName();
      if (inlineName) tasks.push(Promise.resolve((context[inlineName] as () => unknown)()));
      tasks.push(...listeners.map((listener) => Promise.resolve(listener({ currentTarget: provider }))));
      return Promise.all(tasks).then(() => undefined);
    },
    releaseProviderSave(): void {
      assert.ok(releaseProviderSave, "provider save request should be pending");
      releaseProviderSave();
    },
  };
}

test("legacy provider selection saves once before performing one status refresh", async () => {
  const harness = createLegacyProviderHarness();
  const completion = harness.dispatchChange();

  await Promise.resolve();
  assert.deepEqual(harness.requests, ["/api/config/provider"]);

  harness.releaseProviderSave();
  await completion;
  await Promise.resolve();
  assert.deepEqual(harness.requests, ["/api/config/provider", "/api/status"]);
});
