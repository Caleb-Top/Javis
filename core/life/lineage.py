"""Instance lineage and clean-shutdown continuity storage."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    ContinuityCheckpoint,
    InstanceRecord,
    StartupAssessment,
    canonical_content_hash,
)


_ACTIVE_FIELDS = frozenset(
    {
        "schema_version",
        "identity_id",
        "lineage_id",
        "instance_id",
        "record_file",
        "record_content_hash",
    }
)
_HASH = re.compile(r"[0-9a-f]{64}")
_RECORD_FILE = re.compile(r"[0-9a-f]{64}\.json")

_LOCKS_GUARD = threading.Lock()
_STORE_LOCKS: dict[Path, threading.RLock] = {}


class LineageStoreError(RuntimeError):
    """Base class for instance lineage persistence failures."""


class LineageNotFound(LineageStoreError):
    """Raised when no instance lineage exists yet."""


class LineageRecoveryRequired(LineageStoreError):
    """Raised when lineage state cannot be changed safely."""


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


def _fingerprint_hash(value: Any) -> str:
    if type(value) is not str or not value:
        raise ValueError("environment_fingerprint must be a non-empty string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("environment_fingerprint must be valid UTF-8") from exc
    return hashlib.sha256(encoded).hexdigest()


def _safe_file_key(value: Any, *, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8") from exc
    return hashlib.sha256(encoded).hexdigest()


class InstanceLineageStore:
    """Maintain one active, linear instance lineage for an identity."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        instance_id_factory: Callable[[], str] | None = None,
        lineage_id_factory: Callable[[], str] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        self.instances_root = self.data_root / "life" / "instances"
        self.records_dir = self.instances_root / "records"
        self.checkpoints_dir = self.instances_root / "checkpoints"
        self.active_path = self.instances_root / "active.json"
        self._instance_id_factory = instance_id_factory or (lambda: str(uuid.uuid4()))
        self._lineage_id_factory = lineage_id_factory or (lambda: str(uuid.uuid4()))
        self._now = now or time.time
        self._lock = _store_lock(self.instances_root)
        self._assessed_previous_boot: dict[str, str | None] = {}

    def record_path(self, instance_id: str) -> Path:
        key = _safe_file_key(instance_id, field_name="instance_id")
        return self.records_dir / f"{key}.json"

    def checkpoint_path(self, instance_id: str) -> Path:
        key = _safe_file_key(instance_id, field_name="instance_id")
        return self.checkpoints_dir / f"{key}.json"

    def load_or_create(
        self,
        identity_id: str,
        environment_fingerprint: str,
    ) -> InstanceRecord:
        fingerprint_hash = _fingerprint_hash(environment_fingerprint)
        with self._lock:
            try:
                active = self.load_active()
            except LineageNotFound:
                if self._has_artifacts():
                    raise LineageRecoveryRequired(
                        "active instance pointer is missing while instance history exists"
                    )
                timestamp = _timestamp_from_epoch(self._now())
                instance = InstanceRecord(
                    schema_version=1,
                    identity_id=identity_id,
                    lineage_id=self._lineage_id_factory(),
                    instance_id=self._instance_id_factory(),
                    parent_instance_id=None,
                    generation=0,
                    environment_fingerprint_hash=fingerprint_hash,
                    created_at=timestamp,
                    last_started_at=None,
                    last_clean_shutdown_at=None,
                    fork_pending_review=False,
                )
                self._persist_new_instance(instance)
                return instance

            if active.identity_id != identity_id:
                raise LineageRecoveryRequired(
                    "identity_id does not match the active instance lineage"
                )
            if active.environment_fingerprint_hash == fingerprint_hash:
                return active
            return self._fork_for_environment_locked(active, fingerprint_hash)

    def fork_for_environment(
        self,
        identity_id: str,
        environment_fingerprint: str,
    ) -> InstanceRecord:
        fingerprint_hash = _fingerprint_hash(environment_fingerprint)
        with self._lock:
            active = self.load_active()
            if active.identity_id != identity_id:
                raise LineageRecoveryRequired(
                    "identity_id does not match the active instance lineage"
                )
            if active.environment_fingerprint_hash == fingerprint_hash:
                raise ValueError(
                    "fork_for_environment requires a different environment fingerprint"
                )
            return self._fork_for_environment_locked(active, fingerprint_hash)

    def load_active(self) -> InstanceRecord:
        """Load and validate the active pointer and complete linear record set."""

        with self._lock:
            if not self.active_path.is_file():
                if self._has_artifacts():
                    raise LineageRecoveryRequired(
                        "active instance pointer is missing while instance history exists"
                    )
                raise LineageNotFound("instance lineage does not exist")
            try:
                pointer = self._read_json_object(self.active_path)
                self._validate_active_pointer_values(pointer)
                records = self._load_record_set()
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise LineageRecoveryRequired(
                    f"active instance pointer or record set is invalid: {exc}"
                ) from exc

            active = records.get(pointer["instance_id"])
            if active is None:
                raise LineageRecoveryRequired(
                    "active instance pointer references a missing record"
                )
            if pointer != self._active_pointer(active):
                raise LineageRecoveryRequired(
                    "active instance pointer does not match its record"
                )
            self._validate_linear_record_set(records, active)
            self._validate_checkpoint_set(records)
            return active

    def assess_previous_run(self, instance_id: str) -> StartupAssessment:
        with self._lock:
            active = self.load_active()
            if active.instance_id != instance_id:
                raise LineageRecoveryRequired(
                    "only the active instance can assess continuity"
                )
            checkpoint_path = self.checkpoint_path(instance_id)
            if not checkpoint_path.is_file():
                assessment = StartupAssessment(
                    schema_version=1,
                    instance_id=instance_id,
                    previous_boot_id=None,
                    unclean_shutdown=False,
                    temporary_authority_valid=False,
                    last_event_cursor=0,
                    active_request_id=None,
                    reason_code="first_start",
                )
                self._assessed_previous_boot[instance_id] = None
                return assessment

            checkpoint = self._load_checkpoint(instance_id)
            unclean = checkpoint.clean_shutdown_at is None
            assessment = StartupAssessment(
                schema_version=1,
                instance_id=instance_id,
                previous_boot_id=checkpoint.boot_id,
                unclean_shutdown=unclean,
                temporary_authority_valid=False,
                last_event_cursor=checkpoint.last_event_cursor,
                active_request_id=checkpoint.active_request_id,
                reason_code=("unclean_shutdown" if unclean else "clean_shutdown"),
            )
            self._assessed_previous_boot[instance_id] = checkpoint.boot_id
            return assessment

    def mark_started(
        self,
        instance_id: str,
        *,
        boot_id: str,
    ) -> ContinuityCheckpoint:
        with self._lock:
            active = self.load_active()
            if active.instance_id != instance_id:
                raise LineageRecoveryRequired(
                    "only the active instance can be marked started"
                )
            if instance_id not in self._assessed_previous_boot:
                raise LineageRecoveryRequired(
                    "assess_previous_run must run before mark_started"
                )
            previous_boot_id = self._assessed_previous_boot[instance_id]
            if boot_id == previous_boot_id:
                raise LineageRecoveryRequired(
                    "boot_id must not reuse the previous boot identifier"
                )
            timestamp = _timestamp_from_epoch(self._now())
            previous_cursor = 0
            checkpoint_path = self.checkpoint_path(instance_id)
            if checkpoint_path.is_file():
                previous_checkpoint = self._load_checkpoint(instance_id)
                if previous_checkpoint.boot_id != previous_boot_id:
                    raise LineageRecoveryRequired(
                        "previous-run assessment is stale for this checkpoint"
                    )
                previous_cursor = previous_checkpoint.last_event_cursor
            elif previous_boot_id is not None:
                raise LineageRecoveryRequired(
                    "previous-run assessment is stale because its checkpoint is missing"
                )
            checkpoint = self._make_checkpoint(
                instance_id=instance_id,
                boot_id=boot_id,
                started_at=timestamp,
                clean_shutdown_at=None,
                last_event_cursor=previous_cursor,
                active_request_id=None,
                temporary_authority_valid=True,
            )
            updated = InstanceRecord.from_dict(
                active.to_dict() | {"last_started_at": timestamp}
            )
            self._persist_active_record_and_checkpoint(updated, checkpoint)
            self._assessed_previous_boot.pop(instance_id, None)
            return checkpoint

    def mark_clean_shutdown(
        self,
        instance_id: str,
        *,
        boot_id: str,
        last_event_cursor: int,
        active_request_id: str | None,
    ) -> ContinuityCheckpoint:
        if type(last_event_cursor) is not int or last_event_cursor < 0:
            raise ValueError("last_event_cursor must be a non-negative integer")
        with self._lock:
            active = self.load_active()
            if active.instance_id != instance_id:
                raise LineageRecoveryRequired(
                    "only the active instance can be marked cleanly shut down"
                )
            started = self._load_checkpoint(instance_id)
            if started.boot_id != boot_id:
                raise LineageRecoveryRequired(
                    "boot_id does not match the active continuity checkpoint"
                )
            if started.clean_shutdown_at is not None:
                raise LineageRecoveryRequired(
                    "continuity checkpoint is already cleanly shut down"
                )
            timestamp = _timestamp_from_epoch(self._now())
            checkpoint = self._make_checkpoint(
                instance_id=instance_id,
                boot_id=boot_id,
                started_at=started.started_at,
                clean_shutdown_at=timestamp,
                last_event_cursor=last_event_cursor,
                active_request_id=active_request_id,
                temporary_authority_valid=False,
            )
            updated = InstanceRecord.from_dict(
                active.to_dict() | {"last_clean_shutdown_at": timestamp}
            )
            self._persist_active_record_and_checkpoint(updated, checkpoint)
            return checkpoint

    def _fork_for_environment_locked(
        self,
        active: InstanceRecord,
        fingerprint_hash: str,
    ) -> InstanceRecord:
        fork = InstanceRecord(
            schema_version=1,
            identity_id=active.identity_id,
            lineage_id=active.lineage_id,
            instance_id=self._instance_id_factory(),
            parent_instance_id=active.instance_id,
            generation=active.generation + 1,
            environment_fingerprint_hash=fingerprint_hash,
            created_at=_timestamp_from_epoch(self._now()),
            last_started_at=None,
            last_clean_shutdown_at=None,
            fork_pending_review=True,
        )
        self._persist_new_instance(fork)
        return fork

    def _persist_new_instance(self, instance: InstanceRecord) -> None:
        record_path = self.record_path(instance.instance_id)
        self._atomic_write(
            record_path,
            _json_text(instance.to_dict()),
            replace_existing=False,
        )
        self._atomic_write(self.active_path, _json_text(self._active_pointer(instance)))

    def _persist_active_record_and_checkpoint(
        self,
        instance: InstanceRecord,
        checkpoint: ContinuityCheckpoint,
    ) -> None:
        self._atomic_write(
            self.checkpoint_path(instance.instance_id),
            _json_text(checkpoint.to_dict()),
        )
        self._atomic_write(
            self.record_path(instance.instance_id),
            _json_text(instance.to_dict()),
        )
        self._atomic_write(
            self.active_path,
            _json_text(self._active_pointer(instance)),
        )

    def _load_record_set(self) -> dict[str, InstanceRecord]:
        if not self.records_dir.is_dir():
            raise ValueError("instance records directory is missing")
        records: dict[str, InstanceRecord] = {}
        for path in self.records_dir.iterdir():
            if not path.is_file() or _RECORD_FILE.fullmatch(path.name) is None:
                raise ValueError("instance record set contains an invalid path")
            record = InstanceRecord.from_dict(self._read_json_object(path))
            if path != self.record_path(record.instance_id):
                raise ValueError("instance record file name does not match its instance_id")
            if record.instance_id in records:
                raise ValueError("instance record set contains duplicate instance_id values")
            records[record.instance_id] = record
        if not records:
            raise ValueError("instance record set is empty")
        return records

    @staticmethod
    def _validate_linear_record_set(
        records: Mapping[str, InstanceRecord],
        active: InstanceRecord,
    ) -> None:
        identity_ids = {record.identity_id for record in records.values()}
        lineage_ids = {record.lineage_id for record in records.values()}
        if len(identity_ids) != 1 or len(lineage_ids) != 1:
            raise LineageRecoveryRequired(
                "instance record set crosses identity or lineage boundaries"
            )
        roots = [
            record
            for record in records.values()
            if record.parent_instance_id is None
        ]
        if len(roots) != 1 or roots[0].generation != 0:
            raise LineageRecoveryRequired(
                "instance record set must contain exactly one generation-zero root"
            )
        children: dict[str, list[InstanceRecord]] = {
            instance_id: [] for instance_id in records
        }
        for record in records.values():
            if record.parent_instance_id is None:
                continue
            parent = records.get(record.parent_instance_id)
            if parent is None or record.generation != parent.generation + 1:
                raise LineageRecoveryRequired(
                    "instance record set contains a broken parent chain"
                )
            children[parent.instance_id].append(record)
        if any(len(items) > 1 for items in children.values()):
            raise LineageRecoveryRequired(
                "instance record set contains multiple fork branches"
            )
        leaves = [record for record in records.values() if not children[record.instance_id]]
        if len(leaves) != 1 or leaves[0].instance_id != active.instance_id:
            raise LineageRecoveryRequired(
                "active instance pointer is not the unique lineage tip"
            )
        visited: set[str] = set()
        cursor = roots[0]
        while cursor.instance_id not in visited:
            visited.add(cursor.instance_id)
            next_records = children[cursor.instance_id]
            if not next_records:
                break
            cursor = next_records[0]
        if len(visited) != len(records):
            raise LineageRecoveryRequired(
                "instance record set contains an unreachable record"
            )

    def _load_checkpoint(self, instance_id: str) -> ContinuityCheckpoint:
        path = self.checkpoint_path(instance_id)
        try:
            checkpoint = ContinuityCheckpoint.from_dict(
                self._read_json_object(path)
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise LineageRecoveryRequired(
                f"continuity checkpoint is invalid: {exc}"
            ) from exc
        if checkpoint.instance_id != instance_id:
            raise LineageRecoveryRequired(
                "continuity checkpoint belongs to another instance"
            )
        return checkpoint

    def _validate_checkpoint_set(
        self,
        records: Mapping[str, InstanceRecord],
    ) -> None:
        checkpoints: dict[str, ContinuityCheckpoint] = {}
        if self.checkpoints_dir.exists() and not self.checkpoints_dir.is_dir():
            raise LineageRecoveryRequired(
                "continuity checkpoint location is not a directory"
            )
        if self.checkpoints_dir.is_dir():
            for path in self.checkpoints_dir.iterdir():
                if not path.is_file() or _RECORD_FILE.fullmatch(path.name) is None:
                    raise LineageRecoveryRequired(
                        "continuity checkpoint set contains an invalid path"
                    )
                try:
                    checkpoint = ContinuityCheckpoint.from_dict(
                        self._read_json_object(path)
                    )
                except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                    raise LineageRecoveryRequired(
                        f"continuity checkpoint set is invalid: {exc}"
                    ) from exc
                if checkpoint.instance_id not in records:
                    raise LineageRecoveryRequired(
                        "continuity checkpoint belongs to an unknown instance"
                    )
                if path != self.checkpoint_path(checkpoint.instance_id):
                    raise LineageRecoveryRequired(
                        "continuity checkpoint file name does not match its instance_id"
                    )
                if checkpoint.instance_id in checkpoints:
                    raise LineageRecoveryRequired(
                        "continuity checkpoint set contains duplicate instances"
                    )
                checkpoints[checkpoint.instance_id] = checkpoint

        for instance_id, record in records.items():
            checkpoint = checkpoints.get(instance_id)
            if record.last_started_at is None:
                if checkpoint is not None:
                    raise LineageRecoveryRequired(
                        "continuity checkpoint exists for an instance that never started"
                    )
                continue
            if checkpoint is None:
                raise LineageRecoveryRequired(
                    "started instance is missing its continuity checkpoint"
                )
            if checkpoint.started_at != record.last_started_at:
                raise LineageRecoveryRequired(
                    "continuity checkpoint does not match instance start metadata"
                )
            if (
                checkpoint.clean_shutdown_at is not None
                and checkpoint.clean_shutdown_at != record.last_clean_shutdown_at
            ):
                raise LineageRecoveryRequired(
                    "continuity checkpoint does not match clean shutdown metadata"
                )

    @staticmethod
    def _make_checkpoint(
        *,
        instance_id: str,
        boot_id: str,
        started_at: str,
        clean_shutdown_at: str | None,
        last_event_cursor: int,
        active_request_id: str | None,
        temporary_authority_valid: bool,
    ) -> ContinuityCheckpoint:
        payload = {
            "schema_version": 1,
            "instance_id": instance_id,
            "boot_id": boot_id,
            "started_at": started_at,
            "clean_shutdown_at": clean_shutdown_at,
            "last_event_cursor": last_event_cursor,
            "active_request_id": active_request_id,
            "temporary_authority_valid": temporary_authority_valid,
        }
        payload["content_hash"] = canonical_content_hash(payload)
        return ContinuityCheckpoint.from_dict(payload)

    def _active_pointer(self, instance: InstanceRecord) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "identity_id": instance.identity_id,
            "lineage_id": instance.lineage_id,
            "instance_id": instance.instance_id,
            "record_file": self.record_path(instance.instance_id).name,
            "record_content_hash": canonical_content_hash(instance.to_dict()),
        }

    @staticmethod
    def _validate_active_pointer_values(pointer: Mapping[str, Any]) -> None:
        if set(pointer) != _ACTIVE_FIELDS:
            raise ValueError("active pointer has invalid fields")
        if type(pointer["schema_version"]) is not int or pointer["schema_version"] != 1:
            raise ValueError("active pointer has an unknown schema_version")
        for field in ("identity_id", "lineage_id", "instance_id", "record_file"):
            if type(pointer[field]) is not str or not pointer[field]:
                raise ValueError(f"active pointer has an invalid {field}")
        if _RECORD_FILE.fullmatch(pointer["record_file"]) is None:
            raise ValueError("active pointer has an invalid record_file")
        record_hash = pointer["record_content_hash"]
        if type(record_hash) is not str or _HASH.fullmatch(record_hash) is None:
            raise ValueError("active pointer has an invalid record_content_hash")

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if type(payload) is not dict:
            raise ValueError(f"{path.name} must contain a JSON object")
        return payload

    @staticmethod
    def _atomic_write(
        path: Path,
        text: str,
        *,
        replace_existing: bool = True,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not replace_existing and path.exists():
            raise LineageRecoveryRequired(
                f"instance artifact already exists: {path.name}"
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
                raise LineageRecoveryRequired(
                    f"instance artifact already exists: {path.name}"
                )
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def _has_artifacts(self) -> bool:
        return self.instances_root.is_dir() and any(
            path.is_file() for path in self.instances_root.rglob("*")
        )


__all__ = [
    "InstanceLineageStore",
    "LineageNotFound",
    "LineageRecoveryRequired",
    "LineageStoreError",
]
