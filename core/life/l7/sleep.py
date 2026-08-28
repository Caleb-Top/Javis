"""Governed, interruptible coordination for L7 sleep runs.

The coordinator is deliberately an orchestration boundary.  It accepts only
validated policies, bounded job adapters, injected observations, and the
append-only L7 stores.  Jobs never receive raw network or deployment handles.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import re
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Protocol, TypeVar
from zoneinfo import ZoneInfo

from core.cancellation import CancellationToken
from core.life.l1.clock import (
    Clock,
    ClockReading,
    SystemClock,
    parse_utc_milliseconds,
)

from .contracts import (
    NetworkPolicy,
    ScheduleMode,
    SleepJobKind,
    SleepJobReceiptV1,
    SleepJobStatus,
    SleepPolicyV1,
    SleepRunState,
    SleepRunTransitionV1,
    SleepRunV1,
    SleepTriggerKind,
    canonical_content_hash,
)
from .growth_store import GrowthStore
from .run_store import (
    RunSnapshot,
    RunStore,
    RunStoreCASMismatchError,
    RunStoreClaimError,
)


ALLOWED_JOB_KINDS = frozenset(SleepJobKind)
_HASH_EMPTY = hashlib.sha256(b"").hexdigest()
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_OUTPUT_TYPES: Mapping[SleepJobKind, str] = MappingProxyType(
    {
        SleepJobKind.INDEX_VERIFY: "index_receipt",
        SleepJobKind.CONTRADICTION_SCAN: "contradiction_receipt",
        SleepJobKind.RETENTION_APPLY: "retention_receipt",
        SleepJobKind.SUMMARY_BUILD: "summary_receipt",
        SleepJobKind.GROWTH_PROPOSE: "growth_candidate_refs",
        SleepJobKind.ARTIFACT_REVALIDATE: "artifact_validation_receipt",
    }
)


class SleepCoordinatorError(RuntimeError):
    """Base failure for the L7 sleep orchestration boundary."""


class SleepCoordinatorClosedError(SleepCoordinatorError):
    pass


class JobRegistrationError(SleepCoordinatorError):
    pass


@dataclass(frozen=True, slots=True)
class PowerSnapshot:
    on_ac_power: bool
    battery_percent: int

    def __post_init__(self) -> None:
        if type(self.on_ac_power) is not bool:
            raise TypeError("on_ac_power must be bool")
        if type(self.battery_percent) is not int or not 0 <= self.battery_percent <= 100:
            raise ValueError("battery_percent must be an integer in [0, 100]")


@dataclass(frozen=True, slots=True)
class IdleSnapshot:
    idle_seconds: float
    user_active: bool = False
    playback_active: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.idle_seconds, bool) or not isinstance(
            self.idle_seconds, (int, float)
        ):
            raise TypeError("idle_seconds must be a finite number")
        idle_seconds = float(self.idle_seconds)
        if not math.isfinite(idle_seconds) or idle_seconds < 0:
            raise ValueError("idle_seconds must be finite and non-negative")
        if type(self.user_active) is not bool or type(self.playback_active) is not bool:
            raise TypeError("idle activity flags must be bool")
        object.__setattr__(self, "idle_seconds", idle_seconds)


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    policy_revision: int
    approval_id: str | None
    cpu_percent: float
    free_bytes: int
    runtime_healthy: bool
    data_root_healthy: bool
    approval_valid: bool = True
    active_action: bool = False
    shutdown_requested: bool = False

    def __post_init__(self) -> None:
        if type(self.policy_revision) is not int or self.policy_revision <= 0:
            raise ValueError("policy_revision must be positive")
        if self.approval_id is not None and (
            type(self.approval_id) is not str or not self.approval_id
        ):
            raise ValueError("approval_id must be a non-empty ID or None")
        if isinstance(self.cpu_percent, bool) or not isinstance(
            self.cpu_percent, (int, float)
        ):
            raise TypeError("cpu_percent must be a finite number")
        cpu_percent = float(self.cpu_percent)
        if not math.isfinite(cpu_percent) or not 0 <= cpu_percent <= 100:
            raise ValueError("cpu_percent must be in [0, 100]")
        if type(self.free_bytes) is not int or self.free_bytes < 0:
            raise ValueError("free_bytes must be a non-negative integer")
        for name in (
            "runtime_healthy",
            "data_root_healthy",
            "approval_valid",
            "active_action",
            "shutdown_requested",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be bool")
        object.__setattr__(self, "cpu_percent", cpu_percent)


class PowerProbe(Protocol):
    def read(self) -> PowerSnapshot | Awaitable[PowerSnapshot]: ...


class IdleProbe(Protocol):
    def read(self) -> IdleSnapshot | Awaitable[IdleSnapshot]: ...


class HealthProbe(Protocol):
    def read(self) -> HealthSnapshot | Awaitable[HealthSnapshot]: ...


@dataclass(frozen=True, slots=True)
class NetworkGrant:
    grant_id: str
    job_kind: SleepJobKind
    expires_at_utc: str

    def __post_init__(self) -> None:
        if type(self.grant_id) is not str or not self.grant_id:
            raise ValueError("grant_id must be a non-empty ID")
        try:
            kind = (
                self.job_kind
                if isinstance(self.job_kind, SleepJobKind)
                else SleepJobKind(self.job_kind)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("job_kind must be in the sleep job allowlist") from exc
        parse_utc_milliseconds(self.expires_at_utc)
        object.__setattr__(self, "job_kind", kind)


@dataclass(frozen=True, slots=True)
class NetworkAccess:
    allowed: bool = False
    grant_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.allowed) is not bool:
            raise TypeError("allowed must be bool")
        if self.allowed != (self.grant_id is not None):
            raise ValueError("network access requires exactly one explicit grant")


@dataclass(frozen=True, slots=True)
class JobBudget:
    max_records: int
    max_output_bytes: int
    max_seconds: float
    run_deadline_monotonic: float

    def __post_init__(self) -> None:
        if type(self.max_records) is not int or self.max_records <= 0:
            raise ValueError("max_records must be positive")
        if type(self.max_output_bytes) is not int or self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        for name in ("max_seconds", "run_deadline_monotonic"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be finite")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, float(value))


@dataclass(frozen=True, slots=True)
class JobContext:
    run_id: str
    job_id: str
    kind: SleepJobKind
    budget: JobBudget
    cancellation: CancellationToken
    source_checkpoint_ids: tuple[str, ...]
    network_access: NetworkAccess


@dataclass(frozen=True, slots=True)
class JobResult:
    checkpoint_id: str
    checkpoint_hash: str
    input_set_hash: str
    output_set_hash: str | None
    processed_count: int
    skipped_count: int
    output_bytes: int
    source_ids: tuple[str, ...] = ()
    candidate_version_ids: tuple[str, ...] = ()
    status: SleepJobStatus = SleepJobStatus.COMPLETED
    reason_code: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.checkpoint_id, "checkpoint_id")
        for name in ("checkpoint_hash", "input_set_hash"):
            _require_hash(getattr(self, name), name)
        if self.output_set_hash is not None:
            _require_hash(self.output_set_hash, "output_set_hash")
        for name in ("processed_count", "skipped_count", "output_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("source_ids", "candidate_version_ids"):
            values = tuple(getattr(self, name))
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
            for value in values:
                _require_id(value, name)
            object.__setattr__(self, name, values)
        for name in ("reason_code", "error_code"):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not str or _CODE.fullmatch(value) is None
            ):
                raise ValueError(f"{name} must be a bounded reason code or None")
        try:
            status = (
                self.status
                if isinstance(self.status, SleepJobStatus)
                else SleepJobStatus(self.status)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("status must be a SleepJobStatus") from exc
        object.__setattr__(self, "status", status)


JobRunner = Callable[[JobContext], JobResult | Awaitable[JobResult]]


@dataclass(frozen=True, slots=True)
class _JobRegistration:
    runner: JobRunner
    requires_network: bool
    revalidation_adapter: bool


class JobRegistry:
    """Registry that cannot represent jobs outside the frozen six-kind set."""

    def __init__(self) -> None:
        self._jobs: dict[SleepJobKind, _JobRegistration] = {}

    def register(
        self,
        kind: SleepJobKind | str,
        runner: JobRunner,
        *,
        requires_network: bool = False,
        revalidation_adapter: bool = False,
    ) -> None:
        parsed = self._kind(kind)
        if not callable(runner):
            raise TypeError("runner must be callable")
        if type(requires_network) is not bool or type(revalidation_adapter) is not bool:
            raise TypeError("job registry flags must be bool")
        if requires_network and (
            parsed is not SleepJobKind.ARTIFACT_REVALIDATE or not revalidation_adapter
        ):
            raise ValueError("network is restricted to a declared revalidation adapter")
        if parsed in self._jobs:
            raise JobRegistrationError("job kind is already registered")
        self._jobs[parsed] = _JobRegistration(
            runner=runner,
            requires_network=requires_network,
            revalidation_adapter=revalidation_adapter,
        )

    def replace(
        self,
        kind: SleepJobKind | str,
        runner: JobRunner,
        *,
        requires_network: bool | None = None,
        revalidation_adapter: bool | None = None,
    ) -> None:
        parsed = self._kind(kind)
        existing = self._jobs.get(parsed)
        if existing is None:
            raise JobRegistrationError("job kind is not registered")
        network = existing.requires_network if requires_network is None else requires_network
        revalidation = (
            existing.revalidation_adapter
            if revalidation_adapter is None
            else revalidation_adapter
        )
        del self._jobs[parsed]
        self.register(
            parsed,
            runner,
            requires_network=network,
            revalidation_adapter=revalidation,
        )

    def get(self, kind: SleepJobKind | str) -> _JobRegistration | None:
        return self._jobs.get(self._kind(kind))

    @property
    def kinds(self) -> tuple[SleepJobKind, ...]:
        return tuple(self._jobs)

    @staticmethod
    def _kind(kind: SleepJobKind | str) -> SleepJobKind:
        try:
            parsed = kind if isinstance(kind, SleepJobKind) else SleepJobKind(kind)
        except (TypeError, ValueError) as exc:
            raise ValueError("job kind is outside the six-kind allowlist") from exc
        if parsed not in ALLOWED_JOB_KINDS:
            raise ValueError("job kind is outside the six-kind allowlist")
        return parsed


@dataclass(frozen=True, slots=True)
class _Preflight:
    reading: ClockReading
    power: PowerSnapshot
    idle: IdleSnapshot
    health: HealthSnapshot
    snapshot_hash: str


@dataclass(slots=True)
class _ActiveRun:
    run_id: str
    policy: SleepPolicyV1
    cancellation: CancellationToken
    done: asyncio.Event
    driver_task: asyncio.Task[Any] | None
    current_job_id: str | None = None


_ProbeT = TypeVar("_ProbeT")


class SleepCoordinator:
    """Serializes governed sleep runs and persists every durable boundary."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        power_probe: PowerProbe,
        idle_probe: IdleProbe,
        health_probe: HealthProbe,
        run_store: RunStore,
        growth_store: GrowthStore,
        job_registry: JobRegistry,
        instance_id: str,
        cancel_grace_seconds: float = 2.0,
        shutdown_timeout_seconds: float = 5.0,
    ) -> None:
        if not all(hasattr(probe, "read") for probe in (power_probe, idle_probe, health_probe)):
            raise TypeError("power, idle, and health probes must expose read()")
        if not _supports_methods(
            run_store,
            "append_transition",
            "claim_active",
            "create_run",
            "get_run",
            "list_job_checkpoints",
            "list_job_receipts",
            "list_runs",
            "record_job_checkpoint",
            "record_job_receipt",
            "verify_integrity",
        ):
            raise TypeError("run_store does not implement the bounded RunStore API")
        if not _supports_methods(growth_store, "get_candidate"):
            raise TypeError("growth_store does not implement the bounded GrowthStore API")
        if not isinstance(job_registry, JobRegistry):
            raise TypeError("job_registry must be JobRegistry")
        if type(instance_id) is not str or not instance_id:
            raise ValueError("instance_id must be a non-empty ID")
        for name, value in (
            ("cancel_grace_seconds", cancel_grace_seconds),
            ("shutdown_timeout_seconds", shutdown_timeout_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be finite and positive")
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be finite and positive")
        self.clock = clock or SystemClock()
        self.power_probe = power_probe
        self.idle_probe = idle_probe
        self.health_probe = health_probe
        self.run_store = run_store
        self.growth_store = growth_store
        self.job_registry = job_registry
        self.instance_id = instance_id
        self.cancel_grace_seconds = float(cancel_grace_seconds)
        self.shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        self._driver_lock = asyncio.Lock()
        self._active: _ActiveRun | None = None
        self._accepting_runs = True

    @property
    def accepting_runs(self) -> bool:
        return self._accepting_runs

    @property
    def active_run_id(self) -> str | None:
        return None if self._active is None else self._active.run_id

    async def request_manual(
        self,
        policy: SleepPolicyV1,
        *,
        trigger_event_id: str,
        source_checkpoint_ids: tuple[str, ...] = (),
        network_grants: tuple[NetworkGrant, ...] = (),
    ) -> RunSnapshot:
        return await self._coordinate_new(
            policy,
            trigger_kind=SleepTriggerKind.MANUAL,
            trigger_event_id=trigger_event_id,
            source_checkpoint_ids=source_checkpoint_ids,
            network_grants=network_grants,
            window_label=trigger_event_id,
        )

    async def tick(
        self,
        policy: SleepPolicyV1,
        *,
        trigger_event_id: str,
        source_checkpoint_ids: tuple[str, ...] = (),
        network_grants: tuple[NetworkGrant, ...] = (),
    ) -> RunSnapshot | None:
        self._require_policy(policy)
        if not self._accepting_runs:
            raise SleepCoordinatorClosedError("sleep coordinator is shutting down")
        reading = self.clock.read()
        if policy.schedule_mode is ScheduleMode.MANUAL:
            return None
        if policy.schedule_mode is ScheduleMode.QUIET_HOURS:
            window_label = _quiet_window_label(policy, reading.utc)
            if window_label is None:
                return None
            trigger_kind = SleepTriggerKind.QUIET_HOURS
        else:
            idle = await _read_probe(self.idle_probe, IdleSnapshot, "idle")
            if (
                idle.idle_seconds < policy.idle_min_seconds
                or idle.user_active
                or idle.playback_active
            ):
                return None
            trigger_kind = SleepTriggerKind.IDLE
            window_label = trigger_event_id
        return await self._coordinate_new(
            policy,
            trigger_kind=trigger_kind,
            trigger_event_id=trigger_event_id,
            source_checkpoint_ids=source_checkpoint_ids,
            network_grants=network_grants,
            window_label=window_label,
        )

    async def resume_interrupted(
        self,
        policy: SleepPolicyV1,
        *,
        network_grants: tuple[NetworkGrant, ...] = (),
    ) -> tuple[RunSnapshot, ...]:
        self._require_policy(policy)
        if not self._accepting_runs:
            raise SleepCoordinatorClosedError("sleep coordinator is shutting down")
        recovered: list[RunSnapshot] = []
        async with self._driver_lock:
            snapshots = self.run_store.list_runs(
                identity_id=policy.identity_id,
                instance_id=self.instance_id,
                state=SleepRunState.INTERRUPTED,
                limit=256,
            )
            for snapshot in reversed(snapshots):
                if (
                    snapshot.header.policy_id != policy.policy_id
                    or snapshot.header.policy_revision != policy.revision
                    or snapshot.header.owner_subject_id != policy.owner_subject_id
                ):
                    snapshot = self._transition(
                        snapshot,
                        SleepRunState.FAILED,
                        "recovery_policy_mismatch",
                    )
                    recovered.append(snapshot)
                    continue
                if not self._recovery_evidence_valid(snapshot):
                    snapshot = self._transition(
                        snapshot,
                        SleepRunState.FAILED,
                        "recovery_checkpoint_invalid",
                    )
                    recovered.append(snapshot)
                    continue
                checkpoints = self.run_store.list_job_checkpoints(snapshot.run_id, limit=256)
                receipts = self.run_store.list_job_receipts(snapshot.run_id, limit=256)
                snapshot = self._transition(
                    snapshot,
                    SleepRunState.PREFLIGHT,
                    "recovery_requested",
                    checkpoint_id=checkpoints[-1].checkpoint_id if checkpoints else None,
                    receipt_ids=tuple(receipt.receipt_id for receipt in receipts),
                )
                preflight = await self._sample_preflight(policy, SleepTriggerKind.RECOVERY)
                reason = self._preflight_reason(
                    policy,
                    SleepTriggerKind.RECOVERY,
                    preflight,
                    network_grants,
                )
                if reason is not None:
                    snapshot = self._transition(snapshot, SleepRunState.SKIPPED, reason)
                else:
                    snapshot = await self._drive(
                        snapshot,
                        policy,
                        preflight,
                        network_grants,
                    )
                recovered.append(snapshot)
        return tuple(recovered)

    def notify_user_activity(self) -> bool:
        active = self._active
        if active is None or not active.policy.wake_on_user_activity:
            return False
        return self._cancel_active("user_activity")

    def notify_playback_started(self) -> bool:
        return self._cancel_active("playback_started")

    def notify_action_started(self) -> bool:
        return self._cancel_active("action_started")

    def notify_power_changed(self) -> bool:
        return self._cancel_active("power_changed")

    def notify_policy_changed(self, policy: SleepPolicyV1) -> bool:
        active = self._active
        if active is None or policy.policy_id != active.policy.policy_id:
            return False
        if policy.revision == active.policy.revision:
            return False
        return self._cancel_active("policy_safety_changed")

    def begin_shutdown(self) -> bool:
        self._accepting_runs = False
        return self._cancel_active("shutdown_requested")

    async def shutdown(self) -> None:
        self.begin_shutdown()
        active = self._active
        if active is None or active.driver_task is asyncio.current_task():
            return
        try:
            await asyncio.wait_for(active.done.wait(), timeout=self.shutdown_timeout_seconds)
        except TimeoutError:
            if active.driver_task is not None:
                active.driver_task.cancel()
                with suppress(asyncio.CancelledError):
                    await active.driver_task

    close = shutdown

    async def _coordinate_new(
        self,
        policy: SleepPolicyV1,
        *,
        trigger_kind: SleepTriggerKind,
        trigger_event_id: str,
        source_checkpoint_ids: tuple[str, ...],
        network_grants: tuple[NetworkGrant, ...],
        window_label: str,
    ) -> RunSnapshot:
        self._require_policy(policy)
        if not self._accepting_runs:
            raise SleepCoordinatorClosedError("sleep coordinator is shutting down")
        source_checkpoint_ids = tuple(source_checkpoint_ids)
        network_grants = tuple(network_grants)
        key_seed = (
            f"{policy.policy_id}:{policy.revision}:{policy.identity_id}:"
            f"{self.instance_id}:{trigger_kind.value}:{window_label}"
        )
        digest = hashlib.sha256(key_seed.encode("utf-8")).hexdigest()
        run_id = f"sleep-run-{digest[:32]}"
        idempotency_key = f"sleep-window-{digest}"
        async with self._driver_lock:
            existing = self.run_store.get_run(run_id)
            if existing is not None:
                if existing.header.idempotency_key != idempotency_key:
                    raise SleepCoordinatorError("sleep run identity collision")
                return existing
            claimed = self.run_store.claim_active(policy.identity_id, self.instance_id)
            if claimed is not None:
                return claimed
            preflight = await self._sample_preflight(policy, trigger_kind)
            header = self._build_run_header(
                policy,
                run_id=run_id,
                trigger_kind=trigger_kind,
                trigger_event_id=trigger_event_id,
                idempotency_key=idempotency_key,
                source_checkpoint_ids=source_checkpoint_ids,
                preflight=preflight,
            )
            try:
                snapshot = self.run_store.create_run(header)
            except RunStoreClaimError:
                claimed = self.run_store.claim_active(policy.identity_id, self.instance_id)
                if claimed is None:
                    raise
                return claimed
            snapshot = self._transition(
                snapshot,
                SleepRunState.PREFLIGHT,
                "scheduler_accepted",
            )
            reason = self._preflight_reason(
                policy,
                trigger_kind,
                preflight,
                network_grants,
            )
            if reason is not None:
                return self._transition(snapshot, SleepRunState.SKIPPED, reason)
            return await self._drive(snapshot, policy, preflight, network_grants)

    async def _sample_preflight(
        self,
        policy: SleepPolicyV1,
        trigger_kind: SleepTriggerKind,
    ) -> _Preflight:
        reading = self.clock.read()
        power, idle, health = await asyncio.gather(
            _read_probe(self.power_probe, PowerSnapshot, "power"),
            _read_probe(self.idle_probe, IdleSnapshot, "idle"),
            _read_probe(self.health_probe, HealthSnapshot, "health"),
        )
        payload = {
            "policy_id": policy.policy_id,
            "policy_revision": policy.revision,
            "trigger_kind": trigger_kind.value,
            "observed_at_utc": reading.utc_timestamp,
            "power": {
                "on_ac_power": power.on_ac_power,
                "battery_percent": power.battery_percent,
            },
            "idle": {
                "idle_seconds": idle.idle_seconds,
                "user_active": idle.user_active,
                "playback_active": idle.playback_active,
            },
            "health": {
                "policy_revision": health.policy_revision,
                "approval_id": health.approval_id,
                "approval_valid": health.approval_valid,
                "cpu_percent": health.cpu_percent,
                "free_bytes": health.free_bytes,
                "runtime_healthy": health.runtime_healthy,
                "data_root_healthy": health.data_root_healthy,
                "active_action": health.active_action,
                "shutdown_requested": health.shutdown_requested,
            },
        }
        return _Preflight(
            reading=reading,
            power=power,
            idle=idle,
            health=health,
            snapshot_hash=canonical_content_hash(payload),
        )

    def _preflight_reason(
        self,
        policy: SleepPolicyV1,
        trigger_kind: SleepTriggerKind,
        preflight: _Preflight,
        grants: tuple[NetworkGrant, ...],
    ) -> str | None:
        now = preflight.reading.utc
        effective = parse_utc_milliseconds(policy.effective_at_utc)
        expires = parse_utc_milliseconds(policy.expires_at_utc)
        checks = (
            (not self._accepting_runs or preflight.health.shutdown_requested, "shutdown_requested"),
            (not policy.enabled, "policy_disabled"),
            (now < effective, "policy_not_effective"),
            (now >= expires, "policy_expired"),
            (preflight.health.policy_revision != policy.revision, "policy_revision_mismatch"),
            (not preflight.health.approval_valid, "approval_invalid"),
            (preflight.health.approval_id != policy.approval_id, "approval_mismatch"),
            (not preflight.health.runtime_healthy, "runtime_unhealthy"),
            (not preflight.health.data_root_healthy, "data_root_unhealthy"),
            (preflight.health.active_action, "action_active"),
            (preflight.idle.playback_active, "playback_active"),
            (preflight.idle.user_active, "user_active"),
            (policy.require_ac_power and not preflight.power.on_ac_power, "ac_power_required"),
            (
                preflight.power.battery_percent < policy.minimum_battery_percent,
                "battery_below_minimum",
            ),
            (
                preflight.health.cpu_percent > policy.maximum_cpu_percent,
                "cpu_above_maximum",
            ),
            (
                preflight.health.free_bytes < policy.minimum_free_bytes,
                "disk_below_minimum",
            ),
        )
        for failed, reason in checks:
            if failed:
                return reason
        if trigger_kind is SleepTriggerKind.MANUAL and policy.schedule_mode is not ScheduleMode.MANUAL:
            return "schedule_mode_mismatch"
        if trigger_kind is SleepTriggerKind.QUIET_HOURS:
            if policy.schedule_mode is not ScheduleMode.QUIET_HOURS:
                return "schedule_mode_mismatch"
            if _quiet_window_label(policy, now) is None:
                return "outside_quiet_hours"
        if trigger_kind is SleepTriggerKind.IDLE:
            if policy.schedule_mode is not ScheduleMode.IDLE:
                return "schedule_mode_mismatch"
            if preflight.idle.idle_seconds < policy.idle_min_seconds:
                return "idle_below_minimum"
        for kind in policy.allowed_job_kinds:
            registration = self.job_registry.get(kind)
            if registration is None:
                return "job_adapter_unavailable"
            if registration.requires_network:
                if policy.network_policy is not NetworkPolicy.EXPLICIT_JOB_GRANTS:
                    return "network_policy_denied"
                if self._grant_for(kind, grants, now) is None:
                    return "network_grant_required"
        return None

    async def _drive(
        self,
        snapshot: RunSnapshot,
        policy: SleepPolicyV1,
        preflight: _Preflight,
        network_grants: tuple[NetworkGrant, ...],
    ) -> RunSnapshot:
        snapshot = self._transition(snapshot, SleepRunState.RUNNING, "preflight_passed")
        token = CancellationToken()
        active = _ActiveRun(
            run_id=snapshot.run_id,
            policy=policy,
            cancellation=token,
            done=asyncio.Event(),
            driver_task=asyncio.current_task(),
        )
        self._active = active
        run_started = self.clock.read().monotonic_seconds
        run_deadline = run_started + policy.max_run_seconds
        receipts = list(self.run_store.list_job_receipts(snapshot.run_id, limit=256))
        completed_jobs = {
            receipt.job_id
            for receipt in receipts
            if receipt.status in {SleepJobStatus.COMPLETED, SleepJobStatus.SKIPPED}
        }
        try:
            for raw_spec in snapshot.header.job_specs:
                spec = dict(raw_spec)
                job_id = str(spec["job_id"])
                if job_id in completed_jobs:
                    continue
                active.current_job_id = job_id
                if token.cancelled:
                    snapshot = self._transition(
                        snapshot,
                        SleepRunState.CANCELLING,
                        token.reason or "cancelled",
                    )
                    return self._transition(
                        snapshot,
                        SleepRunState.CANCELLED,
                        token.reason or "cancelled",
                    )
                remaining = run_deadline - self.clock.read().monotonic_seconds
                if remaining <= 0:
                    return self._transition(
                        snapshot,
                        SleepRunState.TIMED_OUT,
                        "run_budget_exceeded",
                    )
                kind = SleepJobKind(spec["kind"])
                registration = self.job_registry.get(kind)
                if registration is None:
                    return self._transition(
                        snapshot,
                        SleepRunState.FAILED,
                        "job_adapter_unavailable",
                    )
                network_access = NetworkAccess()
                if registration.requires_network:
                    grant = self._grant_for(kind, network_grants, self.clock.read().utc)
                    if grant is None:
                        return self._transition(
                            snapshot,
                            SleepRunState.FAILED,
                            "network_grant_unavailable",
                        )
                    network_access = NetworkAccess(allowed=True, grant_id=grant.grant_id)
                budget = JobBudget(
                    max_records=min(int(spec["max_records"]), policy.max_input_records),
                    max_output_bytes=min(
                        int(spec["max_output_bytes"]), policy.max_output_bytes
                    ),
                    max_seconds=min(float(spec["max_seconds"]), remaining),
                    run_deadline_monotonic=run_deadline,
                )
                context = JobContext(
                    run_id=snapshot.run_id,
                    job_id=job_id,
                    kind=kind,
                    budget=budget,
                    cancellation=token,
                    source_checkpoint_ids=self._job_source_checkpoints(
                        snapshot.run_id,
                        job_id,
                        tuple(spec["source_checkpoint_ids"]),
                    ),
                    network_access=network_access,
                )
                snapshot, terminal = await self._execute_job(
                    snapshot,
                    policy,
                    context,
                    registration,
                    run_deadline,
                )
                if terminal:
                    return snapshot
                receipts = list(self.run_store.list_job_receipts(snapshot.run_id, limit=256))
            receipt_ids = tuple(receipt.receipt_id for receipt in receipts)
            checkpoints = self.run_store.list_job_checkpoints(snapshot.run_id, limit=256)
            snapshot = self._transition(
                snapshot,
                SleepRunState.COMMITTING,
                "jobs_receipted",
                checkpoint_id=checkpoints[-1].checkpoint_id if checkpoints else None,
                receipt_ids=receipt_ids,
            )
            return self._transition(
                snapshot,
                SleepRunState.COMPLETED,
                "run_completed",
                checkpoint_id=checkpoints[-1].checkpoint_id if checkpoints else None,
                receipt_ids=receipt_ids,
            )
        finally:
            active.done.set()
            if self._active is active:
                self._active = None

    async def _execute_job(
        self,
        snapshot: RunSnapshot,
        policy: SleepPolicyV1,
        context: JobContext,
        registration: _JobRegistration,
        run_deadline: float,
    ) -> tuple[RunSnapshot, bool]:
        started = self.clock.read()
        operation = asyncio.create_task(_invoke(registration.runner, context))
        cancellation = asyncio.create_task(context.cancellation.wait())
        try:
            done, _ = await asyncio.wait(
                {operation, cancellation},
                timeout=context.budget.max_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation in done or context.cancellation.cancelled:
                reason = context.cancellation.reason or "cancelled"
                snapshot = self._transition(
                    snapshot,
                    SleepRunState.CANCELLING,
                    reason,
                )
                result = await self._finish_cancelled_job(
                    operation,
                    context,
                    started,
                    reason,
                )
                snapshot = await self._persist_job_result(snapshot, policy, context, result, started)
                if snapshot.state is SleepRunState.FAILED:
                    return snapshot, True
                snapshot = self._transition(
                    snapshot,
                    SleepRunState.CANCELLED,
                    reason,
                    checkpoint_id=result.checkpoint_id,
                    receipt_ids=(self._receipt_id(context, result),),
                )
                return snapshot, True
            if operation not in done:
                operation.cancel()
                with suppress(asyncio.CancelledError):
                    await operation
                result = self._synthetic_result(
                    context,
                    status=SleepJobStatus.TIMED_OUT,
                    reason_code="job_timeout",
                    error_code="job_timeout",
                )
            else:
                try:
                    raw_result = operation.result()
                except Exception:
                    raw_result = self._synthetic_result(
                        context,
                        status=SleepJobStatus.FAILED,
                        reason_code="job_failed",
                        error_code="job_adapter_failed",
                    )
                result = self._validated_result(raw_result, context)
            elapsed = self.clock.read().monotonic_seconds - started.monotonic_seconds
            if (
                result.status in {SleepJobStatus.COMPLETED, SleepJobStatus.SKIPPED}
                and (
                    elapsed > context.budget.max_seconds
                    or self.clock.read().monotonic_seconds > run_deadline
                )
            ):
                result = self._replace_result_status(
                    result,
                    SleepJobStatus.TIMED_OUT,
                    "job_budget_exceeded",
                    "job_timeout",
                )
            snapshot = await self._persist_job_result(snapshot, policy, context, result, started)
            if snapshot.state is SleepRunState.FAILED:
                return snapshot, True
            if result.status in {SleepJobStatus.COMPLETED, SleepJobStatus.SKIPPED}:
                return snapshot, False
            terminal_state = {
                SleepJobStatus.FAILED: SleepRunState.FAILED,
                SleepJobStatus.CANCELLED: SleepRunState.CANCELLED,
                SleepJobStatus.TIMED_OUT: SleepRunState.TIMED_OUT,
            }[result.status]
            snapshot = self._transition(
                snapshot,
                terminal_state,
                result.reason_code or result.error_code or result.status.value,
                checkpoint_id=result.checkpoint_id,
                receipt_ids=(self._receipt_id(context, result),),
            )
            return snapshot, True
        finally:
            if not cancellation.done():
                cancellation.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation
            if not operation.done():
                operation.cancel()
                with suppress(asyncio.CancelledError):
                    await operation

    async def _finish_cancelled_job(
        self,
        operation: asyncio.Task[JobResult],
        context: JobContext,
        started: ClockReading,
        reason: str,
    ) -> JobResult:
        if not operation.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(operation),
                    timeout=self.cancel_grace_seconds,
                )
            except TimeoutError:
                operation.cancel()
                with suppress(asyncio.CancelledError):
                    await operation
            except Exception:
                pass
        if operation.done() and not operation.cancelled():
            try:
                result = self._validated_result(operation.result(), context)
                return self._replace_result_status(
                    result,
                    SleepJobStatus.CANCELLED,
                    reason,
                    None,
                )
            except Exception:
                pass
        return self._synthetic_result(
            context,
            status=SleepJobStatus.CANCELLED,
            reason_code=reason,
            error_code=None,
        )

    async def _persist_job_result(
        self,
        snapshot: RunSnapshot,
        policy: SleepPolicyV1,
        context: JobContext,
        result: JobResult,
        started: ClockReading,
    ) -> RunSnapshot:
        was_cancelling = snapshot.state is SleepRunState.CANCELLING
        if not was_cancelling:
            snapshot = self._transition(
                snapshot,
                SleepRunState.CHECKPOINTING,
                "job_checkpointing",
            )
        ended = self.clock.read()
        existing = next(
            (
                checkpoint
                for checkpoint in self.run_store.list_job_checkpoints(
                    snapshot.run_id, limit=256
                )
                if checkpoint.checkpoint_id == result.checkpoint_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.job_id != context.job_id
                or existing.content_hash != result.checkpoint_hash
            ):
                return self._transition(
                    snapshot,
                    SleepRunState.FAILED,
                    "checkpoint_replay_mismatch",
                )
        else:
            try:
                self.run_store.record_job_checkpoint(
                    snapshot.run_id,
                    context.job_id,
                    result.checkpoint_id,
                    expected_revision=snapshot.revision,
                    created_at_utc=ended.utc_timestamp,
                    content_hash=result.checkpoint_hash,
                    idempotency_key=f"checkpoint-write-{context.job_id}",
                )
            except Exception:
                return self._transition(
                    self._fresh_snapshot(snapshot.run_id),
                    SleepRunState.FAILED,
                    "checkpoint_commit_failed",
                )
            snapshot = self._fresh_snapshot(snapshot.run_id)
        receipt = self._build_receipt(policy, context, result, started, ended)
        try:
            snapshot = self.run_store.record_job_receipt(
                receipt,
                expected_revision=snapshot.revision,
                idempotency_key=f"receipt-write-{context.job_id}",
            )
        except Exception:
            return self._transition(
                self._fresh_snapshot(snapshot.run_id),
                SleepRunState.FAILED,
                "receipt_commit_failed",
                checkpoint_id=result.checkpoint_id,
            )
        if was_cancelling:
            return snapshot
        return self._transition(
            snapshot,
            SleepRunState.RUNNING,
            "job_receipted",
            checkpoint_id=result.checkpoint_id,
            receipt_ids=(receipt.receipt_id,),
        )

    def _validated_result(self, result: JobResult, context: JobContext) -> JobResult:
        if not isinstance(result, JobResult):
            return self._synthetic_result(
                context,
                status=SleepJobStatus.FAILED,
                reason_code="job_result_invalid",
                error_code="job_result_invalid",
            )
        if result.processed_count + result.skipped_count > context.budget.max_records:
            return self._replace_result_status(
                result,
                SleepJobStatus.FAILED,
                "record_budget_exceeded",
                "record_budget_exceeded",
            )
        if result.output_bytes > context.budget.max_output_bytes:
            return self._replace_result_status(
                result,
                SleepJobStatus.FAILED,
                "output_budget_exceeded",
                "output_budget_exceeded",
            )
        allowed_sources = frozenset(context.source_checkpoint_ids)
        if not set(result.source_ids).issubset(allowed_sources):
            return self._replace_result_status(
                result,
                SleepJobStatus.FAILED,
                "evidence_reference_invalid",
                "evidence_reference_invalid",
            )
        for candidate_version_id in result.candidate_version_ids:
            try:
                candidate_snapshot = self.growth_store.get_candidate(candidate_version_id)
            except Exception:
                candidate_snapshot = None
            if candidate_snapshot is None or not set(
                candidate_snapshot.candidate.source_evidence_ids
            ).issubset(allowed_sources):
                return self._replace_result_status(
                    result,
                    SleepJobStatus.FAILED,
                    "candidate_evidence_invalid",
                    "candidate_evidence_invalid",
                )
        return result

    def _build_receipt(
        self,
        policy: SleepPolicyV1,
        context: JobContext,
        result: JobResult,
        started: ClockReading,
        ended: ClockReading,
    ) -> SleepJobReceiptV1:
        duration_ms = max(
            0,
            int(round((ended.monotonic_seconds - started.monotonic_seconds) * 1_000)),
        )
        payload = {
            "schema_version": 1,
            "receipt_id": self._receipt_id(context, result),
            "run_id": context.run_id,
            "job_id": context.job_id,
            "job_kind": context.kind.value,
            "status": result.status.value,
            "input_set_hash": result.input_set_hash,
            "output_set_hash": result.output_set_hash,
            "processed_count": result.processed_count,
            "skipped_count": result.skipped_count,
            "reason_code": result.reason_code,
            "error_code": result.error_code,
            "checkpoint_id": result.checkpoint_id,
            "source_ids": list(result.source_ids),
            "started_at_utc": started.utc_timestamp,
            "ended_at_utc": ended.utc_timestamp,
            "duration_ms": duration_ms,
            "output_bytes": result.output_bytes,
            "privacy_class": policy.privacy_class.value,
            "retention_class": policy.retention_class.value,
            "provenance": {
                "adapter": f"sleep.{context.kind.value}",
                "source_ids": list(result.source_ids),
            },
        }
        payload["content_hash"] = canonical_content_hash(payload)
        return SleepJobReceiptV1.from_dict(payload)

    def _synthetic_result(
        self,
        context: JobContext,
        *,
        status: SleepJobStatus,
        reason_code: str,
        error_code: str | None,
    ) -> JobResult:
        seed = {
            "run_id": context.run_id,
            "job_id": context.job_id,
            "status": status.value,
            "reason_code": reason_code,
            "source_checkpoint_ids": list(context.source_checkpoint_ids),
        }
        digest = canonical_content_hash(seed)
        return JobResult(
            checkpoint_id=f"checkpoint-{context.job_id}-{status.value}",
            checkpoint_hash=digest,
            input_set_hash=_hash_ids(context.source_checkpoint_ids),
            output_set_hash=None,
            processed_count=0,
            skipped_count=0,
            output_bytes=0,
            source_ids=context.source_checkpoint_ids,
            status=status,
            reason_code=reason_code,
            error_code=error_code,
        )

    @staticmethod
    def _replace_result_status(
        result: JobResult,
        status: SleepJobStatus,
        reason_code: str,
        error_code: str | None,
    ) -> JobResult:
        return JobResult(
            checkpoint_id=result.checkpoint_id,
            checkpoint_hash=result.checkpoint_hash,
            input_set_hash=result.input_set_hash,
            output_set_hash=(
                result.output_set_hash
                if status in {SleepJobStatus.COMPLETED, SleepJobStatus.SKIPPED}
                else None
            ),
            processed_count=result.processed_count,
            skipped_count=result.skipped_count,
            output_bytes=result.output_bytes,
            source_ids=result.source_ids,
            candidate_version_ids=result.candidate_version_ids,
            status=status,
            reason_code=reason_code,
            error_code=error_code,
        )

    def _build_run_header(
        self,
        policy: SleepPolicyV1,
        *,
        run_id: str,
        trigger_kind: SleepTriggerKind,
        trigger_event_id: str,
        idempotency_key: str,
        source_checkpoint_ids: tuple[str, ...],
        preflight: _Preflight,
    ) -> SleepRunV1:
        specs = []
        for index, kind in enumerate(policy.allowed_job_kinds, start=1):
            job_seed = hashlib.sha256(
                f"{run_id}:{index}:{kind.value}".encode("utf-8")
            ).hexdigest()[:24]
            specs.append(
                {
                    "job_id": f"sleep-job-{job_seed}",
                    "kind": kind.value,
                    "max_records": policy.max_input_records,
                    "max_output_bytes": policy.max_output_bytes,
                    "max_seconds": policy.max_job_seconds,
                    "source_checkpoint_ids": list(source_checkpoint_ids),
                    "expected_output_type": _OUTPUT_TYPES[kind],
                    "network_default": "deny",
                }
            )
        timestamp = preflight.reading.utc_timestamp
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "policy_id": policy.policy_id,
            "policy_revision": policy.revision,
            "identity_id": policy.identity_id,
            "instance_id": self.instance_id,
            "owner_subject_id": policy.owner_subject_id,
            "trigger_kind": trigger_kind.value,
            "trigger_event_id": trigger_event_id,
            "idempotency_key": idempotency_key,
            "state": SleepRunState.SCHEDULED.value,
            "revision": 1,
            "scheduled_at_utc": timestamp,
            "started_at_utc": None,
            "ended_at_utc": None,
            "preflight_snapshot_hash": preflight.snapshot_hash,
            "source_checkpoint_ids": list(source_checkpoint_ids),
            "job_specs": specs,
            "current_job_id": specs[0]["job_id"] if specs else None,
            "completed_job_ids": [],
            "receipt_ids": [],
            "candidate_version_ids": [],
            "cancel_reason_code": None,
            "failure_reason_code": None,
            "resource_usage": {
                "max_run_seconds": policy.max_run_seconds,
                "max_job_seconds": policy.max_job_seconds,
                "max_input_records": policy.max_input_records,
                "max_output_bytes": policy.max_output_bytes,
            },
            "created_at_utc": timestamp,
            "updated_at_utc": timestamp,
            "privacy_class": policy.privacy_class.value,
            "retention_class": policy.retention_class.value,
            "provenance": {
                "adapter": "sleep_coordinator",
                "policy_content_hash": policy.content_hash,
                "source_ids": list(source_checkpoint_ids),
            },
        }
        payload["content_hash"] = canonical_content_hash(payload)
        return SleepRunV1.from_dict(payload)

    def _transition(
        self,
        snapshot: RunSnapshot,
        to_state: SleepRunState,
        reason_code: str,
        *,
        checkpoint_id: str | None = None,
        receipt_ids: tuple[str, ...] = (),
    ) -> RunSnapshot:
        seed = (
            f"{snapshot.run_id}:{snapshot.revision}:{snapshot.state.value}:"
            f"{to_state.value}:{reason_code}"
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        payload = {
            "schema_version": 1,
            "transition_id": f"sleep-transition-{digest[:32]}",
            "run_id": snapshot.run_id,
            "from_state": snapshot.state.value,
            "to_state": to_state.value,
            "expected_revision": snapshot.revision,
            "reason_code": reason_code,
            "checkpoint_id": checkpoint_id,
            "receipt_ids": list(receipt_ids),
            "created_at_utc": self.clock.read().utc_timestamp,
            "idempotency_key": f"sleep-transition-{digest}",
        }
        payload["content_hash"] = canonical_content_hash(payload)
        transition = SleepRunTransitionV1.from_dict(payload)
        try:
            return self.run_store.append_transition(transition)
        except RunStoreCASMismatchError:
            current = self._fresh_snapshot(snapshot.run_id)
            if current.state is to_state:
                return current
            raise

    def _recovery_evidence_valid(self, snapshot: RunSnapshot) -> bool:
        if not self.run_store.verify_integrity().ok:
            return False
        checkpoints = {
            checkpoint.checkpoint_id: checkpoint
            for checkpoint in self.run_store.list_job_checkpoints(snapshot.run_id, limit=256)
        }
        receipts = self.run_store.list_job_receipts(snapshot.run_id, limit=256)
        if not snapshot.header.source_checkpoint_ids and not checkpoints:
            return False
        return all(
            receipt.checkpoint_id is not None
            and receipt.checkpoint_id in checkpoints
            and checkpoints[receipt.checkpoint_id].job_id == receipt.job_id
            for receipt in receipts
        )

    def _job_source_checkpoints(
        self,
        run_id: str,
        job_id: str,
        original: tuple[str, ...],
    ) -> tuple[str, ...]:
        matching = tuple(
            checkpoint.checkpoint_id
            for checkpoint in self.run_store.list_job_checkpoints(run_id, limit=256)
            if checkpoint.job_id == job_id
        )
        return tuple(dict.fromkeys((*original, *matching)))

    def _grant_for(
        self,
        kind: SleepJobKind,
        grants: tuple[NetworkGrant, ...],
        now: datetime,
    ) -> NetworkGrant | None:
        matches = [
            grant
            for grant in grants
            if isinstance(grant, NetworkGrant)
            and grant.job_kind is kind
            and parse_utc_milliseconds(grant.expires_at_utc) > now
        ]
        if len(matches) != 1:
            return None
        return matches[0]

    def _cancel_active(self, reason: str) -> bool:
        active = self._active
        if active is None or active.cancellation.cancelled:
            return False
        active.cancellation.cancel(reason)
        return True

    def _fresh_snapshot(self, run_id: str) -> RunSnapshot:
        snapshot = self.run_store.get_run(run_id)
        if snapshot is None:
            raise SleepCoordinatorError("run disappeared from the append-only store")
        return snapshot

    @staticmethod
    def _receipt_id(context: JobContext, result: JobResult) -> str:
        seed = (
            f"{context.run_id}:{context.job_id}:{result.checkpoint_id}:"
            f"{result.status.value}"
        )
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        return f"sleep-receipt-{digest}"

    @staticmethod
    def _require_policy(policy: SleepPolicyV1) -> None:
        if not isinstance(policy, SleepPolicyV1):
            raise TypeError("policy must be SleepPolicyV1")


async def _read_probe(
    probe: Any,
    expected_type: type[_ProbeT],
    name: str,
) -> _ProbeT:
    value = probe.read()
    if inspect.isawaitable(value):
        value = await value
    if not isinstance(value, expected_type):
        raise TypeError(f"{name} probe returned an invalid snapshot")
    return value


async def _invoke(runner: JobRunner, context: JobContext) -> JobResult:
    result = runner(context)
    if inspect.isawaitable(result):
        result = await result
    return result


def _quiet_window_label(policy: SleepPolicyV1, now_utc: datetime) -> str | None:
    if policy.quiet_start_local is None or policy.quiet_end_local is None:
        return None
    local = now_utc.astimezone(ZoneInfo(policy.timezone))
    start_minutes = _local_minutes(policy.quiet_start_local)
    end_minutes = _local_minutes(policy.quiet_end_local)
    now_minutes = local.hour * 60 + local.minute
    window_date: date
    if start_minutes == end_minutes:
        window_date = local.date()
    elif start_minutes < end_minutes:
        if not start_minutes <= now_minutes < end_minutes:
            return None
        window_date = local.date()
    elif now_minutes >= start_minutes:
        window_date = local.date()
    elif now_minutes < end_minutes:
        window_date = local.date() - timedelta(days=1)
    else:
        return None
    # The identity is based on the local wall-clock window, not UTC offset/fold.
    # Both occurrences of a folded hour therefore resolve to exactly one key.
    return (
        f"{policy.timezone}:{window_date.isoformat()}:"
        f"{policy.quiet_start_local}-{policy.quiet_end_local}"
    )


def _local_minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _hash_ids(values: tuple[str, ...]) -> str:
    if not values:
        return _HASH_EMPTY
    return hashlib.sha256("\x00".join(values).encode("utf-8")).hexdigest()


def _require_hash(value: str, field_name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hash")


def _require_id(value: str, field_name: str) -> None:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a bounded ID")


def _supports_methods(value: Any, *names: str) -> bool:
    return all(callable(getattr(value, name, None)) for name in names)


__all__ = [
    "ALLOWED_JOB_KINDS",
    "HealthProbe",
    "HealthSnapshot",
    "IdleProbe",
    "IdleSnapshot",
    "JobBudget",
    "JobContext",
    "JobRegistrationError",
    "JobRegistry",
    "JobResult",
    "NetworkAccess",
    "NetworkGrant",
    "PowerProbe",
    "PowerSnapshot",
    "SleepCoordinator",
    "SleepCoordinatorClosedError",
    "SleepCoordinatorError",
]
