from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from core.environment.contracts import (
    EnvironmentObservationV1,
    SourceHealth,
    SourceKind,
    SOURCE_RETENTION_CLASS,
)
from core.environment.service import EnvironmentService
from core.environment.state import EnvironmentReducer
from core.life.contracts import PrivacyClass


BOOT = "boot-environment-service"
GRANT = "c" * 64


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)

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
    boot: str = BOOT,
    value: float = 0.2,
    event_suffix: str | None = None,
) -> EnvironmentObservationV1:
    suffix = event_suffix or str(sequence)
    return EnvironmentObservationV1(
        schema_version=1,
        observation_id=f"observation-{suffix}",
        runtime_boot_id=boot,
        source_event_id=f"source-event-{suffix}",
        source_kind=SourceKind.DEVICE_HEALTH,
        subject_kind="device",
        subject_key="local-device",
        attributes={"cpu_load": value},
        observed_at_utc=wire_time(clock.value),
        valid_until_utc=wire_time(clock.value + timedelta(seconds=30)),
        sequence=sequence,
        confidence=0.9,
        privacy_class=PrivacyClass.RESTRICTED_SYSTEM,
        retention_class=SOURCE_RETENTION_CLASS[SourceKind.DEVICE_HEALTH],
        grant_id_hash=GRANT,
        provenance={"adapter": "test"},
    )


def running_service(clock: Clock, **kwargs) -> EnvironmentService:
    service = EnvironmentService(BOOT, now=clock, **kwargs)
    assert service.start()
    return service


def test_start_and_stop_are_idempotent():
    clock = Clock()
    service = EnvironmentService(BOOT, now=clock)
    assert service.start()
    assert service.start() is False
    assert service.stop()
    assert service.stop() is False
    assert service.status.state == "stopped"


def test_synchronous_ingest_updates_snapshot():
    clock = Clock()
    service = running_service(clock)
    try:
        assert service.ingest(observation(clock))
        snapshot = service.snapshot()
        assert snapshot.revision == 1
        assert snapshot.facts[0].value == 0.2
        assert snapshot.source_health[SourceKind.DEVICE_HEALTH.value] is SourceHealth.HEALTHY
    finally:
        service.stop()


def test_nonblocking_submit_is_visible_after_snapshot_barrier():
    clock = Clock()
    service = running_service(clock)
    try:
        assert service.submit_observation(observation(clock))
        assert service.snapshot().facts[0].attribute == "cpu_load"
    finally:
        service.stop()


def test_duplicate_ingest_is_a_fail_soft_no_op():
    clock = Clock()
    service = running_service(clock)
    item = observation(clock)
    try:
        assert service.ingest(item)
        assert service.ingest(item) is False
        assert service.snapshot().revision == 1
        assert service.status.failure_count == 0
    finally:
        service.stop()


def test_invalid_command_does_not_escape_or_stop_worker():
    clock = Clock()
    service = running_service(clock)
    try:
        assert service.ingest(object()) is False
        assert service.status.failure_count == 1
        assert service.status.last_error_code == "TypeError"
        assert service.ingest(observation(clock, event_suffix="valid"))
        assert service.snapshot().facts
    finally:
        service.stop()


def test_wrong_boot_degrades_only_environment_source():
    clock = Clock()
    service = running_service(clock)
    unrelated_calls = []
    try:
        assert service.ingest(observation(clock, boot="old-boot")) is False
        unrelated_calls.append("conversation-still-running")
        snapshot = service.snapshot()
        assert snapshot.facts == ()
        assert snapshot.source_health[SourceKind.DEVICE_HEALTH.value] is SourceHealth.DEGRADED
        assert unrelated_calls == ["conversation-still-running"]
    finally:
        service.stop()


