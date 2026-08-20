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

from core.life.memory.contracts import (
    AccessContext,
    ActorKind,
    Audience,
    IdentityAssurance,
    TerminalOutcome,
)


SCHEMA_VERSION = 1
MESSAGE_ROLES = {"user", "assistant"}
MESSAGE_STATES = {"complete", "interrupted"}
EVENT_TYPE_PATTERN = re.compile(r"[a-z0-9_.-]{1,128}")
SOURCE_STORE_ID_META_KEY = "source_store_id"
TERMINAL_OUTCOMES = {
    f"request.{outcome.value}": outcome.value for outcome in TerminalOutcome
}
ACCESS_PROJECTION_KEYS = (
    "schema_version",
    "context_id",
    "actor_subject_id",
    "actor_kind",
    "session_id",
    "participant_subject_ids",
    "audience_ceiling",
    "identity_assurance",
    "acl_epoch",
)


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
                "CREATE INDEX IF NOT EXISTS idx_conversation_events_terminal "
                "ON conversation_events(type, id)"
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
                """
                CREATE TABLE IF NOT EXISTS conversation_redactions (
                    receipt_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    deletion_id TEXT NOT NULL,
                    redacted_at REAL NOT NULL,
                    messages_redacted INTEGER NOT NULL,
                    events_redacted INTEGER NOT NULL,
                    PRIMARY KEY(session_id, request_id),
                    FOREIGN KEY(session_id) REFERENCES conversations(session_id)
                )
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO conversation_meta(key, value) VALUES(?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            db.execute(
                "INSERT OR IGNORE INTO conversation_meta(key, value) VALUES(?, ?)",
                (SOURCE_STORE_ID_META_KEY, uuid.uuid4().hex),
            )

    def source_store_id(self) -> str:
        """Return the durable identity of this conversation evidence store."""

        with self._lock, closing(self._connect()) as db:
            row = db.execute(
                "SELECT value FROM conversation_meta WHERE key=?",
                (SOURCE_STORE_ID_META_KEY,),
            ).fetchone()
        if row is None:
            raise ConversationStoreError("conversation source store ID is missing")
        return str(row["value"])

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
            redaction = db.execute(
                "SELECT deletion_id FROM conversation_redactions "
                "WHERE session_id=? AND request_id=?",
                (session, request),
            ).fetchone()
            if redaction is not None:
                normalized_content = ""
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
                    prior_redaction = db.execute(
                        "SELECT deletion_id FROM conversation_redactions "
                        "WHERE session_id=? AND request_id=?",
                        (session, prior_request),
                    ).fetchone()
                    if prior_redaction is not None:
                        payload_json = _redaction_payload_json(
                            str(prior_redaction["deletion_id"])
                        )
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
                redaction = db.execute(
                    "SELECT deletion_id FROM conversation_redactions "
                    "WHERE session_id=? AND request_id=?",
                    (session, request),
                ).fetchone()
                if redaction is not None:
                    payload_json = _redaction_payload_json(
                        str(redaction["deletion_id"])
                    )
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

    def scan_terminal_events(
        self,
        after_row_id: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Scan canonical request terminals using an opaque global row cursor."""

        after = _row_cursor(after_row_id)
        bounded_limit = _limit(limit, maximum=500)
        terminal_types = tuple(TERMINAL_OUTCOMES)
        placeholders = ", ".join("?" for _ in terminal_types)
        with self._lock, closing(self._connect()) as db:
            rows = db.execute(
                f"""
                SELECT id AS row_id, event_id, session_id, request_id,
                       sequence, type
                FROM conversation_events
                WHERE id>? AND type IN ({placeholders})
                ORDER BY id ASC LIMIT ?
                """,
                (after, *terminal_types, bounded_limit + 1),
            ).fetchall()
        has_more = len(rows) > bounded_limit
        page_rows = rows[:bounded_limit]
        events = [_terminal_scan_dict(row) for row in page_rows]
        next_row_id = int(page_rows[-1]["row_id"]) if page_rows else after
        return {
            "schema_version": SCHEMA_VERSION,
            "source_store_id": self.source_store_id(),
            "after_row_id": after,
            "next_row_id": next_row_id,
            "has_more": has_more,
            "events": events,
        }

    def read_request_evidence(
        self,
        session_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        """Read one request's authoritative evidence from a single snapshot."""

        session = _identifier(session_id, "session_id")
        request = _identifier(request_id, "request_id")
        with self._lock, closing(self._connect()) as db:
            db.execute("PRAGMA query_only=ON")
            try:
                db.execute("BEGIN")
                store_row = db.execute(
                    "SELECT value FROM conversation_meta WHERE key=?",
                    (SOURCE_STORE_ID_META_KEY,),
                ).fetchone()
                message_rows = db.execute(
                    "SELECT * FROM conversation_messages "
                    "WHERE session_id=? AND request_id=? ORDER BY id ASC",
                    (session, request),
                ).fetchall()
                event_rows = db.execute(
                    "SELECT id AS row_id, event_id, session_id, request_id, "
                    "sequence, type, timestamp, payload_json "
                    "FROM conversation_events "
                    "WHERE session_id=? AND request_id=? ORDER BY sequence ASC",
                    (session, request),
                ).fetchall()
                redaction_row = db.execute(
                    "SELECT receipt_id, session_id, request_id, deletion_id, "
                    "redacted_at, messages_redacted, events_redacted "
                    "FROM conversation_redactions "
                    "WHERE session_id=? AND request_id=?",
                    (session, request),
                ).fetchone()
                db.commit()
            except Exception:
                db.rollback()
                raise

        if store_row is None:
            raise ConversationStoreError("conversation source store ID is missing")
        messages = [_message_dict(row) for row in message_rows]
        events = [_evidence_event_dict(row) for row in event_rows]
        accepted_events = [
            event for event in events if event["type"] == "request.accepted"
        ]
        terminal_events = [
            event for event in events if event["type"] in TERMINAL_OUTCOMES
        ]
        accepted_event = accepted_events[0] if len(accepted_events) == 1 else None
        terminal_conflict = len(terminal_events) > 1
        terminal_event = terminal_events[0] if len(terminal_events) == 1 else None
        outcome = (
            TERMINAL_OUTCOMES[terminal_event["type"]]
            if terminal_event is not None
            else None
        )
        access_projection = (
            _safe_access_projection(accepted_event["payload"], session)
            if accepted_event is not None
            else None
        )
        redacted = redaction_row is not None
        evidence_ready = _request_evidence_ready(
            accepted_events=accepted_events,
            terminal_events=terminal_events,
            messages=messages,
            redacted=redacted,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "source_store_id": str(store_row["value"]),
            "session_id": session,
            "request_id": request,
            "sequence_domain": _sequence_domain(session),
            "accepted_event": accepted_event,
            "messages": messages,
            "events": events,
            "terminal_event": terminal_event,
            "terminal_events": terminal_events,
            "terminal_conflict": terminal_conflict,
            "outcome": outcome,
            "access_projection": access_projection,
            "evidence_ready": evidence_ready,
            "redacted": redacted,
            "redaction_receipt": (
                _redaction_receipt_dict(redaction_row, str(store_row["value"]))
                if redaction_row is not None
                else None
            ),
        }

    def redact_request_evidence(
        self,
        session_id: str,
        request_id: str,
        deletion_id: str,
    ) -> dict[str, Any]:
        """Delete request content while retaining ordering and idempotency tombstones."""

        session = _identifier(session_id, "session_id")
        request = _identifier(request_id, "request_id")
        deletion = _identifier(deletion_id, "deletion_id")
        now = time.time()
        with self._lock, closing(self._connect()) as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT receipt_id, session_id, request_id, deletion_id, "
                    "redacted_at, messages_redacted, events_redacted "
                    "FROM conversation_redactions "
                    "WHERE session_id=? AND request_id=?",
                    (session, request),
                ).fetchone()
                if existing is not None:
                    store_id = self._source_store_id_db(db)
                    db.commit()
                    return _redaction_receipt_dict(existing, store_id)

                exists = db.execute(
                    "SELECT EXISTS(SELECT 1 FROM request_keys "
                    "WHERE session_id=? AND request_id=?) "
                    "OR EXISTS(SELECT 1 FROM conversation_messages "
                    "WHERE session_id=? AND request_id=?) "
                    "OR EXISTS(SELECT 1 FROM conversation_events "
                    "WHERE session_id=? AND request_id=?)",
                    (session, request, session, request, session, request),
                ).fetchone()[0]
                if not exists:
                    raise ConversationStoreError("request evidence does not exist")

                message_cursor = db.execute(
                    "UPDATE conversation_messages SET content='' "
                    "WHERE session_id=? AND request_id=?",
                    (session, request),
                )
                event_cursor = db.execute(
                    "UPDATE conversation_events SET payload_json=? "
                    "WHERE session_id=? AND request_id=?",
                    (_redaction_payload_json(deletion), session, request),
                )
                receipt_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO conversation_redactions("
                    "receipt_id, session_id, request_id, deletion_id, redacted_at, "
                    "messages_redacted, events_redacted) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        receipt_id,
                        session,
                        request,
                        deletion,
                        now,
                        message_cursor.rowcount,
                        event_cursor.rowcount,
                    ),
                )
                row = db.execute(
                    "SELECT receipt_id, session_id, request_id, deletion_id, "
                    "redacted_at, messages_redacted, events_redacted "
                    "FROM conversation_redactions "
                    "WHERE session_id=? AND request_id=?",
                    (session, request),
                ).fetchone()
                store_id = self._source_store_id_db(db)
                db.commit()
            except Exception:
                db.rollback()
                raise
        return _redaction_receipt_dict(row, store_id)

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
    def _source_store_id_db(db: sqlite3.Connection) -> str:
        row = db.execute(
            "SELECT value FROM conversation_meta WHERE key=?",
            (SOURCE_STORE_ID_META_KEY,),
        ).fetchone()
        if row is None:
            raise ConversationStoreError("conversation source store ID is missing")
        return str(row["value"])

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


