"""Single runtime boundary for governed L7 sleep and growth work."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.events import Event
from core.subsystem import SubsystemStatus

from .growth_store import GrowthStore
from .layout import DataRootLayout
from .legacy import LegacySleepLearningQuarantine
from .run_store import RunStore
from .sleep import (
    HealthSnapshot,
    IdleSnapshot,
    JobRegistry,
    PowerSnapshot,
    SleepCoordinator,
)


logger = logging.getLogger("jarvis.life.l7.supervisor")

_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}")
_HASH = re.compile(r"[0-9a-f]{64}")
_LAYERS = frozenset({"l2", "l3", "l4", "l5", "l6"})
_KINDS = frozenset({"receipt", "checkpoint"})
_PRIVATE_EVENT_KEYS = frozenset(
    {
        "audio_bytes",
        "authorization",
        "chain_of_thought",
        "credential",
        "file_content",
        "hidden_reasoning",
        "image_bytes",
        "password",
        "raw_audio",
        "raw_payload",
        "raw_screen",
        "screen_pixels",
        "secret",
        "source_code",
        "source_text",
        "token",
        "video_bytes",
    }
)
_RUNTIME_OWNER_LOCK = threading.Lock()


class L7SupervisorError(RuntimeError):
    pass


class L7SupervisorConflictError(L7SupervisorError):
    pass


class L7SupervisorClosedError(L7SupervisorError):
    pass


class UpstreamAuthorityError(L7SupervisorError):
    pass


@dataclass(frozen=True, slots=True)
class AuthoritySpec:
    event_type: str
    layer: Literal["l2", "l3", "l4", "l5", "l6"]
    record_kind: Literal["receipt", "checkpoint"]
    id_field: str
    record_id_field: str
    contract_type: type[Any]

    def __post_init__(self) -> None:
        for name in ("event_type", "id_field", "record_id_field"):
            value = getattr(self, name)
            if type(value) is not str or _CODE.fullmatch(value) is None:
                raise ValueError(f"{name} must be a bounded code")
        if self.layer not in _LAYERS:
            raise ValueError("layer must be one of L2-L6")
        if self.record_kind not in _KINDS:
            raise ValueError("record_kind must be receipt or checkpoint")
        if not callable(getattr(self.contract_type, "from_dict", None)):
            raise TypeError("contract_type must expose strict from_dict validation")


@dataclass(frozen=True, slots=True)
class AuthorityReference:
    event_id: str
    event_type: str
    layer: str
    record_kind: str
    record_id: str


@dataclass(frozen=True, slots=True)
class ValidatedUpstreamRecord:
    layer: str
    record_kind: str
    record_id: str
    schema_version: int
    content_hash: str
    source_event_id: str


class RuntimeAuthorityAdapter:
    """Re-read thin event references from authoritative L2-L6 services."""

    def __init__(
        self,
        runtime: Any,
        *,
        specs: tuple[AuthoritySpec, ...] | None = None,
        resolver: Callable[[str, str, str], Any] | None = None,
    ) -> None:
        self.runtime = runtime
        configured = specs if specs is not None else _default_authority_specs()
        by_event: dict[str, AuthoritySpec] = {}
        for spec in configured:
            if not isinstance(spec, AuthoritySpec):
                raise TypeError("specs must contain AuthoritySpec values")
            if spec.event_type in by_event:
                raise ValueError("authority event types must be unique")
            by_event[spec.event_type] = spec
        self._specs = by_event
        self._resolver = resolver

    @property
    def event_types(self) -> tuple[str, ...]:
        return tuple(self._specs)

    def parse_event(self, event: Event) -> AuthorityReference:
        if not isinstance(event, Event):
            raise UpstreamAuthorityError("authority input must be an Event")
        spec = self._specs.get(event.type)
        if spec is None:
            raise UpstreamAuthorityError("event type is not an L2-L6 authority event")
        if event.schema_version != 1:
            raise UpstreamAuthorityError("authority event schema is unsupported")
        _validate_thin_payload(event.payload)
        if set(event.payload) != {spec.id_field}:
            raise UpstreamAuthorityError("authority event must contain only its record ID")
        record_id = event.payload.get(spec.id_field)
        if type(record_id) is not str or _ID.fullmatch(record_id) is None:
            raise UpstreamAuthorityError("authority event has an invalid record ID")
        return AuthorityReference(
            event_id=event.id,
            event_type=event.type,
            layer=spec.layer,
            record_kind=spec.record_kind,
            record_id=record_id,
        )

    def resolve(self, reference: AuthorityReference) -> ValidatedUpstreamRecord:
        spec = self._specs.get(reference.event_type)
        if spec is None:
            raise UpstreamAuthorityError("authority event is no longer configured")
        value = (
            self._resolver(spec.layer, spec.record_kind, reference.record_id)
            if self._resolver is not None
            else self._resolve_runtime(spec, reference.record_id)
        )
        if inspect.isawaitable(value):
            raise UpstreamAuthorityError("async authority resolution requires worker dispatch")
        if isinstance(value, Future):
            value = value.result(timeout=5.0)
        if value is None:
            raise UpstreamAuthorityError("authoritative record is unavailable")
        if isinstance(value, Mapping):
            wire = dict(value)
        else:
            to_dict = getattr(value, "to_dict", None)
            if not callable(to_dict):
                raise UpstreamAuthorityError("authoritative record has no strict wire form")
            wire = to_dict()
        try:
            contract = spec.contract_type.from_dict(wire)
        except (TypeError, ValueError) as exc:
            raise UpstreamAuthorityError("authoritative record failed schema validation") from exc
        if getattr(contract, "schema_version", None) != 1:
            raise UpstreamAuthorityError("authoritative record schema is unsupported")
        if getattr(contract, spec.record_id_field, None) != reference.record_id:
            raise UpstreamAuthorityError("authoritative record identity does not match the event")
        content_hash = getattr(contract, "content_hash", None)
        if content_hash is None:
            content_hash = hashlib.sha256(
                json.dumps(
                    wire,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        if type(content_hash) is not str or _HASH.fullmatch(content_hash) is None:
            raise UpstreamAuthorityError("authoritative record has an invalid content hash")
        return ValidatedUpstreamRecord(
            layer=spec.layer,
            record_kind=spec.record_kind,
            record_id=reference.record_id,
            schema_version=1,
            content_hash=content_hash,
            source_event_id=reference.event_id,
        )

    def _resolve_runtime(self, spec: AuthoritySpec, record_id: str) -> Any:
        resolver = getattr(self.runtime, "l7_authority_resolver", None)
        if callable(resolver):
            return resolver(spec.layer, spec.record_kind, record_id)

        method_names = {
            "receipt": ("get_receipt", "get_action_receipt", "get_recovery_receipt"),
            "checkpoint": ("get_checkpoint", "get_snapshot", "get_relationship_event"),
        }[spec.record_kind]
        service_names = {
            "l2": ("memory_service", "life"),
            "l3": ("relationship_service", "memory_service"),
            "l4": ("environment_service", "environment_store"),
            "l5": ("intention_service", "intention_store"),
            "l6": ("action_service", "action_executor", "action_store"),
        }[spec.layer]
        for service_name in service_names:
            service = getattr(self.runtime, service_name, None)
            if service is None:
                continue
            for owner in (service, getattr(service, "store", None)):
                if owner is None:
                    continue
                for method_name in method_names:
                    method = getattr(owner, method_name, None)
                    if callable(method):
                        return method(record_id)
        raise UpstreamAuthorityError("runtime has no authority adapter for this record")


@dataclass(slots=True)
class _RunCommand:
    mode: str
    policy: Any
    trigger_event_id: str
    source_checkpoint_ids: tuple[str, ...]
    result: Future[Any]


_STOP = object()


class _Probe:
    def __init__(self, value: Any) -> None:
        self.value = value

    def read(self) -> Any:
        return self.value


class _RuntimeHealthProbe:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.policy_revision = 1
        self.approval_id: str | None = None

    def bind_policy(self, policy: Any) -> None:
        self.policy_revision = int(getattr(policy, "revision", 1))
        self.approval_id = getattr(policy, "approval_id", None)

    def read(self) -> HealthSnapshot:
        life = getattr(self.runtime, "life", None)
        state = getattr(life, "status", lambda: {"state": "degraded"})()
        healthy = state.get("state") == "running"
        return HealthSnapshot(
            policy_revision=self.policy_revision,
            approval_id=self.approval_id,
            cpu_percent=0.0,
            free_bytes=0,
            runtime_healthy=healthy,
            data_root_healthy=True,
            approval_valid=self.approval_id is not None,
            active_action=False,
            shutdown_requested=False,
        )


class L7Supervisor:
    """Own L7 stores, authority ingestion, and the only SleepCoordinator."""

    name = "l7"

    def __init__(
        self,
        *,
        queue_capacity: int = 128,
        validated_capacity: int = 512,
        authority_adapter: RuntimeAuthorityAdapter | None = None,
        run_store_factory: Callable[[DataRootLayout], Any] = RunStore,
        growth_store_factory: Callable[[DataRootLayout], Any] = GrowthStore,
        coordinator_factory: Callable[..., Any] | None = None,
    ) -> None:
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 4_096:
            raise ValueError("queue_capacity must be in [1, 4096]")
        if type(validated_capacity) is not int or not 1 <= validated_capacity <= 8_192:
            raise ValueError("validated_capacity must be in [1, 8192]")
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_capacity)
        self._validated_capacity = validated_capacity
        self._authority_adapter = authority_adapter
        self._run_store_factory = run_store_factory
        self._growth_store_factory = growth_store_factory
        self._coordinator_factory = coordinator_factory
        self._lock = threading.RLock()
        self._runtime: Any | None = None
        self._thread: threading.Thread | None = None
        self._worker_ready = threading.Event()
        self._worker_stopped = threading.Event()
        self._state = "new"
        self._accepting = False
        self._worker_active = False
        self._last_error: str | None = None
        self._accepted = 0
        self._rejected = 0
        self._overloaded = 0
        self._processed = 0
        self._validated_order: deque[tuple[str, str]] = deque()
        self._validated: dict[tuple[str, str], ValidatedUpstreamRecord] = {}
        self.layout: DataRootLayout | None = None
        self.run_store: Any | None = None
        self.growth_store: Any | None = None
        self.coordinator: Any | None = None
        self.legacy: LegacySleepLearningQuarantine | None = None
        self._health_probe: _RuntimeHealthProbe | None = None

    def start(self, runtime: Any) -> bool:
        with self._lock:
            if self._state == "running":
                return False
            if self._state != "new":
                raise L7SupervisorClosedError("L7Supervisor cannot be restarted")
            self._claim_runtime(runtime)
            self._state = "starting"
        try:
            layout = DataRootLayout.from_runtime(runtime).ensure_directories()
            authority = self._authority_adapter or RuntimeAuthorityAdapter(runtime)
            run_store = self._run_store_factory(layout)
            growth_store = self._growth_store_factory(layout)
            coordinator = self._build_coordinator(
                runtime,
                layout=layout,
                run_store=run_store,
                growth_store=growth_store,
                authority_adapter=authority,
            )
            legacy = LegacySleepLearningQuarantine(
                source_root=Path(runtime.root),
                data_root=layout.data_root,
            )
            with self._lock:
                self._runtime = runtime
                self.layout = layout
                self._authority_adapter = authority
                self.run_store = run_store
                self.growth_store = growth_store
                self.coordinator = coordinator
                self.legacy = legacy
                self._accepting = True
                self._state = "running"
            for event_type in authority.event_types:
                runtime.event_bus.subscribe(event_type, self.enqueue_event)
            thread = threading.Thread(
                target=self._worker_main,
                name="javis-l7-supervisor",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            if not self._worker_ready.wait(timeout=2.0):
                raise L7SupervisorError("L7 worker failed to start")
            return True
        except BaseException:
            with self._lock:
                self._accepting = False
                self._state = "degraded"
            self._close_stores()
            self._release_runtime()
            raise

    def enqueue_event(self, event: Event) -> bool:
        with self._lock:
            if not self._accepting or self._authority_adapter is None:
                return False
            authority = self._authority_adapter
        try:
            reference = authority.parse_event(event)
        except (TypeError, ValueError, UpstreamAuthorityError) as exc:
            self._record_rejection(exc)
            return False
        try:
            self._queue.put_nowait(reference)
        except queue.Full:
            with self._lock:
                self._overloaded += 1
            return False
        with self._lock:
            self._accepted += 1
        return True

    def submit_manual_run(
        self,
        policy: Any,
        *,
        trigger_event_id: str,
        source_checkpoint_ids: tuple[str, ...] | None = None,
    ) -> Future[Any]:
        return self._submit_run(
            "manual",
            policy,
            trigger_event_id=trigger_event_id,
            source_checkpoint_ids=source_checkpoint_ids,
        )

    def submit_tick(
        self,
        policy: Any,
        *,
        trigger_event_id: str,
        source_checkpoint_ids: tuple[str, ...] | None = None,
    ) -> Future[Any]:
        return self._submit_run(
            "tick",
            policy,
            trigger_event_id=trigger_event_id,
            source_checkpoint_ids=source_checkpoint_ids,
        )

    def _submit_run(
        self,
        mode: str,
        policy: Any,
        *,
        trigger_event_id: str,
        source_checkpoint_ids: tuple[str, ...] | None,
    ) -> Future[Any]:
        with self._lock:
            if not self._accepting:
                raise L7SupervisorClosedError("L7Supervisor is not accepting runs")
        if type(trigger_event_id) is not str or _ID.fullmatch(trigger_event_id) is None:
            raise ValueError("trigger_event_id must be a bounded ID")
        source_ids = (
            tuple(source_checkpoint_ids)
            if source_checkpoint_ids is not None
            else self.validated_checkpoint_ids()
        )
        if any(type(value) is not str or _ID.fullmatch(value) is None for value in source_ids):
            raise ValueError("source_checkpoint_ids must contain bounded IDs")
        result: Future[Any] = Future()
        command = _RunCommand(mode, policy, trigger_event_id, source_ids, result)
        try:
            self._queue.put_nowait(command)
        except queue.Full as exc:
            with self._lock:
                self._overloaded += 1
            raise L7SupervisorError("L7 work queue is full") from exc
        return result

    def begin_shutdown(self) -> bool:
        with self._lock:
            if self._state in {"stopped", "degraded"}:
                return False
            was_accepting = self._accepting
            self._accepting = False
            if self._state == "running":
                self._state = "stopping"
            coordinator = self.coordinator
        begin = getattr(coordinator, "begin_shutdown", None)
        if callable(begin):
            try:
                begin()
            except Exception as exc:
                self._record_error(exc)
        return was_accepting

    def stop(self, timeout: float = 5.0) -> bool:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._lock:
            if self._state == "stopped":
                return True
            if self._state == "new":
                self._state = "stopped"
                return True
        self.begin_shutdown()
        deadline = time.monotonic() + float(timeout)
        try:
            self._queue.put(_STOP, timeout=max(0.001, deadline - time.monotonic()))
        except queue.Full:
            self._record_error(L7SupervisorError("worker stop signal timed out"))
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        stopped = self._worker_stopped.is_set()
        with self._lock:
            self._state = "stopped" if stopped and self._last_error is None else "degraded"
        if stopped:
            self._release_runtime()
        return stopped and self._last_error is None

    close = stop

    def wait_for_idle(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                active = self._worker_active
            if self._queue.unfinished_tasks == 0 and not active:
                return True
            time.sleep(0.005)
        return False

    def validated_checkpoint_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._validated[key].record_id for key in self._validated_order)

    def status(self) -> SubsystemStatus:
        with self._lock:
            return SubsystemStatus(
                state=self._state,
                detail="" if self._last_error is None else self._last_error,
                metrics={
                    "accepted": self._accepted,
                    "processed": self._processed,
                    "rejected": self._rejected,
                    "overloaded": self._overloaded,
                    "pending": self._queue.qsize(),
                    "validated": len(self._validated),
                    "accepting_runs": bool(
                        self._accepting
                        and getattr(self.coordinator, "accepting_runs", False)
                    ),
                    "active_run": getattr(self.coordinator, "active_run_id", None),
                },
            )

    def _worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._worker_ready.set()
        try:
            while True:
                item = self._queue.get()
                with self._lock:
                    self._worker_active = True
                try:
                    if item is _STOP:
                        break
                    if isinstance(item, AuthorityReference):
                        self._process_reference(item, loop)
                    elif isinstance(item, _RunCommand):
                        self._process_run(item, loop)
                    else:
                        self._record_rejection(TypeError("unknown L7 work item"))
                except Exception as exc:
                    self._record_rejection(exc)
                finally:
                    with self._lock:
                        self._worker_active = False
                    self._queue.task_done()
        finally:
            try:
                shutdown = getattr(self.coordinator, "shutdown", None)
                if callable(shutdown):
                    value = shutdown()
                    if inspect.isawaitable(value):
                        loop.run_until_complete(value)
            except Exception as exc:
                self._record_error(exc)
            self._reject_pending_commands()
            self._close_stores()
            loop.close()
            self._worker_stopped.set()

    def _process_reference(
        self,
        reference: AuthorityReference,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        assert self._authority_adapter is not None
        try:
            record = self._authority_adapter.resolve(reference)
        except UpstreamAuthorityError:
            raise
        if inspect.isawaitable(record):
            record = loop.run_until_complete(record)
        key = (record.layer, record.record_id)
        with self._lock:
            existing = self._validated.get(key)
            if existing is not None:
                if existing.content_hash != record.content_hash:
                    raise UpstreamAuthorityError(
                        "authority record ID changed content hash"
                    )
                self._processed += 1
                return
            if len(self._validated_order) >= self._validated_capacity:
                evicted = self._validated_order.popleft()
                self._validated.pop(evicted, None)
            self._validated_order.append(key)
            self._validated[key] = record
            self._processed += 1

    def _process_run(self, command: _RunCommand, loop: asyncio.AbstractEventLoop) -> None:
        if not self._accepting:
            command.result.set_exception(
                L7SupervisorClosedError("L7Supervisor is shutting down")
            )
            return
        if self._health_probe is not None:
            self._health_probe.bind_policy(command.policy)
        try:
            if command.mode == "manual":
                operation = self.coordinator.request_manual(
                    command.policy,
                    trigger_event_id=command.trigger_event_id,
                    source_checkpoint_ids=command.source_checkpoint_ids,
                )
            else:
                operation = self.coordinator.tick(
                    command.policy,
                    trigger_event_id=command.trigger_event_id,
                    source_checkpoint_ids=command.source_checkpoint_ids,
                )
            command.result.set_result(loop.run_until_complete(operation))
        except BaseException as exc:
            if not command.result.done():
                command.result.set_exception(exc)

    def _build_coordinator(self, runtime: Any, **dependencies: Any) -> Any:
        if self._coordinator_factory is not None:
            return self._coordinator_factory(runtime=runtime, **dependencies)
        snapshot = runtime.life.snapshot()
        self._health_probe = _RuntimeHealthProbe(runtime)
        return SleepCoordinator(
            power_probe=_Probe(PowerSnapshot(on_ac_power=False, battery_percent=0)),
            idle_probe=_Probe(
                IdleSnapshot(
                    idle_seconds=0,
                    user_active=True,
                    playback_active=False,
                )
            ),
            health_probe=self._health_probe,
            run_store=dependencies["run_store"],
            growth_store=dependencies["growth_store"],
            job_registry=JobRegistry(),
            instance_id=snapshot.instance.instance_id,
        )

    def _claim_runtime(self, runtime: Any) -> None:
        runtime_root = Path(getattr(runtime, "data_root", "")).expanduser().resolve()
        if not runtime_root.is_absolute():
            raise L7SupervisorError("runtime data_root must be absolute")
        if getattr(runtime, "event_bus", None) is None:
            raise L7SupervisorError("L7Supervisor requires the runtime EventBus")
        with _RUNTIME_OWNER_LOCK:
            existing = getattr(runtime, "_l7_supervisor_owner", None)
            if existing is not None and existing is not self:
                raise L7SupervisorConflictError("runtime already has an L7Supervisor")
            registered = getattr(runtime, "subsystems", {}).get(self.name)
            if registered is not None and registered is not self:
                raise L7SupervisorConflictError("runtime L7 subsystem is already registered")
            setattr(runtime, "_l7_supervisor_owner", self)
            getattr(runtime, "subsystems", {})[self.name] = self

    def _release_runtime(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        with _RUNTIME_OWNER_LOCK:
            if getattr(runtime, "_l7_supervisor_owner", None) is self:
                delattr(runtime, "_l7_supervisor_owner")

    def _record_rejection(self, exc: BaseException) -> None:
        with self._lock:
            self._rejected += 1
        logger.debug("L7 authority input rejected: %s", type(exc).__name__)

    def _record_error(self, exc: BaseException) -> None:
        with self._lock:
            self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        logger.warning("L7 supervisor degraded: %s", type(exc).__name__)

    def _reject_pending_commands(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                if isinstance(item, _RunCommand) and not item.result.done():
                    item.result.set_exception(
                        L7SupervisorClosedError("L7Supervisor stopped before execution")
                    )
            finally:
                self._queue.task_done()

    def _close_stores(self) -> None:
        for store in (self.growth_store, self.run_store):
            close = getattr(store, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    self._record_error(exc)


def _validate_thin_payload(value: Mapping[str, Any]) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise UpstreamAuthorityError("authority event payload is not bounded JSON") from exc
    if len(encoded) > 4_096:
        raise UpstreamAuthorityError("authority event payload exceeds the thin-event limit")

    def visit(node: Any, depth: int) -> None:
        if depth > 6:
            raise UpstreamAuthorityError("authority event payload is too deeply nested")
        if isinstance(node, Mapping):
            for key, child in node.items():
                if type(key) is not str or key.casefold() in _PRIVATE_EVENT_KEYS:
                    raise UpstreamAuthorityError("authority event contains a private field")
                visit(child, depth + 1)
        elif isinstance(node, (list, tuple)):
            if len(node) > 64:
                raise UpstreamAuthorityError("authority event collection is too large")
            for child in node:
                visit(child, depth + 1)
        elif node is not None and not isinstance(node, (str, int, float, bool)):
            raise UpstreamAuthorityError("authority event contains a non-JSON value")

    visit(value, 0)


def _default_authority_specs() -> tuple[AuthoritySpec, ...]:
    from core.action.contracts import ActionReceiptV1, RecoveryReceiptV1
    from core.environment.contracts import EnvironmentSnapshotV1
    from core.intention.contracts import ThinkingCheckpointV1
    from core.life.l1.contracts import TurnExperienceReceipt
    from core.life.memory.contracts import RelationshipEvent

    return (
        AuthoritySpec(
            "memory.receipt.committed",
            "l2",
            "receipt",
            "receipt_id",
            "receipt_id",
            TurnExperienceReceipt,
        ),
        AuthoritySpec(
            "relationship.event.committed",
            "l3",
            "checkpoint",
            "relationship_event_id",
            "relationship_event_id",
            RelationshipEvent,
        ),
        AuthoritySpec(
            "environment.snapshot.committed",
            "l4",
            "checkpoint",
            "snapshot_id",
            "snapshot_id",
            EnvironmentSnapshotV1,
        ),
        AuthoritySpec(
            "intention.checkpoint.created",
            "l5",
            "checkpoint",
            "checkpoint_id",
            "checkpoint_id",
            ThinkingCheckpointV1,
        ),
        AuthoritySpec(
            "action.receipt.committed",
            "l6",
            "receipt",
            "receipt_id",
            "receipt_id",
            ActionReceiptV1,
        ),
        AuthoritySpec(
            "action.recovery_receipt.committed",
            "l6",
            "receipt",
            "recovery_receipt_id",
            "recovery_receipt_id",
            RecoveryReceiptV1,
        ),
    )


__all__ = [
    "AuthorityReference",
    "AuthoritySpec",
    "L7Supervisor",
    "L7SupervisorClosedError",
    "L7SupervisorConflictError",
    "L7SupervisorError",
    "RuntimeAuthorityAdapter",
    "UpstreamAuthorityError",
    "ValidatedUpstreamRecord",
]
