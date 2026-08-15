from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.life.l1.affect import FunctionalAffectProjector
from core.life.l1.clock import ClockReading
from core.life.l1.contracts import (
    AffectEvidence,
    AppraisalResult,
    AttentionClaim,
    FunctionalAffectKind,
    ReasonCode,
    StateDimension,
)
from core.life.l1.state import BASELINES, HomeostasisReducer


START = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)


def utc(seconds: float = 0.0) -> str:
    value = START + timedelta(seconds=seconds)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def appraisal(
    observation_id: str,
    deltas: dict[StateDimension, float],
    *,
    reason: ReasonCode = ReasonCode.REQUEST_STARTED,
    expires_at_utc: str | None = None,
    evidence: tuple[AffectEvidence, ...] = (),
    attention_claim: AttentionClaim | None = None,
) -> AppraisalResult:
    return AppraisalResult(
        schema_version=1,
        observation_id=observation_id,
        deltas=deltas,
        attention_claim=attention_claim,
        affect_evidence=evidence,
        reason_code=reason,
        confidence=1.0,
        expires_at_utc=expires_at_utc,
    )


def evidence(
    evidence_id: str,
    kind: FunctionalAffectKind,
    reason: ReasonCode,
    *,
    occurred: float = 0.0,
    ttl: float = 10.0,
    intensity: float = 0.6,
    confidence: float = 0.8,
) -> AffectEvidence:
    return AffectEvidence(
        evidence_id=evidence_id,
        kind=kind,
        source_observation_id=f"observation-{evidence_id}",
        reason_code=reason,
        intensity=intensity,
        confidence=confidence,
        occurred_at_utc=utc(occurred),
        valid_until_utc=utc(occurred + ttl),
    )


def values(snapshot) -> dict[str, float]:
    return {
        name: getattr(snapshot, name)
        for name in BASELINES
    }


def test_starts_at_fixed_bounded_baselines() -> None:
    reducer = HomeostasisReducer(utc(), 100)

    snapshot = reducer.snapshot()

    assert snapshot.updated_at_utc == utc()
    assert values(snapshot) == pytest.approx(BASELINES)
    assert all(0.0 <= value <= 1.0 for value in values(snapshot).values())


def test_apply_clamps_deltas_and_deduplicates_observations() -> None:
    reducer = HomeostasisReducer(utc(), 0)
    result = appraisal(
        "observation-1",
        {
            StateDimension.ACTIVATION: 1.0,
            StateDimension.CERTAINTY: -1.0,
            StateDimension.BLOCKEDNESS: 1.0,
        },
    )

    assert reducer.apply(result, utc(), 0)
    assert not reducer.apply(result, utc(), 0)
    snapshot = reducer.snapshot()

    assert snapshot.activation == 1.0
    assert snapshot.certainty == 0.0
    assert snapshot.blockedness == 1.0


def test_decay_is_lazy_deterministic_and_uses_each_dimension_half_life() -> None:
    first = HomeostasisReducer(utc(), 0)
    second = HomeostasisReducer(utc(), 0)
    result = appraisal(
        "observation-decay",
        {
            StateDimension.ACTIVATION: 0.8,
            StateDimension.COGNITIVE_LOAD: 0.8,
            StateDimension.CERTAINTY: 0.5,
        },
    )
    first.apply(result, utc(), 0)
    second.apply(result, utc(), 0)

    snapshot = first.snapshot(utc(30), 30_000)
    replayed = second.snapshot(utc(30), 30_000)

    assert snapshot == replayed
    assert snapshot.activation == pytest.approx(0.60)
    assert snapshot.cognitive_load == pytest.approx(
        0.05 + (0.85 - 0.05) * 2 ** (-30 / 20)
    )
    assert snapshot.certainty == pytest.approx(
        0.50 + (1.0 - 0.50) * 2 ** (-30 / 90)
    )


def test_reducer_and_affect_accept_one_coherent_clock_reading() -> None:
    reading = ClockReading(START, 5.0)
    reducer = HomeostasisReducer(reading)
    projector = FunctionalAffectProjector()
    relieved = evidence(
        "reading-relief",
        FunctionalAffectKind.RELIEVED,
        ReasonCode.RUNTIME_RECOVERED,
    )

    reducer.apply(
        appraisal("reading-appraisal", {StateDimension.ACTIVATION: 0.2}),
        reading,
    )
    projected = projector.apply((relieved,), reading)

    assert reducer.snapshot().activation == pytest.approx(0.4)
    assert projected[0].kind is FunctionalAffectKind.RELIEVED


