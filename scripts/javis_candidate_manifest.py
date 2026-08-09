"""Create the immutable manifest for one already-built Javis candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.app_build_preflight import _drive_is_g, _is_within
    from scripts.javis_release_gate import validate_release_gate_report
    from scripts.javis_release_version import load_version_contract, release_artifact_names
except ModuleNotFoundError:
    from app_build_preflight import _drive_is_g, _is_within
    from javis_release_gate import validate_release_gate_report
    from javis_release_version import load_version_contract, release_artifact_names


ROOT = Path(__file__).resolve().parents[1]
SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_candidate_manifest(
    *,
    version: str,
    runtime_archive: Path,
    package: Path,
    gate_report: dict[str, Any],
) -> dict[str, Any]:
    version = str(version).strip()
    runtime_archive = Path(runtime_archive).resolve()
    package = Path(package).resolve()
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"invalid candidate version: {version!r}")
    gate_errors = validate_release_gate_report(gate_report, expected_version=version)
    if gate_errors:
        raise ValueError("invalid gate report: " + "; ".join(gate_errors))
    source_commit = str(gate_report["source_commit"]).lower()
    if not runtime_archive.is_file():
        raise FileNotFoundError(f"runtime archive missing: {runtime_archive}")
    if runtime_archive.name != "javis-runtime.zip":
        raise ValueError(f"unexpected runtime archive name: {runtime_archive.name}")
    if not package.is_file():
        raise FileNotFoundError(f"package missing: {package}")
    expected_package = release_artifact_names(version)["package"]
    if package.name != expected_package:
        raise ValueError(f"unexpected candidate package name: expected {expected_package}, got {package.name}")

    runtime_hash = sha256(runtime_archive)
    package_hash = sha256(package)
    gate_hash = canonical_json_sha256(gate_report)
    candidate_id = f"javis-v{version}-{source_commit[:12]}-{package_hash[:12]}"
    return {
        "schema_version": 2,
        "product": "Javis",
        "candidate_id": candidate_id,
        "version": version,
        "source_commit": source_commit,
        "runtime_sha256": runtime_hash,
        "package_sha256": package_hash,
        "gate_report_sha256": gate_hash,
        "test_summary": deepcopy(gate_report),
        "acceptance_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime": {"name": runtime_archive.name, "bytes": runtime_archive.stat().st_size},
        "package": {"name": package.name, "bytes": package.stat().st_size},
    }


def validate_candidate_manifest(manifest: Any) -> list[str]:
    if not isinstance(manifest, dict):
        return ["candidate manifest must be an object"]
    errors: list[str] = []
    required = {
        "version",
        "source_commit",
        "runtime_sha256",
        "package_sha256",
        "gate_report_sha256",
        "test_summary",
        "acceptance_status",
        "candidate_id",
    }
    missing = sorted(required - set(manifest))
    if missing:
        errors.append("missing required fields: " + ", ".join(missing))
    if manifest.get("schema_version") != 2:
        errors.append("candidate schema_version must be 2")
    if manifest.get("product") != "Javis":
        errors.append("candidate product must be Javis")
    version = str(manifest.get("version") or "")
    source_commit = str(manifest.get("source_commit") or "").lower()
    runtime_hash = str(manifest.get("runtime_sha256") or "").lower()
    package_hash = str(manifest.get("package_sha256") or "").lower()
    gate_hash = str(manifest.get("gate_report_sha256") or "").lower()
    if not VERSION_PATTERN.fullmatch(version):
        errors.append("version is not strict semantic version")
    if not SHA_PATTERN.fullmatch(source_commit):
        errors.append("source_commit is not a full hexadecimal Git object id")
    if not SHA256_PATTERN.fullmatch(runtime_hash):
        errors.append("runtime_sha256 is not SHA-256")
    if not SHA256_PATTERN.fullmatch(package_hash):
        errors.append("package_sha256 is not SHA-256")
    if not SHA256_PATTERN.fullmatch(gate_hash):
        errors.append("gate_report_sha256 is not SHA-256")
    if manifest.get("acceptance_status") != "pending":
        errors.append("candidate acceptance_status must be pending")
    report = manifest.get("test_summary")
    errors.extend(validate_release_gate_report(report, expected_version=version, expected_commit=source_commit))
    if isinstance(report, dict) and SHA256_PATTERN.fullmatch(gate_hash):
        if canonical_json_sha256(report) != gate_hash:
            errors.append("gate_report_sha256 does not match test_summary")
    if VERSION_PATTERN.fullmatch(version) and SHA_PATTERN.fullmatch(source_commit) and SHA256_PATTERN.fullmatch(package_hash):
        expected_id = f"javis-v{version}-{source_commit[:12]}-{package_hash[:12]}"
        if manifest.get("candidate_id") != expected_id:
            errors.append(f"candidate_id must bind version/source/package: {expected_id}")
    package_info = manifest.get("package")
    if isinstance(package_info, dict) and VERSION_PATTERN.fullmatch(version):
        if package_info.get("name") != release_artifact_names(version)["package"]:
            errors.append("candidate package name does not match version contract")
    runtime_info = manifest.get("runtime")
    if isinstance(runtime_info, dict) and runtime_info.get("name") != "javis-runtime.zip":
        errors.append("candidate runtime name must be javis-runtime.zip")
    return errors


def _source_commit(root: Path) -> str:
    root = Path(root).resolve()
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    commit = result.stdout.strip().lower()
    if result.returncode != 0 or not SHA_PATTERN.fullmatch(commit):
        raise ValueError(f"unable to resolve source commit: {(result.stderr or result.stdout).strip()}")
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an immutable Javis candidate manifest")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    contract = load_version_contract(root)
    head = _source_commit(root)
    report = json.loads(args.gate_report.read_text(encoding="utf-8"))
    gate_errors = validate_release_gate_report(report, expected_version=contract["version"], expected_commit=head)
    if gate_errors:
        raise ValueError("invalid gate report: " + "; ".join(gate_errors))
    for label, path in (("runtime", args.runtime), ("package", args.package), ("gate report", args.gate_report)):
        resolved = path.resolve()
        if not _drive_is_g(resolved) or not _is_within(resolved, root):
            raise ValueError(f"{label} must stay inside the G-drive source root: {resolved}")
    manifest = build_candidate_manifest(
        version=contract["version"],
        runtime_archive=args.runtime,
        package=args.package,
        gate_report=report,
    )
    errors = validate_candidate_manifest(manifest)
    if errors:
        raise ValueError("candidate manifest validation failed: " + "; ".join(errors))
    output = args.output or (
        args.package.resolve().parent / release_artifact_names(contract["version"])["candidate_manifest"]
    )
    output = Path(output).resolve()
    if not _drive_is_g(output) or not _is_within(output, root):
        raise ValueError(f"candidate output must stay inside the G-drive source root: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
