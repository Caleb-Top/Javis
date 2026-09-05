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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.life.memory.contracts import (
    AccessContext,
    AccessPurpose,
    ActorKind,
    Audience,
    BindingStatus,
    ClaimStatus,
    ConfirmSharedMemory,
    CorrectMemory,
    DeletionRequest,
    DeletionScope,
    DeletionState,
    DerivationEdge,
    DerivationRelation,
    ExperienceEpisode,
    ForgetMemory,
    HandoffLease,
    IdentityAssurance,
    JournalEntry,
    MemoryItemKind,
    MemoryItemStatus,
    MigrateLegacyBatch,
    ParticipantRole,
    ParticipantStatus,
    PrivacyClass,
    ProposeSharedMemory,
    RecallQuery,
    RejectSharedMemory,
    RelationshipEvent,
    RelationshipEventStatus,
    RetentionClass,
    RevokeSharedMemory,
    SessionGenerationState,
    SessionParticipant,
    SharedConfirmationReceipt,
    SharedMemory,
    SharedMemoryStatus,
    SourceHandling,
    Subject,
    SubjectBinding,
    SubjectKind,
    SubjectStatus,
    UserModelClaim,
)


DATABASE_RELATIVE_PATH = Path("memory") / "autobiographical.sqlite3"
SCHEMA_VERSION = 6
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
_REQUIRED_V3_TABLES = _REQUIRED_V2_TABLES | {"shared_decisions"}
_REQUIRED_V4_TABLES = _REQUIRED_V3_TABLES | {"correction_receipts", "deletion_audits"}
_REQUIRED_V5_TABLES = _REQUIRED_V4_TABLES | {
    "legacy_memory_candidates",
    "legacy_migration_batches",
}
_REQUIRED_V6_TABLES = _REQUIRED_V5_TABLES | {
    "subject_bindings",
    "session_generations",
    "handoff_leases",
    "claim_decisions",
    "claim_source_suppressions",
    "relationship_view_meta",
    "shared_confirmation_sets",
}
_REQUIRED_V6_COLUMNS = {
    "subjects": {
        "aliases_json",
        "created_by_subject_id",
        "assurance_ceiling",
        "privacy_class",
    },
    "session_participants": {
        "session_generation",
        "binding_id",
        "lease_expires_at_utc",
        "active",
    },
    "user_model_claims": {"claim_status", "confirmation_event_id"},
    "relationship_events": {"direction", "confidence", "view_eligible"},
    "shared_memories": {
        "confirmation_set_revision",
        "required_confirmer_subject_ids_json",
    },
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

_MIGRATION_3 = (
    """
    CREATE TABLE shared_decisions (
        decision_id TEXT PRIMARY KEY,
        shared_memory_id TEXT NOT NULL REFERENCES shared_memories(shared_memory_id) ON DELETE CASCADE,
        actor_subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        decision TEXT NOT NULL CHECK (decision IN ('reject', 'revoke')),
        expected_revision INTEGER NOT NULL CHECK (expected_revision >= 1),
        decided_at_utc TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_shared_decisions_memory ON shared_decisions(shared_memory_id, decision)",
)

_MIGRATION_4 = (
    """
    CREATE TABLE correction_receipts (
        correction_id TEXT PRIMARY KEY,
        actor_subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        target_kind TEXT NOT NULL,
        target_id TEXT NOT NULL,
        expected_revision INTEGER NOT NULL CHECK (expected_revision >= 1),
        corrected_kind TEXT NOT NULL,
        corrected_id TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        UNIQUE (target_kind, target_id, corrected_id)
    )
    """,
    "CREATE INDEX idx_correction_target ON correction_receipts(target_kind, target_id)",
    """
    CREATE TABLE deletion_audits (
        deletion_request_id TEXT PRIMARY KEY
            REFERENCES deletion_requests(deletion_request_id) ON DELETE CASCADE,
        actor_subject_hash TEXT NOT NULL,
        scope TEXT NOT NULL,
        source_handling TEXT NOT NULL,
        selector_hash TEXT NOT NULL,
        target_count INTEGER NOT NULL CHECK (target_count >= 0),
        source_count INTEGER NOT NULL CHECK (source_count >= 0),
        reason_code TEXT NOT NULL,
        completed_at_utc TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_deletion_audits_completed ON deletion_audits(completed_at_utc)",
)

_MIGRATION_5 = (
    """
    CREATE TABLE legacy_memory_candidates (
        candidate_id TEXT PRIMARY KEY,
        migration_id TEXT NOT NULL,
        manifest_digest TEXT NOT NULL,
        source_relative_path_hash TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        item_kind TEXT NOT NULL,
        disposition TEXT NOT NULL CHECK (disposition IN ('candidate', 'quarantined')),
        reason_codes_json TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        UNIQUE (migration_id, source_relative_path_hash, source_sha256)
    )
    """,
    """
    CREATE TABLE legacy_migration_batches (
        migration_id TEXT NOT NULL,
        batch_index INTEGER NOT NULL CHECK (batch_index >= 0),
        manifest_digest TEXT NOT NULL,
        candidate_set_digest TEXT NOT NULL,
        candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
        candidate_count_quarantined INTEGER NOT NULL CHECK (candidate_count_quarantined >= 0),
        candidate_count_reviewable INTEGER NOT NULL CHECK (candidate_count_reviewable >= 0),
        created_at_utc TEXT NOT NULL,
        PRIMARY KEY (migration_id, batch_index)
    )
    """,
    "CREATE INDEX idx_legacy_candidate_disposition "
    "ON legacy_memory_candidates(migration_id, disposition, candidate_id)",
)

_MIGRATION_6 = (
    """
    CREATE TABLE IF NOT EXISTS subject_bindings (
        binding_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
        runtime_boot_id TEXT NOT NULL,
        client_id_hash TEXT NOT NULL,
        assurance TEXT NOT NULL,
        binding_source TEXT NOT NULL,
        status TEXT NOT NULL,
        issued_at_utc TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL,
        revoked_at_utc TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_generations (
        session_id TEXT PRIMARY KEY,
        generation INTEGER NOT NULL CHECK (generation >= 0),
        guest_present INTEGER NOT NULL DEFAULT 1 CHECK (guest_present IN (0, 1)),
        privacy_fenced INTEGER NOT NULL DEFAULT 1 CHECK (privacy_fenced IN (0, 1)),
        owner_subject_id TEXT REFERENCES subjects(subject_id) ON DELETE SET NULL,
        active_binding_id TEXT REFERENCES subject_bindings(binding_id) ON DELETE SET NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS handoff_leases (
        lease_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        issuer_subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
        target_subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
        session_id TEXT NOT NULL REFERENCES session_generations(session_id) ON DELETE CASCADE,
        session_generation INTEGER NOT NULL CHECK (session_generation >= 0),
        assurance TEXT NOT NULL,
        status TEXT NOT NULL,
        issued_at_utc TEXT NOT NULL,
        expires_at_utc TEXT NOT NULL,
        consumed_at_utc TEXT,
        revoked_at_utc TEXT,
        payload_json TEXT NOT NULL,
        CHECK (issuer_subject_id <> target_subject_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claim_decisions (
        decision_id TEXT PRIMARY KEY,
        claim_id TEXT NOT NULL REFERENCES user_model_claims(claim_id) ON DELETE CASCADE,
        actor_subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE RESTRICT,
        decision TEXT NOT NULL CHECK (decision IN ('confirm', 'reject')),
        expected_revision INTEGER NOT NULL CHECK (expected_revision >= 1),
        decided_at_utc TEXT NOT NULL,
        UNIQUE (claim_id, actor_subject_id, expected_revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claim_source_suppressions (
        suppression_id TEXT PRIMARY KEY,
        subject_id TEXT NOT NULL REFERENCES subjects(subject_id) ON DELETE CASCADE,
        predicate TEXT NOT NULL,
        source_evidence_id TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        created_at_utc TEXT NOT NULL,
        UNIQUE (subject_id, predicate, source_evidence_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS relationship_view_meta (
        subject_id TEXT PRIMARY KEY REFERENCES subjects(subject_id) ON DELETE CASCADE,
        source_revision INTEGER NOT NULL DEFAULT 0 CHECK (source_revision >= 0),
        acl_epoch INTEGER NOT NULL DEFAULT 0 CHECK (acl_epoch >= 0),
        invalidated INTEGER NOT NULL DEFAULT 1 CHECK (invalidated IN (0, 1)),
        updated_at_utc TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shared_confirmation_sets (
        shared_memory_id TEXT NOT NULL REFERENCES shared_memories(shared_memory_id) ON DELETE CASCADE,
        confirmation_set_revision INTEGER NOT NULL CHECK (confirmation_set_revision >= 1),
        proposal_revision INTEGER NOT NULL CHECK (proposal_revision >= 1),
        required_subject_ids_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('open', 'confirmed', 'revoked', 'superseded')),
        created_at_utc TEXT NOT NULL,
        updated_at_utc TEXT NOT NULL,
        PRIMARY KEY (shared_memory_id, confirmation_set_revision)
    )
    """,
    "ALTER TABLE subjects ADD COLUMN aliases_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE subjects ADD COLUMN created_by_subject_id TEXT REFERENCES subjects(subject_id) ON DELETE SET NULL",
    "ALTER TABLE subjects ADD COLUMN assurance_ceiling TEXT NOT NULL DEFAULT 'guest'",
    "ALTER TABLE subjects ADD COLUMN privacy_class TEXT NOT NULL DEFAULT 'user_private'",
    "ALTER TABLE session_participants ADD COLUMN session_generation INTEGER NOT NULL DEFAULT 0 CHECK (session_generation >= 0)",
    "ALTER TABLE session_participants ADD COLUMN binding_id TEXT REFERENCES subject_bindings(binding_id) ON DELETE SET NULL",
    "ALTER TABLE session_participants ADD COLUMN lease_expires_at_utc TEXT",
    "ALTER TABLE session_participants ADD COLUMN active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1))",
    "ALTER TABLE user_model_claims ADD COLUMN claim_status TEXT NOT NULL DEFAULT 'quarantined'",
    "ALTER TABLE user_model_claims ADD COLUMN confirmation_event_id TEXT",
    "ALTER TABLE relationship_events ADD COLUMN direction TEXT NOT NULL DEFAULT 'mutual'",
    "ALTER TABLE relationship_events ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0)",
    "ALTER TABLE relationship_events ADD COLUMN view_eligible INTEGER NOT NULL DEFAULT 0 CHECK (view_eligible IN (0, 1))",
    "ALTER TABLE shared_memories ADD COLUMN confirmation_set_revision INTEGER NOT NULL DEFAULT 1 CHECK (confirmation_set_revision >= 1)",
    "ALTER TABLE shared_memories ADD COLUMN required_confirmer_subject_ids_json TEXT NOT NULL DEFAULT '[]'",
    """
    INSERT OR IGNORE INTO session_generations (
        session_id, generation, guest_present, privacy_fenced,
        owner_subject_id, active_binding_id, revision, created_at_utc, updated_at_utc
    )
    SELECT session_id, 0, 1, 1, NULL, NULL, 1,
           MIN(created_at_utc), MAX(updated_at_utc)
      FROM session_participants
     GROUP BY session_id
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_subjects_active_primary ON subjects(subject_kind) WHERE subject_kind = 'primary_user' AND status = 'active'",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_subject_bindings_active_client ON subject_bindings(runtime_boot_id, client_id_hash) WHERE status = 'active'",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_session_generation_owner ON session_participants(session_id, session_generation) WHERE active = 1 AND participant_role IN ('primary', 'owner')",
    "CREATE INDEX IF NOT EXISTS idx_subject_bindings_subject_status ON subject_bindings(subject_id, status, expires_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_handoff_leases_session_status ON handoff_leases(session_id, status, expires_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_claim_decisions_claim ON claim_decisions(claim_id, decision)",
    "CREATE INDEX IF NOT EXISTS idx_claim_suppressions_subject ON claim_source_suppressions(subject_id, predicate)",
    "CREATE INDEX IF NOT EXISTS idx_relationship_events_subjects ON relationship_events(event_kind, view_eligible, occurred_at_utc)",
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

    def active_primary_subject(self) -> Subject | None:
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT payload_json FROM subjects "
                "WHERE subject_kind = 'primary_user' AND status = 'active' "
                "ORDER BY subject_id LIMIT 2"
            ).fetchall()
        if len(rows) > 1:
            raise MemoryStoreConflictError("multiple active primary subjects")
        return None if not rows else Subject.from_dict(json.loads(rows[0]["payload_json"]))

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

    def get_subject_binding(self, binding_id: str) -> SubjectBinding | None:
        _require_text(binding_id, "binding_id")
        with self._read_connection() as db:
            row = db.execute(
                "SELECT payload_json FROM subject_bindings WHERE binding_id = ?",
                (binding_id,),
            ).fetchone()
        return None if row is None else SubjectBinding.from_dict(json.loads(row["payload_json"]))

    def active_subject_binding(
        self, runtime_boot_id: str, client_id_hash: str
    ) -> SubjectBinding | None:
        _require_text(runtime_boot_id, "runtime_boot_id")
        _require_text(client_id_hash, "client_id_hash")
        with self._read_connection() as db:
            row = db.execute(
                "SELECT payload_json FROM subject_bindings "
                "WHERE runtime_boot_id = ? AND client_id_hash = ? AND status = 'active'",
                (runtime_boot_id, client_id_hash),
            ).fetchone()
        return None if row is None else SubjectBinding.from_dict(json.loads(row["payload_json"]))

    def get_session_generation(self, session_id: str) -> SessionGenerationState | None:
        _require_text(session_id, "session_id")
        with self._read_connection() as db:
            row = db.execute(
                "SELECT * FROM session_generations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionGenerationState(
            schema_version=1,
            session_id=str(row["session_id"]),
            generation=int(row["generation"]),
            guest_present=bool(row["guest_present"]),
            privacy_fenced=bool(row["privacy_fenced"]),
            owner_subject_id=row["owner_subject_id"],
            active_binding_id=row["active_binding_id"],
            revision=int(row["revision"]),
            created_at_utc=str(row["created_at_utc"]),
            updated_at_utc=str(row["updated_at_utc"]),
        )

    def get_handoff_lease(self, lease_id: str) -> HandoffLease | None:
        _require_text(lease_id, "lease_id")
        with self._read_connection() as db:
            row = db.execute(
                "SELECT payload_json FROM handoff_leases WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
        return None if row is None else HandoffLease.from_dict(json.loads(row["payload_json"]))

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

    def get_projection_suppression(
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
                "SELECT suppression_id, source_store_id, session_id, request_id, "
                "reason_code, created_at_utc FROM projection_suppressions "
                "WHERE source_store_id = ? AND session_id = ? AND request_id = ?",
                (source_store_id, session_id, request_id),
            ).fetchone()
        return None if row is None else dict(row)

    def deletion_status(
        self,
        deletion_request_id: str,
        *,
        actor_subject_id: str | None = None,
    ) -> dict[str, Any] | None:
        _require_text(deletion_request_id, "deletion_request_id")
        if actor_subject_id is not None:
            _require_text(actor_subject_id, "actor_subject_id")
        owner_filter = ""
        parameters: tuple[str, ...] = (deletion_request_id,)
        if actor_subject_id is not None:
            actor_hash = hashlib.sha256(actor_subject_id.encode("utf-8")).hexdigest()
            owner_filter = (
                " AND (actor_subject_id = ? OR (actor_subject_id IS NULL AND EXISTS ("
                "SELECT 1 FROM deletion_audits AS audit "
                "WHERE audit.deletion_request_id = deletion_requests.deletion_request_id "
                "AND audit.actor_subject_hash = ?)))"
            )
            parameters = (deletion_request_id, actor_subject_id, actor_hash)
        with self._read_connection() as db:
            row = db.execute(
                "SELECT deletion_request_id, scope, source_handling, state, attempt, "
                "last_reason_code, created_at_utc, updated_at_utc, completed_at_utc "
                "FROM deletion_requests WHERE deletion_request_id = ?" + owner_filter,
                parameters,
            ).fetchone()
            if row is None:
                return None
            audit = db.execute(
                "SELECT target_count, source_count, reason_code FROM deletion_audits "
                "WHERE deletion_request_id = ?",
                (deletion_request_id,),
            ).fetchone()
            target_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM deletion_targets WHERE deletion_request_id = ?",
                    (deletion_request_id,),
                ).fetchone()[0]
            )
            source_count = int(
                db.execute(
                    "SELECT COUNT(*) FROM deletion_targets WHERE deletion_request_id = ? "
                    "AND session_id IS NOT NULL AND request_id IS NOT NULL",
                    (deletion_request_id,),
                ).fetchone()[0]
            )
        result = dict(row)
        if audit is not None:
            target_count = int(audit["target_count"])
            source_count = int(audit["source_count"])
            result["completion_reason_code"] = str(audit["reason_code"])
        result["target_count"] = target_count
        result["source_count"] = source_count
        return result

    def pending_deletion_ids(self) -> tuple[str, ...]:
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT deletion_request_id FROM deletion_requests "
                "WHERE state != ? ORDER BY created_at_utc, deletion_request_id",
                (DeletionState.VERIFIED.value,),
            ).fetchall()
        return tuple(str(row["deletion_request_id"]) for row in rows)

    def deletion_sources(self, deletion_request_id: str) -> tuple[dict[str, str], ...]:
        _require_text(deletion_request_id, "deletion_request_id")
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT DISTINCT source_store_id, session_id, request_id "
                "FROM deletion_targets WHERE deletion_request_id = ? "
                "AND source_store_id IS NOT NULL AND session_id IS NOT NULL "
                "AND request_id IS NOT NULL ORDER BY source_store_id, session_id, request_id",
                (deletion_request_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def legacy_migration_status(self, migration_id: str) -> dict[str, Any]:
        _require_text(migration_id, "migration_id")
        with self._read_connection() as db:
            batch = db.execute(
                "SELECT COUNT(*) AS batch_count, COALESCE(SUM(candidate_count), 0) AS copied "
                "FROM legacy_migration_batches WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            rows = db.execute(
                "SELECT disposition, COUNT(*) AS count FROM legacy_memory_candidates "
                "WHERE migration_id = ? GROUP BY disposition",
                (migration_id,),
            ).fetchall()
        counts = {str(row["disposition"]): int(row["count"]) for row in rows}
        return {
            "migration_id": migration_id,
            "batch_count": int(batch["batch_count"]),
            "copied_count": int(batch["copied"]),
            "candidate_count": counts.get("candidate", 0),
            "quarantined_count": counts.get("quarantined", 0),
            "active_count": 0,
            "fts_visible_count": 0,
        }

    def legacy_migration_summary(self) -> dict[str, Any]:
        """Return content-free migration counts for the management surface."""

        with self._read_connection() as db:
            rows = db.execute(
                "SELECT disposition, COUNT(*) AS count, MAX(created_at_utc) AS latest "
                "FROM legacy_memory_candidates GROUP BY disposition"
            ).fetchall()
            batch = db.execute(
                "SELECT COUNT(*) AS batch_count, MAX(created_at_utc) AS latest "
                "FROM legacy_migration_batches"
            ).fetchone()
        counts = {str(row["disposition"]): int(row["count"]) for row in rows}
        timestamps = [str(row["latest"]) for row in rows if row["latest"]]
        if batch is not None and batch["latest"]:
            timestamps.append(str(batch["latest"]))
        return {
            "batch_count": 0 if batch is None else int(batch["batch_count"]),
            "candidate_count": counts.get("candidate", 0),
            "quarantined_count": counts.get("quarantined", 0),
            "last_scan_at_utc": max(timestamps) if timestamps else None,
        }

    def list_items(
        self,
        access_context: AccessContext,
        item_kinds: Iterable[MemoryItemKind | str],
        *,
        statuses: Iterable[MemoryItemStatus | str] = (MemoryItemStatus.ACTIVE,),
        cursor: str | None = None,
        limit: int = 100,
    ) -> tuple[tuple[Any, ...], str | None]:
        """List a bounded SQL-visible page without exposing authority metadata."""

        if not self._context_can_recall(access_context):
            return (), None
        kinds = tuple(dict.fromkeys(self._item_kind_value(kind) for kind in item_kinds))
        if not kinds or len(kinds) > len(_ITEM_CONTRACTS):
            raise ValueError("item_kinds must be a bounded non-empty set")
        status_values = tuple(
            dict.fromkeys(
                value.value if isinstance(value, MemoryItemStatus) else str(value)
                for value in statuses
            )
        )
        allowed_statuses = {status.value for status in MemoryItemStatus}
        if (
            not status_values
            or len(status_values) > len(allowed_statuses)
            or any(value not in allowed_statuses for value in status_values)
        ):
            raise ValueError("statuses contain an unsupported memory status")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if cursor is not None:
            _require_text(cursor, "cursor")

        kind_slots = ",".join("?" for _ in kinds)
        status_slots = ",".join("?" for _ in status_values)
        audiences = _AUDIENCES_BY_CEILING[access_context.audience_ceiling]
        audience_slots = ",".join("?" for _ in audiences)
        filters = [
            f"i.item_kind IN ({kind_slots})",
            f"i.status IN ({status_slots})",
            "i.deletion_fenced = 0",
            "(i.expires_at_utc IS NULL OR i.expires_at_utc > ?)",
            f"i.audience IN ({audience_slots})",
            "(i.audience = 'guest' OR "
            "(i.audience = 'owner_private' AND i.owner_subject_id = ?) OR "
            "(i.audience IN ('participants', 'explicit_shared') AND EXISTS ("
            "SELECT 1 FROM memory_acl AS a WHERE a.item_id = i.item_id "
            "AND a.subject_id = ? AND a.permission IN ('read', 'manage'))))",
        ]
        parameters: list[Any] = [
            *kinds,
            *status_values,
            access_context.issued_at_utc,
            *audiences,
            access_context.actor_subject_id,
            access_context.actor_subject_id,
        ]
        if cursor is not None:
            filters.append("i.item_id > ?")
            parameters.append(cursor)
        parameters.append(limit + 1)
        with self._read_connection() as db:
            rows = db.execute(
                "SELECT i.item_id, i.item_kind, i.payload_json FROM memory_items AS i WHERE "
                + " AND ".join(filters)
                + " ORDER BY i.item_id LIMIT ?",
                tuple(parameters),
            ).fetchall()
        page = rows[:limit]
        next_cursor = str(page[-1]["item_id"]) if len(rows) > limit and page else None
        return (
            tuple(
                self._decode_item(str(row["item_kind"]), str(row["payload_json"]))
                for row in page
            ),
            next_cursor,
        )

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
        visible_sql = (
            "INSERT INTO visible_memory_ids(item_id, item_kind) "
            "SELECT i.item_id, i.item_kind FROM memory_items AS i WHERE "
            + " AND ".join(filters)
        )
        rank_sql = (
            "SELECT v.item_kind, i.payload_json FROM visible_memory_ids AS v "
            "JOIN memory_fts AS f ON f.item_id = v.item_id "
            "JOIN memory_items AS i ON i.item_id = v.item_id "
            "WHERE memory_fts MATCH ? ORDER BY bm25(memory_fts), v.item_id LIMIT ?"
        )
        try:
            with self._search_connection() as db:
                db.execute("BEGIN")
                db.execute(
                    "CREATE TEMP TABLE visible_memory_ids ("
                    "item_id TEXT PRIMARY KEY, item_kind TEXT NOT NULL) WITHOUT ROWID"
                )
                db.execute(visible_sql, parameters[:-2])
                rows = db.execute(rank_sql, parameters[-2:]).fetchall()
                db.rollback()
        except sqlite3.OperationalError:
            return ()
        return tuple(self._decode_item(row["item_kind"], row["payload_json"]) for row in rows)

    def put_subject(self, subject: Subject, *, writer_token: object) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_subject(subject)

    def put_subject_binding(
        self, binding: SubjectBinding, *, writer_token: object
    ) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_subject_binding(binding)

    def retire_subject_bindings(
        self,
        runtime_boot_id: str,
        at_utc: str,
        *,
        writer_token: object,
    ) -> int:
        with self._writer_transaction(writer_token) as writer:
            return writer.retire_subject_bindings(runtime_boot_id, at_utc)

    def revoke_subject_binding(
        self,
        binding_id: str,
        revoked_at_utc: str,
        *,
        writer_token: object,
    ) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.revoke_subject_binding(binding_id, revoked_at_utc)

    def revoke_subject_bindings(
        self,
        subject_id: str,
        revoked_at_utc: str,
        *,
        writer_token: object,
    ) -> int:
        with self._writer_transaction(writer_token) as writer:
            return writer.revoke_subject_bindings(subject_id, revoked_at_utc)

    def put_session_generation(
        self, state: SessionGenerationState, *, writer_token: object
    ) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_session_generation(state)

    def put_handoff_lease(
        self, lease: HandoffLease, *, writer_token: object
    ) -> bool:
        with self._writer_transaction(writer_token) as writer:
            return writer.put_handoff_lease(lease)

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

    def propose_shared_memory(
        self,
        command: ProposeSharedMemory,
        *,
        writer_token: object,
    ) -> SharedMemory:
        with self._writer_transaction(writer_token) as writer:
            return writer.propose_shared_memory(command)

    def confirm_shared_memory(
        self,
        command: ConfirmSharedMemory,
        *,
        writer_token: object,
    ) -> SharedMemory:
        with self._writer_transaction(writer_token) as writer:
            return writer.confirm_shared_memory(command)

    def reject_shared_memory(
        self,
        command: RejectSharedMemory,
        *,
        writer_token: object,
    ) -> SharedMemory:
        with self._writer_transaction(writer_token) as writer:
            return writer.reject_shared_memory(command)

    def revoke_shared_memory(
        self,
        command: RevokeSharedMemory,
        *,
        writer_token: object,
    ) -> SharedMemory:
        with self._writer_transaction(writer_token) as writer:
            return writer.revoke_shared_memory(command)

    def correct_memory(
        self, command: CorrectMemory, *, writer_token: object
    ) -> ExperienceEpisode | JournalEntry:
        with self._writer_transaction(writer_token) as writer:
            return writer.correct_memory(command)

    def migrate_legacy_batch(
        self,
        command: MigrateLegacyBatch,
        candidates: Iterable[Mapping[str, Any]],
        *,
        writer_token: object,
    ) -> dict[str, Any]:
        frozen = tuple(dict(candidate) for candidate in candidates)
        with self._writer_transaction(writer_token) as writer:
            return writer.migrate_legacy_batch(command, frozen)

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

    def prepare_deletion(
        self, command: ForgetMemory, *, writer_token: object
    ) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.prepare_deletion(command)

    def fence_deletion(
        self, deletion_request_id: str, *, writer_token: object
    ) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.fence_deletion(deletion_request_id)

    def advance_deletion_state(
        self,
        deletion_request_id: str,
        *,
        expected: DeletionState,
        target: DeletionState,
        writer_token: object,
    ) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.advance_deletion_state(deletion_request_id, expected, target)

    def delete_deletion_primary_rows(
        self, deletion_request_id: str, *, writer_token: object
    ) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.delete_deletion_primary_rows(deletion_request_id)

    def delete_deletion_derivations(
        self, deletion_request_id: str, *, writer_token: object
    ) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.delete_deletion_derivations(deletion_request_id)

    def delete_deletion_fts(
        self, deletion_request_id: str, *, writer_token: object
    ) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.delete_deletion_fts(deletion_request_id)

    def verify_deletion(
        self, deletion_request_id: str, *, writer_token: object
    ) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.verify_deletion(deletion_request_id)

    def record_deletion_failure(
        self,
        deletion_request_id: str,
        reason_code: str,
        *,
        writer_token: object,
    ) -> dict[str, Any]:
        with self._writer_transaction(writer_token) as writer:
            return writer.record_deletion_failure(deletion_request_id, reason_code)

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

    def complete_terminal_projection(
        self,
        receipt_values: Mapping[str, Any],
        *,
        writer_token: object,
    ) -> dict[str, Any]:
        """Atomically finalize a terminal without creating an episode."""

        values = dict(receipt_values)
        if values.get("projection_state") not in {"excluded", "not_selected"}:
            raise ValueError("terminal completion requires a final non-episode state")
        if values.get("episode_id") is not None:
            raise ValueError("non-episode terminal completion cannot reference an episode")
        cursor = values.get("terminal_row_id")
        source_store_id = values.get("source_store_id")
        with self._writer_transaction(writer_token) as writer:
            receipt = writer.record_terminal_receipt(**values)
            writer.set_source_progress(str(source_store_id), int(cursor))
            return receipt

    def project_episode(
        self,
        episode: ExperienceEpisode,
        receipt_values: Mapping[str, Any],
        *,
        writer_token: object,
    ) -> dict[str, Any]:
        """Atomically persist an episode, finalize its receipt, and move the cursor."""

        if not isinstance(episode, ExperienceEpisode):
            raise TypeError("episode must be an ExperienceEpisode")
        values = dict(receipt_values)
        if (
            values.get("outcome") != "completed"
            or values.get("session_id") != episode.session_id
            or values.get("request_id") != episode.request_id
            or values.get("source_terminal_event_id") != episode.source_terminal_event_id
            or values.get("terminal_row_id") is None
        ):
            raise ValueError("episode and terminal receipt identity must match")
        values.update(
            projection_state="projected",
            reason_code="selected_episode",
            episode_id=episode.episode_id,
            next_retry_at_utc=None,
        )
        with self._writer_transaction(writer_token) as writer:
            created = writer.put_item(episode)
            receipt = writer.record_terminal_receipt(**values)
            writer.set_source_progress(
                str(values["source_store_id"]), int(values["terminal_row_id"])
            )
            return {"episode_created": created, "receipt": receipt}

    def put_journal_projection(
        self,
        entry: JournalEntry,
        derivation_edges: Iterable[DerivationEdge],
        *,
        writer_token: object,
    ) -> dict[str, Any]:
        """Persist a journal and all required derivation edges in one transaction."""

        if not isinstance(entry, JournalEntry):
            raise TypeError("entry must be a JournalEntry")
        edges = tuple(derivation_edges)
        if not edges or any(not isinstance(edge, DerivationEdge) for edge in edges):
            raise ValueError("journal projection requires derivation edges")
        if {edge.source_id for edge in edges} != set(entry.source_episode_ids) or any(
            edge.source_kind is not MemoryItemKind.EXPERIENCE_EPISODE
            or edge.target_kind is not MemoryItemKind.JOURNAL_ENTRY
            or edge.relation.value != "derived_from"
            or edge.target_id != entry.entry_id
            or not edge.active
            for edge in edges
        ):
            raise ValueError("journal derivation closure does not match its sources")
        with self._writer_transaction(writer_token) as writer:
            created = writer.put_item(entry)
            edge_results = tuple(writer.put_derivation_edge(edge) for edge in edges)
            return {"journal_created": created, "edge_results": edge_results}

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

    @contextmanager
    def _search_connection(self) -> Iterator[sqlite3.Connection]:
        """Open a read-only main database that may write only to TEMP tables."""

        if not self.path.is_file():
            raise MemoryStoreReadOnlyError("memory database is unavailable")
        uri = self.path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=5.0)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
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
                version = 2
            if version == 2:
                self._validate_v2_schema(db)
                self._apply_migration(db, 3, _MIGRATION_3)
                version = 3
            if version == 3:
                self._validate_v3_schema(db)
                self._apply_migration(db, 4, _MIGRATION_4)
                version = 4
            if version == 4:
                self._validate_v4_schema(db)
                self._apply_migration(db, 5, _MIGRATION_5)
                version = 5
            if version == 5:
                self._validate_v5_schema(db)
                self._apply_migration(db, 6, _MIGRATION_6)
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
                self._execute_migration_statement(db, statement)
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
    def _execute_migration_statement(db: sqlite3.Connection, statement: str) -> None:
        tokens = statement.strip().split()
        if (
            len(tokens) >= 6
            and tokens[0].casefold() == "alter"
            and tokens[1].casefold() == "table"
            and tokens[3].casefold() == "add"
            and tokens[4].casefold() == "column"
        ):
            table_name = tokens[2]
            column_name = tokens[5]
            columns = {
                str(row["name"])
                for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if column_name in columns:
                return
        db.execute(statement)

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
    def _validate_v2_schema(db: sqlite3.Connection, expected_version: int = 2) -> None:
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
        if meta is None or int(meta["schema_version"]) != expected_version:
            raise sqlite3.DatabaseError("v2 memory metadata schema version is inconsistent")

    @staticmethod
    def _validate_schema(db: sqlite3.Connection) -> None:
        MemoryStore._validate_v5_schema(db, SCHEMA_VERSION)
        rows = db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        names = {str(row["name"]) for row in rows}
        if _REQUIRED_V6_TABLES - names:
            raise sqlite3.DatabaseError("required memory schema objects are missing")
        for table_name, required_columns in _REQUIRED_V6_COLUMNS.items():
            actual_columns = {
                str(row["name"])
                for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if required_columns - actual_columns:
                raise sqlite3.DatabaseError(
                    f"required v6 columns are missing from {table_name}"
                )
        meta = db.execute(
            "SELECT schema_version FROM memory_meta WHERE singleton = 1"
        ).fetchone()
        if meta is None or int(meta["schema_version"]) != SCHEMA_VERSION:
            raise sqlite3.DatabaseError("memory metadata schema version is inconsistent")
        if int(db.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise sqlite3.DatabaseError("foreign key enforcement is unavailable")

    @staticmethod
    def _validate_v3_schema(db: sqlite3.Connection, expected_version: int = 3) -> None:
        MemoryStore._validate_v2_schema(db, expected_version)
        rows = db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        names = {str(row["name"]) for row in rows}
        if _REQUIRED_V3_TABLES - names:
            raise sqlite3.DatabaseError("required v3 memory schema objects are missing")

    @staticmethod
    def _validate_v4_schema(db: sqlite3.Connection, expected_version: int = 4) -> None:
        MemoryStore._validate_v3_schema(db, expected_version)
        rows = db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        names = {str(row["name"]) for row in rows}
        if _REQUIRED_V4_TABLES - names:
            raise sqlite3.DatabaseError("required v4 memory schema objects are missing")

    @staticmethod
    def _validate_v5_schema(db: sqlite3.Connection, expected_version: int = 5) -> None:
        MemoryStore._validate_v4_schema(db, expected_version)
        rows = db.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        names = {str(row["name"]) for row in rows}
        if _REQUIRED_V5_TABLES - names:
            raise sqlite3.DatabaseError("required v5 memory schema objects are missing")

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
        if (
            subject.subject_kind is SubjectKind.PRIMARY_USER
            and subject.status is SubjectStatus.ACTIVE
        ):
            conflict = self.__db.execute(
                "SELECT subject_id FROM subjects "
                "WHERE subject_kind = 'primary_user' AND status = 'active' "
                "AND subject_id <> ?",
                (subject.subject_id,),
            ).fetchone()
            if conflict is not None:
                raise MemoryStoreConflictError("an active primary subject already exists")
        self.__db.execute(
            """
            INSERT INTO subjects (
                subject_id, schema_version, revision, subject_kind, display_name, status,
                identity_assurance, credential_reference_hash, merged_into_subject_id,
                session_scope_id, created_at_utc, updated_at_utc, payload_json,
                aliases_json, created_by_subject_id, assurance_ceiling, privacy_class
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_id) DO UPDATE SET
                schema_version=excluded.schema_version, revision=excluded.revision,
                subject_kind=excluded.subject_kind, display_name=excluded.display_name,
                status=excluded.status, identity_assurance=excluded.identity_assurance,
                credential_reference_hash=excluded.credential_reference_hash,
                merged_into_subject_id=excluded.merged_into_subject_id,
                session_scope_id=excluded.session_scope_id,
                created_at_utc=excluded.created_at_utc, updated_at_utc=excluded.updated_at_utc,
                payload_json=excluded.payload_json, aliases_json=excluded.aliases_json,
                created_by_subject_id=excluded.created_by_subject_id,
                assurance_ceiling=excluded.assurance_ceiling,
                privacy_class=excluded.privacy_class
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
                _json(subject.aliases),
                subject.created_by_subject_id,
                subject.assurance_ceiling.value,
                subject.privacy_class.value,
            ),
        )
        self._bump_meta(acl=True)
        return True

    def put_subject_binding(self, binding: SubjectBinding) -> bool:
        if not isinstance(binding, SubjectBinding):
            raise TypeError("binding must be a SubjectBinding contract")
        payload = _json(binding.to_dict())
        row = self.__db.execute(
            "SELECT revision, payload_json FROM subject_bindings WHERE binding_id = ?",
            (binding.binding_id,),
        ).fetchone()
        if row is not None:
            if row["payload_json"] == payload:
                return False
            if binding.revision <= int(row["revision"]):
                raise MemoryStoreConflictError("binding revision must advance")
        if binding.status is BindingStatus.ACTIVE:
            conflict = self.__db.execute(
                "SELECT binding_id FROM subject_bindings "
                "WHERE runtime_boot_id = ? AND client_id_hash = ? "
                "AND status = 'active' AND binding_id <> ?",
                (binding.runtime_boot_id, binding.client_id_hash, binding.binding_id),
            ).fetchone()
            if conflict is not None:
                raise MemoryStoreConflictError("client already has an active subject binding")
        self.__db.execute(
            """
            INSERT INTO subject_bindings (
                binding_id, schema_version, revision, subject_id, runtime_boot_id,
                client_id_hash, assurance, binding_source, status, issued_at_utc,
                expires_at_utc, revoked_at_utc, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(binding_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                revision=excluded.revision,
                subject_id=excluded.subject_id,
                runtime_boot_id=excluded.runtime_boot_id,
                client_id_hash=excluded.client_id_hash,
                assurance=excluded.assurance,
                binding_source=excluded.binding_source,
                status=excluded.status,
                issued_at_utc=excluded.issued_at_utc,
                expires_at_utc=excluded.expires_at_utc,
                revoked_at_utc=excluded.revoked_at_utc,
                payload_json=excluded.payload_json
            """,
            (
                binding.binding_id,
                binding.schema_version,
                binding.revision,
                binding.subject_id,
                binding.runtime_boot_id,
                binding.client_id_hash,
                binding.assurance.value,
                binding.binding_source.value,
                binding.status.value,
                binding.issued_at_utc,
                binding.expires_at_utc,
                binding.revoked_at_utc,
                payload,
            ),
        )
        self._bump_meta(acl=True)
        return True

    def retire_subject_bindings(self, runtime_boot_id: str, at_utc: str) -> int:
        _require_text(runtime_boot_id, "runtime_boot_id")
        _require_text(at_utc, "at_utc")
        rows = self.__db.execute(
            "SELECT payload_json FROM subject_bindings WHERE status = 'active' "
            "ORDER BY binding_id"
        ).fetchall()
        retired = 0
        for row in rows:
            binding = SubjectBinding.from_dict(json.loads(row["payload_json"]))
            if binding.expires_at_utc <= at_utc:
                status = BindingStatus.EXPIRED
                revoked_at = None
            elif binding.runtime_boot_id != runtime_boot_id:
                status = BindingStatus.REVOKED
                revoked_at = at_utc
            else:
                continue
            self.put_subject_binding(
                replace(
                    binding,
                    revision=binding.revision + 1,
                    status=status,
                    revoked_at_utc=revoked_at,
                )
            )
            retired += 1
        return retired

    def revoke_subject_binding(self, binding_id: str, revoked_at_utc: str) -> bool:
        _require_text(binding_id, "binding_id")
        _require_text(revoked_at_utc, "revoked_at_utc")
        row = self.__db.execute(
            "SELECT payload_json FROM subject_bindings WHERE binding_id = ?",
            (binding_id,),
        ).fetchone()
        if row is None:
            return False
        binding = SubjectBinding.from_dict(json.loads(row["payload_json"]))
        if binding.status is not BindingStatus.ACTIVE:
            return False
        return self.put_subject_binding(
            replace(
                binding,
                revision=binding.revision + 1,
                status=BindingStatus.REVOKED,
                revoked_at_utc=revoked_at_utc,
            )
        )

    def revoke_subject_bindings(self, subject_id: str, revoked_at_utc: str) -> int:
        _require_text(subject_id, "subject_id")
        _require_text(revoked_at_utc, "revoked_at_utc")
        rows = self.__db.execute(
            "SELECT binding_id FROM subject_bindings "
            "WHERE subject_id = ? AND status = 'active' ORDER BY binding_id",
            (subject_id,),
        ).fetchall()
        revoked = 0
        for row in rows:
            if self.revoke_subject_binding(str(row["binding_id"]), revoked_at_utc):
                revoked += 1
        return revoked

    def put_session_generation(self, state: SessionGenerationState) -> bool:
        if not isinstance(state, SessionGenerationState):
            raise TypeError("state must be a SessionGenerationState contract")
        row = self.__db.execute(
            "SELECT * FROM session_generations WHERE session_id = ?",
            (state.session_id,),
        ).fetchone()
        if row is not None:
            current_generation = int(row["generation"])
            current_revision = int(row["revision"])
            if state.generation < current_generation:
                raise MemoryStoreConflictError("session generation cannot move backwards")
            if state.revision <= current_revision:
                current_values = (
                    int(row["generation"]),
                    bool(row["guest_present"]),
                    bool(row["privacy_fenced"]),
                    row["owner_subject_id"],
                    row["active_binding_id"],
                    int(row["revision"]),
                    str(row["created_at_utc"]),
                    str(row["updated_at_utc"]),
                )
                requested_values = (
                    state.generation,
                    state.guest_present,
                    state.privacy_fenced,
                    state.owner_subject_id,
                    state.active_binding_id,
                    state.revision,
                    state.created_at_utc,
                    state.updated_at_utc,
                )
                if current_values == requested_values:
                    return False
                raise MemoryStoreConflictError("session generation revision must advance")
        self.__db.execute(
            """
            INSERT INTO session_generations (
                session_id, generation, guest_present, privacy_fenced,
                owner_subject_id, active_binding_id, revision, created_at_utc, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                generation=excluded.generation,
                guest_present=excluded.guest_present,
                privacy_fenced=excluded.privacy_fenced,
                owner_subject_id=excluded.owner_subject_id,
                active_binding_id=excluded.active_binding_id,
                revision=excluded.revision,
                created_at_utc=excluded.created_at_utc,
                updated_at_utc=excluded.updated_at_utc
            """,
            (
                state.session_id,
                state.generation,
                int(state.guest_present),
                int(state.privacy_fenced),
                state.owner_subject_id,
                state.active_binding_id,
                state.revision,
                state.created_at_utc,
                state.updated_at_utc,
            ),
        )
        self._bump_meta(acl=True)
        return True

    def put_handoff_lease(self, lease: HandoffLease) -> bool:
        if not isinstance(lease, HandoffLease):
            raise TypeError("lease must be a HandoffLease contract")
        payload = _json(lease.to_dict())
        row = self.__db.execute(
            "SELECT revision, payload_json FROM handoff_leases WHERE lease_id = ?",
            (lease.lease_id,),
        ).fetchone()
        if row is not None:
            if row["payload_json"] == payload:
                return False
            if lease.revision <= int(row["revision"]):
                raise MemoryStoreConflictError("handoff lease revision must advance")
        self.__db.execute(
            """
            INSERT INTO handoff_leases (
                lease_id, schema_version, revision, issuer_subject_id,
                target_subject_id, session_id, session_generation, assurance,
                status, issued_at_utc, expires_at_utc, consumed_at_utc,
                revoked_at_utc, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lease_id) DO UPDATE SET
                schema_version=excluded.schema_version,
                revision=excluded.revision,
                issuer_subject_id=excluded.issuer_subject_id,
                target_subject_id=excluded.target_subject_id,
                session_id=excluded.session_id,
                session_generation=excluded.session_generation,
                assurance=excluded.assurance,
                status=excluded.status,
                issued_at_utc=excluded.issued_at_utc,
                expires_at_utc=excluded.expires_at_utc,
                consumed_at_utc=excluded.consumed_at_utc,
                revoked_at_utc=excluded.revoked_at_utc,
                payload_json=excluded.payload_json
            """,
            (
                lease.lease_id,
                lease.schema_version,
                lease.revision,
                lease.issuer_subject_id,
                lease.target_subject_id,
                lease.session_id,
                lease.session_generation,
                lease.assurance.value,
                lease.status.value,
                lease.issued_at_utc,
                lease.expires_at_utc,
                lease.consumed_at_utc,
                lease.revoked_at_utc,
                payload,
            ),
        )
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
        if (
            participant.active
            and participant.participant_role
            in {ParticipantRole.PRIMARY, ParticipantRole.OWNER}
        ):
            conflict = self.__db.execute(
                "SELECT participant_id FROM session_participants "
                "WHERE session_id = ? AND session_generation = ? AND active = 1 "
                "AND participant_role IN ('primary', 'owner') AND participant_id <> ?",
                (
                    participant.session_id,
                    participant.session_generation,
                    participant.participant_id,
                ),
            ).fetchone()
            if conflict is not None:
                raise MemoryStoreConflictError(
                    "session generation already has an active owner"
                )
        self.__db.execute(
            """
            INSERT INTO session_participants (
                participant_id, schema_version, revision, session_id, subject_id,
                participant_role, identity_assurance, joined_at_utc, left_at_utc,
                server_binding_source, status, created_at_utc, updated_at_utc, payload_json,
                session_generation, binding_id, lease_expires_at_utc, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(participant_id) DO UPDATE SET
                schema_version=excluded.schema_version, revision=excluded.revision,
                session_id=excluded.session_id, subject_id=excluded.subject_id,
                participant_role=excluded.participant_role,
                identity_assurance=excluded.identity_assurance,
                joined_at_utc=excluded.joined_at_utc, left_at_utc=excluded.left_at_utc,
                server_binding_source=excluded.server_binding_source, status=excluded.status,
                created_at_utc=excluded.created_at_utc, updated_at_utc=excluded.updated_at_utc,
                payload_json=excluded.payload_json,
                session_generation=excluded.session_generation,
                binding_id=excluded.binding_id,
                lease_expires_at_utc=excluded.lease_expires_at_utc,
                active=excluded.active
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
                participant.session_generation,
                participant.binding_id,
                participant.lease_expires_at_utc,
                int(participant.active),
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

    def propose_shared_memory(self, command: ProposeSharedMemory) -> SharedMemory:
        if not isinstance(command, ProposeSharedMemory):
            raise TypeError("command must be ProposeSharedMemory")
        context = self._require_shared_actor(command.access_context, command.issued_at_utc)
        shared_memory_id = "shared-" + hashlib.sha256(
            f"{context.actor_subject_id}\0{command.idempotency_key}".encode("utf-8")
        ).hexdigest()[:40]
        existing = self._shared_memory(shared_memory_id)
        if existing is not None:
            if (
                existing.owner_subject_id == context.actor_subject_id
                and existing.source_episode_ids == tuple(command.source_episode_ids)
                and existing.proposed_text == command.proposed_text
                and existing.participant_subject_ids
                == tuple(sorted(context.participant_subject_ids))
            ):
                return existing
            raise MemoryStoreConflictError("shared proposal idempotency conflict")
        source_rows = self._live_source_episodes(
            command.source_episode_ids,
            context,
            command.issued_at_utc,
        )
        source_digest = hashlib.sha256(
            _json(
                {
                    "source": [
                        (str(row["item_id"]), str(row["content_hash"]))
                        for row in source_rows
                    ],
                    "text": command.proposed_text,
                    "participants": list(context.participant_subject_ids),
                }
            ).encode("utf-8")
        ).hexdigest()
        proposed = SharedMemory(
            schema_version=1,
            shared_memory_id=shared_memory_id,
            revision=1,
            proposal_revision=1,
            owner_subject_id=context.actor_subject_id,
            audience=Audience.EXPLICIT_SHARED,
            privacy_class=PrivacyClass.USER_PRIVATE,
            source_episode_ids=tuple(command.source_episode_ids),
            proposed_text=command.proposed_text,
            participant_subject_ids=tuple(sorted(context.participant_subject_ids)),
            confirmation_receipts=(),
            status=SharedMemoryStatus.PROPOSED,
            confirmed_at_utc=None,
            revoked_at_utc=None,
            audience_subject_ids=tuple(sorted(context.participant_subject_ids)),
            source_digest=source_digest,
            retention_class=RetentionClass.MEMORY_CANDIDATE,
            expires_at_utc=None,
            created_at_utc=command.issued_at_utc,
            updated_at_utc=command.issued_at_utc,
        )
        self.put_item(proposed)
        for row in source_rows:
            edge_id = "edge-" + hashlib.sha256(
                f"{row['item_id']}\0{shared_memory_id}\0derived_from".encode("utf-8")
            ).hexdigest()[:40]
            self.put_derivation_edge(
                DerivationEdge(
                    schema_version=1,
                    edge_id=edge_id,
                    source_kind=MemoryItemKind.EXPERIENCE_EPISODE,
                    source_id=str(row["item_id"]),
                    target_kind=MemoryItemKind.SHARED_MEMORY,
                    target_id=shared_memory_id,
                    relation=DerivationRelation.DERIVED_FROM,
                    extractor="deterministic.shared.v1",
                    extractor_version="1",
                    source_digest=str(row["source_digest"]),
                    created_at_utc=command.issued_at_utc,
                    active=True,
                )
            )
        return proposed

    def confirm_shared_memory(self, command: ConfirmSharedMemory) -> SharedMemory:
        if not isinstance(command, ConfirmSharedMemory):
            raise TypeError("command must be ConfirmSharedMemory")
        context = self._require_shared_actor(command.access_context, command.issued_at_utc)
        current = self._shared_memory(command.shared_memory_id)
        if current is None:
            raise MemoryStoreConflictError("shared proposal not found")
        receipt_id = "shared-confirm-" + hashlib.sha256(
            f"{context.actor_subject_id}\0{command.idempotency_key}".encode("utf-8")
        ).hexdigest()[:40]
        receipt_row = self.__db.execute(
            "SELECT shared_memory_id, subject_id, proposal_revision FROM shared_confirmations "
            "WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if receipt_row is not None:
            if (
                str(receipt_row["shared_memory_id"]) == command.shared_memory_id
                and str(receipt_row["subject_id"]) == context.actor_subject_id
                and int(receipt_row["proposal_revision"]) == command.proposal_revision
                and current.status in {SharedMemoryStatus.CONFIRMED, SharedMemoryStatus.REVOKED}
            ):
                return current
            raise MemoryStoreConflictError("shared confirmation idempotency conflict")
        self._require_shared_transition(current, context, command.proposal_revision)
        self._live_source_episodes(
            current.source_episode_ids,
            context,
            command.issued_at_utc,
        )
        receipt = SharedConfirmationReceipt(
            receipt_id=receipt_id,
            subject_id=context.actor_subject_id,
            proposal_revision=current.proposal_revision,
            confirmed_at_utc=command.issued_at_utc,
        )
        confirmed = SharedMemory.from_dict(
            {
                **current.to_dict(),
                "revision": current.revision + 1,
                "confirmation_receipts": [receipt.to_dict()],
                "status": SharedMemoryStatus.CONFIRMED.value,
                "confirmed_at_utc": command.issued_at_utc,
                "updated_at_utc": command.issued_at_utc,
            }
        )
        self.put_item(confirmed)
        return confirmed

    def reject_shared_memory(self, command: RejectSharedMemory) -> SharedMemory:
        if not isinstance(command, RejectSharedMemory):
            raise TypeError("command must be RejectSharedMemory")
        context = self._require_shared_actor(command.access_context, command.issued_at_utc)
        current = self._shared_memory(command.shared_memory_id)
        if current is None:
            raise MemoryStoreConflictError("shared proposal not found")
        decision_id = self._shared_decision_id(context.actor_subject_id, command.idempotency_key)
        replay = self._shared_decision_replay(
            decision_id,
            command.shared_memory_id,
            context.actor_subject_id,
            "reject",
            command.proposal_revision,
        )
        if replay:
            if current.status is SharedMemoryStatus.REJECTED:
                return current
            raise MemoryStoreConflictError("shared rejection replay state mismatch")
        self._require_shared_transition(current, context, command.proposal_revision)
        rejected = SharedMemory.from_dict(
            {
                **current.to_dict(),
                "revision": current.revision + 1,
                "proposed_text": "[rejected]",
                "status": SharedMemoryStatus.REJECTED.value,
                "updated_at_utc": command.issued_at_utc,
            }
        )
        self.put_item(rejected)
        self.__db.execute(
            "INSERT INTO shared_decisions (decision_id, shared_memory_id, actor_subject_id, "
            "decision, expected_revision, decided_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                command.shared_memory_id,
                context.actor_subject_id,
                "reject",
                command.proposal_revision,
                command.issued_at_utc,
            ),
        )
        return rejected

    def revoke_shared_memory(self, command: RevokeSharedMemory) -> SharedMemory:
        if not isinstance(command, RevokeSharedMemory):
            raise TypeError("command must be RevokeSharedMemory")
        context = self._require_shared_actor(command.access_context, command.issued_at_utc)
        current = self._shared_memory(command.shared_memory_id)
        if current is None:
            raise MemoryStoreConflictError("shared memory not found")
        decision_id = self._shared_decision_id(context.actor_subject_id, command.idempotency_key)
        replay = self._shared_decision_replay(
            decision_id,
            command.shared_memory_id,
            context.actor_subject_id,
            "revoke",
            command.expected_revision,
        )
        if replay:
            if current.status is SharedMemoryStatus.REVOKED:
                return current
            raise MemoryStoreConflictError("shared revocation replay state mismatch")
        if current.owner_subject_id != context.actor_subject_id:
            raise MemoryStoreAuthorizationError("shared memory owner mismatch")
        if command.expected_revision != current.revision:
            raise MemoryStoreConflictError("shared memory revision is stale")
        if current.status is SharedMemoryStatus.REVOKED:
            return current
        if current.status is not SharedMemoryStatus.CONFIRMED:
            raise MemoryStoreConflictError("only confirmed shared memory can be revoked")
        revoked = SharedMemory.from_dict(
            {
                **current.to_dict(),
                "revision": current.revision + 1,
                "status": SharedMemoryStatus.REVOKED.value,
                "revoked_at_utc": command.issued_at_utc,
                "updated_at_utc": command.issued_at_utc,
            }
        )
        self.put_item(revoked)
        self.__db.execute(
            "INSERT INTO shared_decisions (decision_id, shared_memory_id, actor_subject_id, "
            "decision, expected_revision, decided_at_utc) VALUES (?, ?, ?, ?, ?, ?)",
            (
                decision_id,
                command.shared_memory_id,
                context.actor_subject_id,
                "revoke",
                command.expected_revision,
                command.issued_at_utc,
            ),
        )
        return revoked

    def correct_memory(self, command: CorrectMemory) -> ExperienceEpisode | JournalEntry:
        if not isinstance(command, CorrectMemory):
            raise TypeError("command must be CorrectMemory")
        context = self._require_memory_manager(command.access_context, command.issued_at_utc)
        if command.target_kind not in {
            MemoryItemKind.EXPERIENCE_EPISODE,
            MemoryItemKind.JOURNAL_ENTRY,
        }:
            raise MemoryStoreConflictError(
                "this memory kind requires a governed replacement workflow"
            )
        correction_id = "correction-" + hashlib.sha256(
            f"{context.actor_subject_id}\0{command.idempotency_key}".encode("utf-8")
        ).hexdigest()
        receipt = self.__db.execute(
            "SELECT actor_subject_id, target_kind, target_id, expected_revision, "
            "corrected_kind, corrected_id FROM correction_receipts WHERE correction_id = ?",
            (correction_id,),
        ).fetchone()
        if receipt is not None:
            if (
                str(receipt["actor_subject_id"]) != context.actor_subject_id
                or str(receipt["target_kind"]) != command.target_kind.value
                or str(receipt["target_id"]) != command.target_id
                or int(receipt["expected_revision"]) != command.expected_revision
            ):
                raise MemoryStoreConflictError("correction idempotency conflict")
            corrected = self.__db.execute(
                "SELECT payload_json FROM memory_items WHERE item_kind = ? AND item_id = ?",
                (str(receipt["corrected_kind"]), str(receipt["corrected_id"])),
            ).fetchone()
            if corrected is None:
                raise MemoryStoreConflictError("corrected memory is no longer available")
            return self._store._decode_item(
                str(receipt["corrected_kind"]), str(corrected["payload_json"])
            )
        row = self.__db.execute(
            "SELECT owner_subject_id, revision, status, deletion_fenced, payload_json, "
            "source_digest FROM memory_items WHERE item_kind = ? AND item_id = ?",
            (command.target_kind.value, command.target_id),
        ).fetchone()
        if row is None:
            raise MemoryStoreConflictError("correction target is unavailable")
        if str(row["owner_subject_id"]) != context.actor_subject_id:
            raise MemoryStoreAuthorizationError("correction target owner mismatch")
        if int(row["revision"]) != command.expected_revision:
            raise MemoryStoreConflictError("correction target revision is stale")
        if str(row["status"]) != MemoryItemStatus.ACTIVE.value or int(row["deletion_fenced"]):
            raise MemoryStoreConflictError("correction target is not active")
        original = self._store._decode_item(
            command.target_kind.value, str(row["payload_json"])
        )
        corrected_id = "corrected-" + hashlib.sha256(
            f"{correction_id}\0{command.target_kind.value}\0{command.target_id}".encode("utf-8")
        ).hexdigest()[:40]
        source_digest = hashlib.sha256(
            _json(
                {
                    "original_source_digest": str(row["source_digest"]),
                    "corrected_text": command.corrected_text,
                    "source_evidence_ids": list(command.source_evidence_ids),
                }
            ).encode("utf-8")
        ).hexdigest()
        if isinstance(original, ExperienceEpisode):
            superseded = ExperienceEpisode.from_dict(
                {
                    **original.to_dict(),
                    "revision": original.revision + 1,
                    "status": MemoryItemStatus.SUPERSEDED.value,
                    "updated_at_utc": command.issued_at_utc,
                }
            )
            corrected = ExperienceEpisode.from_dict(
                {
                    **original.to_dict(),
                    "episode_id": corrected_id,
                    "revision": 1,
                    "what_happened": command.corrected_text,
                    "javis_attention": "The user explicitly corrected this governed memory.",
                    "intent_summary": command.corrected_text,
                    "action_summary": "Javis recorded the explicit correction.",
                    "verified_result_summary": "The correction command passed governance checks.",
                    "meaning_for_user": "",
                    "meaning_for_javis": "",
                    "source_terminal_event_id": correction_id,
                    "source_sequence_domain": f"memory_correction:{original.episode_id}",
                    "source_event_ids": sorted(
                        set(original.source_event_ids) | set(command.source_evidence_ids)
                    ),
                    "source_digest": source_digest,
                    "extractor_version": "deterministic.correction.v1",
                    "confidence": 1.0,
                    "status": MemoryItemStatus.ACTIVE.value,
                    "created_at_utc": command.issued_at_utc,
                    "updated_at_utc": command.issued_at_utc,
                }
            )
        else:
            assert isinstance(original, JournalEntry)
            superseded = JournalEntry.from_dict(
                {
                    **original.to_dict(),
                    "revision": original.revision + 1,
                    "status": MemoryItemStatus.SUPERSEDED.value,
                    "updated_at_utc": command.issued_at_utc,
                }
            )
            corrected = JournalEntry.from_dict(
                {
                    **original.to_dict(),
                    "entry_id": corrected_id,
                    "revision": 1,
                    "title": "Corrected memory",
                    "body": command.corrected_text,
                    "source_digest": source_digest,
                    "status": MemoryItemStatus.ACTIVE.value,
                    "created_at_utc": command.issued_at_utc,
                    "updated_at_utc": command.issued_at_utc,
                }
            )
        self.put_item(superseded)
        self.put_item(corrected)
        corrected_kind, actual_corrected_id = self._kind_and_id(corrected)
        edge = DerivationEdge(
            schema_version=1,
            edge_id="edge-" + hashlib.sha256(
                f"{command.target_kind.value}\0{command.target_id}\0{actual_corrected_id}\0corrects".encode(
                    "utf-8"
                )
            ).hexdigest()[:40],
            source_kind=command.target_kind,
            source_id=command.target_id,
            target_kind=MemoryItemKind(corrected_kind),
            target_id=actual_corrected_id,
            relation=DerivationRelation.CORRECTS,
            extractor="deterministic.correction.v1",
            extractor_version="1",
            source_digest=str(row["source_digest"]),
            created_at_utc=command.issued_at_utc,
            active=True,
        )
        self.put_derivation_edge(edge)
        self.__db.execute(
            "INSERT INTO correction_receipts (correction_id, actor_subject_id, target_kind, "
            "target_id, expected_revision, corrected_kind, corrected_id, created_at_utc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                correction_id,
                context.actor_subject_id,
                command.target_kind.value,
                command.target_id,
                command.expected_revision,
                corrected_kind,
                actual_corrected_id,
                command.issued_at_utc,
            ),
        )
        return corrected

    def _require_memory_manager(
        self, context: AccessContext, issued_at_utc: str
    ) -> AccessContext:
        if (
            not isinstance(context, AccessContext)
            or context.purpose is not AccessPurpose.MANAGE
            or "memory.manage" not in context.capability_scopes
            or context.actor_kind not in {ActorKind.PRIMARY_USER, ActorKind.KNOWN_PERSON}
            or context.identity_assurance
            not in {IdentityAssurance.DESKTOP_CONFIRMED, IdentityAssurance.VERIFIED}
            or context.actor_subject_id not in context.participant_subject_ids
            or not (context.issued_at_utc <= issued_at_utc < context.expires_at_utc)
        ):
            raise MemoryStoreAuthorizationError("memory management access denied")
        row = self.__db.execute(
            "SELECT p.status, p.identity_assurance, p.server_binding_source, s.status AS subject_status "
            "FROM session_participants AS p JOIN subjects AS s ON s.subject_id = p.subject_id "
            "WHERE p.session_id = ? AND p.subject_id = ? AND p.participant_role = 'primary'",
            (context.session_id, context.actor_subject_id),
        ).fetchone()
        if (
            row is None
            or str(row["status"]) != ParticipantStatus.ACTIVE.value
            or str(row["subject_status"]) != SubjectStatus.ACTIVE.value
            or str(row["server_binding_source"]) != "packaged_desktop"
            or str(row["identity_assurance"])
            not in {
                IdentityAssurance.DESKTOP_CONFIRMED.value,
                IdentityAssurance.VERIFIED.value,
            }
        ):
            raise MemoryStoreAuthorizationError("memory management identity binding invalid")
        return context

    def migrate_legacy_batch(
        self,
        command: MigrateLegacyBatch,
        candidates: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        if not isinstance(command, MigrateLegacyBatch):
            raise TypeError("command must be MigrateLegacyBatch")
        self._require_migration_actor(command.access_context, command.issued_at_utc)
        expected_fields = {
            "schema_version",
            "candidate_id",
            "migration_id",
            "manifest_digest",
            "source_relative_path_hash",
            "source_sha256",
            "item_kind",
            "disposition",
            "reason_codes",
            "payload",
            "created_at_utc",
        }
        if not candidates:
            raise ValueError("legacy migration batch cannot be empty")
        candidate_ids = tuple(str(value.get("candidate_id") or "") for value in candidates)
        if len(set(candidate_ids)) != len(candidate_ids) or set(candidate_ids) != set(
            command.candidate_ids
        ):
            raise MemoryStoreConflictError("legacy migration candidate set mismatch")
        normalized: list[dict[str, Any]] = []
        for value in candidates:
            if set(value) != expected_fields:
                raise ValueError("legacy migration candidate schema mismatch")
            item = dict(value)
            if item["schema_version"] != 1:
                raise ValueError("legacy migration candidate schema unsupported")
            if (
                item["migration_id"] != command.migration_id
                or item["manifest_digest"] != command.manifest_digest
            ):
                raise MemoryStoreConflictError("legacy migration manifest binding mismatch")
            for field_name in (
                "manifest_digest",
                "source_relative_path_hash",
                "source_sha256",
            ):
                digest = item[field_name]
                if (
                    type(digest) is not str
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise ValueError(f"{field_name} must be lowercase SHA-256")
            if item["disposition"] not in {"candidate", "quarantined"}:
                raise ValueError("legacy migration disposition invalid")
            reasons = item["reason_codes"]
            if (
                not isinstance(reasons, (list, tuple))
                or not reasons
                or len(reasons) > 16
                or any(type(reason) is not str or not reason or len(reason) > 128 for reason in reasons)
            ):
                raise ValueError("legacy migration reason codes invalid")
            if not isinstance(item["payload"], Mapping):
                raise ValueError("legacy migration payload must be an object")
            encoded_payload = _json(dict(item["payload"]))
            if len(encoded_payload.encode("utf-8")) > 16_384:
                raise ValueError("legacy migration payload is too large")
            item["reason_codes"] = sorted(set(reasons))
            item["payload"] = json.loads(encoded_payload)
            normalized.append(item)
        candidate_set_digest = hashlib.sha256(
            _json(sorted(normalized, key=lambda value: value["candidate_id"])).encode("utf-8")
        ).hexdigest()
        existing_batch = self.__db.execute(
            "SELECT manifest_digest, candidate_set_digest, candidate_count, "
            "candidate_count_quarantined, candidate_count_reviewable "
            "FROM legacy_migration_batches WHERE migration_id = ? AND batch_index = ?",
            (command.migration_id, command.batch_index),
        ).fetchone()
        if existing_batch is not None:
            if (
                str(existing_batch["manifest_digest"]) != command.manifest_digest
                or str(existing_batch["candidate_set_digest"]) != candidate_set_digest
                or int(existing_batch["candidate_count"]) != len(normalized)
            ):
                raise MemoryStoreConflictError("legacy migration batch idempotency conflict")
            return self._legacy_batch_result(
                command.migration_id,
                command.batch_index,
                replayed=True,
            )
        for item in normalized:
            row = self.__db.execute(
                "SELECT migration_id, manifest_digest, source_relative_path_hash, source_sha256, "
                "item_kind, disposition, reason_codes_json, payload_json, created_at_utc "
                "FROM legacy_memory_candidates WHERE candidate_id = ?",
                (item["candidate_id"],),
            ).fetchone()
            values = (
                item["migration_id"],
                item["manifest_digest"],
                item["source_relative_path_hash"],
                item["source_sha256"],
                item["item_kind"],
                item["disposition"],
                _json(item["reason_codes"]),
                _json(item["payload"]),
                item["created_at_utc"],
            )
            if row is not None:
                if tuple(row) != values:
                    raise MemoryStoreConflictError("legacy candidate identity conflict")
                continue
            self.__db.execute(
                "INSERT INTO legacy_memory_candidates (candidate_id, migration_id, "
                "manifest_digest, source_relative_path_hash, source_sha256, item_kind, "
                "disposition, reason_codes_json, payload_json, created_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (item["candidate_id"], *values),
            )
        quarantined = sum(item["disposition"] == "quarantined" for item in normalized)
        reviewable = len(normalized) - quarantined
        self.__db.execute(
            "INSERT INTO legacy_migration_batches (migration_id, batch_index, manifest_digest, "
            "candidate_set_digest, candidate_count, candidate_count_quarantined, "
            "candidate_count_reviewable, created_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                command.migration_id,
                command.batch_index,
                command.manifest_digest,
                candidate_set_digest,
                len(normalized),
                quarantined,
                reviewable,
                command.issued_at_utc,
            ),
        )
        return self._legacy_batch_result(
            command.migration_id,
            command.batch_index,
            replayed=False,
        )

    def _require_migration_actor(
        self, context: AccessContext, issued_at_utc: str
    ) -> AccessContext:
        if (
            not isinstance(context, AccessContext)
            or context.purpose is not AccessPurpose.MIGRATION
            or "memory.migrate" not in context.capability_scopes
            or context.actor_kind is not ActorKind.PRIMARY_USER
            or context.identity_assurance
            not in {IdentityAssurance.DESKTOP_CONFIRMED, IdentityAssurance.VERIFIED}
            or context.actor_subject_id not in context.participant_subject_ids
            or not (context.issued_at_utc <= issued_at_utc < context.expires_at_utc)
        ):
            raise MemoryStoreAuthorizationError("legacy migration access denied")
        row = self.__db.execute(
            "SELECT p.status, p.identity_assurance, p.server_binding_source, s.status AS subject_status "
            "FROM session_participants AS p JOIN subjects AS s ON s.subject_id = p.subject_id "
            "WHERE p.session_id = ? AND p.subject_id = ? AND p.participant_role = 'primary'",
            (context.session_id, context.actor_subject_id),
        ).fetchone()
        if (
            row is None
            or str(row["status"]) != ParticipantStatus.ACTIVE.value
            or str(row["subject_status"]) != SubjectStatus.ACTIVE.value
            or str(row["server_binding_source"]) != "packaged_desktop"
            or str(row["identity_assurance"])
            not in {
                IdentityAssurance.DESKTOP_CONFIRMED.value,
                IdentityAssurance.VERIFIED.value,
            }
        ):
            raise MemoryStoreAuthorizationError("legacy migration identity binding invalid")
        return context

    def _legacy_batch_result(
        self, migration_id: str, batch_index: int, *, replayed: bool
    ) -> dict[str, Any]:
        row = self.__db.execute(
            "SELECT candidate_count, candidate_count_quarantined, "
            "candidate_count_reviewable FROM legacy_migration_batches "
            "WHERE migration_id = ? AND batch_index = ?",
            (migration_id, batch_index),
        ).fetchone()
        return {
            "migration_id": migration_id,
            "batch_index": batch_index,
            "copied_count": int(row["candidate_count"]),
            "quarantined_count": int(row["candidate_count_quarantined"]),
            "candidate_count": int(row["candidate_count_reviewable"]),
            "active_count": 0,
            "fts_visible_count": 0,
            "replayed": replayed,
        }

    @staticmethod
    def _shared_decision_id(actor_subject_id: str, idempotency_key: str) -> str:
        return "shared-decision-" + hashlib.sha256(
            f"{actor_subject_id}\0{idempotency_key}".encode("utf-8")
        ).hexdigest()

    def _shared_decision_replay(
        self,
        decision_id: str,
        shared_memory_id: str,
        actor_subject_id: str,
        decision: str,
        expected_revision: int,
    ) -> bool:
        row = self.__db.execute(
            "SELECT shared_memory_id, actor_subject_id, decision, expected_revision "
            "FROM shared_decisions WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()
        if row is None:
            return False
        if (
            str(row["shared_memory_id"]) != shared_memory_id
            or str(row["actor_subject_id"]) != actor_subject_id
            or str(row["decision"]) != decision
            or int(row["expected_revision"]) != expected_revision
        ):
            raise MemoryStoreConflictError("shared decision idempotency conflict")
        return True

    def _require_shared_actor(
        self,
        context: AccessContext,
        issued_at_utc: str,
    ) -> AccessContext:
        if not isinstance(context, AccessContext):
            raise MemoryStoreAuthorizationError("access context is required")
        if (
            context.purpose is not AccessPurpose.MANAGE
            or "memory.manage" not in context.capability_scopes
            or context.actor_kind is not ActorKind.PRIMARY_USER
            or context.identity_assurance
            not in {IdentityAssurance.DESKTOP_CONFIRMED, IdentityAssurance.VERIFIED}
            or context.audience_ceiling is not Audience.EXPLICIT_SHARED
            or not (context.issued_at_utc <= issued_at_utc < context.expires_at_utc)
        ):
            raise MemoryStoreAuthorizationError("shared memory access denied")
        participant_ids = tuple(sorted(context.participant_subject_ids))
        if context.actor_subject_id not in participant_ids or len(participant_ids) != 2:
            raise MemoryStoreAuthorizationError("shared memory participants invalid")
        placeholders = ",".join("?" for _ in participant_ids)
        rows = self.__db.execute(
            "SELECT p.subject_id, p.participant_role, p.identity_assurance, "
            "p.server_binding_source, p.status, "
            "s.subject_kind, s.status AS subject_status "
            "FROM session_participants AS p JOIN subjects AS s ON s.subject_id = p.subject_id "
            f"WHERE p.session_id = ? AND p.subject_id IN ({placeholders})",
            (context.session_id, *participant_ids),
        ).fetchall()
        if len(rows) != 2 or {str(row["subject_id"]) for row in rows} != set(participant_ids):
            raise MemoryStoreAuthorizationError("shared memory participant binding missing")
        primary = tuple(
            row
            for row in rows
            if str(row["subject_id"]) == context.actor_subject_id
            and str(row["participant_role"]) == ParticipantRole.PRIMARY.value
            and str(row["subject_kind"]) == SubjectKind.PRIMARY_USER.value
        )
        javis = tuple(
            row
            for row in rows
            if str(row["participant_role"]) == ParticipantRole.JAVIS.value
            and str(row["subject_kind"]) == SubjectKind.JAVIS.value
        )
        if len(primary) != 1 or len(javis) != 1 or any(
            str(row["status"]) != ParticipantStatus.ACTIVE.value
            or str(row["subject_status"]) != SubjectStatus.ACTIVE.value
            or str(row["server_binding_source"]) != "packaged_desktop"
            for row in rows
        ) or str(primary[0]["identity_assurance"]) not in {
            IdentityAssurance.DESKTOP_CONFIRMED.value,
            IdentityAssurance.VERIFIED.value,
        } or str(javis[0]["identity_assurance"]) != IdentityAssurance.VERIFIED.value:
            raise MemoryStoreAuthorizationError("shared memory participant binding invalid")
        return context

    def _live_source_episodes(
        self,
        source_episode_ids: tuple[str, ...],
        context: AccessContext,
        issued_at_utc: str,
    ) -> tuple[sqlite3.Row, ...]:
        ids = tuple(source_episode_ids)
        placeholders = ",".join("?" for _ in ids)
        rows = self.__db.execute(
            "SELECT i.item_id, i.owner_subject_id, i.status, i.deletion_fenced, "
            "i.expires_at_utc, i.content_hash, i.source_digest, e.payload_json "
            "FROM memory_items AS i JOIN experience_episodes AS e ON e.episode_id = i.item_id "
            f"WHERE i.item_kind = ? AND i.item_id IN ({placeholders})",
            (MemoryItemKind.EXPERIENCE_EPISODE.value, *ids),
        ).fetchall()
        by_id = {str(row["item_id"]): row for row in rows}
        if set(by_id) != set(ids):
            raise MemoryStoreConflictError("shared memory source is unavailable")
        ordered = tuple(by_id[item_id] for item_id in ids)
        for row in ordered:
            episode = ExperienceEpisode.from_dict(json.loads(row["payload_json"]))
            if (
                str(row["owner_subject_id"]) != context.actor_subject_id
                or str(row["status"]) != MemoryItemStatus.ACTIVE.value
                or int(row["deletion_fenced"]) != 0
                or (
                    row["expires_at_utc"] is not None
                    and str(row["expires_at_utc"]) <= issued_at_utc
                )
                or tuple(sorted(episode.participant_subject_ids))
                != tuple(sorted(context.participant_subject_ids))
            ):
                raise MemoryStoreConflictError("shared memory source is not live")
        return ordered

    def _shared_memory(self, shared_memory_id: str) -> SharedMemory | None:
        row = self.__db.execute(
            "SELECT payload_json FROM memory_items WHERE item_id = ? AND item_kind = ?",
            (shared_memory_id, MemoryItemKind.SHARED_MEMORY.value),
        ).fetchone()
        return None if row is None else SharedMemory.from_dict(json.loads(row["payload_json"]))

    @staticmethod
    def _require_shared_transition(
        current: SharedMemory,
        context: AccessContext,
        proposal_revision: int,
    ) -> None:
        if current.owner_subject_id != context.actor_subject_id:
            raise MemoryStoreAuthorizationError("shared memory owner mismatch")
        if context.actor_subject_id not in current.participant_subject_ids:
            raise MemoryStoreAuthorizationError("shared memory actor is not a participant")
        if tuple(sorted(context.participant_subject_ids)) != tuple(
            sorted(current.participant_subject_ids)
        ):
            raise MemoryStoreAuthorizationError("shared memory participant set changed")
        if proposal_revision != current.proposal_revision:
            raise MemoryStoreConflictError("shared proposal revision is stale")
        if current.status is not SharedMemoryStatus.PROPOSED:
            raise MemoryStoreConflictError("shared proposal is not pending")

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

    def prepare_deletion(self, command: ForgetMemory) -> dict[str, Any]:
        if not isinstance(command, ForgetMemory):
            raise TypeError("command must be ForgetMemory")
        context = self._require_delete_actor(command)
        existing = self.__db.execute(
            "SELECT actor_subject_id, actor_subject_hash, scope, target_selector_json, "
            "source_handling, state "
            "FROM deletion_requests WHERE deletion_request_id = ?",
            (command.deletion_request_id,),
        ).fetchone()
        selector_json = _json(command.target_selector.to_dict())
        if existing is not None:
            actor_hash = hashlib.sha256(context.actor_subject_id.encode("utf-8")).hexdigest()
            selector_matches = str(existing["target_selector_json"]) == selector_json
            if str(existing["state"]) == DeletionState.VERIFIED.value:
                audit = self.__db.execute(
                    "SELECT selector_hash FROM deletion_audits WHERE deletion_request_id = ?",
                    (command.deletion_request_id,),
                ).fetchone()
                selector_matches = (
                    audit is not None
                    and str(audit["selector_hash"])
                    == hashlib.sha256(selector_json.encode("utf-8")).hexdigest()
                )
            if (
                str(existing["actor_subject_hash"]) != actor_hash
                or str(existing["scope"]) != command.scope.value
                or not selector_matches
                or str(existing["source_handling"]) != command.source_handling.value
            ):
                raise MemoryStoreConflictError("deletion idempotency conflict")
            return self._deletion_status(command.deletion_request_id)
        request = DeletionRequest(
            schema_version=1,
            deletion_request_id=command.deletion_request_id,
            revision=1,
            actor_subject_id=context.actor_subject_id,
            scope=command.scope,
            target_selector=command.target_selector,
            source_handling=command.source_handling,
            state=DeletionState.ACCEPTED,
            progress_cursor=None,
            attempt=0,
            last_reason_code=command.reason_code,
            created_at_utc=command.issued_at_utc,
            updated_at_utc=command.issued_at_utc,
            completed_at_utc=None,
        )
        self.put_deletion_request(request)
        return self.fence_deletion(command.deletion_request_id)

    def fence_deletion(self, deletion_request_id: str) -> dict[str, Any]:
        row = self._deletion_row(deletion_request_id)
        state = DeletionState(str(row["state"]))
        if state is not DeletionState.ACCEPTED:
            return self._deletion_status(deletion_request_id)
        request = DeletionRequest.from_dict(json.loads(row["payload_json"]))
        initial = self._select_deletion_items(request)
        if not initial:
            raise MemoryStoreConflictError("deletion target is unavailable")
        primary = set(initial)
        if request.source_handling is SourceHandling.SOURCE_AND_DERIVED:
            primary.update(self._reverse_derivation_closure(primary))
        closure = self._forward_derivation_closure(primary)
        all_targets = primary | closure
        if len(all_targets) > 10000:
            raise MemoryStoreConflictError("deletion closure is too large")
        actor = request.actor_subject_id
        placeholders = ",".join("?" for _ in all_targets)
        owners = self.__db.execute(
            f"SELECT item_kind, item_id, owner_subject_id FROM memory_items "
            f"WHERE item_id IN ({placeholders})",
            tuple(item_id for _, item_id in sorted(all_targets)),
        ).fetchall()
        if len(owners) != len(all_targets) or any(
            str(item["owner_subject_id"]) != actor for item in owners
        ):
            raise MemoryStoreAuthorizationError("deletion closure owner mismatch")
        source_store_row = self.__db.execute(
            "SELECT source_store_id FROM memory_meta WHERE singleton = 1"
        ).fetchone()
        source_store_id = (
            None
            if source_store_row is None or source_store_row["source_store_id"] is None
            else str(source_store_row["source_store_id"])
        )
        now = _utc_now()
        for kind, item_id in sorted(all_targets):
            target_state = "fenced_primary" if (kind, item_id) in primary else "fenced_derived"
            session_id = request_id = None
            if kind == MemoryItemKind.EXPERIENCE_EPISODE.value:
                episode = self.__db.execute(
                    "SELECT session_id, request_id FROM experience_episodes WHERE episode_id = ?",
                    (item_id,),
                ).fetchone()
                if episode is not None:
                    session_id = str(episode["session_id"])
                    request_id = str(episode["request_id"])
            target_id = "deletion-target-" + hashlib.sha256(
                f"{deletion_request_id}\0{kind}\0{item_id}".encode("utf-8")
            ).hexdigest()
            self.__db.execute(
                "INSERT OR IGNORE INTO deletion_targets (deletion_target_id, "
                "deletion_request_id, item_kind, item_id, source_store_id, session_id, "
                "request_id, target_state, created_at_utc, updated_at_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    target_id,
                    deletion_request_id,
                    kind,
                    item_id,
                    source_store_id if session_id is not None else None,
                    session_id,
                    request_id,
                    target_state,
                    now,
                    now,
                ),
            )
            self.__db.execute("DELETE FROM memory_fts WHERE item_id = ?", (item_id,))
            self.__db.execute(
                "UPDATE memory_items SET deletion_fenced = 1, status = ? WHERE item_id = ?",
                (MemoryItemStatus.DELETION_FENCED.value, item_id),
            )
            if source_store_id and session_id and request_id:
                suppression_id = "suppression-" + hashlib.sha256(
                    f"{source_store_id}\0{session_id}\0{request_id}".encode("utf-8")
                ).hexdigest()
                self.__db.execute(
                    "INSERT INTO projection_suppressions (suppression_id, source_store_id, "
                    "session_id, request_id, deletion_request_id, reason_code, created_at_utc) "
                    "VALUES (?, ?, ?, ?, ?, 'deletion_suppressed', ?) "
                    "ON CONFLICT(source_store_id, session_id, request_id) DO UPDATE SET "
                    "deletion_request_id=excluded.deletion_request_id, "
                    "reason_code=excluded.reason_code",
                    (
                        suppression_id,
                        source_store_id,
                        session_id,
                        request_id,
                        deletion_request_id,
                        now,
                    ),
                )
                self.__db.execute(
                    "UPDATE terminal_projection_receipts SET projection_state = 'excluded', "
                    "reason_code = 'deletion_suppressed', episode_id = NULL, "
                    "next_retry_at_utc = NULL, updated_at_utc = ? "
                    "WHERE source_store_id = ? AND session_id = ? AND request_id = ?",
                    (now, source_store_id, session_id, request_id),
                )
        self._bump_meta(acl=True, index=True)
        return self._transition_deletion(
            deletion_request_id,
            DeletionState.ACCEPTED,
            DeletionState.FENCED,
        )

    def advance_deletion_state(
        self,
        deletion_request_id: str,
        expected: DeletionState,
        target: DeletionState,
    ) -> dict[str, Any]:
        allowed = {
            DeletionState.FENCED: DeletionState.SOURCE_PENDING,
            DeletionState.SOURCE_PENDING: DeletionState.SOURCE_RETAINED,
            DeletionState.FTS_DELETED: DeletionState.CACHES_INVALIDATED,
            DeletionState.CACHES_INVALIDATED: DeletionState.PROMPT_INVALIDATED,
        }
        if allowed.get(expected) is not target:
            raise MemoryStoreConflictError("invalid deletion state transition")
        return self._transition_deletion(deletion_request_id, expected, target)

    def delete_deletion_primary_rows(self, deletion_request_id: str) -> dict[str, Any]:
        self._require_deletion_state(deletion_request_id, DeletionState.SOURCE_RETAINED)
        self._delete_target_group(deletion_request_id, "fenced_primary")
        return self._transition_deletion(
            deletion_request_id,
            DeletionState.SOURCE_RETAINED,
            DeletionState.PRIMARY_ROWS_DELETED,
        )

    def delete_deletion_derivations(self, deletion_request_id: str) -> dict[str, Any]:
        self._require_deletion_state(deletion_request_id, DeletionState.PRIMARY_ROWS_DELETED)
        self._delete_target_group(deletion_request_id, "fenced_derived")
        return self._transition_deletion(
            deletion_request_id,
            DeletionState.PRIMARY_ROWS_DELETED,
            DeletionState.DERIVATIONS_DELETED,
        )

    def delete_deletion_fts(self, deletion_request_id: str) -> dict[str, Any]:
        self._require_deletion_state(deletion_request_id, DeletionState.DERIVATIONS_DELETED)
        self.__db.execute(
            "DELETE FROM memory_fts WHERE item_id NOT IN (SELECT item_id FROM memory_items)"
        )
        return self._transition_deletion(
            deletion_request_id,
            DeletionState.DERIVATIONS_DELETED,
            DeletionState.FTS_DELETED,
        )

    def verify_deletion(self, deletion_request_id: str) -> dict[str, Any]:
        row = self._require_deletion_state(
            deletion_request_id, DeletionState.PROMPT_INVALIDATED
        )
        remaining = int(
            self.__db.execute(
                "SELECT COUNT(*) FROM deletion_targets WHERE deletion_request_id = ? "
                "AND item_id IS NOT NULL",
                (deletion_request_id,),
            ).fetchone()[0]
        )
        if remaining:
            raise MemoryStoreConflictError("deletion targets remain live")
        self._rebuild_live_fts(f"deletion:{deletion_request_id}")
        orphaned = int(
            self.__db.execute(
                "SELECT COUNT(*) FROM memory_fts AS f LEFT JOIN memory_items AS i "
                "ON i.item_id = f.item_id WHERE i.item_id IS NULL "
                "OR i.status != 'active' OR i.deletion_fenced != 0"
            ).fetchone()[0]
        )
        if orphaned:
            raise MemoryStoreConflictError("deletion verification found stale index rows")
        target_count = int(
            self.__db.execute(
                "SELECT COUNT(*) FROM deletion_targets WHERE deletion_request_id = ?",
                (deletion_request_id,),
            ).fetchone()[0]
        )
        source_count = int(
            self.__db.execute(
                "SELECT COUNT(*) FROM deletion_targets WHERE deletion_request_id = ? "
                "AND session_id IS NOT NULL AND request_id IS NOT NULL",
                (deletion_request_id,),
            ).fetchone()[0]
        )
        completed = _utc_now()
        self.__db.execute(
            "INSERT OR REPLACE INTO deletion_audits (deletion_request_id, actor_subject_hash, "
            "scope, source_handling, selector_hash, target_count, source_count, reason_code, "
            "completed_at_utc) VALUES (?, ?, ?, ?, ?, ?, ?, 'verified_absent', ?)",
            (
                deletion_request_id,
                str(row["actor_subject_hash"]),
                str(row["scope"]),
                str(row["source_handling"]),
                hashlib.sha256(str(row["target_selector_json"]).encode("utf-8")).hexdigest(),
                target_count,
                source_count,
                completed,
            ),
        )
        self.__db.execute(
            "UPDATE deletion_requests SET revision = revision + 1, actor_subject_id = NULL, "
            "target_selector_json = '{}', state = ?, progress_cursor = NULL, "
            "last_reason_code = 'verified_absent', updated_at_utc = ?, completed_at_utc = ?, "
            "payload_json = ? WHERE deletion_request_id = ?",
            (
                DeletionState.VERIFIED.value,
                completed,
                completed,
                _json(
                    {
                        "schema_version": 1,
                        "deletion_request_id": deletion_request_id,
                        "state": DeletionState.VERIFIED.value,
                        "target_count": target_count,
                        "source_count": source_count,
                        "reason_code": "verified_absent",
                    }
                ),
                deletion_request_id,
            ),
        )
        self.__db.execute(
            "DELETE FROM deletion_targets WHERE deletion_request_id = ?",
            (deletion_request_id,),
        )
        return self._deletion_status(deletion_request_id)

    def record_deletion_failure(
        self, deletion_request_id: str, reason_code: str
    ) -> dict[str, Any]:
        _require_text(reason_code, "reason_code")
        self._deletion_row(deletion_request_id)
        self.__db.execute(
            "UPDATE deletion_requests SET revision = revision + 1, attempt = attempt + 1, "
            "last_reason_code = ?, updated_at_utc = ? WHERE deletion_request_id = ?",
            (reason_code[:96], _utc_now(), deletion_request_id),
        )
        return self._deletion_status(deletion_request_id)

    def _require_delete_actor(self, command: ForgetMemory) -> AccessContext:
        context = command.access_context
        if (
            context.purpose is not AccessPurpose.DELETE
            or "memory.delete" not in context.capability_scopes
            or context.actor_kind not in {ActorKind.PRIMARY_USER, ActorKind.KNOWN_PERSON}
            or context.identity_assurance
            not in {IdentityAssurance.DESKTOP_CONFIRMED, IdentityAssurance.VERIFIED}
            or context.actor_subject_id not in context.participant_subject_ids
            or not (context.issued_at_utc <= command.issued_at_utc < context.expires_at_utc)
        ):
            raise MemoryStoreAuthorizationError("memory deletion access denied")
        if (
            command.scope is DeletionScope.SUBJECT_OWNED
            and command.target_selector.subject_id != context.actor_subject_id
        ):
            raise MemoryStoreAuthorizationError("subject-owned deletion actor mismatch")
        row = self.__db.execute(
            "SELECT s.status AS subject_status, p.status AS participant_status, "
            "p.identity_assurance, p.server_binding_source "
            "FROM subjects AS s JOIN session_participants AS p ON p.subject_id = s.subject_id "
            "WHERE s.subject_id = ? AND p.session_id = ? AND p.participant_role = 'primary'",
            (context.actor_subject_id, context.session_id),
        ).fetchone()
        if (
            row is None
            or str(row["subject_status"]) != SubjectStatus.ACTIVE.value
            or str(row["participant_status"]) != ParticipantStatus.ACTIVE.value
            or str(row["server_binding_source"]) != "packaged_desktop"
            or str(row["identity_assurance"])
            not in {
                IdentityAssurance.DESKTOP_CONFIRMED.value,
                IdentityAssurance.VERIFIED.value,
            }
        ):
            raise MemoryStoreAuthorizationError("memory deletion identity binding invalid")
        return context

    def _select_deletion_items(
        self, request: DeletionRequest
    ) -> set[tuple[str, str]]:
        selector = request.target_selector
        actor = request.actor_subject_id
        if request.scope is DeletionScope.ITEM:
            rows = self.__db.execute(
                "SELECT item_kind, item_id FROM memory_items WHERE item_kind = ? "
                "AND item_id = ? AND owner_subject_id = ?",
                (selector.item_kind.value, selector.item_id, actor),
            ).fetchall()
        elif request.scope is DeletionScope.EPISODE:
            rows = self.__db.execute(
                "SELECT item_kind, item_id FROM memory_items WHERE item_kind = ? "
                "AND item_id = ? AND owner_subject_id = ?",
                (MemoryItemKind.EXPERIENCE_EPISODE.value, selector.item_id, actor),
            ).fetchall()
        elif request.scope is DeletionScope.SHARED_MEMORY:
            rows = self.__db.execute(
                "SELECT item_kind, item_id FROM memory_items WHERE item_kind = ? "
                "AND item_id = ? AND owner_subject_id = ?",
                (MemoryItemKind.SHARED_MEMORY.value, selector.item_id, actor),
            ).fetchall()
        elif request.scope is DeletionScope.SUBJECT_OWNED:
            rows = self.__db.execute(
                "SELECT item_kind, item_id FROM memory_items WHERE owner_subject_id = ?",
                (actor,),
            ).fetchall()
        elif request.scope is DeletionScope.SESSION_DERIVED:
            rows = self.__db.execute(
                "SELECT i.item_kind, i.item_id FROM memory_items AS i "
                "JOIN experience_episodes AS e ON e.episode_id = i.item_id "
                "WHERE i.owner_subject_id = ? AND e.session_id = ?",
                (actor, selector.session_id),
            ).fetchall()
        elif request.scope is DeletionScope.TIME_RANGE:
            rows = self.__db.execute(
                "SELECT item_kind, item_id FROM memory_items WHERE owner_subject_id = ? "
                "AND occurred_at_utc >= ? AND occurred_at_utc <= ?",
                (actor, selector.range_started_at_utc, selector.range_ended_at_utc),
            ).fetchall()
        else:
            rows = self.__db.execute(
                "SELECT item_kind, item_id FROM memory_items WHERE owner_subject_id = ?",
                (actor,),
            ).fetchall()
        return {(str(row["item_kind"]), str(row["item_id"])) for row in rows}

    def _reverse_derivation_closure(
        self, targets: set[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        frontier = set(targets)
        while frontier:
            next_frontier: set[tuple[str, str]] = set()
            for kind, item_id in frontier:
                rows = self.__db.execute(
                    "SELECT source_kind, source_id FROM derivation_edges "
                    "WHERE target_kind = ? AND target_id = ? AND active = 1",
                    (kind, item_id),
                ).fetchall()
                for row in rows:
                    value = (str(row["source_kind"]), str(row["source_id"]))
                    if value not in targets and value not in found:
                        found.add(value)
                        next_frontier.add(value)
            frontier = next_frontier
            if len(found) > 10000:
                raise MemoryStoreConflictError("deletion source closure is too large")
        return found

    def _forward_derivation_closure(
        self, sources: set[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        frontier = set(sources)
        while frontier:
            next_frontier: set[tuple[str, str]] = set()
            for kind, item_id in frontier:
                rows = self.__db.execute(
                    "SELECT target_kind, target_id FROM derivation_edges "
                    "WHERE source_kind = ? AND source_id = ? AND active = 1",
                    (kind, item_id),
                ).fetchall()
                for row in rows:
                    value = (str(row["target_kind"]), str(row["target_id"]))
                    if value not in sources and value not in found:
                        found.add(value)
                        next_frontier.add(value)
            frontier = next_frontier
            if len(found) > 10000:
                raise MemoryStoreConflictError("deletion derivation closure is too large")
        return found

    def _delete_target_group(self, deletion_request_id: str, target_state: str) -> None:
        rows = self.__db.execute(
            "SELECT item_id FROM deletion_targets WHERE deletion_request_id = ? "
            "AND target_state = ? AND item_id IS NOT NULL",
            (deletion_request_id, target_state),
        ).fetchall()
        for row in rows:
            item_id = str(row["item_id"])
            self.__db.execute("DELETE FROM memory_fts WHERE item_id = ?", (item_id,))
            self.__db.execute("DELETE FROM memory_items WHERE item_id = ?", (item_id,))
        self._bump_meta(acl=True, index=True)

    def _rebuild_live_fts(self, reason_code: str) -> None:
        meta = self.__db.execute(
            "SELECT index_generation FROM memory_meta WHERE singleton = 1"
        ).fetchone()
        from_generation = int(meta["index_generation"])
        target_generation = from_generation + 1
        rebuild_id = "fts-rebuild-" + hashlib.sha256(
            f"{target_generation}\0{reason_code}".encode("utf-8")
        ).hexdigest()
        now = _utc_now()
        rows = self.__db.execute(
            "SELECT item_kind, payload_json FROM memory_items "
            "WHERE status = 'active' AND deletion_fenced = 0 ORDER BY item_id"
        ).fetchall()
        self.__db.execute(
            "INSERT INTO memory_fts_rebuilds (rebuild_id, from_generation, "
            "target_generation, state, reason_code, expected_item_count, indexed_item_count, "
            "started_at_utc, updated_at_utc, completed_at_utc) "
            "VALUES (?, ?, ?, 'building', ?, ?, 0, ?, ?, NULL)",
            (rebuild_id, from_generation, target_generation, reason_code, len(rows), now, now),
        )
        self.__db.execute("DELETE FROM memory_fts")
        indexed = 0
        for row in rows:
            item = self._store._decode_item(str(row["item_kind"]), str(row["payload_json"]))
            item_id = self._kind_and_id(item)[1]
            self.__db.execute(
                "INSERT INTO memory_fts (item_id, item_kind, searchable_text) VALUES (?, ?, ?)",
                (item_id, str(row["item_kind"]), self._searchable_text(item)),
            )
            indexed += 1
        completed = _utc_now()
        self.__db.execute(
            "UPDATE memory_fts_rebuilds SET state = 'completed', indexed_item_count = ?, "
            "updated_at_utc = ?, completed_at_utc = ? WHERE rebuild_id = ?",
            (indexed, completed, completed, rebuild_id),
        )
        self.__db.execute(
            "UPDATE memory_meta SET index_generation = ?, acl_epoch = acl_epoch + 1, "
            "updated_at_utc = ? WHERE singleton = 1",
            (target_generation, completed),
        )

    def _transition_deletion(
        self,
        deletion_request_id: str,
        expected: DeletionState,
        target: DeletionState,
    ) -> dict[str, Any]:
        row = self._require_deletion_state(deletion_request_id, expected)
        now = _utc_now()
        cursor = f"stage:{target.value}"
        self.__db.execute(
            "UPDATE deletion_requests SET revision = revision + 1, state = ?, "
            "progress_cursor = ?, last_reason_code = NULL, updated_at_utc = ? "
            "WHERE deletion_request_id = ?",
            (target.value, cursor, now, deletion_request_id),
        )
        return self._deletion_status(deletion_request_id)

    def _require_deletion_state(
        self, deletion_request_id: str, expected: DeletionState
    ) -> sqlite3.Row:
        row = self._deletion_row(deletion_request_id)
        if str(row["state"]) != expected.value:
            raise MemoryStoreConflictError("deletion state is stale")
        return row

    def _deletion_row(self, deletion_request_id: str) -> sqlite3.Row:
        _require_text(deletion_request_id, "deletion_request_id")
        row = self.__db.execute(
            "SELECT * FROM deletion_requests WHERE deletion_request_id = ?",
            (deletion_request_id,),
        ).fetchone()
        if row is None:
            raise MemoryStoreConflictError("deletion request not found")
        return row

    def _deletion_status(self, deletion_request_id: str) -> dict[str, Any]:
        row = self._deletion_row(deletion_request_id)
        audit = self.__db.execute(
            "SELECT target_count, source_count, reason_code FROM deletion_audits "
            "WHERE deletion_request_id = ?",
            (deletion_request_id,),
        ).fetchone()
        target_count = int(
            self.__db.execute(
                "SELECT COUNT(*) FROM deletion_targets WHERE deletion_request_id = ?",
                (deletion_request_id,),
            ).fetchone()[0]
        )
        source_count = int(
            self.__db.execute(
                "SELECT COUNT(*) FROM deletion_targets WHERE deletion_request_id = ? "
                "AND session_id IS NOT NULL AND request_id IS NOT NULL",
                (deletion_request_id,),
            ).fetchone()[0]
        )
        result = {
            "deletion_request_id": str(row["deletion_request_id"]),
            "scope": str(row["scope"]),
            "source_handling": str(row["source_handling"]),
            "state": str(row["state"]),
            "attempt": int(row["attempt"]),
            "last_reason_code": row["last_reason_code"],
            "created_at_utc": str(row["created_at_utc"]),
            "updated_at_utc": str(row["updated_at_utc"]),
            "completed_at_utc": row["completed_at_utc"],
        }
        if audit is not None:
            target_count = int(audit["target_count"])
            source_count = int(audit["source_count"])
            result["completion_reason_code"] = str(audit["reason_code"])
        result["target_count"] = target_count
        result["source_count"] = source_count
        return result

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
                    payload_json, confirmation_set_revision,
                    required_confirmer_subject_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(shared_memory_id) DO UPDATE SET
                    schema_version=excluded.schema_version, revision=excluded.revision,
                    proposal_revision=excluded.proposal_revision,
                    source_episode_ids_json=excluded.source_episode_ids_json,
                    proposed_text=excluded.proposed_text,
                    participant_subject_ids_json=excluded.participant_subject_ids_json,
                    status=excluded.status, confirmed_at_utc=excluded.confirmed_at_utc,
                    revoked_at_utc=excluded.revoked_at_utc,
                    audience_subject_ids_json=excluded.audience_subject_ids_json,
                    payload_json=excluded.payload_json,
                    confirmation_set_revision=excluded.confirmation_set_revision,
                    required_confirmer_subject_ids_json=excluded.required_confirmer_subject_ids_json
                """,
                (
                    item.shared_memory_id, item.schema_version, item.revision,
                    item.proposal_revision, _json(item.source_episode_ids), item.proposed_text,
                    _json(item.participant_subject_ids), item.status.value,
                    item.confirmed_at_utc, item.revoked_at_utc,
                    _json(item.audience_subject_ids), payload,
                    item.confirmation_set_revision,
                    _json(item.required_confirmer_subject_ids),
                ),
            )
            confirmation_state = {
                SharedMemoryStatus.PROPOSED: "open",
                SharedMemoryStatus.CONFIRMED: "confirmed",
                SharedMemoryStatus.REVOKED: "revoked",
                SharedMemoryStatus.REJECTED: "superseded",
                SharedMemoryStatus.DELETION_FENCED: "revoked",
            }[item.status]
            self.__db.execute(
                """
                INSERT INTO shared_confirmation_sets (
                    shared_memory_id, confirmation_set_revision, proposal_revision,
                    required_subject_ids_json, state, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(shared_memory_id, confirmation_set_revision) DO UPDATE SET
                    proposal_revision=excluded.proposal_revision,
                    required_subject_ids_json=excluded.required_subject_ids_json,
                    state=excluded.state,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    item.shared_memory_id,
                    item.confirmation_set_revision,
                    item.proposal_revision,
                    _json(item.required_confirmer_subject_ids),
                    confirmation_state,
                    item.created_at_utc,
                    item.updated_at_utc,
                ),
            )
        elif isinstance(item, UserModelClaim):
            self.__db.execute(
                """
                INSERT INTO user_model_claims (
                    claim_id, schema_version, revision, subject_id, predicate, value_type,
                    value_json, epistemic_class, sensitivity, source_evidence_ids_json,
                    contradiction_claim_ids_json, supersedes_claim_id, acl_subject_ids_json,
                    confirmed_at_utc, payload_json, claim_status, confirmation_event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    schema_version=excluded.schema_version, revision=excluded.revision,
                    subject_id=excluded.subject_id, predicate=excluded.predicate,
                    value_type=excluded.value_type, value_json=excluded.value_json,
                    epistemic_class=excluded.epistemic_class, sensitivity=excluded.sensitivity,
                    source_evidence_ids_json=excluded.source_evidence_ids_json,
                    contradiction_claim_ids_json=excluded.contradiction_claim_ids_json,
                    supersedes_claim_id=excluded.supersedes_claim_id,
                    acl_subject_ids_json=excluded.acl_subject_ids_json,
                    confirmed_at_utc=excluded.confirmed_at_utc, payload_json=excluded.payload_json,
                    claim_status=excluded.claim_status,
                    confirmation_event_id=excluded.confirmation_event_id
                """,
                (
                    item.claim_id, item.schema_version, item.revision, item.subject_id,
                    item.predicate, item.value_type.value, _json(item.value),
                    item.epistemic_class.value, item.sensitivity.value,
                    _json(item.source_evidence_ids), _json(item.contradiction_claim_ids),
                    item.supersedes_claim_id, _json(item.acl_subject_ids),
                    item.confirmed_at_utc, payload, item.status.value,
                    item.confirmation_event_id,
                ),
            )
        elif isinstance(item, RelationshipEvent):
            self.__db.execute(
                """
                INSERT INTO relationship_events (
                    relationship_event_id, schema_version, revision, subject_ids_json,
                    event_kind, summary, source_evidence_ids_json, occurred_at_utc, payload_json,
                    direction, confidence, view_eligible
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relationship_event_id) DO UPDATE SET
                    schema_version=excluded.schema_version, revision=excluded.revision,
                    subject_ids_json=excluded.subject_ids_json, event_kind=excluded.event_kind,
                    summary=excluded.summary,
                    source_evidence_ids_json=excluded.source_evidence_ids_json,
                    occurred_at_utc=excluded.occurred_at_utc, payload_json=excluded.payload_json,
                    direction=excluded.direction, confidence=excluded.confidence,
                    view_eligible=excluded.view_eligible
                """,
                (
                    item.relationship_event_id, item.schema_version, item.revision,
                    _json(item.subject_ids), item.event_kind.value, item.summary,
                    _json(item.source_evidence_ids), item.occurred_at_utc, payload,
                    item.direction.value, item.confidence,
                    int(item.status is RelationshipEventStatus.ACTIVE),
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
