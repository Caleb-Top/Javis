from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from types import MappingProxyType

import pytest

from core.environment.contracts import (
    MAX_ATTRIBUTE_ARRAY_ITEMS,
    MAX_ATTRIBUTES,
    MAX_ATTRIBUTES_BYTES,
    OCR_MAX_RETENTION_SECONDS,
    RAW_MEDIA_RETENTION_SECONDS,
    SOURCE_MAX_TTL_SECONDS,
    EnvironmentFact,
    EnvironmentObservationV1,
    EnvironmentSnapshotV1,
    EnvironmentStatus,
    PrivacyClass,
    RetentionClass,
    SourceHealth,
    SourceKind,
    canonical_hash,
    canonical_json_bytes,
    parse_environment_observation_wire,
    parse_environment_snapshot_wire,
    validate_observation_for_reducer,
)


NOW = "2026-08-20T10:00:00.000Z"
RECEIVED = "2026-08-20T10:00:00.500Z"
OBSERVATION_WIRE = {
    "schema_version": 1,
    "observation_id": "observation-1",
    "runtime_boot_id": "boot-1",
    "source_event_id": "window-event-7",
    "source_kind": "foreground_app",
    "subject_kind": "application",
    "subject_key": "editor",
    "attributes": {
        "app_category": "development",
        "foreground": True,
        "window_count": 2,
    },
    "observed_at_utc": NOW,
    "valid_until_utc": "2026-08-20T10:00:05.000Z",
    "sequence": 7,
    "confidence": 0.9,
    "privacy_class": "local_internal",
    "retention_class": "session",
    "grant_id_hash": "a" * 64,
    "provenance": {"adapter": "win32", "adapter_version": 1},
}


def observation_wire(**changes):
    wire = copy.deepcopy(OBSERVATION_WIRE)
    wire.update(changes)
    return wire


def snapshot_wire(observation: EnvironmentObservationV1 | None = None):
    value = observation or EnvironmentObservationV1.from_dict(
        copy.deepcopy(OBSERVATION_WIRE)
    )
    return {
        "schema_version": 1,
        "revision": 3,
        "runtime_boot_id": "boot-1",
        "generated_at_utc": "2026-08-20T10:00:01.000Z",
        "expires_at_utc": "2026-08-20T10:00:04.000Z",
        "facts": [fact.to_dict() for fact in value.to_facts()],
        "stale_keys": [["workspace", "project-alpha", "change_count"]],
        "source_health": {
            "foreground_app": "healthy",
            "workspace_metadata": "degraded",
        },
        "permission_revision": 2,
    }


def test_l4_enums_and_source_retention_policy_are_explicit():
    assert {item.value for item in SourceKind} == {
        "foreground_app",
        "process_health",
        "device_health",
        "workspace_metadata",
        "screen_ocr",
        "screen_object",
        "user_defined_alias",
        "raw_media",
    }
    assert {item.value for item in SourceHealth} == {
        "unknown",
        "healthy",
        "degraded",
        "unavailable",
        "disabled",
    }
    assert {item.value for item in EnvironmentStatus} == {
        "fresh",
        "stale",
        "unknown",
    }
    assert SOURCE_MAX_TTL_SECONDS[SourceKind.RAW_MEDIA] == RAW_MEDIA_RETENTION_SECONDS == 0
    assert SOURCE_MAX_TTL_SECONDS[SourceKind.SCREEN_OCR] == OCR_MAX_RETENTION_SECONDS == 10


@pytest.mark.parametrize(
    ("contract", "wire"),
    (
        (EnvironmentObservationV1, OBSERVATION_WIRE),
        (
            EnvironmentFact,
            EnvironmentObservationV1.from_dict(OBSERVATION_WIRE).to_facts()[0].to_dict(),
        ),
        (EnvironmentSnapshotV1, snapshot_wire()),
    ),
)
def test_wire_contracts_reject_unknown_and_missing_fields(contract, wire):
    unexpected = copy.deepcopy(wire)
    unexpected["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected field"):
        contract.from_dict(unexpected)

    missing = copy.deepcopy(wire)
    missing.pop(next(iter(wire)))
    with pytest.raises(ValueError, match="missing field"):
        contract.from_dict(missing)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("observed_at_utc", "2026-08-20T10:00:00Z"),
        ("observed_at_utc", "2026-08-20T10:00:00.000+00:00"),
        ("observed_at_utc", "2026-02-30T10:00:00.000Z"),
        ("valid_until_utc", "2026-08-20T09:59:59.000Z"),
    ),
)
def test_noncanonical_invalid_and_reversed_times_are_rejected(field, bad_value):
    with pytest.raises(ValueError, match=field):
        EnvironmentObservationV1.from_dict(observation_wire(**{field: bad_value}))


