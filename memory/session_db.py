"""SQLite event store for long-running JARVIS sessions."""

from __future__ import annotations

import json
import math
import queue
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.events import Event, EventBus
from core.life.contracts import PrivacyClass
from core.life.privacy import PayloadRejected, PrivacyPolicy


_GENERIC_EVENT_FIELDS = frozenset(
    {
        "code",
        "count",
        "key",
        "kind",
        "name",
        "ok",
        "state",
        "status",
        "step",
        "summary",
        "success",
        "value",
        "version",
    }
)
_TOOL_EVENT_FIELDS = frozenset(
    {
        "category",
        "confirmed",
        "duration_ms",
        "latency_ms",
        "params",
        "success",
        "task",
        "tool",
    }
)
_SAFE_TOOL_PARAM_FIELDS = frozenset(
    {
        "language",
        "limit",
        "mode",
        "model",
        "query",
        "selector",
        "url",
    }
)
_EVENT_FIELDS = {
    "user.preference": frozenset({"key", "value"}),
    "permission.changed": frozenset({"permission"}),
    "runtime.status": frozenset({"status"}),
    "subsystem.registered": frozenset({"name"}),
}
_IGNORED_SESSION_EVENTS = frozenset(
    {
        "event_store.registered",
        "memory.candidate.applied",
    }
)


@dataclass(frozen=True)
class _SessionEventRecord:
    event: Event
    index_summary: str


