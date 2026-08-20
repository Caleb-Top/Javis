import { parseAvatarManifest } from "./AvatarManifest.ts";
import type { AvatarManifest } from "./avatarTypes.ts";

const BUILT_IN_MANIFEST_INPUTS: readonly unknown[] = [
  {
    schemaVersion: 1,
    id: "javis-lightform",
    version: "1.0.0",
    kind: "procedural3d",
    name: "Javis Lightform",
    description: "A zero-asset procedural head-and-shoulders avatar.",
    accent: "#61e9f0",
    license: {
      id: "Javis-Original",
      author: "Javis Project",
      source: "local procedural geometry",
      attributionFile: "ATTRIBUTION.md",
    },
    capabilities: ["idle", "blink", "gaze", "mouth", "head", "body", "coreLight"],
    assetBudget: {
      maxBytes: 25 * 1024 * 1024,
      maxTriangles: 80_000,
      maxMaterials: 8,
      maxTextureSize: 2048,
    },
    fallbackId: "javis-anime",
  },
  {
    schemaVersion: 1,
    id: "javis-anime",
    version: "1.0.0",
    kind: "sprite2d",
    name: "Javis Neon Pilot",
    description: "The bundled local 2D fallback avatar.",
    accent: "#61e9f0",
    asset: "/pets/javis-anime/idle.png",
    assetSha256: "3a07ee7ec069843272831a43616ad9b5aacb271e14ff7329cd9af379dccd5690",
    license: {
      id: "Javis-Original",
      author: "Javis Project",
      source: "bundled Javis pet asset",
      attributionFile: "ATTRIBUTION.md",
    },
    capabilities: ["idle"],
    assetBudget: {
      maxBytes: 25 * 1024 * 1024,
      maxTriangles: 80_000,
      maxMaterials: 8,
      maxTextureSize: 2048,
    },
    fallbackId: "javis-orb",
  },
  {
    schemaVersion: 1,
    id: "javis-orb",
    version: "1.0.0",
    kind: "orb",
    name: "Javis Orb",
    description: "The terminal zero-asset CSS fallback.",
    accent: "#aa78f1",
    license: {
      id: "Javis-Original",
      author: "Javis Project",
      source: "local CSS rendering",
      attributionFile: "ATTRIBUTION.md",
    },
    capabilities: ["idle"],
    fallbackId: "javis-orb",
  },
];

export class AvatarAssetRegistry {
  readonly #manifests = new Map<string, AvatarManifest>();

  constructor(manifests: Iterable<unknown> = []) {
    for (const manifest of manifests) this.register(manifest);
  }

  register(input: unknown): AvatarManifest {
    const manifest = parseAvatarManifest(input);
    if (this.#manifests.has(manifest.id)) {
      throw new Error(`duplicate avatar id: ${manifest.id}`);
    }
    this.#manifests.set(manifest.id, manifest);
    return manifest;
  }

  get(id: string): AvatarManifest | undefined {
    return this.#manifests.get(id);
  }

  values(): readonly AvatarManifest[] {
    return Object.freeze([...this.#manifests.values()]);
  }

  resolveFallback(id: string): AvatarManifest | undefined {
    const manifest = this.get(id);
    if (!manifest) return this.get("javis-orb");

    const visited = new Set<string>([manifest.id]);
    let cursor = manifest;
    let directFallback: AvatarManifest | undefined;
    while (true) {
      const fallback = this.get(cursor.fallbackId);
      if (!fallback) {
        throw new Error(
          `fallback avatar ${cursor.fallbackId} referenced by ${cursor.id} is not registered`,
        );
      }
      directFallback ??= fallback;
      if (fallback.id === cursor.id && fallback.kind === "orb") return directFallback;
      if (visited.has(fallback.id)) {
        throw new Error(`avatar fallback cycle detected at ${fallback.id}`);
      }
      visited.add(fallback.id);
      cursor = fallback;
    }
  }
}

export const avatarAssetRegistry = new AvatarAssetRegistry(BUILT_IN_MANIFEST_INPUTS);

export function resolveFallback(id: string): AvatarManifest | undefined {
  return avatarAssetRegistry.resolveFallback(id);
}
