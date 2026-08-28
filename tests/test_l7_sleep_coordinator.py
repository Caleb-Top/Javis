from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.life.l1.clock import ManualClock
from core.life.l7.contracts import (
    NetworkPolicy,
    SleepJobKind,
    SleepJobStatus,
    SleepPolicyV1,
    SleepRunState,
    canonical_content_hash,
)
from core.life.l7.growth_store import GrowthStore
from core.life.l7.run_store import RunStore
from core.life.l7.sleep import (
    HealthSnapshot,
    IdleSnapshot,
    JobRegistry,
    JobResult,
    NetworkGrant,
    PowerSnapshot,
    SleepCoordinator,
    SleepCoordinatorClosedError,
)


UTC = timezone.utc
NOW = "2026-08-20T01:00:00.000Z"
END = "2027-08-20T01:00:00.000Z"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _signed(value: dict) -> dict:
    result = dict(value)
    result["content_hash"] = canonical_content_hash(result)
    return result


def _policy(**overrides) -> SleepPolicyV1:
    value = {
        "schema_version": 1,
        "policy_id": "policy-1",
        "identity_id": "identity-1",
        "owner_subject_id": "owner-1",
        "revision": 1,
        "enabled": True,
        "approval_id": "approval-1",
        "approved_at_utc": NOW,
        "schedule_mode": "manual",
        "timezone": "UTC",
        "quiet_start_local": None,
        "quiet_end_local": None,
        "idle_min_seconds": 900,
        "require_ac_power": True,
        "minimum_battery_percent": 40,
        "maximum_cpu_percent": 50,
        "minimum_free_bytes": 1_000_000,
        "network_policy": "deny",
        "allowed_job_kinds": ["index_verify"],
        "max_run_seconds": 3_600,
        "max_job_seconds": 900,
        "max_input_records": 100,
        "max_output_bytes": 4_096,
        "wake_on_user_activity": True,
        "effective_at_utc": NOW,
        "expires_at_utc": END,
        "created_at_utc": NOW,
        "privacy_class": "local_internal",
        "retention_class": "operational",
        "provenance": {"adapter": "test", "source_ids": ["evidence-1"]},
    }
    value.update(overrides)
    return SleepPolicyV1.from_dict(_signed(value))


class _Probe:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


def _completed_result(context, **overrides) -> JobResult:
    values = {
        "checkpoint_id": f"checkpoint-{context.job_id}",
        "checkpoint_hash": HASH_A,
        "input_set_hash": HASH_A,
        "output_set_hash": HASH_B,
        "processed_count": 2,
        "skipped_count": 0,
        "output_bytes": 64,
        "source_ids": context.source_checkpoint_ids,
    }
    values.update(overrides)
    return JobResult(**values)


def _coordinator(
    tmp_path: Path,
    *,
    clock: ManualClock | None = None,
    power: PowerSnapshot | None = None,
    idle: IdleSnapshot | None = None,
    health: HealthSnapshot | None = None,
    registry: JobRegistry | None = None,
    run_store: RunStore | None = None,
    growth_store: GrowthStore | None = None,
    cancel_grace_seconds: float = 0.05,
):
    clock = clock or ManualClock(NOW)
    power_probe = _Probe(power or PowerSnapshot(on_ac_power=True, battery_percent=90))
    idle_probe = _Probe(idle or IdleSnapshot(idle_seconds=2_000))
    health_probe = _Probe(
        health
        or HealthSnapshot(
            policy_revision=1,
            approval_id="approval-1",
            cpu_percent=10,
            free_bytes=10_000_000,
            runtime_healthy=True,
            data_root_healthy=True,
        )
    )
    registry = registry or JobRegistry()
    run_store = run_store or RunStore(tmp_path)
    growth_store = growth_store or GrowthStore(tmp_path)
    coordinator = SleepCoordinator(
        clock=clock,
        power_probe=power_probe,
        idle_probe=idle_probe,
        health_probe=health_probe,
        run_store=run_store,
        growth_store=growth_store,
        job_registry=registry,
        instance_id="instance-1",
        cancel_grace_seconds=cancel_grace_seconds,
    )
    return coordinator, power_probe, idle_probe, health_probe, run_store, growth_store


