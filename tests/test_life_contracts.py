from __future__ import annotations

import copy
import dataclasses
import json
from collections.abc import Mapping

import pytest

import core.life as life
from core.life.contracts import (
    ContinuityCheckpoint,
    ExpressionBaseState,
    ExpressionIntent,
    GazeTarget,
    HealthSummary,
    IdentityConstitution,
    IdentitySummary,
    InstanceRecord,
    InstanceSummary,
    LifeCycleState,
    LifeEvent,
    LifeSnapshot,
    PrivacyClass,
    RetentionClass,
    StartupAssessment,
    VoiceActivity,
    canonical_content_hash,
    canonical_json_bytes,
)


IDENTITY_WIRE = {
    "schema_version": 1,
    "identity_id": "identity-1",
    "name": "Javis",
    "kind": "local_digital_life_partner",
    "relationship_role": "partner",
    "persona_invariants": ["quiet", "focused"],
    "values": ["honesty", "continuity"],
    "hard_boundaries": ["no_secret_storage"],
    "created_at": "1970-01-01T00:01:40.000Z",
    "version": 1,
    "previous_version_hash": None,
    "content_hash": "0638f5edd6db9bd3244c4c24a41e32b7a2114cd8c2ec8bfc409568f5650b0771",
    "approved_by": "built_in_constitution",
    "approved_at": "1970-01-01T00:01:40.000Z",
}

INSTANCE_WIRE = {
    "schema_version": 1,
    "identity_id": "identity-1",
    "lineage_id": "lineage-1",
    "instance_id": "instance-1",
    "parent_instance_id": "instance-parent",
    "generation": 1,
    "environment_fingerprint_hash": "a" * 64,
    "created_at": "1970-01-01T00:01:40.000Z",
    "last_started_at": "1970-01-01T00:01:41.000Z",
    "last_clean_shutdown_at": "1970-01-01T00:01:42.000Z",
    "fork_pending_review": True,
}

CHECKPOINT_WIRE = {
    "schema_version": 1,
    "instance_id": "instance-1",
    "boot_id": "boot-1",
    "started_at": "1970-01-01T00:01:40.000Z",
    "clean_shutdown_at": "1970-01-01T00:01:45.000Z",
    "last_event_cursor": 9,
    "active_request_id": "request-1",
    "temporary_authority_valid": False,
    "content_hash": "35bed9cf970795e5b50e66fca307e7677856a8a0e51a79f91144f09a1dece143",
}

ASSESSMENT_WIRE = {
    "schema_version": 1,
    "instance_id": "instance-1",
    "previous_boot_id": "boot-1",
    "unclean_shutdown": True,
    "temporary_authority_valid": False,
    "last_event_cursor": 9,
    "active_request_id": "request-1",
    "reason_code": "unclean_shutdown",
}

EVENT_WIRE = {
    "schema_version": 1,
    "event_id": "event-1",
    "event_type": "request.started",
    "timestamp_utc": "1970-01-01T00:01:40.000Z",
    "monotonic_offset_ms": 250,
    "source": "conversation_hub",
    "source_event_id": "source-event-1",
    "session_id": "session-1",
    "request_id": "request-1",
    "correlation_id": "correlation-1",
    "causation_id": "causation-1",
    "sequence": 7,
    "identity_id": "identity-1",
    "instance_id": "instance-1",
    "payload": {
        "message": "hello",
        "steps": [{"name": "listen", "complete": True}],
    },
    "privacy_class": "user_private",
    "retention_class": "session",
    "confidence": 0.75,
    "provenance": {"adapter": "conversation", "versions": [1, 2]},
    "redaction_summary": ["authorization_header"],
}

IDENTITY_SUMMARY_WIRE = {
    "identity_id": "identity-1",
    "name": "Javis",
    "kind": "local_digital_life_partner",
    "relationship_role": "partner",
    "version": 1,
    "content_hash": IDENTITY_WIRE["content_hash"],
}

