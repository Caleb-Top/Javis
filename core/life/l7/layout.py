"""Validated fixed filesystem layout for L7 mutable state."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


_REPARSE_POINT = 0x400


def _absolute_path(value: Any, field_name: str) -> Path:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"{field_name} must not be empty")
    try:
        path = Path(value).expanduser()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a path") from exc
    if not path.is_absolute():
        raise ValueError(f"{field_name} must be absolute")
    return Path(os.path.abspath(path))


def _path_key(path: Path) -> str:
    # Javis is a Windows product. Case-folding on every host keeps fixtures and
    # offline validation equivalent to Windows path identity.
    return os.path.normpath(str(path)).replace("/", "\\").casefold().rstrip("\\")


def _is_within(path: Path, boundary: Path) -> bool:
    child = _path_key(path)
    parent = _path_key(boundary)
    return child == parent or child.startswith(parent + "\\")


def _is_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("data_root ancestry cannot be inspected") from exc
    return path.is_symlink() or bool(getattr(stat_result, "st_file_attributes", 0) & _REPARSE_POINT)


def _existing_chain(path: Path) -> tuple[Path, ...]:
    chain: list[Path] = []
    current = path
    while True:
        if current.exists() or current.is_symlink():
            chain.append(current)
        if current == current.parent:
            break
        current = current.parent
    return tuple(reversed(chain))


def _reject_reparse_chain(path: Path) -> None:
    for member in _existing_chain(path):
        if _is_reparse(member):
            raise ValueError("data_root must not traverse a symlink or reparse point")


def _packaged_roots(runtime: Any) -> tuple[Path, ...]:
    roots: list[Path] = []
    for name in ("package_root", "runtime_root", "bundle_root"):
        value = getattr(runtime, name, None)
        if value is not None:
            roots.append(_absolute_path(value, f"runtime.{name}"))
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.append(_absolute_path(frozen_root, "sys._MEIPASS"))
    unique: dict[str, Path] = {_path_key(item): item for item in roots}
    return tuple(unique.values())


@dataclass(frozen=True, slots=True)
class DataRootLayout:
    data_root: Path
    growth: Path
    policies: Path
    runs: Path
    candidates: Path
    artifacts_sha256: Path
    deployments: Path
    receipts: Path
    sandbox: Path
    quarantine: Path
    body: Path
    body_preferences: Path
    body_profiles: Path
    body_diagnostics: Path
    _forbidden_roots: tuple[Path, ...]

    @classmethod
    def from_runtime(
        cls,
        runtime: Any,
        data_root: str | Path | None = None,
    ) -> "DataRootLayout":
        runtime_data_root = _absolute_path(getattr(runtime, "data_root", None), "runtime.data_root")
        requested_root = runtime_data_root if data_root is None else _absolute_path(data_root, "data_root")
        if _path_key(requested_root) != _path_key(runtime_data_root):
            raise ValueError("data_root must match runtime.data_root")

        source_root = _absolute_path(getattr(runtime, "root", None), "runtime.root")
        forbidden = (source_root, *_packaged_roots(runtime))
        for boundary in forbidden:
            if _is_within(requested_root, boundary):
                raise ValueError("data_root must be outside source and packaged runtime roots")

        _reject_reparse_chain(requested_root)
        resolved = requested_root.resolve(strict=False)
        if _path_key(resolved) != _path_key(requested_root):
            raise ValueError("data_root must not traverse a symlink or reparse point")

        growth = requested_root / "growth"
        body = requested_root / "body"
        return cls(
            data_root=requested_root,
            growth=growth,
            policies=growth / "policies",
            runs=growth / "runs",
            candidates=growth / "candidates",
            artifacts_sha256=growth / "artifacts" / "sha256",
            deployments=growth / "deployments",
            receipts=growth / "receipts",
            sandbox=growth / "sandbox",
            quarantine=growth / "quarantine",
            body=body,
            body_preferences=body / "preferences",
            body_profiles=body / "profiles",
            body_diagnostics=body / "diagnostics",
            _forbidden_roots=tuple(forbidden),
        )

    def assert_safe_path(self, path: str | Path) -> Path:
        candidate = _absolute_path(path, "path")
        if not _is_within(candidate, self.data_root):
            raise ValueError("path must remain within runtime.data_root")
        for boundary in self._forbidden_roots:
            if _is_within(candidate, boundary):
                raise ValueError("path must be outside source and packaged runtime roots")
        _reject_reparse_chain(candidate)
        resolved = candidate.resolve(strict=False)
        if _path_key(resolved) != _path_key(candidate):
            raise ValueError("path must not traverse a symlink or reparse point")
        return candidate

    def ensure_directories(self) -> "DataRootLayout":
        self.assert_safe_path(self.data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.assert_safe_path(self.data_root)
        for field in fields(self):
            if field.name in {"data_root", "_forbidden_roots"}:
                continue
            path = self.assert_safe_path(getattr(self, field.name))
            path.mkdir(parents=True, exist_ok=True)
            self.assert_safe_path(path)
        return self


__all__ = ["DataRootLayout"]