def test_future_and_expired_wire_input_fail_closed_against_explicit_clock():
    future = observation_wire(
        observed_at_utc="2026-08-20T10:00:06.000Z",
        valid_until_utc="2026-08-20T10:00:11.000Z",
    )
    with pytest.raises(ValueError, match="future"):
        parse_environment_observation_wire(future, received_at_utc=NOW)

    with pytest.raises(ValueError, match="expired"):
        parse_environment_observation_wire(
            OBSERVATION_WIRE,
            received_at_utc="2026-08-20T10:00:05.000Z",
        )


def test_attribute_count_array_count_and_total_bytes_are_bounded():
    too_many = {f"field_{index}": index for index in range(MAX_ATTRIBUTES + 1)}
    with pytest.raises(ValueError, match="at most"):
        EnvironmentObservationV1.from_dict(observation_wire(attributes=too_many))

    oversized_array = list(range(MAX_ATTRIBUTE_ARRAY_ITEMS + 1))
    with pytest.raises(ValueError, match="at most"):
        EnvironmentObservationV1.from_dict(
            observation_wire(attributes={"levels": oversized_array})
        )

    oversized_bytes = {
        f"field_{index}": "x" * 256 for index in range(MAX_ATTRIBUTES)
    }
    assert len(
        json.dumps(oversized_bytes, sort_keys=True, separators=(",", ":")).encode()
    ) > MAX_ATTRIBUTES_BYTES
    with pytest.raises(ValueError, match="canonical JSON"):
        EnvironmentObservationV1.from_dict(
            observation_wire(attributes=oversized_bytes)
        )


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("observation_id", "observation\x00bad"),
        ("subject_key", "editor\nprivate"),
    ),
)
def test_control_characters_are_rejected_everywhere(field, bad_value):
    with pytest.raises(ValueError, match=field):
        EnvironmentObservationV1.from_dict(observation_wire(**{field: bad_value}))

    with pytest.raises(ValueError, match="control"):
        EnvironmentObservationV1.from_dict(
            observation_wire(attributes={"app_category": "dev\tprivate"})
        )


def test_reducer_validation_rejects_wrong_boot_rollback_and_duplicate_ids():
    observation = EnvironmentObservationV1.from_dict(OBSERVATION_WIRE)
    with pytest.raises(ValueError, match="runtime_boot_id"):
        validate_observation_for_reducer(
            observation,
            expected_runtime_boot_id="boot-2",
            previous_sequence=6,
            received_at_utc=RECEIVED,
        )
    for previous_sequence in (7, 8):
        with pytest.raises(ValueError, match="sequence"):
            validate_observation_for_reducer(
                observation,
                expected_runtime_boot_id="boot-1",
                previous_sequence=previous_sequence,
                received_at_utc=RECEIVED,
            )
    with pytest.raises(ValueError, match="duplicate observation"):
        validate_observation_for_reducer(
            observation,
            expected_runtime_boot_id="boot-1",
            previous_sequence=6,
            received_at_utc=RECEIVED,
            seen_observation_ids={"observation-1"},
        )
    with pytest.raises(ValueError, match="duplicate source"):
        validate_observation_for_reducer(
            observation,
            expected_runtime_boot_id="boot-1",
            previous_sequence=6,
            received_at_utc=RECEIVED,
            seen_source_event_ids={"window-event-7"},
        )


def test_reducer_validation_accepts_only_strictly_new_same_boot_observation():
    observation = parse_environment_observation_wire(
        OBSERVATION_WIRE,
        received_at_utc=RECEIVED,
        expected_runtime_boot_id="boot-1",
        previous_sequence=6,
    )
    assert observation.sequence == 7


