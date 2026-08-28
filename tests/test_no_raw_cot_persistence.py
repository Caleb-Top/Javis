from __future__ import annotations

import json
import logging

import pytest

from core.agent_runs import AgentRunStore
from core.conversation_store import ConversationStore
from core.events import EventBus
from core.intention.checkpoints import (
    CheckpointProjectionError,
    ThinkingCheckpointProjector,
)
from core.intention.contracts import ExecutionBudgetV1
from core.intention.store import IntentionStore


CANARY = "RAW-COT-PERSISTENCE-CANARY-9F24A7"
BUDGET = ExecutionBudgetV1.from_dict(
    {
        "schema_version": 1,
        "max_plan_steps": 8,
        "max_action_attempts": 3,
        "max_wall_seconds": 900,
        "max_model_tokens": 8192,
    }
)


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "reasoning_content",
        "messages",
        "prompt",
        "scratchpad",
        "token probability",
        "token-probabilities",
        "logits",
        "chain_of_thought",
    ],
)
def test_rejected_raw_cot_never_reaches_any_persistence_or_observability_surface(
    tmp_path, caplog, forbidden_key
):
    l5_root = tmp_path / "life"
    with IntentionStore(l5_root):
        pass

    run_store = AgentRunStore(tmp_path / "agent-runs.sqlite3")
    run_store.create_run("safe objective", run_id="run-1")
    run_store.close()

    conversation_store = ConversationStore(tmp_path / "conversations.sqlite3")
    conversation_store.append_event(
        "session-1", "request-1", "activity.planning", {"code": "planning.started"}
    )

    event_bus = EventBus()
    error_response: dict[str, str]
    candidate = {
        "stage": "planning",
        "plan_id": "plan-1",
        "plan_revision": 1,
        "completed_step_ids": [],
        "action_receipt_ids": [],
        "evidence_refs": [],
        "decision_summaries": [
            {
                "code": "plan.selected",
                "summary": "Use the bounded plan.",
                forbidden_key: CANARY,
            }
        ],
        "assumptions": [],
        "open_questions": [],
        "next_safe_step": None,
        "blocker_codes": [],
    }

    caplog.set_level(logging.WARNING)
    try:
        ThinkingCheckpointProjector().project(
            candidate,
            checkpoint_id="checkpoint-rejected",
            intention_id="intention-1",
            commitment_id=None,
            budget_snapshot=BUDGET,
            created_at_utc="2026-08-20T12:10:00.000Z",
        )
    except CheckpointProjectionError as exc:
        error_response = {"error": exc.reason_code}
        logging.getLogger("javis.intention.checkpoints").warning(
            "Checkpoint candidate rejected: %s", exc.reason_code
        )
        event_bus.publish(
            "intention.checkpoint.rejected",
            {"reason_code": exc.reason_code},
            source="intention",
        )
    else:  # pragma: no cover - the assertion explains the security boundary
        pytest.fail("raw CoT fixture was accepted")

    event_capture = [
        {"type": event.type, "payload": event.payload}
        for event in event_bus.history()
    ]
    observable_text = "\n".join(
        (
            json.dumps(error_response, sort_keys=True),
            json.dumps(event_capture, sort_keys=True),
            caplog.text,
        )
    )
    persisted_bytes = b"".join(
        path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    )

    assert CANARY not in observable_text
    assert CANARY.encode() not in persisted_bytes
    assert forbidden_key not in json.dumps(error_response)
