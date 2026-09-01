from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.agent import Agent
from core.conversation_store import ConversationStore
from core.life.memory.contracts import (
    AccessContext,
    CorrectMemory,
    ForgetMemory,
    RecallQuery,
    SessionParticipant,
    Subject,
)
from core.life.memory.extraction import JournalBuilder
from core.life.memory.service import MemoryService
from core.life.memory.store import (
    DATABASE_RELATIVE_PATH,
    MemoryStoreAuthorizationError,
    MemoryStoreConflictError,
)
from core.prompt_builder import PromptBuilder


NOW = "2026-08-20T10:00:00.000Z"
COMMAND_AT = "2099-01-01T10:30:00.000Z"
EXPIRES = "2099-01-01T11:00:00.000Z"
HASH_A = "a" * 64
NEEDLE = "deletion-six-path-needle"


def _subject(subject_id: str, kind: str) -> Subject:
    return Subject.from_dict(
        {
            "schema_version": 1,
            "subject_id": subject_id,
            "revision": 1,
            "subject_kind": kind,
            "display_name": "Javis" if kind == "javis" else "Primary user",
            "status": "active",
            "identity_assurance": "verified" if kind == "javis" else "desktop_confirmed",
            "credential_reference_hash": None,
            "merged_into_subject_id": None,
            "session_scope_id": None,
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
        }
    )


def _participant(subject_id: str, role: str) -> SessionParticipant:
    return SessionParticipant.from_dict(
        {
            "schema_version": 1,
            "participant_id": f"participant-{subject_id}",
            "revision": 1,
            "session_id": "session-1",
            "subject_id": subject_id,
            "participant_role": role,
            "identity_assurance": "verified" if role == "javis" else "desktop_confirmed",
            "joined_at_utc": NOW,
            "left_at_utc": None,
            "server_binding_source": "packaged_desktop",
            "status": "active",
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
        }
    )


def _access(purpose: str, acl_epoch: int = 0, *, scopes: tuple[str, ...] | None = None):
    return AccessContext.from_dict(
        {
            "schema_version": 1,
            "context_id": f"context-{purpose}-{acl_epoch}",
            "runtime_boot_id": "boot-1",
            "client_id_hash": HASH_A,
            "capability_scopes": list(scopes or (f"memory.{purpose}",)),
            "actor_subject_id": "subject-user",
            "actor_kind": "primary_user",
            "session_id": "session-1",
            "participant_subject_ids": ["subject-user", "subject-javis"],
            "audience_ceiling": "owner_private",
            "identity_assurance": "desktop_confirmed",
            "purpose": purpose,
            "acl_epoch": acl_epoch,
            "issued_at_utc": NOW,
            "expires_at_utc": EXPIRES,
        }
    )


def _conversation(path: Path) -> ConversationStore:
    store = ConversationStore(path / "conversations.sqlite3")
    projection = {
        "access_projection": {
            "schema_version": 1,
            "context_id": "accepted-context",
            "actor_subject_id": "subject-user",
            "actor_kind": "primary_user",
            "session_id": "session-1",
            "participant_subject_ids": ["subject-user", "subject-javis"],
            "audience_ceiling": "owner_private",
            "identity_assurance": "desktop_confirmed",
            "acl_epoch": 0,
        },
        "execution_lane": "exclusive",
    }
    store.accept_request(
        "session-1",
        "request-1",
        "request-key-1",
        f"Remember this governed fact: {NEEDLE}.",
        projection,
    )
    store.append_message(
        "session-1", "request-1", "assistant", "The governed request completed."
    )
    store.append_event("session-1", "request-1", "request.completed", {})
    return store