def test_monotonic_rollback_is_rejected_without_mutating_state() -> None:
    reducer = HomeostasisReducer(utc(), 1_000)
    before = reducer.snapshot()

    with pytest.raises(ValueError, match="monotonic"):
        reducer.snapshot(utc(1), 999)

    assert reducer.snapshot() == before


def test_expired_appraisal_does_not_change_state() -> None:
    reducer = HomeostasisReducer(utc(), 0)
    result = appraisal(
        "observation-expired",
        {StateDimension.CAUTION: 0.5},
        expires_at_utc=utc(1),
    )

    assert not reducer.apply(result, utc(2), 2_000)
    assert reducer.snapshot().caution == pytest.approx(BASELINES["caution"])


def test_semantic_threshold_accumulates_small_changes() -> None:
    reducer = HomeostasisReducer(utc(), 0, semantic_threshold=0.05)

    assert not reducer.apply(
        appraisal("small-1", {StateDimension.ACTIVATION: 0.02}), utc(), 0
    )
    assert not reducer.apply(
        appraisal("small-2", {StateDimension.ACTIVATION: 0.02}), utc(), 0
    )
    assert reducer.apply(
        appraisal("small-3", {StateDimension.ACTIVATION: 0.02}), utc(), 0
    )
    assert not reducer.has_semantic_change()


def test_default_semantic_threshold_requires_change_over_one_hundredth() -> None:
    reducer = HomeostasisReducer(utc(), 0)

    assert not reducer.apply(
        appraisal("below-threshold", {StateDimension.ACTIVATION: 0.009}), utc(), 0
    )
    assert reducer.apply(
        appraisal("over-threshold", {StateDimension.ACTIVATION: 0.002}), utc(), 0
    )


def test_request_activity_is_aggregated_per_request_window() -> None:
    reducer = HomeostasisReducer(utc(), 0)

    def activity(observation_id: str, occurred: float) -> AppraisalResult:
        claim = AttentionClaim(
            claim_id="claim:request:request-1",
            target_kind="request",
            target_id="request-1",
            priority=70,
            source_observation_id=observation_id,
            acquired_at_utc=utc(occurred),
            expires_at_utc=utc(occurred + 60),
            interruptible=True,
        )
        return appraisal(
            observation_id,
            {StateDimension.COGNITIVE_LOAD: 0.05},
            reason=ReasonCode.REQUEST_ACTIVITY,
            attention_claim=claim,
        )

    reducer.apply(activity("activity-1", 0), utc(), 0)
    reducer.apply(activity("activity-2", 0.5), utc(), 0)
    after_window_duplicate = reducer.snapshot().cognitive_load
    reducer.apply(activity("activity-3", 1.001), utc(1.001), 1_001)

    assert after_window_duplicate == pytest.approx(0.10)
    assert reducer.snapshot().cognitive_load > after_window_duplicate


def test_tick_reports_thresholded_decay_without_sleeping() -> None:
    reducer = HomeostasisReducer(utc(), 0, semantic_threshold=0.10)
    reducer.apply(
        appraisal("activation", {StateDimension.ACTIVATION: 0.8}), utc(), 0
    )

    assert reducer.tick(utc(10), 10_000)
    assert not reducer.has_semantic_change()


def test_restart_discards_transient_snapshot_and_returns_to_baseline() -> None:
    running = HomeostasisReducer(utc(), 0)
    running.apply(
        appraisal(
            "elevated",
            {
                StateDimension.ACTIVATION: 0.8,
                StateDimension.CAUTION: 0.8,
                StateDimension.BLOCKEDNESS: 0.8,
            },
        ),
        utc(),
        0,
    )

    restarted = HomeostasisReducer.from_restart(
        utc(5), 0, persisted_snapshot=running.snapshot()
    )

    assert values(restarted.snapshot()) == pytest.approx(BASELINES)


