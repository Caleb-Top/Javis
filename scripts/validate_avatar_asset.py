#!/usr/bin/env python3
"""Validate governed, offline avatar manifests without third-party packages."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import struct
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote


HARD_BUDGET = {
    "maxBytes": 25 * 1024 * 1024,
    "maxTriangles": 80_000,
    "maxMaterials": 8,
    "maxTextureSize": 2048,
}
KINDS = {"vrm", "gltf", "procedural3d", "sprite2d", "orb"}
CAPABILITIES = {
    "idle", "blink", "gaze", "mouth", "head", "body", "coreLight",
    "expression", "gesture",
}
COMMON_FIELDS = {
    "schemaVersion", "id", "version", "kind", "name", "description",
    "accent", "license", "capabilities", "assetBudget", "fallbackId",
}
MODEL_FIELDS = {"model", "modelSha256"}
SPRITE_FIELDS = {"asset", "assetSha256"}
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _new_report() -> dict[str, Any]:
    return {
        "ok": False,
        "errors": [],
        "warnings": [],
        "asset_sha256": None,
        "asset_bytes": 0,
        "budget": {
            "bytes": 0,
            "max_bytes": None,
            "triangles": 0,
            "max_triangles": None,
            "materials": 0,
            "max_materials": None,
            "max_texture_size": 0,
            "texture_size_limit": None,
            "within_budget": False,
        },
    }


def _add_error(report: dict[str, Any], message: str) -> None:
    if message not in report["errors"]:
        report["errors"].append(message)


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _local_relative_path(value: Any) -> str | None:
    if not _non_empty_string(value):
        return None
    raw = value.strip()
    try:
        decoded = unquote(raw)
    except Exception:
        return None
    if (
        "\\" in decoded
        or "?" in decoded
        or "#" in decoded
        or "\x00" in decoded
        or decoded.startswith(("/", "//"))
        or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", decoded)
    ):
        return None
    parts = PurePosixPath(decoded).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return raw


def _resolve_local(root: Path, value: Any) -> Path | None:
    relative = _local_relative_path(value)
    if relative is None:
        return None
    candidate = (root / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _parse_glb(
    data: bytes,
) -> tuple[dict[str, Any] | None, bytes | None, str | None]:
    if len(data) < 20:
        return None, None, "model is not a valid GLB/VRM container"
    magic, version, declared_length = struct.unpack_from("<4sII", data, 0)
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if (
        magic != b"glTF"
        or version != 2
        or declared_length != len(data)
        or chunk_type != 0x4E4F534A
        or 20 + chunk_length > len(data)
    ):
        return None, None, "model is not a valid GLB/VRM container"
    try:
        document = json.loads(data[20:20 + chunk_length].rstrip(b" \t\r\n\x00"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None, "model GLB JSON chunk is invalid"
    if not isinstance(document, dict):
        return None, None, "model GLB JSON chunk must be an object"
    binary = None
    offset = 20 + chunk_length
    if offset + 8 <= len(data):
        binary_length, binary_type = struct.unpack_from("<II", data, offset)
        if binary_type == 0x004E4942 and offset + 8 + binary_length <= len(data):
            binary = data[offset + 8:offset + 8 + binary_length]
    return document, binary, None


def _parse_gltf(
    path: Path, data: bytes
) -> tuple[dict[str, Any] | None, bytes | None, str | None]:
    if path.suffix.lower() in {".glb", ".vrm"}:
        return _parse_glb(data)
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None, "model glTF JSON is invalid"
    if not isinstance(document, dict):
        return None, None, "model glTF JSON must be an object"
    return document, None, None


def _accessor_count(document: dict[str, Any], accessor_index: Any) -> int:
    accessors = document.get("accessors")
    if not isinstance(accessors, list) or not isinstance(accessor_index, int):
        return 0
    if accessor_index < 0 or accessor_index >= len(accessors):
        return 0
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        return 0
    count = accessor.get("count")
    return count if isinstance(count, int) and count >= 0 else 0


def _gltf_facts(document: dict[str, Any]) -> dict[str, int]:
    triangles = 0
    meshes = document.get("meshes")
    if isinstance(meshes, list):
        for mesh in meshes:
            if not isinstance(mesh, dict) or not isinstance(mesh.get("primitives"), list):
                continue
            for primitive in mesh["primitives"]:
                if not isinstance(primitive, dict):
                    continue
                count = _accessor_count(document, primitive.get("indices"))
                if count == 0 and isinstance(primitive.get("attributes"), dict):
                    count = _accessor_count(document, primitive["attributes"].get("POSITION"))
                mode = primitive.get("mode", 4)
                if mode == 4:
                    triangles += count // 3
                elif mode in {5, 6}:
                    triangles += max(0, count - 2)
    materials = document.get("materials")
    textures = document.get("textures")
    return {
        "triangles": triangles,
        "materials": len(materials) if isinstance(materials, list) else 0,
        "textures": len(textures) if isinstance(textures, list) else 0,
    }


def _data_uri_bytes(uri: str) -> bytes | None:
    if not uri.startswith("data:") or "," not in uri:
        return None
    header, payload = uri.split(",", 1)
    try:
        return base64.b64decode(payload, validate=True) if ";base64" in header else unquote(payload).encode()
    except (ValueError, UnicodeEncodeError):
        return None


def _image_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])
    if len(data) >= 4 and data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 <= len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            if marker in {0xD8, 0xD9}:
                offset += 2
                continue
            segment_length = int.from_bytes(data[offset + 2:offset + 4], "big")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height = int.from_bytes(data[offset + 5:offset + 7], "big")
                width = int.from_bytes(data[offset + 7:offset + 9], "big")
                return width, height
            if segment_length < 2:
                break
            offset += 2 + segment_length
    return None


def _validate_references(
    document: dict[str, Any], root: Path, binary: bytes | None,
    report: dict[str, Any],
) -> int:
    total = 0
    seen: set[Path] = set()
    buffer_data: list[bytes | None] = []
    buffers = document.get("buffers")
    if isinstance(buffers, list):
        for index, entry in enumerate(buffers):
            data = None
            if isinstance(entry, dict) and "uri" in entry:
                uri = entry["uri"]
                if isinstance(uri, str) and uri.startswith("data:"):
                    data = _data_uri_bytes(uri)
                else:
                    path = _resolve_local(root, uri)
                    if path is None:
                        _add_error(report, "buffers URI must be a local relative asset path")
                    elif not path.is_file():
                        _add_error(report, f"referenced local asset does not exist: {uri}")
                    else:
                        data = path.read_bytes()
                        if path not in seen:
                            seen.add(path)
                            total += len(data)
            elif index == 0:
                data = binary
            buffer_data.append(data)

    images = document.get("images")
    views = document.get("bufferViews")
    if isinstance(images, list):
        for entry in images:
            if not isinstance(entry, dict):
                continue
            image_data = None
            if "uri" in entry:
                uri = entry["uri"]
                if isinstance(uri, str) and uri.startswith("data:"):
                    image_data = _data_uri_bytes(uri)
                else:
                    path = _resolve_local(root, uri)
                    if path is None:
                        _add_error(report, "images URI must be a local relative asset path")
                    elif not path.is_file():
                        _add_error(report, f"referenced local asset does not exist: {uri}")
                    else:
                        image_data = path.read_bytes()
                        if path not in seen:
                            seen.add(path)
                            total += len(image_data)
            elif isinstance(entry.get("bufferView"), int) and isinstance(views, list):
                view_index = entry["bufferView"]
                if 0 <= view_index < len(views) and isinstance(views[view_index], dict):
                    view = views[view_index]
                    buffer_index = view.get("buffer", 0)
                    offset = view.get("byteOffset", 0)
                    length = view.get("byteLength")
                    if (
                        isinstance(buffer_index, int) and 0 <= buffer_index < len(buffer_data)
                        and isinstance(offset, int) and isinstance(length, int)
                        and buffer_data[buffer_index] is not None
                    ):
                        image_data = buffer_data[buffer_index][offset:offset + length]
            if image_data is not None:
                dimensions = _image_size(image_data)
                if dimensions is None:
                    _add_error(report, "texture dimensions could not be determined")
                else:
                    report["budget"]["max_texture_size"] = max(
                        report["budget"]["max_texture_size"], *dimensions
                    )
    return total


def _validate_schema(manifest: Any, root: Path, report: dict[str, Any]) -> str | None:
    if not isinstance(manifest, dict):
        _add_error(report, "manifest must be a JSON object")
        return None
    kind = manifest.get("kind")
    if kind not in KINDS:
        _add_error(report, "kind must be one of: gltf, orb, procedural3d, sprite2d, vrm")
        kind = None
    allowed = set(COMMON_FIELDS)
    if kind in {"vrm", "gltf", "procedural3d"}:
        allowed.update(MODEL_FIELDS)
    if kind == "sprite2d":
        allowed.update(SPRITE_FIELDS)
    for field in sorted(set(manifest) - allowed):
        _add_error(report, f"manifest contains unknown field: {field}")

    if manifest.get("schemaVersion") != 1:
        _add_error(report, "schemaVersion must be 1")
    avatar_id = manifest.get("id")
    if not _non_empty_string(avatar_id) or not ID_RE.fullmatch(avatar_id):
        _add_error(report, "id must use lowercase kebab-case")
    version = manifest.get("version")
    if not _non_empty_string(version) or not VERSION_RE.fullmatch(version):
        _add_error(report, "version must be a semantic version")
    for field in ("name", "description"):
        if not _non_empty_string(manifest.get(field)):
            _add_error(report, f"{field} must be a non-empty string")
    accent = manifest.get("accent")
    if not isinstance(accent, str) or not ACCENT_RE.fullmatch(accent):
        _add_error(report, "accent must be a six-digit hexadecimal color")
    fallback = manifest.get("fallbackId")
    if not _non_empty_string(fallback) or not ID_RE.fullmatch(fallback):
        _add_error(report, "fallbackId must use lowercase kebab-case")
    elif fallback == avatar_id and kind != "orb":
        _add_error(report, "fallbackId must identify a different avatar")

    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        _add_error(report, "capabilities must be a non-empty array")
    elif any(not isinstance(item, str) or item not in CAPABILITIES for item in capabilities):
        _add_error(report, "capabilities contains an unknown capability")
    elif len(set(capabilities)) != len(capabilities):
        _add_error(report, "capabilities must not contain duplicates")

    license_info = manifest.get("license")
    if not isinstance(license_info, dict):
        _add_error(report, "license must be an object")
    else:
        for field in sorted(set(license_info) - {"id", "author", "source", "attributionFile"}):
            _add_error(report, f"license contains unknown field: {field}")
        for field in ("id", "author", "source", "attributionFile"):
            if not _non_empty_string(license_info.get(field)):
                _add_error(report, f"license.{field} must be a non-empty string")
        attribution = _resolve_local(root, license_info.get("attributionFile"))
        if attribution is None:
            _add_error(report, "license.attributionFile must be a local relative path")
        elif not attribution.is_file():
            _add_error(report, "license.attributionFile does not exist")
        elif attribution.stat().st_size == 0:
            _add_error(report, "license.attributionFile must not be empty")

    budget = manifest.get("assetBudget")
    if not isinstance(budget, dict):
        _add_error(report, "assetBudget must be an object")
    else:
        for field in sorted(set(budget) - set(HARD_BUDGET)):
            _add_error(report, f"assetBudget contains unknown field: {field}")
        for field, hard_limit in HARD_BUDGET.items():
            value = budget.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                _add_error(report, f"assetBudget.{field} must be a positive integer")
            elif value > hard_limit:
                _add_error(report, f"assetBudget.{field} exceeds the L0-B limit")

    return kind


def _apply_budget(manifest: dict[str, Any], report: dict[str, Any]) -> None:
    budget = manifest.get("assetBudget")
    if not isinstance(budget, dict):
        return
    facts = report["budget"]
    facts["bytes"] = report["asset_bytes"]
    facts["max_bytes"] = budget.get("maxBytes")
    facts["max_triangles"] = budget.get("maxTriangles")
    facts["max_materials"] = budget.get("maxMaterials")
    facts["texture_size_limit"] = budget.get("maxTextureSize")
    comparisons = (
        ("bytes", "maxBytes", "asset exceeds max_bytes"),
        ("triangles", "maxTriangles", "asset exceeds max_triangles"),
        ("materials", "maxMaterials", "asset exceeds max_materials"),
        ("max_texture_size", "maxTextureSize", "texture exceeds max_texture_size"),
    )
    for fact_key, limit_key, message in comparisons:
        limit = budget.get(limit_key)
        if isinstance(limit, int) and not isinstance(limit, bool) and facts[fact_key] > limit:
            _add_error(report, message)
    facts["within_budget"] = not any(
        error.startswith(("asset exceeds", "texture exceeds"))
        for error in report["errors"]
    )


def validate_avatar_manifest(manifest_path: str | Path) -> dict[str, Any]:
    report = _new_report()
    path = Path(manifest_path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _add_error(report, "manifest file does not exist")
        return report
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _add_error(report, "manifest is not valid UTF-8 JSON")
        return report

    root = path.resolve().parent
    kind = _validate_schema(manifest, root, report)
    if not isinstance(manifest, dict):
        return report

    if kind in {"vrm", "gltf"}:
        model_value = manifest.get("model")
        model_path = _resolve_local(root, model_value)
        if model_path is None:
            _add_error(report, "model must be a local relative asset path")
        elif not model_path.is_file():
            _add_error(report, "model local asset does not exist")
        else:
            suffix = model_path.suffix.lower()
            if kind == "vrm" and suffix != ".vrm":
                _add_error(report, "vrm model must end in .vrm")
            if kind == "gltf" and suffix not in {".gltf", ".glb"}:
                _add_error(report, "gltf model must end in .gltf or .glb")
            data = model_path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            report["asset_sha256"] = digest
            report["asset_bytes"] = len(data)
            pinned_hash = manifest.get("modelSha256")
            if not isinstance(pinned_hash, str) or not SHA256_RE.fullmatch(pinned_hash):
                _add_error(report, "modelSha256 must pin a 64-character SHA-256")
            elif pinned_hash.lower() != digest:
                _add_error(report, "modelSha256 does not match the local asset")
            document, binary, parse_error = _parse_gltf(model_path, data)
            if parse_error:
                _add_error(report, parse_error)
            elif document is not None:
                asset = document.get("asset")
                if not isinstance(asset, dict) or not str(asset.get("version", "")).startswith("2"):
                    _add_error(report, "model must declare glTF 2.x")
                facts = _gltf_facts(document)
                report["budget"]["triangles"] = facts["triangles"]
                report["budget"]["materials"] = facts["materials"]
                report["asset_bytes"] += _validate_references(
                    document, root, binary, report
                )
    elif kind == "sprite2d":
        asset_path = _resolve_local(root, manifest.get("asset"))
        if asset_path is None:
            _add_error(report, "asset must be a local relative asset path")
        elif not asset_path.is_file():
            _add_error(report, "sprite local asset does not exist")
        else:
            data = asset_path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            report["asset_sha256"] = digest
            report["asset_bytes"] = len(data)
            pinned_hash = manifest.get("assetSha256")
            if not isinstance(pinned_hash, str) or not SHA256_RE.fullmatch(pinned_hash):
                _add_error(report, "assetSha256 must pin a 64-character SHA-256")
            elif pinned_hash.lower() != digest:
                _add_error(report, "assetSha256 does not match the local asset")
    elif kind in {"procedural3d", "orb"}:
        if manifest.get("model") not in {None, ""} or manifest.get("modelSha256") not in {None, ""}:
            _add_error(report, f"{kind} must not declare a model asset")

    _apply_budget(manifest, report)
    report["errors"].sort()
    report["warnings"].sort()
    report["ok"] = len(report["errors"]) == 0
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    report = validate_avatar_manifest(args.manifest)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
