"""Read-only readiness report for an offline Javis desktop build."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

try:
    from scripts.app_package_manifest import build_app_package_manifest
    from scripts.javis_full_installer import collect_ollama_model
except ModuleNotFoundError:
    from app_package_manifest import build_app_package_manifest
    from javis_full_installer import collect_ollama_model


ROOT = Path(__file__).resolve().parents[1]


def _drive_is_g(path: Path) -> bool:
    return Path(path).resolve().drive.upper() == "G:"


def _is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError):
        return None


def _shared_tool_root(root: Path) -> Path:
    """Return the main checkout that owns untracked G-drive dependencies."""
    root = Path(root).resolve()
    marker = root / ".git"
    if not marker.is_file():
        return root
    try:
        line = marker.read_text(encoding="utf-8").strip()
        if not line.lower().startswith("gitdir:"):
            return root
        git_dir = Path(line.split(":", 1)[1].strip())
        if not git_dir.is_absolute():
            git_dir = (root / git_dir).resolve()
        if git_dir.parent.name == "worktrees" and len(git_dir.parents) >= 3:
            candidate = git_dir.parents[2].resolve()
            if (candidate / "tools").is_dir():
                return candidate
    except (OSError, ValueError):
        pass
    return root


def _origins(root: Path) -> Iterable[tuple[str, Path]]:
    root = Path(root).resolve()
    yield "worktree", root
    shared = _shared_tool_root(root)
    if shared != root:
        yield "shared", shared


def _find_file(root: Path, relatives: str | Iterable[str]) -> tuple[bool, str, str]:
    relative_items = (relatives,) if isinstance(relatives, str) else tuple(relatives)
    for origin, base in _origins(root):
        for relative in relative_items:
            path = (base / relative).resolve()
            if path.is_file() and _drive_is_g(path) and _is_within(path, base):
                return True, origin, str(path)
    candidates = [str(base / relative) for _, base in _origins(root) for relative in relative_items]
    return False, "missing", " | ".join(candidates)


def _find_directory_with_files(root: Path, relative: str, members: Iterable[str]) -> tuple[bool, str, str]:
    for origin, base in _origins(root):
        directory = (base / relative).resolve()
        if (
            directory.is_dir()
            and _drive_is_g(directory)
            and _is_within(directory, base)
            and all(
                (directory / member).is_file()
                and _drive_is_g((directory / member).resolve())
                and _is_within((directory / member).resolve(), base)
                for member in members
            )
        ):
            return True, origin, str(directory)
    candidates = [str(base / relative) for _, base in _origins(root)]
    return False, "missing", " | ".join(candidates)


def _tauri_crate_cached(root: Path) -> tuple[bool, str, str]:
    for origin, base in _origins(root):
        cargo = (base / "tools" / "rust" / "cargo" / "registry").resolve()
        if not _drive_is_g(cargo) or not _is_within(cargo, base):
            continue
        source_matches = cargo.glob("src/*/tauri-*") if cargo.exists() else ()
        cache_matches = cargo.glob("cache/*/tauri-*.crate") if cargo.exists() else ()
        match = next((path for path in source_matches if path.is_dir()), None)
        if match is None:
            match = next((path for path in cache_matches if path.is_file()), None)
        if match is not None:
            resolved_match = match.resolve()
            if _drive_is_g(resolved_match) and _is_within(resolved_match, base):
                return True, origin, str(resolved_match)
    return False, "missing", "tauri crate absent from worktree and shared Cargo registries"


def _package_boundary(root: Path) -> tuple[dict[str, Any], bool]:
    manifest = build_app_package_manifest(root)
    exclude = set(manifest.get("exclude", []))
    external = set(manifest.get("external", []))
    bundled = set(manifest.get("bundled_release_components", []))
    optional = set(manifest.get("optional_addon_components", []))
    included = set(manifest.get("include", []))
    safe = (
        {"venv", "Lib", "python-embed", ".git", "logs"}.issubset(exclude)
        and {"data", "workspace", "memory/*.sqlite"}.issubset(external)
        and "ollama_models" not in bundled
        and "ollama_models" in optional
        and {"main.py", "blueprint", "core", "control", "memory", "tools"}.issubset(included)
        and "start.py" not in included
    )
    return manifest, safe


def _bundled_r1_available(root: Path) -> tuple[bool, str, str]:
    for origin, base in _origins(root):
        model_root = base / "ollama_models" / "models"
        try:
            if not _drive_is_g(model_root) or not _is_within(model_root, base):
                continue
            report = collect_ollama_model(model_root, "deepseek-r1:8b")
            if bool(report["files"]) and report["bytes"] > 0:
                return True, origin, str(model_root)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return False, "missing", "deepseek-r1:8b optional add-on payload absent"


def _result(
    *,
    available: bool,
    required: bool,
    classification: str,
    origin: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "status": "PASS" if available else ("FAIL" if required else "NOT_RUN"),
        "required": required,
        "classification": classification,
        "origin": origin,
        "detail": detail,
    }


def collect_build_preflight(root: Path = ROOT) -> dict[str, Any]:
    root = Path(root).resolve()
    dependency_root = _shared_tool_root(root)
    app = root / "app"
    tauri_root = app / "src-tauri"
    package = _read_json(app / "package.json")
    config = _read_json(tauri_root / "tauri.conf.json")
    bundle = config.get("bundle", {}) if config else {}
    icons = bundle.get("icon", []) if isinstance(bundle, dict) else []
    icons = icons if isinstance(icons, list) else []
    icon_files = [tauri_root / str(icon) for icon in icons]
    package_boundary, boundary_safe = _package_boundary(root)

    discovered: dict[str, tuple[bool, str, str]] = {
        "bundled_node": _find_file(root, "tools/nodejs/node.exe"),
        "cargo": _find_file(
            root,
            (
                "tools/rust/rustup/toolchains/stable-x86_64-pc-windows-gnu/bin/cargo.exe",
                "tools/rust/cargo/bin/cargo.exe",
            ),
        ),
        "rustc": _find_file(
            root,
            (
                "tools/rust/rustup/toolchains/stable-x86_64-pc-windows-gnu/bin/rustc.exe",
                "tools/rust/cargo/bin/rustc.exe",
            ),
        ),
        "optional_ollama_runtime": _find_file(root, "tools/ollama-runtime/ollama.exe"),
        "bundled_python_runtime": _find_file(root, "tools/python-runtime-3.11/python.exe"),
        "bundled_stt_model": _find_file(root, "models/faster-whisper-base/model.bin"),
        "frontend_dependencies": _find_directory_with_files(
            root,
            "app/node_modules/.bin",
            ("tsc.cmd", "vite.cmd"),
        ),
        "tauri_crate_cached": _tauri_crate_cached(root),
        "tauri_local_nsis_cached": _find_file(
            root,
            "app/src-tauri/target/.tauri/NSIS/makensis.exe",
        ),
        "optional_r1_model": _bundled_r1_available(root),
    }

    results: dict[str, dict[str, Any]] = {}

    def add(
        name: str,
        available: bool,
        *,
        required: bool,
        classification: str,
        origin: str = "worktree",
        detail: str = "",
    ) -> None:
        results[name] = _result(
            available=available,
            required=required,
            classification=classification,
            origin=origin,
            detail=detail,
        )

    add("app_directory", app.is_dir(), required=True, classification="source", detail=str(app))
    add("package_json", package is not None, required=True, classification="source", detail=str(app / "package.json"))
    add("tauri_config", config is not None, required=True, classification="source", detail=str(tauri_root / "tauri.conf.json"))
    add("runtime_entry", (root / "main.py").is_file(), required=True, classification="source", detail=str(root / "main.py"))
    add(
        "source_root_on_g",
        _drive_is_g(root),
        required=True,
        classification="policy",
        detail=str(root),
    )
    add(
        "dependency_root_on_g",
        _drive_is_g(dependency_root),
        required=True,
        classification="policy",
        detail=str(dependency_root),
    )
    for name, required, classification in (
        ("bundled_node", True, "toolchain"),
        ("cargo", True, "toolchain"),
        ("rustc", True, "toolchain"),
        ("optional_ollama_runtime", False, "optional_data"),
        ("optional_r1_model", False, "optional_data"),
        ("bundled_python_runtime", True, "runtime_data"),
        ("bundled_stt_model", True, "runtime_data"),
        ("frontend_dependencies", True, "toolchain"),
        ("tauri_crate_cached", True, "toolchain"),
        ("tauri_local_nsis_cached", True, "toolchain"),
    ):
        available, origin, detail = discovered[name]
        add(
            name,
            available,
            required=required,
            classification=classification,
            origin=origin,
            detail=detail,
        )
    add(
        "tauri_local_tools_enabled",
        bool(bundle.get("useLocalToolsDir")) if isinstance(bundle, dict) else False,
        required=True,
        classification="configuration",
        detail="bundle.useLocalToolsDir",
    )
    add(
        "bundle_active",
        bool(bundle.get("active")) if isinstance(bundle, dict) else False,
        required=True,
        classification="configuration",
        detail="bundle.active",
    )
    add(
        "bundle_targets",
        bool(bundle.get("targets")) if isinstance(bundle, dict) else False,
        required=True,
        classification="configuration",
        detail="bundle.targets",
    )
    add(
        "bundle_icons",
        bool(icon_files) and all(path.is_file() for path in icon_files),
        required=True,
        classification="source",
        detail=" | ".join(str(path) for path in icon_files),
    )
    add(
        "package_boundary_safe",
        boundary_safe,
        required=True,
        classification="policy",
        detail="offline package boundary",
    )

    blocker_names = {
        "app_directory": "app-directory-missing",
        "package_json": "package-json-invalid",
        "tauri_config": "tauri-config-invalid",
        "runtime_entry": "runtime-entry-missing",
        "source_root_on_g": "source-root-not-g-drive",
        "dependency_root_on_g": "dependency-root-not-g-drive",
        "bundled_node": "node-runtime-missing",
        "cargo": "cargo-missing",
        "rustc": "rustc-missing",
        "bundled_python_runtime": "bundled-python-runtime-missing",
        "bundled_stt_model": "bundled-stt-model-missing",
        "frontend_dependencies": "frontend-dependencies-missing",
        "tauri_crate_cached": "tauri-crate-cache-missing",
        "tauri_local_tools_enabled": "tauri-local-tools-disabled",
        "tauri_local_nsis_cached": "tauri-local-nsis-cache-missing",
        "bundle_active": "bundle-disabled",
        "bundle_targets": "bundle-target-missing",
        "bundle_icons": "bundle-icon-missing",
        "package_boundary_safe": "package-boundary-unsafe",
    }
    blockers = [blocker for name, blocker in blocker_names.items() if results[name]["status"] == "FAIL"]
    checks = {name: result["status"] == "PASS" for name, result in results.items()}
    return {
        "schema_version": 2,
        "root": str(root),
        "dependency_root": str(dependency_root),
        "ready_for_offline_build": not blockers,
        "blockers": blockers,
        "not_run": [name for name, result in results.items() if result["status"] == "NOT_RUN"],
        "checks": checks,
        "results": results,
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
