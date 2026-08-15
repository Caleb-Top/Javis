import sqlite3

import pytest

from core.conversation_store import ConversationStore


def _accepted_payload():
    return {
        "text": "hello",
        "interaction_mode": "live",
        "replaces_request_id": "",
        "input_provenance": {
            "modality": "text",
            "verification": "client_claimed",
            "runtime_boot_id": None,
            "source_session_id": None,
            "owner_generation": None,
            "voice_sequence": None,
            "voice_turn": None,
        },
    }


def test_accept_request_atomically_writes_claim_message_and_event(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")

    result = store.accept_request(
        "session-1",
        "request-1",
        "message-1",
        "hello",
        _accepted_payload(),
    )

    assert result["accepted"] is True
    assert result["event"]["type"] == "request.accepted"
    assert store.history("session-1") == [result["message"]]
    assert store.events_after("session-1") == [result["event"]]


def test_accept_request_replay_returns_original_without_new_rows(tmp_path):
    store = ConversationStore(tmp_path / "conversations.sqlite3")
    first = store.accept_request(
        "session-1", "request-1", "message-1", "hello", _accepted_payload()
    )

    replay = store.accept_request(
        "session-1", "request-2", "message-1", "tampered", _accepted_payload()
    )

    assert replay["accepted"] is False
    assert replay["duplicate"] is True
    assert replay["request_id"] == "request-1"
    assert replay["event"] == first["event"]
    assert len(store.history("session-1")) == 1
    assert len(store.events_after("session-1")) == 1


def test_accept_request_rolls_back_every_row_when_event_insert_fails(tmp_path):
    path = tmp_path / "conversations.sqlite3"
    store = ConversationStore(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TRIGGER reject_accept BEFORE INSERT ON conversation_events "
            "WHEN NEW.type='request.accepted' BEGIN "
            "SELECT RAISE(ABORT, 'accept rejected'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="accept rejected"):
        store.accept_request(
            "session-1", "request-1", "message-1", "hello", _accepted_payload()
        )

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM request_keys").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0] == 0
