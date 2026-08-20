import hashlib
import json
import re
import struct
from pathlib import Path

from scripts.validate_avatar_asset import validate_avatar_manifest


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "app"
DEFAULT_AVATAR_ROOT = APP_ROOT / "public/pets/javis-3d"
DEFAULT_AVATAR_MANIFEST = DEFAULT_AVATAR_ROOT / "avatar-manifest.json"
REMOTE_SCHEME = re.compile(r"^(?:https?|data|blob|file):", re.IGNORECASE)


def string_values(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from string_values(nested)
    elif isinstance(value, str):
        yield value


def valid_manifest(*, kind="vrm", model="avatar.vrm"):
    return {
        "schemaVersion": 1,
        "id": "fixture-avatar",
        "version": "1.0.0",
        "kind": kind,
        "name": "Fixture Avatar",
        "description": "Local validator fixture",
        "accent": "#61e9f0",
        "model": model,
        "modelSha256": None,
        "license": {
            "id": "Javis-Test-Only",
            "author": "Javis tests",
            "source": "local fixture",
            "attributionFile": "ATTRIBUTION.md",
        },
        "capabilities": ["idle", "blink", "gaze", "mouth"],
        "assetBudget": {
            "maxBytes": 25 * 1024 * 1024,
            "maxTriangles": 80_000,
            "maxMaterials": 8,
            "maxTextureSize": 2048,
        },
        "fallbackId": "javis-anime",
    }


def make_glb(document):
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    chunk = struct.pack("<II", len(payload), 0x4E4F534A) + payload
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunk)) + chunk


def write_manifest(root, manifest):
    (root / "ATTRIBUTION.md").write_text("test-only asset\n", encoding="utf-8")
    path = root / "avatar-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_validator_accepts_pinned_local_glb_and_reports_budget_facts(tmp_path):
    document = {
        "asset": {"version": "2.0"},
        "accessors": [
            {"count": 300, "type": "SCALAR", "componentType": 5123},
        ],
        "materials": [{"name": "surface"}],
        "meshes": [{"primitives": [{"indices": 0}]}],
    }
    asset = make_glb(document)
    (tmp_path / "avatar.vrm").write_bytes(asset)
    manifest = valid_manifest()
    manifest["modelSha256"] = hashlib.sha256(asset).hexdigest()

    report = validate_avatar_manifest(write_manifest(tmp_path, manifest))

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["asset_sha256"] == manifest["modelSha256"]
    assert report["asset_bytes"] == len(asset)
    assert report["budget"]["triangles"] == 100
    assert report["budget"]["materials"] == 1
    assert report["budget"]["within_budget"] is True


def test_validator_rejects_oversized_or_unlicensed_manifest(tmp_path):
    manifest = valid_manifest()
    (tmp_path / "avatar.vrm").write_bytes(b"x" * 1024)
    manifest["assetBudget"]["maxBytes"] = 32
    manifest["license"]["id"] = ""
    path = write_manifest(tmp_path, manifest)

    report = validate_avatar_manifest(path)

    assert report["ok"] is False
    assert "asset exceeds max_bytes" in report["errors"]
    assert "license.id must be a non-empty string" in report["errors"]
    assert "modelSha256 must pin a 64-character SHA-256" in report["errors"]


def test_validator_rejects_remote_traversal_hash_mismatch_and_bad_glb(tmp_path):
    remote = valid_manifest(model="https://example.com/avatar.vrm")
    remote_report = validate_avatar_manifest(write_manifest(tmp_path, remote))
    assert "model must be a local relative asset path" in remote_report["errors"]

    bad_asset = b"not-a-glb"
    (tmp_path / "avatar.vrm").write_bytes(bad_asset)
    mismatch = valid_manifest(model="avatar.vrm")
    mismatch["modelSha256"] = "0" * 64
    mismatch_report = validate_avatar_manifest(write_manifest(tmp_path, mismatch))
    assert "modelSha256 does not match the local asset" in mismatch_report["errors"]
    assert "model is not a valid GLB/VRM container" in mismatch_report["errors"]

    escaped = valid_manifest(model="../avatar.vrm")
    escaped_report = validate_avatar_manifest(write_manifest(tmp_path, escaped))
    assert "model must be a local relative asset path" in escaped_report["errors"]


def test_procedural_manifest_is_zero_asset_but_still_governed(tmp_path):
    manifest = valid_manifest(kind="procedural3d", model=None)
    manifest["modelSha256"] = None

    report = validate_avatar_manifest(write_manifest(tmp_path, manifest))

    assert report["ok"] is True
    assert report["asset_sha256"] is None
    assert report["asset_bytes"] == 0
    assert report["budget"]["within_budget"] is True


