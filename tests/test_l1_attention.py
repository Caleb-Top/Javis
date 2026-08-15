from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.life.l1.attention import AttentionCoordinator
from core.life.l1.contracts import AttentionClaim, AttentionMode


_BASE = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)


def _time(seconds: float) -> str:
    value = _BASE + timedelta(seconds=seconds)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _claim(
    claim_id: str,
    priority: int,
    acquired: float,
    ttl: float,
    *,
    target_kind: str | None = None,
    target_id: str | None = None,
    observation_id: str | None = None,
) -> AttentionClaim:
    return AttentionClaim(
        claim_id=claim_id,
        target_kind=target_kind or {
            100: "user_input",
            95: "safety",
            90: "approval",
            85: "listening",
            70: "request",
            60: "playback",
            55: "presence",
            50: "recovery",
        }[priority],
        target_id=target_id or f"target-{claim_id}",
        priority=priority,
        source_observation_id=observation_id or f"observation-{claim_id}",
        acquired_at_utc=_time(acquired),
        expires_at_utc=_time(acquired + ttl),
        interruptible=True,
    )


@pytest.mark.parametrize(
    ("priority", "ttl", "expected_mode"),
    [
        (100, 5, AttentionMode.PRESENT),
        (95, 30, AttentionMode.RECOVERING),
        (90, 120, AttentionMode.AWAITING_APPROVAL),
        (85, 15, AttentionMode.LISTENING),
        (70, 60, AttentionMode.ENGAGED),
        (55, 3, AttentionMode.PRESENT),
        (50, 10, AttentionMode.RECOVERING),
    ],
)
def test_fixed_policy_priorities_ttls_and_modes(priority, ttl, expected_mode):
    coordinator = AttentionCoordinator(_time(0))

    snapshot = coordinator.apply(_claim(f"p{priority}", priority, 1, ttl))

    assert snapshot.mode is expected_mode
    assert snapshot.priority == priority
    assert snapshot.expires_at_utc == _time(1 + ttl)


def test_playback_ttl_is_duration_plus_two_seconds():
    coordinator = AttentionCoordinator(_time(0))

    snapshot = coordinator.apply(
        _claim("speech", 60, 1, 8),
        playback_duration_seconds=6,
    )

    assert snapshot.mode is AttentionMode.SPEAKING
    with pytest.raises(ValueError, match="duration plus 2 seconds"):
        coordinator.apply(
            _claim("bad-speech", 60, 2, 9),
            playback_duration_seconds=6,
        )


def test_unknown_priority_and_wrong_fixed_ttl_fail_without_mutation():
    coordinator = AttentionCoordinator(_time(0))
    baseline = coordinator.snapshot()

    with pytest.raises(ValueError, match="governed attention priority"):
        coordinator.apply(_claim("unknown", 42, 1, 5, target_kind="request"))
    with pytest.raises(ValueError, match="TTL"):
        coordinator.apply(_claim("wrong-ttl", 70, 1, 59))

    assert coordinator.snapshot() == baseline


def test_higher_priority_claim_preempts_then_expiry_reveals_live_fallback():
    coordinator = AttentionCoordinator(_time(0))
    coordinator.apply(_claim("request", 70, 1, 60), session_id="foreground")
    coordinator.apply(_claim("listening", 85, 2, 15), session_id="foreground")

    assert coordinator.snapshot().target_id == "target-listening"

    snapshot = coordinator.expire(_time(17))

    assert snapshot.mode is AttentionMode.ENGAGED
    assert snapshot.target_id == "target-request"


def test_stale_terminal_closes_only_the_claim_it_owns():
    coordinator = AttentionCoordinator(_time(0))
    coordinator.apply(_claim("playback-old", 60, 1, 20), session_id="session-1")
    coordinator.apply(_claim("playback-new", 60, 2, 20), session_id="session-1")

    snapshot = coordinator.expire(_time(3), claim_id="playback-old")
    untouched = coordinator.expire(_time(4), claim_id="already-gone")

    assert snapshot.target_id == "target-playback-new"
    assert untouched.target_id == "target-playback-new"


