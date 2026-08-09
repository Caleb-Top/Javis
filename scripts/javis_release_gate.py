"""Fail-closed source gates that must pass before release packaging starts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from scripts.app_build_preflight import (
        _drive_is_g,
        _is_within,
        _shared_tool_root,
        collect_build_preflight,
    )
    from scripts.javis_release_version import load_version_contract
except ModuleNotFoundError:
    from app_build_preflight import _drive_is_g, _is_within, _shared_tool_root, collect_build_preflight
    from javis_release_version import load_version_contract


ROOT = Path(__file__).resolve().parents[1]
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_GATE_IDS = (
    "source_tree_start",
    "version_consistency",
    "preflight",
    "release_contracts",
    "python",
    "app",
    "rust",
    "installer_source",
    "source_tree_end",
)


@dataclass(frozen=True)
class GateCheck:
    check_id: str
    command: tuple[str, ...]
    cwd: Path


Runner = Callable[[GateCheck], tuple[int, str]]


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    root = Path(root).resolve()
    return subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def source_tree_head(root: Path) -> str:
    result = _git(root, "rev-parse", "HEAD")
    commit = result.stdout.strip().lower()
    if result.returncode != 0 or not COMMIT_PATTERN.fullmatch(commit):
        raise RuntimeError("unable to resolve release HEAD: " + (result.stderr or result.stdout).strip())
    return commit


def source_tree_changes(root: Path) -> list[str]:
    result = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        raise RuntimeError("unable to inspect release source tree: " + (result.stderr or result.stdout).strip())
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def release_gate_plan_id(version: str, source_commit: str) -> str:
    payload = {
        "schema_version": 2,
        "version": str(version),
        "source_commit": str(source_commit).lower(),
        "required_gate_ids": list(REQUIRED_GATE_IDS),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_release_gate_report(
    report: Any,
    *,
    expected_version: str | None = None,
    expected_commit: str | None = None,
) -> list[str]:
    if not isinstance(report, dict):
        return ["gate report must be an object"]
    errors: list[str] = []
    version = str(report.get("version") or "")
    commit = str(report.get("source_commit") or "").lower()
    if report.get("schema_version") != 2:
        errors.append("gate report schema_version must be 2")
    if report.get("overall_status") != "PASS":
        errors.append("gate report overall_status must be PASS")
    if expected_version is not None and version != expected_version:
        errors.append(f"gate report version must be {expected_version}")
    if not COMMIT_PATTERN.fullmatch(commit):
        errors.append("gate report source_commit is invalid")
    if expected_commit is not None and commit != expected_commit.lower():
        errors.append(f"gate report source_commit must equal HEAD {expected_commit.lower()}")
    if tuple(report.get("required_gate_ids") or ()) != REQUIRED_GATE_IDS:
        errors.append("gate report required_gate_ids are incomplete or reordered")
    if COMMIT_PATTERN.fullmatch(commit):
        expected_plan = release_gate_plan_id(version, commit)
        if report.get("plan_id") != expected_plan:
            errors.append("gate report plan_id does not bind version/commit/gates")
    checks = report.get("checks")
    if not isinstance(checks, list):
        errors.append("gate report checks must be a list")
    else:
        ids = tuple(check.get("id") for check in checks if isinstance(check, dict))
        if ids != REQUIRED_GATE_IDS or len(checks) != len(REQUIRED_GATE_IDS):
            errors.append("gate report checks do not exactly match required gates")
        for check in checks:
            if not isinstance(check, dict):
                errors.append("gate report check must be an object")
            elif check.get("status") != "PASS" or check.get("required") is not True:
                errors.append(f"required gate is not PASS: {check.get('id')}")
    return errors


def build_gate_plan(
    root: Path,
    *,
    python: Path,
    node: Path,
    cargo: Path,
    source_commit: str,
    powershell: str = "powershell.exe",
) -> list[GateCheck]:
    root = Path(root).resolve()
    source_commit = str(source_commit).lower()
    if not COMMIT_PATTERN.fullmatch(source_commit):
        raise ValueError("source_commit must be a full Git object id")
    python_exe = str(python)
    node_exe = str(node)
    cargo_exe = str(cargo)
    app_tests = tuple(str(path) for path in sorted((root / "app/tests").glob("*.test.ts")))
    snapshot = (
        python_exe,
        "-B",
        str(root / "scripts/javis_release_gate.py"),
        "--root",
        str(root),
        "--check-source-tree",
        "--expected-head",
        source_commit,
    )
    return [
        GateCheck("source_tree_start", snapshot, root),
        GateCheck(
            "version_consistency",
            (python_exe, "-B", str(root / "scripts/javis_release_version.py"), "--root", str(root), "--check"),
            root,
        ),
        GateCheck(
            "preflight",
            (python_exe, "-B", str(root / "scripts/app_build_preflight.py"), "--root", str(root), "--strict"),
            root,
        ),
        GateCheck(
            "release_contracts",
            (
                python_exe,
                "-B",
                "-m",
                "pytest",
                "tests/test_javis_release_baseline.py",
                "tests/test_app_build_preflight.py",
                "tests/test_app_v3_integrated_release.py",
                "tests/test_full_offline_installer.py",
                "-q",
                "-p",
                "no:cacheprovider",
            ),
            root,
        ),
        GateCheck("python", (python_exe, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"), root),
        GateCheck("app", (node_exe, "--experimental-strip-types", "--test", *app_tests), root),
        GateCheck(
            "rust",
            (
                cargo_exe,
                "test",
                "--manifest-path",
                str(root / "app/src-tauri/Cargo.toml"),
                "--locked",
                "--offline",
            ),
            root,
        ),
        GateCheck(
            "installer_source",
            (
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(root / "scripts/verify_javis_v3_installer.ps1"),
                "-SourceRoot",
                str(root),
                "-SourceRegressionOnly",
            ),
            root,
        ),
        GateCheck("source_tree_end", snapshot, root),
    ]


def run_gate_plan(
    plan: Sequence[GateCheck],
    *,
    runner: Runner,
    version: str,
    source_commit: str,
) -> dict[str, Any]:
    plan_ids = tuple(check.check_id for check in plan)
    checks: list[dict[str, Any]] = []
    failed = plan_ids != REQUIRED_GATE_IDS
    if failed:
        checks.append(
            {
                "id": "gate_plan",
                "status": "FAIL",
                "required": True,
                "detail": f"expected {REQUIRED_GATE_IDS}, got {plan_ids}",
            }
        )
    else:
        for check in plan:
            if failed:
                checks.append(
                    {"id": check.check_id, "status": "NOT_RUN", "required": True, "detail": "earlier required gate failed"}
                )
                continue
            started = time.monotonic()
            try:
                returncode, output = runner(check)
            except Exception as error:
                returncode, output = 127, f"{type(error).__name__}: {error}"
            status = "PASS" if returncode == 0 else "FAIL"
            checks.append(
                {
                    "id": check.check_id,
                    "status": status,
                    "required": True,
                    "returncode": returncode,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "detail": str(output)[-4000:],
                }
            )
            failed = status == "FAIL"
    return {
        "schema_version": 2,
        "overall_status": "FAIL" if failed else "PASS",
        "version": version,
        "source_commit": source_commit.lower(),
        "required_gate_ids": list(REQUIRED_GATE_IDS),
        "plan_id": release_gate_plan_id(version, source_commit),
        "checks": checks,
    }


def _existing_file(candidates: Sequence[Path]) -> Path | None:
    return next((path.resolve() for path in candidates if path.is_file()), None)


def validate_gate_tool_path(path: Path, source_root: Path, shared_root: Path) -> Path:
    resolved = Path(path).resolve()
    if not _drive_is_g(resolved) or not any(
        _is_within(resolved, allowed) for allowed in (Path(source_root).resolve(), Path(shared_root).resolve())
    ):
        raise ValueError(f"gate tool must be on G: and contained by source/shared root: {resolved}")
    return resolved


def _resolve_tools(root: Path) -> tuple[Path, Path, Path]:
    root = Path(root).resolve()
    shared = _shared_tool_root(root)
    if not _drive_is_g(root) or not _drive_is_g(shared):
        raise ValueError(f"release source and dependency roots must stay on G:: {root}; {shared}")
    python = _existing_file((root / "venv/Scripts/python.exe", shared / "venv/Scripts/python.exe"))
    preflight = collect_build_preflight(root)

    def checked_path(check_id: str) -> Path | None:
        result = preflight["results"][check_id]
        path = Path(str(result.get("detail") or ""))
        return path.resolve() if result.get("status") == "PASS" and path.is_file() else None

    node = checked_path("bundled_node")
    cargo = checked_path("cargo")
    missing = [name for name, value in (("python", python), ("node", node), ("cargo", cargo)) if value is None]
    if missing:
        raise FileNotFoundError("release gate tools missing: " + ", ".join(missing))
    return tuple(validate_gate_tool_path(path, root, shared) for path in (python, node, cargo))  # type: ignore[return-value]


def _subprocess_runner(root: Path) -> Runner:
    root = Path(root).resolve()
    dependency_root = _shared_tool_root(root)
    build_cache_root = dependency_root.parent / f"{dependency_root.name}-build-cache"
    gate_root = build_cache_root / "release-gate" / root.name
    gate_temp = gate_root / "temp"
    gate_temp.mkdir(parents=True, exist_ok=True)
    toolchain_bin = dependency_root / "tools/rust/rustup/toolchains/stable-x86_64-pc-windows-gnu/bin"
    mingw_bin = dependency_root / "tools/mingw32/bin"
    cargo_bin = dependency_root / "tools/rust/cargo/bin"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "TEMP": str(gate_temp),
            "TMP": str(gate_temp),
            "CARGO_HOME": str(dependency_root / "tools/rust/cargo"),
            "RUSTUP_HOME": str(dependency_root / "tools/rust/rustup"),
            "CARGO_TARGET_DIR": str(gate_root / "rust-target"),
            "TAURI_CONFIG": json.dumps({"bundle": {"resources": []}}, separators=(",", ":")),
            "PATH": os.pathsep.join(
                (str(toolchain_bin), str(mingw_bin), str(cargo_bin), environment.get("PATH", ""))
            ),
        }
    )

    def run(check: GateCheck) -> tuple[int, str]:
        result = subprocess.run(
            list(check.command),
            cwd=check.cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60 * 60,
        )
        return result.returncode, "\n".join(part for part in (result.stdout, result.stderr) if part).strip()

    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all required Javis source release gates")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--check-source-tree", action="store_true")
    parser.add_argument("--expected-head", default="")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.check_source_tree:
        head = source_tree_head(root)
        changes = source_tree_changes(root)
        passed = not changes and bool(args.expected_head) and head == args.expected_head.lower()
        payload = {"schema_version": 1, "status": "PASS" if passed else "FAIL", "head": head, "changes": changes}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if passed else 2

    report_path = (args.report or (root / "tmp/release-gate-report.json")).resolve()
    try:
        version = load_version_contract(root)["version"]
        source_commit = source_tree_head(root)
        python, node, cargo = _resolve_tools(root)
        plan = build_gate_plan(
            root,
            python=python,
            node=node,
            cargo=cargo,
            source_commit=source_commit,
        )
        report = run_gate_plan(
            plan,
            runner=_subprocess_runner(root),
            version=version,
            source_commit=source_commit,
        )
    except Exception as error:
        report = {
            "schema_version": 2,
            "overall_status": "FAIL",
            "version": "",
            "source_commit": "",
            "required_gate_ids": list(REQUIRED_GATE_IDS),
            "plan_id": "",
            "checks": [{"id": "gate_setup", "status": "FAIL", "required": True, "detail": f"{type(error).__name__}: {error}"}],
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(report_path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["overall_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
