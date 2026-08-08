"""Release path policy for the G-drive build and D-drive install test."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def release_layout(source_root: Path, *, test_drive: str = "D:") -> dict[str, str]:
    source_root = Path(source_root).resolve()
    if source_root.drive.upper() != "G:":
        raise ValueError(f"Javis release builds must run from G:, got {source_root}")
    normalized_test_drive = str(test_drive).rstrip("\\/") + "\\"
    test_root = Path(normalized_test_drive)
    if test_root.drive.upper() != "D:":
        raise ValueError(f"Javis independent installation tests must use D:, got {test_drive}")

    artifact_dir = source_root / "artifacts" / "Javis-v3.0.0"
    delivery_dir = test_root / "Javis-v3.0.0-User-Test"
    return {
        "source_root": str(source_root),
        "build_temp": str(source_root / "tmp" / "release-v3"),
        "artifact_dir": str(artifact_dir),
        "cargo_target": str(source_root / "app" / "src-tauri" / "target"),
        "package_output": str(artifact_dir / "Javis-v3.0.0-Setup.exe"),
        "delivery_dir": str(delivery_dir),
        "delivery_installer": str(delivery_dir / "Javis-v3.0.0-Setup.exe"),
        "install_test_root": str(test_root / "Javis-v3-install-test"),
        "install_test_data": str(test_root / "Javis-v3-data-test"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--test-drive", default="D:")
    args = parser.parse_args(argv)
    print(json.dumps(release_layout(args.root, test_drive=args.test_drive), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
