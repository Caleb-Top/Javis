from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.life.memory.contracts import (
    AccessContext,
    ConfirmSharedMemory,
    ExperienceEpisode,
    ProposeSharedMemory,
    RecallQuery,
    RejectSharedMemory,
    RevokeSharedMemory,
    SessionParticipant,
    SharedMemoryStatus,
    Subject,
)
from core.life.memory.service import MemoryService
from core.life.memory.store import MemoryStoreAuthorizationError, MemoryStoreConflictError


NOW = "2026-08-20T10:00:00.000Z"
ENDED = "2026-08-20T10:05:00.000Z"
PROPOSED = "2026-08-20T10:06:00.000Z"
DECIDED = "2026-08-20T10:07:00.000Z"
RECALLED = "2026-08-20T10:08:00.000Z"
EXPIRES = "2026-08-20T11:00:00.000Z"
HASH_A = "a" * 64


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


def _participant(
    subject_id: str,
    role: str,
    *,
    server_binding_source: str = "packaged_desktop",
) -> SessionParticipant:
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
            "server_binding_source": server_binding_source,
            "status": "active",
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
        }
    )


def _episode(text: str = "shared-confirmation-needle") -> ExperienceEpisode:
    return ExperienceEpisode.from_dict(
        {
            "schema_version": 1,
            "episode_id": "episode-1",
            "revision": 1,
            "owner_subject_id": "subject-user",
            "audience": "owner_private",
            "privacy_class": "user_private",
            "session_id": "session-1",
            "request_id": "request-1",
            "participant_subject_ids": ["subject-user", "subject-javis"],
            "started_at_utc": NOW,
            "ended_at_utc": ENDED,
            "outcome": "completed",
            "what_happened": text,
            "javis_attention": "The user explicitly asked to preserve this experience.",
            "intent_summary": "Propose a governed shared memory.",
            "action_summary": "No authority was granted.",
            "verified_result_summary": "The conversation terminal was complete.",
            "meaning_for_user": "",
            "meaning_for_javis": "",
            "source_terminal_event_id": "terminal-1",
            "source_terminal_sequence": 3,
            "source_sequence_domain": "conversation-store:session-1",
            "source_message_ids": ["message-user", "message-javis"],
            "source_event_ids": ["accepted-1", "terminal-1"],
            "source_digest": HASH_A,
            "extractor_version": "deterministic.v1",
            "confidence": 0.95,
            "status": "active",
            "retention_class": "memory_candidate",
            "expires_at_utc": None,
            "created_at_utc": NOW,
            "updated_at_utc": ENDED,
        }
    )


def _context(
    *,
    purpose: str = "manage",
    actor: str = "subject-user",
    actor_kind: str = "primary_user",
    scopes: tuple[str, ...] = ("memory.manage",),
    participants: tuple[str, ...] = ("subject-user", "subject-javis"),
    acl_epoch: int = 0,
) -> AccessContext:
    return AccessContext.from_dict(
        {
            "schema_version": 1,
            "context_id": f"context-{purpose}-{actor}-{acl_epoch}",
            "runtime_boot_id": "boot-1",
            "client_id_hash": HASH_A,
            "capability_scopes": list(scopes),
            "actor_subject_id": actor,
            "actor_kind": actor_kind,
            "session_id": "session-1",
            "participant_subject_ids": list(participants),
            "audience_ceiling": "guest" if actor_kind == "guest" else "explicit_shared",
            "identity_assurance": "guest" if actor_kind == "guest" else "desktop_confirmed",
            "purpose": purpose,
            "acl_epoch": acl_epoch,
            "issued_at_utc": NOW,
            "expires_at_utc": EXPIRES,
        }
    )


def _propose(context: AccessContext | None = None, *, text: str = "A decision we both confirmed"):
    return ProposeSharedMemory.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-propose-1",
            "access_context": (context or _context()).to_dict(),
            "source_episode_ids": ["episode-1"],
            "proposed_text": text,
            "idempotency_key": "idempotency-propose-1",
            "issued_at_utc": PROPOSED,
        }
    )


def _confirm(shared_id: str, context: AccessContext | None = None, *, revision: int = 1):
    return ConfirmSharedMemory.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-confirm-1",
            "access_context": (context or _context()).to_dict(),
            "shared_memory_id": shared_id,
            "proposal_revision": revision,
            "idempotency_key": "idempotency-confirm-1",
            "issued_at_utc": DECIDED,
        }
    )


