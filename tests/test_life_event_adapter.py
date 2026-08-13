import json

import pytest

from core.events import Event
from core.life.contracts import PrivacyClass, RetentionClass
from core.life.event_adapter import LifeEventAdapter
from core.life.privacy import PrivacyPolicy


def _adapter():
    return LifeEventAdapter(
        identity_id="identity-one",
        instance_id="instance-one",
        now=lambda: 100.0,
    )


def _event(event_type, payload, **overrides):
    values = {
        "id": "e1",
        "type": event_type,
        "source": "unit",
        "payload": payload,
        "timestamp": 101.0,
        "correlation_id": "run-1",
        "causation_id": "e0",
        "sequence": 7,
    }
    values.update(overrides)
    return Event(**values)


def test_tool_payload_mapping_preserves_existing_fields_without_inventing_task():
    source = _event(
        "tool.completed",
        {"tool": "screenshot", "duration_ms": 31, "success": True},
        source="tools",
    )

    mapped = _adapter().map(source)[0]

    assert mapped.source_event_id == "e1"
    assert mapped.correlation_id == "run-1"
    assert mapped.causation_id == "e0"
    assert mapped.payload["duration_ms"] == 31
    assert "task" not in mapped.payload
    assert mapped.event_type == "life.tool.completed"


def test_tool_mapping_drops_params_data_paths_and_free_form_errors():
    source = _event(
        "tool.failed",
        {
            "tool": "file_read",
            "category": "filesystem",
            "success": False,
            "params": {"path": "C:/private/secret.txt"},
            "data": "private file contents",
            "error": "failed at C:/private/secret.txt",
            "duration_ms": 12.5,
        },
        source="tools",
    )

    mapped = _adapter().map(source)[0]
    serialized = json.dumps(mapped.to_dict(), ensure_ascii=False)

    assert mapped.payload == {
        "tool": "file_read",
        "category": "filesystem",
        "success": False,
        "duration_ms": 12.5,
    }
    assert "C:/private" not in serialized
    assert "private file contents" not in serialized
    assert mapped.privacy_class is PrivacyClass.RESTRICTED_SYSTEM
    assert mapped.retention_class is RetentionClass.AUDIT


def test_secrets_are_redacted_never_indexed_and_never_persisted():
    payload = {"api_key": "sk-secret", "text": "private prompt"}
    policy = PrivacyPolicy()

    redacted = policy.redact_payload("provider.configured", payload)
    mapped = _adapter().map(_event("provider.configured", payload))[0]

    assert "sk-secret" not in json.dumps(redacted)
    assert "sk-secret" not in json.dumps(mapped.to_dict())
    assert policy.index_summary("provider.configured", payload) == ""
    assert mapped.privacy_class is PrivacyClass.SECRET
    assert mapped.retention_class is RetentionClass.NEVER_PERSIST


def test_request_text_and_response_delta_are_never_copied_or_indexed():
    accepted = _adapter().map(
        _event(
            "request.accepted",
            {
                "session_id": "s1",
                "request_id": "r1",
                "text": "my private request",
                "interaction_mode": "voice",
                "replaces_request_id": "r0",
            },
            source="conversation_hub",
        )
    )[0]
    delta = _adapter().map(
        _event("response.delta", {"text": "private answer"}, id="e2")
    )

    assert accepted.session_id == "s1"
    assert accepted.request_id == "r1"
    assert accepted.payload == {
        "interaction_mode": "voice",
        "replaces_request_id": "r0",
    }
    assert "private request" not in json.dumps(accepted.to_dict())
    assert accepted.privacy_class is PrivacyClass.USER_PRIVATE
    assert delta == []


def test_runtime_and_event_store_paths_are_removed_before_mapping():
    runtime_event = _adapter().map(
        _event("runtime.created", {"root": "G:/Javis"}, source="runtime")
    )[0]
    store_event = _adapter().map(
        _event(
            "event_store.registered",
            {"path": "G:/private/events.sqlite"},
            id="e2",
            source="runtime",
        )
    )[0]

    assert runtime_event.payload == {}
    assert store_event.payload == {}
    assert "G:/" not in json.dumps(runtime_event.to_dict())
    assert "G:/" not in json.dumps(store_event.to_dict())
    assert runtime_event.retention_class is RetentionClass.CONTINUITY