INSTANCE_SUMMARY_WIRE = {
    "lineage_id": "lineage-1",
    "instance_id": "instance-1",
    "parent_instance_id": "instance-parent",
    "generation": 1,
    "fork_pending_review": True,
}

HEALTH_WIRE = {
    "status": "degraded",
    "degraded_components": ["journal"],
    "reason_codes": ["journal_unavailable"],
}

SNAPSHOT_WIRE = {
    "schema_version": 1,
    "revision": 4,
    "identity": IDENTITY_SUMMARY_WIRE,
    "instance": INSTANCE_SUMMARY_WIRE,
    "lifecycle_state": "degraded",
    "active_session_id": "session-1",
    "active_request_id": "request-1",
    "activity": "recovering journal",
    "health": HEALTH_WIRE,
    "degradation_level": 1,
    "recovery_required": True,
    "last_event_id": "event-1",
    "last_sequence": 7,
    "updated_at": "1970-01-01T00:01:40.000Z",
    "explanation": "journal health observation",
}

EXPRESSION_WIRE = {
    "schema_version": 1,
    "revision": 4,
    "base_state": "idle",
    "intensity": 0.2,
    "gaze_target": "none",
    "voice_activity": "silent",
    "transition_ms": 180,
    "interrupt": False,
    "source_snapshot_revision": 4,
    "generated_at": "1970-01-01T00:01:41.000Z",
    "expires_at": "1970-01-01T00:01:46.000Z",
    "explanation_code": "quiet",
}


CONTRACT_CASES = (
    (IdentityConstitution, IDENTITY_WIRE),
    (InstanceRecord, INSTANCE_WIRE),
    (ContinuityCheckpoint, CHECKPOINT_WIRE),
    (StartupAssessment, ASSESSMENT_WIRE),
    (LifeEvent, EVENT_WIRE),
    (IdentitySummary, IDENTITY_SUMMARY_WIRE),
    (InstanceSummary, INSTANCE_SUMMARY_WIRE),
    (HealthSummary, HEALTH_WIRE),
    (LifeSnapshot, SNAPSHOT_WIRE),
    (ExpressionIntent, EXPRESSION_WIRE),
)

SCHEMA_CASES = tuple(
    (contract, wire)
    for contract, wire in CONTRACT_CASES
    if "schema_version" in wire
)


def _copy_wire(wire: dict) -> dict:
    return copy.deepcopy(wire)


def _assert_fresh_containers(first, second) -> None:
    if isinstance(first, dict):
        assert first is not second
        assert first.keys() == second.keys()
        for key in first:
            _assert_fresh_containers(first[key], second[key])
    elif isinstance(first, list):
        assert first is not second
        assert len(first) == len(second)
        for left, right in zip(first, second):
            _assert_fresh_containers(left, right)


def test_package_exports_the_frozen_contract_surface():
    expected = {
        "LifeCycleState",
        "PrivacyClass",
        "RetentionClass",
        "ExpressionBaseState",
        "GazeTarget",
        "VoiceActivity",
        "IdentityConstitution",
        "InstanceRecord",
        "ContinuityCheckpoint",
        "StartupAssessment",
        "LifeEvent",
        "IdentitySummary",
        "InstanceSummary",
        "HealthSummary",
        "LifeSnapshot",
        "ExpressionIntent",
        "canonical_json_bytes",
        "canonical_content_hash",
    }
    assert set(life.__all__) == expected
    for name in expected:
        assert getattr(life, name) is globals()[name]


@pytest.mark.parametrize(
    ("enum_type", "expected"),
    (
        (
            LifeCycleState,
            {
                "booting",
                "awake",
                "quiet",
                "engaged",
                "degraded",
                "recovering",
                "stopping",
                "offline",
            },
        ),
        (
            PrivacyClass,
            {
                "public_surface",
                "local_internal",
                "user_private",
                "secret",
                "biometric",
                "restricted_system",
            },
        ),
        (
            RetentionClass,
            {
                "ephemeral",
                "session",
                "operational",
                "continuity",
                "memory_candidate",
                "audit",
                "never_persist",
            },
        ),
        (
            ExpressionBaseState,
            {
                "idle",
                "attention",
                "listening",
                "thinking",
                "speaking",
                "executing",
                "blocked",
                "error",
                "offline",
            },
        ),
        (GazeTarget, {"none", "user", "content", "task"}),
        (VoiceActivity, {"silent", "listening", "speaking"}),
    ),
)
def test_enum_wire_values_are_exact(enum_type, expected):
    assert {item.value for item in enum_type} == expected
    assert all(isinstance(item, str) for item in enum_type)


