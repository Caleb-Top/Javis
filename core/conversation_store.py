"""Durable conversation messages and replayable request events."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MESSAGE_ROLES = {"user", "assistant"}
MESSAGE_STATES = {"complete", "interrupted"}
EVENT_TYPE_PATTERN = re.compile(r"[a-z0-9_.-]{1,128}")


class ConversationStoreError(ValueError):
    """Raised when conversation data is invalid or cannot be persisted safely."""


class ConversationStore:
    """Stores authoritative conversation history and ordered session events."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS conversation_meta "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES conversations(session_id)
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_messages_session "
                "ON conversation_messages(session_id, id)"
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES conversations(session_id)
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_conversation_events_session "
                "ON conversation_events(session_id, sequence)"
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS request_keys (
                    session_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(session_id, idempotency_key),
                    FOREIGN KEY(session_id) REFERENCES conversations(session_id)
                )
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO conversation_meta(key, value) VALUES(?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )

    def ensure_session(self, session_id: str) -> dict[str, Any]:
        normalized = _identifier(session_id, "session_id")
        now = time.time()
        with self._lock, closing(self._connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO conversations(session_id, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (normalized, now, now),
            )
            row = db.execute(
                "SELECT session_id, created_at, updated_at FROM conversations WHERE session_id=?",
                (normalized,),
            ).fetchone()
        return dict(row)

    def append_message(
        self,
        session_id: str,
        request_id: str,
        role: str,
        content: str,
        *,
        status: str = "complete",
    ) -> dict[str, Any]:
        session = _identifier(session_id, "session_id")
        request = _identifier(request_id, "request_id")
        normalized_role = str(role or "").strip().lower()
        normalized_status = str(status or "").strip().lower()
        normalized_content = str(content or "").strip()
        if normalized_role not in MESSAGE_ROLES:
            raise ConversationStoreError(f"invalid message role: {role}")
        if normalized_status not in MESSAGE_STATES:
            raise ConversationStoreError(f"invalid message status: {status}")
        if not normalized_content:
            raise ConversationStoreError("message content is required")
        now = time.time()
        message_id = uuid.uuid4().hex
        with self._lock, closing(self._connect()) as db, db:
            self._ensure_session_db(db, session, now)
            cursor = db.execute(
                """
                INSERT INTO conversation_messages(
                    message_id, session_id, request_id, role, content, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session,
                    request,
                    normalized_role,
                    normalized_content,
                    normalized_status,
                    now,
                ),
            )
            db.execute(
                "UPDATE conversations SET updated_at=? WHERE session_id=?",
                (now, session),
            )
            row = db.execute(
                "SELECT * FROM conversation_messages WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        return _message_dict(row)

    def accept_request(
        self,
        session_id: str,
        request_id: str,
        idempotency_key: str,
        content: str,
        accepted_payload: dict[str, Any],
        *,
        preceding_events: tuple[tuple[str, str, dict[str, Any]], ...] = (),
    ) -> dict[str, Any]:
        """Atomically claim, persist the user message, and accept the request."""

        session = _identifier(session_id, "session_id")
        request = _identifier(request_id, "request_id")
        key = _identifier(idempotency_key, "idempotency_key")
        text = str(content or "").strip()
        if not text:
            raise ConversationStoreError("message content is required")
        if not isinstance(accepted_payload, dict):
            raise ConversationStoreError("accepted event payload must be an object")
        prepared_events: list[tuple[str, str, str]] = []
        for prior_request_id, event_type, payload in preceding_events:
            prior_request = _identifier(prior_request_id, "request_id")
            normalized_type = str(event_type or "").strip().lower()
            if EVENT_TYPE_PATTERN.fullmatch(normalized_type) is None:
                raise ConversationStoreError(
                    f"invalid conversation event type: {event_type}"
                )
            if not isinstance(payload, dict):
                raise ConversationStoreError("event payload must be an object")
            prepared_events.append(
                (prior_request, normalized_type, _payload_json(payload))
            )
        accepted_json = _payload_json(accepted_payload)
        now = time.time()
        message_id = uuid.uuid4().hex
        accepted_event_id = uuid.uuid4().hex
        prior_event_ids = [uuid.uuid4().hex for _ in prepared_events]

        with self._lock, closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                self._ensure_session_db(db, session, now)
                existing = db.execute(
                    "SELECT request_id FROM request_keys "
                    "WHERE session_id=? AND idempotency_key=?",
                    (session, key),
                ).fetchone()
                if existing is None:
                    existing = db.execute(
                        "SELECT request_id FROM request_keys "
                        "WHERE session_id=? AND request_id=? ORDER BY created_at LIMIT 1",
                        (session, request),
                    ).fetchone()
                if existing is not None:
                    result = self._accepted_request_db(
                        db, session, str(existing["request_id"])
                    )
                    db.commit()
                    return result

                db.execute(
                    "INSERT INTO request_keys(session_id, idempotency_key, request_id, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (session, key, request, now),
                )
                message_cursor = db.execute(
                    """
                    INSERT INTO conversation_messages(
                        message_id, session_id, request_id, role, content, status, created_at
                    ) VALUES (?, ?, ?, 'user', ?, 'complete', ?)
                    """,
                    (message_id, session, request, text, now),
                )
                sequence = int(
                    db.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 "
                        "FROM conversation_events WHERE session_id=?",
                        (session,),
                    ).fetchone()[0]
                )
                persisted_prior: list[dict[str, Any]] = []
                for event_id, (prior_request, event_type, payload_json) in zip(
                    prior_event_ids, prepared_events
                ):
                    db.execute(
                        """
                        INSERT INTO conversation_events(
                            event_id, session_id, request_id, sequence, type,
                            timestamp, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_id,
                            session,
                            prior_request,
                            sequence,
                            event_type,
                            now,
                            payload_json,
                        ),
                    )
                    persisted_prior.append(
                        _event_values(
                            event_id,
                            session,
                            prior_request,
                            sequence,
                            event_type,
                            now,
                            payload_json,
                        )
                    )
                    sequence += 1
                db.execute(
                    """
                    INSERT INTO conversation_events(
                        event_id, session_id, request_id, sequence, type,
                        timestamp, payload_json
                    ) VALUES (?, ?, ?, ?, 'request.accepted', ?, ?)
                    """,
                    (
                        accepted_event_id,
                        session,
                        request,
                        sequence,
                        now,
                        accepted_json,
                    ),
                )
                db.execute(
                    "UPDATE conversations SET updated_at=? WHERE session_id=?",
                    (now, session),
                )
                message_row = db.execute(
                    "SELECT * FROM conversation_messages WHERE id=?",
                    (message_cursor.lastrowid,),
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {
            "accepted": True,
            "duplicate": False,
            "request_id": request,
            "message": _message_dict(message_row),
            "preceding_events": persisted_prior,
            "event": _event_values(
                accepted_event_id,
                session,
                request,
                sequence,
                "request.accepted",
                now,
                accepted_json,
            ),
        }

    def history(
        self,
        session_id: str,
        *,
        limit: int = 80,
        include_interrupted: bool = False,
    ) -> list[dict[str, Any]]:
        session = _identifier(session_id, "session_id")
        bounded_limit = _limit(limit, maximum=500)
        interruption_filter = (
            "" if include_interrupted else "AND NOT (role='assistant' AND status='interrupted')"
        )
        with self._lock, closing(self._connect()) as db:
            rows = db.execute(
                f"""
                SELECT * FROM conversation_messages
                WHERE session_id=? {interruption_filter}
                ORDER BY id DESC LIMIT ?
                """,
                (session, bounded_limit),
            ).fetchall()
        rows.reverse()
        return [_message_dict(row) for row in rows]

    def append_event(
        self,
        session_id: str,
        request_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        session = _identifier(session_id, "session_id")
        request = _identifier(request_id, "request_id", allow_empty=True)
        normalized_type = str(event_type or "").strip().lower()
        if EVENT_TYPE_PATTERN.fullmatch(normalized_type) is None:
            raise ConversationStoreError(f"invalid conversation event type: {event_type}")
        if not isinstance(payload, dict):
            raise ConversationStoreError("event payload must be an object")
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ConversationStoreError(f"event payload is not JSON serializable: {exc}") from exc

        now = time.time()
        event_id = uuid.uuid4().hex
        with self._lock, closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                self._ensure_session_db(db, session, now)
                sequence = int(
                    db.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 "
                        "FROM conversation_events WHERE session_id=?",
                        (session,),
                    ).fetchone()[0]
                )
                db.execute(
                    """
                    INSERT INTO conversation_events(
                        event_id, session_id, request_id, sequence, type, timestamp, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        session,
                        request,
                        sequence,
                        normalized_type,
                        now,
                        payload_json,
                    ),
                )
                db.execute(
                    "UPDATE conversations SET updated_at=? WHERE session_id=?",
                    (now, session),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "session_id": session,
            "request_id": request,
            "sequence": sequence,
            "type": normalized_type,
            "timestamp": now,
            "payload": json.loads(payload_json),
        }

    def events_after(
        self,
        session_id: str,
        sequence: int = 0,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        session = _identifier(session_id, "session_id")
        try:
            after = max(0, int(sequence))
        except (TypeError, ValueError) as exc:
            raise ConversationStoreError("event sequence must be an integer") from exc
        bounded_limit = _limit(limit, maximum=500)
        with self._lock, closing(self._connect()) as db:
            rows = db.execute(
                """
                SELECT event_id, session_id, request_id, sequence, type, timestamp, payload_json
                FROM conversation_events
                WHERE session_id=? AND sequence>?
                ORDER BY sequence ASC LIMIT ?
                """,
                (session, after, bounded_limit),
            ).fetchall()
        return [_event_dict(row) for row in rows]

    def claim_idempotency_key(
        self,
        session_id: str,
        key: str,
        request_id: str,
    ) -> tuple[str, bool]:
        session = _identifier(session_id, "session_id")
        normalized_key = _identifier(key, "idempotency_key")
        request = _identifier(request_id, "request_id")
        now = time.time()
        with self._lock, closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                self._ensure_session_db(db, session, now)
                existing = db.execute(
                    "SELECT request_id FROM request_keys "
                    "WHERE session_id=? AND idempotency_key=?",
                    (session, normalized_key),
                ).fetchone()
                if existing is not None:
                    db.commit()
                    return str(existing["request_id"]), False
                db.execute(
                    "INSERT INTO request_keys(session_id, idempotency_key, request_id, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (session, normalized_key, request, now),
                )
                db.execute(
                    "UPDATE conversations SET updated_at=? WHERE session_id=?",
                    (now, session),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        return request, True

    def accepted_request(
        self,
        session_id: str,
        idempotency_key: str,
        request_id: str = "",
    ) -> dict[str, Any] | None:
        session = _identifier(session_id, "session_id")
        key = _identifier(idempotency_key, "idempotency_key")
        request = _identifier(request_id, "request_id", allow_empty=True)
        with self._lock, closing(self._connect()) as db:
            row = db.execute(
                "SELECT request_id FROM request_keys "
                "WHERE session_id=? AND idempotency_key=?",
                (session, key),
            ).fetchone()
            if row is None and request:
                row = db.execute(
                    "SELECT request_id FROM request_keys "
                    "WHERE session_id=? AND request_id=? ORDER BY created_at LIMIT 1",
                    (session, request),
                ).fetchone()
            if row is None:
                return None
            return self._accepted_request_db(db, session, str(row["request_id"]))

    def stats(self) -> dict[str, int]:
        with self._lock, closing(self._connect()) as db:
            sessions = int(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
            messages = int(
                db.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0]
            )
            events = int(db.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0])
        return {"sessions": sessions, "messages": messages, "events": events}

    @staticmethod
    def _accepted_request_db(
        db: sqlite3.Connection, session_id: str, request_id: str
    ) -> dict[str, Any]:
        message = db.execute(
            "SELECT * FROM conversation_messages "
            "WHERE session_id=? AND request_id=? AND role='user' ORDER BY id LIMIT 1",
            (session_id, request_id),
        ).fetchone()
        event = db.execute(
            "SELECT event_id, session_id, request_id, sequence, type, timestamp, payload_json "
            "FROM conversation_events WHERE session_id=? AND request_id=? "
            "AND type='request.accepted' ORDER BY sequence LIMIT 1",
            (session_id, request_id),
        ).fetchone()
        if message is None or event is None:
            raise ConversationStoreError(
                "idempotency claim exists without an atomic accepted request"
            )
        return {
            "accepted": False,
            "duplicate": True,
            "request_id": request_id,
            "message": _message_dict(message),
            "preceding_events": [],
            "event": _event_dict(event),
        }

    @staticmethod
    def _ensure_session_db(db: sqlite3.Connection, session_id: str, now: float) -> None:
        db.execute(
            "INSERT OR IGNORE INTO conversations(session_id, created_at, updated_at) "
            "VALUES (?, ?, ?)",
            (session_id, now, now),
        )


def _identifier(
    value: Any,
    field: str,
    *,
    allow_empty: bool = False,
) -> str:
    normalized = str(value or "").strip()
    if not normalized and not allow_empty:
        raise ConversationStoreError(f"{field} is required")
    if len(normalized) > 256:
        raise ConversationStoreError(f"{field} is too long")
    if any(character in normalized for character in "\r\n\x00"):
        raise ConversationStoreError(f"{field} contains invalid characters")
    return normalized


def _limit(value: Any, *, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError) as exc:
        raise ConversationStoreError("limit must be an integer") from exc


def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "session_id": row["session_id"],
        "request_id": row["request_id"],
        "role": row["role"],
        "content": row["content"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": row["event_id"],
        "session_id": row["session_id"],
        "request_id": row["request_id"],
        "sequence": row["sequence"],
        "type": row["type"],
        "timestamp": row["timestamp"],
        "payload": json.loads(row["payload_json"] or "{}"),
    }


def _payload_json(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ConversationStoreError(
            f"event payload is not JSON serializable: {exc}"
        ) from exc


def _event_values(
    event_id: str,
    session_id: str,
    request_id: str,
    sequence: int,
    event_type: str,
    timestamp: float,
    payload_json: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "session_id": session_id,
        "request_id": request_id,
        "sequence": sequence,
        "type": event_type,
        "timestamp": timestamp,
        "payload": json.loads(payload_json),
    }
