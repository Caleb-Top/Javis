from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from core.events import Event
from core.life.contracts import PrivacyClass, RetentionClass
from core.life.l1.clock import ManualClock
from core.life.l1.contracts import (
    InputModality,
    InputProvenance,
    InputVerification,
    ObservationKind,
    ObservationOutcome,
    PlaybackLifecycleEvent,
    PlaybackOutcome,
    RiskLevel,
)
from core.life.l1.observation_bridge import (
    GovernedObservationBridge,
    ProjectionRejected,
)


NOW = "2026-08-12T08:00:00.000Z"


def _clock() -> ManualClock:
    return ManualClock(NOW, monotonic_seconds=100.0)


def _verified_voice() -> InputProvenance:
    return InputProvenance(
        modality=InputModality.VOICE,
        verification=InputVerification.SERVER_VERIFIED,
        runtime_boot_id="boot-1",
        source_session_id="session-1",
        owner_generation=7,
        voice_sequence=41,
        voice_turn=3,
    )


def _conversation_event(
    event_type: str,
    *,
    sequence: int,
    payload: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "event_id": f"conversation-event-{sequence}",
        "session_id": "session-1",
        "request_id": "request-1",
        "sequence": sequence,
        "type": event_type,
        "timestamp": datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc).timestamp(),
        "payload": dict(payload or {}),
    }


def test_conversation_projection_keeps_canonical_identity_and_verified_provenance():
    bridge = GovernedObservationBridge(
        runtime_boot_id="boot-1",
        clock=_clock(),
        boot_monotonic_seconds=95.0,
    )
    accepted = _conversation_event(
        "request.accepted",
        sequence=12,
        payload={
            "text": "private request body",
            "input_provenance": _verified_voice().to_dict(),
            "interaction_mode": "voice",
        },
    )

    observation = bridge.project_conversation(accepted)

    assert observation is not None
    assert observation.kind is ObservationKind.REQUEST_STARTED
    assert observation.source_event_id == "conversation-event-12"
    assert observation.sequence == 12
    assert observation.sequence_domain == "conversation_store:session-1"
    assert observation.correlation_id == "request-1"
    assert observation.causation_id is None
    assert observation.monotonic_offset_ms == 5000
    assert observation.source_generation == 7
    assert observation.input_provenance == _verified_voice()
    assert observation.privacy_class is PrivacyClass.USER_PRIVATE
    assert observation.retention_class is RetentionClass.SESSION
    serialized = json.dumps(observation.to_dict(), sort_keys=True)
    assert "private request body" not in serialized
    assert '"text"' not in serialized


def test_request_events_inherit_provenance_until_their_own_terminal_event():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    bridge.project_conversation(
        _conversation_event(
            "request.accepted",
            sequence=1,
            payload={"input_provenance": _verified_voice().to_dict()},
        )
    )

    activity = bridge.project_conversation(
        _conversation_event("activity.thinking", sequence=2, payload={"detail": "secret"})
    )
    terminal = bridge.project_conversation(
        _conversation_event("request.completed", sequence=3, payload={"text": "answer"})
    )
    late = bridge.project_conversation(
        _conversation_event("activity.thinking", sequence=4)
    )

    assert activity is not None and terminal is not None and late is not None
    assert activity.kind is ObservationKind.REQUEST_ACTIVITY
    assert terminal.kind is ObservationKind.REQUEST_COMPLETED
    assert terminal.outcome is ObservationOutcome.COMPLETED
    assert activity.input_provenance == _verified_voice()
    assert terminal.input_provenance == _verified_voice()
    assert late.input_provenance == InputProvenance.unknown()


@pytest.mark.parametrize(
    ("event_type", "kind", "outcome"),
    [
        ("activity.tool_started", ObservationKind.TOOL_STARTED, ObservationOutcome.STARTED),
        ("activity.tool_completed", ObservationKind.TOOL_COMPLETED, ObservationOutcome.COMPLETED),
        ("approval.required", ObservationKind.APPROVAL_REQUIRED, ObservationOutcome.NONE),
        ("request.failed", ObservationKind.REQUEST_FAILED, ObservationOutcome.FAILED),
        ("request.cancelled", ObservationKind.REQUEST_CANCELLED, ObservationOutcome.CANCELLED),
        ("user.invoked", ObservationKind.USER_INVOKED, ObservationOutcome.STARTED),
        (
            "interaction.interrupted",
            ObservationKind.INTERACTION_INTERRUPTED,
            ObservationOutcome.INTERRUPTED,
        ),
        ("goal.verified", ObservationKind.GOAL_VERIFIED, ObservationOutcome.VERIFIED),
    ],
)
def test_conversation_allowlist_has_fixed_kind_and_outcome(event_type, kind, outcome):
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())

    observation = bridge.project_conversation(
        _conversation_event(event_type, sequence=8)
    )

    assert observation is not None
    assert observation.kind is kind
    assert observation.outcome is outcome


