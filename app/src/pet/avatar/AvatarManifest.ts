import {
  AVATAR_CAPABILITIES,
  AVATAR_KINDS,
  type AvatarAssetBudget,
  type AvatarCapability,
  type AvatarKind,
  type AvatarLicense,
  type AvatarManifest,
} from "./avatarTypes.ts";

export const DEFAULT_AVATAR_ASSET_BUDGET: AvatarAssetBudget = Object.freeze({
  maxBytes: 25 * 1024 * 1024,
  maxTriangles: 80_000,
  maxMaterials: 8,
  maxTextureSize: 2048,
});

const COMMON_FIELDS = [
  "schemaVersion",
  "id",
  "version",
  "kind",
  "name",
  "description",
  "accent",
  "license",
  "capabilities",
  "assetBudget",
  "fallbackId",
] as const;

const MODEL_FIELDS = ["model", "modelSha256"] as const;
const SPRITE_FIELDS = ["asset", "assetSha256"] as const;
const ID_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const VERSION_PATTERN = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/;
const SHA256_PATTERN = /^[0-9a-fA-F]{64}$/;
const ACCENT_PATTERN = /^#[0-9a-fA-F]{6}$/;

type JsonRecord = Record<string, unknown>;

function expectRecord(value: unknown, label: string): JsonRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
  return value as JsonRecord;
}

function expectString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new TypeError(`${label} must be a non-empty string`);
  }
  return value.trim();
}

function assertOnlyFields(record: JsonRecord, allowed: readonly string[], label: string): void {
  const allowedFields = new Set(allowed);
  const unknown = Object.keys(record).filter((key) => !allowedFields.has(key)).sort();
  if (unknown.length > 0) {
    throw new TypeError(`${label} contains unknown field: ${unknown[0]}`);
  }
}

function parseKind(value: unknown): AvatarKind {
  if (typeof value !== "string" || !(AVATAR_KINDS as readonly string[]).includes(value)) {
    throw new TypeError(`kind must be one of: ${AVATAR_KINDS.join(", ")}`);
  }
  return value as AvatarKind;
}

function parseIdentifier(value: unknown, label: string): string {
  const id = expectString(value, label);
  if (!ID_PATTERN.test(id)) {
    throw new TypeError(`${label} must use lowercase kebab-case`);
  }
  return id;
}

function parseLocalAssetPath(value: unknown, label: string): string {
  const path = expectString(value, label);
  let decoded: string;
  try {
    decoded = decodeURIComponent(path);
  } catch {
    throw new TypeError(`${label} must be a local asset path`);
  }

  const hasScheme = /^[a-z][a-z0-9+.-]*:/i.test(decoded);
  const isProtocolRelative = decoded.startsWith("//");
  const isWindowsPath = /^[a-z]:/i.test(decoded) || decoded.startsWith("\\");
  const hasUnsafeSyntax = decoded.includes("\\") || decoded.includes("?") || decoded.includes("#") || decoded.includes("\0");
  const parts = decoded.split("/");
  const hasTraversal = parts.some((part) => part === "." || part === "..");
  const outsidePublicPets = decoded.startsWith("/") && !decoded.startsWith("/pets/");

  if (
    hasScheme
    || isProtocolRelative
    || isWindowsPath
    || hasUnsafeSyntax
    || hasTraversal
    || outsidePublicPets
    || decoded === "/pets/"
  ) {
    throw new TypeError(`${label} must be a local asset path within /pets/`);
  }
  return path;
}

function parseRelativeMetadataPath(value: unknown, label: string): string {
  const path = parseLocalAssetPath(value, label);
  if (path.startsWith("/")) {
    throw new TypeError(`${label} must be relative to the manifest`);
  }
  return path;
}

function parseSha256(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA256_PATTERN.test(value)) {
    throw new TypeError(`${label} must be a 64-character SHA-256`);
  }
  return value.toLowerCase();
}

function parseLicense(value: unknown): AvatarLicense {
  const record = expectRecord(value, "license");
  assertOnlyFields(record, ["id", "author", "source", "attributionFile"], "license");
  return Object.freeze({
    id: expectString(record.id, "license.id"),
    author: expectString(record.author, "license.author"),
    source: expectString(record.source, "license.source"),
    attributionFile: parseRelativeMetadataPath(
      record.attributionFile ?? "ATTRIBUTION.md",
      "license.attributionFile",
    ),
  });
}

