import assert from "node:assert/strict";
import test from "node:test";

import { Box3, Vector3 } from "three";

import { createAvatarScene } from "../src/pet/avatar/AvatarScene.ts";
import { createProceduralAvatar } from "../src/pet/avatar/ProceduralAvatar.ts";

test("default camera frames the complete Lightform inside a pet viewport", () => {
  const scene = createAvatarScene();
  const avatar = createProceduralAvatar(scene.modelAnchor);
  scene.setSize(200, 218);
  scene.scene.updateMatrixWorld(true);
  scene.camera.updateMatrixWorld(true);

  const bounds = new Box3().setFromObject(avatar.object);
  const projected = [
    new Vector3(bounds.min.x, bounds.min.y, bounds.min.z),
    new Vector3(bounds.min.x, bounds.max.y, bounds.min.z),
    new Vector3(bounds.max.x, bounds.min.y, bounds.min.z),
    new Vector3(bounds.max.x, bounds.max.y, bounds.min.z),
    new Vector3(bounds.min.x, bounds.min.y, bounds.max.z),
    new Vector3(bounds.min.x, bounds.max.y, bounds.max.z),
    new Vector3(bounds.max.x, bounds.min.y, bounds.max.z),
    new Vector3(bounds.max.x, bounds.max.y, bounds.max.z),
  ].map((point) => point.project(scene.camera));

  assert.ok(projected.every(({ x }) => x >= -1 && x <= 1));
  assert.ok(projected.every(({ y }) => y >= -1 && y <= 1));
  assert.ok(projected.every(({ z }) => z >= -1 && z <= 1));

  avatar.dispose();
  scene.dispose();
});