def test_canonical_json_and_hash_are_sorted_compact_and_round_trip_stable():
    observation = EnvironmentObservationV1.from_dict(OBSERVATION_WIRE)
    reversed_wire = dict(reversed(list(observation.to_dict().items())))
    expected = json.dumps(
        observation.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert canonical_json_bytes(observation) == expected
    assert canonical_json_bytes(reversed_wire) == expected
    assert canonical_hash(observation) == hashlib.sha256(expected).hexdigest()
    assert canonical_hash(reversed_wire) == observation.canonical_hash()
    assert b"\n" not in expected


def test_canonical_json_rejects_non_string_object_keys():
    with pytest.raises(ValueError, match="keys must be strings"):
        canonical_json_bytes({"nested": [{1: "not-wire-json"}]})


def test_contracts_and_nested_values_are_deeply_frozen():
    observation = EnvironmentObservationV1.from_dict(OBSERVATION_WIRE)
    assert isinstance(observation.attributes, MappingProxyType)
    assert isinstance(observation.provenance, MappingProxyType)
    with pytest.raises(dataclasses.FrozenInstanceError):
        observation.sequence = 8
    with pytest.raises(TypeError):
        observation.attributes["foreground"] = False

    snapshot = EnvironmentSnapshotV1.from_dict(snapshot_wire(observation))
    assert isinstance(snapshot.facts, tuple)
    assert isinstance(snapshot.stale_keys, tuple)
    assert isinstance(snapshot.source_health, MappingProxyType)
    with pytest.raises(TypeError):
        snapshot.source_health["foreground_app"] = SourceHealth.DEGRADED


@pytest.mark.parametrize(
    "attributes",
    (
        {"pixels": [1, 2, 3]},
        {"raw_media": "base64data"},
        {"ocr_text": "entire screen contents"},
        {"command_line": "python private.py --token value"},
        {"api_secret": "private"},
        {"workspace_alias": "C:\\Users\\owner\\private"},
        {"workspace_alias": "/home/owner/private"},
        {"diagnostic": "Authorization: Bearer abcdefghijklmnop"},
    ),
)
def test_sensitive_fields_values_and_absolute_paths_are_rejected(attributes):
    with pytest.raises(ValueError, match="forbidden|secret|path"):
        EnvironmentObservationV1.from_dict(observation_wire(attributes=attributes))


def test_raw_media_cannot_be_a_fact_and_ocr_ttl_is_never_persist_and_ten_seconds():
    raw = observation_wire(
        source_kind="raw_media",
        attributes={"frame_available": True},
        valid_until_utc=NOW,
        retention_class="never_persist",
    )
    with pytest.raises(ValueError, match="raw media"):
        EnvironmentObservationV1.from_dict(raw)

    valid_ocr = observation_wire(
        source_kind="screen_ocr",
        attributes={"target_present": True},
        valid_until_utc="2026-08-20T10:00:10.000Z",
        retention_class="never_persist",
    )
    assert EnvironmentObservationV1.from_dict(valid_ocr).retention_class is RetentionClass.NEVER_PERSIST

    overlong = copy.deepcopy(valid_ocr)
    overlong["valid_until_utc"] = "2026-08-20T10:00:10.001Z"
    with pytest.raises(ValueError, match="10 seconds"):
        EnvironmentObservationV1.from_dict(overlong)

    wrong_retention = copy.deepcopy(valid_ocr)
    wrong_retention["retention_class"] = "session"
    with pytest.raises(ValueError, match="never_persist"):
        EnvironmentObservationV1.from_dict(wrong_retention)

    for forbidden_attributes in (
        {"text": "recognized words"},
        {"content_lines": ["recognized", "words"]},
    ):
        full_text = copy.deepcopy(valid_ocr)
        full_text["attributes"] = forbidden_attributes
        with pytest.raises(ValueError, match="recognized text"):
            EnvironmentObservationV1.from_dict(full_text)


def test_snapshot_enforces_boot_freshness_key_uniqueness_and_status():
    snapshot = EnvironmentSnapshotV1.from_dict(snapshot_wire())
    assert snapshot.status_for("application", "editor", "foreground") is EnvironmentStatus.FRESH
    assert snapshot.status_for("workspace", "project-alpha", "change_count") is EnvironmentStatus.STALE
    assert snapshot.status_for("device", "local", "health") is EnvironmentStatus.UNKNOWN

    wrong_boot = snapshot_wire()
    wrong_boot["facts"][0]["runtime_boot_id"] = "boot-2"
    with pytest.raises(ValueError, match="runtime boot"):
        EnvironmentSnapshotV1.from_dict(wrong_boot)

    duplicate = snapshot_wire()
    duplicate["facts"].append(copy.deepcopy(duplicate["facts"][0]))
    with pytest.raises(ValueError, match="unique fact keys"):
        EnvironmentSnapshotV1.from_dict(duplicate)


def test_snapshot_parser_rejects_old_boot_and_revision_rollback():
    wire = snapshot_wire()
    with pytest.raises(ValueError, match="runtime_boot_id"):
        parse_environment_snapshot_wire(
            wire,
            received_at_utc="2026-08-20T10:00:01.500Z",
            expected_runtime_boot_id="boot-2",
        )
    with pytest.raises(ValueError, match="revision"):
        parse_environment_snapshot_wire(
            wire,
            received_at_utc="2026-08-20T10:00:01.500Z",
            expected_runtime_boot_id="boot-1",
            previous_revision=3,
        )


def test_json_parser_rejects_duplicate_wire_fields():
    raw = json.dumps(OBSERVATION_WIRE, separators=(",", ":"))
    duplicate = raw[:-1] + ',"sequence":8}'
    with pytest.raises(ValueError, match="duplicate JSON field"):
        parse_environment_observation_wire(duplicate, received_at_utc=RECEIVED)


def test_secret_and_biometric_privacy_never_enter_environment_facts():
    for privacy_class in (PrivacyClass.SECRET.value, PrivacyClass.BIOMETRIC.value):
        with pytest.raises(ValueError, match="privacy_class"):
            EnvironmentObservationV1.from_dict(
                observation_wire(
                    privacy_class=privacy_class,
                    retention_class="never_persist",
                )
            )