def test_canonical_json_and_hash_are_utf8_sorted_compact_and_newline_free():
    payload = {"b": 1, "a": "贾维斯"}

    assert canonical_json_bytes(payload) == '{"a":"贾维斯","b":1}'.encode("utf-8")
    assert canonical_json_bytes(payload).endswith(b"}")
    assert b"\n" not in canonical_json_bytes(payload)
    assert canonical_content_hash(payload) == (
        "e6362375790e9ed55a0541d5cc1b62e2ef45366c5ff17b0d49620f819e96bb06"
    )


def test_all_public_contract_field_names_are_frozen():
    expected = {
        IdentityConstitution: (
            "schema_version",
            "identity_id",
            "name",
            "kind",
            "relationship_role",
            "persona_invariants",
            "values",
            "hard_boundaries",
            "created_at",
            "version",
            "previous_version_hash",
            "content_hash",
            "approved_by",
            "approved_at",
        ),
        InstanceRecord: (
            "schema_version",
            "identity_id",
            "lineage_id",
            "instance_id",
            "parent_instance_id",
            "generation",
            "environment_fingerprint_hash",
            "created_at",
            "last_started_at",
            "last_clean_shutdown_at",
            "fork_pending_review",
        ),
        ContinuityCheckpoint: (
            "schema_version",
            "instance_id",
            "boot_id",
            "started_at",
            "clean_shutdown_at",
            "last_event_cursor",
            "active_request_id",
            "temporary_authority_valid",
            "content_hash",
        ),
        StartupAssessment: (
            "schema_version",
            "instance_id",
            "previous_boot_id",
            "unclean_shutdown",
            "temporary_authority_valid",
            "last_event_cursor",
            "active_request_id",
            "reason_code",
        ),
        LifeEvent: (
            "schema_version",
            "event_id",
            "event_type",
            "timestamp_utc",
            "monotonic_offset_ms",
            "source",
            "source_event_id",
            "session_id",
            "request_id",
            "correlation_id",
            "causation_id",
            "sequence",
            "identity_id",
            "instance_id",
            "payload",
            "privacy_class",
            "retention_class",
            "confidence",
            "provenance",
            "redaction_summary",
        ),
        IdentitySummary: (
            "identity_id",
            "name",
            "kind",
            "relationship_role",
            "version",
            "content_hash",
        ),
        InstanceSummary: (
            "lineage_id",
            "instance_id",
            "parent_instance_id",
            "generation",
            "fork_pending_review",
        ),
        HealthSummary: ("status", "degraded_components", "reason_codes"),
        LifeSnapshot: (
            "schema_version",
            "revision",
            "identity",
            "instance",
            "lifecycle_state",
            "active_session_id",
            "active_request_id",
            "activity",
            "health",
            "degradation_level",
            "recovery_required",
            "last_event_id",
            "last_sequence",
            "updated_at",
            "explanation",
        ),
        ExpressionIntent: (
            "schema_version",
            "revision",
            "base_state",
            "intensity",
            "gaze_target",
            "voice_activity",
            "transition_ms",
            "interrupt",
            "source_snapshot_revision",
            "generated_at",
            "expires_at",
            "explanation_code",
        ),
    }

    for contract, field_names in expected.items():
        assert tuple(field.name for field in dataclasses.fields(contract)) == field_names


