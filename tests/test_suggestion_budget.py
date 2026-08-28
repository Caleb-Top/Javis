from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from core.intention.contracts import QuietHoursV1, SuggestionBudgetState
from core.intention.store import IntentionStore
from core.intention.suggestions import (
    DEFAULT_CATEGORY_MAX_PRESENTATIONS,
    DEFAULT_COOLDOWN,
    DEFAULT_GLOBAL_MAX_PRESENTATIONS,
    DEFAULT_REJECTION_SUPPRESSION,
    SuggestionBudgetError,
    SuggestionBudgetGate,
    SuggestionBudgetPolicy,
    is_in_quiet_hours,
    local_window_bounds,
)


OWNER = "subject-owner"


def _gate(
    store: IntentionStore,
    clock: list[datetime],
    *,
    policy: SuggestionBudgetPolicy | None = None,
) -> SuggestionBudgetGate:
    return SuggestionBudgetGate(
        store,
        javis_identity_id="identity-1",
        instance_id="instance-1",
        runtime_boot_id="boot-1",
        policy=policy or SuggestionBudgetPolicy(timezone_id="Asia/Shanghai"),
        now=lambda: clock[0],
    )


@pytest.fixture
def clock() -> list[datetime]:
    return [datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)]


@pytest.fixture
def store(tmp_path):
    value = IntentionStore(tmp_path)
    try:
        yield value
    finally:
        value.close()


def test_defaults_are_conservative_and_candidate_evaluation_does_not_consume(
    store, clock
):
    gate = _gate(store, clock)

    decision = gate.evaluate(
        OWNER,
        "workflow",
        candidate_id="candidate-1",
        presented_intention_id="intention-1",
        candidate_metadata={"relationship_level": 999, "emotion": "urgent"},
    )

    assert DEFAULT_GLOBAL_MAX_PRESENTATIONS == 2
    assert DEFAULT_CATEGORY_MAX_PRESENTATIONS == 1
    assert DEFAULT_COOLDOWN == timedelta(hours=4)
    assert DEFAULT_REJECTION_SUPPRESSION == timedelta(days=7)
    assert decision.allowed
    assert not decision.consumed
    assert decision.budget.presentations_used == 0
    assert decision.budget.max_presentations == 1
    assert decision.budget.quiet_hours.starts_at_local == "22:00"
    assert decision.budget.quiet_hours.ends_at_local == "08:00"


def test_default_category_and_global_windows_limit_non_safety_presentations(
    store, clock
):
    gate = _gate(
        store,
        clock,
        policy=SuggestionBudgetPolicy(
            timezone_id="Asia/Shanghai",
            cooldown=timedelta(0),
        ),
    )

    first = gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-workflow-1",
        presented_intention_id="intention-workflow-1",
        idempotency_key="consume-workflow-1",
    )
    same_category = gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-workflow-2",
        presented_intention_id="intention-workflow-2",
        idempotency_key="consume-workflow-2",
    )
    second = gate.consume(
        OWNER,
        "reminder",
        candidate_id="candidate-reminder-1",
        presented_intention_id="intention-reminder-1",
        idempotency_key="consume-reminder-1",
    )
    global_third = gate.consume(
        OWNER,
        "maintenance",
        candidate_id="candidate-maintenance-1",
        presented_intention_id="intention-maintenance-1",
        idempotency_key="consume-maintenance-1",
    )

    assert first.allowed and first.consumed
    assert not same_category.allowed
    assert same_category.reason_code == "category_budget_exhausted"
    assert second.allowed and second.consumed
    assert not global_third.allowed
    assert global_third.reason_code == "global_budget_exhausted"


def test_quiet_hours_use_timezone_and_exact_half_open_boundaries(store, clock):
    gate = _gate(store, clock)
    clock[0] = datetime(2026, 8, 20, 13, 59, 59, 999000, tzinfo=timezone.utc)

    before = gate.evaluate(
        OWNER,
        "workflow",
        candidate_id="candidate-before-quiet",
        presented_intention_id="intention-before-quiet",
    )
    clock[0] = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    at_start = gate.evaluate(
        OWNER,
        "reminder",
        candidate_id="candidate-at-start",
        presented_intention_id="intention-at-start",
    )
    clock[0] = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
    at_end = gate.evaluate(
        OWNER,
        "reminder",
        candidate_id="candidate-at-end",
        presented_intention_id="intention-at-end",
    )

    assert before.allowed
    assert not at_start.allowed
    assert at_start.reason_code == "quiet_hours"
    assert at_start.budget.state is SuggestionBudgetState.QUIET
    assert at_end.allowed


