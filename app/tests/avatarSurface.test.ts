import assert from "node:assert/strict";
import test from "node:test";

import { createAvatarScene } from "../src/pet/avatar/AvatarScene.ts";
import { createAvatarSurface } from "../src/pet/avatar/AvatarSurface.ts";
import {
  createAvatarHostFixture,
  createAvatarResourceFixture,
  proceduralManifestFixture,
  speakingTargetFixture,
} from "./avatarTestFakes.ts";

test("keeps camera, lights and the model anchor inside the scene boundary", () => {
  const avatarScene = createAvatarScene();

  assert.equal(avatarScene.camera.name, "javis-avatar-camera");
  assert.equal(avatarScene.modelAnchor.name, "javis-avatar-model-anchor");
  assert.equal(avatarScene.lights.length, 2);
  assert.equal(avatarScene.scene.children.includes(avatarScene.modelAnchor), true);
  assert.equal(avatarScene.lights.every((light) => avatarScene.scene.children.includes(light)), true);
  avatarScene.setSize(200, 100);
  assert.equal(avatarScene.camera.aspect, 2);
  avatarScene.dispose();
  avatarScene.dispose();
  assert.equal(avatarScene.scene.children.length, 0);
});

test("creates one transparent alpha canvas and disposes every owned resource", async () => {
  const host = createAvatarHostFixture();
  const { resources, counters } = createAvatarResourceFixture();
  const surface = await createAvatarSurface({
    host,
    manifest: proceduralManifestFixture(),
    resources,
  });

  assert.equal(host.querySelectorAll("canvas.avatar-canvas").length, 1);
  assert.deepEqual(counters.rendererOptions, {
    alpha: true,
    antialias: true,
    premultipliedAlpha: true,
  });
  assert.equal(counters.clearColor, 0x000000);
  assert.equal(counters.clearAlpha, 0);
  surface.setVisible(false);
  assert.equal(counters.cancelledFrameCount, 1);

  surface.dispose();
  surface.dispose();
  assert.equal(counters.rendererDisposeCount, 1);
  assert.equal(counters.geometryDisposeCount, counters.geometryCount);
  assert.equal(counters.materialDisposeCount, counters.materialCount);
  assert.equal(counters.textureDisposeCount, counters.textureCount);
  assert.equal(host.querySelectorAll("canvas.avatar-canvas").length, 0);
});

test("runs RAF only while visible in Pet or Live mode", async () => {
  const host = createAvatarHostFixture();
  const fixture = createAvatarResourceFixture();
  const surface = await createAvatarSurface({
    host,
    manifest: proceduralManifestFixture(),
    resources: fixture.resources,
    mode: "pet",
  });

  assert.equal(fixture.counters.requestedFrameCount, 1);
  assert.equal(fixture.runNextFrame(), true);
  assert.equal(fixture.counters.renderCount, 1);
  assert.equal(fixture.counters.requestedFrameCount, 2);
  surface.setVisible(false);
  assert.equal(fixture.counters.cancelledFrameCount, 1);
  assert.equal(fixture.runNextFrame(), false);
  surface.setVisible(true);
  assert.equal(fixture.counters.requestedFrameCount, 3);
  surface.dispose();

  const inactiveFixture = createAvatarResourceFixture();
  const inactive = await createAvatarSurface({
    host: createAvatarHostFixture(),
    manifest: proceduralManifestFixture(),
    resources: inactiveFixture.resources,
    mode: "code",
  });
  assert.equal(inactiveFixture.counters.requestedFrameCount, 0);
  inactive.renderOnce();
  assert.equal(inactiveFixture.counters.renderCount, 1);
  inactive.dispose();
});

test("resizes, renders once and reports the current semantic target", async () => {
  const fixture = createAvatarResourceFixture();
  const surface = await createAvatarSurface({
    host: createAvatarHostFixture(),
    manifest: proceduralManifestFixture(),
    resources: fixture.resources,
    tier: "3d-low",
  });
  const target = speakingTargetFixture();

  surface.setExpressionTarget(target);
  surface.setSize(132, 158);
  surface.renderOnce();

  assert.equal(fixture.counters.rendererOptions?.antialias, false);
  assert.deepEqual(fixture.counters.sizes.at(-1), [132, 158, false]);
  assert.equal(fixture.counters.modelUpdateCount, 1);
  assert.deepEqual(surface.diagnostics(), {
    manifestId: "javis-lightform-fixture",
    tier: "3d-low",
    mode: "pet",
    width: 132,
    height: 158,
    visible: true,
    rendering: true,
    contextLost: false,
    disposed: false,
    frameCount: 1,
    expressionTarget: target,
  });
  surface.dispose();
});

test("stops on context loss, prevents default and removes context listeners", async () => {
  const fixture = createAvatarResourceFixture();
  const lifecycle: string[] = [];
  const frameDurations: number[] = [];
  const surface = await createAvatarSurface({
    host: createAvatarHostFixture(),
    manifest: proceduralManifestFixture(),
    resources: fixture.resources,
    onFrame: (durationMs) => frameDurations.push(durationMs),
    onContextLost: () => lifecycle.push("lost"),
    onContextRestored: () => lifecycle.push("restored"),
  });
  let prevented = false;
  const lostEvent = {
    preventDefault() {
      prevented = true;
    },
  } as Event;

  assert.equal(fixture.canvas.listenerCount("webglcontextlost"), 1);
  assert.equal(fixture.canvas.listenerCount("webglcontextrestored"), 1);
  fixture.canvas.dispatch("webglcontextlost", lostEvent);
  assert.equal(prevented, true);
  assert.equal(surface.diagnostics().contextLost, true);
  assert.equal(surface.diagnostics().rendering, false);
  assert.deepEqual(lifecycle, ["lost"]);
  fixture.canvas.dispatch("webglcontextrestored", {} as Event);
  assert.equal(surface.diagnostics().contextLost, false);
  assert.equal(surface.diagnostics().rendering, true);
  assert.deepEqual(lifecycle, ["lost", "restored"]);
  fixture.runNextFrame();
  assert.deepEqual(frameDurations, [1000 / 60]);

  surface.dispose();
  assert.equal(fixture.canvas.listenerCount("webglcontextlost"), 0);
  assert.equal(fixture.canvas.listenerCount("webglcontextrestored"), 0);
});

test("does not dispose an externally shared model", async () => {
  const fixture = createAvatarResourceFixture({ modelOwned: false });
  const surface = await createAvatarSurface({
    host: createAvatarHostFixture(),
    manifest: proceduralManifestFixture(),
    resources: fixture.resources,
  });

  surface.dispose();
  surface.dispose();
  assert.equal(fixture.counters.geometryDisposeCount, 0);
  assert.equal(fixture.counters.materialDisposeCount, 0);
  assert.equal(fixture.counters.textureDisposeCount, 0);
  assert.equal(fixture.counters.rendererDisposeCount, 1);
});

test("deduplicates owned resources and preserves an explicitly shared texture", async () => {
  const fixture = createAvatarResourceFixture({ sharedTexture: true });
  const surface = await createAvatarSurface({
    host: createAvatarHostFixture(),
    manifest: proceduralManifestFixture(),
    resources: fixture.resources,
  });

  surface.dispose();
  surface.dispose();
  assert.equal(fixture.counters.geometryDisposeCount, 1);
  assert.equal(fixture.counters.materialDisposeCount, 1);
  assert.equal(fixture.counters.textureDisposeCount, 0);
});
