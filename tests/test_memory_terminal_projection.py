from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.conversation_store import ConversationStore
from core.conversation_hub import ConversationHub
from core.agent_runs import AgentRunStore
from core.life.memory.projection import (
    TerminalProjectionError,
    TerminalProjector,
    terminal_receipt_id,
)
from core.life.memory.service import MemoryService
from core.life.memory.store import DATABASE_RELATIVE_PATH


NOW = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)


def terminal(
    *,
    session_id: str = "session-1",
    request_id: str = "request-1",
    row_id: int = 11,
    sequence: int = 3,
    outcome: str = "completed",
):
    return {
        "terminal_row_id": row_id,
        "event_id": f"terminal-{session_id}-{request_id}",
        "session_id": session_id,
        "request_id": request_id,
        "sequence": sequence,
        "sequence_domain": f"conversation_store:{session_id}",
        "outcome": outcome,
    }


def evidence(
    event,
    *,
    source_store_id: str = "conversation-store-1",
    actor_kind: str = "primary_user",
    ready: bool = True,
    redacted: bool = False,
    conflict: bool = False,
):
    actor_subject_id = "subject-user" if actor_kind != "guest" else "guest-session-1"
    participants = ["subject-user", "subject-javis"] if actor_kind != "guest" else []
    return {
        "source_store_id": source_store_id,
        "session_id": event["session_id"],
        "request_id": event["request_id"],
        "sequence_domain": event["sequence_domain"],
        "terminal_events": (
            [
                {
                    "event_id": event["event_id"],
                    "sequence": event["sequence"],
                    "type": f"request.{event['outcome']}",
                }
            ]
            if not conflict
            else [
                {"event_id": event["event_id"], "sequence": 3, "type": "request.completed"},
                {"event_id": "other-terminal", "sequence": 4, "type": "request.failed"},
            ]
        ),
        "terminal_conflict": conflict,
        "evidence_ready": ready,
        "redacted": redacted,
        "redaction_receipt": {"receipt_id": "redaction-1"} if redacted else None,
        "access_projection": {
            "actor_kind": actor_kind,
            "actor_subject_id": actor_subject_id,
            "participant_subject_ids": participants,
            "audience_ceiling": "guest" if actor_kind == "guest" else "owner_private",
            "identity_assurance": "guest" if actor_kind == "guest" else "desktop_confirmed",
            "acl_epoch": 7,
        },
        "messages": [
            {"message_id": "message-user", "role": "user", "content": "remember this"},
            {"message_id": "message-assistant", "role": "assistant", "content": "noted"},
        ],
    }


def access_projection(session_id: str, *, guest: bool = False):
    return {
        "schema_version": 1,
        "context_id": f"context-{session_id}",
        "actor_subject_id": f"guest-{session_id}" if guest else "subject-user",
        "actor_kind": "guest" if guest else "primary_user",
        "session_id": session_id,
        "participant_subject_ids": [] if guest else ["subject-user", "subject-javis"],
        "audience_ceiling": "guest" if guest else "owner_private",
        "identity_assurance": "guest" if guest else "desktop_confirmed",
        "acl_epoch": 0,
    }


def completed_request(
    store: ConversationStore,
    session_id: str,
    request_id: str,
    *,
    assistant: bool = True,
    guest: bool = False,
):
    store.accept_request(
        session_id,
        request_id,
        f"key-{session_id}-{request_id}",
        "Remember this verified decision",
        {"access_projection": access_projection(session_id, guest=guest)},
    )
    if assistant:
        store.append_message(session_id, request_id, "assistant", "Decision recorded")
    return store.append_event(session_id, request_id, "request.completed", {})


def request_with_outcome(
    store: ConversationStore, session_id: str, request_id: str, outcome: str
):
    store.accept_request(
        session_id,
        request_id,
        f"key-{session_id}-{request_id}",
        "attempt",
        {"access_projection": access_projection(session_id)},
    )
    return store.append_event(session_id, request_id, f"request.{outcome}", {})


def row_count(path: Path, table: str) -> int:
    db = sqlite3.connect(path)
    try:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        db.close()


def test_completed_authorized_evidence_yields_content_free_candidate():
    event = terminal()
    decision = TerminalProjector().decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=evidence(event),
        now_utc=NOW,
    )

    assert decision.projection_state == "pending"
    assert decision.reason_code == "eligible_candidate"
    assert decision.advance_cursor is False
    assert decision.candidate is not None
    assert len(decision.candidate.source_digest) == 64
    assert "remember this" not in repr(decision.candidate)
    assert decision.candidate.participant_subject_ids == ("subject-user", "subject-javis")


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "interrupted"])
def test_unsuccessful_terminals_are_final_exclusions_without_evidence(outcome: str):
    event = terminal(outcome=outcome)
    decision = TerminalProjector().decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=None,
        now_utc=NOW,
    )
    assert decision.projection_state == "excluded"
    assert decision.reason_code == f"terminal_{outcome}"
    assert decision.advance_cursor is True
    assert decision.candidate is None