def test_source_health_and_permission_commands_use_writer_thread():
    clock = Clock()
    service = running_service(clock)
    try:
        assert service.ingest(observation(clock))
        assert service.set_source_health(SourceKind.DEVICE_HEALTH, SourceHealth.DEGRADED)
        assert service.set_permission_revision(
            1, invalidated_grant_id_hashes=(GRANT,)
        )
        snapshot = service.snapshot()
        assert snapshot.permission_revision == 1
        assert snapshot.facts == ()
        assert snapshot.stale_keys
        assert snapshot.source_health[SourceKind.DEVICE_HEALTH.value] is SourceHealth.DEGRADED
    finally:
        service.stop()


class BlockingReducer(EnvironmentReducer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def reduce(self, item):
        self.entered.set()
        self.release.wait(2)
        return super().reduce(item)


def test_bounded_queue_rejects_overflow_without_blocking():
    clock = Clock()
    reducer = BlockingReducer(BOOT, now=clock)
    service = EnvironmentService(BOOT, reducer=reducer, queue_capacity=1)
    assert service.start()
    try:
        assert service.submit_observation(observation(clock, sequence=1))
        assert reducer.entered.wait(1)
        assert service.submit_observation(observation(clock, sequence=2))
        started = time.monotonic()
        assert service.submit_observation(observation(clock, sequence=3)) is False
        assert time.monotonic() - started < 0.1
        assert service.status.rejected_commands >= 1
    finally:
        reducer.release.set()
        service.stop()


def test_synchronous_timeout_returns_without_propagating_failure():
    clock = Clock()
    reducer = BlockingReducer(BOOT, now=clock)
    service = EnvironmentService(BOOT, reducer=reducer)
    assert service.start()
    try:
        started = time.monotonic()
        assert service.ingest(observation(clock), timeout=0.01) is False
        assert time.monotonic() - started < 0.2
    finally:
        reducer.release.set()
        service.stop()


def test_snapshot_after_stop_returns_last_immutable_value():
    clock = Clock()
    service = running_service(clock)
    assert service.ingest(observation(clock))
    before = service.snapshot()
    assert service.stop()
    assert service.snapshot(timeout=0) is before


def test_submit_before_start_and_after_stop_is_rejected():
    clock = Clock()
    service = EnvironmentService(BOOT, now=clock)
    item = observation(clock)
    assert service.submit_observation(item) is False
    assert service.start()
    assert service.stop()
    assert service.submit_observation(item) is False


def test_stop_without_drain_unblocks_queued_synchronous_call():
    clock = Clock()
    reducer = BlockingReducer(BOOT, now=clock)
    service = EnvironmentService(BOOT, reducer=reducer, queue_capacity=4)
    assert service.start()
    assert service.submit_observation(observation(clock, sequence=1))
    assert reducer.entered.wait(1)
    result = []

    def call() -> None:
        result.append(service.ingest(observation(clock, sequence=2), timeout=1))

    caller = threading.Thread(target=call)
    caller.start()
    time.sleep(0.02)
    reducer.release.set()
    assert service.stop(drain=False)
    caller.join(1)
    assert result == [False]


def test_reset_discards_snapshot_facts():
    clock = Clock()
    service = running_service(clock)
    try:
        assert service.ingest(observation(clock))
        assert service.reset()
        assert service.snapshot().facts == ()
    finally:
        service.stop()


def test_service_retains_contracts_only_and_has_no_storage_path():
    clock = Clock()
    service = running_service(clock)
    try:
        assert service.ingest(observation(clock))
        snapshot = service.snapshot()
        assert not hasattr(service, "data_root")
        assert not hasattr(service, "storage_path")
        assert "payload" not in snapshot.to_dict()
        assert "raw" not in repr(snapshot).lower()
    finally:
        service.stop()


def test_multiple_producers_are_serialized_by_one_writer():
    clock = Clock()
    service = running_service(clock, queue_capacity=64)
    try:
        results = []

        def produce(sequence: int) -> None:
            results.append(
                service.submit_observation(
                    observation(clock, sequence=sequence, event_suffix=str(sequence))
                )
            )

        threads = [threading.Thread(target=produce, args=(sequence,)) for sequence in range(1, 17)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        snapshot = service.snapshot()
        assert all(results)
        assert len(snapshot.facts) == 1
        assert service.status.failure_count == 0
    finally:
        service.stop()