def _recall(service: MemoryService, text: str):
    status = service.status()["store"]
    context = _context(
        purpose="recall",
        scopes=("memory.read",),
        acl_epoch=status["acl_epoch"],
    )
    query = RecallQuery.from_dict(
        {
            "schema_version": 1,
            "query_id": f"query-{text}-{status['index_generation']}",
            "access_context": context.to_dict(),
            "query_text": text,
            "item_kinds": ["shared_memory"],
            "limit": 8,
            "max_item_chars": 512,
            "max_total_bytes": 4096,
            "occurred_after_utc": None,
            "occurred_before_utc": None,
            "issued_at_utc": RECALLED,
        }
    )
    return service.recall(query).result(timeout=10)


@pytest.fixture
def service(tmp_path: Path):
    value = MemoryService(tmp_path).start()
    _seed_service(value)
    try:
        yield value
    finally:
        assert value.shutdown(timeout=10)


def _seed_service(value: MemoryService) -> None:
    value.put_subject(_subject("subject-user", "primary_user")).result(timeout=10)
    value.put_subject(_subject("subject-javis", "javis")).result(timeout=10)
    value.put_session_participant(_participant("subject-user", "primary")).result(timeout=10)
    value.put_session_participant(_participant("subject-javis", "javis")).result(timeout=10)
    value.put_item(_episode()).result(timeout=10)


def test_proposal_is_idempotent_but_not_searchable_or_prompt_visible(service: MemoryService):
    proposal = service.propose_shared_memory(_propose()).result(timeout=10)
    replay = service.propose_shared_memory(_propose()).result(timeout=10)

    assert replay == proposal
    assert proposal.status is SharedMemoryStatus.PROPOSED
    assert proposal.confirmation_receipts == ()
    assert _recall(service, "decision").items == ()


def test_primary_explicit_confirmation_atomically_enables_acl_fts_and_recall(service: MemoryService):
    proposal = service.propose_shared_memory(_propose()).result(timeout=10)
    before = service.status()["store"]
    confirmed = service.confirm_shared_memory(_confirm(proposal.shared_memory_id)).result(timeout=10)
    replay = service.confirm_shared_memory(_confirm(proposal.shared_memory_id)).result(timeout=10)
    after = service.status()["store"]

    assert replay == confirmed
    assert confirmed.status is SharedMemoryStatus.CONFIRMED
    assert confirmed.confirmation_receipts[0].subject_id == "subject-user"
    assert after["acl_epoch"] > before["acl_epoch"]
    assert after["index_generation"] > before["index_generation"]
    bundle = _recall(service, "decision")
    assert [item.item_id for item in bundle.items] == [proposal.shared_memory_id]
    assert bundle.items[0].prompt_text == "A decision we both confirmed"


def test_confirmed_shared_memory_recall_survives_restart(tmp_path: Path):
    first = MemoryService(tmp_path).start()
    _seed_service(first)
    proposal = first.propose_shared_memory(_propose()).result(timeout=10)
    first.confirm_shared_memory(_confirm(proposal.shared_memory_id)).result(timeout=10)
    assert first.shutdown(timeout=10)

    reopened = MemoryService(tmp_path).start()
    try:
        bundle = _recall(reopened, "decision")
        assert [item.item_id for item in bundle.items] == [proposal.shared_memory_id]
    finally:
        assert reopened.shutdown(timeout=10)


@pytest.mark.parametrize(
    "context",
    [
        _context(actor="subject-guest", actor_kind="guest", participants=("subject-guest",)),
        _context(scopes=("conversation",)),
        _context(participants=("subject-user", "subject-unknown")),
    ],
)
def test_guest_missing_scope_and_nonparticipant_cannot_propose(service: MemoryService, context):
    with pytest.raises(MemoryStoreAuthorizationError):
        service.propose_shared_memory(_propose(context)).result(timeout=10)


