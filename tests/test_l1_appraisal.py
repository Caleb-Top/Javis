from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.life.contracts import PrivacyClass, RetentionClass
from core.life.l1.appraisal import AppraisalReducer
from core.life.l1.clock import (
    ClockReading,
    HOMEOSTASIS_BASELINES,
    HOMEOSTASIS_HALF_LIVES_SECONDS,
    ManualClock,
    decay_toward_baseline,
    elapsed_monotonic_seconds,
)
from core.life.l1.contracts import (
    FunctionalAffectKind,
    InputProvenance,
    LifeObservation,
    ObservationKind,
    ObservationOutcome,
    ReasonCode,
    RiskLevel,
    StateDimension,
)


NOW = "2026-08-12T10:00:00.000Z"


def observation(
    kind: ObservationKind,
    *,
    outcome: ObservationOutcome = ObservationOutcome.NONE,
    risk: RiskLevel = RiskLevel.NONE,
    observation_id: str = "obs-1",
) -> LifeObservation:
    return LifeObservation(
        schema_version=1,
        observation_id=observation_id,
        kind=kind,
        occurred_at_utc=NOW,
        monotonic_offset_ms=100,
        source="conversation",
        source_event_id=f"event-{observation_id[:250]}",
        source_boot_id="boot-1",
        source_generation=1,
        session_id="session-1",
        request_id="request-1",
        correlation_id=None,
        causation_id=None,
        sequence=1,
        sequence_domain="conversation",
        input_provenance=InputProvenance.unknown(),
        outcome=outcome,
        risk_level=risk,
        confidence=0.8,
        privacy_class=PrivacyClass.USER_PRIVATE,
        retention_class=RetentionClass.EPHEMERAL,
    )


@pytest.mark.parametrize(
    ("kind", "outcome", "expected"),
    [
        (
            ObservationKind.USER_INVOKED,
            ObservationOutcome.NONE,
            {StateDimension.ACTIVATION: 0.25, StateDimension.SOCIAL_PRESENCE: 0.55},
        ),
        (
            ObservationKind.VOICE_LISTENING_STARTED,
            ObservationOutcome.STARTED,
            {StateDimension.ACTIVATION: 0.15, StateDimension.SOCIAL_PRESENCE: 0.30},
        ),
        (
            ObservationKind.REQUEST_STARTED,
            ObservationOutcome.STARTED,
            {StateDimension.ACTIVATION: 0.20, StateDimension.COGNITIVE_LOAD: 0.20},
        ),
        (
            ObservationKind.REQUEST_ACTIVITY,
            ObservationOutcome.NONE,
            {StateDimension.COGNITIVE_LOAD: 0.05},
        ),
        (
            ObservationKind.APPROVAL_REQUIRED,
            ObservationOutcome.STARTED,
            {StateDimension.CAUTION: 0.30, StateDimension.CERTAINTY: -0.15},
        ),
        (
            ObservationKind.REQUEST_FAILED,
            ObservationOutcome.FAILED,
            {
                StateDimension.CERTAINTY: -0.25,
                StateDimension.BLOCKEDNESS: 0.35,
                StateDimension.CAUTION: 0.20,
            },
        ),
        (
            ObservationKind.REQUEST_CANCELLED,
            ObservationOutcome.CANCELLED,
            {StateDimension.COGNITIVE_LOAD: -0.20, StateDimension.ACTIVATION: -0.10},
        ),
        (
            ObservationKind.REQUEST_COMPLETED,
            ObservationOutcome.COMPLETED,
            {StateDimension.COGNITIVE_LOAD: -0.25, StateDimension.BLOCKEDNESS: -0.20},
        ),
        (
            ObservationKind.GOAL_VERIFIED,
            ObservationOutcome.VERIFIED,
            {StateDimension.CERTAINTY: 0.25, StateDimension.BLOCKEDNESS: -0.35},
        ),
        (
            ObservationKind.RUNTIME_DEGRADED,
            ObservationOutcome.FAILED,
            {StateDimension.CAUTION: 0.50, StateDimension.CERTAINTY: -0.40},
        ),
        (
            ObservationKind.RUNTIME_RECOVERED,
            ObservationOutcome.COMPLETED,
            {StateDimension.CAUTION: -0.20, StateDimension.BLOCKEDNESS: -0.20},
        ),
    ],
)
def test_fixed_design_delta_mapping(kind, outcome, expected):
    result = AppraisalReducer().reduce(observation(kind, outcome=outcome), NOW)

    assert dict(result.deltas) == expected
    assert result.confidence == 0.8


def test_medium_risk_tool_adds_caution_without_inventing_an_attention_claim():
    result = AppraisalReducer().reduce(
        observation(ObservationKind.TOOL_STARTED, outcome=ObservationOutcome.STARTED, risk=RiskLevel.MEDIUM),
        NOW,
    )

    assert dict(result.deltas) == {
        StateDimension.COGNITIVE_LOAD: 0.10,
        StateDimension.CAUTION: 0.20,
    }
    assert result.reason_code is ReasonCode.TOOL_STARTED
    assert result.attention_claim is None
    assert [item.kind for item in result.affect_evidence] == [FunctionalAffectKind.CAUTIOUS]


def test_low_risk_tool_does_not_invent_caution():
    result = AppraisalReducer().reduce(
        observation(ObservationKind.TOOL_STARTED, outcome=ObservationOutcome.STARTED, risk=RiskLevel.LOW),
        NOW,
    )

    assert dict(result.deltas) == {StateDimension.COGNITIVE_LOAD: 0.10}
    assert result.attention_claim is None
    assert result.affect_evidence == ()


