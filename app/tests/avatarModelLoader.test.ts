import assert from "node:assert/strict";
import test from "node:test";

import { AnimationClip, Group, Object3D } from "three";

import {
  createAvatarModelLoader,
  loadAvatarModel,
  type AvatarAnimationMixer,
  type AvatarGltf,
  type AvatarModelLoaderDependencies,
} from "../src/pet/avatar/AvatarModelLoader.ts";
import { parseAvatarManifest } from "../src/pet/avatar/AvatarManifest.ts";
import type {
  AvatarCapability,
  AvatarManifest,
} from "../src/pet/avatar/avatarTypes.ts";
import { speakingTargetFixture } from "./avatarTestFakes.ts";

function modelManifest(
  kind: "vrm" | "gltf",
  capabilities: readonly AvatarCapability[] = ["idle"],
): AvatarManifest {
  return parseAvatarManifest({
    schemaVersion: 1,
    id: `${kind}-loader-fixture`,
    version: "1.0.0",
    kind,
    name: `${kind.toUpperCase()} Loader Fixture`,
    description: "Local loader test fixture",
    accent: "#61e9f0",
    model: kind === "vrm" ? "/pets/fixture/avatar.vrm" : "/pets/fixture/avatar.glb",
    modelSha256: "a".repeat(64),
    license: {
      id: "Javis-Test-Only",
      author: "Javis tests",
      source: "local fixture",
      attributionFile: "ATTRIBUTION.md",
    },
    capabilities,
    assetBudget: {
      maxBytes: 1024,
      maxTriangles: 1000,
      maxMaterials: 2,
      maxTextureSize: 512,
    },
    fallbackId: "javis-lightform",
  });
}

function loaderFixture(gltf: AvatarGltf | Error) {
  let pluginFactory: ((parser: unknown) => unknown) | null = null;
  let urlModifier: ((url: string) => string) | null = null;
  const counters = {
    loaderCount: 0,
    registerCount: 0,
    loadCount: 0,
    requestedUrl: "",
    plugin: null as unknown,
    parser: { fixture: "parser" },
  };
  const dependencies: AvatarModelLoaderDependencies = {
    createLoadingManager() {
      return {
        setURLModifier(modifier) {
          urlModifier = modifier;
        },
      };
    },
    createGltfLoader() {
      counters.loaderCount += 1;
      return {
        register(factory) {
          counters.registerCount += 1;
          pluginFactory = factory;
        },
        async loadAsync(url) {
          counters.loadCount += 1;
          counters.requestedUrl = url;
          urlModifier?.(url);
          if (pluginFactory) counters.plugin = pluginFactory(counters.parser);
          if (gltf instanceof Error) throw gltf;
          return gltf;
        },
      };
    },
    createVrmLoaderPlugin(parser) {
      return { name: "fixture-vrm-plugin", parser };
    },
  };
  return {
    dependencies,
    counters,
    resolveUrl(url: string): string {
      assert.ok(urlModifier, "URL modifier was not installed");
      return urlModifier(url);
    },
  };
}

test("rejects non-/pets/ model paths and non-model manifests before loading", async () => {
  const fixture = loaderFixture({ scene: new Group() });
  const valid = modelManifest("gltf");
  const load = createAvatarModelLoader(fixture.dependencies);

  for (const model of [
    "https://example.com/avatar.glb",
    "//example.com/avatar.glb",
    "/outside/avatar.glb",
    "/pets/%252e%252e/avatar.glb",
    "/pets/avatar.glb?cache=1",
  ]) {
    await assert.rejects(
      load({ ...valid, model } as AvatarManifest),
      /local asset path within \/pets\//,
    );
  }
  await assert.rejects(
    load({ ...valid, kind: "procedural3d", model: undefined } as unknown as AvatarManifest),
    /only accepts vrm or gltf/,
  );
  assert.equal(fixture.counters.loaderCount, 0);
});

