import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { PET_MENU_EXTRA_WIDTH } from "../src/pet/petWindowLayout.ts";

const cssSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
const menuSource = readFileSync(
  new URL("../src/menu/SurfaceContextMenu.ts", import.meta.url),
  "utf8",
);

test("the oval menu sits close to the assistant and only menu items slide", () => {
  assert.equal(PET_MENU_EXTRA_WIDTH, 116);
  assert.match(
    cssSource,
    /#surface-menu-root\[data-placement="right"\] \.surface-context-menu\s*\{\s*left: calc\(-24px \+/,
  );
  assert.match(
    cssSource,
    /#surface-menu-root\[data-placement="left"\] \.surface-context-menu\s*\{\s*left: 16px;/,
  );
  assert.doesNotMatch(
    cssSource,
    /\.pet-anchor\s*\{[\s\S]*?transition: left 160ms/,
  );
  assert.doesNotMatch(
    cssSource,
    /body\[data-desktop-mode="live"\] \.live-center\s*\{[\s\S]*?transition: left 160ms/,
  );
  assert.match(cssSource, /\.surface-menu-item\s*\{[\s\S]*?transform: translateX/);
  assert.match(cssSource, /\.surface-menu-item\s*\{[\s\S]*?background: rgba\(247, 249, 252/);
});

test("the Live status label overlaps the transparent orb margin", () => {
  assert.match(
    cssSource,
    /top: calc\(var\(--live-surface-visual-size, 200px\) - 20px\)/,
  );
});

test("the center of the orb is a state-reactive fluid wave", () => {
  const rendererSource = readFileSync(
    new URL("../src/live/LiveOrbRenderer.ts", import.meta.url),
    "utf8",
  );
  assert.match(rendererSource, /coreDeformation/);
  assert.match(rendererSource, /coreWaveBand/);
  assert.match(rendererSource, /coreFlow/);
});

test("Code renders immediately while native window geometry settles in the background", () => {
  const handler = mainSource.match(/const showCodeSurface[\s\S]*?\n\};/)?.[0] || "";

  assert.match(handler, /const transition = setDesktopMode\("code"\);/);
  assert.match(handler, /openCodeSurface\(\);/);
  assert.match(handler, /void transition;/);
  assert.doesNotMatch(handler, /\.then\(\(\) => openCodeSurface\(\)\)/);
});

test("full native surfaces resize before secondary window properties", () => {
  const windowModeSource = readFileSync(
    new URL("../src/desktop/windowMode.ts", import.meta.url),
    "utf8",
  );
  const handler = windowModeSource.match(
    /async function expandFullSurface[\s\S]*?\n\}/,
  )?.[0] || "";

  assert.ok(handler.indexOf("window.setSize(size)") >= 0);
  assert.ok(
    handler.indexOf("window.setSize(size)") <
      handler.indexOf("window.setAlwaysOnTop(false)"),
  );
  assert.match(handler, /await Promise\.all\(\[/);
});

test("menu navigation actions do not wait for the close animation", () => {
  const handler = menuSource.match(/async function runMenuItem[\s\S]*?\n  \}/)?.[0] || "";

  assert.match(handler, /options\.onOpenCode\(\);\s+await close\(true\);/);
  assert.match(handler, /options\.onOpenSettings\(\);\s+await close\(true\);/);
});
