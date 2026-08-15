import assert from "node:assert/strict";
import test from "node:test";

import { RuntimeStateCoordinator } from "../src/state/RuntimeStateCoordinator.ts";

test("a recovered voice stream can leave its own transient error state", () => {
  const previousDocument = globalThis.document;
  globalThis.document = {
    querySelector: () => null,
  } as unknown as Document;
  try {
    const coordinator = new RuntimeStateCoordinator();
    coordinator.signal({
      source: "voice",
      state: "error",
      timestamp: 1,
      detail: "麦克风暂时不可用",
    });

    const recovered = coordinator.signal({
      source: "voice",
      state: "listening",
      timestamp: 2,
      detail: "我在听",
    });

    assert.equal(recovered.state, "listening");
    assert.equal(recovered.detail, "我在听");
  } finally {
    globalThis.document = previousDocument;
  }
});