class SessionEventStore:
    """Persists EventBus events for future memory consolidation."""

    def __init__(self, path: str | Path, *, capacity: int = 1_024):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("capacity must be a positive integer")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity
        self._privacy = PrivacyPolicy()
        self._queue: queue.Queue[_SessionEventRecord] = queue.Queue(capacity)
        self._lock = threading.RLock()
        self._stop_requested = threading.Event()
        self._worker: threading.Thread | None = None
        self._attached_bus_ids: set[int] = set()
        self._accepting = True
        self._state = "paused"
        self._seen_event_ids: set[str] = set()
        self._accepted = 0
        self._persisted = 0
        self._duplicates = 0
        self._dropped = 0
        self._write_failures = 0
        self._last_error: str | None = None
        self._init_db()
        with closing(self._connect()) as db:
            rows = db.execute("SELECT event_id FROM events").fetchall()
        self._seen_event_ids.update(str(row["event_id"]) for row in rows)

    def attach(self, bus: EventBus) -> bool:
        if not isinstance(bus, EventBus):
            raise TypeError("bus must be an EventBus")
        with self._lock:
            if not self._accepting or id(bus) in self._attached_bus_ids:
                return False
            self._start_worker_locked()
            self._attached_bus_ids.add(id(bus))
        bus.subscribe("*", self.record_event)
        return True

    def record_event(self, event: Event) -> bool:
        if not isinstance(event, Event):
            return False
        if not self._valid_event_metadata(event):
            return False
        if event.type in _IGNORED_SESSION_EVENTS:
            return False
        record = self._redact_event(event)
        with self._lock:
            if not self._accepting:
                return False
            if record is None:
                self._dropped += 1
                return False
            if record.event.id in self._seen_event_ids:
                self._duplicates += 1
                return False
            if self._worker is None:
                self._start_worker_locked()
            try:
                self._queue.put_nowait(record)
            except queue.Full:
                self._dropped += 1
                return False
            self._seen_event_ids.add(record.event.id)
            self._accepted += 1
            return True

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

    def close(self, timeout: float = 2.0) -> bool:
        timeout = max(0.0, float(timeout))
        deadline = time.monotonic() + timeout
        with self._lock:
            if self._state == "stopped":
                return True
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

    def _record_event_db(self, record: _SessionEventRecord) -> int:
        event = record.event
        with closing(self._connect()) as db:
            with db:
                cursor = db.execute(
                    """
                    INSERT OR IGNORE INTO events
                        (event_id, type, source, timestamp, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.type,
                        event.source,
                        event.timestamp,
                        json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                    ),
                )
                if cursor.rowcount and record.index_summary:
                    self._index_memory_text_db(
                        db,
                        "event",
                        event.id,
                        record.index_summary,
                        [event.id],
                    )
                return int(cursor.rowcount)

    def recent_events(self, limit: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
        if self._state == "running":
            self.flush(timeout=2.0)
        limit = max(1, min(int(limit), 500))
        params: list[Any] = []
        where = ""
        if event_type:
            where = "WHERE type = ?"
            params.append(event_type)
        params.append(limit)
        with closing(self._connect()) as db:
            rows = db.execute(
                    f"""
                    SELECT event_id, type, source, timestamp, payload_json
                    FROM events
                    {where}
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        rows.reverse()
        return [self._row_to_dict(row) for row in rows]

    def upsert_memory_candidate(
        self,
        kind: str,
        content: str,
        evidence_ids: list[str],
        confidence: float = 0.5,
        status: str = "candidate",
    ) -> bool:
        import hashlib
        import time

        candidate_id = hashlib.sha256(f"{kind}:{content}".encode("utf-8")).hexdigest()[:24]
        evidence = sorted(set(evidence_ids))
        now = time.time()
        with closing(self._connect()) as db:
            existing = db.execute(
                "SELECT evidence_json FROM memory_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing:
                merged = sorted(set(json.loads(existing["evidence_json"]) + evidence))
                with db:
                    db.execute(
                        """
                        UPDATE memory_candidates
                        SET evidence_json = ?, evidence_count = ?, confidence = ?, last_seen_at = ?
                        WHERE candidate_id = ?
                        """,
                        (json.dumps(merged, ensure_ascii=False), len(merged), confidence, now, candidate_id),
                    )
                    self._index_memory_text_db(db, "candidate", candidate_id, content, merged)
                return False
            with db:
                db.execute(
                    """
                    INSERT INTO memory_candidates
                        (candidate_id, kind, status, content, confidence, evidence_json,
                         evidence_count, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        kind,
                        status,
                        content,
                        confidence,
                        json.dumps(evidence, ensure_ascii=False),
                        len(evidence),
                        now,
                        now,
                    ),
                )
                self._index_memory_text_db(db, "candidate", candidate_id, content, evidence)
            return True

    def memory_candidates(
        self,
        kind: str | None = None,
        status: str | None = "candidate",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        clauses = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with closing(self._connect()) as db:
            rows = db.execute(
                f"""
                SELECT candidate_id, kind, status, content, confidence, evidence_json,
                       evidence_count, created_at, last_seen_at
                FROM memory_candidates
                {where}
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        rows.reverse()
        return [self._candidate_row_to_dict(row) for row in rows]

    def upsert_evolution_candidate(
        self,
        kind: str,
        title: str,
        description: str,
        evidence_ids: list[str],
        confidence: float = 0.5,
        risk: str = "trusted",
        proposal: dict[str, Any] | None = None,
        status: str = "candidate",
    ) -> bool:
        import hashlib
        import time

        candidate_id = hashlib.sha256(f"{kind}:{title}:{description}".encode("utf-8")).hexdigest()[:24]
        evidence = sorted(set(evidence_ids))
        now = time.time()
        proposal_json = json.dumps(proposal or {}, ensure_ascii=False, sort_keys=True)
        with closing(self._connect()) as db:
            existing = db.execute(
                "SELECT evidence_json FROM evolution_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if existing:
                merged = sorted(set(json.loads(existing["evidence_json"]) + evidence))
                with db:
                    db.execute(
                        """
                        UPDATE evolution_candidates
                        SET description = ?, confidence = ?, risk = ?, proposal_json = ?,
                            validation_status = 'pending', validation_json = '{}',
                            evidence_json = ?, evidence_count = ?, last_seen_at = ?
                        WHERE candidate_id = ?
                        """,
                        (
                            description,
                            confidence,
                            risk,
                            proposal_json,
                            json.dumps(merged, ensure_ascii=False),
                            len(merged),
                            now,
                            candidate_id,
                        ),
                    )
                return False
            with db:
                db.execute(
                    """
                    INSERT INTO evolution_candidates
                        (candidate_id, kind, status, title, description, confidence, risk,
                         proposal_json, validation_status, validation_json,
                         evidence_json, evidence_count, created_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate_id,
                        kind,
                        status,
                        title,
                        description,
                        confidence,
                        risk,
                        proposal_json,
                        "pending",
                        "{}",
                        json.dumps(evidence, ensure_ascii=False),
                        len(evidence),
                        now,
                        now,
                    ),
                )
            return True

    def evolution_candidates(
        self,
        kind: str | None = None,
        status: str | None = "candidate",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        clauses = []
        params: list[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with closing(self._connect()) as db:
            rows = db.execute(
                f"""
                SELECT candidate_id, kind, status, title, description, confidence, risk,
                       proposal_json, validation_status, validation_json,
                       evidence_json, evidence_count, created_at, last_seen_at
                FROM evolution_candidates
                {where}
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        rows.reverse()
        return [self._evolution_candidate_row_to_dict(row) for row in rows]

    def update_memory_candidate_status(self, candidate_id: str, status: str) -> bool:
        allowed = {"candidate", "active", "rejected", "archived"}
        if status not in allowed:
            return False
        with closing(self._connect()) as db:
            with db:
                cursor = db.execute(
                    "UPDATE memory_candidates SET status = ? WHERE candidate_id = ?",
                    (status, candidate_id),
                )
            return cursor.rowcount > 0

    def update_evolution_candidate_status(self, candidate_id: str, status: str) -> bool:
        allowed = {"candidate", "staged", "active", "rejected", "archived"}
        if status not in allowed:
            return False
        with closing(self._connect()) as db:
            current = db.execute(
                "SELECT status, validation_status FROM evolution_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if not current:
                return False
            current_status = current["status"]
            if status == "active" and current_status != "staged":
                return False
            if status == "staged" and current["validation_status"] != "passed":
                return False
            with db:
                cursor = db.execute(
                    "UPDATE evolution_candidates SET status = ? WHERE candidate_id = ?",
                    (status, candidate_id),
                )
            return cursor.rowcount > 0

    def validate_evolution_candidate(self, candidate_id: str) -> dict[str, Any]:
        with closing(self._connect()) as db:
            row = db.execute(
                """
                SELECT candidate_id, proposal_json, risk
                FROM evolution_candidates
                WHERE candidate_id = ?
                """,
                (candidate_id,),
            ).fetchone()
            if not row:
                return {"ok": False, "status": "missing", "reason": "candidate not found"}
            proposal = json.loads(row["proposal_json"])
            validation = self._validate_evolution_proposal(proposal, row["risk"])
            with db:
                db.execute(
                    """
                    UPDATE evolution_candidates
                    SET validation_status = ?, validation_json = ?
                    WHERE candidate_id = ?
                    """,
                    (
                        validation["status"],
                        json.dumps(validation, ensure_ascii=False, sort_keys=True),
                        candidate_id,
                    ),
                )
            return {"ok": validation["status"] == "passed", **validation}

    def record_evolution_performance(
        self,
        candidate_id: str,
        success: bool,
        latency_ms: float | None = None,
        quality_score: float | None = None,
        window: int = 5,
        max_failure_rate: float = 0.5,
        max_latency_ms: float | None = None,
        min_quality_score: float | None = None,
    ) -> dict[str, Any]:
        import time

        window = max(2, min(int(window or 5), 50))
        max_failure_rate = max(0.0, min(1.0, float(max_failure_rate)))
        with closing(self._connect()) as db:
            candidate = db.execute(
                "SELECT status FROM evolution_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            if not candidate:
                return {"ok": False, "rolled_back": False, "reason": "candidate not found"}
            with db:
                db.execute(
                    """
                    INSERT INTO evolution_performance
                        (candidate_id, success, latency_ms, quality_score, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (candidate_id, 1 if success else 0, latency_ms, quality_score, time.time()),
                )
            rows = db.execute(
                """
                SELECT success, latency_ms, quality_score
                FROM evolution_performance
                WHERE candidate_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (candidate_id, window),
            ).fetchall()
            samples = len(rows)
            failures = len([row for row in rows if int(row["success"]) == 0])
            failure_rate = failures / samples if samples else 0.0
            latencies = [float(row["latency_ms"]) for row in rows if row["latency_ms"] is not None]
            qualities = [float(row["quality_score"]) for row in rows if row["quality_score"] is not None]
            avg_latency_ms = sum(latencies) / len(latencies) if latencies else None
            avg_quality_score = sum(qualities) / len(qualities) if qualities else None
            rollback_reasons = []
            if failure_rate > max_failure_rate:
                rollback_reasons.append("failure_rate")
            if max_latency_ms is not None and avg_latency_ms is not None and avg_latency_ms > float(max_latency_ms):
                rollback_reasons.append("latency")
            if (
                min_quality_score is not None
                and avg_quality_score is not None
                and avg_quality_score < float(min_quality_score)
            ):
                rollback_reasons.append("quality")
            rolled_back = False
            if candidate["status"] == "active" and samples >= window and rollback_reasons:
                with db:
                    db.execute(
                        "UPDATE evolution_candidates SET status = ? WHERE candidate_id = ?",
                        ("staged", candidate_id),
                    )
                rolled_back = True
            return {
                "ok": True,
                "candidate_id": candidate_id,
                "samples": samples,
                "failures": failures,
                "failure_rate": round(failure_rate, 3),
                "avg_latency_ms": round(avg_latency_ms, 1) if avg_latency_ms is not None else None,
                "avg_quality_score": round(avg_quality_score, 3) if avg_quality_score is not None else None,
                "rollback_reasons": rollback_reasons,
                "rolled_back": rolled_back,
            }

    def recall(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            return []
        limit = max(1, min(int(limit), 100))
        with closing(self._connect()) as db:
            try:
                rows = db.execute(
                    """
                    SELECT ref_kind, ref_id, content, evidence_json
                    FROM memory_fts
                    WHERE memory_fts MATCH ?
                    LIMIT ?
                    """,
                    (self._fts_query(query), limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = db.execute(
                    """
                    SELECT ref_kind, ref_id, content, evidence_json
                    FROM memory_fts
                    WHERE content LIKE ?
                    LIMIT ?
                    """,
                    (f"%{query}%", limit),
                ).fetchall()
        return [self._recall_row_to_dict(row, query) for row in rows]

    def _start_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_requested.clear()
        self._state = "running"
        self._worker = threading.Thread(
            target=self._worker_loop,
            name="javis-session-event-store",
            daemon=True,
        )
        self._worker.start()

    def _worker_loop(self) -> None:
        while True:
            if self._stop_requested.is_set() and self._queue.empty():
                return
            try:
                record = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            succeeded = False
            inserted = 0
            for attempt in range(3):
                try:
                    inserted = int(self._record_event_db(record) or 0)
                    succeeded = True
                    break
                except Exception as exc:
                    with self._lock:
                        self._write_failures += 1
                        self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                    if attempt < 2:
                        time.sleep(0.01 * (attempt + 1))
            with self._lock:
                if succeeded:
                    self._persisted += inserted
                else:
                    self._seen_event_ids.discard(record.event.id)
                    self._dropped += 1
            self._queue.task_done()

    def _redact_event(self, event: Event) -> _SessionEventRecord | None:
        event_type = event.type if type(event.type) is str else "invalid.event"
        privacy, _ = self._privacy.classify(event_type, event.payload)
        if privacy in {PrivacyClass.SECRET, PrivacyClass.BIOMETRIC}:
            return None
        allowed_fields = self._allowed_fields(event_type)
        try:
            payload = self._privacy.redact_allowlisted(
                event.payload,
                allowed_fields=allowed_fields,
            ).payload
        except (PayloadRejected, TypeError, ValueError):
            payload = {"diagnostic": "payload_rejected"}
        if event_type.startswith("tool.") and isinstance(payload.get("params"), dict):
            payload["params"] = {
                key: value
                for key, value in payload["params"].items()
                if key in _SAFE_TOOL_PARAM_FIELDS
                and (value is None or type(value) in {bool, int, float, str})
            }
        elif event_type not in _EVENT_FIELDS:
            payload = {
                key: value
                for key, value in payload.items()
                if value is None or type(value) in {bool, int, float, str}
            }
        redacted = Event(
            id=event.id,
            type=event_type,
            payload=payload,
            source=event.source,
            timestamp=event.timestamp,
            schema_version=event.schema_version,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            sequence=event.sequence,
        )
        return _SessionEventRecord(
            event=redacted,
            index_summary=self._index_summary(redacted),
        )

    @staticmethod
    def _allowed_fields(event_type: str) -> frozenset[str]:
        if event_type.startswith("tool."):
            return _TOOL_EVENT_FIELDS
        return _EVENT_FIELDS.get(event_type, _GENERIC_EVENT_FIELDS)

    @staticmethod
    def _valid_event_metadata(event: Event) -> bool:
        text_fields = (
            (event.id, 256),
            (event.type, 128),
            (event.source, 256),
        )
        for value, limit in text_fields:
            if type(value) is not str or not value or len(value) > limit:
                return False
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                return False
            if any(ord(character) < 32 for character in value):
                return False
        if isinstance(event.timestamp, bool) or not isinstance(
            event.timestamp,
            (int, float),
        ):
            return False
        if not math.isfinite(float(event.timestamp)):
            return False
        return type(event.sequence) is int and event.sequence >= 0

    @staticmethod
    def _index_summary(event: Event) -> str:
        payload = event.payload
        if event.type == "user.preference":
            return " ".join(
                str(value)
                for value in (
                    event.type,
                    payload.get("key", ""),
                    payload.get("value", ""),
                )
                if value != ""
            )[:768]
        if event.type.startswith("tool."):
            return " ".join(
                str(value)
                for value in (
                    event.type,
                    payload.get("tool", ""),
                    payload.get("task", ""),
                    payload.get("success", ""),
                )
                if value != ""
            )[:768]
        if event.type.startswith("perception."):
            return f"{event.type} {payload.get('summary', '')}"[:768].strip()
        return f"{event.type} {event.source}"[:512]

    def status(self) -> dict[str, Any]:
        if self._state == "running":
            self.flush(timeout=0.5)
        with closing(self._connect()) as db:
            events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            candidates = db.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0]
            evolution_candidates = db.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[0]
        with self._lock:
            return {
                "state": self._state,
                "path": str(self.path),
                "events": int(events),
                "candidates": int(candidates),
                "evolution_candidates": int(evolution_candidates),
                "pending": self._queue.qsize(),
                "accepted": self._accepted,
                "persisted": self._persisted,
                "duplicates": self._duplicates,
                "dropped": self._dropped,
                "write_failures": self._write_failures,
                "last_error": self._last_error,
            }

    def _init_db(self) -> None:
        with closing(self._connect()) as db:
            with db:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL UNIQUE,
                        type TEXT NOT NULL,
                        source TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                db.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_candidates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        candidate_id TEXT NOT NULL UNIQUE,
                        kind TEXT NOT NULL,
                        status TEXT NOT NULL,
                        content TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        evidence_json TEXT NOT NULL,
                        evidence_count INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        last_seen_at REAL NOT NULL
                    )
                    """
                )
                db.execute("CREATE INDEX IF NOT EXISTS idx_memory_candidates_kind ON memory_candidates(kind)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_memory_candidates_status ON memory_candidates(status)")
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evolution_candidates (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        candidate_id TEXT NOT NULL UNIQUE,
                        kind TEXT NOT NULL,
                        status TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        risk TEXT NOT NULL,
                        proposal_json TEXT NOT NULL,
                        validation_status TEXT NOT NULL DEFAULT 'pending',
                        validation_json TEXT NOT NULL DEFAULT '{}',
                        evidence_json TEXT NOT NULL,
                        evidence_count INTEGER NOT NULL,
                        created_at REAL NOT NULL,
                        last_seen_at REAL NOT NULL
                    )
                    """
                )
                db.execute("CREATE INDEX IF NOT EXISTS idx_evolution_candidates_kind ON evolution_candidates(kind)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_evolution_candidates_status ON evolution_candidates(status)")
                self._ensure_column(db, "evolution_candidates", "validation_status", "TEXT NOT NULL DEFAULT 'pending'")
                self._ensure_column(db, "evolution_candidates", "validation_json", "TEXT NOT NULL DEFAULT '{}'")
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evolution_performance (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        candidate_id TEXT NOT NULL,
                        success INTEGER NOT NULL,
                        latency_ms REAL,
                        quality_score REAL,
                        created_at REAL NOT NULL
                    )
                    """
                )
                self._ensure_column(db, "evolution_performance", "quality_score", "REAL")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_evolution_performance_candidate ON evolution_performance(candidate_id)"
                )
                try:
                    db.execute(
                        """
                        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                        USING fts5(ref_kind UNINDEXED, ref_id UNINDEXED, content, evidence_json UNINDEXED)
                        """
                    )
                except sqlite3.OperationalError:
                    db.execute(
                        """
                        CREATE TABLE IF NOT EXISTS memory_fts (
                            ref_kind TEXT NOT NULL,
                            ref_id TEXT NOT NULL,
                            content TEXT NOT NULL,
                            evidence_json TEXT NOT NULL
                        )
                        """
                    )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "type": row["type"],
            "source": row["source"],
            "timestamp": row["timestamp"],
            "payload": json.loads(row["payload_json"]),
        }

    @staticmethod
    def _candidate_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "candidate_id": row["candidate_id"],
            "kind": row["kind"],
            "status": row["status"],
            "content": row["content"],
            "confidence": row["confidence"],
            "evidence_ids": json.loads(row["evidence_json"]),
            "evidence_count": row["evidence_count"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
        }

    @staticmethod
    def _evolution_candidate_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "candidate_id": row["candidate_id"],
            "kind": row["kind"],
            "status": row["status"],
            "title": row["title"],
            "description": row["description"],
            "confidence": row["confidence"],
            "risk": row["risk"],
            "proposal": json.loads(row["proposal_json"]),
            "validation": json.loads(row["validation_json"]),
            "evidence_ids": json.loads(row["evidence_json"]),
            "evidence_count": row["evidence_count"],
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
        }

    def _validate_evolution_proposal(self, proposal: dict[str, Any], risk: str) -> dict[str, Any]:
        task = str(proposal.get("task", "") or "").strip()
        tool = str(proposal.get("tool", "") or "").strip()
        code = str(proposal.get("code", "") or "")
        language = str(proposal.get("language", "python") or "python")
        if not task or not tool:
            return {"status": "failed", "reason": "proposal requires task and tool", "checks": ["metadata"]}
        if str(risk or "").lower() == "root":
            return {"status": "failed", "reason": "root-risk candidates require manual review", "checks": ["risk"]}
        if code:
            try:
                from tools.sandbox import CodeValidator

                safe, reason = CodeValidator.validate(code, language=language)
            except Exception as exc:
                return {"status": "failed", "reason": f"sandbox validator unavailable: {exc}", "checks": ["sandbox"]}
            if not safe:
                return {"status": "failed", "reason": reason, "checks": ["sandbox_static"]}
        return {
            "status": "passed",
            "reason": "metadata and static sandbox checks passed",
            "checks": ["metadata", "sandbox_static" if code else "metadata_only"],
        }

    def _index_memory_text_db(
        self,
        db: sqlite3.Connection,
        ref_kind: str,
        ref_id: str,
        content: str,
        evidence_ids: list[str],
    ) -> None:
        db.execute("DELETE FROM memory_fts WHERE ref_kind = ? AND ref_id = ?", (ref_kind, ref_id))
        db.execute(
            """
            INSERT INTO memory_fts (ref_kind, ref_id, content, evidence_json)
            VALUES (?, ?, ?, ?)
            """,
            (ref_kind, ref_id, content, json.dumps(evidence_ids, ensure_ascii=False)),
        )

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = [term.replace('"', '""') for term in query.split() if term.strip()]
        if not terms:
            return '""'
        return " OR ".join(f'"{term}"' for term in terms)

    @staticmethod
    def _recall_row_to_dict(row: sqlite3.Row, query: str) -> dict[str, Any]:
        kind = "candidate" if row["ref_kind"] == "candidate" else "event"
        return {
            "query": query,
            "kind": kind,
            "ref_kind": row["ref_kind"],
            "ref_id": row["ref_id"],
            "content": row["content"],
            "evidence_ids": json.loads(row["evidence_json"]),
            "why": f"Matched '{query}' in {kind} text indexed from {row['ref_id']}",
        }
