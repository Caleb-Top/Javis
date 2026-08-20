import assert from "node:assert/strict";
import test from "node:test";

import {
  hasAvatarCapability,
  parseAvatarManifest,
} from "../src/pet/avatar/AvatarManifest.ts";
import {
  AvatarAssetRegistry,
  avatarAssetRegistry,
  resolveFallback,
} from "../src/pet/avatar/AvatarAssetRegistry.ts";

const license = {
  id: "Javis-Original",
  author: "Javis Project",
  source: "local",
};

test("accepts the built-in procedural avatar and rejects remote assets", () => {
  const manifest = parseAvatarManifest({
    schemaVersion: 1,
    id: "javis-lightform",
    kind: "procedural3d",
    name: "Javis Lightform",
    accent: "#61e9f0",
    license,
    capabilities: ["idle", "blink", "gaze", "mouth"],
    fallbackId: "javis-anime",
  });

  assert.equal(manifest.id, "javis-lightform");
  assert.equal(manifest.version, "1.0.0");
  assert.equal(manifest.assetBudget.maxBytes, 25 * 1024 * 1024);
  assert.equal(hasAvatarCapability(manifest, "mouth"), true);
  assert.throws(
    () => parseAvatarManifest({ ...manifest, model: "https://example.com/a.vrm" }),
    /local asset/,
  );
});

test("enforces an exact discriminated schema and governed 3d assets", () => {
  const modelManifest = {
    schemaVersion: 1,
    id: "fixture-avatar",
    version: "1.2.3",
    kind: "vrm",
    name: "Fixture Avatar",
    description: "Local test fixture",
    accent: "#61e9f0",
    model: "avatar.vrm",
    modelSha256: "a".repeat(64),
    license: { ...license, attributionFile: "ATTRIBUTION.md" },
    capabilities: ["idle", "blink", "gaze", "mouth"],
    assetBudget: {
      maxBytes: 1024,
      maxTriangles: 1000,
      maxMaterials: 2,
      maxTextureSize: 512,
    },
    fallbackId: "javis-anime",
  };

  assert.equal(parseAvatarManifest(modelManifest).kind, "vrm");
  assert.throws(
    () => parseAvatarManifest({ ...modelManifest, model: "../avatar.vrm" }),
    /local asset/,
  );
  assert.throws(
    () => parseAvatarManifest({ ...modelManifest, modelSha256: "unpinned" }),
    /SHA-256/,
  );
  assert.throws(
    () => parseAvatarManifest({ ...modelManifest, unexpected: true }),
    /unknown field/,
  );
  assert.throws(
    () => parseAvatarManifest({ ...modelManifest, assetBudget: { ...modelManifest.assetBudget, maxMaterials: 9 } }),
    /maxMaterials/,
  );
});

test("registry resolves explicit local fallbacks and rejects broken chains", () => {
  assert.equal(avatarAssetRegistry.get("javis-lightform")?.kind, "procedural3d");
  assert.equal(resolveFallback("javis-lightform")?.id, "javis-anime");
  assert.equal(resolveFallback("missing-avatar")?.id, "javis-orb");

  const broken = new AvatarAssetRegistry([
    {
      schemaVersion: 1,
      id: "only-avatar",
      kind: "procedural3d",
      name: "Only Avatar",
      accent: "#61e9f0",
      license,
      capabilities: ["idle"],
      fallbackId: "not-registered",
    },
  ]);
  assert.throws(() => broken.resolveFallback("only-avatar"), /not registered/);

  assert.throws(
    () => new AvatarAssetRegistry([
      {
        schemaVersion: 1,
        id: "duplicate",
        kind: "orb",
        name: "First",
        accent: "#61e9f0",
        license,
        capabilities: ["idle"],
        fallbackId: "duplicate",
      },
      {
        schemaVersion: 1,
        id: "duplicate",
        kind: "orb",
        name: "Second",
        accent: "#61e9f0",
        license,
        capabilities: ["idle"],
        fallbackId: "duplicate",
      },
    ]),
    /duplicate avatar id/,
  );
});
