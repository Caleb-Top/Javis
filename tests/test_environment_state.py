from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from core.environment.contracts import (
    EnvironmentObservationV1,
    EnvironmentStatus,
    SourceHealth,
    SourceKind,
    SOURCE_RETENTION_CLASS,
)
from core.environment.state import EnvironmentReducer
from core.life.contracts import PrivacyClass


BOOT = "boot-environment-001"
GRANT = "a" * 64
OTHER_GRANT = "b" * 64


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def wire_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def observation(
    clock: Clock,
    *,
    sequence: int = 1,
    observation_id: str | None = None,
    event_id: str | None = None,
    source: SourceKind = SourceKind.DEVICE_HEALTH,
    subject_key: str = "local-device",
    attributes=None,
    ttl: int = 30,
    boot: str = BOOT,
    grant: str = GRANT,
) -> EnvironmentObservationV1:
    if attributes is None:
        attributes = {"cpu_load": 0.2}
    return EnvironmentObservationV1(
        schema_version=1,
        observation_id=observation_id or f"observation-{source.value}-{sequence}",
        runtime_boot_id=boot,
        source_event_id=event_id or f"event-{source.value}-{sequence}",
        source_kind=source,
        subject_kind="device",
        subject_key=subject_key,
        attributes=attributes,
        observed_at_utc=wire_time(clock.value),
        valid_until_utc=wire_time(clock.value + timedelta(seconds=ttl)),
        sequence=sequence,
        confidence=0.9,
        privacy_class=(
            PrivacyClass.RESTRICTED_SYSTEM
            if source in {SourceKind.DEVICE_HEALTH, SourceKind.PROCESS_HEALTH}
            else PrivacyClass.USER_PRIVATE
        ),
        retention_class=SOURCE_RETENTION_CLASS[source],
        grant_id_hash=grant,
        provenance={"adapter": "test"},
    )


def test_initial_snapshot_is_empty_unknown_and_boot_scoped():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)

    snapshot = reducer.snapshot()

    assert snapshot.runtime_boot_id == BOOT
    assert snapshot.revision == 0
    assert snapshot.facts == ()
    assert snapshot.stale_keys == ()
    assert snapshot.permission_revision == 0
    assert set(snapshot.source_health.values()) == {SourceHealth.UNKNOWN}
    assert SourceKind.RAW_MEDIA.value not in snapshot.source_health


def test_reduce_replaces_facts_by_key_and_preserves_other_attributes():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    assert reducer.reduce(
        observation(clock, sequence=1, attributes={"cpu_load": 0.2, "battery_level": 0.8})
    )
    clock.advance(1)
    assert reducer.reduce(
        observation(clock, sequence=2, attributes={"cpu_load": 0.6})
    )

    snapshot = reducer.snapshot()

    values = {fact.attribute: fact.value for fact in snapshot.facts}
    assert values == {"battery_level": 0.8, "cpu_load": 0.6}
    assert snapshot.revision == 2


@pytest.mark.parametrize("duplicate_field", ["observation", "event"])
def test_duplicates_are_idempotent_no_ops(duplicate_field):
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    first = observation(clock, sequence=1)
    assert reducer.reduce(first)
    clock.advance(1)
    duplicate = observation(
        clock,
        sequence=2,
        observation_id=(first.observation_id if duplicate_field == "observation" else "new-observation"),
        event_id=(first.source_event_id if duplicate_field == "event" else "new-event"),
        attributes={"cpu_load": 0.9},
    )

    assert reducer.reduce(duplicate) is False
    assert reducer.snapshot().revision == 1
    assert reducer.snapshot().facts[0].value == 0.2


def test_out_of_order_sequence_is_ignored_per_source():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    assert reducer.reduce(observation(clock, sequence=8))
    assert reducer.reduce(
        observation(clock, sequence=7, observation_id="older", event_id="older-event")
    ) is False
    assert reducer.snapshot().revision == 1


def test_sequences_are_independent_between_sources():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    assert reducer.reduce(observation(clock, sequence=1))
    assert reducer.reduce(
        observation(
            clock,
            sequence=1,
            source=SourceKind.FOREGROUND_APP,
            subject_key="foreground",
            attributes={"app_category": "development"},
            ttl=5,
        )
    )
    assert reducer.fact_count == 2


def test_wrong_boot_is_rejected_without_mutation():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    with pytest.raises(ValueError, match="runtime_boot_id"):
        reducer.reduce(observation(clock, boot="previous-boot"))
    assert reducer.revision == 0


def test_already_expired_observation_is_rejected():
    clock = Clock()
    item = observation(clock, ttl=1)
    clock.advance(2)
    reducer = EnvironmentReducer(BOOT, now=clock)
    with pytest.raises(ValueError, match="expired"):
        reducer.reduce(item)
    assert reducer.fact_count == 0


def test_ttl_moves_fresh_to_stale_then_unknown():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock, stale_ttl_seconds=4)
    reducer.reduce(observation(clock, ttl=2))
    key = ("device", "local-device", "cpu_load")
    assert reducer.snapshot().status_for(*key) is EnvironmentStatus.FRESH

    clock.advance(2)
    stale = reducer.snapshot()
    assert stale.status_for(*key) is EnvironmentStatus.STALE
    assert stale.facts == ()

    clock.advance(4)
    unknown = reducer.snapshot()
    assert unknown.status_for(*key) is EnvironmentStatus.UNKNOWN
    assert unknown.stale_keys == ()