def _seed(service: MemoryService, conversations: ConversationStore):
    service.put_subject(_subject("subject-user", "primary_user")).result(timeout=20)
    service.put_subject(_subject("subject-javis", "javis")).result(timeout=20)
    service.put_session_participant(_participant("subject-user", "primary")).result(timeout=20)
    service.put_session_participant(_participant("subject-javis", "javis")).result(timeout=20)
    assert service.reconcile_once().result(timeout=20)["advanced"] == 1
    receipt = service.get_terminal_receipt(
        conversations.source_store_id(), "session-1", "request-1"
    ).result(timeout=20)
    assert receipt["projection_state"] == "projected"
    episode = service.get_item(
        "experience_episode",
        receipt["episode_id"],
        _access("recall", service.status()["store"]["acl_epoch"], scopes=("memory.read",)),
    ).result(timeout=20)
    assert episode is not None
    return episode


def _recall(service: MemoryService, text: str = NEEDLE):
    epoch = service.status()["store"]["acl_epoch"]
    query = RecallQuery.from_dict(
        {
            "schema_version": 1,
            "query_id": f"query-{epoch}",
            "access_context": _access(
                "recall", epoch, scopes=("memory.read",)
            ).to_dict(),
            "query_text": text,
            "item_kinds": [],
            "limit": 8,
            "max_item_chars": 512,
            "max_total_bytes": 4096,
            "occurred_after_utc": None,
            "occurred_before_utc": None,
            "issued_at_utc": COMMAND_AT,
        }
    )
    return service.recall(query).result(timeout=20)


def _forget(
    episode_id: str,
    *,
    deletion_id: str = "deletion-1",
    source_handling: str = "derived_only",
    idempotency_key: str = "forget-idempotency-1",
) -> ForgetMemory:
    return ForgetMemory.from_dict(
        {
            "schema_version": 1,
            "command_id": f"command-{deletion_id}",
            "access_context": _access("delete", scopes=("memory.delete",)).to_dict(),
            "deletion_request_id": deletion_id,
            "scope": "episode",
            "target_selector": {
                "item_kind": None,
                "item_id": episode_id,
                "subject_id": None,
                "session_id": None,
                "range_started_at_utc": None,
                "range_ended_at_utc": None,
            },
            "source_handling": source_handling,
            "reason_code": "user_requested",
            "idempotency_key": idempotency_key,
            "issued_at_utc": COMMAND_AT,
        }
    )


def test_derived_only_deletion_covers_db_fts_cache_prompt_restart_reindex_and_replay(
    tmp_path: Path,
):
    conversations = _conversation(tmp_path / "conversation")
    data_root = tmp_path / "data"
    service = MemoryService(
        data_root, conversation_store=conversations, reconcile_interval_seconds=60
    ).start()
    episode = _seed(service, conversations)
    journal = JournalBuilder().build((episode,))
    assert journal is not None
    service.put_journal_projection(journal).result(timeout=20)
    bundle = _recall(service)
    assert bundle.items

    builder = PromptBuilder(brain=None)
    assembled = builder.build(recall_bundle=bundle)
    assert NEEDLE in assembled
    agent = Agent.__new__(Agent)
    agent._cached_prompt = assembled
    agent._cached_prompt_step = 1
    agent.prompt_builder = builder
    service.register_prompt_invalidator(agent.invalidate_memory_context)

    command = _forget(episode.episode_id)
    result = service.forget_memory(command).result(timeout=20)
    replay = service.forget_memory(command).result(timeout=20)
    assert replay == result
    assert result["state"] == "verified"
    assert result["target_count"] == 2
    assert agent._cached_prompt == ""
    assert _recall(service).items == ()
    assert conversations.read_request_evidence("session-1", "request-1")["redacted"] is False
    assert service.shutdown(timeout=20)

    db_path = data_root / DATABASE_RELATIVE_PATH
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM projection_suppressions").fetchone()[0] == 1
        assert db.execute(
            "SELECT projection_state, reason_code FROM terminal_projection_receipts"
        ).fetchone() == ("excluded", "deletion_suppressed")
        assert db.execute(
            "SELECT target_count, source_count, reason_code FROM deletion_audits"
        ).fetchone() == (2, 1, "verified_absent")
        assert db.execute("SELECT COUNT(*) FROM deletion_targets").fetchone()[0] == 0
        assert db.execute(
            "SELECT state FROM memory_fts_rebuilds ORDER BY target_generation DESC LIMIT 1"
        ).fetchone() == ("completed",)
        db.execute("DELETE FROM terminal_projection_receipts")
        db.execute("UPDATE memory_meta SET terminal_cursor = 0")
        db.commit()

    reopened = MemoryService(
        data_root, conversation_store=conversations, reconcile_interval_seconds=60
    ).start()
    try:
        assert _recall(reopened).items == ()
        assert reopened.reconcile_once().result(timeout=20)["advanced"] == 1
        assert _recall(reopened).items == ()
    finally:
        assert reopened.shutdown(timeout=20)