def test_public_snapshot_contains_no_relationship_or_permission_state() -> None:
    reducer = HomeostasisReducer(utc(), 0)

    payload = reducer.snapshot().to_dict()

    assert set(payload) == {"updated_at_utc", *BASELINES}
    assert "relationship" not in payload
    assert "permission" not in payload


def test_affect_projection_requires_live_evidence_and_expires_it() -> None:
    projector = FunctionalAffectProjector()
    cautious = evidence(
        "cautious-1",
        FunctionalAffectKind.CAUTIOUS,
        ReasonCode.APPROVAL_REQUIRED,
        ttl=5,
    )

    projected = projector.apply((cautious,), utc())

    assert len(projected) == 1
    assert projected[0].kind is FunctionalAffectKind.CAUTIOUS
    assert projected[0].evidence_ids == ("cautious-1",)
    assert projector.snapshot(utc(5)) == ()


def test_approval_denial_can_project_appraiser_blocked_evidence() -> None:
    projector = FunctionalAffectProjector()
    denied = evidence(
        "approval-denied",
        FunctionalAffectKind.BLOCKED,
        ReasonCode.APPROVAL_DENIED,
    )

    (projected,) = projector.apply((denied,), utc())

    assert projected.kind is FunctionalAffectKind.BLOCKED


def test_affect_projection_aggregates_without_inflating_intensity() -> None:
    projector = FunctionalAffectProjector()
    weak = evidence(
        "relief-1",
        FunctionalAffectKind.RELIEVED,
        ReasonCode.REQUEST_COMPLETED,
        intensity=0.3,
        confidence=0.6,
        ttl=8,
    )
    strong = evidence(
        "relief-2",
        FunctionalAffectKind.RELIEVED,
        ReasonCode.RUNTIME_RECOVERED,
        intensity=0.7,
        confidence=0.9,
        ttl=12,
    )

    (projected,) = projector.apply((weak, strong), utc())

    assert projected.intensity == 0.7
    assert projected.confidence == 0.9
    assert projected.evidence_ids == ("relief-1", "relief-2")
    assert projected.valid_until_utc == utc(8)


def test_conflicting_evidence_batch_is_rejected_atomically() -> None:
    projector = FunctionalAffectProjector()
    accepted = evidence(
        "accepted",
        FunctionalAffectKind.RELIEVED,
        ReasonCode.RUNTIME_RECOVERED,
    )
    first = evidence(
        "reused",
        FunctionalAffectKind.CAUTIOUS,
        ReasonCode.APPROVAL_REQUIRED,
    )
    conflicting = evidence(
        "reused",
        FunctionalAffectKind.CAUTIOUS,
        ReasonCode.RUNTIME_DEGRADED,
    )

    with pytest.raises(ValueError, match="reused"):
        projector.apply((accepted, first, conflicting), utc())

    assert projector.snapshot(utc()) == ()


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (FunctionalAffectKind.SATISFIED, ReasonCode.REQUEST_COMPLETED),
        (FunctionalAffectKind.BLOCKED, ReasonCode.QUIET_BASELINE),
        (FunctionalAffectKind.CURIOUS, ReasonCode.QUIET_BASELINE),
    ],
)
def test_affect_projection_rejects_unsupported_evidence_bindings(
    kind: FunctionalAffectKind,
    reason: ReasonCode,
) -> None:
    projector = FunctionalAffectProjector()

    if kind is FunctionalAffectKind.SATISFIED:
        with pytest.raises(ValueError):
            candidate = evidence("invalid", kind, reason)
            projector.apply((candidate,), utc())
    else:
        candidate = evidence("invalid", kind, reason)
        with pytest.raises(ValueError, match="evidence"):
            projector.apply((candidate,), utc())


def test_satisfied_requires_goal_verification_and_never_persists_restart() -> None:
    projector = FunctionalAffectProjector()
    verified = evidence(
        "verified-1",
        FunctionalAffectKind.SATISFIED,
        ReasonCode.GOAL_VERIFIED,
    )

    (projected,) = projector.apply((verified,), utc())
    restarted = FunctionalAffectProjector.from_restart(
        persisted_evidence=(verified,)
    )

    assert projected.kind is FunctionalAffectKind.SATISFIED
    assert restarted.snapshot(utc()) == ()
