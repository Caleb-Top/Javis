from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from types import MappingProxyType

import pytest

from core.life.l7.contracts import (
    BodyCapabilityV1,
    CandidateTransitionV1,
    ExpressionPlanV1,
    GrowthCandidateV1,
    GrowthDecisionV1,
    SkillArtifactV1,
    SkillDeploymentV1,
    SleepJobReceiptV1,
    SleepPolicyV1,
    SleepRunTransitionV1,
    SleepRunV1,
    canonical_content_hash,
    canonical_json_bytes,
)


NOW = "2026-08-20T01:02:03.000Z"
LATER = "2026-08-20T02:02:03.000Z"
END = "2026-08-21T01:02:03.000Z"
HASH_A = "a" * 64
HASH_B = "b" * 64
PERSISTENCE = {
    "privacy_class": "local_internal",
    "retention_class": "operational",
    "provenance": {"adapter": "test", "source_ids": ["source-1"]},
}


def _signed(payload: dict) -> dict:
    result = dict(payload)
    result["content_hash"] = canonical_content_hash(result)
    return result


def _policy() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "policy_id": "policy-1",
            "identity_id": "javis-1",
            "owner_subject_id": "owner-1",
            "revision": 1,
            "enabled": True,
            "approval_id": "approval-1",
            "approved_at_utc": NOW,
            "schedule_mode": "quiet_hours",
            "timezone": "UTC",
            "quiet_start_local": "01:00",
            "quiet_end_local": "05:00",
            "idle_min_seconds": 900,
            "require_ac_power": True,
            "minimum_battery_percent": 40,
            "maximum_cpu_percent": 50,
            "minimum_free_bytes": 1_000_000,
            "network_policy": "deny",
            "allowed_job_kinds": ["index_verify", "summary_build"],
            "max_run_seconds": 3_600,
            "max_job_seconds": 900,
            "max_input_records": 1_000,
            "max_output_bytes": 1_000_000,
            "wake_on_user_activity": True,
            "effective_at_utc": NOW,
            "expires_at_utc": END,
            "created_at_utc": NOW,
            **PERSISTENCE,
        }
    )


def _run() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "run_id": "run-1",
            "policy_id": "policy-1",
            "policy_revision": 1,
            "identity_id": "javis-1",
            "instance_id": "instance-1",
            "owner_subject_id": "owner-1",
            "trigger_kind": "manual",
            "trigger_event_id": "event-1",
            "idempotency_key": "idem-run-1",
            "state": "running",
            "revision": 2,
            "scheduled_at_utc": NOW,
            "started_at_utc": NOW,
            "ended_at_utc": None,
            "preflight_snapshot_hash": HASH_A,
            "source_checkpoint_ids": ["checkpoint-1"],
            "job_specs": [
                {
                    "job_id": "job-1",
                    "kind": "index_verify",
                    "budget": {"records": 100, "bytes": 4096},
                }
            ],
            "current_job_id": "job-1",
            "completed_job_ids": [],
            "receipt_ids": [],
            "candidate_version_ids": [],
            "cancel_reason_code": None,
            "failure_reason_code": None,
            "resource_usage": {"elapsed_ms": 25, "output_bytes": 0},
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
            **PERSISTENCE,
        }
    )


def _sleep_transition() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "transition_id": "transition-1",
            "run_id": "run-1",
            "from_state": "scheduled",
            "to_state": "preflight",
            "expected_revision": 1,
            "reason_code": "scheduler.accepted",
            "checkpoint_id": None,
            "receipt_ids": [],
            "created_at_utc": NOW,
            "idempotency_key": "idem-transition-1",
        }
    )


def _job_receipt() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "receipt_id": "receipt-1",
            "run_id": "run-1",
            "job_id": "job-1",
            "job_kind": "index_verify",
            "status": "completed",
            "input_set_hash": HASH_A,
            "output_set_hash": HASH_B,
            "processed_count": 10,
            "skipped_count": 1,
            "reason_code": None,
            "error_code": None,
            "checkpoint_id": "checkpoint-1",
            "source_ids": ["source-1"],
            "started_at_utc": NOW,
            "ended_at_utc": LATER,
            "duration_ms": 100,
            "output_bytes": 512,
            **PERSISTENCE,
        }
    )


