"""Report or remove only reproducible Javis build outputs."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROTECTED_ROOTS = {
    ".git", ".claude", ".codex", ".hermes", "blueprint", "brain_data", "data",
    "memory", "models", "ollama_models", "skills", "tools/yolo", "train_output",
    "training", "workspace",
}
GENERATED_TARGETS = (
    "app/src-tauri/target",
    "_sdk_extract",
)


def _inside_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def _protected(path: Path) -> bool:
    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    return any(relative == item or relative.startswith(item + "/") for item in PROTECTED_ROOTS)


def generated_paths() -> list[Path]:
    paths = [ROOT / relative for relative in GENERATED_TARGETS]
    paths.extend(ROOT.rglob("__pycache__"))
    paths.extend(ROOT.rglob(".pytest_cache"))
    unique: dict[str, Path] = {}
    for path in paths:
        if path.exists() and _inside_root(path) and not _protected(path):
            unique[str(path.resolve()).lower()] = path
    return sorted(unique.values(), key=lambda item: (len(item.parts), str(item)), reverse=True)


def path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean reproducible Javis outputs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report only; this is the default")
    mode.add_argument("--execute", action="store_true", help="Remove the reported generated paths")
    args = parser.parse_args()

    targets = generated_paths()
    total = sum(path_size(path) for path in targets)
    for path in targets:
        relative = path.resolve().relative_to(ROOT.resolve())
        print(f"{'REMOVE' if args.execute else 'KEEP'} {relative} {path_size(path)}")
    print(f"TOTAL_BYTES={total}")
    if not args.execute:
        return 0
    for path in targets:
        if not _inside_root(path) or _protected(path):
            raise RuntimeError(f"refusing unsafe cleanup target: {path}")
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