def test_approval_resolution_and_risk_use_only_bounded_governed_fields():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())

    denied = bridge.project_conversation(
        _conversation_event(
            "approval.resolved",
            sequence=5,
            payload={
                "confirmed": False,
                "risk_level": "high",
                "tool_args": {"path": "private"},
            },
        )
    )

    assert denied is not None
    assert denied.kind is ObservationKind.APPROVAL_RESOLVED
    assert denied.outcome is ObservationOutcome.DENIED
    assert denied.risk_level is RiskLevel.HIGH
    serialized = json.dumps(denied.to_dict())
    assert '"path": "private"' not in serialized
    assert "tool_args" not in serialized


def test_unsupported_conversation_events_are_not_promoted_to_life_facts():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())

    assert bridge.project_conversation(
        _conversation_event("response.delta", sequence=6, payload={"text": "not speech"})
    ) is None
    assert bridge.project_conversation(
        _conversation_event("request.cancellation_pending", sequence=7)
    ) is None


def test_voice_projection_requires_governed_lease_metadata_and_never_projects_transcript():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    ready = {
        "type": "audio.stream.ready",
        "sequence": 19,
        "timestamp": datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc).timestamp(),
        "owner_generation": 4,
        "trackLabel": "private microphone label",
    }

    listening = bridge.project_voice(
        ready,
        session_id="session-1",
        owner_generation=4,
    )
    transcript = bridge.project_voice(
        {**ready, "type": "transcript.final", "text": "private transcript"},
        session_id="session-1",
        owner_generation=4,
    )

    assert listening is not None
    assert listening.kind is ObservationKind.VOICE_LISTENING_STARTED
    assert listening.outcome is ObservationOutcome.STARTED
    assert listening.sequence == 19
    assert listening.source_generation == 4
    assert listening.input_provenance.modality is InputModality.VOICE
    assert listening.input_provenance.verification is InputVerification.UNKNOWN
    assert transcript is None
    serialized = json.dumps(listening.to_dict())
    assert "private microphone label" not in serialized
    assert "private transcript" not in serialized


def test_voice_error_stops_listening_and_owner_mismatch_is_rejected():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    event = {
        "type": "audio.error",
        "sequence": 20,
        "timestamp": datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc).timestamp(),
        "owner_generation": 5,
        "message": "device path must not escape",
    }

    stopped = bridge.project_voice(event, session_id="session-1", owner_generation=5)

    assert stopped is not None
    assert stopped.kind is ObservationKind.VOICE_LISTENING_STOPPED
    assert stopped.outcome is ObservationOutcome.FAILED
    with pytest.raises(ProjectionRejected, match="owner generation"):
        bridge.project_voice(event, session_id="session-1", owner_generation=6)


def test_playback_projection_preserves_generation_and_scopes_terminal_causation():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    started = PlaybackLifecycleEvent(
        schema_version=1,
        playback_id="playback-1",
        generation=9,
        runtime_boot_id="boot-1",
        session_id="session-1",
        request_id="request-1",
        outcome=PlaybackOutcome.STARTED,
        occurred_at_utc=NOW,
        reason_code="playback_started",
    )
    completed = PlaybackLifecycleEvent(
        schema_version=1,
        playback_id="playback-1",
        generation=9,
        runtime_boot_id="boot-1",
        session_id="session-1",
        request_id="request-1",
        outcome=PlaybackOutcome.COMPLETED,
        occurred_at_utc=NOW,
        reason_code="playback_completed",
    )

    speech_started = bridge.project_playback(started)
    speech_stopped = bridge.project_playback(completed)

    assert speech_started.kind is ObservationKind.SPEECH_STARTED
    assert speech_stopped.kind is ObservationKind.SPEECH_STOPPED
    assert speech_started.sequence == 18
    assert speech_stopped.sequence == 19
    assert speech_started.source_generation == 9
    assert speech_stopped.causation_id == speech_started.source_event_id
    assert speech_stopped.outcome is ObservationOutcome.COMPLETED


def _playback_event(
    *,
    generation: int,
    outcome: PlaybackOutcome,
    playback_id: str = "playback-1",
) -> PlaybackLifecycleEvent:
    return PlaybackLifecycleEvent(
        schema_version=1,
        playback_id=playback_id,
        generation=generation,
        runtime_boot_id="boot-1",
        session_id="session-1",
        request_id="request-1",
        outcome=outcome,
        occurred_at_utc=NOW,
        reason_code=f"playback_{outcome.value}",
    )


def test_playback_terminal_tombstone_prevents_old_generation_from_reviving():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())

    bridge.project_playback(
        _playback_event(generation=1, outcome=PlaybackOutcome.STARTED)
    )
    bridge.project_playback(
        _playback_event(generation=1, outcome=PlaybackOutcome.STOPPED)
    )
    bridge.project_playback(
        _playback_event(generation=2, outcome=PlaybackOutcome.STARTED)
    )

    with pytest.raises(ProjectionRejected, match="already terminal"):
        bridge.project_playback(
            _playback_event(generation=1, outcome=PlaybackOutcome.STARTED)
        )

    generation_two_terminal = bridge.project_playback(
        _playback_event(generation=2, outcome=PlaybackOutcome.COMPLETED)
    )
    assert generation_two_terminal.source_generation == 2
    assert generation_two_terminal.outcome is ObservationOutcome.COMPLETED


