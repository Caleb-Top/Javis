import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  Material,
  Mesh,
  PointLight,
  Scene,
} from "three";

import { createProceduralAvatar } from "../src/pet/avatar/ProceduralAvatar.ts";
import { applyExpressionToProceduralAvatar } from "../src/pet/avatar/AvatarSurface.ts";

const source = readFileSync(
  new URL("../src/pet/avatar/ProceduralAvatar.ts", import.meta.url),
  "utf8",
);

test("lightform exposes semantic handles within the zero-asset budget", () => {
  const scene = new Scene();
  const avatar = createProceduralAvatar(scene);

  assert.deepEqual(Object.keys(avatar.handles).sort(), [
    "body",
    "coreLight",
    "eyes",
    "head",
    "mouth",
  ]);
  assert.ok(avatar.diagnostics.triangles > 0);
  assert.ok(avatar.diagnostics.triangles <= 20_000);
  assert.ok(avatar.diagnostics.materials > 0);
  assert.ok(avatar.diagnostics.materials <= 6);
  assert.ok(scene.getObjectByName("javis-lightform-head"));
  assert.ok(scene.getObjectByName("javis-lightform-shoulders"));
  assert.ok(scene.getObjectByName("javis-lightform-mouth"));

  avatar.dispose();
  assert.equal(avatar.diagnostics.disposed, true);
});

test("uses graphite, pearl, cyan-violet eyes and a central core light", () => {
  const scene = new Scene();
  const avatar = createProceduralAvatar(scene);

  const materialNames = new Set<string>();
  scene.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const meshMaterials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    meshMaterials.forEach((material) => materialNames.add(material.name));
  });

  assert.ok(materialNames.has("javis-lightform-graphite"));
  assert.ok(materialNames.has("javis-lightform-pearl"));
  assert.ok(materialNames.has("javis-lightform-eye-cyan"));
  assert.ok(materialNames.has("javis-lightform-eye-violet"));
  assert.ok(materialNames.has("javis-lightform-core"));

  const coreLight = scene.getObjectByName("javis-lightform-core-point-light");
  assert.ok(coreLight instanceof PointLight);
  assert.ok(scene.getObjectByName("javis-lightform-left-eye-light") instanceof PointLight);
  assert.ok(scene.getObjectByName("javis-lightform-right-eye-light") instanceof PointLight);

  avatar.dispose();
});

test("numeric handles clamp all visual drive values to zero through one", () => {
  const scene = new Scene();
  const avatar = createProceduralAvatar(scene);

  for (const handle of Object.values(avatar.handles)) {
    assert.equal(handle.min, 0);
    assert.equal(handle.max, 1);
    assert.equal(handle.set(-10), 0);
    assert.equal(handle.value, 0);
    assert.equal(handle.set(10), 1);
    assert.equal(handle.value, 1);
    assert.equal(handle.set(Number.NaN), 0);
    assert.equal(handle.value, 0);
  }

  avatar.dispose();
});

test("nine-state expression channels produce distinct bounded Lightform poses", () => {
  const scene = new Scene();
  const avatar = createProceduralAvatar(scene);

  applyExpressionToProceduralAvatar(avatar, {
    state: "listening",
    gaze: "user",
    mouth: 0,
    blinkRate: "normal",
    posture: "attentive",
    color: "cyan",
    gesture: "none",
    interrupt: false,
  });
  const listening = {
    eyes: avatar.handles.eyes.value,
    head: avatar.handles.head.value,
    body: avatar.handles.body.value,
    core: avatar.handles.coreLight.value,
  };

  applyExpressionToProceduralAvatar(avatar, {
    state: "thinking",
    gaze: "content",
    mouth: 0,
    blinkRate: "slow",
    posture: "focused",
    color: "violet",
    gesture: "none",
    interrupt: false,
  });
  const thinking = {
    eyes: avatar.handles.eyes.value,
    head: avatar.handles.head.value,
    body: avatar.handles.body.value,
    core: avatar.handles.coreLight.value,
  };

  assert.notDeepEqual(listening, thinking);
  assert.deepEqual(listening, { eyes: 1, head: 0.55, body: 0.58, core: 0.72 });
  assert.deepEqual(thinking, { eyes: 0.78, head: 0.43, body: 0.46, core: 0.82 });
  avatar.dispose();
});

test("contains no texture, random-particle, physics or LifeState dependency", () => {
  const scene = new Scene();
  const avatar = createProceduralAvatar(scene);
  const materials = new Set<Material>();

  scene.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const meshMaterials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    meshMaterials.forEach((material) => materials.add(material));
  });

  for (const material of materials) {
    const textureSlots = [
      "alphaMap",
      "aoMap",
      "bumpMap",
      "emissiveMap",
      "envMap",
      "lightMap",
      "map",
      "metalnessMap",
      "normalMap",
      "roughnessMap",
    ];
    for (const slot of textureSlots) {
      assert.equal((material as unknown as Record<string, unknown>)[slot] ?? null, null);
    }
  }
  assert.doesNotMatch(source, /Math\.random|TextureLoader|\bPoints\b|LifeState|physics/i);

  avatar.dispose();
});

test("dispose is idempotent and releases each owned geometry and material once", () => {
  const scene = new Scene();
  const avatar = createProceduralAvatar(scene);
  const geometries = new Set<ReturnType<typeof getGeometry>>();
  const materials = new Set<Material>();
  const geometryDisposals = new Map<object, number>();
  const materialDisposals = new Map<object, number>();

  scene.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    geometries.add(getGeometry(object));
    const meshMaterials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    meshMaterials.forEach((material) => materials.add(material));
  });

  for (const geometry of geometries) {
    const originalDispose = geometry.dispose.bind(geometry);
    geometry.dispose = () => {
      geometryDisposals.set(geometry, (geometryDisposals.get(geometry) ?? 0) + 1);
      originalDispose();
    };
  }
  for (const material of materials) {
    const originalDispose = material.dispose.bind(material);
    material.dispose = () => {
      materialDisposals.set(material, (materialDisposals.get(material) ?? 0) + 1);
      originalDispose();
    };
  }

  avatar.dispose();
  avatar.dispose();

  assert.equal(scene.getObjectByName("javis-lightform"), undefined);
  assert.equal(avatar.diagnostics.disposed, true);
  geometries.forEach((geometry) => assert.equal(geometryDisposals.get(geometry), 1));
  materials.forEach((material) => assert.equal(materialDisposals.get(material), 1));
});

function getGeometry(mesh: Mesh) {
  return mesh.geometry;
}