def test_manual_run_persists_checkpoint_and_receipt_before_completion(tmp_path: Path):
    seen = []
    registry = JobRegistry()

    async def run_job(context):
        seen.append(context)
        return _completed_result(context)

    registry.register(SleepJobKind.INDEX_VERIFY, run_job)
    coordinator, _, _, _, store, _ = _coordinator(tmp_path, registry=registry)

    result = asyncio.run(
        coordinator.request_manual(
            _policy(),
            trigger_event_id="manual-request-1",
            source_checkpoint_ids=("evidence-1",),
        )
    )

    assert result.state is SleepRunState.COMPLETED
    assert seen[0].budget.max_records == 100
    assert seen[0].budget.max_output_bytes == 4_096
    assert seen[0].budget.max_seconds == 900
    assert seen[0].source_checkpoint_ids == ("evidence-1",)
    assert seen[0].network_access.allowed is False
    assert store.list_job_checkpoints(result.run_id)[0].checkpoint_id.startswith("checkpoint-")
    assert store.list_job_receipts(result.run_id)[0].status is SleepJobStatus.COMPLETED
    assert [transition.to_state for transition in store.list_transitions(result.run_id)] == [
        SleepRunState.PREFLIGHT,
        SleepRunState.RUNNING,
        SleepRunState.CHECKPOINTING,
        SleepRunState.RUNNING,
        SleepRunState.COMMITTING,
        SleepRunState.COMPLETED,
    ]


@pytest.mark.parametrize(
    ("policy_overrides", "power", "health", "reason"),
    [
        ({"enabled": False}, None, None, "policy_disabled"),
        (
            {
                "effective_at_utc": "2026-08-19T01:00:00.000Z",
                "expires_at_utc": NOW,
            },
            None,
            None,
            "policy_expired",
        ),
        ({}, PowerSnapshot(on_ac_power=False, battery_percent=90), None, "ac_power_required"),
        ({}, PowerSnapshot(on_ac_power=True, battery_percent=20), None, "battery_below_minimum"),
        ({}, None, {"cpu_percent": 80}, "cpu_above_maximum"),
        ({}, None, {"free_bytes": 10}, "disk_below_minimum"),
        ({}, None, {"data_root_healthy": False}, "data_root_unhealthy"),
        ({}, None, {"runtime_healthy": False}, "runtime_unhealthy"),
        ({}, None, {"active_action": True}, "action_active"),
        ({}, None, {"shutdown_requested": True}, "shutdown_requested"),
        ({}, None, {"policy_revision": 2}, "policy_revision_mismatch"),
        ({}, None, {"approval_id": "approval-other"}, "approval_mismatch"),
    ],
)
def test_preflight_skips_without_starting_jobs(
    tmp_path: Path,
    policy_overrides,
    power,
    health,
    reason,
):
    calls = []
    registry = JobRegistry()

    async def run_job(context):
        calls.append(context)
        return _completed_result(context)

    registry.register(SleepJobKind.INDEX_VERIFY, run_job)
    base_health = HealthSnapshot(
        policy_revision=1,
        approval_id="approval-1",
        cpu_percent=10,
        free_bytes=10_000_000,
        runtime_healthy=True,
        data_root_healthy=True,
    )
    health_snapshot = replace(base_health, **health) if health else base_health
    coordinator, _, _, _, store, _ = _coordinator(
        tmp_path,
        power=power,
        health=health_snapshot,
        registry=registry,
    )

    snapshot = asyncio.run(
        coordinator.request_manual(
            _policy(**policy_overrides),
            trigger_event_id="manual-preflight",
            source_checkpoint_ids=("evidence-1",),
        )
    )

    assert snapshot.state is SleepRunState.SKIPPED
    assert store.list_transitions(snapshot.run_id)[-1].reason_code == reason
    assert calls == []