@pytest.mark.parametrize(
    ("contract", "wire"), CONTRACT_CASES, ids=lambda value: getattr(value, "__name__", "wire")
)
def test_each_contract_has_exact_wire_keys_and_strict_round_trip(contract, wire):
    instance = contract.from_dict(_copy_wire(wire))

    assert set(instance.to_dict()) == set(wire)
    assert instance.to_dict() == wire
    assert contract.from_dict(instance.to_dict()) == instance
    json.dumps(instance.to_dict(), ensure_ascii=False)


@pytest.mark.parametrize(
    ("contract", "wire"), CONTRACT_CASES, ids=lambda value: getattr(value, "__name__", "wire")
)
def test_each_contract_rejects_missing_and_extra_wire_fields(contract, wire):
    missing = _copy_wire(wire)
    missing.pop(next(iter(missing)))
    with pytest.raises(ValueError, match="missing field"):
        contract.from_dict(missing)

    extra = _copy_wire(wire)
    extra["unexpected"] = "not allowed"
    with pytest.raises(ValueError, match="unexpected field"):
        contract.from_dict(extra)


@pytest.mark.parametrize(
    ("contract", "wire"), SCHEMA_CASES, ids=lambda value: getattr(value, "__name__", "wire")
)
def test_each_versioned_contract_rejects_unknown_schema_versions(contract, wire):
    invalid = _copy_wire(wire)
    invalid["schema_version"] = 2

    with pytest.raises(ValueError, match="schema_version"):
        contract.from_dict(invalid)


@pytest.mark.parametrize(
    ("contract", "wire"), CONTRACT_CASES, ids=lambda value: getattr(value, "__name__", "wire")
)
def test_each_contract_is_frozen_and_to_dict_deeply_reallocates(contract, wire):
    instance = contract.from_dict(_copy_wire(wire))
    field_name = dataclasses.fields(contract)[0].name

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, None)

    _assert_fresh_containers(instance.to_dict(), instance.to_dict())


@pytest.mark.parametrize(
    ("contract", "wire", "field"),
    (
        (LifeEvent, EVENT_WIRE, "privacy_class"),
        (LifeEvent, EVENT_WIRE, "retention_class"),
        (LifeSnapshot, SNAPSHOT_WIRE, "lifecycle_state"),
        (ExpressionIntent, EXPRESSION_WIRE, "base_state"),
        (ExpressionIntent, EXPRESSION_WIRE, "gaze_target"),
        (ExpressionIntent, EXPRESSION_WIRE, "voice_activity"),
    ),
)
def test_all_enum_fields_reject_unknown_wire_values(contract, wire, field):
    invalid = _copy_wire(wire)
    invalid[field] = "future_unknown_value"

    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


def test_default_constitution_is_javis_model_independent_and_epoch_normalized():
    identity = IdentityConstitution.create_default(identity_id="identity-贾维斯", now=100.0)
    data = identity.to_dict()

    assert data["name"] == "Javis"
    assert data["identity_id"] == "identity-贾维斯"
    assert data["created_at"] == "1970-01-01T00:01:40.000Z"
    assert data["approved_at"] == "1970-01-01T00:01:40.000Z"
    assert data["version"] == 1
    assert data["previous_version_hash"] is None
    assert data["approved_by"] == "built_in_constitution"
    assert identity.persona_invariants
    assert identity.values
    assert identity.hard_boundaries
    assert "model" not in data
    assert "provider" not in data
    assert "api_key" not in data
    assert identity.verify_hash()


def test_hash_bearing_contracts_reject_tampering_and_verify_canonical_content():
    identity = IdentityConstitution.from_dict(_copy_wire(IDENTITY_WIRE))
    checkpoint = ContinuityCheckpoint.from_dict(_copy_wire(CHECKPOINT_WIRE))
    assert identity.verify_hash()
    assert checkpoint.verify_hash()

    tampered_identity = _copy_wire(IDENTITY_WIRE)
    tampered_identity["name"] = "Not Javis"
    with pytest.raises(ValueError, match="content_hash"):
        IdentityConstitution.from_dict(tampered_identity)

    tampered_checkpoint = _copy_wire(CHECKPOINT_WIRE)
    tampered_checkpoint["last_event_cursor"] += 1
    with pytest.raises(ValueError, match="content_hash"):
        ContinuityCheckpoint.from_dict(tampered_checkpoint)