def _row_cursor(value: Any) -> int:
    try:
        cursor = int(value)
    except (TypeError, ValueError) as exc:
        raise ConversationStoreError("terminal row cursor must be an integer") from exc
    if cursor < 0:
        raise ConversationStoreError("terminal row cursor must be non-negative")
    return cursor


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


def _evidence_event_dict(row: sqlite3.Row) -> dict[str, Any]:
    event = _event_dict(row)
    event["row_id"] = int(row["row_id"])
    event["sequence_domain"] = _sequence_domain(str(row["session_id"]))
    return event


def _terminal_scan_dict(row: sqlite3.Row) -> dict[str, Any]:
    row_id = int(row["row_id"])
    session_id = str(row["session_id"])
    sequence = int(row["sequence"])
    return {
        "schema_version": SCHEMA_VERSION,
        "row_id": row_id,
        "terminal_row_id": row_id,
        "event_id": str(row["event_id"]),
        "session_id": session_id,
        "request_id": str(row["request_id"]),
        "sequence": sequence,
        "sequence_domain": _sequence_domain(session_id),
        "type": str(row["type"]),
        "outcome": TERMINAL_OUTCOMES[str(row["type"])],
    }


def _sequence_domain(session_id: str) -> str:
    return f"conversation_store:{session_id}"