def test_pending_receipt_has_bounded_backoff_and_immediate_replay_does_not_rewrite():
    event = terminal()
    projector = TerminalProjector(retry_base_seconds=1, retry_max_seconds=2)
    first = projector.decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=evidence(event, ready=False),
        now_utc=NOW,
    )
    immediate = projector.decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=evidence(event, ready=False),
        existing_receipt=first.receipt_values,
        now_utc=NOW + timedelta(milliseconds=100),
    )
    retry = projector.decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=evidence(event, ready=False),
        existing_receipt=first.receipt_values,
        now_utc=NOW + timedelta(seconds=1),
    )

    assert first.receipt_values["attempt"] == 1
    assert immediate.write_receipt is False
    assert immediate.receipt_values["attempt"] == 1
    assert retry.write_receipt is True
    assert retry.receipt_values["attempt"] == 2
    assert retry.receipt_values["next_retry_at_utc"] == "2026-08-20T10:00:03.000Z"


def test_guest_missing_participant_and_redacted_evidence_are_excluded():
    event = terminal()
    projector = TerminalProjector()
    guest = projector.decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=evidence(event, actor_kind="guest"),
        now_utc=NOW,
    )
    missing = evidence(event)
    missing["access_projection"]["participant_subject_ids"] = ["subject-javis"]
    unbound = projector.decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=missing,
        now_utc=NOW,
    )
    deleted = projector.decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=evidence(event, redacted=True),
        now_utc=NOW,
    )

    assert (guest.reason_code, unbound.reason_code, deleted.reason_code) == (
        "guest_session",
        "participant_binding_missing",
        "deletion_suppressed",
    )
    assert all(item.advance_cursor for item in (guest, unbound, deleted))


def test_source_event_and_sequence_domain_mismatches_fail_closed():
    projector = TerminalProjector()
    event = terminal()
    bad_domain = dict(event, sequence_domain="conversation_store:other-session")
    with pytest.raises(TerminalProjectionError, match="sequence_domain_mismatch"):
        projector.decide(
            source_store_id="conversation-store-1",
            terminal=bad_domain,
            evidence=evidence(event),
            now_utc=NOW,
        )
    with pytest.raises(TerminalProjectionError, match="source_store_mismatch"):
        projector.decide(
            source_store_id="conversation-store-1",
            terminal=event,
            evidence=evidence(event, source_store_id="other-store"),
            now_utc=NOW,
        )
    changed = evidence(event)
    changed["terminal_events"][0]["event_id"] = "other-terminal"
    with pytest.raises(TerminalProjectionError, match="terminal_evidence_mismatch"):
        projector.decide(
            source_store_id="conversation-store-1",
            terminal=event,
            evidence=changed,
            now_utc=NOW,
        )


def test_receipt_identity_is_stable_and_session_domain_prevents_sequence_collision():
    one = terminal_receipt_id("store-1", "session-1", "request-1")
    replay = terminal_receipt_id("store-1", "session-1", "request-1")
    other_session = terminal_receipt_id("store-1", "session-2", "request-1")
    assert one == replay
    assert one != other_session


def test_evidence_digest_is_deterministic_across_mapping_order():
    event = terminal()
    first_evidence = evidence(event)
    reordered = dict(reversed(list(first_evidence.items())))
    projector = TerminalProjector()
    first = projector.decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=first_evidence,
        now_utc=NOW,
    )
    second = projector.decide(
        source_store_id="conversation-store-1",
        terminal=event,
        evidence=reordered,
        now_utc=NOW,
    )
    assert first.candidate.source_digest == second.candidate.source_digest


def test_duplicate_wakeup_and_restart_keep_one_receipt(tmp_path: Path):
    conversations = ConversationStore(tmp_path / "conversations.sqlite3")
    completed_request(conversations, "session-1", "request-1")
    data_root = tmp_path / "data"
    projector = TerminalProjector(retry_base_seconds=60, retry_max_seconds=60)

    first = MemoryService(
        data_root,
        conversation_store=conversations,
        reconcile_interval_seconds=60,
        terminal_projector=projector,
    ).start()
    try:
        assert first.reconcile_once().result(timeout=20)["pending"] == 1
        assert first.reconcile_once().result(timeout=20)["pending"] == 1
        path = data_root / DATABASE_RELATIVE_PATH
        assert row_count(path, "terminal_projection_receipts") == 1
    finally:
        assert first.shutdown(timeout=20)

    restarted = MemoryService(
        data_root,
        conversation_store=conversations,
        reconcile_interval_seconds=60,
        terminal_projector=projector,
    ).start()
    try:
        assert restarted.reconcile_once().result(timeout=20)["pending"] == 1
        assert row_count(path, "terminal_projection_receipts") == 1
        assert restarted.status()["store"]["terminal_cursor"] == 0
    finally:
        assert restarted.shutdown(timeout=20)


