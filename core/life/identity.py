"""Versioned, model-independent identity constitution storage."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    IdentityConstitution,
    IdentitySummary,
    canonical_content_hash,
)


_VERSION_FILE = re.compile(r"([1-9]\d*)-([0-9a-f]{64})\.json")
_AUDIT_FILE = re.compile(r"([1-9]\d*)-([0-9a-f]{64})\.json")
_SEMANTIC_FIELDS = frozenset(
    {
        "name",
        "kind",
        "relationship_role",
        "persona_invariants",
        "values",
        "hard_boundaries",
    }
)
_RECOVERY_FIELDS = frozenset(
    {"schema_version", "identity_id", "version", "content_hash", "version_file"}
)
_AUDIT_FIELDS = frozenset(
    {
        "schema_version",
        "action",
        "identity_id",
        "version",
        "content_hash",
        "previous_version_hash",
        "approved_by",
        "approved_at",
        "changed_fields",
        "source_version",
    }
)

_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[Path, threading.RLock] = {}


class IdentityStoreError(RuntimeError):
    """Base class for identity persistence failures."""


class IdentityNotFound(IdentityStoreError):
    """Raised when no identity artifacts exist yet."""


class IdentityRecoveryRequired(IdentityStoreError):
    """Raised when identity state must not be changed automatically."""


def _store_lock(path: Path) -> threading.RLock:
    with _LOCKS_GUARD:
        lock = _STORE_LOCKS.get(path)
        if lock is None:
            lock = threading.RLock()
            _STORE_LOCKS[path] = lock
        return lock


def _timestamp_from_epoch(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("now must return a finite epoch number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("now must return a finite epoch number")
    try:
        return datetime.fromtimestamp(numeric, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError("now is outside the supported epoch range") from exc


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


class IdentityConstitutionStore:
    """Own the immutable Javis constitution history under one user data root."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        id_factory: Callable[[], str] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self.identity_root = self.data_root / "life" / "identity"
        self.current_path = self.identity_root / "current.json"
        self.versions_dir = self.identity_root / "versions"
        self.recovery_path = self.identity_root / "recovery.json"
        self.audit_dir = self.identity_root / "audit"
        self._instances_root = self.data_root / "life" / "instances"
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._now = now or time.time
        self._lock = _store_lock(self.identity_root)

    def version_path(self, constitution: IdentityConstitution) -> Path:
        return self.versions_dir / (
            f"{constitution.version}-{constitution.content_hash}.json"
        )

    def load_or_create(self) -> IdentityConstitution:
        with self._lock:
            try:
                return self.load()
            except IdentityNotFound:
                if self._has_instance_history():
                    raise IdentityRecoveryRequired(
                        "identity is missing while instance history exists"
                    )
                if self._has_identity_artifacts():
                    raise IdentityRecoveryRequired(
                        "current constitution is missing while identity history exists"
                    )
                constitution = IdentityConstitution.create_default(
                    identity_id=self._id_factory(),
                    now=self._now(),
                )
                self._persist_new_version(
                    constitution,
                    action="identity.created",
                    changed_fields=tuple(sorted(_SEMANTIC_FIELDS)),
                    source_version=None,
                )
                return constitution

    def load(self) -> IdentityConstitution:
        """Load the active constitution without mutating or repairing disk."""

        with self._lock:
            if not self.current_path.is_file():
                if self._has_identity_artifacts():
                    raise IdentityRecoveryRequired(
                        "current constitution is missing while identity history exists"
                    )
                raise IdentityNotFound("identity constitution does not exist")

            try:
                current = self._read_constitution(self.current_path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise IdentityRecoveryRequired(
                    f"current constitution is invalid: {exc}"
                ) from exc

            chain, history_invalid = self._verified_chain()
            if history_invalid or not chain:
                raise IdentityRecoveryRequired("version history is invalid")
            if chain[-1] != current:
                raise IdentityRecoveryRequired(
                    "version history does not match the current constitution"
                )
            self._validate_recovery_pointer(current)
            self._validate_audit_history(chain)
            return current

    def load_last_verified(self) -> IdentityConstitution:
        """Return the highest contiguous valid immutable version without writes."""

        with self._lock:
            chain, _ = self._verified_chain()
            if not chain:
                raise IdentityRecoveryRequired(
                    "version history has no verified constitution"
                )
            return chain[-1]

    def write_version(
        self,
        current: IdentityConstitution,
        *,
        changes: Mapping[str, Any],
        approved_by: str,
    ) -> IdentityConstitution:
        with self._lock:
            return self._write_version_locked(
                current,
                changes=changes,
                approved_by=approved_by,
                action="identity.versioned",
                source_version=current.version,
            )

    def rollback_to_previous(self, *, approved_by: str) -> IdentityConstitution:
        with self._lock:
            self._validate_approver(approved_by)
            current = self.load()
            chain, history_invalid = self._verified_chain()
            if history_invalid or not chain or chain[-1] != current:
                raise IdentityRecoveryRequired("version history is invalid")
            if len(chain) < 2:
                raise ValueError("identity rollback requires a previous version")
            previous = chain[-2]
            changes = {
                field: previous.to_dict()[field]
                for field in sorted(_SEMANTIC_FIELDS)
            }
            return self._write_version_locked(
                current,
                changes=changes,
                approved_by=approved_by,
                action="identity.rollback",
                source_version=previous.version,
            )

    def summary(self) -> IdentitySummary:
        current = self.load()
        return IdentitySummary(
            identity_id=current.identity_id,
            name=current.name,
            kind=current.kind,
            relationship_role=current.relationship_role,
            version=current.version,
            content_hash=current.content_hash,
        )

    def audit_records(self) -> list[dict[str, Any]]:
        with self._lock:
            records: list[dict[str, Any]] = []
            if not self.audit_dir.is_dir():
                return records
            for path in sorted(
                self.audit_dir.iterdir(),
                key=self._numbered_path_sort_key,
            ):
                if not path.is_file() or _AUDIT_FILE.fullmatch(path.name) is None:
                    raise IdentityRecoveryRequired("identity audit history is invalid")
                try:
                    record = self._read_json_object(path)
                    self._validate_audit_record(record, path)
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    raise IdentityRecoveryRequired(
                        f"identity audit history is invalid: {exc}"
                    ) from exc
                records.append(record)
            return records

    def _write_version_locked(
        self,
        current: IdentityConstitution,
        *,
        changes: Mapping[str, Any],
        approved_by: str,
        action: str,
        source_version: int | None,
    ) -> IdentityConstitution:
        self._validate_approver(approved_by)
        normalized_changes = self._validate_changes(changes)
        active = self.load()
        if (
            current.identity_id != active.identity_id
            or current.version != active.version
            or current.content_hash != active.content_hash
        ):
            raise IdentityRecoveryRequired(
                "stale constitution cannot extend the active version chain"
            )

        payload = active.to_dict()
        payload.update(normalized_changes)
        payload["version"] = active.version + 1
        payload["previous_version_hash"] = active.content_hash
        payload["approved_by"] = approved_by.strip()
        payload["approved_at"] = _timestamp_from_epoch(self._now())
        payload.pop("content_hash")
        payload["content_hash"] = canonical_content_hash(payload)
        next_version = IdentityConstitution.from_dict(payload)
        self._persist_new_version(
            next_version,
            action=action,
            changed_fields=tuple(sorted(normalized_changes)),
            source_version=source_version,
        )
        return next_version

    @staticmethod
    def _validate_approver(approved_by: Any) -> None:
        if type(approved_by) is not str or not approved_by.strip():
            raise ValueError("approved_by must be a non-empty explicit approver")

    @staticmethod
    def _validate_changes(changes: Any) -> dict[str, Any]:
        if not isinstance(changes, Mapping):
            raise ValueError("identity changes must be an object")
        normalized: dict[str, Any] = {}
        for field, value in changes.items():
            if type(field) is not str or field not in _SEMANTIC_FIELDS:
                raise ValueError(f"forbidden identity field: {field!r}")
            normalized[field] = value
        if not normalized:
            raise ValueError("identity changes must not be empty")
        return normalized

    def _persist_new_version(
        self,
        constitution: IdentityConstitution,
        *,
        action: str,
        changed_fields: tuple[str, ...],
        source_version: int | None,
    ) -> None:
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        version_path = self.version_path(constitution)
        audit_path = self.audit_dir / (
            f"{constitution.version}-{constitution.content_hash}.json"
        )
        pointer = {
            "schema_version": 1,
            "identity_id": constitution.identity_id,
            "version": constitution.version,
            "content_hash": constitution.content_hash,
            "version_file": version_path.name,
        }
        audit = {
            "schema_version": 1,
            "action": action,
            "identity_id": constitution.identity_id,
            "version": constitution.version,
            "content_hash": constitution.content_hash,
            "previous_version_hash": constitution.previous_version_hash,
            "approved_by": constitution.approved_by,
            "approved_at": constitution.approved_at,
            "changed_fields": list(changed_fields),
            "source_version": source_version,
        }

        self._atomic_write(
            version_path,
            _json_text(constitution.to_dict()),
            replace_existing=False,
        )
        self._atomic_write(
            audit_path,
            _json_text(audit),
            replace_existing=False,
        )
        self._atomic_write(self.recovery_path, _json_text(pointer))
        self._atomic_write(
            self.current_path,
            _json_text(constitution.to_dict()),
        )

    @staticmethod
    def _atomic_write(
        path: Path,
        text: str,
        *,
        replace_existing: bool = True,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not replace_existing and path.exists():
            raise IdentityRecoveryRequired(
                f"immutable identity artifact already exists: {path.name}"
            )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            if not replace_existing and path.exists():
                raise IdentityRecoveryRequired(
                    f"immutable identity artifact already exists: {path.name}"
                )
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _verified_chain(self) -> tuple[list[IdentityConstitution], bool]:
        if not self.versions_dir.is_dir():
            return [], self.versions_dir.exists()
        grouped: dict[int, list[IdentityConstitution]] = defaultdict(list)
        invalid = False
        file_count = 0
        for path in self.versions_dir.iterdir():
            if not path.is_file():
                invalid = True
                continue
            file_count += 1
            match = _VERSION_FILE.fullmatch(path.name)
            if match is None:
                invalid = True
                continue
            try:
                constitution = self._read_constitution(path)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                invalid = True
                continue
            version = int(match.group(1))
            content_hash = match.group(2)
            if (
                constitution.version != version
                or constitution.content_hash != content_hash
            ):
                invalid = True
                continue
            grouped[version].append(constitution)

        chain: list[IdentityConstitution] = []
        expected_version = 1
        expected_previous_hash: str | None = None
        identity_id: str | None = None
        while True:
            candidates = grouped.get(expected_version, [])
            if not candidates:
                break
            if len(candidates) != 1:
                invalid = True
                break
            candidate = candidates[0]
            if identity_id is None:
                identity_id = candidate.identity_id
            if (
                candidate.identity_id != identity_id
                or candidate.previous_version_hash != expected_previous_hash
            ):
                invalid = True
                break
            chain.append(candidate)
            expected_previous_hash = candidate.content_hash
            expected_version += 1

        if len(chain) != file_count or any(
            version >= expected_version for version in grouped
        ):
            invalid = True
        return chain, invalid

    def _validate_recovery_pointer(
        self,
        current: IdentityConstitution,
    ) -> None:
        try:
            pointer = self._read_json_object(self.recovery_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise IdentityRecoveryRequired(
                f"identity recovery pointer is invalid: {exc}"
            ) from exc
        if set(pointer) != _RECOVERY_FIELDS:
            raise IdentityRecoveryRequired("identity recovery pointer has invalid fields")
        if (
            type(pointer["schema_version"]) is not int
            or pointer["schema_version"] != 1
            or type(pointer["identity_id"]) is not str
            or not pointer["identity_id"]
            or type(pointer["version"]) is not int
            or pointer["version"] < 1
            or type(pointer["content_hash"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", pointer["content_hash"]) is None
            or type(pointer["version_file"]) is not str
            or not pointer["version_file"]
        ):
            raise IdentityRecoveryRequired("identity recovery pointer has invalid values")
        expected = {
            "schema_version": 1,
            "identity_id": current.identity_id,
            "version": current.version,
            "content_hash": current.content_hash,
            "version_file": self.version_path(current).name,
        }
        if pointer != expected:
            raise IdentityRecoveryRequired(
                "identity recovery pointer does not match the current constitution"
            )

    def _validate_audit_history(
        self,
        chain: list[IdentityConstitution],
    ) -> None:
        records = self.audit_records()
        if len(records) != len(chain):
            raise IdentityRecoveryRequired("identity audit history is incomplete")
        for record, constitution in zip(records, chain, strict=True):
            if (
                record["identity_id"] != constitution.identity_id
                or record["version"] != constitution.version
                or record["content_hash"] != constitution.content_hash
                or record["previous_version_hash"]
                != constitution.previous_version_hash
                or record["approved_by"] != constitution.approved_by
                or record["approved_at"] != constitution.approved_at
            ):
                raise IdentityRecoveryRequired(
                    "identity audit history does not match version history"
                )

    @staticmethod
    def _validate_audit_record(record: Mapping[str, Any], path: Path) -> None:
        if set(record) != _AUDIT_FIELDS:
            raise ValueError(f"{path.name} has invalid audit fields")
        match = _AUDIT_FILE.fullmatch(path.name)
        if match is None:
            raise ValueError(f"{path.name} has an invalid audit file name")
        if type(record["schema_version"]) is not int or record["schema_version"] != 1:
            raise ValueError(f"{path.name} has an unknown schema_version")
        if record["action"] not in {
            "identity.created",
            "identity.versioned",
            "identity.rollback",
        }:
            raise ValueError(f"{path.name} has an unknown identity action")
        if type(record["version"]) is not int or record["version"] < 1:
            raise ValueError(f"{path.name} has an invalid version")
        if record["version"] != int(match.group(1)):
            raise ValueError(f"{path.name} version does not match its file name")
        if record["content_hash"] != match.group(2):
            raise ValueError(f"{path.name} hash does not match its file name")
        if (
            type(record["identity_id"]) is not str
            or not record["identity_id"]
            or type(record["content_hash"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", record["content_hash"]) is None
            or (
                record["previous_version_hash"] is not None
                and (
                    type(record["previous_version_hash"]) is not str
                    or re.fullmatch(
                        r"[0-9a-f]{64}", record["previous_version_hash"]
                    )
                    is None
                )
            )
            or type(record["approved_by"]) is not str
            or not record["approved_by"].strip()
            or type(record["approved_at"]) is not str
        ):
            raise ValueError(f"{path.name} has invalid identity metadata")
        if type(record["changed_fields"]) is not list or any(
            type(field) is not str or field not in _SEMANTIC_FIELDS
            for field in record["changed_fields"]
        ):
            raise ValueError(f"{path.name} has invalid changed_fields")
        if len(set(record["changed_fields"])) != len(record["changed_fields"]):
            raise ValueError(f"{path.name} has duplicate changed_fields")
        source_version = record["source_version"]
        if record["action"] == "identity.created":
            if source_version is not None or record["version"] != 1:
                raise ValueError(f"{path.name} has an invalid creation source")
        elif (
            type(source_version) is not int
            or source_version < 1
            or source_version >= record["version"]
        ):
            raise ValueError(f"{path.name} has an invalid source_version")

    @staticmethod
    def _read_constitution(path: Path) -> IdentityConstitution:
        return IdentityConstitution.from_dict(
            IdentityConstitutionStore._read_json_object(path)
        )

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if type(payload) is not dict:
            raise ValueError(f"{path.name} must contain a JSON object")
        return payload

    @staticmethod
    def _numbered_path_sort_key(path: Path) -> tuple[int, str]:
        match = re.match(r"([1-9]\d*)-", path.name)
        return (int(match.group(1)) if match else 2**63 - 1, path.name)

    def _has_identity_artifacts(self) -> bool:
        return self.identity_root.is_dir() and any(
            path.is_file() for path in self.identity_root.rglob("*")
        )

    def _has_instance_history(self) -> bool:
        return self._instances_root.is_dir() and any(
            path.is_file() for path in self._instances_root.rglob("*")
        )


__all__ = [
    "IdentityConstitutionStore",
    "IdentityNotFound",
    "IdentityRecoveryRequired",
    "IdentityStoreError",
]