def test_validator_rejects_unknown_fields_and_missing_attribution(tmp_path):
    asset = make_glb({"asset": {"version": "2.0"}})
    (tmp_path / "avatar.vrm").write_bytes(asset)
    manifest = valid_manifest()
    manifest["modelSha256"] = hashlib.sha256(asset).hexdigest()
    manifest["remoteTexture"] = "https://example.com/texture.png"
    path = write_manifest(tmp_path, manifest)
    (tmp_path / "ATTRIBUTION.md").unlink()

    report = validate_avatar_manifest(path)

    assert "manifest contains unknown field: remoteTexture" in report["errors"]
    assert "license.attributionFile does not exist" in report["errors"]


def test_validator_enforces_local_texture_dimensions(tmp_path):
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", 4096, 64)
    (tmp_path / "texture.png").write_bytes(png_header)
    document = {
        "asset": {"version": "2.0"},
        "images": [{"uri": "texture.png"}],
        "textures": [{"source": 0}],
    }
    asset = make_glb(document)
    (tmp_path / "avatar.vrm").write_bytes(asset)
    manifest = valid_manifest()
    manifest["modelSha256"] = hashlib.sha256(asset).hexdigest()

    report = validate_avatar_manifest(write_manifest(tmp_path, manifest))

    assert report["budget"]["max_texture_size"] == 4096
    assert "texture exceeds max_texture_size" in report["errors"]


def test_default_avatar_release_is_offline_licensed_and_bounded():
    manifest = json.loads(DEFAULT_AVATAR_MANIFEST.read_text(encoding="utf-8"))
    report = validate_avatar_manifest(DEFAULT_AVATAR_MANIFEST)

    assert report["ok"] is True, report["errors"]
    assert report["errors"] == []
    assert manifest["id"] == "javis-lightform"
    assert manifest["fallbackId"] in {"javis-anime", "javis-orb"}
    assert not any(REMOTE_SCHEME.match(value.strip()) for value in string_values(manifest))

    license_info = manifest["license"]
    assert all(license_info[field].strip() for field in ("id", "author", "source", "attributionFile"))
    attribution = DEFAULT_AVATAR_ROOT / license_info["attributionFile"]
    attribution_text = attribution.read_text(encoding="utf-8")
    assert attribution.is_file()
    assert license_info["id"] in attribution_text
    assert license_info["author"] in attribution_text
    assert license_info["source"] in attribution_text

    budget = manifest["assetBudget"]
    assert budget["maxBytes"] <= 25 * 1024 * 1024
    assert budget["maxTriangles"] <= 80_000
    assert budget["maxMaterials"] <= 8
    assert budget["maxTextureSize"] <= 2048
    assert sum(path.stat().st_size for path in DEFAULT_AVATAR_ROOT.rglob("*") if path.is_file()) <= budget["maxBytes"]
    assert report["budget"]["triangles"] <= budget["maxTriangles"]
    assert report["budget"]["materials"] <= budget["maxMaterials"]
    assert report["budget"]["max_texture_size"] <= budget["maxTextureSize"]
    assert report["budget"]["within_budget"] is True


def test_release_permanently_packages_2d_and_orb_fallbacks():
    skin_manifest = json.loads(
        (APP_ROOT / "public/pets/javis-anime/skin.json").read_text(encoding="utf-8")
    )
    for field in ("idle", "sourceStrip"):
        asset_path = APP_ROOT / "public" / skin_manifest[field].lstrip("/")
        assert asset_path.is_file()
        assert asset_path.stat().st_size > 0

    idle_asset = APP_ROOT / "public" / skin_manifest["idle"].lstrip("/")
    idle_sha256 = hashlib.sha256(idle_asset.read_bytes()).hexdigest()
    registry_source = (APP_ROOT / "src/pet/avatar/AvatarAssetRegistry.ts").read_text(encoding="utf-8")
    skin_registry_source = (APP_ROOT / "src/pet/PetSkinRegistry.ts").read_text(encoding="utf-8")
    styles_source = (APP_ROOT / "src/styles.css").read_text(encoding="utf-8")

    assert skin_manifest["id"] == "javis-anime"
    assert idle_sha256 in registry_source
    assert 'id: "javis-anime"' in registry_source
    assert 'fallbackId: "javis-orb"' in registry_source
    assert 'id: "javis-orb"' in registry_source
    assert 'id: "javis-anime"' in skin_registry_source
    assert 'id: "javis-orb"' in skin_registry_source
    assert ".pet-sprite.skin-orb" in styles_source