def _candidate() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "candidate_id": "candidate-1",
            "candidate_version_id": "candidate-version-1",
            "version_number": 1,
            "previous_version_id": None,
            "previous_content_hash": None,
            "identity_id": "javis-1",
            "instance_id": "instance-1",
            "owner_subject_id": "owner-1",
            "candidate_kind": "skill",
            "title": "Bounded local skill",
            "bounded_summary": "A candidate derived from terminal evidence.",
            "source_evidence_ids": ["evidence-1"],
            "source_evidence_hash": HASH_A,
            "minimum_evidence_count": 1,
            "evidence_window": {"start_at_utc": NOW, "end_at_utc": LATER},
            "expected_benefit": {"metric": "latency", "direction": "lower"},
            "applicability": {"platforms": ["windows"]},
            "assumptions": ["local runtime available"],
            "risk_level": "medium",
            "risk_reasons": ["requests one local capability"],
            "permission_delta": {"added": ["workspace.read"], "removed": []},
            "validation_plan": {"test_suites": ["contract"]},
            "acceptance_thresholds": {"pass_rate": 1.0},
            "rollback_plan": {"kind": "pointer_restore"},
            "rollback_artifact_refs": [],
            "requires_user_approval": True,
            "created_by": "growth-proposer-1",
            "created_at_utc": NOW,
            "expires_at_utc": END,
            **PERSISTENCE,
        }
    )


def _candidate_transition() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "transition_id": "candidate-transition-1",
            "candidate_version_id": "candidate-version-1",
            "from_state": "proposed",
            "to_state": "evidence_ready",
            "expected_revision": 1,
            "actor_kind": "system",
            "actor_id": "growth-store-1",
            "decision_id": None,
            "reason_code": "evidence.threshold.met",
            "receipt_ids": ["receipt-1"],
            "artifact_id": None,
            "created_at_utc": NOW,
            "idempotency_key": "idem-candidate-transition-1",
        }
    )


def _decision() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "decision_id": "decision-1",
            "candidate_version_id": "candidate-version-1",
            "decision_kind": "approve",
            "actor_subject_id": "owner-1",
            "expected_revision": 6,
            "candidate_content_hash": HASH_A,
            "artifact_id": "artifact-1",
            "artifact_payload_sha256": HASH_B,
            "test_receipt_ids": ["test-receipt-1"],
            "permission_delta_hash": HASH_A,
            "reason_code": "user.approved",
            "created_at_utc": NOW,
            "idempotency_key": "idem-decision-1",
        }
    )


def _artifact() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "artifact_id": "artifact-1",
            "artifact_kind": "skill",
            "candidate_version_id": "candidate-version-1",
            "skill_name": "local_skill",
            "skill_version": "1.0.0",
            "payload_sha256": HASH_A,
            "manifest_sha256": HASH_B,
            "total_bytes": 4096,
            "file_count": 2,
            "entrypoints": {"python": "main"},
            "source_manifest": {"files": ["skill.py", "manifest.json"]},
            "source_archive_sha256": HASH_A,
            "license_spdx": "MIT",
            "attribution_refs": ["attribution-1"],
            "sbom_ref": "sbom-1",
            "dependency_lock_hash": HASH_B,
            "required_capabilities": ["workspace.read"],
            "sandbox_policy_version": "1",
            "build_receipt_id": "build-receipt-1",
            "test_receipt_ids": ["test-receipt-1"],
            "compatibility_range": {"minimum_release": "1.0.0"},
            "created_at_utc": NOW,
            "created_by_forge_version": "1.0.0",
            "privacy_class": "local_internal",
            "retention_class": "audit",
        }
    )


def _deployment() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "deployment_id": "deployment-1",
            "skill_name": "local_skill",
            "artifact_id": "artifact-1",
            "artifact_payload_sha256": HASH_A,
            "candidate_version_id": "candidate-version-1",
            "catalog_record_id": "catalog-1",
            "catalog_status_required": "active",
            "approval_id": "approval-1",
            "required_capabilities": ["workspace.read"],
            "granted_capability_refs": ["grant-1"],
            "previous_deployment_id": None,
            "deployed_at_utc": NOW,
            "deployed_by": "owner-1",
            "rollback_receipt_id": None,
            "revision": 1,
        }
    )