def test_late_assistant_message_updates_pending_receipt_without_duplication(tmp_path: Path):
    conversations = ConversationStore(tmp_path / "conversations.sqlite3")
    completed_request(conversations, "session-1", "request-1", assistant=False)
    data_root = tmp_path / "data"
    service = MemoryService(
        data_root,
        conversation_store=conversations,
        reconcile_interval_seconds=60,
        terminal_projector=TerminalProjector(
            retry_base_seconds=0.001, retry_max_seconds=0.002
        ),
    ).start()
    try:
        assert service.reconcile_once().result(timeout=20)["pending"] == 1
        receipt = service.get_terminal_receipt(
            conversations.source_store_id(), "session-1", "request-1"
        ).result(timeout=20)
        assert receipt["reason_code"] == "evidence_pending"
        conversations.append_message("session-1", "request-1", "assistant", "late result")
        time.sleep(0.005)
        assert service.reconcile_once().result(timeout=20)["pending"] == 1
        updated = service.get_terminal_receipt(
            conversations.source_store_id(), "session-1", "request-1"
        ).result(timeout=20)
        assert updated["receipt_id"] == receipt["receipt_id"]
        assert updated["reason_code"] == "eligible_candidate"
        assert row_count(data_root / DATABASE_RELATIVE_PATH, "terminal_projection_receipts") == 1
    finally:
        assert service.shutdown(timeout=20)


def test_failed_and_cancelled_requests_create_no_memory_objects(tmp_path: Path):
    conversations = ConversationStore(tmp_path / "conversations.sqlite3")
    request_with_outcome(conversations, "session-1", "request-failed", "failed")
    request_with_outcome(conversations, "session-2", "request-cancelled", "cancelled")
    data_root = tmp_path / "data"
    service = MemoryService(
        data_root,
        conversation_store=conversations,
        reconcile_interval_seconds=60,
    ).start()
    try:
        result = service.reconcile_once().result(timeout=20)
        assert result == {"scanned": 2, "advanced": 2, "pending": 0}
        path = data_root / DATABASE_RELATIVE_PATH
        assert row_count(path, "terminal_projection_receipts") == 2
        for table in (
            "memory_items",
            "experience_episodes",
            "journal_entries",
            "shared_memories",
            "user_model_claims",
            "relationship_events",
        ):
            assert row_count(path, table) == 0
    finally:
        assert service.shutdown(timeout=20)


def test_terminal_conflict_and_deletion_suppression_have_distinct_cursor_rules(
    tmp_path: Path,
):
    conflict_store = ConversationStore(tmp_path / "conflict.sqlite3")
    completed_request(conflict_store, "session-conflict", "request-1")
    conflict_store.append_event("session-conflict", "request-1", "request.failed", {})
    conflict_service = MemoryService(
        tmp_path / "conflict-data",
        conversation_store=conflict_store,
        reconcile_interval_seconds=60,
    ).start()
    try:
        assert conflict_service.reconcile_once().result(timeout=20)["pending"] == 1
        assert conflict_service.status()["store"]["terminal_cursor"] == 0
        assert conflict_service.status()["state"] == "degraded"
        assert conflict_service.status()["reason_code"] == "terminal_conflict"
    finally:
        assert conflict_service.shutdown(timeout=20)

    deleted_store = ConversationStore(tmp_path / "deleted.sqlite3")
    completed_request(deleted_store, "session-deleted", "request-1")
    deleted_store.redact_request_evidence("session-deleted", "request-1", "deletion-1")
    deleted_service = MemoryService(
        tmp_path / "deleted-data",
        conversation_store=deleted_store,
        reconcile_interval_seconds=60,
    ).start()
    try:
        result = deleted_service.reconcile_once().result(timeout=20)
        assert result["advanced"] == 1
        receipt = deleted_service.get_terminal_receipt(
            deleted_store.source_store_id(), "session-deleted", "request-1"
        ).result(timeout=20)
        assert receipt["reason_code"] == "deletion_suppressed"
    finally:
        assert deleted_service.shutdown(timeout=20)


def test_conversation_hub_terminal_wakeup_is_best_effort_and_non_blocking(tmp_path: Path):
    conversations = ConversationStore(tmp_path / "conversations.sqlite3")
    runs = AgentRunStore(tmp_path / "runs.sqlite3")
    wakeups: list[str] = []

    def wakeup():
        wakeups.append("terminal")
        if len(wakeups) == 1:
            raise RuntimeError("simulated dropped wakeup")

    hub = ConversationHub(conversations, runs, terminal_wakeup=wakeup)

    async def publish():
        await hub._publish("session-1", "request-1", "activity.thinking", {})
        await hub._publish("session-1", "request-1", "request.completed", {})
        await hub._publish("session-2", "request-2", "request.failed", {})

    try:
        asyncio.run(publish())
        assert wakeups == ["terminal", "terminal"]
        assert len(conversations.scan_terminal_events()["events"]) == 2
    finally:
        runs.close()
