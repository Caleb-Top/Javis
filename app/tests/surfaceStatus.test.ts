import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import * as runtimeStateTypes from "../src/state/runtimeStateTypes.ts";

const liveStageSource = readFileSync(
  new URL("../src/live/LiveStage.ts", import.meta.url),
  "utf8",
);
const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const petSource = readFileSync(
  new URL("../src/pet/PetSurface.ts", import.meta.url),
  "utf8",
);

test("runtime details become short user-facing status text", () => {
  const formatSurfaceStatus = (
    runtimeStateTypes as typeof runtimeStateTypes & {
      formatSurfaceStatus: (snapshot: {
        state: "idle" | "thinking" | "executing";
        detail: string;
      }) => string;
    }
  ).formatSurfaceStatus;

  assert.equal(formatSurfaceStatus({ state: "idle", detail: "Javis 已待命" }), "待命");
  assert.equal(
    formatSurfaceStatus({ state: "thinking", detail: "正在分析桌面布局" }),
    "思考中 · 正在分析桌面布局",
  );
  assert.equal(
    formatSurfaceStatus({
      state: "executing",
      detail: "tool_start",
    }),
    "执行中 · 调用工具",
  );
  assert.ok(
    formatSurfaceStatus({
      state: "thinking",
      detail: "这是一段非常非常长的内部处理过程说明，不应该完整铺在桌面上影响阅读",
    }).length <= 34,
  );
});

test("Live and Pet both render the shared process status", () => {
  assert.match(liveStageSource, /live-surface-status/);
  assert.match(mainSource, /liveSurfaceStatus\.setSnapshot\(snapshot\)/);
  assert.match(mainSource, /petSurface\.setSnapshot\(snapshot\)/);
  assert.match(petSource, /formatSurfaceStatus\(snapshot\)/);
});
