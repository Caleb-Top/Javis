from __future__ import annotations

import copy

import pytest

from core.intention.checkpoints import (
    CheckpointProjectionError,
    ThinkingCheckpointCandidate,
    ThinkingCheckpointProjector,
)
from core.intention.contracts import (
    ExecutionBudgetV1,
    MAX_CHECKPOINT_BYTES,
    ThinkingCheckpointV1,
)


CREATED_AT = "2026-08-20T12:10:00.000Z"
BUDGET = ExecutionBudgetV1.from_dict(
    {
        "schema_version": 1,
        "max_plan_steps": 8,
        "max_action_attempts": 3,
        "max_wall_seconds": 900,
        "max_model_tokens": 8192,
    }
)


def _candidate() -> dict[str, object]:
    return {
        "stage": "awaiting_action",
        "plan_id": "plan-1",
        "plan_revision": 2,
        "completed_step_ids": ["step-1"],
        "action_receipt_ids": ["receipt-1"],
        "evidence_refs": ["evidence-1"],
        "decision_summaries": [
            {"code": "path.selected", "summary": "Use the governed workspace path."}
        ],
        "assumptions": [
            {
                "statement": "The workspace mount remains available.",
                "source_ref": "evidence-1",
                "confidence": 0.9,
                "invalidation_condition": "The workspace mount changes.",
            }
        ],
        "open_questions": ["Does the owner want a second verification read?"],
        "next_safe_step": {
            "code": "request.action",
            "summary": "Submit one bounded action request to L6.",
            "capability_hint": "workspace.write",
        },
        "blocker_codes": [],
    }


def _project(
    candidate: ThinkingCheckpointCandidate | dict[str, object],
) -> ThinkingCheckpointV1:
    return ThinkingCheckpointProjector().project(
        candidate,
        checkpoint_id="checkpoint-1",
        intention_id="intention-1",
        commitment_id="commitment-1",
        budget_snapshot=BUDGET,
        created_at_utc=CREATED_AT,
    )


def test_projector_is_deterministic_and_does_not_mutate_candidate():
    candidate = _candidate()
    original = copy.deepcopy(candidate)
    reordered = dict(reversed(tuple(candidate.items())))

    first = _project(candidate)
    second = _project(reordered)

    assert candidate == original
    assert first == second
    assert first.content_hash == second.content_hash
    assert first.to_dict() == {
        "schema_version": 1,
        "checkpoint_id": "checkpoint-1",
        "intention_id": "intention-1",
        "commitment_id": "commitment-1",
        "plan_id": "plan-1",
        "plan_revision": 2,
        "stage": "awaiting_action",
        "completed_step_ids": ["step-1"],
        "action_receipt_ids": ["receipt-1"],
        "evidence_refs": ["evidence-1"],
        "decision_summaries": [
            {"code": "path.selected", "summary": "Use the governed workspace path."}
        ],
        "assumptions": [
            {
                "statement": "The workspace mount remains available.",
                "source_ref": "evidence-1",
                "confidence": 0.9,
                "invalidation_condition": "The workspace mount changes.",
            }
        ],
        "open_questions": ["Does the owner want a second verification read?"],
        "next_safe_step": {
            "code": "request.action",
            "summary": "Submit one bounded action request to L6.",
            "capability_hint": "workspace.write",
        },
        "blocker_codes": [],
        "budget_snapshot": BUDGET.to_dict(),
        "created_at_utc": CREATED_AT,
        "content_hash": first.content_hash,
    }
    assert len(first.canonical_json_bytes()) <= MAX_CHECKPOINT_BYTES


def test_candidate_accepts_only_bounded_checkpoint_projection_fields():
    candidate = _candidate()
    candidate["tool_arguments"] = {"path": "secret"}

    with pytest.raises(
        CheckpointProjectionError, match="checkpoint.unexpected_field"
    ):
        _project(candidate)

    too_long = _candidate()
    too_long["open_questions"] = ["x" * 241]
    with pytest.raises(ValueError, match="240 UTF-8 bytes"):
        _project(too_long)


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "reasoning_content",
        "Reasoning-Content",
        "r_e-a.s o/n i\\nG cOnTeNt",
        "messages",
        "Mes-Sa_Ges",
        "prompt",
        "SYSTEM PROMPT",
        "developer.prompt",
        "scratchpad",
        "Scratch Pad",
        "token probability",
        "tokenProbability",
        "TOKEN-PROBABILITIES",
        "logits",
        "chain of thought",
        "C.O.T",
        "hidden-state",
        "raw model output",
    ],
)
def test_projector_recursively_rejects_forbidden_key_synonyms(forbidden_key):
    candidate = _candidate()
    candidate["decision_summaries"][0][forbidden_key] = "RAW-COT-CANARY"

    with pytest.raises(
        CheckpointProjectionError, match="checkpoint.forbidden_field"
    ) as rejected:
        _project(candidate)

    assert "RAW-COT-CANARY" not in str(rejected.value)


def test_forbidden_scan_precedes_unknown_field_handling_at_every_depth():
    candidate = _candidate()
    candidate["next_safe_step"] = {
        "code": "request.action",
        "summary": "Submit one bounded action request.",
        "capability_hint": "workspace.write",
        "wrapper": [{"DeVeLoPeR---PrOmPt": "RAW-COT-CANARY"}],
    }

    with pytest.raises(
        CheckpointProjectionError, match="checkpoint.forbidden_field"
    ):
        _project(candidate)


def test_projector_rejects_output_over_16_kib_instead_of_truncating():
    under = _candidate()
    under["completed_step_ids"] = [
        f"step-{index:03d}-" + "x" * 60 for index in range(128)
    ]
    projected = _project(under)
    assert len(projected.canonical_json_bytes()) <= MAX_CHECKPOINT_BYTES

    over = _candidate()
    over["completed_step_ids"] = [
        f"step-{index:03d}-" + "x" * 125 for index in range(128)
    ]
    with pytest.raises(ValueError, match="16 KiB"):
        _project(over)


def test_typed_candidate_and_mapping_candidate_have_the_same_projection():
    mapping = _candidate()
    typed = ThinkingCheckpointCandidate.from_dict(mapping)

    assert _project(typed) == _project(mapping)
