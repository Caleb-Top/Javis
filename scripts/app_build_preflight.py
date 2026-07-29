"""Read-only readiness report for an offline Javis desktop build."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

try:
    from scripts.app_package_manifest import build_app_package_manifest
except ModuleNotFoundError:
    from app_package_manifest import build_app_package_manifest


ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


def _tool_exists(root: Path, bundled_relative: str, command: str) -> bool:
    return (root / bundled_relative).is_file() or shutil.which(command) is not None


def _tauri_crate_cached(root: Path) -> bool:
    cargo = root / "tools" / "rust" / "cargo" / "registry"
    source_matches = cargo.glob("src/*/tauri-*") if cargo.exists() else ()
    cache_matches = cargo.glob("cache/*/tauri-*.crate") if cargo.exists() else ()
    return any(path.is_dir() for path in source_matches) or any(path.is_file() for path in cache_matches)


def _package_boundary(root: Path) -> tuple[dict[str, Any], bool]:
    manifest = build_app_package_manifest(root)
    exclude = set(manifest.get("exclude", []))
    external = set(manifest.get("external", []))
    included = set(manifest.get("include", []))
    safe = (
        {"venv", "Lib", "python-embed", ".git", "logs"}.issubset(exclude)
        and {"ollama_models", "data", "workspace", "memory/*.sqlite"}.issubset(external)
        and {"main.py", "blueprint", "core", "control", "memory", "tools"}.issubset(included)
        and "start.py" not in included
    )
    return manifest, safe


def collect_build_preflight(root: Path = ROOT) -> dict[str, Any]:
    root = Path(root).resolve()
    app = root / "app"
    tauri_root = app / "src-tauri"
    package = _read_json(app / "package.json")
    config = _read_json(tauri_root / "tauri.conf.json")
    bundle = config.get("bundle", {}) if config else {}
    icons = bundle.get("icon", []) if isinstance(bundle, dict) else []
    icons = icons if isinstance(icons, list) else []
    icon_files = [tauri_root / str(icon) for icon in icons]
    package_boundary, boundary_safe = _package_boundary(root)

    checks = {
        "app_directory": app.is_dir(),
        "package_json": package is not None,
        "tauri_config": config is not None,
        "runtime_entry": (root / "main.py").is_file(),
        "bundled_node": _tool_exists(root, "tools/nodejs/node.exe", "node"),
        "cargo": _tool_exists(root, "tools/rust/cargo/bin/cargo.exe", "cargo"),
        "rustc": _tool_exists(root, "tools/rust/cargo/bin/rustc.exe", "rustc"),
        "frontend_dependencies": (
            (app / "node_modules").is_dir()
            and (app / "node_modules" / ".bin" / "tsc.cmd").is_file()
            and (app / "node_modules" / ".bin" / "vite.cmd").is_file()
        ),
        "tauri_crate_cached": _tauri_crate_cached(root),
        "bundle_active": bool(bundle.get("active")) if isinstance(bundle, dict) else False,
        "bundle_targets": bool(bundle.get("targets")) if isinstance(bundle, dict) else False,
        "bundle_icons": bool(icon_files) and all(path.is_file() for path in icon_files),
        "package_boundary_safe": boundary_safe,
    }

    blocker_map = [
        ("app-directory-missing", "app_directory"),
        ("package-json-invalid", "package_json"),
        ("tauri-config-invalid", "tauri_config"),
        ("runtime-entry-missing", "runtime_entry"),
        ("node-runtime-missing", "bundled_node"),
        ("cargo-missing", "cargo"),
        ("rustc-missing", "rustc"),
        ("frontend-dependencies-missing", "frontend_dependencies"),
        ("tauri-crate-cache-missing", "tauri_crate_cached"),
        ("bundle-disabled", "bundle_active"),
        ("bundle-target-missing", "bundle_targets"),
        ("bundle-icon-missing", "bundle_icons"),
        ("package-boundary-unsafe", "package_boundary_safe"),
    ]
    blockers = [name for name, check in blocker_map if not checks[check]]
    return {
        "schema_version": 1,
        "root": str(root),
        "ready_for_offline_build": not blockers,
        "blockers": blockers,
        "checks": checks,
        "bundle": {
            "active": bool(bundle.get("active")) if isinstance(bundle, dict) else False,
            "targets": bundle.get("targets", []) if isinstance(bundle, dict) else [],
            "icons": [str(path.relative_to(tauri_root)) for path in icon_files],
        },
        "package_boundary": package_boundary,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report offline Javis App build readiness")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    report = collect_build_preflight(args.root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.strict and not report["ready_for_offline_build"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
