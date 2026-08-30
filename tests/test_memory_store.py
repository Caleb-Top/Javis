from __future__ import annotations

import copy
import sqlite3
import threading
from pathlib import Path

import pytest

from core.life.memory.contracts import (
    AccessContext,
    DerivationEdge,
    ExperienceEpisode,
    JournalEntry,
    RecallQuery,
    SessionParticipant,
    Subject,
)
from core.life.memory.store import (
    DATABASE_RELATIVE_PATH,
    MemoryStore,
    MemoryStoreAuthorizationError,
    MemoryStoreConflictError,
    MemoryStoreReadOnlyError,
    SCHEMA_VERSION,
)


NOW = "2026-08-20T10:00:00.000Z"
LATER = "2026-08-20T10:05:00.000Z"
LATEST = "2026-08-20T10:10:00.000Z"
HASH_A = "a" * 64


def subject_wire(subject_id: str, *, kind: str = "primary_user", revision: int = 1) -> dict:
    return {
        "schema_version": 1,
        "subject_id": subject_id,
        "revision": revision,
        "subject_kind": kind,
        "display_name": "Primary user" if kind == "primary_user" else "Javis",
        "status": "active",
        "identity_assurance": "desktop_confirmed" if kind == "primary_user" else "verified",
        "credential_reference_hash": None,
        "merged_into_subject_id": None,
        "session_scope_id": None,
        "created_at_utc": NOW,
        "updated_at_utc": NOW if revision == 1 else LATER,
    }


def episode_wire(*, revision: int = 1, text: str = "alpha-memory-needle") -> dict:
    return {
        "schema_version": 1,
        "episode_id": "episode-1",
        "revision": revision,
        "owner_subject_id": "subject-user",
        "audience": "owner_private",
        "privacy_class": "user_private",
        "session_id": "session-1",
        "request_id": "request-1",
        "participant_subject_ids": ["subject-user", "subject-javis"],
        "started_at_utc": NOW,
        "ended_at_utc": LATER,
        "outcome": "completed",
        "what_happened": text,
        "javis_attention": "Explicit remember intent was present.",
        "intent_summary": "Preserve a verified decision.",
        "action_summary": "Javis recorded the governed episode.",
        "verified_result_summary": "Terminal evidence was complete.",
        "meaning_for_user": "",
        "meaning_for_javis": "",
        "source_terminal_event_id": "terminal-event-1",
        "source_terminal_sequence": 7,
        "source_sequence_domain": "conversation.session-1",
        "source_message_ids": ["message-user-1", "message-assistant-1"],
        "source_event_ids": ["event-accepted-1", "terminal-event-1"],
        "source_digest": HASH_A,
        "extractor_version": "deterministic.v1",
        "confidence": 0.95,
        "status": "active",
        "retention_class": "memory_candidate",
        "expires_at_utc": None,
        "created_at_utc": NOW,
        "updated_at_utc": LATER if revision == 1 else LATEST,
    }


def journal_wire() -> dict:
    return {
        "schema_version": 1,
        "entry_id": "journal-1",
        "revision": 1,
        "owner_subject_id": "subject-user",
        "audience": "owner_private",
        "privacy_class": "user_private",
        "range_started_at_utc": NOW,
        "range_ended_at_utc": LATER,
        "title": "Continuity note",
        "body": "An interpretation derived from the governed episode.",
        "source_episode_ids": ["episode-1"],
        "entry_kind": "continuity_note",
        "source_digest": HASH_A,
        "status": "active",
        "retention_class": "continuity",
        "expires_at_utc": None,
        "created_at_utc": NOW,
        "updated_at_utc": LATER,
    }


def access_context(acl_epoch: int) -> AccessContext:
    return AccessContext.from_dict(
        {
            "schema_version": 1,
            "context_id": f"context-{acl_epoch}",
            "runtime_boot_id": "boot-1",
            "client_id_hash": HASH_A,
            "capability_scopes": ["memory.recall"],
            "actor_subject_id": "subject-user",
            "actor_kind": "primary_user",
            "session_id": "session-1",
            "participant_subject_ids": ["subject-user", "subject-javis"],
            "audience_ceiling": "explicit_shared",
            "identity_assurance": "desktop_confirmed",
            "purpose": "recall",
            "acl_epoch": acl_epoch,
            "issued_at_utc": NOW,
            "expires_at_utc": LATEST,
        }
    )


def recall_query(text: str, acl_epoch: int) -> RecallQuery:
    return RecallQuery.from_dict(
        {
            "schema_version": 1,
            "query_id": f"query-{text}",
            "access_context": access_context(acl_epoch).to_dict(),
            "query_text": text,
            "item_kinds": ["experience_episode"],
            "limit": 8,
            "max_item_chars": 512,
            "max_total_bytes": 4096,
            "occurred_after_utc": None,
            "occurred_before_utc": None,
            "issued_at_utc": NOW,
        }
    )


