import sqlite3

import pytest

from core.conversation_store import ConversationStore, ConversationStoreError


def _accepted_payload(text: str = "remember this") -> dict:
    return {
        "text": text,
        "interaction_mode": "live",
        "access_projection": {
            "schema_version": 1,
            "context_id": "context-1",
            "actor_subject_id": "subject-user",
            "actor_kind": "primary_user",
            "session_id": "session-1",
            "participant_subject_ids": ["subject-user", "subject-javis"],
            "audience_ceiling": "owner_private",
            "identity_assurance": "desktop_confirmed",
            "acl_epoch": 7,
            "bearer_token": "must-not-leak",
            "nonce": "must-not-leak",
        },
    }


def _completed_request(store: ConversationStore, request_id: str = "request-1"):
    accepted = store.accept_request(
        "session-1",
        request_id,
        f"key-{request_id}",
        "remember this",
        _accepted_payload(),
    )
    terminal = store.append_event(
        "session-1",
        request_id,
        "request.completed",
        {"detail": "private completion detail"},
    )
    assistant = store.append_message(
        "session-1", request_id, "assistant", "private assistant response"
    )
    return accepted, terminal, assistant


def test_source_store_id_is_durable_across_reopen_and_unique_per_database(tmp_path):
    path = tmp_path / "conversations.sqlite3"
    source_store_id = ConversationStore(path).source_store_id()

    assert ConversationStore(path).source_store_id() == source_store_id
    assert (
        ConversationStore(tmp_path / "other.sqlite3").source_store_id()
        != source_store_id
    )

    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT value FROM conversation_meta WHERE key='source_store_id'"
        ).fetchone()[0] == source_store_id


