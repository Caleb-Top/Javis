"""Bounded asynchronous persistence for governed life events."""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .contracts import LifeEvent, PrivacyClass, RetentionClass
from .privacy import PrivacyPolicy


_CRITICAL_RETENTION = frozenset(
    {
        RetentionClass.CONTINUITY,
        RetentionClass.MEMORY_CANDIDATE,
        RetentionClass.AUDIT,
    }
)
_CRITICAL_EVENT_PARTS = (
    "identity",
    "approval",
    "deletion",
    "recovery",
    "action_result",
    "action.result",
)
_EVICTABLE_RETENTION = frozenset(
    {
        RetentionClass.EPHEMERAL,
        RetentionClass.SESSION,
    }
)


class LifeEventJournal:
    """Persist LifeEvent envelopes without running SQLite on EventBus threads."""

    def __init__(
        self,
        path: str | Path,
        *,
        capacity: int = 512,
        batch_size: int = 32,
        max_write_retries: int = 2,
        privacy_policy: PrivacyPolicy | None = None,
    ) -> None:
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if type(max_write_retries) is not int or max_write_retries < 0:
            raise ValueError("max_write_retries must be a non-negative integer")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity
        self.batch_size = min(batch_size, capacity)
        self.max_write_retries = max_write_retries
        self.privacy_policy = privacy_policy or PrivacyPolicy()
        self._queue: queue.Queue[LifeEvent] = queue.Queue(maxsize=capacity)
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._worker: threading.Thread | None = None
        self._state = "paused"
        self._accepting = True
        self._seen_event_ids: set[str] = set()
        self._accepted = 0
        self._persisted = 0
        self._duplicates = 0
        self._coalesced = 0
        self._write_failures = 0
        self._dropped_write_failure = 0
        self._dropped_never_persist = 0
        self._dropped_ephemeral = 0
        self._dropped_session = 0
        self._dropped_operational = 0
        self._dropped_critical = 0
        self._last_error: str | None = None
        self._init_db()
        self._seen_event_ids.update(self._load_event_ids())
        self._cursor = self._load_cursor()

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._cursor

    def start(self) -> bool:
        with self._lock:
            if self._state == "stopped":
                return False
            if self._worker is not None and self._worker.is_alive():
                return False
            self._stop_requested.clear()
            self._state = "running"
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="javis-life-journal",
                daemon=True,
            )
            self._worker.start()
            return True

    def enqueue(self, event: LifeEvent) -> bool:
        if not isinstance(event, LifeEvent):
            raise TypeError("event must be a LifeEvent")
        with self._lock:
            if not self._accepting:
                return False
            if (
                event.retention_class is RetentionClass.NEVER_PERSIST
                or event.privacy_class in {PrivacyClass.SECRET, PrivacyClass.BIOMETRIC}
            ):
                self._dropped_never_persist += 1
                return False
            if event.event_id in self._seen_event_ids:
                self._duplicates += 1
                return False
            if self._replace_coalesced_locked(event):
                self._seen_event_ids.add(event.event_id)
                self._accepted += 1
                self._coalesced += 1
                return True
            if self._queue.full():
                evicted = None
                if self._is_critical(event):
                    evicted = self._evict_low_value_locked()
                if evicted is None:
                    self._record_drop_locked(event)
                    return False
                self._record_drop_locked(evicted)
                self._seen_event_ids.discard(evicted.event_id)
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                self._record_drop_locked(event)
                return False
            self._seen_event_ids.add(event.event_id)
            self._accepted += 1
            return True

    def pending_event_ids(self) -> tuple[str, ...]:
        with self._queue.mutex:
            return tuple(event.event_id for event in self._queue.queue)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if self._state == "running":
            self.flush(timeout=2.0)
        with closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT schema_version, event_id, event_type, timestamp_utc,
                       monotonic_offset_ms, source, source_event_id, session_id,
                       request_id, correlation_id, causation_id, sequence,
                       identity_id, instance_id, payload_json, privacy_class,
                       retention_class, confidence, provenance_json,
                       redaction_summary_json
                FROM events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        rows.reverse()
        return [self._row_to_dict(row) for row in rows]

    def status(self) -> dict[str, Any]:
        with self._lock:
            worker_alive = self._worker is not None and self._worker.is_alive()
            return {
                "state": self._state,
                "path": str(self.path),
                "capacity": self.capacity,
                "pending": self._queue.qsize(),
                "worker_alive": worker_alive,
                "accepted": self._accepted,
                "persisted": self._persisted,
                "duplicates": self._duplicates,
                "coalesced": self._coalesced,
                "write_failures": self._write_failures,
                "dropped_write_failure": self._dropped_write_failure,
                "dropped_never_persist": self._dropped_never_persist,
                "dropped_ephemeral": self._dropped_ephemeral,
                "dropped_session": self._dropped_session,
                "dropped_operational": self._dropped_operational,
                "dropped_critical": self._dropped_critical,
                "last_error": self._last_error,
            }

    def flush(self, timeout: float = 2.0) -> bool:
        timeout = max(0.0, float(timeout))
        deadline = time.monotonic() + timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._queue.all_tasks_done.wait(remaining)
        return True

    def stop(self, timeout: float = 2.0) -> bool:
        timeout = max(0.0, float(timeout))
        deadline = time.monotonic() + timeout
        with self._lock:
            if self._state == "stopped":
                return True
            if self._worker is None and self._queue.qsize():
                self.start()
            self._accepting = False
            worker = self._worker
        flushed = self.flush(timeout=max(0.0, deadline - time.monotonic()))
        self._stop_requested.set()
        if worker is not None:
            worker.join(max(0.0, deadline - time.monotonic()))
        stopped = worker is None or not worker.is_alive()
        with self._lock:
            self._state = "stopped" if flushed and stopped else "degraded"
        return flushed and stopped

    def _worker_loop(self) -> None:
        while True:
            if self._stop_requested.is_set() and self._queue.empty():
                return
            try:
                first = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < self.batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            inserted = 0
            succeeded = False
            for attempt in range(self.max_write_retries + 1):
                try:
                    result = self._write_batch(batch)
                    inserted = int(result or 0)
                    succeeded = True
                    break
                except Exception as exc:
                    with self._lock:
                        self._write_failures += 1
                        self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                    if attempt < self.max_write_retries:
                        time.sleep(min(0.05, 0.01 * (attempt + 1)))
            with self._lock:
                if succeeded:
                    self._persisted += inserted
                    self._cursor += inserted
                else:
                    self._dropped_write_failure += len(batch)
                    self._state = "degraded"
                    for event in batch:
                        self._seen_event_ids.discard(event.event_id)
            for _ in batch:
                self._queue.task_done()

    def _write_batch(self, batch: list[LifeEvent]) -> int:
        inserted = 0
        with closing(self._connect()) as db:
            with db:
                for event in batch:
                    if event.retention_class is RetentionClass.NEVER_PERSIST:
                        continue
                    cursor = db.execute(
                        """
                        INSERT OR IGNORE INTO events (
                            schema_version, event_id, event_type, timestamp_utc,
                            monotonic_offset_ms, source, source_event_id, session_id,
                            request_id, correlation_id, causation_id, sequence,
                            identity_id, instance_id, payload_json, privacy_class,
                            retention_class, confidence, provenance_json,
                            redaction_summary_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        self._event_values(event),
                    )
                    if not cursor.rowcount:
                        continue
                    inserted += 1
                    summary = self.privacy_policy.index_summary(
                        event.event_type,
                        event.payload,
                    )
                    if summary:
                        db.execute(
                            """
                            INSERT INTO event_summaries (event_id, summary)
                            VALUES (?, ?)
                            """,
                            (event.event_id, summary),
                        )
                        db.execute(
                            """
                            INSERT INTO life_event_fts (event_id, summary)
                            VALUES (?, ?)
                            """,
                            (event.event_id, summary),
                        )
        return inserted

    def _replace_coalesced_locked(self, event: LifeEvent) -> bool:
        key = self._coalescing_key(event)
        if key is None:
            return False
        with self._queue.mutex:
            for index, pending in enumerate(self._queue.queue):
                if self._coalescing_key(pending) == key:
                    self._queue.queue[index] = event
                    return True
        return False

    def _evict_low_value_locked(self) -> LifeEvent | None:
        with self._queue.mutex:
            for index, pending in enumerate(self._queue.queue):
                if pending.retention_class not in _EVICTABLE_RETENTION:
                    continue
                del self._queue.queue[index]
                self._queue.unfinished_tasks -= 1
                self._queue.not_full.notify()
                return pending
        return None

    def _record_drop_locked(self, event: LifeEvent) -> None:
        if self._is_critical(event):
            self._dropped_critical += 1
        elif event.retention_class is RetentionClass.EPHEMERAL:
            self._dropped_ephemeral += 1
        elif event.retention_class is RetentionClass.SESSION:
            self._dropped_session += 1
        else:
            self._dropped_operational += 1

    @staticmethod
    def _is_critical(event: LifeEvent) -> bool:
        event_type = event.event_type.casefold()
        is_action_result = (
            event_type.startswith(("life.tool.", "life.request.", "life.action."))
            and event_type.endswith((".completed", ".failed", ".cancelled"))
        )
        return (
            event.retention_class in _CRITICAL_RETENTION
            or any(part in event_type for part in _CRITICAL_EVENT_PARTS)
            or is_action_result
        )

    @staticmethod
    def _coalescing_key(event: LifeEvent) -> tuple[str, str] | None:
        if event.correlation_id is None:
            return None
        event_type = event.event_type.casefold()
        is_surface = (
            event.privacy_class is PrivacyClass.PUBLIC_SURFACE
            or event_type.startswith("life.surface.")
            or event_type.startswith("life.expression.")
        )
        if not is_surface:
            return None
        return event.event_type, event.correlation_id

    def _init_db(self) -> None:
        with closing(self._connect()) as db:
            with db:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        schema_version INTEGER NOT NULL,
                        event_id TEXT NOT NULL UNIQUE,
                        event_type TEXT NOT NULL,
                        timestamp_utc TEXT NOT NULL,
                        monotonic_offset_ms INTEGER NOT NULL,
                        source TEXT NOT NULL,
                        source_event_id TEXT,
                        session_id TEXT,
                        request_id TEXT,
                        correlation_id TEXT,
                        causation_id TEXT,
                        sequence INTEGER NOT NULL,
                        identity_id TEXT NOT NULL,
                        instance_id TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        privacy_class TEXT NOT NULL,
                        retention_class TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        provenance_json TEXT NOT NULL,
                        redaction_summary_json TEXT NOT NULL
                    )
                    """
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_life_events_type ON events(event_type)"
                )
                db.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_life_events_timestamp
                    ON events(timestamp_utc)
                    """
                )
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS event_summaries (
                        event_id TEXT NOT NULL UNIQUE,
                        summary TEXT NOT NULL
                    )
                    """
                )
                try:
                    db.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS life_event_fts
                        USING fts5(event_id UNINDEXED, summary)
                        """
                    )
                except sqlite3.OperationalError:
                    db.execute(
                        """
                        CREATE TABLE IF NOT EXISTS life_event_fts (
                            event_id TEXT NOT NULL,
                            summary TEXT NOT NULL
                        )
                        """
                    )

    def _load_event_ids(self) -> set[str]:
        with closing(self._connect()) as db:
            rows = db.execute("SELECT event_id FROM events").fetchall()
        return {str(row["event_id"]) for row in rows}

    def _load_cursor(self) -> int:
        with closing(self._connect()) as db:
            row = db.execute("SELECT COALESCE(MAX(id), 0) AS cursor FROM events").fetchone()
        return int(row["cursor"])

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _event_values(event: LifeEvent) -> tuple[Any, ...]:
        wire = event.to_dict()
        return (
            event.schema_version,
            event.event_id,
            event.event_type,
            event.timestamp_utc,
            event.monotonic_offset_ms,
            event.source,
            event.source_event_id,
            event.session_id,
            event.request_id,
            event.correlation_id,
            event.causation_id,
            event.sequence,
            event.identity_id,
            event.instance_id,
            json.dumps(wire["payload"], ensure_ascii=False, sort_keys=True),
            event.privacy_class.value,
            event.retention_class.value,
            event.confidence,
            json.dumps(wire["provenance"], ensure_ascii=False, sort_keys=True),
            json.dumps(wire["redaction_summary"], ensure_ascii=False),
        )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": row["schema_version"],
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "timestamp_utc": row["timestamp_utc"],
            "monotonic_offset_ms": row["monotonic_offset_ms"],
            "source": row["source"],
            "source_event_id": row["source_event_id"],
            "session_id": row["session_id"],
            "request_id": row["request_id"],
            "correlation_id": row["correlation_id"],
            "causation_id": row["causation_id"],
            "sequence": row["sequence"],
            "identity_id": row["identity_id"],
            "instance_id": row["instance_id"],
            "payload": json.loads(row["payload_json"]),
            "privacy_class": row["privacy_class"],
            "retention_class": row["retention_class"],
            "confidence": row["confidence"],
            "provenance": json.loads(row["provenance_json"]),
            "redaction_summary": json.loads(row["redaction_summary_json"]),
        }


__all__ = ["LifeEventJournal"]
