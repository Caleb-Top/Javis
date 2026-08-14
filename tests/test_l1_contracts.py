from __future__ import annotations

import copy
import dataclasses
from types import MappingProxyType

import pytest

import core.life.l1 as l1
from core.life.contracts import canonical_content_hash
from core.life.l1.contracts import (
    AffectEvidence,
    AppraisalResult,
    AttentionClaim,
    AttentionSnapshot,
    FunctionalAffect,
    HomeostasisSnapshot,
    InnerStateSnapshot,
    InputProvenance,
    LifeObservation,
    PlaybackLifecycleEvent,
    PresenceSnapshot,
    TurnExperienceReceipt,
)


PROVENANCE = {
    "modality": "voice",
    "verification": "server_verified",
    "runtime_boot_id": "boot-1",
    "source_session_id": "session-1",
    "owner_generation": 2,
    "voice_sequence": 9,
    "voice_turn": 3,
}

OBSERVATION = {
    "schema_version": 1,
    "observation_id": "observation-1",
    "kind": "request.started",
    "occurred_at_utc": "2026-08-12T10:00:00.000Z",
    "monotonic_offset_ms": 100,
    "source": "conversation_hub",
    "source_event_id": "event-1",
    "source_boot_id": "boot-1",
    "source_generation": 2,
    "session_id": "session-1",
    "request_id": "request-1",
    "correlation_id": "correlation-1",
    "causation_id": "causation-1",
    "sequence": 7,
    "sequence_domain": "conversation.session-1",
    "input_provenance": PROVENANCE,
    "outcome": "started",
    "risk_level": "low",
    "confidence": 0.9,
    "privacy_class": "local_internal",
    "retention_class": "operational",
}

CLAIM = {
    "claim_id": "claim-1",
    "target_kind": "request",
    "target_id": "request-1",
    "priority": 70,
    "source_observation_id": "observation-1",
    "acquired_at_utc": "2026-08-12T10:00:00.000Z",
    "expires_at_utc": "2026-08-12T10:01:00.000Z",
    "interruptible": True,
}

ATTENTION = {
    "mode": "engaged",
    "target_kind": "request",
    "target_id": "request-1",
    "priority": 70,
    "since_utc": "2026-08-12T10:00:00.000Z",
    "expires_at_utc": "2026-08-12T10:01:00.000Z",
    "source_observation_id": "observation-1",
}

EVIDENCE = {
    "evidence_id": "evidence-1",
    "kind": "cautious",
    "source_observation_id": "observation-1",
    "reason_code": "tool_risk_observed",
    "intensity": 0.4,
    "confidence": 0.9,
    "occurred_at_utc": "2026-08-12T10:00:00.000Z",
    "valid_until_utc": "2026-08-12T10:00:30.000Z",
}

APPRAISAL = {
    "schema_version": 1,
    "observation_id": "observation-1",
    "deltas": {"activation": 0.2, "cognitive_load": 0.2},
    "attention_claim": CLAIM,
    "affect_evidence": [EVIDENCE],
    "reason_code": "request_started",
    "confidence": 0.9,
    "expires_at_utc": "2026-08-12T10:01:00.000Z",
}

HOMEOSTASIS = {
    "updated_at_utc": "2026-08-12T10:00:00.000Z",
    "activation": 0.4,
    "cognitive_load": 0.25,
    "certainty": 0.5,
    "caution": 0.1,
    "curiosity": 0.25,
    "blockedness": 0.0,
    "social_presence": 0.3,
}

AFFECT = {
    "kind": "cautious",
    "intensity": 0.4,
    "confidence": 0.9,
    "reason_code": "tool_risk_observed",
    "evidence_ids": ["evidence-1"],
    "valid_until_utc": "2026-08-12T10:00:30.000Z",
}

PRESENCE = {
    "mode": "engaged",
    "intensity": 0.6,
    "session_id": "session-1",
    "source_observation_id": "observation-1",
    "reason_code": "request_started",
    "since_utc": "2026-08-12T10:00:00.000Z",
    "expires_at_utc": "2026-08-12T10:01:00.000Z",
}

