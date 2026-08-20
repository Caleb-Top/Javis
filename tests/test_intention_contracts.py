from __future__ import annotations

import copy
import dataclasses
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import pytest

from core.intention.contracts import (
    AudienceScope,
    CommitmentState,
    CommitmentV1,
    CriterionOutcome,
    CriterionResultV1,
    ExecutionBudgetV1,
    INTENTION_STATE_TRANSITIONS,
    IntentionEventType,
    IntentionEventV1,
    IntentionKind,
    IntentionState,
    IntentionTransitionEventV1,
    IntentionV1,
    InterruptionCost,
    JAVIS_CANONICAL_JSON_V1,
    MAX_CHECKPOINT_BYTES,
    PrivacyClass,
    ResumePolicy,
    RetentionClass,
    RiskCeiling,
    SuggestionBudgetState,
    SuggestionBudgetV1,
    SuggestionCategory,
    SuggestionOutcome,
    SuccessCriterionV1,
    TERMINAL_INTENTION_STATES,
    ThinkingCheckpointV1,
    ThinkingStage,
    TriggerKind,
    Urgency,
    VerificationRecordV1,
    VerificationRequestedBy,
    VerificationResult,
    VerifierKind,
    canonical_content_hash,
    canonical_json_bytes,
    is_valid_intention_transition,
)


ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-08-20T12:00:00.000Z"
T05 = "2026-08-20T12:05:00.000Z"
T10 = "2026-08-20T12:10:00.000Z"
T30 = "2026-08-20T12:30:00.000Z"
T60 = "2026-08-20T13:00:00.000Z"
T120 = "2026-08-20T14:00:00.000Z"
T24H = "2026-08-21T12:00:00.000Z"


def _seal(payload: dict[str, object]) -> dict[str, object]:
    wire = copy.deepcopy(payload)
    wire.pop("content_hash", None)
    wire["content_hash"] = canonical_content_hash(wire)
    return wire


CRITERION = {
    "schema_version": 1,
    "criterion_id": "criterion-1",
    "description": "The requested file content is visible on read-back.",
    "required": True,
    "verifier_kind": "deterministic",
    "evidence_requirements": ["file.readback"],
    "freshness_seconds": 3600,
    "subjective": False,
    "user_confirmation_required": None,
}

EXECUTION_BUDGET = {
    "schema_version": 1,
    "max_plan_steps": 8,
    "max_action_attempts": 3,
    "max_wall_seconds": 900,
    "max_model_tokens": 8192,
}

INTENTION = _seal(
    {
        "schema_version": 1,
        "intention_id": "intention-1",
        "javis_identity_id": "identity-1",
        "instance_id": "instance-1",
        "owner_subject_id": "subject-owner",
        "participant_ids": ["subject-owner"],
        "audience": "owner_private",
        "source_event_ids": ["event-request-1"],
        "runtime_boot_id": "boot-1",
        "created_at_utc": T0,
        "updated_at_utc": T0,
        "expires_at_utc": T120,
        "privacy_class": "user_private",
        "retention_class": "continuity",
        "state": "accepted",
        "revision": 1,
        "provenance": "conversation",
        "kind": "request",
        "trigger_kind": "explicit_command",
        "why_summary": "The owner explicitly requested this result.",
        "expected_user_value": "The requested result is ready and verified.",
        "goal_statement": "Create the requested file and verify its exact content.",
        "success_criteria": [CRITERION],
        "execution_budget": EXECUTION_BUDGET,
        "risk_ceiling": "low",
        "capability_hints": ["workspace.write", "workspace.read"],
        "urgency": "normal",
        "interruption_cost": "low",
        "valid_until_utc": T120,
        "cancellation_conditions": ["user.cancelled", "source.deleted"],
        "commitment_id": "commitment-1",
        "latest_checkpoint_id": None,
        "latest_verification_id": None,
        "state_reason_code": "user.request.accepted",
        "supersedes_intention_id": None,
    }
)