def test_approval_denial_is_operational_blocking_not_psychological_narrative():
    result = AppraisalReducer().reduce(
        observation(ObservationKind.APPROVAL_RESOLVED, outcome=ObservationOutcome.DENIED),
        NOW,
    )

    assert result.reason_code is ReasonCode.APPROVAL_DENIED
    assert dict(result.deltas) == {
        StateDimension.CAUTION: 0.10,
        StateDimension.BLOCKEDNESS: 0.10,
    }
    assert [item.kind for item in result.affect_evidence] == [FunctionalAffectKind.BLOCKED]


def test_approval_resolution_with_unknown_outcome_fails_closed():
    with pytest.raises(ValueError, match="approved or denied"):
        AppraisalReducer().reduce(observation(ObservationKind.APPROVAL_RESOLVED), NOW)


def test_claim_ttls_and_targets_use_injected_time():
    reducer = AppraisalReducer()
    invoked = reducer.reduce(observation(ObservationKind.USER_INVOKED), NOW)
    listening = reducer.reduce(observation(ObservationKind.VOICE_LISTENING_STARTED), NOW)
    request = reducer.reduce(observation(ObservationKind.REQUEST_STARTED), NOW)
    approval = reducer.reduce(observation(ObservationKind.APPROVAL_REQUIRED), NOW)

    assert (invoked.attention_claim.priority, invoked.attention_claim.expires_at_utc) == (
        55,
        "2026-08-12T10:00:03.000Z",
    )
    assert (listening.attention_claim.priority, listening.attention_claim.expires_at_utc) == (
        85,
        "2026-08-12T10:00:15.000Z",
    )
    assert (request.attention_claim.priority, request.attention_claim.expires_at_utc) == (
        70,
        "2026-08-12T10:01:00.000Z",
    )
    assert (approval.attention_claim.priority, approval.attention_claim.expires_at_utc) == (
        90,
        "2026-08-12T10:02:00.000Z",
    )


def test_high_risk_approval_uses_safety_priority_and_fault_ttl():
    result = AppraisalReducer().reduce(
        observation(
            ObservationKind.APPROVAL_REQUIRED,
            outcome=ObservationOutcome.STARTED,
            risk=RiskLevel.HIGH,
        ),
        NOW,
    )

    assert result.attention_claim is not None
    assert result.attention_claim.priority == 95
    assert result.attention_claim.expires_at_utc == "2026-08-12T10:00:30.000Z"


def test_satisfied_can_only_come_from_goal_verified():
    reducer = AppraisalReducer()
    completed = reducer.reduce(
        observation(ObservationKind.REQUEST_COMPLETED, outcome=ObservationOutcome.COMPLETED),
        NOW,
    )
    verified = reducer.reduce(
        observation(ObservationKind.GOAL_VERIFIED, outcome=ObservationOutcome.VERIFIED),
        NOW,
    )

    assert [item.kind for item in completed.affect_evidence] == [FunctionalAffectKind.RELIEVED]
    assert [item.kind for item in verified.affect_evidence] == [FunctionalAffectKind.SATISFIED]
    assert verified.affect_evidence[0].reason_code is ReasonCode.GOAL_VERIFIED


def test_same_observation_and_injected_time_are_field_for_field_deterministic():
    reducer = AppraisalReducer()
    event = observation(
        ObservationKind.REQUEST_FAILED,
        outcome=ObservationOutcome.FAILED,
        observation_id="obs-repeatable",
    )

    assert reducer.reduce(event, NOW) == reducer.reduce(event, NOW)


def test_missing_attention_identity_fails_closed():
    event = replace(observation(ObservationKind.REQUEST_STARTED), request_id=None)

    with pytest.raises(ValueError, match="target identity"):
        AppraisalReducer().reduce(event, NOW)


def test_derived_ids_remain_bounded_for_maximum_length_observation_id():
    event = observation(
        ObservationKind.GOAL_VERIFIED,
        outcome=ObservationOutcome.VERIFIED,
        observation_id="o" * 256,
    )

    result = AppraisalReducer().reduce(event, NOW)

    assert len(result.affect_evidence[0].evidence_id) <= 256


def test_decay_matches_each_design_baseline_and_half_life():
    for dimension, baseline in HOMEOSTASIS_BASELINES.items():
        half_life = HOMEOSTASIS_HALF_LIVES_SECONDS[dimension]
        initial = 1.0 if baseline < 1.0 else 0.0
        expected = baseline + (initial - baseline) / 2.0
        assert decay_toward_baseline(initial, baseline, half_life, half_life) == pytest.approx(expected)


def test_monotonic_rollback_never_reverses_decay():
    assert elapsed_monotonic_seconds(10.0, 9.0) == 0.0
    assert decay_toward_baseline(0.8, 0.2, 0.0, 30.0) == 0.8


def test_manual_clock_advances_without_sleep_and_utc_can_roll_back_independently():
    clock = ManualClock(NOW, monotonic_seconds=5.0)

    assert clock.advance(2.5) == ClockReading(
        datetime(2026, 8, 12, 10, 0, 2, 500000, tzinfo=timezone.utc),
        7.5,
    )
    clock.set_utc("2026-08-12T09:59:00.000Z")
    assert clock.read().monotonic_seconds == 7.5


@pytest.mark.parametrize(
    "args",
    [
        (1.1, 0.2, 1.0, 30.0),
        (0.8, -0.1, 1.0, 30.0),
        (0.8, 0.2, -1.0, 30.0),
        (0.8, 0.2, 1.0, 0.0),
        (float("nan"), 0.2, 1.0, 30.0),
    ],
)
def test_decay_rejects_unbounded_or_non_finite_input(args):
    with pytest.raises(ValueError):
        decay_toward_baseline(*args)
