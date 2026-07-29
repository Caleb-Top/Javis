"""CLI for producing the current Javis packaged Python runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from .javis_release_runtime import build_runtime_archive, validate_runtime_archive
except ImportError:
    from javis_release_runtime import build_runtime_archive, validate_runtime_archive


def _discover_stt_model(root: Path) -> Path:
    packaged = root / "models/faster-whisper-base"
    if (packaged / "model.bin").is_file():
        return packaged
    snapshots = (
        Path.home()
        / ".cache/huggingface/hub/models--Systran--faster-whisper-base/snapshots"
    )
    if snapshots.is_dir():
        candidates = sorted(
            (path for path in snapshots.iterdir() if (path / "model.bin").is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]
    raise FileNotFoundError(
        "local faster-whisper base model is missing; runtime staging never downloads models"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Javis release runtime archive")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python-root", type=Path)
    parser.add_argument("--site-packages", type=Path)
    parser.add_argument("--stt-model-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.root.resolve()
    python_root = (args.python_root or Path(sys.base_prefix)).resolve()
    site_packages = (
        args.site_packages or Path(sys.prefix) / "Lib/site-packages"
    ).resolve()
    stt_model_root = (
        args.stt_model_root.resolve()
        if args.stt_model_root
        else _discover_stt_model(root).resolve()
    )
    output = (args.output or root / "app/src-tauri/resources/javis-runtime.zip").resolve()
    manifest = build_runtime_archive(
        root,
        output,
        python_root,
        site_packages_root=site_packages,
        stt_model_root=stt_model_root,
    )
    errors = validate_runtime_archive(output, manifest)
    print(json.dumps({"archive": str(output), "manifest": manifest, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