test("registers the VRM plugin and blocks remote dependent resources", async () => {
  const scene = new Group();
  let vrmUpdateCount = 0;
  const vrm = {
    scene,
    update() {
      vrmUpdateCount += 1;
    },
  };
  const fixture = loaderFixture({ scene, userData: { vrm } });
  const loaded = await loadAvatarModel(modelManifest("vrm"), fixture.dependencies);

  assert.equal(fixture.counters.registerCount, 1);
  assert.equal(fixture.counters.requestedUrl, "/pets/fixture/avatar.vrm");
  assert.deepEqual(fixture.counters.plugin, {
    name: "fixture-vrm-plugin",
    parser: fixture.counters.parser,
  });
  assert.equal(fixture.resolveUrl("/pets/fixture/texture.png"), "/pets/fixture/texture.png");
  assert.throws(() => fixture.resolveUrl("https://example.com/texture.png"), /within \/pets\//);
  loaded.update?.(1 / 60, null);
  assert.equal(vrmUpdateCount, 1);
});

test("keeps a GLTF animation mixer alive for bounded per-frame updates", async () => {
  const scene = new Group();
  const clip = new AnimationClip("idle", 1, []);
  const mixerCalls = {
    played: 0,
    updates: [] as number[],
    stopped: 0,
    uncached: 0,
  };
  const mixer: AvatarAnimationMixer = {
    clipAction(received) {
      assert.equal(received, clip);
      return {
        play() {
          mixerCalls.played += 1;
        },
      };
    },
    update(deltaSeconds) {
      mixerCalls.updates.push(deltaSeconds);
    },
    stopAllAction() {
      mixerCalls.stopped += 1;
    },
    uncacheRoot(root) {
      assert.equal(root, scene);
      mixerCalls.uncached += 1;
    },
  };
  const fixture = loaderFixture({ scene, animations: [clip] });
  const loaded = await loadAvatarModel(modelManifest("gltf"), {
    ...fixture.dependencies,
    createAnimationMixer: () => mixer,
  });

  assert.equal(fixture.counters.registerCount, 0);
  assert.equal(loaded.object, scene);
  assert.equal(loaded.owned, true);
  assert.equal(mixerCalls.played, 1);
  loaded.update?.(0.25, speakingTargetFixture());
  loaded.update?.(Number.NaN, null);
  assert.deepEqual(mixerCalls.updates, [0.1, 0]);

  loaded.dispose?.();
  loaded.dispose?.();
  loaded.update?.(0.05, null);
  assert.equal(mixerCalls.stopped, 1);
  assert.equal(mixerCalls.uncached, 1);
  assert.deepEqual(mixerCalls.updates, [0.1, 0]);
});

test("updates only supported, capability-backed VRM expression and pose channels", async () => {
  const scene = new Group();
  const head = new Object3D();
  const chest = new Object3D();
  const supported = new Set(["blink", "aa", "coreLight"]);
  const values: Array<readonly [string, number]> = [];
  const deltas: number[] = [];
  const vrm = {
    scene,
    expressionManager: {
      getExpression(name: string) {
        return supported.has(name) ? { name } : null;
      },
      setValue(name: string, value: number) {
        assert.equal(supported.has(name), true);
        values.push([name, value]);
      },
    },
    humanoid: {
      getNormalizedBoneNode(name: string) {
        if (name === "head") return head;
        if (name === "chest") return chest;
        return null;
      },
    },
    update(deltaSeconds: number) {
      deltas.push(deltaSeconds);
    },
  };
  const fixture = loaderFixture({ scene, userData: { vrm } });
  const loaded = await loadAvatarModel(modelManifest("vrm", [
    "idle",
    "blink",
    "mouth",
    "head",
    "body",
    "coreLight",
  ]), fixture.dependencies);

  loaded.update?.(0.05, speakingTargetFixture({
    mouth: 4,
    posture: "active",
    gaze: "task",
    color: "red",
    interrupt: true,
  }));

  assert.deepEqual(deltas, [0.05]);
  assert.equal(values.some(([name, value]) => name === "aa" && value === 1), true);
  assert.equal(values.some(([name, value]) => name === "coreLight" && value === 0.95), true);
  assert.equal(values.every(([, value]) => value >= 0 && value <= 1), true);
  assert.notEqual(head.quaternion.w, 1);
  assert.notEqual(chest.quaternion.w, 1);

  loaded.update?.(0.05, null);
  assert.deepEqual(values.slice(-3), [
    ["blink", 0],
    ["aa", 0],
    ["coreLight", 0],
  ]);
  assert.equal(head.quaternion.equals(new Object3D().quaternion), true);
  assert.equal(chest.quaternion.equals(new Object3D().quaternion), true);
});

test("missing capabilities and model channels degrade to neutral without throwing", async () => {
  const scene = new Group();
  let expressionWrites = 0;
  let vrmUpdates = 0;
  const vrm = {
    scene,
    expressionManager: {
      getExpression() {
        throw new Error("channel absent");
      },
      setValue() {
        expressionWrites += 1;
      },
    },
    humanoid: {
      getNormalizedBoneNode() {
        throw new Error("bone absent");
      },
    },
    update() {
      vrmUpdates += 1;
    },
  };
  const fixture = loaderFixture({ scene, userData: { vrm } });
  const loaded = await loadAvatarModel(modelManifest("vrm", ["idle"]), fixture.dependencies);

  assert.doesNotThrow(() => loaded.update?.(1 / 60, speakingTargetFixture()));
  assert.equal(expressionWrites, 0);
  assert.equal(vrmUpdates, 1);
});

test("propagates model loading failures without retrying", async () => {
  const failure = new Error("fixture load failed");
  const fixture = loaderFixture(failure);

  await assert.rejects(
    loadAvatarModel(modelManifest("gltf"), fixture.dependencies),
    (error) => error === failure,
  );
  assert.equal(fixture.counters.loadCount, 1);
});