COMMITMENT = _seal(
    {
        "schema_version": 1,
        "commitment_id": "commitment-1",
        "javis_identity_id": "identity-1",
        "instance_id": "instance-1",
        "owner_subject_id": "subject-owner",
        "participant_ids": ["subject-owner"],
        "audience": "owner_private",
        "source_event_ids": ["event-request-1"],
        "runtime_boot_id": "boot-1",
        "created_at_utc": T0,
        "updated_at_utc": T0,
        "expires_at_utc": T120,
        "privacy_class": "user_private",
        "retention_class": "continuity",
        "state": "active",
        "revision": 1,
        "provenance": "conversation",
        "intention_id": "intention-1",
        "accepted_from_event_id": "event-request-1",
        "promise_summary": "Javis will track this request through verification.",
        "due_at_utc": T60,
        "resume_policy": "reconcile_then_ask",
        "current_blockers": [],
        "next_review_at_utc": None,
        "last_checkpoint_id": None,
        "last_action_receipt_id": None,
        "last_recovery_receipt_id": None,
        "verification_record_ids": [],
        "state_reason_code": "commitment.activated",
    }
)

CHECKPOINT = _seal(
    {
        "schema_version": 1,
        "checkpoint_id": "checkpoint-1",
        "intention_id": "intention-1",
        "commitment_id": "commitment-1",
        "plan_id": "plan-1",
        "plan_revision": 2,
        "stage": "awaiting_action",
        "completed_step_ids": ["step-1"],
        "action_receipt_ids": ["receipt-1"],
        "evidence_refs": ["environment-fact-1"],
        "decision_summaries": [
            {"code": "path.selected", "summary": "Use the governed workspace path."}
        ],
        "assumptions": [
            {
                "statement": "The target path remains available.",
                "source_ref": "environment-fact-1",
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
        "budget_snapshot": EXECUTION_BUDGET,
        "created_at_utc": T10,
    }
)

CRITERION_RESULT = {
    "schema_version": 1,
    "criterion_id": "criterion-1",
    "outcome": "satisfied",
    "evidence_refs": ["receipt-1", "environment-fact-1"],
    "fresh_until_utc": T60,
    "method": "exact.content.readback",
    "explanation_code": "content.matches",
}

VERIFICATION = _seal(
    {
        "schema_version": 1,
        "verification_id": "verification-1",
        "javis_identity_id": "identity-1",
        "instance_id": "instance-1",
        "owner_subject_id": "subject-owner",
        "participant_ids": ["subject-owner"],
        "audience": "owner_private",
        "source_event_ids": ["event-request-1"],
        "runtime_boot_id": "boot-1",
        "created_at_utc": T10,
        "updated_at_utc": T10,
        "expires_at_utc": T60,
        "privacy_class": "user_private",
        "retention_class": "audit",
        "state": "satisfied",
        "revision": 1,
        "provenance": "goal.verifier",
        "intention_id": "intention-1",
        "intention_revision": 3,
        "commitment_id": "commitment-1",
        "requested_by": "intention_service",
        "verifier_kind": "deterministic",
        "verifier_version": "exact.readback.v1",
        "criterion_results": [CRITERION_RESULT],
        "evidence_refs": ["receipt-1", "environment-fact-1"],
        "observed_at_utc": T05,
        "result": "satisfied",
        "limitations": [],
        "conflicting_evidence_refs": [],
    }
)

SUGGESTION_BUDGET = _seal(
    {
        "schema_version": 1,
        "budget_id": "budget-1",
        "javis_identity_id": "identity-1",
        "instance_id": "instance-1",
        "owner_subject_id": "subject-owner",
        "participant_ids": ["subject-owner"],
        "audience": "owner_private",
        "source_event_ids": [],
        "runtime_boot_id": "boot-1",
        "created_at_utc": T0,
        "updated_at_utc": T30,
        "expires_at_utc": T24H,
        "privacy_class": "user_private",
        "retention_class": "operational",
        "state": "available",
        "revision": 2,
        "provenance": "intention.service",
        "category": "workflow",
        "window_started_at_utc": T0,
        "window_ends_at_utc": T24H,
        "max_presentations": 1,
        "presentations_used": 0,
        "global_budget_revision": 4,
        "cooldown_until_utc": None,
        "quiet_hours": {
            "timezone_id": "Asia/Shanghai",
            "starts_at_local": "22:00",
            "ends_at_local": "08:00",
        },
        "suppressed_until_utc": None,
        "last_candidate_id": None,
        "last_presented_intention_id": None,
        "last_outcome": "none",
    }
)

TRANSITION = _seal(
    {
        "schema_version": 1,
        "transition_id": "transition-1",
        "intention_id": "intention-1",
        "from_state": "accepted",
        "to_state": "active",
        "expected_revision": 1,
        "resulting_revision": 2,
        "idempotency_key": "request-1:activate",
        "reason_code": "criteria.budget.frozen",
        "evidence_refs": [],
        "runtime_boot_id": "boot-1",
        "occurred_at_utc": T10,
    }
)

EVENT = {
    "schema_version": 1,
    "event_id": "thin-event-1",
    "event_type": "intention.state_changed",
    "object_id": "intention-1",
    "owner_subject_hash": "a" * 64,
    "revision": 2,
    "state": "active",
    "reason_code": "criteria.budget.frozen",
    "source_event_id": "event-request-1",
    "occurred_at_utc": T10,
}

GOLDEN_INTENTION_HASH = "275740acdaed758f8c35207ced9cf1ebc080d0cd4e283e034e1647cbfbe07eb2"


@pytest.mark.parametrize(
    ("contract", "wire"),
    [
        (SuccessCriterionV1, CRITERION),
        (ExecutionBudgetV1, EXECUTION_BUDGET),
        (IntentionV1, INTENTION),
        (CommitmentV1, COMMITMENT),
        (ThinkingCheckpointV1, CHECKPOINT),
        (CriterionResultV1, CRITERION_RESULT),
        (VerificationRecordV1, VERIFICATION),
        (SuggestionBudgetV1, SUGGESTION_BUDGET),
        (IntentionTransitionEventV1, TRANSITION),
        (IntentionEventV1, EVENT),
    ],
)
def test_all_contract_fields_round_trip_and_instances_are_frozen(contract, wire):
    value = contract.from_dict(copy.deepcopy(wire))
    assert value.to_dict() == wire
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.schema_version = 2


@pytest.mark.parametrize(
    ("contract", "wire"),
    [
        (SuccessCriterionV1, CRITERION),
        (ExecutionBudgetV1, EXECUTION_BUDGET),
        (IntentionV1, INTENTION),
        (CommitmentV1, COMMITMENT),
        (ThinkingCheckpointV1, CHECKPOINT),
        (CriterionResultV1, CRITERION_RESULT),
        (VerificationRecordV1, VERIFICATION),
        (SuggestionBudgetV1, SUGGESTION_BUDGET),
        (IntentionTransitionEventV1, TRANSITION),
        (IntentionEventV1, EVENT),
    ],
)
def test_all_parsers_reject_missing_and_unknown_fields(contract, wire):
    missing = copy.deepcopy(wire)
    missing.pop(next(iter(missing)))
    with pytest.raises(ValueError, match="missing field"):
        contract.from_dict(missing)

    unexpected = copy.deepcopy(wire)
    unexpected["surprise"] = "not allowed"
    with pytest.raises(ValueError, match="unexpected field"):
        contract.from_dict(unexpected)


def test_enum_sets_are_frozen_to_the_specification():
    assert {item.value for item in IntentionKind} == {
        "request", "suggestion", "commitment", "goal", "risk_response", "maintenance"
    }
    assert {item.value for item in TriggerKind} == {
        "conversation", "governed_event", "schedule", "recovery", "explicit_command"
    }
    assert {item.value for item in RiskCeiling} == {
        "none", "low", "medium", "high", "irreversible"
    }
    assert {item.value for item in Urgency} == {
        "background", "normal", "time_sensitive", "safety"
    }
    assert {item.value for item in InterruptionCost} == {"silent", "low", "medium", "high"}
    assert {item.value for item in ResumePolicy} == {
        "ask", "reconcile_then_ask", "no_effect_only"
    }
    assert {item.value for item in ThinkingStage} == {
        "planning", "awaiting_action", "awaiting_observation", "awaiting_user",
        "ready_to_verify", "blocked"
    }
    assert {item.value for item in VerificationRequestedBy} == {
        "intention_service", "user", "recovery_reconcile"
    }
    assert {item.value for item in SuggestionCategory} == {
        "safety", "reminder", "workflow", "wellbeing", "maintenance"
    }
    assert {item.value for item in SuggestionOutcome} == {
        "none", "accepted", "dismissed", "rejected", "expired"
    }


def test_canonical_profile_is_utf8_nfc_sorted_compact_and_golden():
    assert JAVIS_CANONICAL_JSON_V1 == "JAVIS_CANONICAL_JSON_V1"
    payload = {"z": "e\u0301", "a": "贾维斯"}
    assert canonical_json_bytes(payload) == '{"a":"贾维斯","z":"é"}'.encode()
    assert b"\n" not in canonical_json_bytes(payload)
    assert INTENTION["content_hash"] == GOLDEN_INTENTION_HASH
    assert canonical_content_hash(INTENTION) == GOLDEN_INTENTION_HASH


def test_canonical_hash_is_stable_across_processes_and_key_order():
    script = (
        "import json,sys;"
        "from core.intention.contracts import canonical_content_hash;"
        "print(canonical_content_hash(json.load(sys.stdin)))"
    )
    shuffled = dict(reversed(list(INTENTION.items())))
    output = subprocess.check_output(
        [sys.executable, "-c", script],
        cwd=ROOT,
        input=json.dumps(shuffled, ensure_ascii=False).encode("utf-8"),
    ).decode().strip()
    assert output == INTENTION["content_hash"]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_rejects_nan_and_infinity(invalid):
    with pytest.raises(ValueError, match="finite"):
        canonical_json_bytes({"number": invalid})


def test_canonical_json_rejects_duplicate_keys_after_nfc_normalization():
    with pytest.raises(ValueError, match="duplicate keys"):
        canonical_json_bytes({"é": 1, "e\u0301": 2})


@pytest.mark.parametrize(
    ("contract", "wire", "field", "replacement"),
    [
        (IntentionV1, INTENTION, "goal_statement", "tampered goal"),
        (CommitmentV1, COMMITMENT, "promise_summary", "tampered promise"),
        (ThinkingCheckpointV1, CHECKPOINT, "stage", "planning"),
        (VerificationRecordV1, VERIFICATION, "verifier_version", "other.v1"),
        (SuggestionBudgetV1, SUGGESTION_BUDGET, "global_budget_revision", 5),
        (IntentionTransitionEventV1, TRANSITION, "reason_code", "tampered"),
    ],
)
def test_hashed_contracts_reject_tampering(contract, wire, field, replacement):
    tampered = copy.deepcopy(wire)
    tampered[field] = replacement
    with pytest.raises(ValueError, match="content_hash"):
        contract.from_dict(tampered)


@pytest.mark.parametrize(
    ("contract", "wire", "path"),
    [
        (SuccessCriterionV1, CRITERION, ("freshness_seconds",)),
        (ExecutionBudgetV1, EXECUTION_BUDGET, ("max_plan_steps",)),
        (IntentionV1, INTENTION, ("revision",)),
        (ThinkingCheckpointV1, CHECKPOINT, ("plan_revision",)),
        (CriterionResultV1, CRITERION_RESULT, ("schema_version",)),
        (VerificationRecordV1, VERIFICATION, ("intention_revision",)),
        (SuggestionBudgetV1, SUGGESTION_BUDGET, ("presentations_used",)),
        (IntentionTransitionEventV1, TRANSITION, ("expected_revision",)),
    ],
)
def test_bool_is_never_accepted_as_an_integer(contract, wire, path):
    invalid = copy.deepcopy(wire)
    invalid[path[0]] = True
    if "content_hash" in invalid:
        invalid = _seal(invalid)
    with pytest.raises(ValueError, match="integer|schema_version"):
        contract.from_dict(invalid)


@pytest.mark.parametrize(
    "bad_time",
    [
        "2026-08-20T12:00:00Z",
        "2026-08-20T12:00:00.000+00:00",
        "2026-08-20 12:00:00.000Z",
        "2026-02-30T12:00:00.000Z",
        "2026-08-20T12:00:00.000z",
    ],
)
def test_noncanonical_or_invalid_utc_is_rejected(bad_time):
    invalid = copy.deepcopy(INTENTION)
    invalid["created_at_utc"] = bad_time
    invalid = _seal(invalid)
    with pytest.raises(ValueError, match="UTC|RFC3339"):
        IntentionV1.from_dict(invalid)


@pytest.mark.parametrize(
    ("field", "valid_size", "invalid_size"),
    [
        ("why_summary", 1024, 1025),
        ("expected_user_value", 512, 513),
        ("goal_statement", 2048, 2049),
    ],
)
def test_intention_text_limits_are_utf8_byte_boundaries(field, valid_size, invalid_size):
    valid = copy.deepcopy(INTENTION)
    valid[field] = "x" * valid_size
    IntentionV1.from_dict(_seal(valid))

    invalid = copy.deepcopy(INTENTION)
    invalid[field] = "x" * invalid_size
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        IntentionV1.from_dict(_seal(invalid))


def test_utf8_limits_count_bytes_instead_of_characters():
    valid = copy.deepcopy(INTENTION)
    valid["why_summary"] = "界" * 341
    IntentionV1.from_dict(_seal(valid))

    invalid = copy.deepcopy(INTENTION)
    invalid["why_summary"] = "界" * 342
    with pytest.raises(ValueError, match="1024 UTF-8 bytes"):
        IntentionV1.from_dict(_seal(invalid))


def test_success_criterion_and_commitment_text_boundaries():
    criterion = copy.deepcopy(CRITERION)
    criterion["description"] = "x" * 1024
    SuccessCriterionV1.from_dict(criterion)
    criterion["description"] += "x"
    with pytest.raises(ValueError, match="1024 UTF-8 bytes"):
        SuccessCriterionV1.from_dict(criterion)

    commitment = copy.deepcopy(COMMITMENT)
    commitment["promise_summary"] = "x" * 1024
    CommitmentV1.from_dict(_seal(commitment))
    commitment["promise_summary"] += "x"
    with pytest.raises(ValueError, match="1024 UTF-8 bytes"):
        CommitmentV1.from_dict(_seal(commitment))


def test_checkpoint_item_text_and_list_boundaries():
    valid = copy.deepcopy(CHECKPOINT)
    valid["decision_summaries"] = [
        {"code": f"decision.{index}", "summary": "x" * 240}
        for index in range(8)
    ]
    valid["assumptions"] = []
    valid["open_questions"] = ["x" * 240] * 8
    ThinkingCheckpointV1.from_dict(_seal(valid))

    too_many = copy.deepcopy(valid)
    too_many["decision_summaries"].append({"code": "decision.9", "summary": "x"})
    with pytest.raises(ValueError, match="0..8 items"):
        ThinkingCheckpointV1.from_dict(_seal(too_many))

    too_long = copy.deepcopy(CHECKPOINT)
    too_long["decision_summaries"][0]["summary"] = "x" * 241
    with pytest.raises(ValueError, match="240 UTF-8 bytes"):
        ThinkingCheckpointV1.from_dict(_seal(too_long))


def test_checkpoint_canonical_json_must_not_exceed_16_kib():
    under = copy.deepcopy(CHECKPOINT)
    under["completed_step_ids"] = [f"step-{index:03d}-" + "x" * 60 for index in range(128)]
    parsed = ThinkingCheckpointV1.from_dict(_seal(under))
    assert len(parsed.canonical_json_bytes()) <= MAX_CHECKPOINT_BYTES

    over = copy.deepcopy(CHECKPOINT)
    over["completed_step_ids"] = [f"step-{index:03d}-" + "x" * 125 for index in range(128)]
    with pytest.raises(ValueError, match="16 KiB"):
        ThinkingCheckpointV1.from_dict(_seal(over))


@pytest.mark.parametrize(
    "forbidden",
    [
        "chain_of_thought",
        "Chain-Of-Thought",
        "cot",
        "reasoning",
        "reasoningContent",
        "reasoning_content",
        "scratch_pad",
        "hiddenState",
        "system_prompt",
        "developer-prompt",
        "prompt",
        "messages",
        "raw_model_output",
        "raw_prompt",
        "logits",
        "token probabilities",
    ],
)
def test_parser_recursively_rejects_raw_cot_and_prompt_key_variants(forbidden):
    invalid = copy.deepcopy(CHECKPOINT)
    invalid["next_safe_step"][forbidden] = "must never persist"
    invalid = _seal(invalid)
    with pytest.raises(ValueError, match="forbidden field"):
        ThinkingCheckpointV1.from_dict(invalid)


def test_no_raw_cot_field_exists_in_checkpoint_wire_contract():
    wire = ThinkingCheckpointV1.from_dict(copy.deepcopy(CHECKPOINT)).to_dict()
    serialized = json.dumps(wire, sort_keys=True)
    for token in (
        "reasoning_content", "chain_of_thought", "raw_prompt", "messages", "scratchpad"
    ):
        assert token not in serialized


def test_intention_list_boundaries_and_duplicate_ids():
    valid = copy.deepcopy(INTENTION)
    valid["source_event_ids"] = [f"event-{index}" for index in range(32)]
    valid["success_criteria"] = [
        {**CRITERION, "criterion_id": f"criterion-{index}"} for index in range(16)
    ]
    valid["cancellation_conditions"] = [f"condition.{index}" for index in range(8)]
    IntentionV1.from_dict(_seal(valid))

    for field, value in (
        ("source_event_ids", []),
        ("source_event_ids", [f"event-{index}" for index in range(33)]),
        ("success_criteria", []),
        ("success_criteria", [{**CRITERION, "criterion_id": f"c-{i}"} for i in range(17)]),
        ("cancellation_conditions", [f"condition.{index}" for index in range(9)]),
    ):
        invalid = copy.deepcopy(INTENTION)
        invalid[field] = value
        with pytest.raises(ValueError, match="items"):
            IntentionV1.from_dict(_seal(invalid))

    duplicate = copy.deepcopy(INTENTION)
    duplicate["source_event_ids"] = ["event-1", "event-1"]
    with pytest.raises(ValueError, match="duplicates"):
        IntentionV1.from_dict(_seal(duplicate))


def test_wrong_common_envelope_fails_closed():
    mutations = {
        "schema_version": 2,
        "participant_ids": "subject-owner",
        "audience": "public",
        "privacy_class": "private-ish",
        "retention_class": "forever",
        "revision": 0,
        "provenance": {"untyped": "bypass"},
        "expires_at_utc": T60,
    }
    for field, replacement in mutations.items():
        invalid = copy.deepcopy(INTENTION)
        invalid[field] = replacement
        with pytest.raises(ValueError):
            IntentionV1.from_dict(_seal(invalid))


def test_nested_contracts_leave_no_untyped_mapping_persistence_path():
    persisted = (
        IntentionV1,
        CommitmentV1,
        ThinkingCheckpointV1,
        VerificationRecordV1,
        SuggestionBudgetV1,
    )

    def contains_untyped_mapping(annotation: object) -> bool:
        if annotation is Any:
            return True
        origin = get_origin(annotation)
        if origin in (dict, Mapping):
            return True
        return any(contains_untyped_mapping(item) for item in get_args(annotation))

    for contract in persisted:
        for annotation in get_type_hints(contract).values():
            assert not contains_untyped_mapping(annotation)


def test_subjective_criterion_requires_explicit_user_confirmation():
    invalid = copy.deepcopy(CRITERION)
    invalid["subjective"] = True
    invalid["user_confirmation_required"] = False
    with pytest.raises(ValueError, match="subjective"):
        SuccessCriterionV1.from_dict(invalid)

    invalid["user_confirmation_required"] = True
    with pytest.raises(ValueError, match="user_confirmation"):
        SuccessCriterionV1.from_dict(invalid)

    invalid["verifier_kind"] = "user_confirmation"
    SuccessCriterionV1.from_dict(invalid)


def test_commitment_waiting_and_blocked_invariants():
    waiting = copy.deepcopy(COMMITMENT)
    waiting["state"] = "waiting"
    waiting["next_review_at_utc"] = T30
    CommitmentV1.from_dict(_seal(waiting))

    blocked = copy.deepcopy(waiting)
    blocked["state"] = "blocked"
    blocked["current_blockers"] = [
        {"code": "permission.required", "evidence_refs": ["policy-denial-1"]}
    ]
    CommitmentV1.from_dict(_seal(blocked))

    blocked["current_blockers"] = []
    with pytest.raises(ValueError, match="blocked-state evidence"):
        CommitmentV1.from_dict(_seal(blocked))


def test_verification_rejects_missing_duplicate_conflicting_and_mismatched_results():
    duplicate = copy.deepcopy(VERIFICATION)
    duplicate["criterion_results"].append(copy.deepcopy(CRITERION_RESULT))
    with pytest.raises(ValueError, match="unique"):
        VerificationRecordV1.from_dict(_seal(duplicate))

    missing_record_evidence = copy.deepcopy(VERIFICATION)
    missing_record_evidence["evidence_refs"] = ["receipt-1"]
    with pytest.raises(ValueError, match="record evidence_refs"):
        VerificationRecordV1.from_dict(_seal(missing_record_evidence))

    conflicting = copy.deepcopy(VERIFICATION)
    conflicting["conflicting_evidence_refs"] = ["recovery-contradiction-1"]
    with pytest.raises(ValueError, match="conflicting evidence"):
        VerificationRecordV1.from_dict(_seal(conflicting))

    mismatched = copy.deepcopy(VERIFICATION)
    mismatched["state"] = "inconclusive"
    with pytest.raises(ValueError, match="must equal result"):
        VerificationRecordV1.from_dict(_seal(mismatched))


def test_verification_parser_defers_required_criterion_completion_gate_to_service():
    mixed = copy.deepcopy(VERIFICATION)
    mixed["criterion_results"].append(
        {
            "schema_version": 1,
            "criterion_id": "criterion-optional",
            "outcome": "inconclusive",
            "evidence_refs": [],
            "fresh_until_utc": T60,
            "method": "optional.observation",
            "explanation_code": "evidence.unavailable",
        }
    )
    assert VerificationRecordV1.from_dict(_seal(mixed)).result is VerificationResult.SATISFIED


def test_suggestion_budget_enforces_count_window_and_state_invariants():
    exhausted = copy.deepcopy(SUGGESTION_BUDGET)
    exhausted["state"] = "exhausted"
    exhausted["presentations_used"] = exhausted["max_presentations"]
    SuggestionBudgetV1.from_dict(_seal(exhausted))

    overused = copy.deepcopy(SUGGESTION_BUDGET)
    overused["presentations_used"] = 2
    with pytest.raises(ValueError, match="must not exceed"):
        SuggestionBudgetV1.from_dict(_seal(overused))

    cooling = copy.deepcopy(SUGGESTION_BUDGET)
    cooling["state"] = "cooling_down"
    cooling["cooldown_until_utc"] = T60
    SuggestionBudgetV1.from_dict(_seal(cooling))

    cooling["cooldown_until_utc"] = T0
    with pytest.raises(ValueError, match="future"):
        SuggestionBudgetV1.from_dict(_seal(cooling))


def test_state_machine_matrix_includes_every_legal_and_illegal_edge():
    all_from = [None, *list(IntentionState)]
    for from_state in all_from:
        for to_state in IntentionState:
            expected = to_state in INTENTION_STATE_TRANSITIONS[from_state]
            assert is_valid_intention_transition(from_state, to_state) is expected
    assert TERMINAL_INTENTION_STATES == {
        IntentionState.COMPLETED,
        IntentionState.FAILED,
        IntentionState.CANCELLED,
        IntentionState.EXPIRED,
    }
    for terminal in TERMINAL_INTENTION_STATES:
        assert not INTENTION_STATE_TRANSITIONS[terminal]


def test_transition_event_rejects_illegal_edges_stale_revision_and_missing_evidence():
    IntentionTransitionEventV1.from_dict(copy.deepcopy(TRANSITION))

    illegal = copy.deepcopy(TRANSITION)
    illegal["from_state"] = "completed"
    illegal["to_state"] = "active"
    with pytest.raises(ValueError, match="illegal transition"):
        IntentionTransitionEventV1.from_dict(_seal(illegal))

    stale = copy.deepcopy(TRANSITION)
    stale["resulting_revision"] = 9
    with pytest.raises(ValueError, match="expected_revision"):
        IntentionTransitionEventV1.from_dict(_seal(stale))

    completed = copy.deepcopy(TRANSITION)
    completed["from_state"] = "verifying"
    completed["to_state"] = "completed"
    completed["expected_revision"] = 4
    completed["resulting_revision"] = 5
    completed["evidence_refs"] = []
    with pytest.raises(ValueError, match="requires evidence"):
        IntentionTransitionEventV1.from_dict(_seal(completed))


def test_thin_event_allowlist_and_payload_remain_non_content_bearing():
    value = IntentionEventV1.from_dict(copy.deepcopy(EVENT))
    assert value.event_type is IntentionEventType.INTENTION_STATE_CHANGED

    wrong_state = copy.deepcopy(EVENT)
    wrong_state["event_type"] = "commitment.state_changed"
    wrong_state["state"] = "candidate"
    with pytest.raises(ValueError, match="does not match"):
        IntentionEventV1.from_dict(wrong_state)

    content_bearing = copy.deepcopy(EVENT)
    content_bearing["goal_statement"] = "private body"
    with pytest.raises(ValueError, match="unexpected field"):
        IntentionEventV1.from_dict(content_bearing)


def test_l0_privacy_and_retention_enums_are_reused_not_redeclared():
    assert IntentionV1.from_dict(copy.deepcopy(INTENTION)).privacy_class is PrivacyClass.USER_PRIVATE
    assert IntentionV1.from_dict(copy.deepcopy(INTENTION)).retention_class is RetentionClass.CONTINUITY
    assert AudienceScope.OWNER_PRIVATE.value == "owner_private"
    assert CommitmentState.FULFILLED.value == "fulfilled"
    assert VerificationResult.SATISFIED.value == CriterionOutcome.SATISFIED.value
    assert SuggestionBudgetState.SUPPRESSED.value == "suppressed"
