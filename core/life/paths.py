"""Filesystem boundaries for the Javis life kernel."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def _normalize_root(value: str | Path, *, field_name: str) -> Path:
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    try:
        path = Path(value).expanduser()
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if not path.is_absolute():
        raise ValueError(f"{field_name} must be absolute")
    try:
        return Path(os.path.abspath(path))
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc


def resolve_data_root(
    root: str | Path,
    *,
    explicit: str | Path | None = None,
    environ: Mapping[str, str] = os.environ,
) -> Path:
    """Resolve the one writable data root without creating or probing it."""

    code_root = _normalize_root(root, field_name="root")
    if explicit is not None:
        return _normalize_root(explicit, field_name="data_root")

    configured = environ.get("JAVIS_DATA_ROOT")
    if configured is not None and configured.strip():
        return _normalize_root(configured, field_name="JAVIS_DATA_ROOT")

    raise ValueError("JAVIS_DATA_ROOT is required when data_root is not explicit")


__all__ = ["resolve_data_root"]
