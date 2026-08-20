from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.conversation_store import ConversationStore
from core.life.memory.contracts import AccessContext, ExperienceEpisode, Subject
from core.life.memory.extraction import (
    DeterministicEpisodeSelector,
    EpisodeExtractionError,
    EpisodeExtractor,
    EpisodeSelectionReason,
    JournalBuilder,
)
from core.life.memory.projection import TerminalProjector
from core.life.memory.service import MemoryService
from core.life.memory.store import DATABASE_RELATIVE_PATH, MemoryStore


NOW = "2026-08-20T10:00:00.000Z"
LATER = "2026-08-20T10:10:00.000Z"
HASH_A = "a" * 64


def projection(session_id: str, *, lane: str = "exclusive"):
    return {
        "access_projection": {
            "schema_version": 1,
            "context_id": f"context-{session_id}",
            "actor_subject_id": "subject-user",
            "actor_kind": "primary_user",
            "session_id": session_id,
            "participant_subject_ids": ["subject-user", "subject-javis"],
            "audience_ceiling": "owner_private",
            "identity_assurance": "desktop_confirmed",
            "acl_epoch": 0,
        },
        "execution_lane": lane,
    }


def completed_evidence(
    root: Path,
    text: str,
    *,
    assistant: str = "I recorded the completed response.",
    verified_event: tuple[str, dict] | None = None,
    lane: str = "exclusive",
):
    store = ConversationStore(root / "conversations.sqlite3")
    session_id = "session-1"
    request_id = "request-1"
    store.accept_request(
        session_id,
        request_id,
        "key-1",
        text,
        projection(session_id, lane=lane),
    )
    if verified_event is not None:
        store.append_event(session_id, request_id, verified_event[0], verified_event[1])
    store.append_message(session_id, request_id, "assistant", assistant)
    terminal = store.append_event(session_id, request_id, "request.completed", {})
    evidence = store.read_request_evidence(session_id, request_id)
    scanned = store.scan_terminal_events()["events"][0]
    assert scanned["event_id"] == terminal["event_id"]
    decision = TerminalProjector().decide(
        source_store_id=store.source_store_id(),
        terminal=scanned,
        evidence=evidence,
    )
    assert decision.candidate is not None
    return store, evidence, decision.candidate


def owner_subject() -> Subject:
    return Subject.from_dict(
        {
            "schema_version": 1,
            "subject_id": "subject-user",
            "revision": 1,
            "subject_kind": "primary_user",
            "display_name": "Primary user",
            "status": "active",
            "identity_assurance": "desktop_confirmed",
            "credential_reference_hash": None,
            "merged_into_subject_id": None,
            "session_scope_id": None,
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
        }
    )


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
            "audience_ceiling": "owner_private",
            "identity_assurance": "desktop_confirmed",
            "purpose": "recall",
            "acl_epoch": acl_epoch,
            "issued_at_utc": NOW,
            "expires_at_utc": LATER,
        }
    )


def count(path: Path, table: str) -> int:
    db = sqlite3.connect(path)
    try:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        db.close()


@pytest.mark.parametrize(
    ("text", "lane", "reason"),
    [
        ("What time is it?", "exclusive", EpisodeSelectionReason.ORDINARY_TURN),
        ("Javis", "deterministic_local", EpisodeSelectionReason.EXACT_INVOCATION),
        ("I prefer tea.", "exclusive", EpisodeSelectionReason.ORDINARY_TURN),
        ("Do not remember this draft.", "exclusive", EpisodeSelectionReason.ORDINARY_TURN),
        ("Do you remember this draft?", "exclusive", EpisodeSelectionReason.ORDINARY_TURN),
    ],
)
def test_ordinary_exact_invocation_and_unrequested_claims_are_not_selected(
    tmp_path: Path, text: str, lane: str, reason: EpisodeSelectionReason
):
    _, evidence, candidate = completed_evidence(tmp_path, text, lane=lane)
    result = EpisodeExtractor().extract(candidate, evidence)
    assert result.episode is None
    assert result.selection.reason is reason