def test_playback_lifecycle_requires_started_and_accepts_each_phase_once():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    started = _playback_event(generation=4, outcome=PlaybackOutcome.STARTED)
    terminal = _playback_event(generation=4, outcome=PlaybackOutcome.FAILED)

    with pytest.raises(ProjectionRejected, match="requires STARTED"):
        bridge.project_playback(terminal)

    bridge.project_playback(started)
    with pytest.raises(ProjectionRejected, match="duplicate STARTED"):
        bridge.project_playback(started)

    bridge.project_playback(terminal)
    with pytest.raises(ProjectionRejected, match="already terminal"):
        bridge.project_playback(terminal)


def test_playback_started_admission_is_thread_safe():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    started = _playback_event(generation=7, outcome=PlaybackOutcome.STARTED)

    def project_once() -> bool:
        try:
            bridge.project_playback(started)
        except ProjectionRejected:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(lambda _: project_once(), range(16)))

    assert accepted.count(True) == 1
    assert accepted.count(False) == 15


def test_playback_lifecycle_index_is_bounded():
    bridge = GovernedObservationBridge(
        runtime_boot_id="boot-1",
        clock=_clock(),
        playback_lifecycle_capacity=2,
    )
    for number in range(3):
        playback_id = f"playback-{number}"
        bridge.project_playback(
            _playback_event(
                playback_id=playback_id,
                generation=number,
                outcome=PlaybackOutcome.STARTED,
            )
        )
        bridge.project_playback(
            _playback_event(
                playback_id=playback_id,
                generation=number,
                outcome=PlaybackOutcome.COMPLETED,
            )
        )

    stats = bridge.stats()
    assert stats["tracked_playback_lifecycles"] == 2
    assert stats["playback_lifecycle_evictions"] == 1


def test_playback_from_another_boot_is_rejected():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    event = PlaybackLifecycleEvent(
        schema_version=1,
        playback_id="playback-1",
        generation=1,
        runtime_boot_id="old-boot",
        session_id="session-1",
        request_id="request-1",
        outcome=PlaybackOutcome.STOPPED,
        occurred_at_utc=NOW,
        reason_code="playback_stopped",
    )

    with pytest.raises(ProjectionRejected, match="runtime boot"):
        bridge.project_playback(event)


@pytest.mark.parametrize(
    ("event_type", "kind", "outcome"),
    [
        ("runtime.degraded", ObservationKind.RUNTIME_DEGRADED, ObservationOutcome.FAILED),
        ("runtime.recovered", ObservationKind.RUNTIME_RECOVERED, ObservationOutcome.COMPLETED),
    ],
)
def test_runtime_projection_uses_event_bus_identity_without_detail_payload(
    event_type, kind, outcome
):
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    event = Event(
        id="runtime-event-1",
        type=event_type,
        payload={"detail": "private filesystem path"},
        source="runtime",
        timestamp=datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc).timestamp(),
        sequence=14,
    )

    observation = bridge.project_runtime(event)

    assert observation is not None
    assert observation.kind is kind
    assert observation.outcome is outcome
    assert observation.source_event_id == "runtime-event-1"
    assert observation.sequence_domain == "runtime:boot-1"
    assert "private filesystem path" not in json.dumps(observation.to_dict())


def test_recognized_source_with_malformed_identity_fails_closed():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    malformed = _conversation_event("request.completed", sequence=3)
    malformed["event_id"] = ""

    with pytest.raises(ProjectionRejected, match="event_id"):
        bridge.project_conversation(malformed)


def test_conversation_event_from_unregistered_publisher_is_rejected():
    bridge = GovernedObservationBridge(runtime_boot_id="boot-1", clock=_clock())
    event = _conversation_event("request.completed", sequence=3)
    event["source"] = "browser"

    with pytest.raises(ProjectionRejected, match="conversation source"):
        bridge.project_conversation(event)


def test_provenance_cache_is_bounded():
    bridge = GovernedObservationBridge(
        runtime_boot_id="boot-1",
        clock=_clock(),
        provenance_capacity=2,
    )
    provenance = InputProvenance(
        InputModality.TEXT,
        InputVerification.CLIENT_CLAIMED,
        None,
        None,
        None,
        None,
        None,
    )
    for number in range(3):
        event = _conversation_event(
            "request.accepted",
            sequence=number + 1,
            payload={"input_provenance": provenance.to_dict()},
        )
        event["request_id"] = f"request-{number}"
        bridge.project_conversation(event)

    assert bridge.stats()["tracked_request_provenance"] == 2
    assert bridge.stats()["provenance_evictions"] == 1