def test_source_and_derived_redacts_content_but_preserves_ordering_tombstones(tmp_path: Path):
    conversations = _conversation(tmp_path / "conversation")
    data_root = tmp_path / "data"
    service = MemoryService(
        data_root, conversation_store=conversations, reconcile_interval_seconds=60
    ).start()
    try:
        episode = _seed(service, conversations)
        result = service.forget_memory(
            _forget(
                episode.episode_id,
                deletion_id="deletion-source-1",
                source_handling="source_and_derived",
            )
        ).result(timeout=20)
        assert result["state"] == "verified"
        evidence = conversations.read_request_evidence("session-1", "request-1")
        assert evidence["redacted"] is True
        assert all(message["content"] == "" for message in evidence["messages"])
        assert conversations.scan_terminal_events()["events"]
        with sqlite3.connect(conversations.path) as db:
            assert db.execute("SELECT COUNT(*) FROM request_keys").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM conversation_redactions").fetchone()[0] == 1
    finally:
        assert service.shutdown(timeout=20)


@pytest.mark.parametrize(
    "crash_state",
    [
        "fenced",
        "source_pending",
        "source_retained",
        "primary_rows_deleted",
        "derivations_deleted",
        "fts_deleted",
        "caches_invalidated",
        "prompt_invalidated",
    ],
)
def test_restart_resumes_each_persisted_deletion_state(tmp_path: Path, crash_state: str):
    conversations = _conversation(tmp_path / "conversation")
    data_root = tmp_path / "data"

    def crash_after(state: str, _deletion_id: str) -> None:
        if state == crash_state:
            raise RuntimeError(f"crash_after_{state}")

    service = MemoryService(
        data_root,
        conversation_store=conversations,
        reconcile_interval_seconds=60,
        deletion_stage_hook=crash_after,
    ).start()
    episode = _seed(service, conversations)
    with pytest.raises(RuntimeError, match=f"crash_after_{crash_state}"):
        service.forget_memory(_forget(episode.episode_id)).result(timeout=20)
    status = service.deletion_status("deletion-1").result(timeout=20)
    assert status["state"] == crash_state
    assert _recall(service).items == ()
    assert service.shutdown(timeout=20)

    resumed = MemoryService(
        data_root, conversation_store=conversations, reconcile_interval_seconds=60
    ).start()
    try:
        status = resumed.deletion_status("deletion-1").result(timeout=20)
        assert status["state"] == "verified"
        assert _recall(resumed).items == ()
    finally:
        assert resumed.shutdown(timeout=20)