def test_collection_fields_and_nested_json_are_recursively_immutable():
    incoming_identity = _copy_wire(IDENTITY_WIRE)
    identity = IdentityConstitution.from_dict(incoming_identity)
    incoming_identity["persona_invariants"].append("outside mutation")
    assert identity.persona_invariants == ("quiet", "focused")
    assert isinstance(identity.values, tuple)
    assert isinstance(identity.hard_boundaries, tuple)

    incoming_event = _copy_wire(EVENT_WIRE)
    event = LifeEvent.from_dict(incoming_event)
    incoming_event["payload"]["steps"][0]["name"] = "outside mutation"
    assert isinstance(event.payload, Mapping)
    assert isinstance(event.payload["steps"], tuple)
    assert isinstance(event.payload["steps"][0], Mapping)
    assert event.payload["steps"][0]["name"] == "listen"
    assert isinstance(event.provenance["versions"], tuple)
    assert isinstance(event.redaction_summary, tuple)
    with pytest.raises(TypeError):
        event.payload["message"] = "mutated"
    with pytest.raises(TypeError):
        event.payload["steps"][0]["name"] = "mutated"

    health = HealthSummary.from_dict(_copy_wire(HEALTH_WIRE))
    assert isinstance(health.degraded_components, tuple)
    assert isinstance(health.reason_codes, tuple)


def test_to_dict_mutation_never_changes_contract_state_or_later_outputs():
    event = LifeEvent.from_dict(_copy_wire(EVENT_WIRE))
    first_event_wire = event.to_dict()
    first_event_wire["payload"]["steps"][0]["name"] = "outside mutation"
    first_event_wire["redaction_summary"].append("outside mutation")
    assert event.to_dict() == EVENT_WIRE

    snapshot = LifeSnapshot.from_dict(_copy_wire(SNAPSHOT_WIRE))
    first_snapshot_wire = snapshot.to_dict()
    first_snapshot_wire["identity"]["name"] = "outside mutation"
    first_snapshot_wire["health"]["reason_codes"].append("outside mutation")
    assert snapshot.to_dict() == SNAPSHOT_WIRE


@pytest.mark.parametrize(
    ("contract", "wire", "field"),
    (
        (IdentityConstitution, IDENTITY_WIRE, "created_at"),
        (IdentityConstitution, IDENTITY_WIRE, "approved_at"),
        (InstanceRecord, INSTANCE_WIRE, "created_at"),
        (InstanceRecord, INSTANCE_WIRE, "last_started_at"),
        (InstanceRecord, INSTANCE_WIRE, "last_clean_shutdown_at"),
        (ContinuityCheckpoint, CHECKPOINT_WIRE, "started_at"),
        (ContinuityCheckpoint, CHECKPOINT_WIRE, "clean_shutdown_at"),
        (LifeEvent, EVENT_WIRE, "timestamp_utc"),
        (LifeSnapshot, SNAPSHOT_WIRE, "updated_at"),
        (ExpressionIntent, EXPRESSION_WIRE, "generated_at"),
        (ExpressionIntent, EXPRESSION_WIRE, "expires_at"),
    ),
)
@pytest.mark.parametrize(
    "invalid_timestamp",
    (
        "1970-01-01T00:00:01Z",
        "1970-01-01T00:00:01.000+00:00",
        "1970-01-01T00:00:01.000000Z",
        "1970-13-01T00:00:01.000Z",
        1.0,
    ),
)
def test_every_timestamp_field_requires_valid_rfc3339_utc_milliseconds(
    contract, wire, field, invalid_timestamp
):
    invalid = _copy_wire(wire)
    invalid[field] = invalid_timestamp

    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