@pytest.mark.parametrize(
    "text",
    [
        "Remember that the launch boundary is Friday.",
        "请记住，发布边界是星期五。",
        "Don't forget this verified project decision.",
    ],
)
def test_explicit_remember_builds_source_bounded_active_episode(tmp_path: Path, text: str):
    _, evidence, candidate = completed_evidence(tmp_path, text)
    result = EpisodeExtractor().extract(candidate, evidence)
    episode = result.episode

    assert result.selection.reason is EpisodeSelectionReason.EXPLICIT_REMEMBER
    assert episode is not None
    assert episode.status.value == "active"
    assert episode.privacy_class.value == "user_private"
    assert episode.audience.value == "owner_private"
    assert text in episode.what_happened
    assert episode.source_terminal_event_id == candidate.source_terminal_event_id
    assert set(episode.source_message_ids) == {
        item["message_id"] for item in evidence["messages"]
    }
    assert set(episode.source_event_ids) == {
        item["event_id"] for item in evidence["events"]
    }


@pytest.mark.parametrize(
    ("event_type", "payload", "reason"),
    [
        ("decision.confirmed", {"confirmed": True}, EpisodeSelectionReason.CONFIRMED_DECISION),
        ("boundary.confirmed", {"verified": True}, EpisodeSelectionReason.CONFIRMED_BOUNDARY),
        ("goal.verified", {"verified": True}, EpisodeSelectionReason.VERIFIED_GOAL),
        ("milestone.verified", {"verified": True}, EpisodeSelectionReason.VERIFIED_MILESTONE),
    ],
)
def test_verified_lifecycle_events_select_without_keyword(
    tmp_path: Path, event_type: str, payload: dict, reason: EpisodeSelectionReason
):
    _, evidence, candidate = completed_evidence(
        tmp_path,
        "Proceed with the reviewed state.",
        verified_event=(event_type, payload),
    )
    result = EpisodeExtractor().extract(candidate, evidence)
    assert result.selection.reason is reason
    assert result.episode is not None


def test_tool_success_or_unverified_lifecycle_event_does_not_upgrade_to_episode(
    tmp_path: Path,
):
    _, evidence, candidate = completed_evidence(
        tmp_path,
        "Run the check.",
        verified_event=("activity.tool_completed", {"success": True, "output": "done"}),
    )
    assert EpisodeExtractor().extract(candidate, evidence).episode is None

    second_root = tmp_path / "second"
    _, unverified, second_candidate = completed_evidence(
        second_root,
        "Consider the decision.",
        verified_event=("decision.confirmed", {"confirmed": False}),
    )
    assert EpisodeExtractor().extract(second_candidate, unverified).episode is None


def valid_model_draft(evidence):
    messages = evidence["messages"]
    events = evidence["events"]
    return {
        "what_happened": messages[0]["content"],
        "javis_attention": messages[0]["content"],
        "intent_summary": messages[0]["content"],
        "action_summary": messages[1]["content"],
        "verified_result_summary": messages[1]["content"],
        "meaning_for_user": "",
        "meaning_for_javis": "",
        "source_message_ids": [item["message_id"] for item in messages],
        "source_event_ids": [item["event_id"] for item in events],
        "epistemic_label": "evidence_derived",
        "privacy_class": "user_private",
    }


class FakeAdapter:
    def __init__(self, transform):
        self.transform = transform

    def extract(self, candidate, evidence, selection):
        return self.transform(valid_model_draft(evidence))


@pytest.mark.parametrize(
    ("transform", "reason"),
    [
        (lambda draft: dict(draft, owner_subject_id="attacker"), "model_draft_schema_invalid"),
        (lambda draft: dict(draft, what_happened="The user moved to Mars."), "model_new_fact_rejected"),
        (lambda draft: dict(draft, privacy_class="public_surface"), "model_privacy_invalid"),
        (lambda draft: dict(draft, epistemic_label="confirmed_shared"), "model_epistemic_label_invalid"),
        (lambda draft: dict(draft, source_event_ids=["invented-event"]), "model_source_refs_invalid"),
    ],
)
def test_model_adapter_cannot_invent_fields_facts_privacy_or_sources(
    tmp_path: Path, transform, reason: str
):
    _, evidence, candidate = completed_evidence(tmp_path, "Remember this source statement.")
    extractor = EpisodeExtractor(adapter=FakeAdapter(transform))
    with pytest.raises(EpisodeExtractionError, match=reason):
        extractor.extract(candidate, evidence)


