"""Canonical writable layout for Javis continuity state."""

from __future__ import annotations

import hashlib
import os
import secrets
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


_REPARSE_POINT = 0x400
_ROOT_ID_DOMAIN = b"javis.root-id.v1\0"
_ROOT_SALT_BYTES = 32


def _absolute(value: Any, field_name: str) -> Path:
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
    return os.path.normpath(str(path)).replace("/", "\\").casefold().rstrip("\\")


def _within(path: Path, parent: Path) -> bool:
    candidate = _path_key(path)
    boundary = _path_key(parent)
    return candidate == boundary or candidate.startswith(boundary + "\\")


def _existing_chain(path: Path) -> tuple[Path, ...]:
    members: list[Path] = []
    current = path
    while True:
        if current.exists() or current.is_symlink():
            members.append(current)
        if current == current.parent:
            break
        current = current.parent
    return tuple(reversed(members))


def _is_reparse(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("data root ancestry cannot be inspected") from exc
    return path.is_symlink() or bool(
        getattr(stat_result, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _reject_reparse_chain(path: Path) -> None:
    if any(_is_reparse(member) for member in _existing_chain(path)):
        raise ValueError("data root must not traverse a symlink or reparse point")


@dataclass(frozen=True, slots=True)
class ContinuityLayout:
    """Typed paths below the one canonical ``JAVIS_DATA_ROOT``."""

    data_root: Path
    life: Path
    conversations: Path
    memory: Path
    relationships: Path
    environment: Path
    intents: Path
    actions: Path
    growth: Path
    body: Path
    config: Path
    logs: Path
    local_ai: Path
    continuity: Path
    manifests: Path
    migrations: Path
    lineage_ops: Path
    lineage_heads: Path
    sync_inbox: Path
    sync_outbox: Path
    sync_conflicts: Path
    keys: Path
    termination: Path
    native_journal_inbox: Path
    _forbidden_roots: tuple[Path, ...]

    @classmethod
    def from_roots(
        cls,
        data_root: str | Path,
        *,
        source_root: str | Path,
        runtime_root: str | Path | None = None,
    ) -> "ContinuityLayout":
        root = _absolute(data_root, "data_root")
        source = _absolute(source_root, "source_root")
        forbidden = [source]
        if runtime_root is not None:
            forbidden.append(_absolute(runtime_root, "runtime_root"))
        for boundary in forbidden:
            if _within(root, boundary) or _within(boundary, root):
                raise ValueError("data root must be separate from source and runtime roots")
        _reject_reparse_chain(root)
        if _path_key(root.resolve(strict=False)) != _path_key(root):
            raise ValueError("data root must be canonical and must not traverse reparse points")

        continuity = root / "continuity"
        lineage = continuity / "lineage"
        sync = continuity / "sync"
        return cls(
            data_root=root,
            life=root / "life",
            conversations=root / "conversations",
            memory=root / "memory",
            relationships=root / "relationships",
            environment=root / "environment",
            intents=root / "intents",
            actions=root / "actions",
            growth=root / "growth",
            body=root / "body",
            config=root / "config",
            logs=root / "logs",
            local_ai=root / "local-ai",
            continuity=continuity,
            manifests=continuity / "manifests",
            migrations=continuity / "migrations",
            lineage_ops=lineage / "ops",
            lineage_heads=lineage / "heads",
            sync_inbox=sync / "inbox",
            sync_outbox=sync / "outbox",
            sync_conflicts=sync / "conflicts",
            keys=continuity / "keys",
            termination=continuity / "termination",
            native_journal_inbox=continuity / "native-journal" / "inbox",
            _forbidden_roots=tuple(forbidden),
        )

    @classmethod
    def from_runtime(cls, runtime: Any) -> "ContinuityLayout":
        packaged = getattr(runtime, "runtime_root", None)
        return cls.from_roots(
            runtime.data_root,
            source_root=runtime.root,
            runtime_root=packaged,
        )

    def assert_safe_path(self, value: str | Path) -> Path:
        path = _absolute(value, "path")
        if not _within(path, self.data_root):
            raise ValueError("path must remain below the canonical data root")
        if any(_within(path, boundary) for boundary in self._forbidden_roots):
            raise ValueError("path must remain outside source and runtime roots")
        _reject_reparse_chain(path)
        if _path_key(path.resolve(strict=False)) != _path_key(path):
            raise ValueError("path must not traverse a symlink or reparse point")
        return path

    def ensure_directories(self, *, probe_writable: bool = True) -> "ContinuityLayout":
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.assert_safe_path(self.data_root)
        for item in fields(self):
            if item.name in {"data_root", "_forbidden_roots"}:
                continue
            path = self.assert_safe_path(getattr(self, item.name))
            path.mkdir(parents=True, exist_ok=True)
            self.assert_safe_path(path)
        if probe_writable:
            self._probe_writable()
        return self

    def root_id(self) -> str:
        """Return a salted identifier without exposing the absolute root."""

        self.ensure_directories()
        salt_path = self.assert_safe_path(self.keys / "root-id.salt")
        try:
            with salt_path.open("xb") as stream:
                salt = secrets.token_bytes(_ROOT_SALT_BYTES)
                stream.write(salt)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            salt = salt_path.read_bytes()
        if len(salt) != _ROOT_SALT_BYTES or salt_path.is_symlink():
            raise ValueError("continuity root salt is invalid")
        normalized = _path_key(self.data_root).encode("utf-8")
        return hashlib.sha256(_ROOT_ID_DOMAIN + normalized + b"\0" + salt).hexdigest()

    def _probe_writable(self) -> None:
        descriptor, name = tempfile.mkstemp(prefix=".javis-write-probe-", dir=self.data_root)
        try:
            os.write(descriptor, b"javis")
            os.fsync(descriptor)
        except OSError as exc:
            raise ValueError("data root must be writable") from exc
        finally:
            os.close(descriptor)
            Path(name).unlink(missing_ok=True)


__all__ = ["ContinuityLayout"]