def test_local_calendar_windows_are_23_and_25_hours_across_dst():
    spring_start, spring_end = local_window_bounds(
        datetime(2026, 3, 8, 12, tzinfo=timezone.utc),
        "America/New_York",
    )
    fall_start, fall_end = local_window_bounds(
        datetime(2026, 11, 1, 12, tzinfo=timezone.utc),
        "America/New_York",
    )

    assert spring_end - spring_start == timedelta(hours=23)
    assert fall_end - fall_start == timedelta(hours=25)

    fallback_quiet = QuietHoursV1("America/New_York", "01:00", "02:00")
    assert is_in_quiet_hours(
        datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), fallback_quiet
    )
    assert is_in_quiet_hours(
        datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), fallback_quiet
    )


def test_cooldown_blocks_until_the_exact_boundary(store, clock):
    gate = _gate(
        store,
        clock,
        policy=SuggestionBudgetPolicy(
            timezone_id="Asia/Shanghai",
            global_max_presentations=10,
            category_max_presentations=3,
        ),
    )
    started = clock[0]
    assert gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-1",
        presented_intention_id="intention-1",
        idempotency_key="cooldown-1",
    ).allowed

    clock[0] = started + timedelta(hours=4) - timedelta(milliseconds=1)
    blocked = gate.evaluate(
        OWNER,
        "workflow",
        candidate_id="candidate-2",
        presented_intention_id="intention-2",
    )
    clock[0] = started + timedelta(hours=4)
    allowed = gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-2",
        presented_intention_id="intention-2",
        idempotency_key="cooldown-2",
    )

    assert not blocked.allowed
    assert blocked.reason_code == "category_cooldown"
    assert allowed.allowed
    assert allowed.budget.presentations_used == 2


def test_rejection_suppresses_category_for_seven_days_across_window_rollovers(
    store, clock
):
    gate = _gate(
        store,
        clock,
        policy=SuggestionBudgetPolicy(
            timezone_id="Asia/Shanghai",
            global_max_presentations=10,
            category_max_presentations=3,
        ),
    )
    rejected_at = clock[0]
    gate.consume(
        OWNER,
        "wellbeing",
        candidate_id="candidate-rejected",
        presented_intention_id="intention-rejected",
        idempotency_key="reject-consume",
    )
    rejected = gate.reject(
        OWNER,
        "wellbeing",
        candidate_id="candidate-rejected",
        idempotency_key="reject-outcome",
    )

    assert rejected.state is SuggestionBudgetState.SUPPRESSED
    assert rejected.suppressed_until_utc == "2026-08-27T04:00:00.000Z"

    clock[0] = rejected_at + timedelta(days=7) - timedelta(milliseconds=1)
    before_end = gate.evaluate(
        OWNER,
        "wellbeing",
        candidate_id="candidate-before-seven-days",
        presented_intention_id="intention-before-seven-days",
    )
    clock[0] = rejected_at + timedelta(days=7)
    at_end = gate.evaluate(
        OWNER,
        "wellbeing",
        candidate_id="candidate-at-seven-days",
        presented_intention_id="intention-at-seven-days",
    )

    assert not before_end.allowed
    assert before_end.reason_code == "user_suppressed"
    assert at_end.allowed


def test_user_pause_blocks_safety_and_resumes_at_exact_boundary(store, clock):
    gate = _gate(store, clock)
    started = clock[0]
    gate.pause(
        OWNER,
        category="safety",
        until=started + timedelta(hours=1),
        idempotency_key="pause-safety",
    )

    blocked = gate.evaluate(
        OWNER,
        "safety",
        candidate_id="safety-paused",
        presented_intention_id="safety-intention-paused",
    )
    clock[0] = started + timedelta(hours=1)
    allowed = gate.evaluate(
        OWNER,
        "safety",
        candidate_id="safety-resumed",
        presented_intention_id="safety-intention-resumed",
    )

    assert not blocked.allowed
    assert blocked.reason_code == "user_suppressed"
    assert allowed.allowed
    assert allowed.count_bypassed


def test_safety_bypasses_only_counts_and_obeys_quiet_delivery_policy(store, clock):
    clock[0] = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    gate = _gate(
        store,
        clock,
        policy=SuggestionBudgetPolicy(
            timezone_id="UTC",
            cooldown=timedelta(0),
        ),
    )
    for category in ("workflow", "reminder"):
        assert gate.consume(
            OWNER,
            category,
            candidate_id=f"candidate-{category}",
            presented_intention_id=f"intention-{category}",
            idempotency_key=f"consume-{category}",
        ).allowed

    safety = gate.consume(
        OWNER,
        "safety",
        candidate_id="safety-after-global-limit",
        presented_intention_id="safety-intention-1",
        idempotency_key="safety-1",
    )
    clock[0] = datetime(2026, 8, 20, 22, 30, tzinfo=timezone.utc)
    quiet_safety = gate.consume(
        OWNER,
        "safety",
        candidate_id="safety-during-quiet",
        presented_intention_id="safety-intention-2",
        idempotency_key="safety-2",
    )

    assert safety.allowed and safety.count_bypassed
    assert quiet_safety.allowed and quiet_safety.visual_allowed
    assert not quiet_safety.sound_allowed
    assert quiet_safety.reason_code == "safety_visual_only"
    assert not quiet_safety.execution_authorized
    assert quiet_safety.authorization is None
    assert not quiet_safety.grants_execution