def _body_capability() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "capability_id": "capability-1",
            "body_id": "lightform",
            "body_version": "1.0.0",
            "manifest_sha256": HASH_A,
            "renderer_kind": "procedural",
            "supported_channels": ["mouth_open", "gaze"],
            "channel_ranges": {"mouth_open": {"minimum": 0.0, "maximum": 1.0}},
            "viseme_set": [],
            "supported_gestures": ["acknowledge"],
            "supported_postures": ["idle", "listening"],
            "supports_gaze": True,
            "supports_blink": True,
            "supports_energy_mouth": True,
            "supports_word_timing": False,
            "supports_phonemes": False,
            "interrupt_latency_budget_ms": 100,
            "performance_tiers": ["high", "medium", "low"],
            "reduced_motion_substitutions": {"acknowledge": "gaze"},
            "fallback_body_id": "orb",
            "license_ref": "license-1",
            "created_at_utc": NOW,
        }
    )


def _expression_plan() -> dict:
    return _signed(
        {
            "schema_version": 1,
            "plan_id": "plan-1",
            "body_id": "lightform",
            "capability_id": "capability-1",
            "source_expression_id": "expression-1",
            "source_expression_revision": 2,
            "source_snapshot_revision": 3,
            "request_id": "request-1",
            "playback_generation": 4,
            "generated_at_utc": NOW,
            "expires_at_utc": LATER,
            "priority": 50,
            "interrupt": False,
            "timing_source": "energy",
            "channel_tracks": {
                "mouth_open": [
                    {"at_ms": 0, "value": 0.0, "interpolation": "linear"},
                    {"at_ms": 100, "value": 0.5, "interpolation": "linear"},
                ]
            },
            "reduced_motion_applied": False,
            "unsupported_channels": [],
            "fallback_actions": [],
            "transition_ms": 120,
        }
    )


CONTRACTS = (
    (SleepPolicyV1, _policy),
    (SleepRunV1, _run),
    (SleepRunTransitionV1, _sleep_transition),
    (SleepJobReceiptV1, _job_receipt),
    (GrowthCandidateV1, _candidate),
    (CandidateTransitionV1, _candidate_transition),
    (GrowthDecisionV1, _decision),
    (SkillArtifactV1, _artifact),
    (SkillDeploymentV1, _deployment),
    (BodyCapabilityV1, _body_capability),
    (ExpressionPlanV1, _expression_plan),
)


@pytest.mark.parametrize(("contract_type", "factory"), CONTRACTS)
def test_all_contracts_have_exact_fields_round_trip_and_are_frozen(contract_type, factory):
    wire = factory()
    contract = contract_type.from_dict(wire)

    assert contract.to_dict() == wire
    assert tuple(field.name for field in fields(contract_type)) == tuple(wire)
    assert contract.canonical_content_hash() == wire["content_hash"]
    with pytest.raises(FrozenInstanceError):
        contract.content_hash = HASH_B


@pytest.mark.parametrize(("contract_type", "factory"), CONTRACTS)
def test_all_contracts_reject_missing_unknown_and_tampered_fields(contract_type, factory):
    wire = factory()
    missing = dict(wire)
    missing.pop(next(iter(wire)))
    with pytest.raises(ValueError, match="missing field"):
        contract_type.from_dict(missing)

    unknown = dict(wire, unexpected=True)
    with pytest.raises(ValueError, match="unexpected field"):
        contract_type.from_dict(unknown)

    tampered = dict(wire)
    editable = next(name for name in tampered if name not in {"schema_version", "content_hash"})
    tampered[editable] = "tampered"
    with pytest.raises(ValueError):
        contract_type.from_dict(tampered)


def test_canonical_profile_is_utf8_sorted_compact_and_excludes_content_hash():
    payload = {"z": "贾维斯", "a": {"b": 1}}
    assert canonical_json_bytes(payload) == '{"a":{"b":1},"z":"贾维斯"}'.encode()
    signed = _signed(payload)
    assert canonical_content_hash(signed) == canonical_content_hash(payload)