def test_scan_terminal_events_pages_by_opaque_global_row_cursor(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    store.append_event("session-1", "request-1", "request.accepted", {})
    first = store.append_event("session-1", "request-1", "request.completed", {})
    store.append_event("session-1", "request-1", "activity.planning", {})
    second = store.append_event("session-2", "request-2", "request.failed", {})
    third = store.append_event("session-1", "request-3", "request.cancelled", {})

    page = store.scan_terminal_events(0, 2)
    next_page = store.scan_terminal_events(
        after_row_id=page["next_row_id"], limit=2
    )

    assert page["source_store_id"] == store.source_store_id()
    assert page["has_more"] is True
    assert [event["event_id"] for event in page["events"]] == [
        first["event_id"],
        second["event_id"],
    ]
    assert [event["outcome"] for event in page["events"]] == [
        "completed",
        "failed",
    ]
    assert page["events"][0]["sequence_domain"] == "conversation_store:session-1"
    assert page["events"][1]["sequence_domain"] == "conversation_store:session-2"
    assert page["next_row_id"] == page["events"][-1]["row_id"]
    assert next_page["has_more"] is False
    assert [event["event_id"] for event in next_page["events"]] == [
        third["event_id"]
    ]


def test_completed_evidence_waits_for_late_assistant_message(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    accepted = store.accept_request(
        "session-1",
        "request-1",
        "key-1",
        "remember this",
        _accepted_payload(),
    )
    terminal = store.append_event(
        "session-1", "request-1", "request.completed", {"detail": "done"}
    )

    pending = store.read_request_evidence("session-1", "request-1")
    assistant = store.append_message(
        "session-1", "request-1", "assistant", "I will remember it."
    )
    ready = store.read_request_evidence("session-1", "request-1")

    assert pending["evidence_ready"] is False
    assert ready["evidence_ready"] is True
    assert ready["accepted_event"]["event_id"] == accepted["event"]["event_id"]
    assert ready["terminal_event"]["event_id"] == terminal["event_id"]
    assert ready["outcome"] == "completed"
    assert [message["message_id"] for message in ready["messages"]] == [
        accepted["message"]["message_id"],
        assistant["message_id"],
    ]
    assert ready["access_projection"] == {
        "schema_version": 1,
        "context_id": "context-1",
        "actor_subject_id": "subject-user",
        "actor_kind": "primary_user",
        "session_id": "session-1",
        "participant_subject_ids": ["subject-user", "subject-javis"],
        "audience_ceiling": "owner_private",
        "identity_assurance": "desktop_confirmed",
        "acl_epoch": 7,
    }


def test_request_with_multiple_terminal_events_is_conflicted(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    _completed_request(store)
    store.append_event(
        "session-1", "request-1", "request.failed", {"error": "late conflict"}
    )

    evidence = store.read_request_evidence("session-1", "request-1")

    assert evidence["terminal_conflict"] is True
    assert evidence["terminal_event"] is None
    assert evidence["outcome"] is None
    assert evidence["evidence_ready"] is False
    assert [event["type"] for event in evidence["terminal_events"]] == [
        "request.completed",
        "request.failed",
    ]


def test_failed_evidence_returns_interrupted_message_without_waiting_for_completion(
    tmp_path,
):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    store.accept_request(
        "session-1", "request-1", "key-1", "try this", _accepted_payload("try this")
    )
    store.append_event(
        "session-1", "request-1", "request.failed", {"error": "failed"}
    )
    interrupted = store.append_message(
        "session-1",
        "request-1",
        "assistant",
        "partial response",
        status="interrupted",
    )

    evidence = store.read_request_evidence("session-1", "request-1")

    assert evidence["outcome"] == "failed"
    assert evidence["evidence_ready"] is True
    assert evidence["messages"][-1] == interrupted


def test_redaction_is_idempotent_and_preserves_replay_identity(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    accepted, _, _ = _completed_request(store)
    store.append_event(
        "session-1",
        "request-1",
        "activity.tool_completed",
        {"output": "private tool output", "success": True},
    )
    events_before = store.events_after("session-1")
    event_identity = [
        (event["event_id"], event["sequence"], event["type"], event["request_id"])
        for event in events_before
    ]
    message_ids = [
        message["message_id"]
        for message in store.history("session-1", include_interrupted=True)
    ]

    receipt = store.redact_request_evidence(
        "session-1", "request-1", "deletion-1"
    )
    repeated = store.redact_request_evidence(
        "session-1", "request-1", "deletion-1"
    )
    reopened = ConversationStore(store.path)
    repeated_with_new_id = reopened.redact_request_evidence(
        "session-1", "request-1", "deletion-2"
    )

    assert repeated == receipt
    assert repeated_with_new_id == receipt
    assert receipt["deletion_id"] == "deletion-1"
    events_after = store.events_after("session-1")
    assert [
        (event["event_id"], event["sequence"], event["type"], event["request_id"])
        for event in events_after
    ] == event_identity
    assert all(
        event["payload"]
        == {"deletion_id": "deletion-1", "redacted": True, "tombstone": True}
        for event in events_after
    )
    history = store.history("session-1", include_interrupted=True)
    assert [message["message_id"] for message in history] == message_ids
    assert all(message["content"] == "" for message in history)
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT GROUP_CONCAT(content, '') FROM conversation_messages "
            "WHERE session_id='session-1' AND request_id='request-1'"
        ).fetchone()[0] == ""
        assert all(
            "private" not in payload
            for (payload,) in db.execute(
                "SELECT payload_json FROM conversation_events "
                "WHERE session_id='session-1' AND request_id='request-1'"
            )
        )

    replay = store.accept_request(
        "session-1",
        "request-replayed",
        "key-request-1",
        "tampered replay",
        {"tampered": True},
    )
    assert replay["duplicate"] is True
    assert replay["request_id"] == "request-1"
    assert replay["message"]["message_id"] == accepted["message"]["message_id"]
    assert replay["message"]["content"] == ""
    assert replay["event"]["event_id"] == accepted["event"]["event_id"]
    assert replay["event"]["payload"]["tombstone"] is True

    evidence = store.read_request_evidence("session-1", "request-1")
    assert evidence["redacted"] is True
    assert evidence["evidence_ready"] is False
    assert evidence["redaction_receipt"] == receipt


def test_redaction_fences_late_message_and_event_content(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    _completed_request(store)
    store.redact_request_evidence("session-1", "request-1", "deletion-1")

    message = store.append_message(
        "session-1", "request-1", "assistant", "late private response"
    )
    event = store.append_event(
        "session-1", "request-1", "activity.error", {"detail": "late secret"}
    )

    assert message["content"] == ""
    assert event["payload"] == {
        "deletion_id": "deletion-1",
        "redacted": True,
        "tombstone": True,
    }
    assert "late private response" not in str(
        store.read_request_evidence("session-1", "request-1")
    )


def test_redaction_rejects_unknown_request_without_creating_receipt(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")

    with pytest.raises(ConversationStoreError, match="does not exist"):
        store.redact_request_evidence("session-1", "missing", "deletion-1")
