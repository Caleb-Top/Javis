"""Governed SQLite storage for autobiographical memory.

The store owns the database schema and enforces the L2 single-writer boundary.
Callers can submit typed contracts to writer transactions or use bounded read
methods; no public method accepts arbitrary SQL.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.life.memory.contracts import (
    AccessContext,
    AccessPurpose,
    ActorKind,
    Audience,
    ClaimStatus,
    DeletionRequest,
    DerivationEdge,
    ExperienceEpisode,
    JournalEntry,
    MemoryItemKind,
    MemoryItemStatus,
    RecallQuery,
    RelationshipEvent,
    RelationshipEventStatus,
    SessionParticipant,
    SharedMemory,
    SharedMemoryStatus,
    Subject,
    UserModelClaim,
)


DATABASE_RELATIVE_PATH = Path("memory") / "autobiographical.sqlite3"
SCHEMA_VERSION = 2
_READ_PERMISSIONS = frozenset({"read", "manage", "delete"})
_PROJECTION_STATES = frozenset({"pending", "excluded", "not_selected", "projected"})
_TERMINAL_OUTCOMES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_ITEM_CONTRACTS = {
    MemoryItemKind.EXPERIENCE_EPISODE.value: ExperienceEpisode,
    MemoryItemKind.JOURNAL_ENTRY.value: JournalEntry,
    MemoryItemKind.SHARED_MEMORY.value: SharedMemory,
    MemoryItemKind.USER_MODEL_CLAIM.value: UserModelClaim,
    MemoryItemKind.RELATIONSHIP_EVENT.value: RelationshipEvent,
}
_AUDIENCES_BY_CEILING = {
    Audience.GUEST: (Audience.GUEST.value,),
    Audience.OWNER_PRIVATE: (Audience.GUEST.value, Audience.OWNER_PRIVATE.value),
    Audience.PARTICIPANTS: (
        Audience.GUEST.value,
        Audience.OWNER_PRIVATE.value,
        Audience.PARTICIPANTS.value,
    ),
    Audience.EXPLICIT_SHARED: tuple(audience.value for audience in Audience),
}
_REQUIRED_V1_TABLES = frozenset(
    {
        "schema_migrations",
        "memory_meta",
        "subjects",
        "session_participants",
        "memory_items",
        "experience_episodes",
        "journal_entries",
        "shared_memories",
        "user_model_claims",
        "relationship_events",
        "memory_acl",
        "shared_confirmations",
        "derivation_edges",
        "deletion_requests",
        "deletion_targets",
        "terminal_projection_receipts",
        "projection_suppressions",
    }
)
_REQUIRED_V2_TABLES = _REQUIRED_V1_TABLES | {
    "memory_fts",
    "memory_fts_rebuilds",
}


class MemoryStoreError(RuntimeError):
    """Base error for the governed memory store."""


class MemoryStoreAuthorizationError(MemoryStoreError):
    """Raised when a write is attempted outside the bound writer authority."""


class MemoryStoreReadOnlyError(MemoryStoreError):
    """Raised when a degraded, closed, or read-only store receives a mutation."""


class MemoryStoreConflictError(MemoryStoreError):
    """Raised when an idempotency or revision invariant is violated."""


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _require_text(value: Any, field_name: str) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_non_negative(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


_MIGRATION_1 = (
    """
    CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at_utc TEXT NOT NULL,
        checksum TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE memory_meta (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
        source_store_id TEXT,
        terminal_cursor INTEGER NOT NULL DEFAULT 0 CHECK (terminal_cursor >= 0),
        acl_epoch INTEGER NOT NULL DEFAULT 0 CHECK (acl_epoch >= 0),
        index_generation INTEGER NOT NULL DEFAULT 0 CHECK (index_generation >= 0),
        updated_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE subjects (
        subject_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        subject_kind TEXT NOT NULL,
        display_name TEXT,
        status TEXT NOT NULL,
        identity_assurance TEXT NOT NULL,
        credential_reference_hash TEXT,
        merged_into_subject_id TEXT REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        session_scope_id TEXT,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE session_participants (
        participant_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        session_id TEXT NOT NULL,
        subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        participant_role TEXT NOT NULL,
        identity_assurance TEXT NOT NULL,
        joined_at_utc TEXT NOT NULL,
        left_at_utc TEXT,
        server_binding_source TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE memory_items (
        item_id TEXT PRIMARY KEY,
        item_kind TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        owner_subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        audience TEXT NOT NULL,
        privacy_class TEXT NOT NULL,
        status TEXT NOT NULL,
        retention_class TEXT,
        occurred_at_utc TEXT NOT NULL,
        expires_at_utc TEXT,
        source_digest TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
        epistemic_label TEXT NOT NULL,
        deletion_fenced INTEGER NOT NULL DEFAULT 0 CHECK (deletion_fenced IN (0, 1)),
        content_hash TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        UNIQUE (item_kind, item_id)
    )
    """,
    """
    CREATE TABLE experience_episodes (
        episode_id TEXT PRIMARY KEY REFERENCES memory_items(item_id) ON DELETE CASCADE,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        session_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        participant_subject_ids_json TEXT NOT NULL,
        started_at_utc TEXT NOT NULL,
        ended_at_utc TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome = 'completed'),
        what_happened TEXT NOT NULL,
        javis_attention TEXT NOT NULL,
        intent_summary TEXT NOT NULL,
        action_summary TEXT NOT NULL,
        verified_result_summary TEXT NOT NULL,
        meaning_for_user TEXT NOT NULL,
        meaning_for_javis TEXT NOT NULL,
        source_terminal_event_id TEXT NOT NULL UNIQUE,
        source_terminal_sequence INTEGER NOT NULL CHECK (source_terminal_sequence >= 0),
        source_sequence_domain TEXT NOT NULL,
        source_message_ids_json TEXT NOT NULL,
        source_event_ids_json TEXT NOT NULL,
        extractor_version TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE journal_entries (
        entry_id TEXT PRIMARY KEY REFERENCES memory_items(item_id) ON DELETE CASCADE,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        range_started_at_utc TEXT NOT NULL,
        range_ended_at_utc TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        source_episode_ids_json TEXT NOT NULL,
        entry_kind TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE shared_memories (
        shared_memory_id TEXT PRIMARY KEY REFERENCES memory_items(item_id) ON DELETE CASCADE,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        proposal_revision INTEGER NOT NULL CHECK (proposal_revision >= 1),
        source_episode_ids_json TEXT NOT NULL,
        proposed_text TEXT NOT NULL,
        participant_subject_ids_json TEXT NOT NULL,
        status TEXT NOT NULL,
        confirmed_at_utc TEXT,
        revoked_at_utc TEXT,
        audience_subject_ids_json TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE user_model_claims (
        claim_id TEXT PRIMARY KEY REFERENCES memory_items(item_id) ON DELETE CASCADE,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        predicate TEXT NOT NULL,
        value_type TEXT NOT NULL,
        value_json TEXT NOT NULL,
        epistemic_class TEXT NOT NULL,
        sensitivity TEXT NOT NULL,
        source_evidence_ids_json TEXT NOT NULL,
        contradiction_claim_ids_json TEXT NOT NULL,
        supersedes_claim_id TEXT REFERENCES user_model_claims(claim_id) ON DELETE SET NULL,
        acl_subject_ids_json TEXT NOT NULL,
        confirmed_at_utc TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE relationship_events (
        relationship_event_id TEXT PRIMARY KEY REFERENCES memory_items(item_id) ON DELETE CASCADE,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        subject_ids_json TEXT NOT NULL,
        event_kind TEXT NOT NULL,
        summary TEXT NOT NULL,
        source_evidence_ids_json TEXT NOT NULL,
        occurred_at_utc TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE memory_acl (
        acl_id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id TEXT NOT NULL REFERENCES memory_items(item_id) ON DELETE CASCADE,
        subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        permission TEXT NOT NULL CHECK (permission IN ('read', 'manage', 'delete')),
        granted_by_subject_id TEXT REFERENCES subjects(subject_id) ON DELETE SET NULL,
        created_at_utc TEXT NOT NULL,
        UNIQUE (item_id, subject_id, permission)
    )
    """,
    """
    CREATE TABLE shared_confirmations (
        receipt_id TEXT PRIMARY KEY,
        shared_memory_id TEXT NOT NULL REFERENCES shared_memories(shared_memory_id) ON DELETE CASCADE,
        subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        proposal_revision INTEGER NOT NULL CHECK (proposal_revision >= 1),
        confirmed_at_utc TEXT NOT NULL,
        UNIQUE (shared_memory_id, subject_id, proposal_revision)
    )
    """,
    """
    CREATE TABLE derivation_edges (
        edge_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        source_kind TEXT NOT NULL,
        source_id TEXT NOT NULL REFERENCES memory_items(item_id) ON DELETE CASCADE,
        target_kind TEXT NOT NULL,
        target_id TEXT NOT NULL REFERENCES memory_items(item_id) ON DELETE CASCADE,
        relation TEXT NOT NULL,
        extractor TEXT NOT NULL,
        extractor_version TEXT NOT NULL,
        source_digest TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        active INTEGER NOT NULL CHECK (active IN (0, 1)),
        payload_json TEXT NOT NULL,
        UNIQUE (source_kind, source_id, target_kind, target_id, relation)
    )
    """,
    """
    CREATE TABLE deletion_requests (
        deletion_request_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        actor_subject_id TEXT REFERENCES subjects(subject_id) ON DELETE SET NULL,
        actor_subject_hash TEXT NOT NULL,
        scope TEXT NOT NULL,
        target_selector_json TEXT NOT NULL,
        source_handling TEXT NOT NULL,
        state TEXT NOT NULL,
        progress_cursor TEXT,
        attempt INTEGER NOT NULL CHECK (attempt >= 0),
        last_reason_code TEXT,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        completed_at_utc TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE deletion_targets (
        deletion_target_id TEXT PRIMARY KEY,
        deletion_request_id TEXT NOT NULL REFERENCES deletion_requests(deletion_request_id) ON DELETE CASCADE,
        item_kind TEXT,
        item_id TEXT REFERENCES memory_items(item_id) ON DELETE SET NULL,
        source_store_id TEXT,
        session_id TEXT,
        request_id TEXT,
        target_state TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        UNIQUE (deletion_request_id, item_kind, item_id)
    )
    """,
    """
    CREATE TABLE terminal_projection_receipts (
        receipt_id TEXT PRIMARY KEY,
        source_store_id TEXT NOT NULL,
        terminal_row_id INTEGER NOT NULL CHECK (terminal_row_id >= 1),
        session_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        source_terminal_event_id TEXT NOT NULL UNIQUE,
        outcome TEXT NOT NULL,
        projection_state TEXT NOT NULL,
        reason_code TEXT,
        episode_id TEXT REFERENCES experience_episodes(episode_id) ON DELETE SET NULL,
        attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
        next_retry_at_utc TEXT,
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        UNIQUE (source_store_id, session_id, request_id)
    )
    """,
    """
    CREATE TABLE projection_suppressions (
        suppression_id TEXT PRIMARY KEY,
        source_store_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        deletion_request_id TEXT REFERENCES deletion_requests(deletion_request_id) ON DELETE SET NULL,
        reason_code TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        UNIQUE (source_store_id, session_id, request_id)
    )
    """,
    "CREATE INDEX idx_subjects_kind_status ON subjects(subject_kind, status)",
    "CREATE INDEX idx_session_participants_session ON session_participants(session_id, status)",
    "CREATE UNIQUE INDEX uq_session_participants_active_subject "
    "ON session_participants(session_id, subject_id) WHERE status = 'active'",
    "CREATE UNIQUE INDEX uq_session_participants_active_primary "
    "ON session_participants(session_id) "
    "WHERE status = 'active' AND participant_role = 'primary'",
    "CREATE INDEX idx_memory_items_owner_status ON memory_items(owner_subject_id, status)",
    "CREATE INDEX idx_memory_items_audience_status ON memory_items(audience, status, deletion_fenced)",
    "CREATE INDEX idx_memory_items_occurred ON memory_items(occurred_at_utc DESC)",
    "CREATE INDEX idx_memory_items_expiry ON memory_items(expires_at_utc) WHERE expires_at_utc IS NOT NULL",
    "CREATE INDEX idx_episodes_request ON experience_episodes(session_id, request_id)",
    "CREATE INDEX idx_acl_subject_permission ON memory_acl(subject_id, permission, item_id)",
    "CREATE INDEX idx_derivation_source ON derivation_edges(source_kind, source_id, active)",
    "CREATE INDEX idx_derivation_target ON derivation_edges(target_kind, target_id, active)",
    "CREATE INDEX idx_deletion_state ON deletion_requests(state, updated_at_utc)",
    "CREATE INDEX idx_deletion_targets_request ON deletion_targets(deletion_request_id, target_state)",
    "CREATE INDEX idx_terminal_projection_state "
    "ON terminal_projection_receipts(projection_state, terminal_row_id)",
    "CREATE INDEX idx_projection_suppression_request "
    "ON projection_suppressions(source_store_id, session_id, request_id)",
)

_MIGRATION_2 = (
    """
    CREATE VIRTUAL TABLE memory_fts USING fts5(
        item_id UNINDEXED,
        item_kind UNINDEXED,
        searchable_text,
        tokenize = 'unicode61'
    )
    """,
    """
    CREATE TABLE memory_fts_rebuilds (
        rebuild_id TEXT PRIMARY KEY,
        from_generation INTEGER NOT NULL CHECK (from_generation >= 0),
        target_generation INTEGER NOT NULL CHECK (target_generation >= 1),
        state TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        expected_item_count INTEGER,
        indexed_item_count INTEGER NOT NULL DEFAULT 0 CHECK (indexed_item_count >= 0),
        started_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        completed_at_utc TEXT,
        UNIQUE (target_generation)
    )
    """,
    "CREATE INDEX idx_fts_rebuild_state ON memory_fts_rebuilds(state, updated_at_utc)",
)


class MemoryStore:
    """Versioned, single-writer storage rooted below the runtime data root."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        writer_token: object,
        writer_thread: threading.Thread | None = None,
        writer_thread_id: int | None = None,
    ) -> None:
        if writer_token is None:
            raise ValueError("writer_token must be an explicit non-None object")
        if writer_thread is not None and writer_thread_id is not None:
            raise ValueError("provide writer_thread or writer_thread_id, not both")
        root = Path(data_root).expanduser().resolve()
        if root.exists() and not root.is_dir():
            raise ValueError("data_root must be a directory")
        if writer_thread is not None:
            if writer_thread.ident is None:
                raise ValueError("writer_thread must be started before binding")
            writer_thread_id = writer_thread.ident
        if writer_thread_id is None:
            writer_thread_id = threading.get_ident()
        if type(writer_thread_id) is not int or writer_thread_id <= 0:
            raise ValueError("writer_thread_id must be a positive thread identifier")

        self.data_root = root
        self.path = root / DATABASE_RELATIVE_PATH
        self._writer_token = writer_token
        self._writer_thread_id = writer_thread_id
        self._writer_connection: sqlite3.Connection | None = None
        self._state = "initializing"
        self._reason_code: str | None = None
        self._detail: str | None = None
        self._fts_available = False
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def writer_thread_id(self) -> int:
        return self._writer_thread_id

    @property
    def read_only(self) -> bool:
        return self._writer_connection is None

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state,
            "reason_code": self._reason_code,
            "detail": self._detail,
            "read_only": self.read_only,
            "fts_available": self._fts_available,
            "schema_version": self._read_user_version(),
            "path": str(self.path),
        }

    def metadata(self) -> dict[str, Any]:
        """Return content-free cache and reconciliation metadata."""

        try:
            with self._read_connection() as db:
                row = db.execute(
                    "SELECT schema_version, source_store_id, terminal_cursor, "
                    "acl_epoch, index_generation, updated_at_utc "
                    "FROM memory_meta WHERE singleton = 1"
                ).fetchone()
        except sqlite3.Error:
            return {
                "schema_version": self._read_user_version(),
                "source_store_id": None,
                "terminal_cursor": 0,
                "acl_epoch": 0,
                "index_generation": 0,
                "updated_at_utc": None,
            }
        if row is None:
            raise MemoryStoreError("memory metadata row is missing")
        return dict(row)

    def connection_settings(self) -> dict[str, Any]:
        """Expose bounded connection diagnostics without exposing SQL execution."""

        with self._read_connection() as db:
            return {
                "journal_mode": str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
                "foreign_keys": int(db.execute("PRAGMA foreign_keys").fetchone()[0]),
                "query_only": int(db.execute("PRAGMA query_only").fetchone()[0]),
            }

    def get_subject(self, subject_id: str) -> Subject | None:
        _require_text(subject_id, "subject_id")
        with self._read_connection() as db:
            row = db.execute(
                "SELECT payload_json FROM subjects WHERE subject_id = ?", (subject_id,)
            ).fetchone()
        return None if row is None else Subject.from_dict(json.loads(row["payload_json"]))

    def active_session_participants(self, session_id: str) -> tuple[SessionParticipant, ...]:
        _require_text(session_id, "session_id")
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM session_participants "
                "WHERE session_id = ? AND status = 'active' "
                "ORDER BY joined_at_utc, participant_id",
                (session_id,),
            ).fetchall()
        return tuple(SessionParticipant.from_dict(json.loads(row["payload_json"])) for row in rows)

    def get_terminal_receipt(
        self, source_store_id: str, session_id: str, request_id: str
    ) -> dict[str, Any] | None:
        for name, value in (
            ("source_store_id", source_store_id),
            ("session_id", session_id),
            ("request_id", request_id),
        ):
            _require_text(value, name)
        with self._read_connection() as db:
            row = db.execute(
                "SELECT * FROM terminal_projection_receipts "
                "WHERE source_store_id = ? AND session_id = ? AND request_id = ?",
                (source_store_id, session_id, request_id),
            ).fetchone()
        return None if row is None else dict(row)

    def get_item(
        self,
        item_kind: MemoryItemKind | str,
        item_id: str,
        access_context: AccessContext,
    ) -> ExperienceEpisode | JournalEntry | SharedMemory | UserModelClaim | RelationshipEvent | None:
        """Read one active item after owner/ACL filtering in SQLite."""

        kind = self._item_kind_value(item_kind)
        _require_text(item_id, "item_id")
        if not self._context_can_recall(access_context):
            return None
        audiences = _AUDIENCES_BY_CEILING[access_context.audience_ceiling]
        audience_placeholders = ",".join("?" for _ in audiences)
        with self._read_connection() as db:
            row = db.execute(
                f"""
                SELECT i.payload_json
                FROM memory_items AS i
                WHERE i.item_kind = ? AND i.item_id = ?
                  AND i.status = 'active' AND i.deletion_fenced = 0
                  AND (i.expires_at_utc IS NULL OR i.expires_at_utc > ?)
                  AND i.audience IN ({audience_placeholders})
                  AND (
                    i.audience = 'guest'
                    OR (i.audience = 'owner_private' AND i.owner_subject_id = ?)
                    OR (i.audience IN ('participants', 'explicit_shared') AND EXISTS (
                        SELECT 1 FROM memory_acl AS a
                        WHERE a.item_id = i.item_id
                          AND a.subject_id = ? AND a.permission = 'read'
                    ))
                  )
                """,
                (
                    kind,
                    item_id,
                    access_context.issued_at_utc,
                    *audiences,
                    access_context.actor_subject_id,
                    access_context.actor_subject_id,
                ),
            ).fetchone()
        return None if row is None else self._decode_item(kind, row["payload_json"])

    def search_items(
        self, query: RecallQuery
    ) -> tuple[ExperienceEpisode | JournalEntry | SharedMemory | UserModelClaim | RelationshipEvent, ...]:
        """Run fail-closed FTS over the SQL-visible set for an AccessContext."""

        if not self._fts_available or not self._context_can_recall(query.access_context):
            return ()
        kinds = tuple(kind.value for kind in query.item_kinds) or tuple(_ITEM_CONTRACTS)
        placeholders = ",".join("?" for _ in kinds)
        audiences = _AUDIENCES_BY_CEILING[query.access_context.audience_ceiling]
        audience_placeholders = ",".join("?" for _ in audiences)
        filters = [
            "i.item_kind IN (" + placeholders + ")",
            "i.status = 'active'",
            "i.deletion_fenced = 0",
            "(i.expires_at_utc IS NULL OR i.expires_at_utc > ?)",
            "i.audience IN (" + audience_placeholders + ")",
            "(i.audience = 'guest' OR "
            "(i.audience = 'owner_private' AND i.owner_subject_id = ?) OR "
            "(i.audience IN ('participants', 'explicit_shared') AND EXISTS ("
            "SELECT 1 FROM memory_acl AS a WHERE a.item_id = i.item_id "
            "AND a.subject_id = ? AND a.permission = 'read')))",
        ]
        parameters: list[Any] = [
            *kinds,
            query.issued_at_utc,
            *audiences,
            query.access_context.actor_subject_id,
            query.access_context.actor_subject_id,
        ]
        if query.occurred_after_utc is not None:
            filters.append("i.occurred_at_utc >= ?")
            parameters.append(query.occurred_after_utc)
        if query.occurred_before_utc is not None:
            filters.append("i.occurred_at_utc <= ?")
            parameters.append(query.occurred_before_utc)
        fts_phrase = '"' + query.query_text.replace('"', '""') + '"'
        parameters.extend((fts_phrase, query.limit))
        sql = (
            "WITH visible AS MATERIALIZED ("
            "SELECT i.item_id, i.item_kind, i.payload_json FROM memory_items AS i WHERE "
            + " AND ".join(filters)
            + ") SELECT v.item_kind, v.payload_json FROM visible AS v "
            "JOIN memory_fts AS f ON f.item_id = v.item_id "
            "WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts), v.item_id LIMIT ?"
        )
        try:
            with self._read_connection() as db:
                rows = db.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError:
            return ()
        return tuple(self._decode_item(row["item_kind"], row["payload_json"]) for row in rows)

    def put_subject(self, subject: Subject, *, writer_token: object) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_subject(subject)

    def put_session_participant(
        self, participant: SessionParticipant, *, writer_token: object
    ) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_session_participant(participant)

    def put_item(
        self,
        item: ExperienceEpisode | JournalEntry | SharedMemory | UserModelClaim | RelationshipEvent,
        *,
        writer_token: object,
        acl: Mapping[str, Iterable[str]] | None = None,
    ) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_item(item, acl=acl)

    upsert_item = put_item

    def delete_item(
        self,
        item_kind: MemoryItemKind | str,
        item_id: str,
        *,
        writer_token: object,
    ) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.delete_item(item_kind, item_id)

    def put_derivation_edge(self, edge: DerivationEdge, *, writer_token: object) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_derivation_edge(edge)

    def put_deletion_request(
        self, request: DeletionRequest, *, writer_token: object
    ) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_deletion_request(request)

    def record_terminal_receipt(self, *, writer_token: object, **values: Any) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.record_terminal_receipt(**values)

    def set_source_progress(
        self,
        source_store_id: str,
        terminal_cursor: int,
        *,
        writer_token: object,
    ) -> None:
        with self._writer_transaction(writer_token) as writer:
            writer.set_source_progress(source_store_id, terminal_cursor)

    @contextmanager
    def _writer_transaction(self, writer_token: object) -> Iterator["_MemoryWriter"]:
        """Yield domain primitives only to the bound token on the bound thread."""

        self._assert_writer(writer_token)
        db = self._writer_connection
        assert db is not None
        try:
            db.execute("BEGIN IMMEDIATE")
            yield _MemoryWriter(self, db)
            db.commit()
        except Exception:
            db.rollback()
            raise

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        """Create a short-lived, URI read-only connection with query_only forced on."""

        if not self.path.is_file():
            raise MemoryStoreReadOnlyError("memory database is unavailable")
        uri = self.path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA query_only = ON")
            yield db
        finally:
            db.close()

    def close(self) -> None:
        if self._closed:
            return
        db = self._writer_connection
        self._writer_connection = None
        if db is not None:
            db.close()
        self._closed = True
        self._state = "stopped"

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _initialize(self) -> None:
        db: sqlite3.Connection | None = None
        try:
            db = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise sqlite3.DatabaseError("database schema is newer than this runtime")
            if version < 1:
                self._apply_migration(db, 1, _MIGRATION_1)
                version = 1
            if version == 1:
                self._validate_v1_schema(db)
                self._apply_migration(db, 2, _MIGRATION_2)
            self._validate_schema(db)
            mode = str(db.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
            if mode != "wal":
                raise sqlite3.OperationalError("WAL mode is unavailable")
            db.execute("PRAGMA synchronous = FULL")
        except (sqlite3.Error, MemoryStoreError) as exc:
            if db is not None:
                try:
                    db.rollback()
                except sqlite3.Error:
                    pass
                db.close()
            self._writer_connection = None
            self._fts_available = False
            self._state = "degraded"
            self._reason_code = "schema_migration_failed"
            self._detail = f"{type(exc).__name__}: {str(exc)[:160]}"
            return
        self._writer_connection = db
        self._fts_available = True
        self._state = "ready"

    def _apply_migration(
        self, db: sqlite3.Connection, version: int, statements: tuple[str, ...]
    ) -> None:
        checksum = hashlib.sha256("\n".join(statements).encode("utf-8")).hexdigest()
        try:
            db.execute("BEGIN IMMEDIATE")
            for statement in statements:
                db.execute(statement)
            if version == 1:
                db.execute(
                    "INSERT INTO memory_meta (singleton, schema_version, updated_at_utc) "
                    "VALUES (1, 1, ?)",
                    (_utc_now(),),
                )
            if version == 2:
                row = db.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memory_fts'"
                ).fetchone()
                if row is None or "fts5" not in str(row["sql"]).casefold():
                    raise sqlite3.OperationalError("FTS5 is unavailable")
            db.execute(
                "INSERT INTO schema_migrations (version, applied_at_utc, checksum) VALUES (?, ?, ?)",
                (version, _utc_now(), checksum),
            )
            db.execute(
                "UPDATE memory_meta SET schema_version = ?, updated_at_utc = ? WHERE singleton = 1",
                (version, _utc_now()),
            )
            db.execute(f"PRAGMA user_version = {version}")
            db.commit()
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def _validate_v1_schema(db: sqlite3.Connection) -> None:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        names = {str(row["name"]) for row in rows}
        if _REQUIRED_V1_TABLES - names:
            raise sqlite3.DatabaseError("required v1 memory schema objects are missing")
        meta = db.execute(
            "SELECT schema_version FROM memory_meta WHERE singleton = 1"
        ).fetchone()
        if meta is None or int(meta["schema_version"]) != 1:
            raise sqlite3.DatabaseError("v1 memory metadata is inconsistent")
        migration = db.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 1"
        ).fetchone()
        if migration is None:
            raise sqlite3.DatabaseError("v1 migration receipt is missing")

    @staticmethod
    def _validate_schema(db: sqlite3.Connection) -> None:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        names = {str(row["name"]) for row in rows}
        missing = _REQUIRED_V2_TABLES - names
        if missing:
            raise sqlite3.DatabaseError("required memory schema objects are missing")
        row = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memory_fts'"
        ).fetchone()
        if row is None or "fts5" not in str(row["sql"]).casefold():
            raise sqlite3.DatabaseError("memory_fts is not an FTS5 virtual table")
        meta = db.execute(
            "SELECT schema_version FROM memory_meta WHERE singleton = 1"
        ).fetchone()
        if meta is None or int(meta["schema_version"]) != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("memory metadata schema version is inconsistent")
        if int(db.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise sqlite3.DatabaseError("foreign key enforcement is unavailable")

    def _assert_writer(self, writer_token: object) -> None:
        if writer_token is not self._writer_token:
            raise MemoryStoreAuthorizationError("writer token rejected")
        if threading.get_ident() != self._writer_thread_id:
            raise MemoryStoreAuthorizationError("write attempted outside the bound writer thread")
        if self._closed or self._writer_connection is None:
            raise MemoryStoreReadOnlyError("memory store is read-only")

    def _context_can_recall(self, context: AccessContext) -> bool:
        if not isinstance(context, AccessContext) or context.actor_kind is ActorKind.GUEST:
            return False
        if context.purpose not in {
            AccessPurpose.CONVERSATION,
            AccessPurpose.RECALL,
            AccessPurpose.MANAGE,
        }:
            return False
        return context.acl_epoch == self.metadata()["acl_epoch"]

    def _read_user_version(self) -> int:
        if not self.path.is_file():
            return 0
        db: sqlite3.Connection | None = None
        try:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            db = sqlite3.connect(uri, uri=True, timeout=1.0)
            return int(db.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error:
            return 0
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _item_kind_value(item_kind: MemoryItemKind | str) -> str:
        value = _enum_value(item_kind)
        if value not in _ITEM_CONTRACTS:
            raise ValueError("unknown memory item kind")
        return str(value)

    @staticmethod
    def _decode_item(item_kind: str, payload_json: str) -> Any:
        contract = _ITEM_CONTRACTS.get(item_kind)
        if contract is None:
            raise MemoryStoreError("stored item has an unknown kind")
        return contract.from_dict(json.loads(payload_json))


class _MemoryWriter:
    """Domain-only transaction primitives; deliberately has no SQL passthrough."""

    def __init__(self, store: MemoryStore, db: sqlite3.Connection) -> None:
        self._store = store
        self.__db = db

    def put_subject(self, subject: Subject) -> bool:
        if not isinstance(subject, Subject):
            raise TypeError("subject must be a Subject contract")
        payload = _json(subject.to_dict())
        row = self.__db.execute(
            "SELECT revision, payload_json FROM subjects WHERE subject_id = ?",
            (subject.subject_id,),
        ).fetchone()
        if row is not None:
            if row["payload_json"] == payload:
                return False
            if subject.revision <= int(row["revision"]):
                raise MemoryStoreConflictError("subject revision must advance")
        self.__db.execute(
            """
            INSERT INTO subjects (
                subject_id, schema_version, revision, subject_kind, display_name, status,
                identity_assurance, credential_reference_hash, merged_into_subject_id,
                session_scope_id, created_at_utc, updated_at_utc, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_id) DO UPDATE SET
                schema_version=excluded.schema_version, revision=excluded.revision,
                subject_kind=excluded.subject_kind, display_name=excluded.display_name,
                status=excluded.status, identity_assurance=excluded.identity_assurance,
                credential_reference_hash=excluded.credential_reference_hash,
                merged_into_subject_id=excluded.merged_into_subject_id,
                session_scope_id=excluded.session_scope_id,
                created_at_utc=excluded.created_at_utc, updated_at_utc=excluded.updated_at_utc,
                payload_json=excluded.payload_json
            """,
            (
                subject.subject_id,
                subject.schema_version,
                subject.revision,
                subject.subject_kind.value,
                subject.display_name,
                subject.status.value,
                subject.identity_assurance.value,
                subject.credential_reference_hash,
                subject.merged_into_subject_id,
                subject.session_scope_id,
                subject.created_at_utc,
                subject.updated_at_utc,
                payload,
            ),
        )
        self._bump_meta(acl=True)
        return True

    def put_session_participant(self, participant: SessionParticipant) -> bool:
        if not isinstance(participant, SessionParticipant):
            raise TypeError("participant must be a SessionParticipant contract")
        payload = _json(participant.to_dict())
        row = self.__db.execute(
            "SELECT revision, payload_json FROM session_participants WHERE participant_id = ?",
            (participant.participant_id,),
        ).fetchone()
        if row is not None:
            if row["payload_json"] == payload:
                return False
            if participant.revision <= int(row["revision"]):
                raise MemoryStoreConflictError("participant revision must advance")
        self.__db.execute(
            """
            INSERT INTO session_participants (
                participant_id, schema_version, revision, session_id, subject_id,
                participant_role, identity_assurance, joined_at_utc, left_at_utc,
                server_binding_source, status, created_at_utc, updated_at_utc, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(participant_id) DO UPDATE SET
                schema_version=excluded.schema_version, revision=excluded.revision,
                session_id=excluded.session_id, subject_id=excluded.subject_id,
                participant_role=excluded.participant_role,
                identity_assurance=excluded.identity_assurance,
                joined_at_utc=excluded.joined_at_utc, left_at_utc=excluded.left_at_utc,
                server_binding_source=excluded.server_binding_source, status=excluded.status,
                created_at_utc=excluded.created_at_utc, updated_at_utc=excluded.updated_at_utc,
                payload_json=excluded.payload_json
            """,
            (
                participant.participant_id,
                participant.schema_version,
                participant.revision,
                participant.session_id,
                participant.subject_id,
                participant.participant_role.value,
                participant.identity_assurance.value,
                participant.joined_at_utc,
                participant.left_at_utc,
                participant.server_binding_source,
                participant.status.value,
                participant.created_at_utc,
                participant.updated_at_utc,
                payload,
            ),
        )
        self._bump_meta(acl=True)
        return True

    def put_item(
        self,
        item: ExperienceEpisode | JournalEntry | SharedMemory | UserModelClaim | RelationshipEvent,
        *,
        acl: Mapping[str, Iterable[str]] | None = None,
    ) -> bool:
        kind, item_id = self._kind_and_id(item)
        wire = item.to_dict()
        payload = _json(wire)
        content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        row = self.__db.execute(
            "SELECT item_kind, revision, content_hash FROM memory_items WHERE item_id = ?",
            (item_id,),
        ).fetchone()
        if row is not None:
            if row["item_kind"] != kind:
                raise MemoryStoreConflictError("item ID already belongs to another kind")
            if row["content_hash"] == content_hash:
                return False
            if item.revision <= int(row["revision"]):
                raise MemoryStoreConflictError("item revision must advance")

        envelope = self._envelope(item, kind, item_id, payload, content_hash)
        self.__db.execute(
            """
            INSERT INTO memory_items (
                item_id, item_kind, revision, owner_subject_id, audience, privacy_class,
                status, retention_class, occurred_at_utc, expires_at_utc, source_digest,
                confidence, epistemic_label, deletion_fenced, content_hash,
                created_at_utc, updated_at_utc, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                item_kind=excluded.item_kind, revision=excluded.revision,
                owner_subject_id=excluded.owner_subject_id, audience=excluded.audience,
                privacy_class=excluded.privacy_class, status=excluded.status,
                retention_class=excluded.retention_class,
                occurred_at_utc=excluded.occurred_at_utc, expires_at_utc=excluded.expires_at_utc,
                source_digest=excluded.source_digest, confidence=excluded.confidence,
                epistemic_label=excluded.epistemic_label,
                deletion_fenced=excluded.deletion_fenced, content_hash=excluded.content_hash,
                created_at_utc=excluded.created_at_utc, updated_at_utc=excluded.updated_at_utc,
                payload_json=excluded.payload_json
            """,
            envelope,
        )
        self._put_detail(item, payload)
        self._replace_acl(item, item_id, acl)
        self._replace_confirmations(item)
        self.__db.execute("DELETE FROM memory_fts WHERE item_id = ?", (item_id,))
        if envelope[6] == MemoryItemStatus.ACTIVE.value:
            self.__db.execute(
                "INSERT INTO memory_fts (item_id, item_kind, searchable_text) VALUES (?, ?, ?)",
                (item_id, kind, self._searchable_text(item)),
            )
        self._bump_meta(acl=True, index=True)
        return True

    def delete_item(self, item_kind: MemoryItemKind | str, item_id: str) -> bool:
        kind = self._store._item_kind_value(item_kind)
        _require_text(item_id, "item_id")
        row = self.__db.execute(
            "SELECT 1 FROM memory_items WHERE item_kind = ? AND item_id = ?", (kind, item_id)
        ).fetchone()
        if row is None:
            return False
        self.__db.execute("DELETE FROM memory_fts WHERE item_id = ?", (item_id,))
        self.__db.execute(
            "DELETE FROM memory_items WHERE item_kind = ? AND item_id = ?", (kind, item_id)
        )
        self._bump_meta(acl=True, index=True)
        return True

    def put_derivation_edge(self, edge: DerivationEdge) -> bool:
        if not isinstance(edge, DerivationEdge):
            raise TypeError("edge must be a DerivationEdge contract")
        payload = _json(edge.to_dict())
        existing = self.__db.execute(
            "SELECT payload_json FROM derivation_edges WHERE edge_id = ?", (edge.edge_id,)
        ).fetchone()
        if existing is not None:
            if existing["payload_json"] == payload:
                return False
            raise MemoryStoreConflictError("derivation edge ID conflict")
        try:
            self.__db.execute(
                """
                INSERT INTO derivation_edges (
                    edge_id, schema_version, source_kind, source_id, target_kind, target_id,
                    relation, extractor, extractor_version, source_digest, created_at_utc,
                    active, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.edge_id,
                    edge.schema_version,
                    edge.source_kind.value,
                    edge.source_id,
                    edge.target_kind.value,
                    edge.target_id,
                    edge.relation.value,
                    edge.extractor,
                    edge.extractor_version,
                    edge.source_digest,
                    edge.created_at_utc,
                    int(edge.active),
                    payload,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise MemoryStoreConflictError("duplicate or invalid derivation edge") from exc
        return True

    def put_deletion_request(self, request: DeletionRequest) -> bool:
        if not isinstance(request, DeletionRequest):
            raise TypeError("request must be a DeletionRequest contract")
        payload = _json(request.to_dict())
        row = self.__db.execute(
            "SELECT revision, payload_json FROM deletion_requests WHERE deletion_request_id = ?",
            (request.deletion_request_id,),
        ).fetchone()
        if row is not None:
            if row["payload_json"] == payload:
                return False
            if request.revision <= int(row["revision"]):
                raise MemoryStoreConflictError("deletion request revision must advance")
        actor_hash = hashlib.sha256(request.actor_subject_id.encode("utf-8")).hexdigest()
        self.__db.execute(
            """
            INSERT INTO deletion_requests (
                deletion_request_id, schema_version, revision, actor_subject_id,
                actor_subject_hash, scope, target_selector_json, source_handling, state,
                progress_cursor, attempt, last_reason_code, created_at_utc, updated_at_utc,
                completed_at_utc, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(deletion_request_id) DO UPDATE SET
                schema_version=excluded.schema_version, revision=excluded.revision,
                actor_subject_id=excluded.actor_subject_id,
                actor_subject_hash=excluded.actor_subject_hash, scope=excluded.scope,
                target_selector_json=excluded.target_selector_json,
                source_handling=excluded.source_handling, state=excluded.state,
                progress_cursor=excluded.progress_cursor, attempt=excluded.attempt,
                last_reason_code=excluded.last_reason_code,
                created_at_utc=excluded.created_at_utc, updated_at_utc=excluded.updated_at_utc,
                completed_at_utc=excluded.completed_at_utc, payload_json=excluded.payload_json
            """,
            (
                request.deletion_request_id,
                request.schema_version,
                request.revision,
                request.actor_subject_id,
                actor_hash,
                request.scope.value,
                _json(request.target_selector.to_dict()),
                request.source_handling.value,
                request.state.value,
                request.progress_cursor,
                request.attempt,
                request.last_reason_code,
                request.created_at_utc,
                request.updated_at_utc,
                request.completed_at_utc,
                payload,
            ),
        )
        return True

    def record_terminal_receipt(
        self,
        *,
        receipt_id: str,
        source_store_id: str,
        terminal_row_id: int,
        session_id: str,
        request_id: str,
        source_terminal_event_id: str,
        outcome: str,
        projection_state: str,
        reason_code: str | None = None,
        episode_id: str | None = None,
        attempt: int = 0,
        next_retry_at_utc: str | None = None,
        created_at_utc: str | None = None,
        updated_at_utc: str | None = None,
    ) -> dict[str, Any]:
        for name, value in (
            ("receipt_id", receipt_id),
            ("source_store_id", source_store_id),
            ("session_id", session_id),
            ("request_id", request_id),
            ("source_terminal_event_id", source_terminal_event_id),
        ):
            _require_text(value, name)
        if type(terminal_row_id) is not int or terminal_row_id < 1:
            raise ValueError("terminal_row_id must be a positive integer")
        outcome = str(_enum_value(outcome))
        if outcome not in _TERMINAL_OUTCOMES:
            raise ValueError("unknown terminal outcome")
        if projection_state not in _PROJECTION_STATES:
            raise ValueError("unknown projection state")
        _require_non_negative(attempt, "attempt")
        now = _utc_now()
        created_at_utc = created_at_utc or now
        updated_at_utc = updated_at_utc or now
        existing = self.__db.execute(
            "SELECT * FROM terminal_projection_receipts "
            "WHERE (source_store_id = ? AND session_id = ? AND request_id = ?) "
            "OR source_terminal_event_id = ?",
            (source_store_id, session_id, request_id, source_terminal_event_id),
        ).fetchall()
        for row in existing:
            same_identity = (
                row["source_store_id"] == source_store_id
                and row["session_id"] == session_id
                and row["request_id"] == request_id
                and row["source_terminal_event_id"] == source_terminal_event_id
            )
            if not same_identity:
                raise MemoryStoreConflictError("terminal receipt uniqueness conflict")
            if row["outcome"] != outcome or int(row["terminal_row_id"]) != terminal_row_id:
                raise MemoryStoreConflictError("terminal receipt evidence changed")
            mutable = (
                projection_state,
                reason_code,
                episode_id,
                attempt,
                next_retry_at_utc,
            )
            stored = (
                row["projection_state"],
                row["reason_code"],
                row["episode_id"],
                int(row["attempt"]),
                row["next_retry_at_utc"],
            )
            if mutable == stored:
                return dict(row)
            self.__db.execute(
                "UPDATE terminal_projection_receipts SET projection_state = ?, "
                "reason_code = ?, episode_id = ?, attempt = ?, next_retry_at_utc = ?, "
                "updated_at_utc = ? WHERE receipt_id = ?",
                (
                    projection_state,
                    reason_code,
                    episode_id,
                    attempt,
                    next_retry_at_utc,
                    updated_at_utc,
                    row["receipt_id"],
                ),
            )
            updated = self.__db.execute(
                "SELECT * FROM terminal_projection_receipts WHERE receipt_id = ?",
                (row["receipt_id"],),
            ).fetchone()
            assert updated is not None
            return dict(updated)
        self.__db.execute(
            """
            INSERT INTO terminal_projection_receipts (
                receipt_id, source_store_id, terminal_row_id, session_id, request_id,
                source_terminal_event_id, outcome, projection_state, reason_code,
                episode_id, attempt, next_retry_at_utc, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                source_store_id,
                terminal_row_id,
                session_id,
                request_id,
                source_terminal_event_id,
                outcome,
                projection_state,
                reason_code,
                episode_id,
                attempt,
                next_retry_at_utc,
                created_at_utc,
                updated_at_utc,
            ),
        )
        row = self.__db.execute(
            "SELECT * FROM terminal_projection_receipts WHERE receipt_id = ?", (receipt_id,)
        ).fetchone()
        assert row is not None
        return dict(row)

    def set_source_progress(self, source_store_id: str, terminal_cursor: int) -> None:
        _require_text(source_store_id, "source_store_id")
        _require_non_negative(terminal_cursor, "terminal_cursor")
        row = self.__db.execute(
            "SELECT source_store_id, terminal_cursor FROM memory_meta WHERE singleton = 1"
        ).fetchone()
        assert row is not None
        existing = row["source_store_id"]
        if existing is not None and existing != source_store_id:
            raise MemoryStoreConflictError("source store ID is already bound")
        if terminal_cursor < int(row["terminal_cursor"]):
            raise MemoryStoreConflictError("terminal cursor cannot move backwards")
        self.__db.execute(
            "UPDATE memory_meta SET source_store_id = ?, terminal_cursor = ?, "
            "updated_at_utc = ? WHERE singleton = 1",
            (source_store_id, terminal_cursor, _utc_now()),
        )

    def _put_detail(self, item: Any, payload: str) -> None:
        if isinstance(item, ExperienceEpisode):
            self.__db.execute(
                """
                INSERT INTO experience_episodes (
                    episode_id, schema_version, revision, session_id, request_id,
                    participant_subject_ids_json, started_at_utc, ended_at_utc, outcome,
                    what_happened, javis_attention, intent_summary, action_summary,
                    verified_result_summary, meaning_for_user, meaning_for_javis,
                    source_terminal_event_id, source_terminal_sequence, source_sequence_domain,
                    source_message_ids_json, source_event_ids_json, extractor_version, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO UPDATE SET
                    schema_version=excluded.schema_version, revision=excluded.revision,
                    session_id=excluded.session_id, request_id=excluded.request_id,
                    participant_subject_ids_json=excluded.participant_subject_ids_json,
                    started_at_utc=excluded.started_at_utc, ended_at_utc=excluded.ended_at_utc,
                    outcome=excluded.outcome, what_happened=excluded.what_happened,
                    javis_attention=excluded.javis_attention,
                    intent_summary=excluded.intent_summary, action_summary=excluded.action_summary,
                    verified_result_summary=excluded.verified_result_summary,
                    meaning_for_user=excluded.meaning_for_user,
                    meaning_for_javis=excluded.meaning_for_javis,
                    source_terminal_event_id=excluded.source_terminal_event_id,
                    source_terminal_sequence=excluded.source_terminal_sequence,
                    source_sequence_domain=excluded.source_sequence_domain,
                    source_message_ids_json=excluded.source_message_ids_json,
                    source_event_ids_json=excluded.source_event_ids_json,
                    extractor_version=excluded.extractor_version, payload_json=excluded.payload_json
                """,
                (
                    item.episode_id, item.schema_version, item.revision, item.session_id,
                    item.request_id, _json(item.participant_subject_ids), item.started_at_utc,
                    item.ended_at_utc, item.outcome.value, item.what_happened,
                    item.javis_attention, item.intent_summary, item.action_summary,
                    item.verified_result_summary, item.meaning_for_user, item.meaning_for_javis,
                    item.source_terminal_event_id, item.source_terminal_sequence,
                    item.source_sequence_domain, _json(item.source_message_ids),
                    _json(item.source_event_ids), item.extractor_version, payload,
                ),
            )
        elif isinstance(item, JournalEntry):
            self.__db.execute(
                """
                INSERT INTO journal_entries (
                    entry_id, schema_version, revision, range_started_at_utc,
                    range_ended_at_utc, title, body, source_episode_ids_json,
                    entry_kind, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    schema_version=excluded.schema_version, revision=excluded.revision,
                    range_started_at_utc=excluded.range_started_at_utc,
                    range_ended_at_utc=excluded.range_ended_at_utc,
                    title=excluded.title, body=excluded.body,
                    source_episode_ids_json=excluded.source_episode_ids_json,
                    entry_kind=excluded.entry_kind, payload_json=excluded.payload_json
                """,
                (
                    item.entry_id, item.schema_version, item.revision,
                    item.range_started_at_utc, item.range_ended_at_utc, item.title, item.body,
                    _json(item.source_episode_ids), item.entry_kind.value, payload,
                ),
            )
        elif isinstance(item, SharedMemory):
            self.__db.execute(
                """
                INSERT INTO shared_memories (
                    shared_memory_id, schema_version, revision, proposal_revision,
                    source_episode_ids_json, proposed_text, participant_subject_ids_json,
                    status, confirmed_at_utc, revoked_at_utc, audience_subject_ids_json,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(shared_memory_id) DO UPDATE SET
                    schema_version=excluded.schema_version, revision=excluded.revision,
                    proposal_revision=excluded.proposal_revision,
                    source_episode_ids_json=excluded.source_episode_ids_json,
                    proposed_text=excluded.proposed_text,
                    participant_subject_ids_json=excluded.participant_subject_ids_json,
                    status=excluded.status, confirmed_at_utc=excluded.confirmed_at_utc,
                    revoked_at_utc=excluded.revoked_at_utc,
                    audience_subject_ids_json=excluded.audience_subject_ids_json,
                    payload_json=excluded.payload_json
                """,
                (
                    item.shared_memory_id, item.schema_version, item.revision,
                    item.proposal_revision, _json(item.source_episode_ids), item.proposed_text,
                    _json(item.participant_subject_ids), item.status.value,
                    item.confirmed_at_utc, item.revoked_at_utc,
                    _json(item.audience_subject_ids), payload,
                ),
            )
        elif isinstance(item, UserModelClaim):
            self.__db.execute(
                """
                INSERT INTO user_model_claims (
                    claim_id, schema_version, revision, subject_id, predicate, value_type,
                    value_json, epistemic_class, sensitivity, source_evidence_ids_json,
                    contradiction_claim_ids_json, supersedes_claim_id, acl_subject_ids_json,
                    confirmed_at_utc, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    schema_version=excluded.schema_version, revision=excluded.revision,
                    subject_id=excluded.subject_id, predicate=excluded.predicate,
                    value_type=excluded.value_type, value_json=excluded.value_json,
                    epistemic_class=excluded.epistemic_class, sensitivity=excluded.sensitivity,
                    source_evidence_ids_json=excluded.source_evidence_ids_json,
                    contradiction_claim_ids_json=excluded.contradiction_claim_ids_json,
                    supersedes_claim_id=excluded.supersedes_claim_id,
                    acl_subject_ids_json=excluded.acl_subject_ids_json,
                    confirmed_at_utc=excluded.confirmed_at_utc, payload_json=excluded.payload_json
                """,
                (
                    item.claim_id, item.schema_version, item.revision, item.subject_id,
                    item.predicate, item.value_type.value, _json(item.value),
                    item.epistemic_class.value, item.sensitivity.value,
                    _json(item.source_evidence_ids), _json(item.contradiction_claim_ids),
                    item.supersedes_claim_id, _json(item.acl_subject_ids),
                    item.confirmed_at_utc, payload,
                ),
            )
        elif isinstance(item, RelationshipEvent):
            self.__db.execute(
                """
                INSERT INTO relationship_events (
                    relationship_event_id, schema_version, revision, subject_ids_json,
                    event_kind, summary, source_evidence_ids_json, occurred_at_utc, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relationship_event_id) DO UPDATE SET
                    schema_version=excluded.schema_version, revision=excluded.revision,
                    subject_ids_json=excluded.subject_ids_json, event_kind=excluded.event_kind,
                    summary=excluded.summary,
                    source_evidence_ids_json=excluded.source_evidence_ids_json,
                    occurred_at_utc=excluded.occurred_at_utc, payload_json=excluded.payload_json
                """,
                (
                    item.relationship_event_id, item.schema_version, item.revision,
                    _json(item.subject_ids), item.event_kind.value, item.summary,
                    _json(item.source_evidence_ids), item.occurred_at_utc, payload,
                ),
            )
        else:
            raise TypeError("item must be a memory contract")

    def _replace_acl(
        self,
        item: Any,
        item_id: str,
        supplied: Mapping[str, Iterable[str]] | None,
    ) -> None:
        permissions: dict[str, set[str]] = {
            item.owner_subject_id: {"read", "manage", "delete"}
        }
        audience_subjects: Iterable[str] = ()
        if isinstance(item, SharedMemory):
            audience_subjects = item.audience_subject_ids
        elif isinstance(item, UserModelClaim):
            audience_subjects = item.acl_subject_ids
        elif item.audience is Audience.PARTICIPANTS:
            if isinstance(item, ExperienceEpisode):
                audience_subjects = item.participant_subject_ids
            elif isinstance(item, RelationshipEvent):
                audience_subjects = item.subject_ids
        for subject_id in audience_subjects:
            permissions.setdefault(subject_id, set()).add("read")
        if supplied is not None:
            for subject_id, values in supplied.items():
                _require_text(subject_id, "ACL subject_id")
                normalized = {str(_enum_value(value)) for value in values}
                if not normalized <= _READ_PERMISSIONS:
                    raise ValueError("unknown ACL permission")
                permissions.setdefault(subject_id, set()).update(normalized)
        self.__db.execute("DELETE FROM memory_acl WHERE item_id = ?", (item_id,))
        now = _utc_now()
        for subject_id in sorted(permissions):
            for permission in sorted(permissions[subject_id]):
                self.__db.execute(
                    "INSERT INTO memory_acl (item_id, subject_id, permission, "
                    "granted_by_subject_id, created_at_utc) VALUES (?, ?, ?, ?, ?)",
                    (item_id, subject_id, permission, item.owner_subject_id, now),
                )

    def _replace_confirmations(self, item: Any) -> None:
        if not isinstance(item, SharedMemory):
            return
        self.__db.execute(
            "DELETE FROM shared_confirmations WHERE shared_memory_id = ?",
            (item.shared_memory_id,),
        )
        for receipt in item.confirmation_receipts:
            self.__db.execute(
                "INSERT INTO shared_confirmations (receipt_id, shared_memory_id, subject_id, "
                "proposal_revision, confirmed_at_utc) VALUES (?, ?, ?, ?, ?)",
                (
                    receipt.receipt_id,
                    item.shared_memory_id,
                    receipt.subject_id,
                    receipt.proposal_revision,
                    receipt.confirmed_at_utc,
                ),
            )

    def _bump_meta(self, *, acl: bool = False, index: bool = False) -> None:
        self.__db.execute(
            "UPDATE memory_meta SET acl_epoch = acl_epoch + ?, "
            "index_generation = index_generation + ?, updated_at_utc = ? WHERE singleton = 1",
            (int(acl), int(index), _utc_now()),
        )

    @staticmethod
    def _kind_and_id(item: Any) -> tuple[str, str]:
        if isinstance(item, ExperienceEpisode):
            return MemoryItemKind.EXPERIENCE_EPISODE.value, item.episode_id
        if isinstance(item, JournalEntry):
            return MemoryItemKind.JOURNAL_ENTRY.value, item.entry_id
        if isinstance(item, SharedMemory):
            return MemoryItemKind.SHARED_MEMORY.value, item.shared_memory_id
        if isinstance(item, UserModelClaim):
            return MemoryItemKind.USER_MODEL_CLAIM.value, item.claim_id
        if isinstance(item, RelationshipEvent):
            return MemoryItemKind.RELATIONSHIP_EVENT.value, item.relationship_event_id
        raise TypeError("item must be a memory contract")

    @staticmethod
    def _envelope(
        item: Any, kind: str, item_id: str, payload: str, content_hash: str
    ) -> tuple[Any, ...]:
        if isinstance(item, SharedMemory):
            if item.status is SharedMemoryStatus.CONFIRMED:
                status = MemoryItemStatus.ACTIVE.value
            elif item.status is SharedMemoryStatus.DELETION_FENCED:
                status = MemoryItemStatus.DELETION_FENCED.value
            elif item.status is SharedMemoryStatus.PROPOSED:
                status = MemoryItemStatus.CANDIDATE.value
            else:
                status = MemoryItemStatus.SUPERSEDED.value
            epistemic = "confirmed_shared"
            confidence = 1.0
        elif isinstance(item, UserModelClaim):
            status = (
                MemoryItemStatus.ACTIVE.value
                if item.status in {ClaimStatus.ACTIVE, ClaimStatus.CONFIRMED}
                else item.status.value
            )
            epistemic = (
                "explicit_statement"
                if item.epistemic_class.value == "explicit_statement"
                else "interpretation"
            )
            confidence = item.confidence
        elif isinstance(item, RelationshipEvent):
            status = (
                MemoryItemStatus.ACTIVE.value
                if item.status is RelationshipEventStatus.ACTIVE
                else item.status.value
            )
            epistemic = "evidence_derived"
            confidence = 1.0
        else:
            status = item.status.value
            epistemic = "interpretation" if isinstance(item, JournalEntry) else "evidence_derived"
            confidence = item.confidence if isinstance(item, ExperienceEpisode) else 1.0
        occurred = (
            item.ended_at_utc
            if isinstance(item, ExperienceEpisode)
            else item.range_ended_at_utc
            if isinstance(item, JournalEntry)
            else item.occurred_at_utc
            if isinstance(item, RelationshipEvent)
            else item.confirmed_at_utc or item.updated_at_utc
            if isinstance(item, (SharedMemory, UserModelClaim))
            else item.updated_at_utc
        )
        retention = getattr(item, "retention_class", None)
        return (
            item_id,
            kind,
            item.revision,
            item.owner_subject_id,
            item.audience.value,
            item.privacy_class.value,
            status,
            None if retention is None else retention.value,
            occurred,
            item.expires_at_utc,
            item.source_digest,
            confidence,
            epistemic,
            int(status == MemoryItemStatus.DELETION_FENCED.value),
            content_hash,
            item.created_at_utc,
            item.updated_at_utc,
            payload,
        )

    @staticmethod
    def _searchable_text(item: Any) -> str:
        if isinstance(item, ExperienceEpisode):
            return "\n".join(
                (
                    item.what_happened,
                    item.javis_attention,
                    item.intent_summary,
                    item.action_summary,
                    item.verified_result_summary,
                    item.meaning_for_user,
                    item.meaning_for_javis,
                )
            )
        if isinstance(item, JournalEntry):
            return f"{item.title}\n{item.body}"
        if isinstance(item, SharedMemory):
            return item.proposed_text
        if isinstance(item, UserModelClaim):
            return f"{item.predicate}\n{item.value}"
        if isinstance(item, RelationshipEvent):
            return item.summary
        raise TypeError("item must be a memory contract")


__all__ = [
    "DATABASE_RELATIVE_PATH",
    "MemoryStore",
    "MemoryStoreAuthorizationError",
    "MemoryStoreConflictError",
    "MemoryStoreError",
    "MemoryStoreReadOnlyError",
    "SCHEMA_VERSION",
]