def test_nested_maps_and_lists_are_recursively_frozen_and_reallocated():
    wire = _candidate()
    candidate = GrowthCandidateV1.from_dict(wire)
    assert isinstance(candidate.validation_plan, MappingProxyType)
    assert isinstance(candidate.validation_plan["test_suites"], tuple)

    with pytest.raises(TypeError):
        candidate.validation_plan["new"] = True
    with pytest.raises(TypeError):
        candidate.validation_plan["test_suites"][0] = "changed"

    thawed = candidate.to_dict()
    thawed["validation_plan"]["test_suites"].append("other")
    assert candidate.to_dict() == wire


@pytest.mark.parametrize("forbidden", ["secret", "api_key", "raw_audio", "hidden_reasoning", "source_code"])
def test_recursive_sensitive_fields_are_rejected(forbidden):
    wire = _candidate()
    wire["validation_plan"] = {"nested": {forbidden: "must-not-cross"}}
    wire["content_hash"] = canonical_content_hash(wire)
    with pytest.raises(ValueError, match="forbidden"):
        GrowthCandidateV1.from_dict(wire)


def test_nan_infinity_oversized_text_and_invalid_enum_are_rejected():
    wire = _candidate()
    wire["acceptance_thresholds"] = {"score": float("nan")}
    with pytest.raises(ValueError, match="finite"):
        GrowthCandidateV1.from_dict(wire)

    wire = _candidate()
    wire["title"] = "x" * 161
    wire["content_hash"] = canonical_content_hash(wire)
    with pytest.raises(ValueError, match="at most 160"):
        GrowthCandidateV1.from_dict(wire)

    wire = _policy()
    wire["schedule_mode"] = "whenever"
    wire["content_hash"] = canonical_content_hash(wire)
    with pytest.raises(ValueError, match="unknown ScheduleMode"):
        SleepPolicyV1.from_dict(wire)


def test_sleep_and_candidate_state_machines_reject_illegal_jumps():
    wire = _sleep_transition()
    wire["to_state"] = "completed"
    wire["content_hash"] = canonical_content_hash(wire)
    with pytest.raises(ValueError, match="illegal transition"):
        SleepRunTransitionV1.from_dict(wire)

    wire = _candidate_transition()
    wire["to_state"] = "active"
    wire["content_hash"] = canonical_content_hash(wire)
    with pytest.raises(ValueError, match="illegal transition"):
        CandidateTransitionV1.from_dict(wire)


def test_model_cannot_approve_and_approval_binds_exact_artifact_receipts():
    transition = _signed(
        {
            **{key: value for key, value in _candidate_transition().items() if key != "content_hash"},
            "from_state": "awaiting_approval",
            "to_state": "approved",
            "actor_kind": "model_proposer",
            "decision_id": "decision-1",
        }
    )
    with pytest.raises(ValueError, match="cannot approve"):
        CandidateTransitionV1.from_dict(transition)

    decision = _decision()
    decision["test_receipt_ids"] = []
    decision["content_hash"] = canonical_content_hash(decision)
    with pytest.raises(ValueError, match="approval requires"):
        GrowthDecisionV1.from_dict(decision)


def test_expression_tracks_are_bounded_monotonic_and_strict():
    wire = _expression_plan()
    wire["channel_tracks"]["mouth_open"][1]["at_ms"] = -1
    wire["content_hash"] = canonical_content_hash(wire)
    with pytest.raises(ValueError, match="at_ms"):
        ExpressionPlanV1.from_dict(wire)

    wire = _expression_plan()
    wire["channel_tracks"]["mouth_open"][0]["extra"] = True
    wire["content_hash"] = canonical_content_hash(wire)
    with pytest.raises(ValueError, match="exactly"):
        ExpressionPlanV1.from_dict(wire)


def test_body_phonemes_require_real_timing_capability():
    wire = _body_capability()
    wire["supports_phonemes"] = True
    wire["content_hash"] = canonical_content_hash(wire)
    with pytest.raises(ValueError, match="word timing"):
        BodyCapabilityV1.from_dict(wire)
