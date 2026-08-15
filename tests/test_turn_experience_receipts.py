from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.life.contracts import PrivacyClass, RetentionClass
from core.life.l1.contracts import (
    InputModality,
    InputProvenance,
    InputVerification,
    LifeObservation,
    ObservationKind,
    ObservationOutcome,
    ReceiptCompleteness,
    ResponsePath,
    RiskLevel,
    TurnOutcome,
)
from core.life.l1.receipts import TurnExperienceProjector


START = datetime(2026, 8, 15, 4, 0, tzinfo=timezone.utc)
PROVENANCE = InputProvenance(
    modality=InputModality.TEXT,
    verification=InputVerification.CLIENT_CLAIMED,
    runtime_boot_id=None,
    source_session_id="session-1",
    owner_generation=None,
    voice_sequence=None,
    voice_turn=None,
)


def utc(seconds: float = 0.0) -> str:
    return (START + timedelta(seconds=seconds)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def observation(
    kind: ObservationKind,
    sequence: int,
    *,
    session_id: str = "session-1",
    request_id: str = "request-1",
    boot_id: str = "boot-1",
    outcome: ObservationOutcome = ObservationOutcome.NONE,
    domain: str | None = None,
) -> LifeObservation:
    return LifeObservation(
        schema_version=1,
        observation_id=f"observation-{session_id}-{request_id}-{sequence}",
        kind=kind,
        occurred_at_utc=utc(sequence),
        monotonic_offset_ms=sequence * 1000,
        source="conversation",
        source_event_id=f"event-{session_id}-{request_id}-{sequence}",
        source_boot_id=boot_id,
        source_generation=None,
        session_id=session_id,
        request_id=request_id,
        correlation_id=request_id,
        causation_id=None,
        sequence=sequence,
        sequence_domain=domain or f"conversation.{session_id}",
        input_provenance=InputProvenance(
            modality=PROVENANCE.modality,
            verification=PROVENANCE.verification,
            runtime_boot_id=PROVENANCE.runtime_boot_id,
            source_session_id=session_id,
            owner_generation=PROVENANCE.owner_generation,
            voice_sequence=PROVENANCE.voice_sequence,
            voice_turn=PROVENANCE.voice_turn,
        ),
        outcome=outcome,
        risk_level=RiskLevel.NONE,
        confidence=1.0,
        privacy_class=PrivacyClass.USER_PRIVATE,
        retention_class=RetentionClass.EPHEMERAL,
    )


def complete_turn(
    projector: TurnExperienceProjector,
    index: int,
) -> None:
    session = "bounded-session"
    request = f"request-{index:02d}"
    projector.apply(
        observation(
            ObservationKind.REQUEST_STARTED,
            index * 2 + 1,
            session_id=session,
            request_id=request,
        )
    )
    projector.apply(
        observation(
            ObservationKind.REQUEST_COMPLETED,
            index * 2 + 2,
            session_id=session,
            request_id=request,
            outcome=ObservationOutcome.COMPLETED,
        )
    )


def test_projects_privacy_thin_incomplete_receipt_from_canonical_start() -> None:
    projector = TurnExperienceProjector()
    started = observation(ObservationKind.REQUEST_STARTED, 1)

    receipt = projector.apply(
        started,
        model_route="local.default",
        response_path=ResponsePath.MODEL,
    )

    assert receipt is not None
    assert receipt.receipt_id.startswith("receipt-")
    assert receipt.session_id == "session-1"
    assert receipt.request_id == "request-1"
    assert receipt.first_event_id == started.source_event_id
    assert receipt.first_event_sequence == 1
    assert receipt.first_event_sequence_domain == "conversation.session-1"
    assert receipt.outcome is TurnOutcome.UNKNOWN
    assert receipt.completeness is ReceiptCompleteness.INCOMPLETE
    assert receipt.terminal_event_id is None
    assert receipt.response_path is ResponsePath.MODEL
    assert receipt.model_route == "local.default"
    assert receipt.privacy_class is PrivacyClass.LOCAL_INTERNAL
    assert receipt.retention_class is RetentionClass.OPERATIONAL
    assert receipt.verify_hash()
    assert not {
        "text",
        "transcript",
        "audio",
        "tool_args",
        "tool_result",
        "response",
        "reasoning",
    } & set(receipt.to_dict())


def test_reduces_activity_and_real_terminal_event_deterministically() -> None:
    projector = TurnExperienceProjector()
    events = (
        observation(ObservationKind.REQUEST_STARTED, 1),
        observation(ObservationKind.TOOL_STARTED, 2, outcome=ObservationOutcome.STARTED),
        observation(ObservationKind.APPROVAL_REQUIRED, 3),
        observation(
            ObservationKind.APPROVAL_RESOLVED,
            4,
            outcome=ObservationOutcome.APPROVED,
        ),
        observation(
            ObservationKind.INTERACTION_INTERRUPTED,
            5,
            outcome=ObservationOutcome.INTERRUPTED,
        ),
        observation(
            ObservationKind.GOAL_VERIFIED,
            6,
            outcome=ObservationOutcome.VERIFIED,
        ),
        observation(
            ObservationKind.TOOL_COMPLETED,
            7,
            outcome=ObservationOutcome.COMPLETED,
        ),
        observation(
            ObservationKind.REQUEST_COMPLETED,
            8,
            outcome=ObservationOutcome.COMPLETED,
        ),
    )

    final = None
    for event in events:
        final = projector.apply(
            event,
            model_route="local.default" if event is events[0] else None,
        )

    assert final is not None
    assert final.outcome is TurnOutcome.COMPLETED
    assert final.completeness is ReceiptCompleteness.COMPLETE
    assert final.terminal_event_id == events[-1].source_event_id
    assert final.terminal_event_sequence == 8
    assert final.terminal_event_sequence_domain == "conversation.session-1"
    assert final.activity_kinds == (
        "tool.started",
        "approval.required",
        "approval.resolved",
        "interaction.interrupted",
        "goal.verified",
        "tool.completed",
    )
    assert final.tool_count == 1
    assert final.approval_outcome.value == "approved"
    assert final.interruption_count == 1
    assert final.goal_verified is True
    assert final.response_path is ResponsePath.MIXED
    assert final.verify_hash()

    replay = TurnExperienceProjector()
    replayed = None
    for event in events:
        replayed = replay.apply(
            event,
            model_route="local.default" if event is events[0] else None,
        )
    assert replayed == final


@pytest.mark.parametrize(
    ("kind", "observation_outcome", "receipt_outcome"),
    (
        (ObservationKind.REQUEST_COMPLETED, ObservationOutcome.COMPLETED, TurnOutcome.COMPLETED),
        (ObservationKind.REQUEST_FAILED, ObservationOutcome.FAILED, TurnOutcome.FAILED),
        (ObservationKind.REQUEST_CANCELLED, ObservationOutcome.CANCELLED, TurnOutcome.CANCELLED),
    ),
)
def test_maps_only_canonical_request_terminal_events(
    kind: ObservationKind,
    observation_outcome: ObservationOutcome,
    receipt_outcome: TurnOutcome,
) -> None:
    projector = TurnExperienceProjector()
    projector.apply(observation(ObservationKind.REQUEST_STARTED, 1))

    receipt = projector.apply(
        observation(kind, 2, outcome=observation_outcome)
    )

    assert receipt is not None
    assert receipt.outcome is receipt_outcome
    assert receipt.terminal_event_id == "event-session-1-request-1-2"


def test_event_and_turn_replays_are_idempotent() -> None:
    projector = TurnExperienceProjector()
    started = observation(ObservationKind.REQUEST_STARTED, 1)
    completed = observation(
        ObservationKind.REQUEST_COMPLETED,
        2,
        outcome=ObservationOutcome.COMPLETED,
    )

    first = projector.apply(started)
    assert projector.apply(started) is None
    final = projector.apply(completed)
    assert projector.apply(completed) is None
    assert projector.apply(started) is None

    assert first is not None and final is not None
    assert final.receipt_id == first.receipt_id
    assert projector.receipt_for("session-1", "request-1") == final
    assert projector.recent_receipts() == (final,)


def test_late_and_cross_boot_events_cannot_mutate_a_turn() -> None:
    projector = TurnExperienceProjector()
    projector.apply(observation(ObservationKind.REQUEST_STARTED, 10))
    current = projector.apply(observation(ObservationKind.REQUEST_ACTIVITY, 12))

    assert projector.apply(observation(ObservationKind.TOOL_STARTED, 11)) is None
    assert projector.receipt_for("session-1", "request-1") == current
    with pytest.raises(ValueError, match="boot"):
        projector.apply(
            observation(
                ObservationKind.REQUEST_COMPLETED,
                13,
                boot_id="boot-2",
                outcome=ObservationOutcome.COMPLETED,
            )
        )


def test_recent_projection_view_is_bounded_to_32_turns() -> None:
    projector = TurnExperienceProjector()

    for index in range(40):
        complete_turn(projector, index)

    recent = projector.recent_receipts()
    assert len(recent) == 32
    assert [receipt.request_id for receipt in recent] == [
        f"request-{index:02d}" for index in range(8, 40)
    ]
    assert projector.receipt_for("bounded-session", "request-07") is None
    assert projector.receipt_for("bounded-session", "request-08") == recent[0]


def test_restart_recovers_incomplete_without_fabricating_terminal_event() -> None:
    running = TurnExperienceProjector()
    running.apply(observation(ObservationKind.REQUEST_STARTED, 1))
    incomplete = running.apply(observation(ObservationKind.REQUEST_ACTIVITY, 2))
    assert incomplete is not None

    restarted = TurnExperienceProjector()
    recovered = restarted.recover_after_restart(
        (incomplete, incomplete),
        recovered_at_utc=utc(20),
        recovery_event_id="life-recovery-1",
    )

    assert len(recovered) == 1
    receipt = recovered[0]
    assert receipt.outcome is TurnOutcome.INTERRUPTED
    assert receipt.completeness is ReceiptCompleteness.INTERRUPTED_BY_RESTART
    assert receipt.ended_at_utc == utc(20)
    assert receipt.recovery_event_id == "life-recovery-1"
    assert receipt.recovered_at_utc == utc(20)
    assert receipt.terminal_event_id is None
    assert receipt.terminal_event_sequence is None
    assert receipt.terminal_event_sequence_domain is None
    assert receipt.verify_hash()
    assert restarted.recover_after_restart(
        (incomplete,),
        recovered_at_utc=utc(20),
        recovery_event_id="life-recovery-1",
    ) == ()


def test_restart_keeps_terminal_receipts_and_can_recover_without_event() -> None:
    running = TurnExperienceProjector()
    running.apply(observation(ObservationKind.REQUEST_STARTED, 1))
    completed = running.apply(
        observation(
            ObservationKind.REQUEST_COMPLETED,
            2,
            outcome=ObservationOutcome.COMPLETED,
        )
    )
    assert completed is not None

    pending = TurnExperienceProjector()
    incomplete = pending.apply(
        observation(
            ObservationKind.REQUEST_STARTED,
            3,
            request_id="request-2",
        )
    )
    assert incomplete is not None

    restarted = TurnExperienceProjector()
    recovered = restarted.recover_after_restart(
        (completed, incomplete),
        recovered_at_utc=utc(20),
    )

    assert len(recovered) == 1
    assert recovered[0].request_id == "request-2"
    assert recovered[0].recovery_event_id is None
    assert recovered[0].recovered_at_utc is None
    assert restarted.receipt_for("session-1", "request-1") == completed


def test_orphan_events_and_invalid_inputs_are_rejected_without_projection() -> None:
    projector = TurnExperienceProjector()

    assert projector.apply(
        observation(
            ObservationKind.REQUEST_COMPLETED,
            2,
            outcome=ObservationOutcome.COMPLETED,
        )
    ) is None
    with pytest.raises(TypeError, match="LifeObservation"):
        projector.apply({"text": "private"})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_recent"):
        TurnExperienceProjector(max_recent=33)
