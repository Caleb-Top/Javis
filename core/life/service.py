"""Runtime-owned orchestration for the Javis L0 life kernel."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.events import Event, EventBus

from .contracts import (
    ExpressionIntent,
    IdentitySummary,
    InstanceSummary,
    LifeCycleState,
    LifeSnapshot,
)
from .event_adapter import LifeEventAdapter
from .expression import ExpressionProjector
from .identity import IdentityConstitutionStore, IdentityRecoveryRequired
from .journal import LifeEventJournal
from .lineage import InstanceLineageStore
from .state import InvalidLifeTransition, MinimalLifeStateMachine


logger = logging.getLogger("jarvis.life.service")

_STATE_EVENT_TYPES = frozenset(
    {
        "request.accepted",
        "request.completed",
        "request.cancelled",
        "request.failed",
        "voice.listening",
        "activity.understanding",
        "activity.thinking",
        "thinking.started",
        "voice.speaking",
        "response.speaking",
        "tool.started",
        "activity.tool_started",
        "approval.requested",
        "activity.blocked",
        "activity.error",
        "health.degraded",
        "health.recovered",
        "life.recovery.required",
        "client.offline",
        "client.reconnected",
    }
)
_REQUEST_BOUND_EVENTS = frozenset(
    {
        "request.accepted",
        "request.completed",
        "request.cancelled",
        "request.failed",
        "voice.listening",
        "activity.understanding",
        "activity.thinking",
        "thinking.started",
        "voice.speaking",
        "response.speaking",
        "tool.started",
        "activity.tool_started",
        "approval.requested",
        "activity.blocked",
        "activity.error",
    }
)

LifeListener = Callable[[LifeSnapshot, ExpressionIntent], None]


class LifeService:
    """Own one identity, lineage, lifecycle reducer and event journal."""

    name = "life"

    def __init__(
        self,
        data_root: str | Path,
        *,
        environment_fingerprint: str,
        now: Callable[[], float] | None = None,
        boot_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        if type(environment_fingerprint) is not str or not environment_fingerprint:
            raise ValueError("environment_fingerprint must be a non-empty string")
        self.environment_fingerprint = environment_fingerprint
        self._now = now or time.time
        self._boot_id_factory = boot_id_factory or (lambda: str(uuid.uuid4()))
        self.identity_store = IdentityConstitutionStore(self.data_root, now=self._now)
        self.lineage_store = InstanceLineageStore(self.data_root, now=self._now)
        self.journal = LifeEventJournal(
            self.data_root / "life" / "journal" / "life.sqlite3"
        )
        self._lock = threading.RLock()
        self._listeners: set[LifeListener] = set()
        self._runtime: Any | None = None
        self._event_bus: EventBus | None = None
        self._handler_installed = False
        self._accepting = False
        self._state = "new"
        self._boot_id: str | None = None
        self._previous_run_reason = "not_assessed"
        self._previous_run_unclean = False
        self._previous_event_cursor = 0
        self._read_only_recovery = False
        self._identity = None
        self._instance = None
        self._state_machine: MinimalLifeStateMachine | None = None
        self._expression_projector = ExpressionProjector()
        self._expression: ExpressionIntent | None = None
        self._last_error: str | None = None

    def verify_runtime(self, runtime: Any) -> None:
        runtime_root = Path(getattr(runtime, "data_root", "")).expanduser().resolve()
        if runtime_root != self.data_root:
            raise RuntimeError("LifeService data_root does not match runtime data_root")
        if not isinstance(getattr(runtime, "event_bus", None), EventBus):
            raise RuntimeError("LifeService requires the runtime EventBus")

    def start(self, runtime: Any) -> bool:
        with self._lock:
            if self._state != "new":
                return False
            self.verify_runtime(runtime)
            identity_is_new = not any(
                path.exists()
                for path in (
                    self.identity_store.current_path,
                    self.identity_store.versions_dir,
                    self.identity_store.audit_dir,
                    self.identity_store.recovery_path,
                )
            )
            instance_is_new = not self.lineage_store.active_path.is_file()
            recovery_reasons: list[str] = []
            try:
                identity = self.identity_store.load_or_create()
            except IdentityRecoveryRequired:
                identity = self.identity_store.load_last_verified()
                recovery_reasons.append("identity_recovery_required")
                self._read_only_recovery = True
            instance = self.lineage_store.load_or_create(
                identity.identity_id,
                self.environment_fingerprint,
            )
            assessment = self.lineage_store.assess_previous_run(instance.instance_id)
            boot_id = self._boot_id_factory()
            self.lineage_store.mark_started(instance.instance_id, boot_id=boot_id)
            now = self._now()
            machine = MinimalLifeStateMachine(
                identity=identity,
                instance=instance,
                initial_state=LifeCycleState.BOOTING,
                now=now,
            )
            if assessment.unclean_shutdown:
                recovery_reasons.append("unclean_shutdown")
            if instance.fork_pending_review:
                recovery_reasons.append("environment_fork_pending_review")
            if recovery_reasons:
                machine.apply(
                    "health.degraded",
                    {
                        "components": ["continuity"],
                        "reason_codes": recovery_reasons,
                        "degradation_level": 1,
                    },
                    now,
                )
                machine.apply(
                    "life.recovery.required",
                    {"reason_codes": recovery_reasons},
                    now,
                )
            else:
                machine.apply("runtime.ready", {}, now)
            self._runtime = runtime
            self._event_bus = runtime.event_bus
            self._identity = identity
            self._instance = instance
            self._state_machine = machine
            self._boot_id = boot_id
            self._previous_run_reason = assessment.reason_code
            self._previous_run_unclean = assessment.unclean_shutdown
            self._previous_event_cursor = assessment.last_event_cursor
            self._expression = self._expression_projector.project(
                machine.snapshot(),
                now,
            )
            if not self.journal.start():
                raise RuntimeError("LifeService journal failed to start")
            self._accepting = True
            self._state = "running"
            if not self._handler_installed:
                self._event_bus.subscribe("*", self._handle_event)
                self._handler_installed = True
            if identity_is_new:
                self._event_bus.publish(
                    "life.identity.created",
                    {"version": identity.version},
                    source="life",
                )
            if instance_is_new:
                self._event_bus.publish(
                    "life.instance.created",
                    {
                        "generation": instance.generation,
                        "fork_pending_review": instance.fork_pending_review,
                    },
                    source="life",
                )
            if recovery_reasons:
                self._event_bus.publish(
                    "life.recovery.required",
                    {"reason_codes": recovery_reasons},
                    source="life",
                )
            return True

    def stop(self, timeout: float = 2.0) -> bool:
        with self._lock:
            if self._state == "stopped":
                return True
            if self._state == "new":
                self._state = "stopped"
                return self.journal.stop(timeout=timeout)
            self._accepting = False
            machine = self._state_machine
            instance = self._instance
            boot_id = self._boot_id
            checkpoint_active_request_id = (
                machine.snapshot().active_request_id if machine is not None else None
            )
            if machine is not None:
                try:
                    machine.apply("runtime.stopping", {}, self._now())
                except InvalidLifeTransition:
                    pass
        flushed = self.journal.flush(timeout=timeout)
        checkpointed = False
        try:
            if flushed and instance is not None and boot_id is not None:
                self.lineage_store.mark_clean_shutdown(
                    instance.instance_id,
                    boot_id=boot_id,
                    last_event_cursor=self.journal.cursor,
                    active_request_id=checkpoint_active_request_id,
                )
                checkpointed = True
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            logger.warning("LifeService clean checkpoint failed: %s", exc)
        journal_stopped = self.journal.stop(timeout=timeout)
        with self._lock:
            complete = flushed and checkpointed and journal_stopped
            self._state = "stopped" if complete else "degraded"
            return complete

    def snapshot(self) -> LifeSnapshot:
        with self._lock:
            if self._state_machine is None:
                raise RuntimeError("LifeService has not started")
            return self._state_machine.snapshot()

    def expression(self) -> ExpressionIntent:
        with self._lock:
            if self._expression is None:
                raise RuntimeError("LifeService has not started")
            return self._expression

    def identity_summary(self) -> IdentitySummary:
        return self.snapshot().identity

    def lineage_summary(self) -> InstanceSummary:
        return self.snapshot().instance

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.journal.recent(limit=limit)

    def subscribe(self, listener: LifeListener) -> Callable[[], None]:
        if not callable(listener):
            raise TypeError("listener must be callable")
        with self._lock:
            self._listeners.add(listener)

        def unsubscribe() -> None:
            with self._lock:
                self._listeners.discard(listener)

        return unsubscribe

    def status(self) -> dict[str, Any]:
        journal_status = self.journal.status()
        with self._lock:
            lifecycle = (
                self._state_machine.snapshot().lifecycle_state.value
                if self._state_machine is not None
                else "offline"
            )
            return {
                "state": self._state,
                "lifecycle_state": lifecycle,
                "journal_state": journal_status["state"],
                "journal_accepted": journal_status["accepted"],
                "journal_persisted": journal_status["persisted"],
                "journal_pending": journal_status["pending"],
                "journal_cursor": self.journal.cursor,
                "boot_id": self._boot_id,
                "previous_run_reason": self._previous_run_reason,
                "previous_run_unclean": self._previous_run_unclean,
                "previous_event_cursor": self._previous_event_cursor,
                "read_only_recovery": self._read_only_recovery,
                "handler_installed": self._handler_installed,
                "accepting": self._accepting,
                "last_error": self._last_error or journal_status["last_error"],
            }

    def _handle_event(self, event: Event) -> None:
        listeners: tuple[LifeListener, ...] = ()
        snapshot: LifeSnapshot | None = None
        expression: ExpressionIntent | None = None
        with self._lock:
            if not self._accepting or self._state_machine is None:
                return
            adapter = LifeEventAdapter(
                identity_id=self._identity.identity_id,
                instance_id=self._instance.instance_id,
                now=self._now,
            )
            life_events = adapter.map(event)
            for life_event in life_events:
                self.journal.enqueue(life_event)
            state_payload = self._state_payload(event, life_events)
            if state_payload is None:
                return
            try:
                changed = self._state_machine.apply(
                    event.type,
                    state_payload,
                    event.timestamp,
                )
            except (InvalidLifeTransition, TypeError, ValueError) as exc:
                self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                return
            if not changed:
                return
            snapshot = self._state_machine.snapshot()
            expression = self._expression_projector.project(snapshot, event.timestamp)
            self._expression = expression
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot, expression)
            except Exception as exc:
                logger.warning("LifeService listener failed: %s", exc)

    @staticmethod
    def _state_payload(
        event: Event,
        life_events: list,
    ) -> dict[str, Any] | None:
        if event.type not in _STATE_EVENT_TYPES or not life_events:
            return None
        mapped = life_events[0]
        payload: dict[str, Any] = dict(mapped.to_dict()["payload"])
        payload["event_id"] = mapped.event_id
        payload["sequence"] = event.sequence
        if mapped.session_id is not None:
            payload["session_id"] = mapped.session_id
        if mapped.request_id is not None:
            payload["request_id"] = mapped.request_id
        if event.type in _REQUEST_BOUND_EVENTS and "request_id" not in payload:
            return None
        return payload


__all__ = ["LifeService"]