def test_valid_structured_adapter_remains_bounded_to_authoritative_text(tmp_path: Path):
    _, evidence, candidate = completed_evidence(tmp_path, "Remember this source statement.")
    result = EpisodeExtractor(adapter=FakeAdapter(lambda draft: draft)).extract(
        candidate, evidence
    )
    assert result.episode.what_happened == evidence["messages"][0]["content"]
    assert result.episode.source_digest == candidate.source_digest


def test_journal_builder_skips_empty_input_and_requires_active_source():
    builder = JournalBuilder()
    assert builder.build(()) is None

    inactive = ExperienceEpisode.from_dict(
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
            "ended_at_utc": LATER,
            "outcome": "completed",
            "what_happened": "Source statement.",
            "javis_attention": "Source statement.",
            "intent_summary": "Source statement.",
            "action_summary": "Source statement.",
            "verified_result_summary": "request.completed",
            "meaning_for_user": "",
            "meaning_for_javis": "",
            "source_terminal_event_id": "terminal-1",
            "source_terminal_sequence": 3,
            "source_sequence_domain": "conversation_store:session-1",
            "source_message_ids": ["message-1"],
            "source_event_ids": ["event-1", "terminal-1"],
            "source_digest": HASH_A,
            "extractor_version": "deterministic.v1",
            "confidence": 0.95,
            "status": "candidate",
            "retention_class": "memory_candidate",
            "expires_at_utc": None,
            "created_at_utc": LATER,
            "updated_at_utc": LATER,
        }
    )
    with pytest.raises(EpisodeExtractionError, match="journal_requires_active_episodes"):
        builder.build((inactive,))


def test_service_projects_episode_journal_and_citations_across_restart(tmp_path: Path):
    conversations, _, _ = completed_evidence(
        tmp_path,
        "Remember that deployment requires explicit approval.",
    )
    data_root = tmp_path / "data"
    service = MemoryService(
        data_root,
        conversation_store=conversations,
        reconcile_interval_seconds=60,
    ).start()
    try:
        assert service.put_subject(owner_subject()).result(timeout=20) is True
        assert service.reconcile_once().result(timeout=20) == {
            "scanned": 1,
            "advanced": 1,
            "pending": 0,
        }
        receipt = service.get_terminal_receipt(
            conversations.source_store_id(), "session-1", "request-1"
        ).result(timeout=20)
        assert receipt["projection_state"] == "projected"
        epoch = service.status()["store"]["acl_epoch"]
        episode = service.get_item(
            "experience_episode", receipt["episode_id"], access_context(epoch)
        ).result(timeout=20)
        assert episode is not None

        journal = JournalBuilder().build((episode,))
        assert journal is not None
        result = service.put_journal_projection(journal).result(timeout=20)
        assert result["journal_created"] is True
        source = conversations.read_request_evidence(episode.session_id, episode.request_id)
        assert set(episode.source_message_ids) <= {
            item["message_id"] for item in source["messages"]
        }
        assert episode.source_terminal_event_id == source["terminal_event"]["event_id"]
    finally:
        assert service.shutdown(timeout=20)

    path = data_root / DATABASE_RELATIVE_PATH
    assert count(path, "experience_episodes") == 1
    assert count(path, "journal_entries") == 1
    assert count(path, "derivation_edges") == 1
    assert count(path, "user_model_claims") == 0
    assert count(path, "relationship_events") == 0

    token = object()
    reopened = MemoryStore(data_root, writer_token=token)
    try:
        epoch = reopened.metadata()["acl_epoch"]
        persisted = reopened.get_item(
            "experience_episode", receipt["episode_id"], access_context(epoch)
        )
        assert persisted is not None
        assert persisted.source_terminal_event_id == episode.source_terminal_event_id
    finally:
        reopened.close()


def test_episode_write_failure_leaves_pending_receipt_and_cursor_for_retry(tmp_path: Path):
    conversations, _, _ = completed_evidence(tmp_path, "Remember this retryable source.")
    data_root = tmp_path / "data"
    service = MemoryService(
        data_root,
        conversation_store=conversations,
        reconcile_interval_seconds=60,
    ).start()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            service.reconcile_once().result(timeout=20)
        path = data_root / DATABASE_RELATIVE_PATH
        assert service.status()["store"]["terminal_cursor"] == 0
        assert count(path, "terminal_projection_receipts") == 1
        assert count(path, "experience_episodes") == 0
    finally:
        assert service.shutdown(timeout=20)
