import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { createLifeStateBridge } from "../src/life/LifeStateBridge.ts";
import { PerformanceGovernor } from "../src/pet/avatar/PerformanceGovernor.ts";
import { getPetSkin } from "../src/pet/PetSkinRegistry.ts";
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

test("fallback activity stays concise and never reads as completed", () => {
  assert.equal(
    runtimeStateTypes.formatSurfaceStatus({
      state: "thinking",
      detail: "camera failed; choosing another path",
    }),
    "\u601d\u8003\u4e2d \u00b7 \u6b63\u5728\u5c1d\u8bd5\u66ff\u4ee3\u65b9\u6848",
  );
});

test("voice failures never combine an error label with the thinking detail", () => {
  assert.equal(
    runtimeStateTypes.formatSurfaceStatus({
      state: "error",
      detail: "正在理解",
    }),
    "需要检查",
  );
  assert.equal(
    runtimeStateTypes.formatSurfaceStatus({
      state: "error",
      detail: "麦克风设备不可用",
    }),
    "需要检查 · 麦克风设备不可用",
  );
});

test("reduced motion lowers the tier and renders a static avatar frame", () => {
  const governor = new PerformanceGovernor({ initialTier: "3d-high", reducedMotion: true });
  assert.equal(governor.tier(), "3d-low");
  assert.equal(governor.setReducedMotion(false).tier, "3d-low");
  assert.match(petSource, /matchMedia\?\.\("\(prefers-reduced-motion: reduce\)"\)\.matches/);
  assert.match(petSource, /mountedSurface\.setVisible\(false\);\s*mountedSurface\.renderOnce\(\)/);
});

test("renderer failure preserves status, interactions and manual Orb switching", () => {
  const mountAvatarSource = petSource.slice(
    petSource.indexOf("async function mountAvatarSkin"),
    petSource.indexOf("function applySkin"),
  );
  assert.match(mountAvatarSource, /catch \{[\s\S]*?fallbackFrom\(skin\)/);
  assert.match(petSource, /status\.textContent = formatSurfaceStatus\(snapshot\)/);
  assert.match(petSource, /status\.dataset\.visible = "true"/);
  assert.match(petSource, /spriteButton\.addEventListener\("click"/);
  assert.match(petSource, /spriteButton\.addEventListener\("contextmenu"/);
  assert.match(petSource, /function setSkin\(skinId: string\)[\s\S]*?applySkin\(getPetSkin\(skinId\)\)/);
  assert.match(petSource, /petSkinRegistry\.find\(\(candidate\) => candidate\.kind === "orb"\)/);
  assert.equal(getPetSkin("javis-orb").kind, "orb");
});

test("unknown life states fail closed to a neutral idle target", () => {
  const bridge = createLifeStateBridge({
    signal: () => undefined,
    now: () => Date.parse("2026-08-20T10:00:00.000Z"),
  });
  const accepted = bridge.handle({
    type: "life.expression",
    payload: {
      schema_version: 1,
      revision: 1,
      base_state: "celebrating",
      intensity: 0.5,
      gaze_target: "none",
      voice_activity: "silent",
      transition_ms: 180,
      interrupt: false,
      source_snapshot_revision: 1,
      generated_at: "2026-08-20T09:59:59.000Z",
      expires_at: "2026-08-20T10:00:01.000Z",
      explanation_code: "unknown_test_state",
    },
  });

  assert.equal(accepted, false);
  assert.equal(bridge.snapshot().state, "idle");
  assert.equal(bridge.snapshot().expression, null);
});
