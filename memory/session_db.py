"""SQLite event store for long-running JARVIS sessions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from core.events import Event, EventBus


class SessionEventStore:
    """Persists EventBus events for future memory consolidation."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def attach(self, bus: EventBus) -> None:
        bus.subscribe("*", self.record_event)

    def record_event(self, event: Event) -> None:
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
                if cursor.rowcount:
                    self._index_memory_text_db(
                        db,
                        "event",
                        event.id,
                        f"{event.type} {event.source} {json.dumps(event.payload, ensure_ascii=False, sort_keys=True)}",
                        [event.id],
                    )

    def recent_events(self, limit: int = 50, event_type: str | None = None) -> list[dict[str, Any]]:
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

    def status(self) -> dict[str, Any]:
        with closing(self._connect()) as db:
            events = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            candidates = db.execute("SELECT COUNT(*) FROM memory_candidates").fetchone()[0]
            evolution_candidates = db.execute("SELECT COUNT(*) FROM evolution_candidates").fetchone()[0]
        return {
            "state": "running",
            "path": str(self.path),
            "events": int(events),
            "candidates": int(candidates),
            "evolution_candidates": int(evolution_candidates),
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