def test_terminal_target_cannot_close_another_request_claim():
    coordinator = AttentionCoordinator(_time(0))
    coordinator.apply(
        _claim("request-old", 70, 1, 60, target_id="request-1"),
        session_id="session-1",
    )
    coordinator.apply(
        _claim("request-new", 70, 2, 60, target_id="request-2"),
        session_id="session-1",
    )

    snapshot = coordinator.expire(
        _time(3), target_kind="request", target_id="request-1"
    )

    assert snapshot.target_id == "request-2"


def test_equal_priority_uses_event_time_not_arrival_order():
    coordinator = AttentionCoordinator(_time(0))
    coordinator.apply(_claim("first", 70, 1, 60), session_id="session-1")
    coordinator.apply(_claim("newest", 70, 3, 60), session_id="session-1")
    coordinator.apply(_claim("late-old", 70, 2, 60), session_id="session-1")

    assert coordinator.snapshot().target_id == "target-newest"


def test_newer_renewal_replaces_same_claim_but_stale_replay_does_not():
    coordinator = AttentionCoordinator(_time(0))
    coordinator.apply(_claim("request", 70, 1, 60), session_id="session-1")
    coordinator.apply(
        _claim("request", 70, 5, 60, observation_id="observation-renewed"),
        session_id="session-1",
    )
    coordinator.apply(
        _claim("request", 70, 2, 60, observation_id="observation-stale"),
        session_id="session-1",
    )

    snapshot = coordinator.snapshot()
    assert snapshot.since_utc == _time(5)
    assert snapshot.expires_at_utc == _time(65)
    assert snapshot.source_observation_id == "observation-renewed"


def test_background_work_cannot_steal_foreground_but_user_input_can_switch_it():
    coordinator = AttentionCoordinator(_time(0))
    coordinator.apply(
        _claim("foreground-presence", 55, 1, 3),
        session_id="foreground",
        foreground=True,
    )
    coordinator.apply(
        _claim("background-request", 70, 2, 60),
        session_id="background",
        foreground=False,
    )

    assert coordinator.snapshot().target_id == "target-foreground-presence"

    switched = coordinator.apply(
        _claim("background-input", 100, 2.5, 5),
        session_id="background",
    )

    assert coordinator.foreground_session_id == "background"
    assert switched.target_id == "target-background-input"


def test_safety_claim_may_surface_across_sessions():
    coordinator = AttentionCoordinator(_time(0))
    coordinator.apply(
        _claim("foreground-request", 70, 1, 60),
        session_id="foreground",
        foreground=True,
    )

    snapshot = coordinator.apply(
        _claim("background-safety", 95, 2, 30),
        session_id="background",
        foreground=False,
    )

    assert snapshot.target_id == "target-background-safety"
    assert coordinator.foreground_session_id == "foreground"


def test_snapshot_lazily_expires_and_records_idle_transition_time():
    coordinator = AttentionCoordinator(_time(0))
    coordinator.apply(_claim("presence", 55, 1, 3), session_id="session-1")

    snapshot = coordinator.snapshot(_time(4))

    assert snapshot.mode is AttentionMode.IDLE
    assert snapshot.priority == 0
    assert snapshot.target_id is None
    assert snapshot.expires_at_utc is None
    assert snapshot.since_utc == _time(4)


def test_active_claim_storage_is_bounded_without_dropping_the_winner():
    coordinator = AttentionCoordinator(_time(0), claim_capacity=2)
    coordinator.apply(_claim("recovery", 50, 1, 10))
    coordinator.apply(_claim("presence", 55, 2, 3))
    coordinator.apply(_claim("request", 70, 3, 60))

    assert coordinator.active_claim_count == 2
    assert coordinator.snapshot().target_id == "target-request"