def test_non_desktop_participant_binding_cannot_authorize_shared_memory(tmp_path: Path):
    value = MemoryService(tmp_path).start()
    try:
        value.put_subject(_subject("subject-user", "primary_user")).result(timeout=10)
        value.put_subject(_subject("subject-javis", "javis")).result(timeout=10)
        value.put_session_participant(
            _participant("subject-user", "primary", server_binding_source="web_claim")
        ).result(timeout=10)
        value.put_session_participant(_participant("subject-javis", "javis")).result(timeout=10)
        value.put_item(_episode()).result(timeout=10)

        with pytest.raises(MemoryStoreAuthorizationError, match="binding invalid"):
            value.propose_shared_memory(_propose()).result(timeout=10)
    finally:
        assert value.shutdown(timeout=10)


def test_stale_revision_and_deleted_source_cannot_confirm(service: MemoryService):
    proposal = service.propose_shared_memory(_propose()).result(timeout=10)
    with pytest.raises(MemoryStoreConflictError, match="stale"):
        service.confirm_shared_memory(_confirm(proposal.shared_memory_id, revision=2)).result(
            timeout=10
        )

    service.delete_item("experience_episode", "episode-1").result(timeout=10)
    with pytest.raises(MemoryStoreConflictError, match="source"):
        service.confirm_shared_memory(_confirm(proposal.shared_memory_id)).result(timeout=10)


def test_reject_removes_proposed_text_and_never_creates_search_rows(service: MemoryService):
    secret = "reject-only-private-text"
    proposal = service.propose_shared_memory(_propose(text=secret)).result(timeout=10)
    command = RejectSharedMemory.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-reject-1",
            "access_context": _context().to_dict(),
            "shared_memory_id": proposal.shared_memory_id,
            "proposal_revision": proposal.proposal_revision,
            "idempotency_key": "idempotency-reject-1",
            "issued_at_utc": DECIDED,
        }
    )
    rejected = service.reject_shared_memory(command).result(timeout=10)
    replay = service.reject_shared_memory(command).result(timeout=10)

    assert replay == rejected
    assert rejected.status is SharedMemoryStatus.REJECTED
    assert rejected.proposed_text == "[rejected]"
    assert _recall(service, secret).items == ()
    with sqlite3.connect(service.data_root / "memory" / "autobiographical.sqlite3") as db:
        fts_rows = db.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE item_id = ?", (proposal.shared_memory_id,)
        ).fetchone()[0]
        body = db.execute(
            "SELECT proposed_text FROM shared_memories WHERE shared_memory_id = ?",
            (proposal.shared_memory_id,),
        ).fetchone()[0]
        decision = db.execute(
            "SELECT decision, expected_revision FROM shared_decisions WHERE shared_memory_id = ?",
            (proposal.shared_memory_id,),
        ).fetchone()
    assert fts_rows == 0
    assert body == "[rejected]"
    assert decision == ("reject", proposal.proposal_revision)
    assert "idempotency-reject-1" not in (
        service.data_root / "memory" / "autobiographical.sqlite3"
    ).read_bytes().decode("utf-8", errors="ignore")


def test_revoke_invalidates_existing_recall_and_removes_fts(service: MemoryService):
    proposal = service.propose_shared_memory(_propose()).result(timeout=10)
    confirmed = service.confirm_shared_memory(_confirm(proposal.shared_memory_id)).result(timeout=10)
    assert _recall(service, "decision").items
    command = RevokeSharedMemory.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-revoke-1",
            "access_context": _context().to_dict(),
            "shared_memory_id": confirmed.shared_memory_id,
            "expected_revision": confirmed.revision,
            "idempotency_key": "idempotency-revoke-1",
            "issued_at_utc": RECALLED,
        }
    )
    revoked = service.revoke_shared_memory(command).result(timeout=10)
    replay = service.revoke_shared_memory(command).result(timeout=10)

    assert replay == revoked
    assert revoked.status is SharedMemoryStatus.REVOKED
    assert _recall(service, "decision").items == ()
    with sqlite3.connect(service.data_root / "memory" / "autobiographical.sqlite3") as db:
        assert db.execute(
            "SELECT COUNT(*) FROM memory_fts WHERE item_id = ?", (proposal.shared_memory_id,)
        ).fetchone()[0] == 0


def test_assistant_text_or_generic_approval_cannot_replace_typed_confirmation(
    service: MemoryService,
):
    proposal = service.propose_shared_memory(_propose()).result(timeout=10)
    assert proposal.status is SharedMemoryStatus.PROPOSED
    assert not hasattr(service, "approve_shared_memory")
    assert not hasattr(service, "confirm_from_assistant_text")
    assert _recall(service, "decision").items == ()