def _request_evidence_ready(
    *,
    accepted_events: list[dict[str, Any]],
    terminal_events: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    redacted: bool,
) -> bool:
    if redacted or len(accepted_events) != 1 or len(terminal_events) != 1:
        return False
    accepted = accepted_events[0]
    terminal = terminal_events[0]
    if int(terminal["sequence"]) <= int(accepted["sequence"]):
        return False
    has_user_message = any(
        message["role"] == "user"
        and message["status"] == "complete"
        and bool(message["content"])
        for message in messages
    )
    if not has_user_message:
        return False
    if TERMINAL_OUTCOMES[terminal["type"]] != TerminalOutcome.COMPLETED.value:
        return True
    return any(
        message["role"] == "assistant"
        and message["status"] == "complete"
        and bool(message["content"])
        for message in messages
    )


def _safe_access_projection(
    accepted_payload: dict[str, Any],
    session_id: str,
) -> dict[str, Any] | None:
    candidates = (
        accepted_payload.get("access_projection"),
        accepted_payload.get("access_context_projection"),
        accepted_payload.get("access_context"),
    )
    candidate = next((item for item in candidates if isinstance(item, dict)), None)
    if candidate is None and "context_id" in accepted_payload:
        candidate = accepted_payload
    if candidate is None:
        return None

    if "schema_version" in candidate and candidate["schema_version"] != SCHEMA_VERSION:
        return None

    try:
        if set(candidate) == {field for field in AccessContext.__dataclass_fields__}:
            context = AccessContext.from_dict(candidate)
            candidate = context.to_dict()
    except (TypeError, ValueError):
        return None

    required = set(ACCESS_PROJECTION_KEYS) - {"schema_version"}
    if not required.issubset(candidate):
        return None
    projected = {key: candidate[key] for key in ACCESS_PROJECTION_KEYS if key in candidate}
    projected["schema_version"] = SCHEMA_VERSION
    if not _valid_projection_id(projected.get("context_id")):
        return None
    if not _valid_projection_id(projected.get("actor_subject_id")):
        return None
    if projected.get("session_id") != session_id:
        return None
    if projected.get("actor_kind") not in {item.value for item in ActorKind}:
        return None
    if projected.get("audience_ceiling") not in {item.value for item in Audience}:
        return None
    if projected.get("identity_assurance") not in {
        item.value for item in IdentityAssurance
    }:
        return None
    if projected["actor_kind"] == ActorKind.GUEST.value and (
        projected["audience_ceiling"] != Audience.GUEST.value
        or projected["identity_assurance"] != IdentityAssurance.GUEST.value
    ):
        return None
    participants = projected.get("participant_subject_ids")
    if not isinstance(participants, (list, tuple)) or len(participants) > 16:
        return None
    if any(not _valid_projection_id(item) for item in participants):
        return None
    if len(set(participants)) != len(participants):
        return None
    acl_epoch = projected.get("acl_epoch")
    if isinstance(acl_epoch, bool) or not isinstance(acl_epoch, int) or acl_epoch < 0:
        return None
    projected["participant_subject_ids"] = list(participants)
    return projected


def _valid_projection_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= 256
        and not any(character in value for character in "\r\n\x00")
    )


def _redaction_payload_json(deletion_id: str) -> str:
    return _payload_json(
        {"deletion_id": deletion_id, "redacted": True, "tombstone": True}
    )


def _redaction_receipt_dict(
    row: sqlite3.Row,
    source_store_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_id": str(row["receipt_id"]),
        "source_store_id": source_store_id,
        "session_id": str(row["session_id"]),
        "request_id": str(row["request_id"]),
        "deletion_id": str(row["deletion_id"]),
        "redacted_at": float(row["redacted_at"]),
        "messages_redacted": int(row["messages_redacted"]),
        "events_redacted": int(row["events_redacted"]),
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