def test_source_health_changes_and_disabled_source_is_invalidated():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    reducer.reduce(observation(clock))
    assert reducer.set_source_health(SourceKind.DEVICE_HEALTH, SourceHealth.DEGRADED)
    assert reducer.snapshot().source_health[SourceKind.DEVICE_HEALTH.value] is SourceHealth.DEGRADED

    assert reducer.set_source_health(SourceKind.DEVICE_HEALTH, SourceHealth.DISABLED)
    snapshot = reducer.snapshot()
    assert snapshot.facts == ()
    assert snapshot.stale_keys == (("device", "local-device", "cpu_load"),)


def test_raw_media_cannot_be_added_to_source_health():
    reducer = EnvironmentReducer(BOOT, now=Clock())
    with pytest.raises(ValueError, match="raw media"):
        reducer.set_source_health(SourceKind.RAW_MEDIA, SourceHealth.HEALTHY)


def test_permission_revision_invalidates_only_matching_grant():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    reducer.reduce(observation(clock, sequence=1, subject_key="first", grant=GRANT))
    clock.advance(1)
    reducer.reduce(observation(clock, sequence=2, subject_key="second", grant=OTHER_GRANT))

    assert reducer.set_permission_revision(
        1, invalidated_grant_id_hashes=(GRANT,)
    )
    snapshot = reducer.snapshot()

    assert snapshot.permission_revision == 1
    assert [fact.subject_key for fact in snapshot.facts] == ["second"]
    assert snapshot.status_for("device", "first", "cpu_load") is EnvironmentStatus.STALE


def test_permission_revision_is_monotonic_and_idempotent():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    assert reducer.set_permission_revision(3)
    revision = reducer.revision
    assert reducer.set_permission_revision(2) is False
    assert reducer.set_permission_revision(3) is False
    assert reducer.revision == revision


def test_invalidated_source_removes_only_that_source():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    reducer.reduce(observation(clock, sequence=1))
    reducer.reduce(
        observation(
            clock,
            sequence=1,
            source=SourceKind.PROCESS_HEALTH,
            subject_key="javis",
            attributes={"running": True},
        )
    )
    reducer.set_permission_revision(
        1, invalidated_sources=(SourceKind.PROCESS_HEALTH,)
    )
    snapshot = reducer.snapshot()
    assert [fact.source_kind for fact in snapshot.facts] == [SourceKind.DEVICE_HEALTH]


def test_fact_capacity_is_bounded_and_eviction_becomes_stale():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock, max_facts=2)
    reducer.reduce(
        observation(
            clock,
            attributes={"cpu_load": 0.2, "battery_level": 0.8, "memory_load": 0.3},
        )
    )
    snapshot = reducer.snapshot()
    assert len(snapshot.facts) == 2
    assert len(snapshot.stale_keys) == 1


def test_stale_key_capacity_is_bounded():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock, max_facts=1, max_stale_keys=2)
    reducer.reduce(
        observation(
            clock,
            attributes={
                "cpu_load": 0.2,
                "battery_level": 0.8,
                "memory_load": 0.3,
                "disk_load": 0.4,
            },
        )
    )
    assert reducer.stale_count == 2


def test_seen_identifier_memory_is_bounded():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock, seen_capacity=2)
    for sequence in range(1, 5):
        reducer.reduce(observation(clock, sequence=sequence))
        clock.advance(0.1)
    assert len(reducer._seen_observation_ids) == 2
    assert len(reducer._seen_source_event_ids) == 2


def test_snapshot_is_deterministic_and_immutable():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    reducer.reduce(observation(clock, attributes={"memory_load": 0.3, "cpu_load": 0.2}))
    snapshot = reducer.snapshot()
    assert [fact.attribute for fact in snapshot.facts] == ["cpu_load", "memory_load"]
    with pytest.raises(FrozenInstanceError):
        snapshot.revision = 10


def test_reset_drops_volatile_state_without_changing_boot():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    reducer.reduce(observation(clock))
    assert reducer.reset()
    snapshot = reducer.snapshot()
    assert snapshot.runtime_boot_id == BOOT
    assert snapshot.facts == ()
    assert snapshot.stale_keys == ()


def test_concurrent_duplicate_reduction_has_one_winner():
    clock = Clock()
    reducer = EnvironmentReducer(BOOT, now=clock)
    item = observation(clock)
    barrier = threading.Barrier(8)
    results = []

    def run() -> None:
        barrier.wait()
        results.append(reducer.reduce(item))

    threads = [threading.Thread(target=run) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert results.count(False) == 7


@pytest.mark.parametrize("bad_clock", [lambda: float("nan"), lambda: object(), lambda: datetime.now()])
def test_invalid_clock_fails_closed(bad_clock):
    reducer = EnvironmentReducer(BOOT, now=bad_clock)
    with pytest.raises(RuntimeError, match="clock"):
        reducer.snapshot()