function parseCapabilities(value: unknown): readonly AvatarCapability[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new TypeError("capabilities must be a non-empty array");
  }
  const known = new Set<string>(AVATAR_CAPABILITIES);
  const capabilities = value.map((item) => {
    if (typeof item !== "string" || !known.has(item)) {
      throw new TypeError(`unknown avatar capability: ${String(item)}`);
    }
    return item as AvatarCapability;
  });
  if (new Set(capabilities).size !== capabilities.length) {
    throw new TypeError("capabilities must not contain duplicates");
  }
  return Object.freeze(capabilities);
}

function parseBudget(value: unknown, required: boolean): AvatarAssetBudget {
  if (value === undefined && !required) {
    return DEFAULT_AVATAR_ASSET_BUDGET;
  }
  const record = expectRecord(value, "assetBudget");
  assertOnlyFields(
    record,
    ["maxBytes", "maxTriangles", "maxMaterials", "maxTextureSize"],
    "assetBudget",
  );

  const parseLimit = (key: keyof AvatarAssetBudget): number => {
    const limit = record[key];
    if (!Number.isSafeInteger(limit) || (limit as number) <= 0) {
      throw new TypeError(`assetBudget.${key} must be a positive integer`);
    }
    if ((limit as number) > DEFAULT_AVATAR_ASSET_BUDGET[key]) {
      throw new RangeError(
        `assetBudget.${key} exceeds the L0-B limit of ${DEFAULT_AVATAR_ASSET_BUDGET[key]}`,
      );
    }
    return limit as number;
  };

  return Object.freeze({
    maxBytes: parseLimit("maxBytes"),
    maxTriangles: parseLimit("maxTriangles"),
    maxMaterials: parseLimit("maxMaterials"),
    maxTextureSize: parseLimit("maxTextureSize"),
  });
}

export function parseAvatarManifest(input: unknown): AvatarManifest {
  const record = expectRecord(input, "avatar manifest");
  const kind = parseKind(record.kind);

  // Validate asset-looking fields before reporting their kind-specific presence.
  if (record.model !== undefined) parseLocalAssetPath(record.model, "model");
  if (record.asset !== undefined) parseLocalAssetPath(record.asset, "asset");

  const allowed = kind === "vrm" || kind === "gltf"
    ? [...COMMON_FIELDS, ...MODEL_FIELDS]
    : kind === "sprite2d"
      ? [...COMMON_FIELDS, ...SPRITE_FIELDS]
      : COMMON_FIELDS;
  assertOnlyFields(record, allowed, "avatar manifest");

  if (record.schemaVersion !== 1) {
    throw new TypeError("schemaVersion must be 1");
  }
  const id = parseIdentifier(record.id, "id");
  const fallbackId = parseIdentifier(record.fallbackId, "fallbackId");
  if (fallbackId === id && kind !== "orb") {
    throw new TypeError("fallbackId must identify a different avatar");
  }

  const version = record.version === undefined ? "1.0.0" : expectString(record.version, "version");
  if (!VERSION_PATTERN.test(version)) {
    throw new TypeError("version must be a semantic version");
  }
  const accent = expectString(record.accent, "accent");
  if (!ACCENT_PATTERN.test(accent)) {
    throw new TypeError("accent must be a six-digit hexadecimal color");
  }

  const common = {
    schemaVersion: 1 as const,
    id,
    version,
    kind,
    name: expectString(record.name, "name"),
    description: record.description === undefined ? "" : expectString(record.description, "description"),
    accent: accent.toLowerCase(),
    license: parseLicense(record.license),
    capabilities: parseCapabilities(record.capabilities),
    assetBudget: parseBudget(record.assetBudget, kind === "vrm" || kind === "gltf" || kind === "sprite2d"),
    fallbackId,
  };

  if (kind === "vrm" || kind === "gltf") {
    const model = parseLocalAssetPath(record.model, "model");
    const lowerModel = model.toLowerCase();
    if (kind === "vrm" && !lowerModel.endsWith(".vrm")) {
      throw new TypeError("a vrm manifest model must end in .vrm");
    }
    if (kind === "gltf" && !lowerModel.endsWith(".gltf") && !lowerModel.endsWith(".glb")) {
      throw new TypeError("a gltf manifest model must end in .gltf or .glb");
    }
    return Object.freeze({
      ...common,
      kind,
      model,
      modelSha256: parseSha256(record.modelSha256, "modelSha256"),
    }) as AvatarManifest;
  }

  if (kind === "sprite2d") {
    return Object.freeze({
      ...common,
      kind,
      asset: parseLocalAssetPath(record.asset, "asset"),
      assetSha256: parseSha256(record.assetSha256, "assetSha256"),
    });
  }

  return Object.freeze({ ...common, kind });
}

export function hasAvatarCapability(
  manifest: AvatarManifest,
  capability: AvatarCapability,
): boolean {
  return manifest.capabilities.includes(capability);
}
