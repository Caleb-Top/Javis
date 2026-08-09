"""Validate and hash the v3 main installer plus optional R1 add-on."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

try:
    from scripts.javis_candidate_manifest import build_candidate_manifest, validate_candidate_manifest
    from scripts.javis_release_gate import source_tree_head, validate_release_gate_report
    from scripts.javis_release_version import load_version_contract, release_artifact_names
except ModuleNotFoundError:
    from javis_candidate_manifest import build_candidate_manifest, validate_candidate_manifest
    from javis_release_gate import source_tree_head, validate_release_gate_report
    from javis_release_version import load_version_contract, release_artifact_names


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--runtime-archive", type=Path, required=True)
    parser.add_argument("--gate-report", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--without-addon", action="store_true")
    args = parser.parse_args(argv)

    artifact = args.artifact_dir.resolve()
    setup = artifact / "Javis-v3.0.0-Setup.exe"
    addon_dir = artifact / "Javis-R1-8B-Addon"
    addon_manifest_path = addon_dir / "Javis-R1-8B-Addon.manifest.json"
    addon_report = artifact / "Javis-R1-8B-Addon.report.json"
    runtime_report = artifact / "RUNTIME-TEST-REPORT.md"
    install_report = artifact / "INSTALL-TEST-REPORT.md"
    required = [setup, runtime_report, install_report]
    if not args.without_addon:
        required.extend((addon_manifest_path, addon_report))
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing release artifacts: " + ", ".join(missing))
    with setup.open("rb") as stream:
        signature = stream.read(2)
    if setup.stat().st_size >= 4 * 1024**3 or signature != b"MZ":
        raise ValueError("main setup is not a Windows-loadable sub-4GB PE file")

    runtime_copy = artifact / args.runtime_manifest.name
    release_copy = artifact / args.release_manifest.name
    shutil.copy2(args.runtime_manifest, runtime_copy)
    shutil.copy2(args.release_manifest, release_copy)

    addon: dict[str, object] = {}
    payload_files: list[Path] = []
    payload_hashes: dict[str, str] = {}
    addon_files: list[Path] = []
    if not args.without_addon:
        addon = json.loads(addon_manifest_path.read_text(encoding="utf-8"))
        addon_files.extend((addon_manifest_path, addon_report))
        for item in addon.get("payloads", []):
            path = (addon_dir / str(item.get("path") or "")).resolve()
            if path.parent != addon_dir or not path.is_file():
                raise ValueError(f"invalid add-on payload path: {path}")
            if path.stat().st_size != int(item.get("size") or -1):
                raise ValueError(f"add-on payload size mismatch: {path.name}")
            actual = sha256(path)
            if actual != str(item.get("sha256") or "").lower():
                raise ValueError(f"add-on payload checksum mismatch: {path.name}")
            payload_files.append(path)
            payload_hashes[path.name] = actual

    files = [
        setup,
        runtime_copy,
        release_copy,
        runtime_report,
        install_report,
        *addon_files,
        *payload_files,
    ]
    hashes = {str(path.relative_to(artifact)).replace("\\", "/"): payload_hashes.get(path.name) or sha256(path) for path in files}
    summary = {
        "schema": 1,
        "product": "Javis",
        "version": "3.0.0",
        "delivery": "main-setup-only" if args.without_addon else "main-setup-plus-optional-r1-addon",
        "main_installer": {
            "path": setup.name,
            "size": setup.stat().st_size,
            "sha256": hashes[setup.name],
            "contains_local_model": False,
        },
        "optional_addon": {"included": False} if args.without_addon else {
            "included": True,
            "path": addon_dir.name,
            "model": addon.get("model"),
            "payloads": addon.get("payloads", []),
        },
        "runtime_test": "PASS" if "Overall: PASS" in runtime_report.read_text(encoding="utf-8") else "FAIL",
        "installation_test": "PASS" if "Overall: PASS" in install_report.read_text(encoding="utf-8") else "FAIL",
        "hashes": hashes,
    }
    if summary["runtime_test"] != "PASS" or summary["installation_test"] != "PASS":
        raise ValueError("release test reports are not PASS")
    contract = load_version_contract(args.root)
    gate_report = json.loads(args.gate_report.read_text(encoding="utf-8"))
    head = source_tree_head(args.root)
    gate_errors = validate_release_gate_report(
        gate_report,
        expected_version=contract["version"],
        expected_commit=head,
    )
    if gate_errors:
        raise ValueError("release gate report is invalid: " + "; ".join(gate_errors))
    candidate = build_candidate_manifest(
        version=contract["version"],
        runtime_archive=args.runtime_archive,
        package=setup,
        gate_report=gate_report,
    )
    candidate_errors = validate_candidate_manifest(candidate)
    if candidate_errors:
        raise ValueError("candidate manifest is invalid: " + "; ".join(candidate_errors))
    if candidate["package_sha256"] != hashes[setup.name]:
        raise ValueError("candidate package hash diverged during finalization")
    runtime_metadata = json.loads(args.runtime_manifest.read_text(encoding="utf-8"))
    expected_runtime_hash = str(runtime_metadata.get("archive", {}).get("sha256") or "").lower()
    if candidate["runtime_sha256"] != expected_runtime_hash:
        raise ValueError("candidate runtime hash does not match runtime manifest")
    candidate_path = artifact / release_artifact_names(contract["version"])["candidate_manifest"]
    candidate_temp = candidate_path.with_suffix(candidate_path.suffix + ".tmp")
    candidate_temp.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    candidate_temp.replace(candidate_path)
    hashes[candidate_path.name] = sha256(candidate_path)
    (artifact / "Javis-v3.0.0-Release.manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifact / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()),
        encoding="ascii",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