@pytest.mark.parametrize(
    ("event_type", "payload", "expected_type", "retention"),
    [
        (
            "subsystem.registered",
            {"name": "voice"},
            "life.subsystem.registered",
            RetentionClass.OPERATIONAL,
        ),
        (
            "approval.requested",
            {"approval_id": "a1", "tool": "open_file", "params": {"path": "x"}},
            "life.approval.requested",
            RetentionClass.AUDIT,
        ),
        (
            "agent_run.completed",
            {"run_id": "run-1", "status": "completed", "objective": "private"},
            "life.agent_run.completed",
            RetentionClass.OPERATIONAL,
        ),
        (
            "memory.candidate.status_changed",
            {"candidate_id": "c1", "status": "approved", "content": "private"},
            "life.memory.candidate.status_changed",
            RetentionClass.OPERATIONAL,
        ),
        (
            "activity.tool_completed",
            {"tool": "system_info", "success": True, "data": "private"},
            "life.activity.tool_completed",
            RetentionClass.SESSION,
        ),
    ],
)
def test_real_event_families_have_explicit_allowlisted_mappings(
    event_type,
    payload,
    expected_type,
    retention,
):
    mapped = _adapter().map(_event(event_type, payload))[0]
    serialized = json.dumps(mapped.to_dict())

    assert mapped.event_type == expected_type
    assert mapped.retention_class is retention
    assert '"private"' not in serialized
    assert "params" not in mapped.payload
    assert "data" not in mapped.payload


def test_biometric_payload_is_never_persisted_even_without_secret_keys():
    mapped = _adapter().map(
        _event(
            "voice.audio.captured",
            {"audio": b"raw-audio", "rms": 0.2},
            source="voice",
        )
    )[0]

    assert mapped.privacy_class is PrivacyClass.BIOMETRIC
    assert mapped.retention_class is RetentionClass.NEVER_PERSIST
    assert mapped.payload == {"diagnostic": "payload_rejected"}


def test_cyclic_payload_produces_a_diagnostic_without_raising():
    payload = {"safe": "value"}
    payload["cycle"] = payload

    mapped = _adapter().map(_event("tool.completed", payload))[0]

    assert mapped.payload == {"diagnostic": "payload_rejected"}
    assert "payload_rejected" in mapped.redaction_summary
    assert mapped.confidence == 0.0


def test_unknown_cyclic_payload_is_still_reported_as_invalid():
    payload = {}
    payload["cycle"] = payload

    mapped = _adapter().map(_event("future.unknown", payload))[0]

    assert mapped.event_type == "life.observation.invalid"
    assert mapped.payload == {"diagnostic": "payload_rejected"}
    assert mapped.confidence == 0.0


def test_unserializable_payload_never_raises_into_the_event_bus_path():
    mapped = _adapter().map(
        _event("future.unknown", {"unsupported": object()})
    )[0]

    assert mapped.event_type == "life.observation.invalid"
    assert mapped.payload == {"diagnostic": "payload_rejected"}


def test_oversized_and_deep_payload_is_bounded():
    payload = {
        "tool": "x" * 10_000,
        "duration_ms": 1,
        "extra": [[[[[["secret-deep-value"]]]]]],
    }

    mapped = _adapter().map(_event("tool.completed", payload))[0]
    serialized = json.dumps(mapped.to_dict())

    assert len(serialized.encode("utf-8")) < 12_000
    assert "secret-deep-value" not in serialized
    assert len(mapped.payload["tool"]) <= 256
    assert "string_truncated" in mapped.redaction_summary


def test_secret_scan_has_a_fixed_depth_budget():
    payload = {"leaf": True}
    for _ in range(2_000):
        payload = {"nested": payload}

    policy = PrivacyPolicy(max_depth=4)

    assert policy.classify("future.unknown", payload) == (
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.OPERATIONAL,
    )


def test_unknown_event_becomes_payload_free_diagnostic_observation():
    mapped = _adapter().map(
        _event("future.unknown", {"private": "do not copy"}, source="future")
    )[0]

    assert mapped.event_type == "life.observation.unknown"
    assert mapped.payload == {"source_event_type": "future.unknown"}
    assert "do not copy" not in json.dumps(mapped.to_dict())
    assert mapped.retention_class is RetentionClass.OPERATIONAL


@pytest.mark.parametrize(
    "event_type",
    ["thinking.delta", "agent_delta.appended", "response.delta", "voice.audio.level"],
)
def test_high_frequency_or_content_delta_events_are_ignored(event_type):
    assert _adapter().map(_event(event_type, {"text": "private"})) == []


def test_malformed_event_metadata_returns_diagnostic_instead_of_raising():
    source = _event(
        "tool.completed",
        {"tool": "system_info"},
        id="bad\nevent",
        sequence=-2,
        correlation_id="bad\ncorrelation",
        timestamp=float("nan"),
    )

    mapped = _adapter().map(source)[0]

    assert mapped.event_type == "life.observation.invalid"
    assert mapped.source_event_id is None
    assert mapped.sequence == 0
    assert mapped.correlation_id is None
    assert mapped.confidence == 0.0


def test_mapping_is_deterministic_for_the_same_source_event():
    source = _event("tool.completed", {"tool": "system_info", "success": True})
    adapter = _adapter()

    first = adapter.map(source)[0]
    second = adapter.map(source)[0]

    assert first.event_id == second.event_id
    assert first.to_dict() == second.to_dict()
