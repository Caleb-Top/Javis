"""Validate and hash the v3 main installer plus optional R1 add-on."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    args = parser.parse_args()

    artifact = args.artifact_dir.resolve()
    setup = artifact / "Javis-v3.0.0-Setup.exe"
    addon_dir = artifact / "Javis-R1-8B-Addon"
    addon_manifest_path = addon_dir / "Javis-R1-8B-Addon.manifest.json"
    addon_report = artifact / "Javis-R1-8B-Addon.report.json"
    runtime_report = artifact / "RUNTIME-TEST-REPORT.md"
    install_report = artifact / "INSTALL-TEST-REPORT.md"
    required = [setup, addon_manifest_path, addon_report, runtime_report, install_report]
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

    addon = json.loads(addon_manifest_path.read_text(encoding="utf-8"))
    payload_files: list[Path] = []
    payload_hashes: dict[str, str] = {}
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
        addon_manifest_path,
        addon_report,
        runtime_copy,
        release_copy,
        runtime_report,
        install_report,
        *payload_files,
    ]
    hashes = {str(path.relative_to(artifact)).replace("\\", "/"): payload_hashes.get(path.name) or sha256(path) for path in files}
    summary = {
        "schema": 1,
        "product": "Javis",
        "version": "3.0.0",
        "delivery": "main-setup-plus-optional-r1-addon",
        "main_installer": {
            "path": setup.name,
            "size": setup.stat().st_size,
            "sha256": hashes[setup.name],
            "contains_local_model": False,
        },
        "optional_addon": {
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