INNER_STATE = {
    "schema_version": 1,
    "source_life_snapshot_revision": 8,
    "identity_id": "identity-1",
    "instance_id": "instance-1",
    "generated_at_utc": "2026-08-12T10:00:00.000Z",
    "phase": "engaged",
    "attention": ATTENTION,
    "homeostasis": HOMEOSTASIS,
    "affects": [AFFECT],
    "presence": PRESENCE,
    "last_observation_id": "observation-1",
    "degraded": False,
}

PLAYBACK = {
    "schema_version": 1,
    "playback_id": "playback-1",
    "generation": 3,
    "runtime_boot_id": "boot-1",
    "session_id": "session-1",
    "request_id": "request-1",
    "outcome": "started",
    "occurred_at_utc": "2026-08-12T10:00:01.000Z",
    "reason_code": "playback_started",
}

RECEIPT = {
    "schema_version": 1,
    "receipt_id": "receipt-1",
    "session_id": "session-1",
    "request_id": "request-1",
    "input_provenance": PROVENANCE,
    "source_boot_id": "boot-1",
    "first_event_id": "event-1",
    "first_event_sequence": 7,
    "first_event_sequence_domain": "conversation.session-1",
    "started_at_utc": "2026-08-12T10:00:00.000Z",
    "ended_at_utc": "2026-08-12T10:00:03.000Z",
    "outcome": "completed",
    "terminal_event_id": "event-3",
    "terminal_event_sequence": 9,
    "terminal_event_sequence_domain": "conversation.session-1",
    "recovery_event_id": None,
    "recovered_at_utc": None,
    "activity_kinds": ["understanding", "responding"],
    "tool_count": 0,
    "approval_outcome": "none",
    "interruption_count": 0,
    "goal_verified": False,
    "model_route": "local.default",
    "response_path": "model",
    "completeness": "complete",
    "privacy_class": "local_internal",
    "retention_class": "operational",
}
RECEIPT["content_hash"] = canonical_content_hash(RECEIPT)


CONTRACT_CASES = (
    (InputProvenance, PROVENANCE),
    (LifeObservation, OBSERVATION),
    (AttentionClaim, CLAIM),
    (AttentionSnapshot, ATTENTION),
    (AffectEvidence, EVIDENCE),
    (AppraisalResult, APPRAISAL),
    (HomeostasisSnapshot, HOMEOSTASIS),
    (FunctionalAffect, AFFECT),
    (PresenceSnapshot, PRESENCE),
    (InnerStateSnapshot, INNER_STATE),
    (PlaybackLifecycleEvent, PLAYBACK),
    (TurnExperienceReceipt, RECEIPT),
)


def test_l1_package_exports_only_the_frozen_contract_surface():
    expected = {contract.__name__ for contract, _ in CONTRACT_CASES}
    assert set(l1.__all__) == expected
    assert all(getattr(l1, name).__name__ == name for name in expected)


@pytest.mark.parametrize(("contract", "wire"), CONTRACT_CASES)
def test_contracts_round_trip_with_frozen_nested_values(contract, wire):
    value = contract.from_dict(copy.deepcopy(wire))
    assert dataclasses.is_dataclass(value)
    assert value.to_dict() == wire
    assert contract.from_dict(value.to_dict()) == value
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.schema_version = 2
    if contract is AppraisalResult:
        assert isinstance(value.deltas, MappingProxyType)
        with pytest.raises(TypeError):
            value.deltas["activation"] = 1.0


@pytest.mark.parametrize(("contract", "wire"), CONTRACT_CASES)
def test_contracts_reject_missing_and_extra_fields(contract, wire):
    missing = copy.deepcopy(wire)
    missing.pop(next(iter(missing)))
    with pytest.raises(ValueError, match="missing field"):
        contract.from_dict(missing)
    extra = copy.deepcopy(wire)
    extra["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected field"):
        contract.from_dict(extra)