@pytest.mark.parametrize(
    ("contract", "wire", "field"),
    (
        (IdentityConstitution, IDENTITY_WIRE, "identity_id"),
        (InstanceRecord, INSTANCE_WIRE, "identity_id"),
        (InstanceRecord, INSTANCE_WIRE, "lineage_id"),
        (InstanceRecord, INSTANCE_WIRE, "instance_id"),
        (InstanceRecord, INSTANCE_WIRE, "parent_instance_id"),
        (ContinuityCheckpoint, CHECKPOINT_WIRE, "instance_id"),
        (ContinuityCheckpoint, CHECKPOINT_WIRE, "boot_id"),
        (ContinuityCheckpoint, CHECKPOINT_WIRE, "active_request_id"),
        (StartupAssessment, ASSESSMENT_WIRE, "instance_id"),
        (StartupAssessment, ASSESSMENT_WIRE, "previous_boot_id"),
        (StartupAssessment, ASSESSMENT_WIRE, "active_request_id"),
        (LifeEvent, EVENT_WIRE, "event_id"),
        (LifeEvent, EVENT_WIRE, "source_event_id"),
        (LifeEvent, EVENT_WIRE, "session_id"),
        (LifeEvent, EVENT_WIRE, "request_id"),
        (LifeEvent, EVENT_WIRE, "correlation_id"),
        (LifeEvent, EVENT_WIRE, "causation_id"),
        (LifeEvent, EVENT_WIRE, "identity_id"),
        (LifeEvent, EVENT_WIRE, "instance_id"),
        (IdentitySummary, IDENTITY_SUMMARY_WIRE, "identity_id"),
        (InstanceSummary, INSTANCE_SUMMARY_WIRE, "lineage_id"),
        (InstanceSummary, INSTANCE_SUMMARY_WIRE, "instance_id"),
        (InstanceSummary, INSTANCE_SUMMARY_WIRE, "parent_instance_id"),
        (LifeSnapshot, SNAPSHOT_WIRE, "active_session_id"),
        (LifeSnapshot, SNAPSHOT_WIRE, "active_request_id"),
        (LifeSnapshot, SNAPSHOT_WIRE, "last_event_id"),
    ),
)
@pytest.mark.parametrize("invalid_id", ("", "line\nbreak", "x" * 257))
def test_every_id_field_rejects_empty_control_or_overlong_values(
    contract, wire, field, invalid_id
):
    invalid = _copy_wire(wire)
    invalid[field] = invalid_id

    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


@pytest.mark.parametrize(
    ("contract", "wire", "field"),
    (
        (IdentityConstitution, IDENTITY_WIRE, "version"),
        (InstanceRecord, INSTANCE_WIRE, "generation"),
        (ContinuityCheckpoint, CHECKPOINT_WIRE, "last_event_cursor"),
        (StartupAssessment, ASSESSMENT_WIRE, "last_event_cursor"),
        (LifeEvent, EVENT_WIRE, "monotonic_offset_ms"),
        (LifeEvent, EVENT_WIRE, "sequence"),
        (IdentitySummary, IDENTITY_SUMMARY_WIRE, "version"),
        (InstanceSummary, INSTANCE_SUMMARY_WIRE, "generation"),
        (LifeSnapshot, SNAPSHOT_WIRE, "revision"),
        (LifeSnapshot, SNAPSHOT_WIRE, "degradation_level"),
        (LifeSnapshot, SNAPSHOT_WIRE, "last_sequence"),
        (ExpressionIntent, EXPRESSION_WIRE, "revision"),
        (ExpressionIntent, EXPRESSION_WIRE, "transition_ms"),
        (ExpressionIntent, EXPRESSION_WIRE, "source_snapshot_revision"),
    ),
)
@pytest.mark.parametrize("invalid_value", (-1, True))
def test_counter_fields_are_non_negative_integers(contract, wire, field, invalid_value):
    invalid = _copy_wire(wire)
    invalid[field] = invalid_value

    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


