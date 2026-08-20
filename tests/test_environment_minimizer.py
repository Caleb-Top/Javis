from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.environment.contracts import SourceKind
from core.environment.minimizer import (
    EnvironmentMinimizationError,
    EnvironmentMinimizer,
)
from core.environment.sources import EnvironmentSourceAdapter, EnvironmentSourceEvent


HASH = "a" * 64


@pytest.mark.parametrize(
    ("kind", "payload", "expected"),
    [
        (
            SourceKind.FOREGROUND_APP,
            {"app_category": "development", "availability": "available", "is_foreground": True},
            "development",
        ),
        (
            SourceKind.PROCESS_HEALTH,
            {"cpu_level": "normal", "memory_level": "high", "process_count": 14, "responsive": True},
            14,
        ),
        (
            SourceKind.DEVICE_HEALTH,
            {"availability": "available", "disk_level": "normal", "thermal_level": "low"},
            "normal",
        ),
        (
            SourceKind.WORKSPACE_METADATA,
            {"workspace_alias": "Javis", "availability": "available", "change_count": 3, "dirty": True},
            "Javis",
        ),
        (
            SourceKind.SCREEN_OCR,
            {"task_state": "blocked", "match_count": 1, "has_blocker": True, "confidence": 0.9},
            "blocked",
        ),
        (
            SourceKind.SCREEN_OBJECT,
            {"object_classes": ["dialog", "warning"], "object_count": 2, "target_present": True},
            ("dialog", "warning"),
        ),
    ],
)
def test_each_source_emits_only_allowlisted_minimized_attributes(kind, payload, expected):
    result = EnvironmentMinimizer().minimize(kind, payload)

    assert expected in result.values()
    with pytest.raises(TypeError):
        result["injected"] = True


@pytest.mark.parametrize(
    "field",
    [
        "window_title",
        "ocr_text",
        "file_content",
        "absolute_path",
        "command_line",
        "raw_pixels",
        "video_bytes",
        "biometric_template",
        "access_token",
        "prompt",
    ],
)
def test_sensitive_or_full_content_fields_are_rejected_before_validation(field):
    with pytest.raises(EnvironmentMinimizationError, match="sensitive"):
        EnvironmentMinimizer().minimize(
            SourceKind.WORKSPACE_METADATA,
            {field: "must-not-survive"},
        )


def test_unknown_fields_and_raw_media_fail_closed():
    minimizer = EnvironmentMinimizer()
    with pytest.raises(EnvironmentMinimizationError, match="allowlisted"):
        minimizer.minimize(SourceKind.DEVICE_HEALTH, {"hostname": "private-host"})
    with pytest.raises(EnvironmentMinimizationError, match="raw media"):
        minimizer.minimize(SourceKind.RAW_MEDIA, {"frame": b"pixels"})


@pytest.mark.parametrize("alias", ["C:/Users/private", "../escape", "folder/name", "bad\\name"])
def test_workspace_alias_cannot_smuggle_a_path(alias):
    with pytest.raises(EnvironmentMinimizationError, match="path"):
        EnvironmentMinimizer().minimize(
            SourceKind.WORKSPACE_METADATA,
            {"workspace_alias": alias},
        )


def test_output_has_no_reference_to_mutable_raw_payload():
    classes = ["dialog"]
    payload = {"object_classes": classes, "object_count": 1}
    result = EnvironmentMinimizer().minimize(SourceKind.SCREEN_OBJECT, payload)
    classes.append("warning")
    payload["object_count"] = 99

    assert result == {"object_classes": ("dialog",), "object_count": 1}


def test_source_adapter_builds_a_bounded_contract_without_raw_payload():
    adapter = EnvironmentSourceAdapter("boot-l4-fixture")
    event = EnvironmentSourceEvent(
        source_event_id="event-l4-fixture",
        source_kind=SourceKind.SCREEN_OCR,
        subject_kind="screen",
        subject_key="primary-display",
        payload={"task_state": "ready", "target_present": True, "confidence": 0.8},
        observed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        sequence=7,
        confidence=0.8,
    )

    observation = adapter.adapt(event, grant_id_hash=HASH)

    assert observation.source_kind is SourceKind.SCREEN_OCR
    assert observation.attributes == {
        "task_state": "ready",
        "target_present": True,
        "confidence": 0.8,
    }
    assert observation.valid_until_utc == "2026-08-20T12:00:10.000Z"
    wire = observation.to_dict()
    assert "payload" not in wire
    assert HASH not in repr(event.payload)


def test_source_adapter_rejects_absolute_subjects_and_bad_grant_hashes():
    adapter = EnvironmentSourceAdapter("boot-l4-fixture")
    event = EnvironmentSourceEvent(
        source_event_id="event-l4-fixture",
        source_kind=SourceKind.WORKSPACE_METADATA,
        subject_kind="workspace",
        subject_key="C:/private/workspace",
        payload={"workspace_alias": "Project"},
        observed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        sequence=1,
    )
    with pytest.raises(ValueError, match="absolute filesystem paths"):
        adapter.adapt(event, grant_id_hash=HASH)

    safe_event = EnvironmentSourceEvent(
        source_event_id="event-l4-safe",
        source_kind=SourceKind.DEVICE_HEALTH,
        subject_kind="device",
        subject_key="local-device",
        payload={"availability": "available"},
        observed_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        sequence=1,
    )
    with pytest.raises(ValueError, match="grant_id_hash"):
        adapter.adapt(safe_event, grant_id_hash="not-a-hash")