def test_concurrent_double_consumption_has_exactly_one_winner(store, clock):
    gate = _gate(
        store,
        clock,
        policy=SuggestionBudgetPolicy(
            timezone_id="Asia/Shanghai",
            cooldown=timedelta(0),
        ),
    )
    barrier = threading.Barrier(3)
    results = []

    def consume(index: int) -> None:
        barrier.wait()
        results.append(
            gate.consume(
                OWNER,
                "maintenance",
                candidate_id=f"candidate-{index}",
                presented_intention_id=f"intention-{index}",
                idempotency_key=f"concurrent-{index}",
            )
        )

    threads = [threading.Thread(target=consume, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(result.allowed for result in results) == 1
    assert sum(result.consumed for result in results) == 1
    assert gate.get_budget(OWNER, "maintenance").presentations_used == 1


def test_sender_failure_still_counts_and_same_idempotency_key_replays(store, clock):
    gate = _gate(store, clock)

    def fail_sender(_decision) -> None:
        raise RuntimeError("delivery failed")

    with pytest.raises(RuntimeError, match="delivery failed"):
        gate.present(
            OWNER,
            "workflow",
            candidate_id="candidate-send-failed",
            presented_intention_id="intention-send-failed",
            idempotency_key="send-failed",
            sender=fail_sender,
        )

    replay = gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-send-failed",
        presented_intention_id="intention-send-failed",
        idempotency_key="send-failed",
    )
    restarted_gate = _gate(store, clock)
    durable_replay = restarted_gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-send-failed",
        presented_intention_id="intention-send-failed",
        idempotency_key="send-failed",
    )

    assert replay.allowed and replay.idempotent_replay
    assert durable_replay.allowed and durable_replay.idempotent_replay
    assert gate.get_budget(OWNER, "workflow").presentations_used == 1


def test_idempotency_key_cannot_be_rebound_to_another_candidate(store, clock):
    gate = _gate(store, clock)
    gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-1",
        presented_intention_id="intention-1",
        idempotency_key="same-key",
    )

    with pytest.raises(SuggestionBudgetError, match="idempotency_conflict"):
        gate.consume(
            OWNER,
            "workflow",
            candidate_id="candidate-2",
            presented_intention_id="intention-2",
            idempotency_key="same-key",
        )


def test_candidate_deduplication_survives_category_and_window_changes(store, clock):
    gate = _gate(store, clock)
    gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-stable",
        presented_intention_id="intention-first",
        idempotency_key="dedupe-first",
    )
    clock[0] += timedelta(days=1)

    duplicate = gate.consume(
        OWNER,
        "reminder",
        candidate_id="candidate-stable",
        presented_intention_id="intention-second",
        idempotency_key="dedupe-second",
    )

    assert not duplicate.allowed
    assert duplicate.reason_code == "duplicate_candidate"
    assert duplicate.budget.presentations_used == 0


def test_relationship_emotion_and_spoofed_safety_do_not_raise_budget(store, clock):
    gate = _gate(
        store,
        clock,
        policy=SuggestionBudgetPolicy(
            timezone_id="Asia/Shanghai",
            global_max_presentations=1,
            cooldown=timedelta(0),
        ),
    )
    gate.consume(
        OWNER,
        "workflow",
        candidate_id="candidate-first",
        presented_intention_id="intention-first",
        idempotency_key="influence-first",
    )

    injected = gate.consume(
        OWNER,
        "reminder",
        candidate_id="candidate-injected",
        presented_intention_id="intention-injected",
        idempotency_key="influence-second",
        safety=True,
        candidate_metadata={
            "relationship": "intimate",
            "emotion": "panic",
            "model_urgency": 1_000_000,
        },
        relationship_score=1.0,
        emotional_state="distressed",
    )

    assert not injected.allowed
    assert injected.reason_code == "global_budget_exhausted"
    assert not injected.count_bypassed
    persisted = injected.budget.to_dict()
    assert "relationship" not in persisted
    assert "emotion" not in persisted