def test_quiet_hours_fold_uses_one_persistent_window_key(tmp_path: Path):
    clock = ManualClock(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    calls = []
    registry = JobRegistry()

    async def run_job(context):
        calls.append(context.job_id)
        return _completed_result(context)

    registry.register(SleepJobKind.INDEX_VERIFY, run_job)
    coordinator, _, _, _, _, _ = _coordinator(tmp_path, clock=clock, registry=registry)
    policy = _policy(
        schedule_mode="quiet_hours",
        timezone="America/New_York",
        quiet_start_local="00:30",
        quiet_end_local="03:00",
    )

    first = asyncio.run(
        coordinator.tick(
            policy,
            trigger_event_id="timer-before-fold",
            source_checkpoint_ids=("evidence-1",),
        )
    )
    clock.advance(3_600)
    replay = asyncio.run(
        coordinator.tick(
            policy,
            trigger_event_id="timer-after-fold",
            source_checkpoint_ids=("evidence-1",),
        )
    )

    assert first is not None
    assert replay is not None
    assert replay.run_id == first.run_id
    assert replay.header.idempotency_key == first.header.idempotency_key
    assert len(calls) == 1


def test_idle_timer_waits_for_the_configured_idle_window(tmp_path: Path):
    registry = JobRegistry()
    registry.register(SleepJobKind.INDEX_VERIFY, lambda context: _completed_result(context))
    coordinator, _, idle, _, _, _ = _coordinator(
        tmp_path,
        idle=IdleSnapshot(idle_seconds=899),
        registry=registry,
    )
    policy = _policy(schedule_mode="idle")

    assert asyncio.run(coordinator.tick(policy, trigger_event_id="idle-episode-1")) is None
    idle.value = IdleSnapshot(idle_seconds=900)
    result = asyncio.run(
        coordinator.tick(
            policy,
            trigger_event_id="idle-episode-1",
            source_checkpoint_ids=("evidence-1",),
        )
    )
    assert result is not None
    assert result.state is SleepRunState.COMPLETED


def test_registry_is_six_kind_allowlisted_and_network_is_denied_by_default(tmp_path: Path):
    registry = JobRegistry()
    with pytest.raises(ValueError, match="allowlist"):
        registry.register("model_download", lambda context: None)
    with pytest.raises(ValueError, match="revalidation"):
        registry.register(SleepJobKind.SUMMARY_BUILD, lambda context: None, requires_network=True)

    seen = []

    async def revalidate(context):
        seen.append(context.network_access)
        return _completed_result(context)

    registry.register(
        SleepJobKind.ARTIFACT_REVALIDATE,
        revalidate,
        requires_network=True,
        revalidation_adapter=True,
    )
    coordinator, _, _, _, _, _ = _coordinator(tmp_path, registry=registry)
    policy = _policy(
        network_policy="explicit_job_grants",
        allowed_job_kinds=["artifact_revalidate"],
    )
    grant = NetworkGrant(
        grant_id="grant-1",
        job_kind=SleepJobKind.ARTIFACT_REVALIDATE,
        expires_at_utc=END,
    )

    result = asyncio.run(
        coordinator.request_manual(
            policy,
            trigger_event_id="manual-network",
            source_checkpoint_ids=("evidence-1",),
            network_grants=(grant,),
        )
    )

    assert result.state is SleepRunState.COMPLETED
    assert seen[0].allowed is True
    assert seen[0].grant_id == "grant-1"


@pytest.mark.parametrize(
    ("signal", "reason"),
    [
        ("notify_user_activity", "user_activity"),
        ("notify_playback_started", "playback_started"),
        ("notify_action_started", "action_started"),
        ("begin_shutdown", "shutdown_requested"),
    ],
)
def test_activity_action_playback_and_shutdown_cancel_with_a_receipt(
    tmp_path: Path,
    signal: str,
    reason: str,
):
    async def exercise():
        started = asyncio.Event()
        registry = JobRegistry()

        async def wait_for_cancel(context):
            started.set()
            await context.cancellation.wait()
            return _completed_result(context)

        registry.register(SleepJobKind.INDEX_VERIFY, wait_for_cancel)
        coordinator, _, _, _, store, _ = _coordinator(tmp_path, registry=registry)
        task = asyncio.create_task(
            coordinator.request_manual(
                _policy(),
                trigger_event_id=f"manual-{signal}",
                source_checkpoint_ids=("evidence-1",),
            )
        )
        await started.wait()
        getattr(coordinator, signal)()
        snapshot = await asyncio.wait_for(task, timeout=1)
        receipt = store.list_job_receipts(snapshot.run_id)[0]
        return snapshot, receipt, store.list_transitions(snapshot.run_id)

    snapshot, receipt, transitions = asyncio.run(exercise())
    assert snapshot.state is SleepRunState.CANCELLED
    assert receipt.status is SleepJobStatus.CANCELLED
    assert receipt.reason_code == reason
    assert [transition.to_state for transition in transitions][-2:] == [
        SleepRunState.CANCELLING,
        SleepRunState.CANCELLED,
    ]


def test_non_cooperative_job_is_force_cancelled_after_bounded_grace(tmp_path: Path):
    async def exercise():
        started = asyncio.Event()
        never = asyncio.Event()
        registry = JobRegistry()

        async def ignore_cancel(context):
            started.set()
            await never.wait()
            return _completed_result(context)

        registry.register(SleepJobKind.INDEX_VERIFY, ignore_cancel)
        coordinator, _, _, _, _, _ = _coordinator(
            tmp_path,
            registry=registry,
            cancel_grace_seconds=0.01,
        )
        task = asyncio.create_task(
            coordinator.request_manual(
                _policy(),
                trigger_event_id="manual-forced-cancel",
                source_checkpoint_ids=("evidence-1",),
            )
        )
        await started.wait()
        coordinator.notify_action_started()
        return await asyncio.wait_for(task, timeout=0.5)

    assert asyncio.run(exercise()).state is SleepRunState.CANCELLED


def test_elapsed_job_budget_produces_timeout_receipt_before_terminal_state(tmp_path: Path):
    clock = ManualClock(NOW)
    registry = JobRegistry()

    async def exceed_budget(context):
        clock.advance(2)
        return _completed_result(context)

    registry.register(SleepJobKind.INDEX_VERIFY, exceed_budget)
    coordinator, _, _, _, store, _ = _coordinator(tmp_path, clock=clock, registry=registry)
    snapshot = asyncio.run(
        coordinator.request_manual(
            _policy(max_run_seconds=1, max_job_seconds=1),
            trigger_event_id="manual-timeout",
            source_checkpoint_ids=("evidence-1",),
        )
    )

    assert snapshot.state is SleepRunState.TIMED_OUT
    assert store.list_job_receipts(snapshot.run_id)[0].status is SleepJobStatus.TIMED_OUT
    assert store.list_transitions(snapshot.run_id)[-1].receipt_ids


def test_partial_receipt_commit_fails_run_without_claiming_job_completion(tmp_path: Path):
    registry = JobRegistry()
    registry.register(SleepJobKind.INDEX_VERIFY, lambda context: _completed_result(context))
    coordinator, _, _, _, store, _ = _coordinator(tmp_path, registry=registry)

    def fail_receipt(*args, **kwargs):
        raise RuntimeError("receipt storage unavailable")

    store.record_job_receipt = fail_receipt
    snapshot = asyncio.run(
        coordinator.request_manual(
            _policy(),
            trigger_event_id="manual-partial-receipt",
            source_checkpoint_ids=("evidence-1",),
        )
    )

    assert snapshot.state is SleepRunState.FAILED
    assert len(store.list_job_checkpoints(snapshot.run_id)) == 1
    assert store.list_job_receipts(snapshot.run_id) == ()
    assert store.list_transitions(snapshot.run_id)[-1].reason_code == "receipt_commit_failed"


def test_summary_output_cannot_invent_evidence_references(tmp_path: Path):
    registry = JobRegistry()

    async def invent_evidence(context):
        return _completed_result(context, source_ids=("invented-fact",))

    registry.register(SleepJobKind.SUMMARY_BUILD, invent_evidence)
    coordinator, _, _, _, store, _ = _coordinator(tmp_path, registry=registry)
    snapshot = asyncio.run(
        coordinator.request_manual(
            _policy(allowed_job_kinds=["summary_build"]),
            trigger_event_id="manual-summary",
            source_checkpoint_ids=("evidence-1",),
        )
    )

    assert snapshot.state is SleepRunState.FAILED
    receipt = store.list_job_receipts(snapshot.run_id)[0]
    assert receipt.status is SleepJobStatus.FAILED
    assert receipt.error_code == "evidence_reference_invalid"


def test_crash_replay_resumes_interrupted_run_from_source_checkpoint(tmp_path: Path):
    async def crash_running_run(first: SleepCoordinator):
        started = asyncio.Event()

        async def suspend(context):
            started.set()
            await asyncio.Event().wait()

        first.job_registry.replace(SleepJobKind.INDEX_VERIFY, suspend)
        task = asyncio.create_task(
            first.request_manual(
                _policy(),
                trigger_event_id="manual-crash",
                source_checkpoint_ids=("evidence-1",),
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    first_registry = JobRegistry()
    first_registry.register(SleepJobKind.INDEX_VERIFY, lambda context: _completed_result(context))
    first, _, _, _, first_store, first_growth = _coordinator(
        tmp_path,
        registry=first_registry,
    )
    asyncio.run(crash_running_run(first))
    run_id = first_store.list_runs(limit=1)[0].run_id
    assert first_store.get_run(run_id).state is SleepRunState.RUNNING
    first_store.close()
    first_growth.close()

    replay_registry = JobRegistry()
    calls = []

    async def replay(context):
        calls.append(context.source_checkpoint_ids)
        return _completed_result(context)

    replay_registry.register(SleepJobKind.INDEX_VERIFY, replay)
    reopened_run_store = RunStore(tmp_path)
    reopened_growth_store = GrowthStore(tmp_path)
    second, _, _, _, _, _ = _coordinator(
        tmp_path,
        registry=replay_registry,
        run_store=reopened_run_store,
        growth_store=reopened_growth_store,
    )

    recovered = asyncio.run(second.resume_interrupted(_policy()))

    assert len(recovered) == 1
    assert recovered[0].run_id == run_id
    assert recovered[0].state is SleepRunState.COMPLETED
    assert calls == [("evidence-1",)]
    transitions = reopened_run_store.list_transitions(run_id)
    assert SleepRunState.INTERRUPTED in [transition.to_state for transition in transitions]
    assert any(
        transition.from_state is SleepRunState.INTERRUPTED
        and transition.to_state is SleepRunState.PREFLIGHT
        for transition in transitions
    )


def test_crash_after_checkpoint_reuses_it_as_replay_input(tmp_path: Path):
    class SimulatedCrash(BaseException):
        pass

    first_registry = JobRegistry()
    first_registry.register(SleepJobKind.INDEX_VERIFY, lambda context: _completed_result(context))
    first, _, _, _, first_store, first_growth = _coordinator(
        tmp_path,
        registry=first_registry,
    )

    def crash_before_receipt(*args, **kwargs):
        raise SimulatedCrash()

    first_store.record_job_receipt = crash_before_receipt
    with pytest.raises(SimulatedCrash):
        asyncio.run(
            first.request_manual(
                _policy(),
                trigger_event_id="manual-checkpoint-crash",
                source_checkpoint_ids=("evidence-1",),
            )
        )
    interrupted_run = first_store.list_runs(limit=1)[0]
    checkpoint = first_store.list_job_checkpoints(interrupted_run.run_id)[0]
    assert interrupted_run.state is SleepRunState.CHECKPOINTING
    first_store.close()
    first_growth.close()

    replay_inputs = []
    replay_registry = JobRegistry()

    async def replay(context):
        replay_inputs.append(context.source_checkpoint_ids)
        return _completed_result(context)

    replay_registry.register(SleepJobKind.INDEX_VERIFY, replay)
    reopened_run_store = RunStore(tmp_path)
    reopened_growth_store = GrowthStore(tmp_path)
    second, _, _, _, _, _ = _coordinator(
        tmp_path,
        registry=replay_registry,
        run_store=reopened_run_store,
        growth_store=reopened_growth_store,
    )

    recovered = asyncio.run(second.resume_interrupted(_policy()))

    assert recovered[0].state is SleepRunState.COMPLETED
    assert replay_inputs == [("evidence-1", checkpoint.checkpoint_id)]
    assert len(reopened_run_store.list_job_checkpoints(interrupted_run.run_id)) == 1


def test_shutdown_rejects_new_runs_after_current_job_is_cancelled(tmp_path: Path):
    registry = JobRegistry()
    registry.register(SleepJobKind.INDEX_VERIFY, lambda context: _completed_result(context))
    coordinator, _, _, _, _, _ = _coordinator(tmp_path, registry=registry)

    coordinator.begin_shutdown()
    with pytest.raises(SleepCoordinatorClosedError):
        asyncio.run(
            coordinator.request_manual(
                _policy(),
                trigger_event_id="manual-after-shutdown",
                source_checkpoint_ids=("evidence-1",),
            )
        )
    assert coordinator.accepting_runs is False


def test_manual_trigger_is_idempotent_across_duplicate_requests(tmp_path: Path):
    calls = []
    registry = JobRegistry()

    async def run_job(context):
        calls.append(context.job_id)
        return _completed_result(context)

    registry.register(SleepJobKind.INDEX_VERIFY, run_job)
    coordinator, _, _, _, _, _ = _coordinator(tmp_path, registry=registry)

    first = asyncio.run(
        coordinator.request_manual(
            _policy(),
            trigger_event_id="manual-idempotent",
            source_checkpoint_ids=("evidence-1",),
        )
    )
    replay = asyncio.run(
        coordinator.request_manual(
            _policy(),
            trigger_event_id="manual-idempotent",
            source_checkpoint_ids=("evidence-1",),
        )
    )

    assert replay.run_id == first.run_id
    assert len(calls) == 1


def test_job_kind_without_registered_adapter_is_skipped_at_preflight(tmp_path: Path):
    coordinator, _, _, _, store, _ = _coordinator(tmp_path)
    snapshot = asyncio.run(
        coordinator.request_manual(
            _policy(),
            trigger_event_id="manual-missing-adapter",
            source_checkpoint_ids=("evidence-1",),
        )
    )
    assert snapshot.state is SleepRunState.SKIPPED
    assert store.list_transitions(snapshot.run_id)[-1].reason_code == "job_adapter_unavailable"


def test_network_grant_is_ignored_when_policy_denies_network(tmp_path: Path):
    seen = []
    registry = JobRegistry()

    async def run_job(context):
        seen.append(context.network_access.allowed)
        return _completed_result(context)

    registry.register(SleepJobKind.INDEX_VERIFY, run_job)
    coordinator, _, _, _, _, _ = _coordinator(tmp_path, registry=registry)
    grant = NetworkGrant(
        grant_id="grant-denied",
        job_kind=SleepJobKind.INDEX_VERIFY,
        expires_at_utc=END,
    )
    snapshot = asyncio.run(
        coordinator.request_manual(
            _policy(network_policy=NetworkPolicy.DENY.value),
            trigger_event_id="manual-denied-network",
            source_checkpoint_ids=("evidence-1",),
            network_grants=(grant,),
        )
    )
    assert snapshot.state is SleepRunState.COMPLETED
    assert seen == [False]