@pytest.fixture
def store_and_token(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    try:
        yield store, token
    finally:
        store.close()


def seed_subjects(store: MemoryStore, token: object) -> None:
    assert store.put_subject(Subject.from_dict(subject_wire("subject-user")), writer_token=token)
    assert store.put_subject(
        Subject.from_dict(subject_wire("subject-javis", kind="javis")), writer_token=token
    )


def raw_rows(path: Path, sql: str) -> list[tuple]:
    db = sqlite3.connect(path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def test_path_is_fixed_below_data_root_and_isolated_from_other_databases(tmp_path: Path):
    conversation_path = tmp_path / "conversations" / "conversations.sqlite3"
    conversation_path.parent.mkdir(parents=True)
    conversation_path.write_bytes(b"conversation-sentinel")
    token = object()

    store = MemoryStore(tmp_path, writer_token=token)
    try:
        assert store.path == (tmp_path / DATABASE_RELATIVE_PATH).resolve()
        assert store.path.is_file()
        assert conversation_path.read_bytes() == b"conversation-sentinel"
    finally:
        store.close()


def test_empty_database_and_reopen_are_idempotent_with_complete_schema(tmp_path: Path):
    token = object()
    first = MemoryStore(tmp_path, writer_token=token)
    first_meta = first.metadata()
    first.close()

    reopened = MemoryStore(tmp_path, writer_token=token)
    try:
        assert reopened.status()["state"] == "ready"
        assert reopened.status()["schema_version"] == SCHEMA_VERSION
        assert reopened.metadata() == first_meta
        names = {
            row[0]
            for row in raw_rows(
                reopened.path,
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')",
            )
        }
        assert {
            "memory_meta",
            "memory_items",
            "experience_episodes",
            "journal_entries",
            "shared_memories",
            "user_model_claims",
            "relationship_events",
            "subjects",
            "session_participants",
            "derivation_edges",
            "deletion_requests",
            "deletion_targets",
            "terminal_projection_receipts",
            "projection_suppressions",
            "memory_acl",
            "shared_confirmations",
            "shared_decisions",
            "memory_fts",
            "memory_fts_rebuilds",
        } <= names
        assert raw_rows(reopened.path, "SELECT version FROM schema_migrations ORDER BY version") == [
            (1,),
            (2,),
            (3,),
        ]
    finally:
        reopened.close()


def test_v1_database_upgrades_to_latest_and_reopen_does_not_repeat_migration(tmp_path: Path):
    token = object()
    initial = MemoryStore(tmp_path, writer_token=token)
    path = initial.path
    initial.close()

    db = sqlite3.connect(path)
    try:
        db.execute("DROP TABLE memory_fts")
        db.execute("DROP TABLE memory_fts_rebuilds")
        db.execute("DROP TABLE shared_decisions")
        db.execute("DELETE FROM schema_migrations WHERE version IN (2, 3)")
        db.execute("UPDATE memory_meta SET schema_version = 1")
        db.execute("PRAGMA user_version = 1")
        db.commit()
    finally:
        db.close()

    upgraded = MemoryStore(tmp_path, writer_token=token)
    try:
        assert upgraded.status()["state"] == "ready"
        assert upgraded.fts_available is True
        assert upgraded.metadata()["schema_version"] == SCHEMA_VERSION
    finally:
        upgraded.close()
    reopened = MemoryStore(tmp_path, writer_token=token)
    try:
        assert raw_rows(path, "SELECT COUNT(*) FROM schema_migrations WHERE version = 2") == [(1,)]
        assert raw_rows(path, "SELECT COUNT(*) FROM schema_migrations WHERE version = 3") == [(1,)]
    finally:
        reopened.close()


def test_v2_database_adds_content_free_shared_decision_receipts(tmp_path: Path):
    token = object()
    initial = MemoryStore(tmp_path, writer_token=token)
    path = initial.path
    initial.close()

    db = sqlite3.connect(path)
    try:
        db.execute("DROP TABLE shared_decisions")
        db.execute("DELETE FROM schema_migrations WHERE version = 3")
        db.execute("UPDATE memory_meta SET schema_version = 2")
        db.execute("PRAGMA user_version = 2")
        db.commit()
    finally:
        db.close()

    upgraded = MemoryStore(tmp_path, writer_token=token)
    try:
        assert upgraded.status()["state"] == "ready"
        assert upgraded.metadata()["schema_version"] == SCHEMA_VERSION
        columns = {
            row[1]
            for row in raw_rows(path, "PRAGMA table_info(shared_decisions)")
        }
        assert columns == {
            "decision_id",
            "shared_memory_id",
            "actor_subject_id",
            "decision",
            "expected_revision",
            "decided_at_utc",
        }
    finally:
        upgraded.close()


def test_non_fts_search_table_forces_degraded_without_legacy_fallback(tmp_path: Path):
    token = object()
    initial = MemoryStore(tmp_path, writer_token=token)
    path = initial.path
    initial.close()

    db = sqlite3.connect(path)
    try:
        db.execute("DROP TABLE memory_fts")
        db.execute("DROP TABLE memory_fts_rebuilds")
        db.execute("DELETE FROM schema_migrations WHERE version = 2")
        db.execute("UPDATE memory_meta SET schema_version = 1")
        db.execute(
            "CREATE TABLE memory_fts (item_id TEXT, item_kind TEXT, searchable_text TEXT)"
        )
        db.execute(
            "INSERT INTO memory_fts VALUES "
            "('legacy-secret', 'experience_episode', 'legacy-secret-needle')"
        )
        db.execute("PRAGMA user_version = 1")
        db.commit()
    finally:
        db.close()

    degraded = MemoryStore(tmp_path, writer_token=token)
    try:
        assert degraded.status()["state"] == "degraded"
        assert degraded.status()["read_only"] is True
        assert degraded.fts_available is False
        assert degraded.search_items(recall_query("legacy-secret-needle", 0)) == ()
        assert raw_rows(path, "SELECT searchable_text FROM memory_fts") == [
            ("legacy-secret-needle",)
        ]
    finally:
        degraded.close()


def test_wal_foreign_keys_and_query_only_read_connections(store_and_token):
    store, token = store_and_token
    assert store.connection_settings() == {
        "journal_mode": "wal",
        "foreign_keys": 1,
        "query_only": 1,
    }
    with store._read_connection() as read_db:
        assert read_db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            read_db.execute("UPDATE memory_meta SET acl_epoch = 99")

    missing_owner = ExperienceEpisode.from_dict(episode_wire())
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        store.put_item(missing_owner, writer_token=token)
    assert store.metadata()["index_generation"] == 0


def test_duplicate_terminal_identity_is_idempotent_and_conflicts_are_rejected(store_and_token):
    store, token = store_and_token
    values = {
        "receipt_id": "receipt-1",
        "source_store_id": "conversation-store-1",
        "terminal_row_id": 11,
        "session_id": "session-1",
        "request_id": "request-1",
        "source_terminal_event_id": "terminal-1",
        "outcome": "completed",
        "projection_state": "not_selected",
        "reason_code": "not_meaningful",
    }
    first = store.record_terminal_receipt(writer_token=token, **values)
    replay = store.record_terminal_receipt(writer_token=token, **values)
    assert replay["receipt_id"] == first["receipt_id"]
    assert raw_rows(store.path, "SELECT COUNT(*) FROM terminal_projection_receipts") == [(1,)]

    conflicting_request = dict(values, receipt_id="receipt-2", request_id="request-2")
    with pytest.raises(MemoryStoreConflictError, match="uniqueness"):
        store.record_terminal_receipt(writer_token=token, **conflicting_request)
    conflicting_terminal = dict(
        values, receipt_id="receipt-3", source_terminal_event_id="terminal-2"
    )
    with pytest.raises(MemoryStoreConflictError, match="uniqueness"):
        store.record_terminal_receipt(writer_token=token, **conflicting_terminal)


def test_foreign_key_closure_removes_body_acl_fts_and_edges_atomically(store_and_token):
    store, token = store_and_token
    seed_subjects(store, token)
    episode = ExperienceEpisode.from_dict(episode_wire())
    journal = JournalEntry.from_dict(journal_wire())
    assert store.put_item(episode, writer_token=token)
    assert store.put_item(journal, writer_token=token)
    edge = DerivationEdge.from_dict(
        {
            "schema_version": 1,
            "edge_id": "edge-1",
            "source_kind": "experience_episode",
            "source_id": "episode-1",
            "target_kind": "journal_entry",
            "target_id": "journal-1",
            "relation": "derived_from",
            "extractor": "journal",
            "extractor_version": "deterministic.v1",
            "source_digest": HASH_A,
            "created_at_utc": LATER,
            "active": True,
        }
    )
    assert store.put_derivation_edge(edge, writer_token=token)

    assert store.delete_item("experience_episode", "episode-1", writer_token=token)
    counts = raw_rows(
        store.path,
        """
        SELECT
          (SELECT COUNT(*) FROM memory_items WHERE item_id = 'episode-1'),
          (SELECT COUNT(*) FROM experience_episodes WHERE episode_id = 'episode-1'),
          (SELECT COUNT(*) FROM memory_acl WHERE item_id = 'episode-1'),
          (SELECT COUNT(*) FROM memory_fts WHERE item_id = 'episode-1'),
          (SELECT COUNT(*) FROM derivation_edges WHERE source_id = 'episode-1'),
          (SELECT COUNT(*) FROM memory_items WHERE item_id = 'journal-1')
        """,
    )
    assert counts == [(0, 0, 0, 0, 0, 1)]


def test_writer_token_and_bound_thread_are_both_required(store_and_token):
    store, token = store_and_token
    subject = Subject.from_dict(subject_wire("subject-user"))
    with pytest.raises(MemoryStoreAuthorizationError, match="token"):
        store.put_subject(subject, writer_token=object())

    errors: list[Exception] = []

    def write_from_other_thread() -> None:
        try:
            store.put_subject(subject, writer_token=token)
        except Exception as exc:  # noqa: BLE001 - the assertion inspects the boundary error
            errors.append(exc)

    worker = threading.Thread(target=write_from_other_thread)
    worker.start()
    worker.join()
    assert len(errors) == 1
    assert isinstance(errors[0], MemoryStoreAuthorizationError)
    assert "thread" in str(errors[0])
    assert store.get_subject("subject-user") is None


def test_active_primary_binding_has_a_unique_owner(store_and_token):
    store, token = store_and_token
    seed_subjects(store, token)
    base = {
        "schema_version": 1,
        "revision": 1,
        "session_id": "session-1",
        "participant_role": "primary",
        "identity_assurance": "desktop_confirmed",
        "joined_at_utc": NOW,
        "left_at_utc": None,
        "server_binding_source": "desktop",
        "status": "active",
        "created_at_utc": NOW,
        "updated_at_utc": NOW,
    }
    first = SessionParticipant.from_dict(
        dict(base, participant_id="participant-1", subject_id="subject-user")
    )
    second = SessionParticipant.from_dict(
        dict(base, participant_id="participant-2", subject_id="subject-javis")
    )
    assert store.put_session_participant(first, writer_token=token)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        store.put_session_participant(second, writer_token=token)
    assert store.active_session_participants("session-1") == (first,)


def test_item_mutation_keeps_envelope_acl_fts_and_generations_consistent(store_and_token):
    store, token = store_and_token
    seed_subjects(store, token)
    before = store.metadata()
    first = ExperienceEpisode.from_dict(episode_wire())
    assert store.put_item(first, writer_token=token)
    after_insert = store.metadata()
    assert after_insert["acl_epoch"] == before["acl_epoch"] + 1
    assert after_insert["index_generation"] == before["index_generation"] + 1
    assert store.search_items(recall_query("alpha-memory-needle", after_insert["acl_epoch"])) == (
        first,
    )

    revised_wire = copy.deepcopy(episode_wire(revision=2, text="beta-memory-needle"))
    revised = ExperienceEpisode.from_dict(revised_wire)
    assert store.put_item(revised, writer_token=token)
    after_update = store.metadata()
    assert after_update["acl_epoch"] == after_insert["acl_epoch"] + 1
    assert after_update["index_generation"] == after_insert["index_generation"] + 1
    assert store.search_items(recall_query("alpha-memory-needle", after_update["acl_epoch"])) == ()
    assert store.search_items(recall_query("beta-memory-needle", after_update["acl_epoch"])) == (
        revised,
    )
    assert store.search_items(recall_query("beta-memory-needle", after_insert["acl_epoch"])) == ()

    assert store.delete_item("experience_episode", "episode-1", writer_token=token)
    after_delete = store.metadata()
    assert after_delete["acl_epoch"] == after_update["acl_epoch"] + 1
    assert after_delete["index_generation"] == after_update["index_generation"] + 1
    assert store.search_items(recall_query("beta-memory-needle", after_delete["acl_epoch"])) == ()


def test_corrupt_migration_enters_read_only_recovery_without_destroying_data(tmp_path: Path):
    path = tmp_path / DATABASE_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    try:
        db.execute("CREATE TABLE memory_meta (broken_column TEXT)")
        db.execute("CREATE TABLE recovery_sentinel (value TEXT NOT NULL)")
        db.execute("INSERT INTO recovery_sentinel VALUES ('preserved')")
        db.execute("PRAGMA user_version = 0")
        db.commit()
    finally:
        db.close()

    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    try:
        status = store.status()
        assert status["state"] == "degraded"
        assert status["reason_code"] == "schema_migration_failed"
        assert status["read_only"] is True
        assert raw_rows(path, "SELECT value FROM recovery_sentinel") == [("preserved",)]
        with store._read_connection() as read_db:
            assert read_db.execute("SELECT value FROM recovery_sentinel").fetchone()[0] == "preserved"
            assert read_db.execute("PRAGMA query_only").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                read_db.execute("DELETE FROM recovery_sentinel")
        with pytest.raises(MemoryStoreReadOnlyError, match="read-only"):
            store.put_subject(
                Subject.from_dict(subject_wire("subject-user")), writer_token=token
            )
    finally:
        store.close()
