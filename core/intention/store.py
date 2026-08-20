"""Single-writer durable store for governed L5 intention state.

All mutations are serialized by one worker thread.  Callers submit immutable,
typed commands and perform reads through short-lived independent connections.
The store deliberately keeps service policy out of SQLite while enforcing the
storage invariants that must survive retries, crashes, and process restarts.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, TypeAlias

from core.intention.contracts import (
    CommitmentState,
    CommitmentV1,
    IntentionState,
    IntentionTransitionEventV1,
    IntentionV1,
    SuggestionBudgetV1,
    ThinkingCheckpointV1,
    VerificationRecordV1,
    canonical_json_bytes,
)


STORE_SCHEMA_VERSION = 1
DATABASE_RELATIVE_PATH = Path("intentions") / "intentions.sqlite3"


class IntentionStoreError(RuntimeError):
    """Base error for durable intention storage."""


class IntentionStoreClosedError(IntentionStoreError):
    """Raised when a command is submitted after shutdown begins."""


class IntentionStoreQueueFullError(IntentionStoreError):
    """Raised when the bounded writer queue has no applicable capacity."""


class IntentionStoreBusyError(IntentionStoreError):
    """Raised when SQLite remains locked beyond the configured timeout."""


class IntentionStoreConflictError(IntentionStoreError):
    """Raised for stale revisions or an already-held writer lease."""


class IntentionStoreIdempotencyConflictError(IntentionStoreConflictError):
    """Raised when one idempotency key is reused for a different command."""


class IntentionStoreIntegrityError(IntentionStoreError):
    """Raised when persisted state fails structural or hash validation."""


class StorePriority(str, Enum):
    NORMAL = "normal"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class PersistIntentionCommand:
    intention: IntentionV1
    transition: IntentionTransitionEventV1
    commitment: CommitmentV1 | None = None
    priority: StorePriority = StorePriority.NORMAL


@dataclass(frozen=True, slots=True)
class PersistCommitmentCommand:
    commitment: CommitmentV1
    priority: StorePriority = StorePriority.NORMAL


@dataclass(frozen=True, slots=True)
class PersistCheckpointCommand:
    checkpoint: ThinkingCheckpointV1
    priority: StorePriority = StorePriority.NORMAL


@dataclass(frozen=True, slots=True)
class PersistVerificationCommand:
    verification: VerificationRecordV1
    priority: StorePriority = StorePriority.CRITICAL


@dataclass(frozen=True, slots=True)
class PersistSuggestionBudgetCommand:
    budget: SuggestionBudgetV1
    priority: StorePriority = StorePriority.NORMAL


@dataclass(frozen=True, slots=True)
class ConsumeSuggestionCommand:
    budget: SuggestionBudgetV1
    candidate_id: str
    presented_intention_id: str
    consumed_at_utc: str
    priority: StorePriority = StorePriority.NORMAL


@dataclass(frozen=True, slots=True)
class RecordDerivationCommand:
    derivation_id: str
    target_kind: str
    target_id: str
    source_refs: tuple[str, ...]
    created_at_utc: str
    priority: StorePriority = StorePriority.NORMAL


StoreCommand: TypeAlias = (
    PersistIntentionCommand
    | PersistCommitmentCommand
    | PersistCheckpointCommand
    | PersistVerificationCommand
    | PersistSuggestionBudgetCommand
    | ConsumeSuggestionCommand
    | RecordDerivationCommand
)


@dataclass(frozen=True, slots=True)
class StoreResult:
    operation: str
    object_id: str
    revision: int
    state: str
    content_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "object_id": self.object_id,
            "revision": self.revision,
            "state": self.state,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "StoreResult":
        return cls(
            operation=str(value["operation"]),
            object_id=str(value["object_id"]),
            revision=int(value["revision"]),
            state=str(value["state"]),
            content_hash=str(value["content_hash"]),
        )


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    ok: bool
    schema_version: int
    rows_scanned: int
    errors: tuple[str, ...]


@dataclass(slots=True)
class _WriteRequest:
    command: StoreCommand
    expected_revision: int
    idempotency_key: str
    command_json: str
    command_hash: str
    done: threading.Event
    result: StoreResult | None = None
    error: BaseException | None = None


class _Stop:
    pass


@dataclass(slots=True)
class _RestoreRequest:
    source: Path
    done: threading.Event
    error: BaseException | None = None


_STOP = _Stop()


_INTENTION_TO_COMMITMENT: dict[IntentionState, CommitmentState | None] = {
    IntentionState.CANDIDATE: None,
    IntentionState.ACCEPTED: CommitmentState.ACTIVE,
    IntentionState.ACTIVE: CommitmentState.ACTIVE,
    IntentionState.WAITING: CommitmentState.WAITING,
    IntentionState.BLOCKED: CommitmentState.BLOCKED,
    IntentionState.VERIFYING: CommitmentState.VERIFYING,
    IntentionState.COMPLETED: CommitmentState.FULFILLED,
    IntentionState.FAILED: CommitmentState.FAILED,
    IntentionState.CANCELLED: CommitmentState.CANCELLED,
    IntentionState.EXPIRED: CommitmentState.EXPIRED,
}


def _json_text(value: Mapping[str, object]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _hash_json(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_text(value: object, field: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 256:
        raise ValueError(f"{field} must contain 1..256 UTF-8 bytes")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field} must not contain control characters")
    return value


def _contract_wire(value: Any) -> dict[str, object]:
    if not hasattr(value, "to_dict"):
        raise TypeError("store commands require typed intention contracts")
    return value.to_dict()


def _command_wire(command: StoreCommand, expected_revision: int) -> dict[str, object]:
    common: dict[str, object] = {
        "expected_revision": expected_revision,
        "priority": command.priority.value,
    }
    if isinstance(command, PersistIntentionCommand):
        return {
            **common,
            "operation": "persist_intention",
            "intention": _contract_wire(command.intention),
            "transition": _contract_wire(command.transition),
            "commitment": (
                None if command.commitment is None else _contract_wire(command.commitment)
            ),
        }
    if isinstance(command, PersistCommitmentCommand):
        return {
            **common,
            "operation": "persist_commitment",
            "commitment": _contract_wire(command.commitment),
        }
    if isinstance(command, PersistCheckpointCommand):
        return {
            **common,
            "operation": "persist_checkpoint",
            "checkpoint": _contract_wire(command.checkpoint),
        }
    if isinstance(command, PersistVerificationCommand):
        return {
            **common,
            "operation": "persist_verification",
            "verification": _contract_wire(command.verification),
        }
    if isinstance(command, PersistSuggestionBudgetCommand):
        return {
            **common,
            "operation": "persist_suggestion_budget",
            "budget": _contract_wire(command.budget),
        }
    if isinstance(command, ConsumeSuggestionCommand):
        return {
            **common,
            "operation": "consume_suggestion",
            "budget": _contract_wire(command.budget),
            "candidate_id": command.candidate_id,
            "presented_intention_id": command.presented_intention_id,
            "consumed_at_utc": command.consumed_at_utc,
        }
    if isinstance(command, RecordDerivationCommand):
        return {
            **common,
            "operation": "record_derivation",
            "derivation_id": command.derivation_id,
            "target_kind": command.target_kind,
            "target_id": command.target_id,
            "source_refs": list(command.source_refs),
            "created_at_utc": command.created_at_utc,
        }
    raise TypeError(f"unsupported store command: {type(command).__name__}")


class IntentionStore:
    """SQLite intention store with one bounded asynchronous writer."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        queue_capacity: int = 128,
        critical_reserve: int = 16,
        busy_timeout_ms: int = 1_000,
        fault_injector: Callable[[str, StoreCommand], None] | None = None,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        if critical_reserve < 1:
            raise ValueError("critical_reserve must be positive")
        if busy_timeout_ms < 1:
            raise ValueError("busy_timeout_ms must be positive")
        root = Path(data_root).expanduser().resolve()
        self.database_path = root / DATABASE_RELATIVE_PATH
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._queue_capacity = queue_capacity
        self._critical_reserve = critical_reserve
        self._busy_timeout_ms = busy_timeout_ms
        self._fault_injector = fault_injector
        self._queue: queue.Queue[_WriteRequest | _RestoreRequest | _Stop] = queue.Queue(
            maxsize=queue_capacity + critical_reserve
        )
        self._state_lock = threading.Lock()
        self._accepting = True
        self._normal_inflight = 0
        self._critical_inflight = 0
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._closed = threading.Event()
        self._lease_handle: Any = None
        self._acquire_writer_lease()
        self._writer = threading.Thread(
            target=self._writer_main,
            name=f"javis-intention-writer-{uuid.uuid4().hex[:8]}",
            daemon=True,
        )
        self._writer.start()
        self._ready.wait()
        if self._startup_error is not None:
            self._release_writer_lease()
            raise IntentionStoreError("failed to initialize intention store") from self._startup_error

    @property
    def writer_thread_id(self) -> int | None:
        return self._writer.ident

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def execute(
        self,
        command: StoreCommand,
        expected_revision: int,
        idempotency_key: str,
        *,
        timeout: float | None = None,
    ) -> StoreResult:
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        key = _require_text(idempotency_key, "idempotency_key")
        wire = _command_wire(command, expected_revision)
        command_json = _json_text(wire)
        request = _WriteRequest(
            command=command,
            expected_revision=expected_revision,
            idempotency_key=key,
            command_json=command_json,
            command_hash=hashlib.sha256(command_json.encode("utf-8")).hexdigest(),
            done=threading.Event(),
        )
        is_critical = command.priority is StorePriority.CRITICAL
        with self._state_lock:
            if not self._accepting:
                raise IntentionStoreClosedError("intention store is closing or closed")
            if not is_critical and self._normal_inflight >= self._queue_capacity:
                raise IntentionStoreQueueFullError("normal writer queue capacity exhausted")
            if is_critical and self._critical_inflight >= self._critical_reserve:
                raise IntentionStoreQueueFullError("critical writer reserve exhausted")
            try:
                self._queue.put_nowait(request)
            except queue.Full as exc:
                raise IntentionStoreQueueFullError("writer queue capacity exhausted") from exc
            if is_critical:
                self._critical_inflight += 1
            else:
                self._normal_inflight += 1
        if not request.done.wait(timeout):
            raise TimeoutError(
                "intention store command timed out; retry with the same idempotency key"
            )
        if request.error is not None:
            raise request.error
        assert request.result is not None
        return request.result

    def get_intention(self, intention_id: str) -> IntentionV1 | None:
        return self._get_contract(
            "SELECT payload_json FROM intentions WHERE intention_id = ?",
            (_require_text(intention_id, "intention_id"),),
            IntentionV1,
        )

    def get_commitment(self, commitment_id: str) -> CommitmentV1 | None:
        return self._get_contract(
            "SELECT payload_json FROM commitments WHERE commitment_id = ?",
            (_require_text(commitment_id, "commitment_id"),),
            CommitmentV1,
        )

    def get_checkpoint(self, checkpoint_id: str) -> ThinkingCheckpointV1 | None:
        return self._get_contract(
            "SELECT payload_json FROM checkpoints WHERE checkpoint_id = ?",
            (_require_text(checkpoint_id, "checkpoint_id"),),
            ThinkingCheckpointV1,
        )

    def get_verification(self, verification_id: str) -> VerificationRecordV1 | None:
        return self._get_contract(
            "SELECT payload_json FROM verification_records WHERE verification_id = ?",
            (_require_text(verification_id, "verification_id"),),
            VerificationRecordV1,
        )

    def get_suggestion_budget(self, budget_id: str) -> SuggestionBudgetV1 | None:
        return self._get_contract(
            "SELECT payload_json FROM suggestion_budgets WHERE budget_id = ?",
            (_require_text(budget_id, "budget_id"),),
            SuggestionBudgetV1,
        )

    def list_transitions(self, intention_id: str) -> tuple[IntentionTransitionEventV1, ...]:
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM intention_transitions "
                "WHERE intention_id = ? ORDER BY resulting_revision",
                (_require_text(intention_id, "intention_id"),),
            ).fetchall()
        return tuple(
            IntentionTransitionEventV1.from_dict(json.loads(row["payload_json"]))
            for row in rows
        )

    def scan_integrity(self) -> IntegrityReport:
        errors: list[str] = []
        scanned = 0
        with self._read_connection() as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                errors.append(f"sqlite.integrity_check:{integrity}")
            for row in connection.execute("PRAGMA foreign_key_check").fetchall():
                errors.append(f"sqlite.foreign_key:{tuple(row)}")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            table_contracts: tuple[tuple[str, str, Any], ...] = (
                ("intentions", "intention_id", IntentionV1),
                ("intention_transitions", "transition_id", IntentionTransitionEventV1),
                ("commitments", "commitment_id", CommitmentV1),
                ("checkpoints", "checkpoint_id", ThinkingCheckpointV1),
                ("verification_records", "verification_id", VerificationRecordV1),
                ("suggestion_budgets", "budget_id", SuggestionBudgetV1),
            )
            for table, id_column, contract_type in table_contracts:
                for row in connection.execute(
                    f"SELECT * FROM {table}"
                ).fetchall():
                    scanned += 1
                    try:
                        contract = contract_type.from_dict(json.loads(row["payload_json"]))
                        if getattr(contract, id_column) != row[id_column]:
                            raise ValueError("indexed object ID differs from payload")
                        if contract.content_hash != row["content_hash"]:
                            raise ValueError("indexed content_hash differs from payload")
                        columns = set(row.keys())
                        if "revision" in columns and contract.revision != row["revision"]:
                            raise ValueError("indexed revision differs from payload")
                        if "state" in columns:
                            contract_state = getattr(contract.state, "value", contract.state)
                            if contract_state != row["state"]:
                                raise ValueError("indexed state differs from payload")
                        if "result" in columns and contract.result.value != row["result"]:
                            raise ValueError("indexed verification result differs from payload")
                        if "resulting_revision" in columns and (
                            contract.resulting_revision != row["resulting_revision"]
                            or contract.expected_revision != row["expected_revision"]
                            or contract.to_state.value != row["to_state"]
                        ):
                            raise ValueError("indexed transition differs from payload")
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        errors.append(f"{table}:{row[id_column]}:{exc}")
            for row in connection.execute(
                "SELECT intention_id, state, commitment_id FROM intentions"
            ).fetchall():
                state = IntentionState(row["state"])
                commitment_id = row["commitment_id"]
                if commitment_id is None:
                    if state not in {
                        IntentionState.CANDIDATE,
                        IntentionState.FAILED,
                        IntentionState.CANCELLED,
                        IntentionState.EXPIRED,
                    }:
                        errors.append(
                            f"intentions:{row['intention_id']}:required commitment missing"
                        )
                    continue
                commitment = connection.execute(
                    "SELECT intention_id, state FROM commitments WHERE commitment_id = ?",
                    (commitment_id,),
                ).fetchone()
                expected_state = _INTENTION_TO_COMMITMENT[state]
                if (
                    commitment is None
                    or commitment["intention_id"] != row["intention_id"]
                    or expected_state is None
                    or commitment["state"] != expected_state.value
                ):
                    errors.append(
                        f"intentions:{row['intention_id']}:commitment consistency"
                    )
            for row in connection.execute(
                "SELECT idempotency_key, command_json, command_hash, result_json, result_hash "
                "FROM command_idempotency"
            ).fetchall():
                scanned += 1
                if hashlib.sha256(row["command_json"].encode("utf-8")).hexdigest() != row[
                    "command_hash"
                ]:
                    errors.append(f"command_idempotency:{row['idempotency_key']}:command_hash")
                if hashlib.sha256(row["result_json"].encode("utf-8")).hexdigest() != row[
                    "result_hash"
                ]:
                    errors.append(f"command_idempotency:{row['idempotency_key']}:result_hash")
            for row in connection.execute(
                "SELECT derivation_id, payload_json, content_hash FROM derivations"
            ).fetchall():
                scanned += 1
                try:
                    payload = json.loads(row["payload_json"])
                    if _hash_json(payload) != row["content_hash"]:
                        raise ValueError("content hash mismatch")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"derivations:{row['derivation_id']}:{exc}")
        return IntegrityReport(
            ok=not errors and version == STORE_SCHEMA_VERSION,
            schema_version=version,
            rows_scanned=scanned,
            errors=tuple(errors),
        )

    def backup_to(self, destination: str | Path) -> Path:
        target = Path(destination).expanduser().resolve()
        if target == self.database_path:
            raise ValueError("backup destination must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._read_connection() as source, sqlite3.connect(target) as destination_db:
            source.backup(destination_db)
        return target

    def restore_from_backup(self, source: str | Path) -> None:
        backup = Path(source).expanduser().resolve()
        if not backup.is_file() or backup == self.database_path:
            raise ValueError("backup must be an existing separate SQLite file")
        with sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True) as check:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise IntentionStoreIntegrityError("backup failed SQLite integrity_check")
            if int(check.execute("PRAGMA user_version").fetchone()[0]) != STORE_SCHEMA_VERSION:
                raise IntentionStoreIntegrityError("backup schema version is not supported")
        request = _RestoreRequest(source=backup, done=threading.Event())
        with self._state_lock:
            if not self._accepting:
                raise IntentionStoreClosedError("intention store is closing or closed")
            if self._critical_inflight >= self._critical_reserve:
                raise IntentionStoreQueueFullError("critical writer reserve exhausted")
            try:
                self._queue.put_nowait(request)
            except queue.Full as exc:
                raise IntentionStoreQueueFullError("writer queue capacity exhausted") from exc
            self._critical_inflight += 1
        request.done.wait()
        if request.error is not None:
            raise request.error

    rollback = restore_from_backup

    def close(self, *, timeout: float | None = None) -> None:
        with self._state_lock:
            if not self._accepting:
                already_closing = True
            else:
                already_closing = False
                self._accepting = False
        # Never wait for queue space while holding _state_lock: the writer
        # needs that lock to release capacity for the sentinel.
        if not already_closing:
            self._queue.put(_STOP)
        if not already_closing:
            self._writer.join(timeout)
        elif not self._closed.is_set():
            self._writer.join(timeout)
        if self._writer.is_alive():
            raise TimeoutError("intention store shutdown did not drain before timeout")
        self._release_writer_lease()

    shutdown = close

    def __enter__(self) -> "IntentionStore":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _get_contract(self, sql: str, parameters: tuple[object, ...], contract_type: Any) -> Any:
        with self._read_connection() as connection:
            row = connection.execute(sql, parameters).fetchone()
        if row is None:
            return None
        return contract_type.from_dict(json.loads(row["payload_json"]))

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        if self._closed.is_set():
            raise IntentionStoreClosedError("intention store is closed")
        connection = sqlite3.connect(
            f"file:{self.database_path.as_posix()}?mode=ro",
            uri=True,
            timeout=self._busy_timeout_ms / 1000,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        try:
            yield connection
        finally:
            connection.close()

    def _writer_main(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.database_path,
                timeout=self._busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            self._migrate(connection)
        except BaseException as exc:
            self._startup_error = exc
            if connection is not None:
                connection.close()
            self._ready.set()
            self._closed.set()
            return
        self._ready.set()
        try:
            while True:
                item = self._queue.get()
                if item is _STOP:
                    self._queue.task_done()
                    break
                if isinstance(item, _RestoreRequest):
                    try:
                        with sqlite3.connect(
                            f"file:{item.source.as_posix()}?mode=ro", uri=True
                        ) as source_db:
                            source_db.backup(connection)
                        connection.execute("PRAGMA foreign_keys = ON")
                        connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
                        if int(connection.execute("PRAGMA user_version").fetchone()[0]) != STORE_SCHEMA_VERSION:
                            raise IntentionStoreIntegrityError(
                                "restored backup schema version is not supported"
                            )
                    except BaseException as exc:
                        item.error = self._normalize_sqlite_error(exc)
                    finally:
                        with self._state_lock:
                            self._critical_inflight -= 1
                        item.done.set()
                        self._queue.task_done()
                    continue
                assert isinstance(item, _WriteRequest)
                try:
                    item.result = self._execute_transaction(connection, item)
                except BaseException as exc:
                    item.error = self._normalize_sqlite_error(exc)
                finally:
                    with self._state_lock:
                        if item.command.priority is StorePriority.CRITICAL:
                            self._critical_inflight -= 1
                        else:
                            self._normal_inflight -= 1
                    item.done.set()
                    self._queue.task_done()
        finally:
            connection.close()
            self._closed.set()

    def _execute_transaction(
        self, connection: sqlite3.Connection, request: _WriteRequest
    ) -> StoreResult:
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT command_hash, result_json FROM command_idempotency "
                "WHERE idempotency_key = ?",
                (request.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["command_hash"] != request.command_hash:
                    raise IntentionStoreIdempotencyConflictError(
                        "idempotency key is already bound to a different command"
                    )
                connection.execute("COMMIT")
                return StoreResult.from_dict(json.loads(existing["result_json"]))
            result = self._dispatch(
                connection, request.command, request.expected_revision, request.idempotency_key
            )
            if self._fault_injector is not None:
                self._fault_injector("before_commit", request.command)
            result_json = _json_text(result.to_dict())
            connection.execute(
                "INSERT INTO command_idempotency (idempotency_key, command_hash, command_json, "
                "result_hash, result_json) VALUES (?, ?, ?, ?, ?)",
                (
                    request.idempotency_key,
                    request.command_hash,
                    request.command_json,
                    hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
                    result_json,
                ),
            )
            connection.execute("COMMIT")
            return result
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _dispatch(
        self,
        connection: sqlite3.Connection,
        command: StoreCommand,
        expected_revision: int,
        idempotency_key: str,
    ) -> StoreResult:
        if isinstance(command, PersistIntentionCommand):
            return self._persist_intention(
                connection, command, expected_revision, idempotency_key
            )
        if isinstance(command, PersistCommitmentCommand):
            return self._persist_commitment(connection, command.commitment, expected_revision)
        if isinstance(command, PersistCheckpointCommand):
            return self._persist_checkpoint(connection, command.checkpoint, expected_revision)
        if isinstance(command, PersistVerificationCommand):
            return self._persist_verification(
                connection, command.verification, expected_revision
            )
        if isinstance(command, PersistSuggestionBudgetCommand):
            return self._persist_budget(connection, command.budget, expected_revision)
        if isinstance(command, ConsumeSuggestionCommand):
            return self._consume_suggestion(connection, command, expected_revision)
        if isinstance(command, RecordDerivationCommand):
            return self._record_derivation(connection, command, expected_revision)
        raise TypeError(f"unsupported store command: {type(command).__name__}")

    def _persist_intention(
        self,
        connection: sqlite3.Connection,
        command: PersistIntentionCommand,
        expected_revision: int,
        idempotency_key: str,
    ) -> StoreResult:
        intention = command.intention
        transition = command.transition
        if transition.intention_id != intention.intention_id:
            raise IntentionStoreConflictError("transition references another intention")
        if transition.expected_revision != expected_revision:
            raise IntentionStoreConflictError("transition expected revision differs from command")
        if transition.resulting_revision != intention.revision:
            raise IntentionStoreConflictError("transition result revision differs from intention")
        if transition.to_state is not intention.state:
            raise IntentionStoreConflictError("transition state differs from intention state")
        if transition.idempotency_key != idempotency_key:
            raise IntentionStoreConflictError("transition idempotency key differs from command")
        row = connection.execute(
            "SELECT revision, state, commitment_id FROM intentions WHERE intention_id = ?",
            (intention.intention_id,),
        ).fetchone()
        if row is None:
            if expected_revision != 0 or transition.from_state is not None:
                raise IntentionStoreConflictError("intention does not exist at expected revision")
            connection.execute(
                "INSERT INTO intentions (intention_id, revision, state, commitment_id, "
                "owner_subject_id, runtime_boot_id, content_hash, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    intention.intention_id,
                    intention.revision,
                    intention.state.value,
                    intention.commitment_id,
                    intention.owner_subject_id,
                    intention.runtime_boot_id,
                    intention.content_hash,
                    _json_text(intention.to_dict()),
                ),
            )
        else:
            if int(row["revision"]) != expected_revision:
                raise IntentionStoreConflictError("stale intention revision")
            if transition.from_state is None or transition.from_state.value != row["state"]:
                raise IntentionStoreConflictError("transition from_state is stale")
            changed = connection.execute(
                "UPDATE intentions SET revision = ?, state = ?, commitment_id = ?, "
                "owner_subject_id = ?, runtime_boot_id = ?, content_hash = ?, payload_json = ? "
                "WHERE intention_id = ? AND revision = ?",
                (
                    intention.revision,
                    intention.state.value,
                    intention.commitment_id,
                    intention.owner_subject_id,
                    intention.runtime_boot_id,
                    intention.content_hash,
                    _json_text(intention.to_dict()),
                    intention.intention_id,
                    expected_revision,
                ),
            ).rowcount
            if changed != 1:
                raise IntentionStoreConflictError("intention CAS failed")
        connection.execute(
            "INSERT INTO intention_transitions (transition_id, intention_id, expected_revision, "
            "resulting_revision, from_state, to_state, idempotency_key, content_hash, "
            "payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                transition.transition_id,
                transition.intention_id,
                transition.expected_revision,
                transition.resulting_revision,
                None if transition.from_state is None else transition.from_state.value,
                transition.to_state.value,
                transition.idempotency_key,
                transition.content_hash,
                _json_text(transition.to_dict()),
            ),
        )
        if command.commitment is not None:
            current_commitment = connection.execute(
                "SELECT revision FROM commitments WHERE commitment_id = ?",
                (command.commitment.commitment_id,),
            ).fetchone()
            commitment_expected = (
                0 if current_commitment is None else int(current_commitment["revision"])
            )
            self._persist_commitment(connection, command.commitment, commitment_expected)
        self._assert_commitment_consistency(connection, intention)
        return StoreResult(
            "persist_intention",
            intention.intention_id,
            intention.revision,
            intention.state.value,
            intention.content_hash,
        )

    def _persist_commitment(
        self,
        connection: sqlite3.Connection,
        commitment: CommitmentV1,
        expected_revision: int,
    ) -> StoreResult:
        intention = connection.execute(
            "SELECT revision, state, commitment_id FROM intentions WHERE intention_id = ?",
            (commitment.intention_id,),
        ).fetchone()
        if intention is None:
            raise IntentionStoreConflictError("commitment intention does not exist")
        if intention["commitment_id"] != commitment.commitment_id:
            raise IntentionStoreConflictError("intention does not reference commitment")
        expected_state = _INTENTION_TO_COMMITMENT[IntentionState(intention["state"])]
        if expected_state is not commitment.state:
            raise IntentionStoreConflictError("commitment state is inconsistent with intention")
        row = connection.execute(
            "SELECT revision FROM commitments WHERE commitment_id = ?",
            (commitment.commitment_id,),
        ).fetchone()
        payload = _json_text(commitment.to_dict())
        if row is None:
            if expected_revision != 0 or commitment.revision != 1:
                raise IntentionStoreConflictError("commitment does not exist at expected revision")
            connection.execute(
                "INSERT INTO commitments (commitment_id, intention_id, revision, state, "
                "content_hash, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    commitment.commitment_id,
                    commitment.intention_id,
                    commitment.revision,
                    commitment.state.value,
                    commitment.content_hash,
                    payload,
                ),
            )
        else:
            if int(row["revision"]) != expected_revision:
                raise IntentionStoreConflictError("stale commitment revision")
            changed = connection.execute(
                "UPDATE commitments SET revision = ?, state = ?, content_hash = ?, "
                "payload_json = ? WHERE commitment_id = ? AND revision = ?",
                (
                    commitment.revision,
                    commitment.state.value,
                    commitment.content_hash,
                    payload,
                    commitment.commitment_id,
                    expected_revision,
                ),
            ).rowcount
            if changed != 1 or commitment.revision != expected_revision + 1:
                raise IntentionStoreConflictError("commitment CAS failed")
        return StoreResult(
            "persist_commitment",
            commitment.commitment_id,
            commitment.revision,
            commitment.state.value,
            commitment.content_hash,
        )

    def _persist_checkpoint(
        self,
        connection: sqlite3.Connection,
        checkpoint: ThinkingCheckpointV1,
        expected_revision: int,
    ) -> StoreResult:
        intention = connection.execute(
            "SELECT revision, commitment_id FROM intentions WHERE intention_id = ?",
            (checkpoint.intention_id,),
        ).fetchone()
        if intention is None or int(intention["revision"]) != expected_revision:
            raise IntentionStoreConflictError("checkpoint parent revision is stale")
        if checkpoint.commitment_id != intention["commitment_id"]:
            raise IntentionStoreConflictError("checkpoint commitment is inconsistent")
        connection.execute(
            "INSERT INTO checkpoints (checkpoint_id, intention_id, commitment_id, "
            "intention_revision, content_hash, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                checkpoint.checkpoint_id,
                checkpoint.intention_id,
                checkpoint.commitment_id,
                expected_revision,
                checkpoint.content_hash,
                _json_text(checkpoint.to_dict()),
            ),
        )
        return StoreResult(
            "persist_checkpoint",
            checkpoint.checkpoint_id,
            expected_revision,
            checkpoint.stage.value,
            checkpoint.content_hash,
        )

    def _persist_verification(
        self,
        connection: sqlite3.Connection,
        verification: VerificationRecordV1,
        expected_revision: int,
    ) -> StoreResult:
        intention = connection.execute(
            "SELECT revision, commitment_id FROM intentions WHERE intention_id = ?",
            (verification.intention_id,),
        ).fetchone()
        if intention is None or int(intention["revision"]) != expected_revision:
            raise IntentionStoreConflictError("verification parent revision is stale")
        if verification.intention_revision != expected_revision:
            raise IntentionStoreConflictError("verification intention_revision is stale")
        if verification.commitment_id != intention["commitment_id"]:
            raise IntentionStoreConflictError("verification commitment is inconsistent")
        connection.execute(
            "INSERT INTO verification_records (verification_id, intention_id, commitment_id, "
            "intention_revision, result, content_hash, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                verification.verification_id,
                verification.intention_id,
                verification.commitment_id,
                verification.intention_revision,
                verification.result.value,
                verification.content_hash,
                _json_text(verification.to_dict()),
            ),
        )
        return StoreResult(
            "persist_verification",
            verification.verification_id,
            verification.revision,
            verification.result.value,
            verification.content_hash,
        )

    def _persist_budget(
        self,
        connection: sqlite3.Connection,
        budget: SuggestionBudgetV1,
        expected_revision: int,
    ) -> StoreResult:
        row = connection.execute(
            "SELECT revision FROM suggestion_budgets WHERE budget_id = ?",
            (budget.budget_id,),
        ).fetchone()
        payload = _json_text(budget.to_dict())
        if row is None:
            if expected_revision != 0 or budget.revision != 1:
                raise IntentionStoreConflictError("suggestion budget does not exist")
            connection.execute(
                "INSERT INTO suggestion_budgets (budget_id, owner_subject_id, category, "
                "revision, state, content_hash, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    budget.budget_id,
                    budget.owner_subject_id,
                    budget.category.value,
                    budget.revision,
                    budget.state.value,
                    budget.content_hash,
                    payload,
                ),
            )
        else:
            if int(row["revision"]) != expected_revision or budget.revision != expected_revision + 1:
                raise IntentionStoreConflictError("suggestion budget CAS failed")
            connection.execute(
                "UPDATE suggestion_budgets SET revision = ?, state = ?, content_hash = ?, "
                "payload_json = ? WHERE budget_id = ? AND revision = ?",
                (
                    budget.revision,
                    budget.state.value,
                    budget.content_hash,
                    payload,
                    budget.budget_id,
                    expected_revision,
                ),
            )
        return StoreResult(
            "persist_suggestion_budget",
            budget.budget_id,
            budget.revision,
            budget.state.value,
            budget.content_hash,
        )

    def _consume_suggestion(
        self,
        connection: sqlite3.Connection,
        command: ConsumeSuggestionCommand,
        expected_revision: int,
    ) -> StoreResult:
        _require_text(command.candidate_id, "candidate_id")
        _require_text(command.presented_intention_id, "presented_intention_id")
        _require_text(command.consumed_at_utc, "consumed_at_utc")
        previous = connection.execute(
            "SELECT payload_json FROM suggestion_budgets WHERE budget_id = ?",
            (command.budget.budget_id,),
        ).fetchone()
        if previous is None:
            raise IntentionStoreConflictError("suggestion budget does not exist")
        prior = SuggestionBudgetV1.from_dict(json.loads(previous["payload_json"]))
        if command.budget.presentations_used != prior.presentations_used + 1:
            raise IntentionStoreConflictError("suggestion consumption must increment used by one")
        if command.budget.last_candidate_id != command.candidate_id:
            raise IntentionStoreConflictError("budget last_candidate_id is inconsistent")
        if command.budget.last_presented_intention_id != command.presented_intention_id:
            raise IntentionStoreConflictError("budget presented intention is inconsistent")
        result = self._persist_budget(connection, command.budget, expected_revision)
        connection.execute(
            "INSERT INTO suggestion_consumptions (budget_id, candidate_id, "
            "presented_intention_id, consumed_at_utc) VALUES (?, ?, ?, ?)",
            (
                command.budget.budget_id,
                command.candidate_id,
                command.presented_intention_id,
                command.consumed_at_utc,
            ),
        )
        return StoreResult(
            "consume_suggestion",
            result.object_id,
            result.revision,
            result.state,
            result.content_hash,
        )

    def _record_derivation(
        self,
        connection: sqlite3.Connection,
        command: RecordDerivationCommand,
        expected_revision: int,
    ) -> StoreResult:
        if expected_revision != 0:
            raise IntentionStoreConflictError("immutable derivations require expected revision 0")
        payload: dict[str, object] = {
            "derivation_id": _require_text(command.derivation_id, "derivation_id"),
            "target_kind": _require_text(command.target_kind, "target_kind"),
            "target_id": _require_text(command.target_id, "target_id"),
            "source_refs": [
                _require_text(item, "source_ref") for item in command.source_refs
            ],
            "created_at_utc": _require_text(command.created_at_utc, "created_at_utc"),
        }
        if not payload["source_refs"]:
            raise ValueError("source_refs must not be empty")
        content_hash = _hash_json(payload)
        connection.execute(
            "INSERT INTO derivations (derivation_id, target_kind, target_id, content_hash, "
            "payload_json) VALUES (?, ?, ?, ?, ?)",
            (
                command.derivation_id,
                command.target_kind,
                command.target_id,
                content_hash,
                _json_text(payload),
            ),
        )
        return StoreResult(
            "record_derivation", command.derivation_id, 1, "recorded", content_hash
        )

    def _assert_commitment_consistency(
        self, connection: sqlite3.Connection, intention: IntentionV1
    ) -> None:
        expected = _INTENTION_TO_COMMITMENT[intention.state]
        if intention.commitment_id is None:
            if intention.state not in (
                IntentionState.CANDIDATE,
                IntentionState.FAILED,
                IntentionState.CANCELLED,
                IntentionState.EXPIRED,
            ):
                raise IntentionStoreConflictError(
                    "accepted or executing intention state requires a commitment"
                )
            return
        row = connection.execute(
            "SELECT intention_id, state FROM commitments WHERE commitment_id = ?",
            (intention.commitment_id,),
        ).fetchone()
        if row is None or row["intention_id"] != intention.intention_id:
            raise IntentionStoreConflictError("referenced commitment does not exist")
        if expected is None or row["state"] != expected.value:
            raise IntentionStoreConflictError("intention and commitment states diverge")

    def _migrate(self, connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > STORE_SCHEMA_VERSION:
            raise IntentionStoreIntegrityError("database schema is newer than this runtime")
        if version == STORE_SCHEMA_VERSION:
            self._create_schema(connection)
            return
        backup_path = self.database_path.with_suffix(".pre-migration.sqlite3")
        existed = self.database_path.exists() and self.database_path.stat().st_size > 0
        if existed:
            with sqlite3.connect(backup_path) as backup:
                connection.backup(backup)
        try:
            self._create_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(f"PRAGMA user_version = {STORE_SCHEMA_VERSION}")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations (version, migration_id) VALUES (?, ?)",
                (STORE_SCHEMA_VERSION, "l5-intention-store-v1"),
            )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY CHECK(version > 0),
                migration_id TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS intentions (
                intention_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL CHECK(revision > 0),
                state TEXT NOT NULL CHECK(state IN (
                    'candidate','accepted','active','waiting','blocked','verifying',
                    'completed','failed','cancelled','expired')),
                commitment_id TEXT,
                owner_subject_id TEXT NOT NULL,
                runtime_boot_id TEXT NOT NULL,
                content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS intention_transitions (
                transition_id TEXT PRIMARY KEY,
                intention_id TEXT NOT NULL REFERENCES intentions(intention_id),
                expected_revision INTEGER NOT NULL CHECK(expected_revision >= 0),
                resulting_revision INTEGER NOT NULL CHECK(resulting_revision = expected_revision + 1),
                from_state TEXT,
                to_state TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
                payload_json TEXT NOT NULL,
                UNIQUE(intention_id, resulting_revision)
            );
            CREATE TABLE IF NOT EXISTS commitments (
                commitment_id TEXT PRIMARY KEY,
                intention_id TEXT NOT NULL UNIQUE REFERENCES intentions(intention_id),
                revision INTEGER NOT NULL CHECK(revision > 0),
                state TEXT NOT NULL CHECK(state IN (
                    'active','waiting','blocked','verifying','fulfilled','failed','cancelled','expired')),
                content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                intention_id TEXT NOT NULL REFERENCES intentions(intention_id),
                commitment_id TEXT REFERENCES commitments(commitment_id),
                intention_revision INTEGER NOT NULL CHECK(intention_revision > 0),
                content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS verification_records (
                verification_id TEXT PRIMARY KEY,
                intention_id TEXT NOT NULL REFERENCES intentions(intention_id),
                commitment_id TEXT REFERENCES commitments(commitment_id),
                intention_revision INTEGER NOT NULL CHECK(intention_revision > 0),
                result TEXT NOT NULL CHECK(result IN ('satisfied','unsatisfied','inconclusive')),
                content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS suggestion_budgets (
                budget_id TEXT PRIMARY KEY,
                owner_subject_id TEXT NOT NULL,
                category TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK(revision > 0),
                state TEXT NOT NULL,
                content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
                payload_json TEXT NOT NULL,
                UNIQUE(owner_subject_id, category)
            );
            CREATE TABLE IF NOT EXISTS suggestion_consumptions (
                budget_id TEXT NOT NULL REFERENCES suggestion_budgets(budget_id),
                candidate_id TEXT NOT NULL,
                presented_intention_id TEXT NOT NULL,
                consumed_at_utc TEXT NOT NULL,
                PRIMARY KEY(budget_id, candidate_id),
                UNIQUE(presented_intention_id)
            );
            CREATE TABLE IF NOT EXISTS command_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                command_hash TEXT NOT NULL CHECK(length(command_hash) = 64),
                command_json TEXT NOT NULL,
                result_hash TEXT NOT NULL CHECK(length(result_hash) = 64),
                result_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS derivations (
                derivation_id TEXT PRIMARY KEY,
                target_kind TEXT NOT NULL,
                target_id TEXT NOT NULL,
                content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
                payload_json TEXT NOT NULL,
                UNIQUE(target_kind, target_id)
            );
            CREATE TRIGGER IF NOT EXISTS verification_records_no_update
            BEFORE UPDATE ON verification_records BEGIN
                SELECT RAISE(ABORT, 'verification records are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS verification_records_no_delete
            BEFORE DELETE ON verification_records BEGIN
                SELECT RAISE(ABORT, 'verification records are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS checkpoints_no_update
            BEFORE UPDATE ON checkpoints BEGIN
                SELECT RAISE(ABORT, 'checkpoints are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS checkpoints_no_delete
            BEFORE DELETE ON checkpoints BEGIN
                SELECT RAISE(ABORT, 'checkpoints are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS transitions_no_update
            BEFORE UPDATE ON intention_transitions BEGIN
                SELECT RAISE(ABORT, 'transitions are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS transitions_no_delete
            BEFORE DELETE ON intention_transitions BEGIN
                SELECT RAISE(ABORT, 'transitions are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS idempotency_no_update
            BEFORE UPDATE ON command_idempotency BEGIN
                SELECT RAISE(ABORT, 'idempotency records are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS idempotency_no_delete
            BEFORE DELETE ON command_idempotency BEGIN
                SELECT RAISE(ABORT, 'idempotency records are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS derivations_no_update
            BEFORE UPDATE ON derivations BEGIN
                SELECT RAISE(ABORT, 'derivations are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS derivations_no_delete
            BEFORE DELETE ON derivations BEGIN
                SELECT RAISE(ABORT, 'derivations are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS suggestion_consumptions_no_update
            BEFORE UPDATE ON suggestion_consumptions BEGIN
                SELECT RAISE(ABORT, 'suggestion consumptions are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS suggestion_consumptions_no_delete
            BEFORE DELETE ON suggestion_consumptions BEGIN
                SELECT RAISE(ABORT, 'suggestion consumptions are immutable');
            END;
            """
        )

    def _acquire_writer_lease(self) -> None:
        lock_path = self.database_path.with_suffix(".writer.lock")
        handle = open(lock_path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                if handle.tell() == handle.seek(0, os.SEEK_END) == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise IntentionStoreConflictError("another intention writer is active") from exc
        self._lease_handle = handle

    def _release_writer_lease(self) -> None:
        handle = self._lease_handle
        if handle is None:
            return
        self._lease_handle = None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @staticmethod
    def _normalize_sqlite_error(exc: BaseException) -> BaseException:
        if isinstance(exc, sqlite3.OperationalError) and (
            "locked" in str(exc).lower() or "busy" in str(exc).lower()
        ):
            return IntentionStoreBusyError(str(exc))
        if isinstance(exc, sqlite3.IntegrityError):
            return IntentionStoreConflictError(str(exc))
        return exc


__all__ = [
    "DATABASE_RELATIVE_PATH",
    "STORE_SCHEMA_VERSION",
    "ConsumeSuggestionCommand",
    "IntegrityReport",
    "IntentionStore",
    "IntentionStoreBusyError",
    "IntentionStoreClosedError",
    "IntentionStoreConflictError",
    "IntentionStoreError",
    "IntentionStoreIdempotencyConflictError",
    "IntentionStoreIntegrityError",
    "IntentionStoreQueueFullError",
    "PersistCheckpointCommand",
    "PersistCommitmentCommand",
    "PersistIntentionCommand",
    "PersistSuggestionBudgetCommand",
    "PersistVerificationCommand",
    "RecordDerivationCommand",
    "StorePriority",
    "StoreResult",
]
