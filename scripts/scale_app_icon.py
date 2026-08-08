"""Scale the transparent app-icon artwork while preserving a 512px canvas."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


def scale_icon(path: Path, scale: float) -> tuple[int, int, int, int] | None:
    image = Image.open(path).convert("RGBA")
    if image.size != (512, 512):
        raise ValueError(f"expected a 512x512 source icon, got {image.size}")
    scaled_size = round(512 * scale)
    scaled = image.resize((scaled_size, scaled_size), Image.Resampling.LANCZOS)
    left = (scaled_size - 512) // 2
    result = scaled.crop((left, left, left + 512, left + 512))
    result.save(path, format="PNG", optimize=True)
    return result.getchannel("A").getbbox()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("icon", type=Path)
    parser.add_argument("--scale", type=float, default=1.08)
    args = parser.parse_args()
    if not 1.0 < args.scale <= 1.2:
        parser.error("--scale must be greater than 1.0 and no more than 1.2")
    print(scale_icon(args.icon.resolve(), args.scale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