@pytest.mark.parametrize(
    ("contract", "wire", "field"),
    (
        (LifeEvent, EVENT_WIRE, "confidence"),
        (ExpressionIntent, EXPRESSION_WIRE, "intensity"),
    ),
)
@pytest.mark.parametrize("invalid_value", (-0.1, 1.1, float("nan"), float("inf"), True))
def test_unit_interval_fields_reject_out_of_range_or_non_finite_values(
    contract, wire, field, invalid_value
):
    invalid = _copy_wire(wire)
    invalid[field] = invalid_value

    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


@pytest.mark.parametrize(
    ("contract", "wire", "field"),
    (
        (InstanceRecord, INSTANCE_WIRE, "fork_pending_review"),
        (ContinuityCheckpoint, CHECKPOINT_WIRE, "temporary_authority_valid"),
        (StartupAssessment, ASSESSMENT_WIRE, "unclean_shutdown"),
        (StartupAssessment, ASSESSMENT_WIRE, "temporary_authority_valid"),
        (InstanceSummary, INSTANCE_SUMMARY_WIRE, "fork_pending_review"),
        (LifeSnapshot, SNAPSHOT_WIRE, "recovery_required"),
        (ExpressionIntent, EXPRESSION_WIRE, "interrupt"),
    ),
)
def test_boolean_fields_reject_integer_substitutes(contract, wire, field):
    invalid = _copy_wire(wire)
    invalid[field] = 0

    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


@pytest.mark.parametrize(
    "retention_class",
    [item.value for item in RetentionClass if item is not RetentionClass.NEVER_PERSIST],
)
def test_secret_events_must_never_persist(retention_class):
    invalid = _copy_wire(EVENT_WIRE)
    invalid["privacy_class"] = "secret"
    invalid["retention_class"] = retention_class

    with pytest.raises(ValueError, match="never_persist"):
        LifeEvent.from_dict(invalid)


def test_secret_event_with_never_persist_is_valid_without_invented_exceptions():
    wire = _copy_wire(EVENT_WIRE)
    wire["privacy_class"] = "secret"
    wire["retention_class"] = "never_persist"

    assert LifeEvent.from_dict(wire).to_dict() == wire


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        ("payload", {1: "non-string key"}),
        ("payload", {"not_json": object()}),
        ("payload", {"not_finite": float("nan")}),
        ("provenance", {"not_finite": float("inf")}),
        ("payload", ["not", "an", "object"]),
    ),
)
def test_event_json_objects_reject_non_json_or_non_object_values(field, invalid_value):
    invalid = _copy_wire(EVENT_WIRE)
    invalid[field] = invalid_value

    with pytest.raises(ValueError, match=field):
        LifeEvent.from_dict(invalid)


@pytest.mark.parametrize(
    ("contract", "wire", "field"),
    (
        (IdentityConstitution, IDENTITY_WIRE, "persona_invariants"),
        (IdentityConstitution, IDENTITY_WIRE, "values"),
        (IdentityConstitution, IDENTITY_WIRE, "hard_boundaries"),
        (LifeEvent, EVENT_WIRE, "redaction_summary"),
        (HealthSummary, HEALTH_WIRE, "degraded_components"),
        (HealthSummary, HEALTH_WIRE, "reason_codes"),
    ),
)
def test_string_collection_fields_reject_non_string_items(contract, wire, field):
    invalid = _copy_wire(wire)
    invalid[field] = [1]

    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


@pytest.mark.parametrize(
    ("contract", "wire", "field"),
    (
        (IdentityConstitution, IDENTITY_WIRE, "content_hash"),
        (IdentityConstitution, IDENTITY_WIRE, "previous_version_hash"),
        (InstanceRecord, INSTANCE_WIRE, "environment_fingerprint_hash"),
        (ContinuityCheckpoint, CHECKPOINT_WIRE, "content_hash"),
        (IdentitySummary, IDENTITY_SUMMARY_WIRE, "content_hash"),
    ),
)
def test_hash_fields_require_lowercase_sha256_hex(contract, wire, field):
    invalid = _copy_wire(wire)
    invalid[field] = "A" * 64

    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)