def test_delete_authority_and_completed_idempotency_fail_closed(tmp_path: Path):
    conversations = _conversation(tmp_path / "conversation")
    service = MemoryService(
        tmp_path / "data", conversation_store=conversations, reconcile_interval_seconds=60
    ).start()
    try:
        episode = _seed(service, conversations)
        denied = _forget(episode.episode_id)
        denied_wire = denied.to_dict()
        denied_wire["access_context"]["capability_scopes"] = ["memory.read"]
        denied = ForgetMemory.from_dict(denied_wire)
        with pytest.raises(MemoryStoreAuthorizationError):
            service.forget_memory(denied).result(timeout=20)

        command = _forget(episode.episode_id)
        completed = service.forget_memory(command).result(timeout=20)
        assert service.forget_memory(command).result(timeout=20) == completed
        rebound = _forget("different-episode", idempotency_key="different-key")
        with pytest.raises(MemoryStoreConflictError, match="idempotency"):
            service.forget_memory(rebound).result(timeout=20)

        with sqlite3.connect(service.data_root / DATABASE_RELATIVE_PATH) as db:
            request = db.execute(
                "SELECT actor_subject_id, target_selector_json, payload_json "
                "FROM deletion_requests WHERE deletion_request_id = 'deletion-1'"
            ).fetchone()
        assert request[0] is None
        assert request[1] == "{}"
        assert episode.episode_id not in request[2]
    finally:
        assert service.shutdown(timeout=20)


def test_correction_supersedes_old_text_and_is_idempotent_and_source_linked(tmp_path: Path):
    conversations = _conversation(tmp_path / "conversation")
    service = MemoryService(
        tmp_path / "data", conversation_store=conversations, reconcile_interval_seconds=60
    ).start()
    try:
        episode = _seed(service, conversations)
        assert _recall(service).items
        agent = Agent.__new__(Agent)
        agent._cached_prompt = "prompt containing stale recalled memory"
        agent._cached_prompt_step = 1
        agent.prompt_builder = PromptBuilder(brain=None)
        service.register_prompt_invalidator(agent.invalidate_memory_context)
        command = CorrectMemory.from_dict(
            {
                "schema_version": 1,
                "command_id": "command-correction-1",
                "access_context": _access("manage", scopes=("memory.manage",)).to_dict(),
                "target_kind": "experience_episode",
                "target_id": episode.episode_id,
                "expected_revision": episode.revision,
                "corrected_text": "The corrected governed date is Monday.",
                "source_evidence_ids": [episode.source_terminal_event_id],
                "idempotency_key": "correction-idempotency-1",
                "issued_at_utc": COMMAND_AT,
            }
        )
        corrected = service.correct_memory(command).result(timeout=20)
        replay = service.correct_memory(command).result(timeout=20)
        assert replay == corrected
        assert corrected.episode_id != episode.episode_id
        assert corrected.what_happened == "The corrected governed date is Monday."
        assert corrected.extractor_version == "deterministic.correction.v1"
        assert agent._cached_prompt == ""
        assert _recall(service).items == ()
        assert [item.item_id for item in _recall(service, "corrected governed date").items] == [
            corrected.episode_id
        ]

        stale_wire = command.to_dict()
        stale_wire["idempotency_key"] = "correction-idempotency-stale"
        stale_wire["expected_revision"] = episode.revision + 2
        with pytest.raises(MemoryStoreConflictError, match="stale"):
            service.correct_memory(CorrectMemory.from_dict(stale_wire)).result(timeout=20)

        with sqlite3.connect(service.data_root / DATABASE_RELATIVE_PATH) as db:
            statuses = db.execute(
                "SELECT item_id, status FROM memory_items ORDER BY item_id"
            ).fetchall()
            edge = db.execute(
                "SELECT source_id, target_id, relation FROM derivation_edges "
                "WHERE relation = 'corrects'"
            ).fetchone()
            receipt = db.execute(
                "SELECT target_id, corrected_id FROM correction_receipts"
            ).fetchone()
        assert sorted(status for _, status in statuses) == ["active", "superseded"]
        assert edge == (episode.episode_id, corrected.episode_id, "corrects")
        assert receipt == (episode.episode_id, corrected.episode_id)
        assert "correction-idempotency-1" not in (
            service.data_root / DATABASE_RELATIVE_PATH
        ).read_bytes().decode("utf-8", errors="ignore")
    finally:
        assert service.shutdown(timeout=20)
