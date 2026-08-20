"""Append-only SQLite persistence for governed L7 sleep runs.

The store accepts only validated domain contracts and bounded checkpoint
references.  Immutable events are hash chained per run; the mutable projection
can therefore be discarded and independently verified or rebuilt.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .contracts import (
    SleepJobReceiptV1,
    SleepRunState,
    SleepRunTransitionV1,
    SleepRunV1,
    canonical_content_hash,
)
from .layout import DataRootLayout


STORE_SCHEMA_VERSION = 1
DATABASE_RELATIVE_PATH = Path("growth") / "runs" / "runs.sqlite3"
GENESIS_HASH = "0" * 64
MAX_QUERY_LIMIT = 256
_HASH = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_UTC_MILLISECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
_TERMINAL_STATES = frozenset(
    {
        SleepRunState.SKIPPED,
        SleepRunState.CANCELLED,
        SleepRunState.COMPLETED,
        SleepRunState.FAILED,
        SleepRunState.TIMED_OUT,
    }
)
_RECOVERABLE_STATES = frozenset(
    {
        SleepRunState.RUNNING,
        SleepRunState.CHECKPOINTING,
        SleepRunState.COMMITTING,
    }
)
_CHAIN_DOMAIN = b"javis.l7.run-chain.v1\x00"


class RunStoreError(RuntimeError):
    """Base error for bounded run persistence."""


class RunStoreClosedError(RunStoreError):
    pass


class RunStoreConflictError(RunStoreError):
    pass


class RunStoreCASMismatchError(RunStoreConflictError):
    pass


class RunStoreClaimError(RunStoreConflictError):
    pass


class RunStoreIntegrityError(RunStoreError):
    pass


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    """Current projection paired with its immutable scheduling header."""

    header: SleepRunV1
    state: SleepRunState
    revision: int
    updated_at_utc: str
    ended_at_utc: str | None
    last_sequence: int
    last_chain_hash: str

    @property
    def run_id(self) -> str:
        return self.header.run_id

    @property
    def identity_id(self) -> str:
        return self.header.identity_id

    @property
    def instance_id(self) -> str:
        return self.header.instance_id

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class JobCheckpointReference:
    run_id: str
    job_id: str
    checkpoint_id: str
    expected_revision: int
    created_at_utc: str
    content_hash: str
    idempotency_key: str
    sequence: int
    chain_hash: str


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    ok: bool
    schema_version: int
    runs_scanned: int
    events_scanned: int
    errors: tuple[str, ...]


_SCHEMA = (
    """
    CREATE TABLE store_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE run_headers (
        run_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL,
        identity_id TEXT NOT NULL,
        instance_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        UNIQUE (run_id, idempotency_key)
    )
    """,
    """
    CREATE TABLE run_projection (
        run_id TEXT PRIMARY KEY REFERENCES run_headers(run_id),
        identity_id TEXT NOT NULL,
        instance_id TEXT NOT NULL,
        state TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        updated_at_utc TEXT NOT NULL,
        ended_at_utc TEXT,
        last_sequence INTEGER NOT NULL CHECK (last_sequence > 0),
        last_chain_hash TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX one_active_run_per_instance
    ON run_projection(identity_id, instance_id)
    WHERE state NOT IN ('skipped', 'cancelled', 'completed', 'failed', 'timed_out')
    """,
    """
    CREATE TABLE run_chain (
        run_id TEXT NOT NULL REFERENCES run_headers(run_id),
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        event_kind TEXT NOT NULL CHECK (
            event_kind IN ('run_header', 'transition', 'job_checkpoint', 'job_receipt')
        ),
        event_id TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        previous_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence),
        UNIQUE (run_id, event_kind, event_id),
        UNIQUE (run_id, chain_hash)
    )
    """,
    """
    CREATE TABLE run_transitions (
        transition_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES run_headers(run_id),
        idempotency_key TEXT NOT NULL,
        expected_revision INTEGER NOT NULL,
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        previous_chain_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        UNIQUE (run_id, idempotency_key),
        UNIQUE (run_id, sequence),
        FOREIGN KEY (run_id, sequence) REFERENCES run_chain(run_id, sequence)
    )
    """,
    """
    CREATE TABLE job_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES run_headers(run_id),
        job_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        expected_revision INTEGER NOT NULL,
        created_at_utc TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        previous_chain_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        UNIQUE (run_id, idempotency_key),
        UNIQUE (run_id, sequence),
        FOREIGN KEY (run_id, sequence) REFERENCES run_chain(run_id, sequence)
    )
    """,
    """
    CREATE TABLE receipt_references (
        receipt_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES run_headers(run_id),
        job_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        expected_revision INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        previous_chain_hash TEXT NOT NULL,
        chain_hash TEXT NOT NULL,
        UNIQUE (run_id, idempotency_key),
        UNIQUE (run_id, sequence),
        FOREIGN KEY (run_id, sequence) REFERENCES run_chain(run_id, sequence)
    )
    """,
    "CREATE INDEX run_projection_state ON run_projection(state, updated_at_utc)",
    "CREATE INDEX run_transition_order ON run_transitions(run_id, sequence)",
    "CREATE INDEX run_checkpoint_order ON job_checkpoints(run_id, sequence)",
    "CREATE INDEX run_receipt_order ON receipt_references(run_id, sequence)",
)

_APPEND_ONLY_TABLES = (
    "run_headers",
    "run_chain",
    "run_transitions",
    "job_checkpoints",
    "receipt_references",
)


def _json_text(contract: SleepRunV1 | SleepRunTransitionV1 | SleepJobReceiptV1) -> str:
    return contract.canonical_json_bytes().decode("utf-8")


def _require_id(value: Any, field_name: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a bounded opaque ID")
    return value


def _require_hash(value: Any, field_name: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256 hex")
    return value


def _require_revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("expected_revision must be a positive integer")
    return value


def _require_timestamp(value: Any) -> str:
    if type(value) is not str or _UTC_MILLISECONDS.fullmatch(value) is None:
        raise ValueError("created_at_utc must use RFC3339 UTC milliseconds")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError("created_at_utc must be a valid UTC timestamp") from exc
    return value


def _require_limit(limit: Any) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_QUERY_LIMIT:
        raise ValueError(f"limit must be in [1, {MAX_QUERY_LIMIT}]")
    return limit


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _chain_hash(
    previous_hash: str,
    event_kind: str,
    event_id: str,
    payload_hash: str,
) -> str:
    _require_hash(previous_hash, "previous_hash")
    _require_hash(payload_hash, "payload_hash")
    material = b"\x00".join(
        (
            previous_hash.encode("ascii"),
            event_kind.encode("ascii"),
            event_id.encode("utf-8"),
            payload_hash.encode("ascii"),
        )
    )
    return hashlib.sha256(_CHAIN_DOMAIN + material).hexdigest()


def _checkpoint_payload_hash(values: dict[str, Any]) -> str:
    payload = json.dumps(
        values,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class RunStore:
    """Fixed-layout append-only store with CAS projections and no SQL escape hatch."""

    def __init__(
        self,
        data_root: DataRootLayout | str | Path,
        *,
        recover_on_open: bool = True,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 60_000:
            raise ValueError("busy_timeout_ms must be in [1, 60000]")
        if isinstance(data_root, DataRootLayout):
            layout = data_root.ensure_directories()
            runs_dir = layout.assert_safe_path(layout.runs)
            self.data_root = layout.data_root
        else:
            root = Path(data_root).expanduser().resolve()
            if root.exists() and not root.is_dir():
                raise ValueError("data_root must be a directory")
            self.data_root = root
            runs_dir = root / "growth" / "runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
        self.path = runs_dir / "runs.sqlite3"
        self._busy_timeout_ms = busy_timeout_ms
        self._state_lock = threading.Lock()
        self._closed = False
        self._initialize()
        report = self.verify_integrity()
        if not report.ok:
            self._closed = True
            raise RunStoreIntegrityError("run store integrity verification failed")
        if recover_on_open:
            self.recover_interrupted()

    def connection_settings(self) -> dict[str, int | str]:
        with self._read_connection() as db:
            return {
                "journal_mode": str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "foreign_keys": int(db.execute("PRAGMA foreign_keys").fetchone()[0]),
                "query_only": int(db.execute("PRAGMA query_only").fetchone()[0]),
                "schema_version": int(db.execute("PRAGMA user_version").fetchone()[0]),
            }

    def create_run(self, run: SleepRunV1) -> RunSnapshot:
        if not isinstance(run, SleepRunV1):
            raise TypeError("run must be SleepRunV1")
        if run.state is not SleepRunState.SCHEDULED:
            raise RunStoreConflictError("new runs must begin in scheduled state")
        payload_json = _json_text(run)
        try:
            with self._write_transaction() as db:
                existing = db.execute(
                    "SELECT payload_json FROM run_headers WHERE run_id = ?",
                    (run.run_id,),
                ).fetchone()
                if existing is not None:
                    if existing["payload_json"] != payload_json:
                        raise RunStoreConflictError("run ID is already bound to different content")
                    return self._snapshot_db(db, run.run_id)
                sequence = 1
                chain_hash = _chain_hash(
                    GENESIS_HASH, "run_header", run.run_id, run.content_hash
                )
                db.execute(
                    "INSERT INTO run_headers VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run.run_id,
                        run.idempotency_key,
                        run.identity_id,
                        run.instance_id,
                        payload_json,
                        run.content_hash,
                        run.created_at_utc,
                    ),
                )
                db.execute(
                    "INSERT INTO run_chain VALUES (?, ?, 'run_header', ?, ?, ?, ?)",
                    (
                        run.run_id,
                        sequence,
                        run.run_id,
                        run.content_hash,
                        GENESIS_HASH,
                        chain_hash,
                    ),
                )
                db.execute(
                    "INSERT INTO run_projection VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run.run_id,
                        run.identity_id,
                        run.instance_id,
                        run.state.value,
                        run.revision,
                        run.updated_at_utc,
                        run.ended_at_utc,
                        sequence,
                        chain_hash,
                    ),
                )
                return self._snapshot_db(db, run.run_id)
        except sqlite3.IntegrityError as exc:
            raise RunStoreClaimError(
                "identity and instance already have a non-terminal run"
            ) from exc
        except sqlite3.Error as exc:
            raise RunStoreError("run persistence failed") from exc

    put_run = create_run

    def get_run(self, run_id: str) -> RunSnapshot | None:
        _require_id(run_id, "run_id")
        with self._read_connection() as db:
            row = db.execute(
                "SELECT 1 FROM run_headers WHERE run_id = ?", (run_id,)
            ).fetchone()
            return None if row is None else self._snapshot_db(db, run_id)

    def list_runs(
        self,
        *,
        identity_id: str | None = None,
        instance_id: str | None = None,
        state: SleepRunState | str | None = None,
        limit: int = 100,
    ) -> tuple[RunSnapshot, ...]:
        limit = _require_limit(limit)
        filters: list[str] = []
        values: list[Any] = []
        if identity_id is not None:
            filters.append("p.identity_id = ?")
            values.append(_require_id(identity_id, "identity_id"))
        if instance_id is not None:
            filters.append("p.instance_id = ?")
            values.append(_require_id(instance_id, "instance_id"))
        if state is not None:
            try:
                state_value = state.value if isinstance(state, SleepRunState) else SleepRunState(state).value
            except (TypeError, ValueError) as exc:
                raise ValueError("state must be a SleepRunState") from exc
            filters.append("p.state = ?")
            values.append(state_value)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        values.append(limit)
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT p.run_id FROM run_projection AS p"
                + where
                + " ORDER BY p.updated_at_utc DESC, p.run_id LIMIT ?",
                values,
            ).fetchall()
            return tuple(self._snapshot_db(db, row["run_id"]) for row in rows)

    def claim_active(
        self,
        identity_id: str,
        instance_id: str,
        *,
        run_id: str | None = None,
    ) -> RunSnapshot | None:
        """Atomically return the sole non-terminal run for an instance."""

        identity_id = _require_id(identity_id, "identity_id")
        instance_id = _require_id(instance_id, "instance_id")
        if run_id is not None:
            run_id = _require_id(run_id, "run_id")
        with self._write_transaction() as db:
            rows = db.execute(
                "SELECT run_id FROM run_projection WHERE identity_id = ? AND instance_id = ? "
                "AND state NOT IN ('skipped', 'cancelled', 'completed', 'failed', 'timed_out')",
                (identity_id, instance_id),
            ).fetchall()
            if len(rows) > 1:
                raise RunStoreIntegrityError("multiple active runs violate the instance claim")
            if not rows:
                return None
            claimed_id = rows[0]["run_id"]
            if run_id is not None and run_id != claimed_id:
                raise RunStoreClaimError("another run owns the active instance claim")
            return self._snapshot_db(db, claimed_id)

    def append_transition(self, transition: SleepRunTransitionV1) -> RunSnapshot:
        if not isinstance(transition, SleepRunTransitionV1):
            raise TypeError("transition must be SleepRunTransitionV1")
        try:
            with self._write_transaction() as db:
                return self._append_transition_db(db, transition)
        except sqlite3.Error as exc:
            raise RunStoreError("transition persistence failed") from exc

    record_transition = append_transition

    def record_job_checkpoint(
        self,
        run_id: str,
        job_id: str,
        checkpoint_id: str,
        *,
        expected_revision: int,
        created_at_utc: str,
        content_hash: str,
        idempotency_key: str,
    ) -> JobCheckpointReference:
        values = {
            "run_id": _require_id(run_id, "run_id"),
            "job_id": _require_id(job_id, "job_id"),
            "checkpoint_id": _require_id(checkpoint_id, "checkpoint_id"),
            "expected_revision": _require_revision(expected_revision),
            "created_at_utc": _require_timestamp(created_at_utc),
            "content_hash": _require_hash(content_hash, "content_hash"),
            "idempotency_key": _require_id(idempotency_key, "idempotency_key"),
        }
        try:
            with self._write_transaction() as db:
                existing = db.execute(
                    "SELECT * FROM job_checkpoints WHERE checkpoint_id = ? OR "
                    "(run_id = ? AND idempotency_key = ?)",
                    (checkpoint_id, run_id, idempotency_key),
                ).fetchall()
                if existing:
                    if len(existing) != 1 or not self._checkpoint_matches(existing[0], values):
                        raise RunStoreConflictError(
                            "checkpoint identity is already bound to different content"
                        )
                    return self._checkpoint_row(existing[0])
                projection = self._projection_for_write(db, run_id, expected_revision)
                self._require_nonterminal(projection["state"])
                checkpoint_payload_hash = _checkpoint_payload_hash(values)
                sequence, previous_hash, chain_hash = self._append_chain_db(
                    db,
                    projection,
                    "job_checkpoint",
                    checkpoint_id,
                    checkpoint_payload_hash,
                )
                db.execute(
                    "INSERT INTO job_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        checkpoint_id,
                        run_id,
                        job_id,
                        idempotency_key,
                        expected_revision,
                        created_at_utc,
                        content_hash,
                        sequence,
                        previous_hash,
                        chain_hash,
                    ),
                )
                self._advance_projection_db(
                    db,
                    projection,
                    state=projection["state"],
                    updated_at_utc=created_at_utc,
                    ended_at_utc=projection["ended_at_utc"],
                    sequence=sequence,
                    chain_hash=chain_hash,
                )
                row = db.execute(
                    "SELECT * FROM job_checkpoints WHERE checkpoint_id = ?",
                    (checkpoint_id,),
                ).fetchone()
                assert row is not None
                return self._checkpoint_row(row)
        except sqlite3.Error as exc:
            raise RunStoreError("checkpoint persistence failed") from exc

    append_job_checkpoint = record_job_checkpoint

    def record_job_receipt(
        self,
        receipt: SleepJobReceiptV1,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> RunSnapshot:
        if not isinstance(receipt, SleepJobReceiptV1):
            raise TypeError("receipt must be SleepJobReceiptV1")
        expected_revision = _require_revision(expected_revision)
        idempotency_key = _require_id(idempotency_key, "idempotency_key")
        payload_json = _json_text(receipt)
        try:
            with self._write_transaction() as db:
                existing = db.execute(
                    "SELECT * FROM receipt_references WHERE receipt_id = ? OR "
                    "(run_id = ? AND idempotency_key = ?)",
                    (receipt.receipt_id, receipt.run_id, idempotency_key),
                ).fetchall()
                if existing:
                    row = existing[0]
                    if (
                        len(existing) != 1
                        or row["payload_json"] != payload_json
                        or row["expected_revision"] != expected_revision
                        or row["idempotency_key"] != idempotency_key
                    ):
                        raise RunStoreConflictError(
                            "receipt identity is already bound to different content"
                        )
                    return self._snapshot_db(db, receipt.run_id)
                projection = self._projection_for_write(
                    db, receipt.run_id, expected_revision
                )
                self._require_nonterminal(projection["state"])
                if receipt.checkpoint_id is not None:
                    checkpoint = db.execute(
                        "SELECT run_id FROM job_checkpoints WHERE checkpoint_id = ?",
                        (receipt.checkpoint_id,),
                    ).fetchone()
                    if checkpoint is None or checkpoint["run_id"] != receipt.run_id:
                        raise RunStoreConflictError("receipt checkpoint is unavailable")
                sequence, previous_hash, chain_hash = self._append_chain_db(
                    db,
                    projection,
                    "job_receipt",
                    receipt.receipt_id,
                    receipt.content_hash,
                )
                db.execute(
                    "INSERT INTO receipt_references VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        receipt.receipt_id,
                        receipt.run_id,
                        receipt.job_id,
                        idempotency_key,
                        expected_revision,
                        payload_json,
                        receipt.content_hash,
                        sequence,
                        previous_hash,
                        chain_hash,
                    ),
                )
                self._advance_projection_db(
                    db,
                    projection,
                    state=projection["state"],
                    updated_at_utc=receipt.ended_at_utc,
                    ended_at_utc=projection["ended_at_utc"],
                    sequence=sequence,
                    chain_hash=chain_hash,
                )
                return self._snapshot_db(db, receipt.run_id)
        except sqlite3.Error as exc:
            raise RunStoreError("receipt persistence failed") from exc

    append_job_receipt = record_job_receipt

    def list_transitions(
        self, run_id: str, *, limit: int = 100
    ) -> tuple[SleepRunTransitionV1, ...]:
        run_id = _require_id(run_id, "run_id")
        limit = _require_limit(limit)
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM run_transitions WHERE run_id = ? "
                "ORDER BY sequence LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return tuple(SleepRunTransitionV1.from_dict(json.loads(row[0])) for row in rows)

    def list_job_checkpoints(
        self, run_id: str, *, limit: int = 100
    ) -> tuple[JobCheckpointReference, ...]:
        run_id = _require_id(run_id, "run_id")
        limit = _require_limit(limit)
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT * FROM job_checkpoints WHERE run_id = ? ORDER BY sequence LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return tuple(self._checkpoint_row(row) for row in rows)

    def list_job_receipts(
        self, run_id: str, *, limit: int = 100
    ) -> tuple[SleepJobReceiptV1, ...]:
        run_id = _require_id(run_id, "run_id")
        limit = _require_limit(limit)
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM receipt_references WHERE run_id = ? "
                "ORDER BY sequence LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return tuple(SleepJobReceiptV1.from_dict(json.loads(row[0])) for row in rows)

    def recover_interrupted(self) -> tuple[RunSnapshot, ...]:
        """Append recovery transitions after validating the existing event chain."""

        recovered: list[RunSnapshot] = []
        with self._write_transaction() as db:
            rows = db.execute(
                "SELECT * FROM run_projection WHERE state IN ('running', 'checkpointing', 'committing') "
                "ORDER BY run_id"
            ).fetchall()
            for row in rows:
                created_at = _utc_now()
                seed = hashlib.sha256(
                    f"{row['run_id']}:{row['revision']}:{row['last_chain_hash']}".encode("utf-8")
                ).hexdigest()
                payload = {
                    "schema_version": 1,
                    "transition_id": f"recovery-{uuid.uuid4().hex}",
                    "run_id": row["run_id"],
                    "from_state": row["state"],
                    "to_state": SleepRunState.INTERRUPTED.value,
                    "expected_revision": row["revision"],
                    "reason_code": "crash_recovery",
                    "checkpoint_id": None,
                    "receipt_ids": [],
                    "created_at_utc": created_at,
                    "idempotency_key": f"recovery-{seed}",
                }
                payload["content_hash"] = canonical_content_hash(payload)
                transition = SleepRunTransitionV1.from_dict(payload)
                recovered.append(self._append_transition_db(db, transition))
        return tuple(recovered)

    def verify_integrity(self) -> IntegrityReport:
        self._ensure_open()
        errors: list[str] = []
        runs_scanned = 0
        events_scanned = 0
        schema_version = 0
        try:
            with self._read_connection() as db:
                schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
                if schema_version != STORE_SCHEMA_VERSION:
                    errors.append("unsupported_schema_version")
                check = str(db.execute("PRAGMA quick_check").fetchone()[0])
                if check != "ok":
                    errors.append("sqlite_quick_check_failed")
                trigger_names = {
                    row[0]
                    for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                }
                for table in _APPEND_ONLY_TABLES:
                    if f"prevent_{table}_update" not in trigger_names:
                        errors.append(f"{table}:update_guard_missing")
                    if f"prevent_{table}_delete" not in trigger_names:
                        errors.append(f"{table}:delete_guard_missing")
                headers = db.execute(
                    "SELECT * FROM run_headers ORDER BY run_id"
                ).fetchall()
                for header_row in headers:
                    runs_scanned += 1
                    run_id = header_row["run_id"]
                    try:
                        header = SleepRunV1.from_dict(
                            json.loads(header_row["payload_json"])
                        )
                        if (
                            header.run_id != run_id
                            or header.content_hash != header_row["content_hash"]
                            or header.idempotency_key != header_row["idempotency_key"]
                            or header.identity_id != header_row["identity_id"]
                            or header.instance_id != header_row["instance_id"]
                            or header.state is not SleepRunState.SCHEDULED
                        ):
                            raise ValueError("header index mismatch")
                        state = header.state
                        revision = header.revision
                        updated_at = header.updated_at_utc
                        ended_at = header.ended_at_utc
                    except (TypeError, ValueError, json.JSONDecodeError):
                        errors.append(f"run:{run_id}:invalid_header")
                        continue
                    chain_rows = db.execute(
                        "SELECT * FROM run_chain WHERE run_id = ? ORDER BY sequence",
                        (run_id,),
                    ).fetchall()
                    expected_previous = GENESIS_HASH
                    expected_sequence = 1
                    for chain in chain_rows:
                        events_scanned += 1
                        if chain["sequence"] != expected_sequence:
                            errors.append(f"run:{run_id}:noncontiguous_chain")
                            break
                        expected_hash = _chain_hash(
                            expected_previous,
                            chain["event_kind"],
                            chain["event_id"],
                            chain["payload_hash"],
                        )
                        if (
                            chain["previous_hash"] != expected_previous
                            or chain["chain_hash"] != expected_hash
                        ):
                            errors.append(f"run:{run_id}:invalid_chain")
                            break
                        if expected_sequence == 1:
                            if (
                                chain["event_kind"] != "run_header"
                                or chain["event_id"] != run_id
                                or chain["payload_hash"] != header.content_hash
                            ):
                                errors.append(f"run:{run_id}:invalid_genesis")
                        else:
                            result = self._verify_event_db(
                                db, run_id, chain, state, revision
                            )
                            if result is None:
                                errors.append(
                                    f"run:{run_id}:invalid_event:{chain['sequence']}"
                                )
                            else:
                                state, revision, event_time, terminal_time = result
                                updated_at = event_time
                                if terminal_time is not None:
                                    ended_at = terminal_time
                        expected_previous = chain["chain_hash"]
                        expected_sequence += 1
                    projection = db.execute(
                        "SELECT * FROM run_projection WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    if projection is None:
                        errors.append(f"run:{run_id}:projection_missing")
                        continue
                    event_count = sum(
                        db.execute(
                            f"SELECT COUNT(*) FROM {table} WHERE run_id = ?",
                            (run_id,),
                        ).fetchone()[0]
                        for table in (
                            "run_transitions",
                            "job_checkpoints",
                            "receipt_references",
                        )
                    )
                    if len(chain_rows) != event_count + 1:
                        errors.append(f"run:{run_id}:orphan_event")
                    if (
                        projection["state"] != state.value
                        or projection["identity_id"] != header.identity_id
                        or projection["instance_id"] != header.instance_id
                        or projection["revision"] != revision
                        or projection["updated_at_utc"] != updated_at
                        or projection["ended_at_utc"] != ended_at
                        or projection["last_sequence"] != len(chain_rows)
                        or not chain_rows
                        or projection["last_chain_hash"] != chain_rows[-1]["chain_hash"]
                    ):
                        errors.append(f"run:{run_id}:projection_mismatch")
        except (sqlite3.Error, OSError, ValueError):
            errors.append("database_unreadable")
        return IntegrityReport(
            ok=not errors,
            schema_version=schema_version,
            runs_scanned=runs_scanned,
            events_scanned=events_scanned,
            errors=tuple(errors),
        )

    def close(self) -> None:
        with self._state_lock:
            self._closed = True

    shutdown = close

    def __enter__(self) -> "RunStore":
        self._ensure_open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _initialize(self) -> None:
        try:
            with sqlite3.connect(
                self.path,
                timeout=self._busy_timeout_ms / 1_000,
                isolation_level=None,
            ) as db:
                db.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
                db.execute("PRAGMA foreign_keys = ON")
                mode = str(db.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
                if mode != "wal":
                    raise RunStoreIntegrityError("WAL mode is unavailable")
                db.execute("PRAGMA synchronous = FULL")
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
                if version not in (0, STORE_SCHEMA_VERSION):
                    raise RunStoreIntegrityError("run store schema is newer than this runtime")
                if version == 0:
                    db.execute("BEGIN IMMEDIATE")
                    try:
                        for statement in _SCHEMA:
                            db.execute(statement)
                        db.execute(
                            "INSERT INTO store_meta VALUES (1, ?)",
                            (STORE_SCHEMA_VERSION,),
                        )
                        for table in _APPEND_ONLY_TABLES:
                            db.execute(
                                f"CREATE TRIGGER prevent_{table}_update BEFORE UPDATE ON {table} "
                                "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
                            )
                            db.execute(
                                f"CREATE TRIGGER prevent_{table}_delete BEFORE DELETE ON {table} "
                                "BEGIN SELECT RAISE(ABORT, 'append-only table'); END"
                            )
                        db.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")
                        db.execute("COMMIT")
                    except BaseException:
                        if db.in_transaction:
                            db.execute("ROLLBACK")
                        raise
        except RunStoreError:
            raise
        except sqlite3.Error as exc:
            raise RunStoreIntegrityError("run store initialization failed") from exc

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        uri = self.path.resolve().as_uri() + "?mode=ro"
        try:
            db = sqlite3.connect(
                uri,
                uri=True,
                timeout=self._busy_timeout_ms / 1_000,
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            db.execute("PRAGMA query_only = ON")
        except sqlite3.Error as exc:
            raise RunStoreIntegrityError("run store is unavailable") from exc
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        self._ensure_open()
        try:
            db = sqlite3.connect(
                self.path,
                timeout=self._busy_timeout_ms / 1_000,
                isolation_level=None,
            )
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            db.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            db.execute("PRAGMA synchronous = FULL")
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise RunStoreError("run store is busy or unavailable") from exc
        try:
            yield db
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def _append_transition_db(
        self, db: sqlite3.Connection, transition: SleepRunTransitionV1
    ) -> RunSnapshot:
        payload_json = _json_text(transition)
        existing = db.execute(
            "SELECT * FROM run_transitions WHERE transition_id = ? OR "
            "(run_id = ? AND idempotency_key = ?)",
            (transition.transition_id, transition.run_id, transition.idempotency_key),
        ).fetchall()
        if existing:
            if len(existing) != 1 or existing[0]["payload_json"] != payload_json:
                raise RunStoreConflictError(
                    "transition identity is already bound to different content"
                )
            return self._snapshot_db(db, transition.run_id)
        projection = self._projection_for_write(
            db, transition.run_id, transition.expected_revision
        )
        self._require_nonterminal(projection["state"])
        if projection["state"] != transition.from_state.value:
            raise RunStoreCASMismatchError("transition from_state is stale")
        if transition.checkpoint_id is not None:
            checkpoint = db.execute(
                "SELECT run_id FROM job_checkpoints WHERE checkpoint_id = ?",
                (transition.checkpoint_id,),
            ).fetchone()
            if checkpoint is None or checkpoint["run_id"] != transition.run_id:
                raise RunStoreConflictError("transition checkpoint is unavailable")
        if transition.receipt_ids:
            placeholders = ",".join("?" for _ in transition.receipt_ids)
            rows = db.execute(
                f"SELECT receipt_id, run_id FROM receipt_references WHERE receipt_id IN ({placeholders})",
                transition.receipt_ids,
            ).fetchall()
            if len(rows) != len(transition.receipt_ids) or any(
                row["run_id"] != transition.run_id for row in rows
            ):
                raise RunStoreConflictError("transition receipt is unavailable")
        sequence, previous_hash, chain_hash = self._append_chain_db(
            db,
            projection,
            "transition",
            transition.transition_id,
            transition.content_hash,
        )
        db.execute(
            "INSERT INTO run_transitions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                transition.transition_id,
                transition.run_id,
                transition.idempotency_key,
                transition.expected_revision,
                transition.from_state.value,
                transition.to_state.value,
                payload_json,
                transition.content_hash,
                sequence,
                previous_hash,
                chain_hash,
            ),
        )
        ended_at = (
            transition.created_at_utc
            if transition.to_state in _TERMINAL_STATES
            else projection["ended_at_utc"]
        )
        self._advance_projection_db(
            db,
            projection,
            state=transition.to_state.value,
            updated_at_utc=transition.created_at_utc,
            ended_at_utc=ended_at,
            sequence=sequence,
            chain_hash=chain_hash,
        )
        return self._snapshot_db(db, transition.run_id)

    @staticmethod
    def _append_chain_db(
        db: sqlite3.Connection,
        projection: sqlite3.Row,
        event_kind: str,
        event_id: str,
        payload_hash: str,
    ) -> tuple[int, str, str]:
        sequence = int(projection["last_sequence"]) + 1
        previous_hash = projection["last_chain_hash"]
        chain_hash = _chain_hash(previous_hash, event_kind, event_id, payload_hash)
        db.execute(
            "INSERT INTO run_chain VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                projection["run_id"],
                sequence,
                event_kind,
                event_id,
                payload_hash,
                previous_hash,
                chain_hash,
            ),
        )
        return sequence, previous_hash, chain_hash

    @staticmethod
    def _advance_projection_db(
        db: sqlite3.Connection,
        projection: sqlite3.Row,
        *,
        state: str,
        updated_at_utc: str,
        ended_at_utc: str | None,
        sequence: int,
        chain_hash: str,
    ) -> None:
        cursor = db.execute(
            "UPDATE run_projection SET state = ?, revision = revision + 1, "
            "updated_at_utc = ?, ended_at_utc = ?, last_sequence = ?, last_chain_hash = ? "
            "WHERE run_id = ? AND revision = ?",
            (
                state,
                updated_at_utc,
                ended_at_utc,
                sequence,
                chain_hash,
                projection["run_id"],
                projection["revision"],
            ),
        )
        if cursor.rowcount != 1:
            raise RunStoreCASMismatchError("run revision changed concurrently")

    @staticmethod
    def _checkpoint_matches(row: sqlite3.Row, values: dict[str, Any]) -> bool:
        return all(row[name] == value for name, value in values.items())

    @staticmethod
    def _checkpoint_row(row: sqlite3.Row) -> JobCheckpointReference:
        return JobCheckpointReference(
            run_id=row["run_id"],
            job_id=row["job_id"],
            checkpoint_id=row["checkpoint_id"],
            expected_revision=row["expected_revision"],
            created_at_utc=row["created_at_utc"],
            content_hash=row["content_hash"],
            idempotency_key=row["idempotency_key"],
            sequence=row["sequence"],
            chain_hash=row["chain_hash"],
        )

    @staticmethod
    def _require_nonterminal(state: str) -> None:
        if SleepRunState(state) in _TERMINAL_STATES:
            raise RunStoreConflictError("terminal runs cannot be reopened")

    @staticmethod
    def _projection_for_write(
        db: sqlite3.Connection, run_id: str, expected_revision: int
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM run_projection WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunStoreConflictError("run does not exist")
        if row["revision"] != expected_revision:
            raise RunStoreCASMismatchError("run revision is stale")
        return row

    @staticmethod
    def _snapshot_db(db: sqlite3.Connection, run_id: str) -> RunSnapshot:
        row = db.execute(
            "SELECT h.payload_json, p.* FROM run_headers AS h "
            "JOIN run_projection AS p ON p.run_id = h.run_id WHERE h.run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise RunStoreConflictError("run does not exist")
        try:
            header = SleepRunV1.from_dict(json.loads(row["payload_json"]))
            state = SleepRunState(row["state"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RunStoreIntegrityError("run projection is invalid") from exc
        return RunSnapshot(
            header=header,
            state=state,
            revision=row["revision"],
            updated_at_utc=row["updated_at_utc"],
            ended_at_utc=row["ended_at_utc"],
            last_sequence=row["last_sequence"],
            last_chain_hash=row["last_chain_hash"],
        )

    @staticmethod
    def _verify_event_db(
        db: sqlite3.Connection,
        run_id: str,
        chain: sqlite3.Row,
        state: SleepRunState,
        revision: int,
    ) -> tuple[SleepRunState, int, str, str | None] | None:
        kind = chain["event_kind"]
        if kind == "transition":
            row = db.execute(
                "SELECT * FROM run_transitions WHERE transition_id = ?",
                (chain["event_id"],),
            ).fetchone()
            if row is None:
                return None
            try:
                event = SleepRunTransitionV1.from_dict(json.loads(row["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            if (
                event.run_id != run_id
                or row["run_id"] != run_id
                or event.content_hash != chain["payload_hash"]
                or row["content_hash"] != event.content_hash
                or row["idempotency_key"] != event.idempotency_key
                or row["expected_revision"] != event.expected_revision
                or row["from_state"] != event.from_state.value
                or row["to_state"] != event.to_state.value
                or row["sequence"] != chain["sequence"]
                or row["previous_chain_hash"] != chain["previous_hash"]
                or row["chain_hash"] != chain["chain_hash"]
                or event.expected_revision != revision
                or event.from_state is not state
            ):
                return None
            terminal_time = event.created_at_utc if event.to_state in _TERMINAL_STATES else None
            return event.to_state, revision + 1, event.created_at_utc, terminal_time
        if kind == "job_checkpoint":
            row = db.execute(
                "SELECT * FROM job_checkpoints WHERE checkpoint_id = ?",
                (chain["event_id"],),
            ).fetchone()
            if row is not None:
                checkpoint_values = {
                    "run_id": row["run_id"],
                    "job_id": row["job_id"],
                    "checkpoint_id": row["checkpoint_id"],
                    "expected_revision": row["expected_revision"],
                    "created_at_utc": row["created_at_utc"],
                    "content_hash": row["content_hash"],
                    "idempotency_key": row["idempotency_key"],
                }
                checkpoint_payload_hash = _checkpoint_payload_hash(checkpoint_values)
            else:
                checkpoint_payload_hash = ""
            if (
                row is None
                or row["run_id"] != run_id
                or checkpoint_payload_hash != chain["payload_hash"]
                or row["expected_revision"] != revision
                or row["sequence"] != chain["sequence"]
                or row["previous_chain_hash"] != chain["previous_hash"]
                or row["chain_hash"] != chain["chain_hash"]
            ):
                return None
            try:
                _require_id(row["job_id"], "job_id")
                _require_id(row["checkpoint_id"], "checkpoint_id")
                _require_id(row["idempotency_key"], "idempotency_key")
                _require_timestamp(row["created_at_utc"])
            except ValueError:
                return None
            return state, revision + 1, row["created_at_utc"], None
        if kind == "job_receipt":
            row = db.execute(
                "SELECT * FROM receipt_references WHERE receipt_id = ?",
                (chain["event_id"],),
            ).fetchone()
            if row is None:
                return None
            try:
                event = SleepJobReceiptV1.from_dict(json.loads(row["payload_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            if (
                event.run_id != run_id
                or row["run_id"] != run_id
                or event.content_hash != chain["payload_hash"]
                or row["content_hash"] != event.content_hash
                or row["job_id"] != event.job_id
                or row["expected_revision"] != revision
                or row["sequence"] != chain["sequence"]
                or row["previous_chain_hash"] != chain["previous_hash"]
                or row["chain_hash"] != chain["chain_hash"]
            ):
                return None
            return state, revision + 1, event.ended_at_utc, None
        return None

    def _ensure_open(self) -> None:
        with self._state_lock:
            if self._closed:
                raise RunStoreClosedError("run store is closed")


__all__ = [
    "DATABASE_RELATIVE_PATH",
    "GENESIS_HASH",
    "IntegrityReport",
    "JobCheckpointReference",
    "RunSnapshot",
    "RunStore",
    "RunStoreCASMismatchError",
    "RunStoreClaimError",
    "RunStoreClosedError",
    "RunStoreConflictError",
    "RunStoreError",
    "RunStoreIntegrityError",
    "STORE_SCHEMA_VERSION",
]