def test_l1_contract_field_names_are_exact():
    assert tuple(field.name for field in dataclasses.fields(LifeObservation)) == tuple(OBSERVATION)
    assert tuple(field.name for field in dataclasses.fields(AppraisalResult)) == tuple(APPRAISAL)
    assert tuple(field.name for field in dataclasses.fields(InnerStateSnapshot)) == tuple(INNER_STATE)
    assert tuple(field.name for field in dataclasses.fields(TurnExperienceReceipt)) == tuple(RECEIPT)


@pytest.mark.parametrize("bad_timestamp", ["2026-08-12T10:00:00Z", "not-time", "2026-13-40T10:00:00.000Z"])
def test_timestamps_require_real_utc_milliseconds(bad_timestamp):
    wire = copy.deepcopy(OBSERVATION)
    wire["occurred_at_utc"] = bad_timestamp
    with pytest.raises(ValueError, match="millisecond|timestamp"):
        LifeObservation.from_dict(wire)


@pytest.mark.parametrize("field", ["activation", "cognitive_load", "certainty", "caution", "curiosity", "blockedness", "social_presence"])
def test_homeostasis_values_are_finite_units(field):
    for invalid in (-0.01, 1.01, float("inf"), float("nan"), True):
        wire = copy.deepcopy(HOMEOSTASIS)
        wire[field] = invalid
        with pytest.raises(ValueError, match=field):
            HomeostasisSnapshot.from_dict(wire)


def test_appraisal_deltas_are_known_finite_signed_dimensions():
    for deltas in ({"mood": 0.2}, {"activation": 1.1}, {"activation": float("nan")}):
        wire = copy.deepcopy(APPRAISAL)
        wire["deltas"] = deltas
        with pytest.raises(ValueError, match="deltas"):
            AppraisalResult.from_dict(wire)


@pytest.mark.parametrize("kind", ["happy", "sad", "surprised", "familiar"])
def test_functional_affect_rejects_unfounded_emotion_labels(kind):
    wire = copy.deepcopy(AFFECT)
    wire["kind"] = kind
    with pytest.raises(ValueError, match="kind"):
        FunctionalAffect.from_dict(wire)


@pytest.mark.parametrize("forbidden", ["text", "transcript", "audio", "tool_result", "api_key", "token", "hidden_reasoning"])
def test_recursive_forbidden_field_scan_runs_before_nested_coercion(forbidden):
    wire = copy.deepcopy(APPRAISAL)
    wire["deltas"] = {"activation": {forbidden: "private"}}
    with pytest.raises(ValueError, match="forbidden field"):
        AppraisalResult.from_dict(wire)


def test_receipt_hash_excludes_itself_and_detects_tampering():
    receipt = TurnExperienceReceipt.from_dict(copy.deepcopy(RECEIPT))
    assert receipt.verify_hash()
    tampered = receipt.to_dict()
    tampered["tool_count"] = 1
    with pytest.raises(ValueError, match="content_hash"):
        TurnExperienceReceipt.from_dict(tampered)


def test_receipt_never_carries_turn_content_and_restart_cannot_forge_terminal_event():
    assert not ({"text", "transcript", "audio", "response", "tool_result"} & set(RECEIPT))
    interrupted = copy.deepcopy(RECEIPT)
    interrupted.update({
        "outcome": "interrupted",
        "terminal_event_id": None,
        "terminal_event_sequence": None,
        "terminal_event_sequence_domain": None,
        "recovery_event_id": "recovery-1",
        "recovered_at_utc": "2026-08-12T10:00:04.000Z",
        "completeness": "interrupted_by_restart",
    })
    interrupted.pop("content_hash")
    interrupted["content_hash"] = canonical_content_hash(interrupted)
    assert TurnExperienceReceipt.from_dict(interrupted).outcome.value == "interrupted"

    forged = copy.deepcopy(interrupted)
    forged["terminal_event_id"] = "fabricated-cancel"
    forged.pop("content_hash")
    forged["content_hash"] = canonical_content_hash(forged)
    with pytest.raises(ValueError, match="terminal"):
        TurnExperienceReceipt.from_dict(forged)
