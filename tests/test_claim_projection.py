import json
import sqlite3
from dataclasses import replace

import pytest

from core.conversation_store import ConversationStore
from core.life.memory.service import MemoryService
from core.life.memory.claims import claim_prompt_eligible
from core.life.memory.contracts import UserModelClaim
from core.life.memory.store import DATABASE_RELATIVE_PATH
from tests.test_memory_deletion import _access, _participant, _subject


def complete(conversations, request_id, text):
    access = _access("recall").to_dict()
    access["guest_present"] = False
    conversations.accept_request(
        "session-1", request_id, "key-" + request_id, text,
        {"access_projection": access, "execution_lane": "exclusive"},
    )
    conversations.append_message("session-1", request_id, "assistant", "Understood.")
    conversations.append_event("session-1", request_id, "request.completed", {})


def start(tmp_path):
    conversations = ConversationStore(tmp_path / "conversation.sqlite3")
    service = MemoryService(tmp_path / "data", conversation_store=conversations,
                            reconcile_interval_seconds=3600).start()
    for subject_id, kind, role in (
        ("subject-user", "primary_user", "primary"), ("subject-javis", "javis", "javis")
    ):
        service.put_subject(_subject(subject_id, kind)).result(timeout=5)
        service.put_session_participant(_participant(subject_id, role)).result(timeout=5)
    return service, conversations


def test_completed_original_preference_projects_claim_and_conflicts(tmp_path):
    service, conversations = start(tmp_path)
    try:
        complete(conversations, "brief", "Remember: I prefer brief answers.")
        assert service.reconcile_once().result(timeout=5)["advanced"] == 1
        with sqlite3.connect(service.data_root / DATABASE_RELATIVE_PATH) as db:
            wire = json.loads(db.execute("SELECT payload_json FROM user_model_claims").fetchone()[0])
            assert wire["value"] == "brief"
            assert wire["status"] == "active"
            assert wire["subject_id"] == "subject-user"
            assert db.execute("SELECT COUNT(*) FROM derivation_edges WHERE target_id = ?", (wire["claim_id"],)).fetchone()[0] == 1
        complete(conversations, "detailed", "Remember: I prefer detailed answers.")
        assert service.reconcile_once().result(timeout=5)["advanced"] == 1
        assert service.reconcile_once().result(timeout=5)["advanced"] == 0
        with sqlite3.connect(service.data_root / DATABASE_RELATIVE_PATH) as db:
            claims = [json.loads(row[0]) for row in db.execute("SELECT payload_json FROM user_model_claims")]
            assert len(claims) == 2
            assert all(item["contradiction_claim_ids"] for item in claims)
            assert next(item for item in claims if item["value"] == "detailed")["status"] == "candidate"
            access = replace(_access("recall"), guest_present=False)
            assert all(not claim_prompt_eligible(UserModelClaim.from_dict(item), access,
                                                at_utc=item["updated_at_utc"]) for item in claims)
    finally:
        assert service.shutdown()


def test_claim_failure_rolls_back_episode_edges_and_cursor(tmp_path):
    service, conversations = start(tmp_path)
    try:
        complete(conversations, "brief", "Remember: I prefer brief answers.")
        with sqlite3.connect(service.data_root / DATABASE_RELATIVE_PATH) as db:
            cursor = db.execute("SELECT terminal_cursor FROM memory_meta").fetchone()[0]
            db.execute("CREATE TRIGGER fail_projected_claim BEFORE INSERT ON user_model_claims "
                       "BEGIN SELECT RAISE(ABORT, 'claim failed'); END")
        with pytest.raises(sqlite3.IntegrityError, match="claim failed"):
            service.reconcile_once().result(timeout=5)
        with sqlite3.connect(service.data_root / DATABASE_RELATIVE_PATH) as db:
            assert db.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM derivation_edges").fetchone()[0] == 0
            assert db.execute("SELECT terminal_cursor FROM memory_meta").fetchone()[0] == cursor
            assert db.execute("SELECT projection_state FROM terminal_projection_receipts").fetchone()[0] == "pending"
            db.execute("DROP TRIGGER fail_projected_claim")
            db.execute("UPDATE terminal_projection_receipts SET next_retry_at_utc = NULL")
        assert service.reconcile_once().result(timeout=5)["advanced"] == 1
        with sqlite3.connect(service.data_root / DATABASE_RELATIVE_PATH) as db:
            assert db.execute("SELECT COUNT(*) FROM user_model_claims").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM experience_episodes").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM derivation_edges").fetchone()[0] == 1
    finally:
        assert service.shutdown()


@pytest.mark.parametrize("text,predicate,value,sensitivity,status", [
    ("Remember: do not mention my health.", "boundary.do_not_mention", ["health"], "sensitive", "candidate"),
    ("Remember: do not store my finances.", "boundary.do_not_store", ["finances"], "sensitive", "candidate"),
    ("Remember: ask me before discussing my family.", "boundary.ask_before_topic", ["family"], "sensitive", "candidate"),
    ("Remember: I prefer concise confirmations.", "workflow.preferred_confirmation_style", "concise", "ordinary", "active"),
    ("Remember: my focus window is 22:00-06:00.", "workflow.focus_window", "22:00-06:00", "personal", "active"),
])
def test_workflow_and_boundary_projection_policy(tmp_path, text, predicate, value, sensitivity, status):
    service, conversations = start(tmp_path)
    try:
        complete(conversations, "policy", text)
        assert service.reconcile_once().result(timeout=5)["advanced"] == 1
        with sqlite3.connect(service.data_root / DATABASE_RELATIVE_PATH) as db:
            row = db.execute("SELECT payload_json FROM user_model_claims").fetchone()
            assert row is not None
            wire = json.loads(row[0])
            assert (wire["predicate"], wire["value"], wire["sensitivity"], wire["status"]) == (
                predicate, value, sensitivity, status,
            )
            assert wire["confirmation_event_id"] is None
            access = replace(_access("recall"), guest_present=False)
            assert claim_prompt_eligible(UserModelClaim.from_dict(wire), access,
                                         at_utc=wire["updated_at_utc"]) is (status == "active")
    finally:
        assert service.shutdown()
