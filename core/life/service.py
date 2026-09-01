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
    ExpressionBaseState,
    GazeTarget,
    IdentitySummary,
    InstanceSummary,
    LifeCycleState,
    LifeSnapshot,
    VoiceActivity,
)
from .event_adapter import LifeEventAdapter
from .expression import ExpressionProjector
from .identity import IdentityConstitutionStore, IdentityRecoveryRequired
from .journal import LifeEventJournal, TurnReceiptJournal
from .l1.affect import FunctionalAffectProjector
from .l1.appraisal import AppraisalReducer, playback_target_id
from .l1.attention import AttentionCoordinator
from .l1.clock import Clock, SystemClock, coerce_utc
from .l1.contracts import (
    AttentionMode,
    InnerStateSnapshot,
    LifeObservation,
    ObservationKind,
    PlaybackLifecycleEvent,
    PresenceSnapshot,
    ReasonCode,
    ResponsePath,
    TurnExperienceReceipt,
)
from .l1.observation_bridge import GovernedObservationBridge, ProjectionRejected
from .l1.receipts import TurnExperienceProjector
from .l1.state import HomeostasisReducer
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
        clock: Clock | None = None,
    ) -> None:
        self.data_root = Path(data_root).expanduser().resolve()
        if type(environment_fingerprint) is not str or not environment_fingerprint:
            raise ValueError("environment_fingerprint must be a non-empty string")
        self.environment_fingerprint = environment_fingerprint
        self._now = now or time.time
        self._l1_clock = clock or SystemClock()
        self._boot_id_factory = boot_id_factory or (lambda: str(uuid.uuid4()))
        self.identity_store = IdentityConstitutionStore(self.data_root, now=self._now)
        self.lineage_store = InstanceLineageStore(self.data_root, now=self._now)
        self.journal = LifeEventJournal(
            self.data_root / "life" / "journal" / "life.sqlite3"
        )
        self.receipt_journal = TurnReceiptJournal(self.journal.path)
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
        self._observation_bridge: GovernedObservationBridge | None = None
        self._appraisal_reducer = AppraisalReducer()
        self._homeostasis: HomeostasisReducer | None = None
        self._attention: AttentionCoordinator | None = None
        self._affect: FunctionalAffectProjector | None = None
        self._receipt_projector: TurnExperienceProjector | None = None
        self._inner_state: InnerStateSnapshot | None = None
        self._last_reason_code = ReasonCode.QUIET_BASELINE
        self._presence_session_id: str | None = None
        self._expiry_timer: threading.Timer | None = None
        self._expiry_generation = 0
        self._expiry_sequence = 0
        self._last_error: str | None = None
        self._l7_supervisor: Any | None = None

    def attach_l7_supervisor(self, supervisor: Any) -> None:
        if getattr(supervisor, "name", None) != "l7":
            raise TypeError("supervisor must be the L7 subsystem")
        with self._lock:
            if self._l7_supervisor not in (None, supervisor):
                raise RuntimeError("LifeService already has an L7 supervisor")
            self._l7_supervisor = supervisor

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
            reading = self._l1_clock.read()
            observation_bridge = GovernedObservationBridge(
                runtime_boot_id=boot_id,
                clock=self._l1_clock,
                boot_monotonic_seconds=reading.monotonic_seconds,
            )
            homeostasis = HomeostasisReducer(reading)
            attention = AttentionCoordinator(reading.utc_timestamp)
            affect = FunctionalAffectProjector()
            receipt_projector = TurnExperienceProjector()
            recovered_receipts = receipt_projector.recover_after_restart(
                self._persisted_receipts_for_restart(),
                recovered_at_utc=reading.utc_timestamp,
                recovery_event_id=f"restart:{boot_id}",
            )
            self._runtime = runtime
            self._event_bus = runtime.event_bus
            self._identity = identity
            self._instance = instance
            self._state_machine = machine
            self._observation_bridge = observation_bridge
            self._homeostasis = homeostasis
            self._attention = attention
            self._affect = affect
            self._receipt_projector = receipt_projector
            self._boot_id = boot_id
            self._previous_run_reason = assessment.reason_code
            self._previous_run_unclean = assessment.unclean_shutdown
            self._previous_event_cursor = assessment.last_event_cursor
            self._inner_state = self._build_inner_state_locked(reading)
            self._expression = self._project_expression_locked(reading)
            if not self.journal.start():
                raise RuntimeError("LifeService journal failed to start")
            if not self.receipt_journal.start():
                self.journal.stop(timeout=1.0)
                raise RuntimeError("LifeService receipt journal failed to start")
            for receipt in recovered_receipts:
                if self.receipt_journal.enqueue(receipt):
                    continue
                if not self.receipt_journal.flush(timeout=2.0):
                    raise RuntimeError("LifeService receipt recovery failed")
                if not self.receipt_journal.enqueue(receipt):
                    raise RuntimeError("LifeService receipt recovery queue rejected data")
            if recovered_receipts and not self.receipt_journal.flush(timeout=2.0):
                raise RuntimeError("LifeService receipt recovery failed")
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
            prompt_builder = getattr(getattr(runtime, "agent", None), "prompt_builder", None)
            if prompt_builder is not None and hasattr(
                prompt_builder, "set_runtime_state_provider"
            ):
                prompt_builder.set_runtime_state_provider(self.inner_state_snapshot)
            return True

    def stop(self, timeout: float = 2.0) -> bool:
        supervisor = self._l7_supervisor
        begin_l7_shutdown = getattr(supervisor, "begin_shutdown", None)
        if callable(begin_l7_shutdown):
            try:
                begin_l7_shutdown()
            except Exception as exc:
                logger.warning("L7 shutdown notification failed: %s", type(exc).__name__)
        with self._lock:
            if self._state == "stopped":
                return True
            if self._state == "new":
                self._state = "stopped"
                journal_stopped = self.journal.stop(timeout=timeout)
                receipts_stopped = self.receipt_journal.stop(timeout=timeout)
                return journal_stopped and receipts_stopped
            self._accepting = False
            self._cancel_expiry_timer_locked()
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
        receipts_flushed = self.receipt_journal.flush(timeout=timeout)
        checkpointed = False
        try:
            if (
                flushed
                and receipts_flushed
                and instance is not None
                and boot_id is not None
            ):
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
        receipts_stopped = self.receipt_journal.stop(timeout=timeout)
        with self._lock:
            complete = (
                flushed
                and receipts_flushed
                and checkpointed
                and journal_stopped
                and receipts_stopped
            )
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

    def inner_state_snapshot(self) -> InnerStateSnapshot:
        with self._lock:
            if self._inner_state is None:
                raise RuntimeError("LifeService has not started")
            return self._inner_state

    def identity_summary(self) -> IdentitySummary:
        return self.snapshot().identity

    def lineage_summary(self) -> InstanceSummary:
        return self.snapshot().instance

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.journal.recent(limit=limit)

    def recent_turn_receipts(
        self,
        limit: int = 32,
    ) -> tuple[TurnExperienceReceipt, ...]:
        return self.receipt_journal.recent(limit=limit)

    def observe_voice_event(
        self,
        event: dict[str, Any],
        *,
        session_id: str,
        owner_generation: int,
    ) -> bool:
        with self._lock:
            if not self._accepting or self._observation_bridge is None:
                return False
            observation = self._observation_bridge.project_voice(
                event,
                session_id=session_id,
                owner_generation=owner_generation,
            )
        if observation is None:
            return False
        return self._accept_observation(observation)

    def observe_playback(
        self,
        event: PlaybackLifecycleEvent | dict[str, Any],
        playback_duration_seconds: float | None = None,
    ) -> bool:
        with self._lock:
            if not self._accepting or self._observation_bridge is None:
                return False
            observation = self._observation_bridge.project_playback(event)
        return self._accept_observation(
            observation,
            playback_duration_seconds=playback_duration_seconds,
        )

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
        receipt_status = self.receipt_journal.status()
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
                "receipt_journal_state": receipt_status["state"],
                "receipt_journal_accepted": receipt_status["accepted"],
                "receipt_journal_persisted": receipt_status["persisted"],
                "receipt_journal_pending": receipt_status["pending"],
                "receipt_pending_turns": (
                    self._receipt_projector.pending_count
                    if self._receipt_projector is not None
                    else 0
                ),
                "observation_bridge": (
                    self._observation_bridge.stats()
                    if self._observation_bridge is not None
                    else {}
                ),
                "boot_id": self._boot_id,
                "previous_run_reason": self._previous_run_reason,
                "previous_run_unclean": self._previous_run_unclean,
                "previous_event_cursor": self._previous_event_cursor,
                "read_only_recovery": self._read_only_recovery,
                "handler_installed": self._handler_installed,
                "accepting": self._accepting,
                "l7_attached": self._l7_supervisor is not None,
                "last_error": (
                    self._last_error
                    or journal_status["last_error"]
                    or receipt_status["last_error"]
                ),
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
            l0_changed = False
            if state_payload is not None:
                try:
                    l0_changed = self._state_machine.apply(
                        event.type,
                        state_payload,
                        event.timestamp,
                    )
                except (InvalidLifeTransition, TypeError, ValueError) as exc:
                    self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                    return

            observation: LifeObservation | None = None
            try:
                if event.source == "conversation" and self._observation_bridge is not None:
                    observation = self._observation_bridge.project_conversation(event)
                elif event.source == "runtime" and self._observation_bridge is not None:
                    observation = self._observation_bridge.project_runtime(event)
            except ProjectionRejected as exc:
                self._last_error = f"ProjectionRejected: {str(exc)[:160]}"

            l1_changed = False
            if observation is not None:
                try:
                    l1_changed = self._apply_l1_observation_locked(
                        observation,
                        event_payload=event.payload,
                    )
                except (TypeError, ValueError) as exc:
                    self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"

            if l1_changed and not l0_changed:
                l0_changed = self._state_machine.mark_semantic_change(
                    event_id=observation.observation_id,
                    now=event.timestamp,
                    explanation=f"life.{observation.kind.value}",
                )
            if not (l0_changed or l1_changed):
                return
            reading = self._l1_clock.read()
            self._inner_state = self._build_inner_state_locked(reading)
            self._expression = self._project_expression_locked(reading)
            self._schedule_l1_expiry_locked(reading)
            snapshot = self._state_machine.snapshot()
            expression = self._expression
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot, expression)
            except Exception as exc:
                logger.warning("LifeService listener failed: %s", exc)

    def _persisted_receipts_for_restart(
        self,
    ) -> tuple[TurnExperienceReceipt, ...]:
        persisted: list[TurnExperienceReceipt] = []
        known: set[tuple[str, str]] = set()
        offset = 0
        while True:
            page = self.receipt_journal.incomplete(limit=1024, offset=offset)
            if not page:
                break
            for receipt in page:
                key = (receipt.session_id, receipt.request_id)
                if key not in known:
                    persisted.append(receipt)
                    known.add(key)
            offset += len(page)
            if len(page) < 1024:
                break
        for receipt in self.receipt_journal.recent(limit=32):
            key = (receipt.session_id, receipt.request_id)
            if key not in known:
                persisted.append(receipt)
                known.add(key)
        persisted.sort(
            key=lambda item: (
                item.ended_at_utc,
                item.first_event_sequence,
                item.receipt_id,
            )
        )
        return tuple(persisted)

    def _accept_observation(
        self,
        observation: LifeObservation,
        *,
        playback_duration_seconds: float | None = None,
    ) -> bool:
        listeners: tuple[LifeListener, ...] = ()
        snapshot: LifeSnapshot | None = None
        expression: ExpressionIntent | None = None
        with self._lock:
            if not self._accepting or self._state_machine is None:
                return False
            changed = self._apply_l1_observation_locked(
                observation,
                playback_duration_seconds=playback_duration_seconds,
            )
            if changed:
                self._state_machine.mark_semantic_change(
                    event_id=observation.observation_id,
                    now=self._now(),
                    explanation=f"life.{observation.kind.value}",
                )
                reading = self._l1_clock.read()
                self._inner_state = self._build_inner_state_locked(reading)
                self._expression = self._project_expression_locked(reading)
                self._schedule_l1_expiry_locked(reading)
                snapshot = self._state_machine.snapshot()
                expression = self._expression
                listeners = tuple(self._listeners)
        if snapshot is not None and expression is not None:
            for listener in listeners:
                try:
                    listener(snapshot, expression)
                except Exception as exc:
                    logger.warning("LifeService listener failed: %s", exc)
        return True

    def _apply_l1_observation_locked(
        self,
        observation: LifeObservation,
        *,
        playback_duration_seconds: float | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> bool:
        if any(
            component is None
            for component in (
                self._homeostasis,
                self._attention,
                self._affect,
                self._receipt_projector,
            )
        ):
            raise RuntimeError("L1 reducers are unavailable")
        assert self._homeostasis is not None
        assert self._attention is not None
        assert self._affect is not None
        assert self._receipt_projector is not None

        reading = self._l1_clock.read()
        appraisal = self._appraisal_reducer.reduce(
            observation,
            reading,
            playback_duration_seconds=playback_duration_seconds,
        )
        attention_before = (
            self._inner_state.attention
            if self._inner_state is not None
            else self._attention.snapshot()
        )
        affects_before = (
            self._inner_state.affects
            if self._inner_state is not None
            else self._affect.snapshot(reading)
        )
        homeostasis_changed = self._homeostasis.apply(appraisal, reading)

        if appraisal.attention_claim is not None:
            self._attention.apply(
                appraisal.attention_claim,
                session_id=observation.session_id,
                foreground=(
                    True
                    if observation.kind
                    in {
                        ObservationKind.USER_INVOKED,
                        ObservationKind.VOICE_LISTENING_STARTED,
                        ObservationKind.REQUEST_STARTED,
                        ObservationKind.INTERACTION_INTERRUPTED,
                    }
                    else None
                ),
                now_utc=reading.utc_timestamp,
                playback_duration_seconds=(
                    playback_duration_seconds
                    if observation.kind is ObservationKind.SPEECH_STARTED
                    else None
                ),
            )
        self._close_attention_for_observation_locked(observation, reading.utc_timestamp)
        attention_after = self._attention.snapshot(reading.utc_timestamp)
        affects_after = self._affect.apply(appraisal, reading)

        if attention_after.mode is AttentionMode.IDLE:
            self._presence_session_id = None
        elif observation.session_id is not None:
            self._presence_session_id = observation.session_id
        self._last_reason_code = appraisal.reason_code

        payload = event_payload or {}
        execution_lane = payload.get("execution_lane")
        response_path = (
            ResponsePath.DETERMINISTIC_LOCAL
            if execution_lane == "deterministic_local"
            else ResponsePath.TOOL
            if observation.kind is ObservationKind.TOOL_STARTED
            else ResponsePath.MODEL
        )
        receipt = self._receipt_projector.apply(
            observation,
            response_path=response_path,
        )
        if receipt is not None:
            self.receipt_journal.enqueue(receipt)

        return (
            homeostasis_changed
            or attention_before != attention_after
            or affects_before != affects_after
        )

    def _close_attention_for_observation_locked(
        self,
        observation: LifeObservation,
        now_utc: str,
    ) -> None:
        assert self._attention is not None
        if observation.kind is ObservationKind.VOICE_LISTENING_STOPPED:
            self._attention.expire(
                now_utc,
                target_kind="voice_session",
                target_id=observation.session_id,
            )
        elif observation.kind is ObservationKind.APPROVAL_RESOLVED:
            self._attention.expire(
                now_utc,
                target_kind="approval",
                target_id=observation.request_id,
            )
        elif observation.kind in {
            ObservationKind.REQUEST_COMPLETED,
            ObservationKind.REQUEST_FAILED,
            ObservationKind.REQUEST_CANCELLED,
        }:
            for target_kind in ("request", "approval", "interruption"):
                self._attention.expire(
                    now_utc,
                    target_kind=target_kind,
                    target_id=observation.request_id,
                )
        elif observation.kind is ObservationKind.SPEECH_STOPPED:
            assert observation.request_id is not None
            assert observation.source_generation is not None
            self._attention.expire(
                now_utc,
                target_kind="playback",
                target_id=playback_target_id(
                    observation.request_id,
                    observation.source_generation,
                ),
            )
        elif observation.kind is ObservationKind.RUNTIME_RECOVERED:
            self._attention.expire(
                now_utc,
                target_kind="recovery",
                target_id=observation.source_boot_id,
            )

    def _build_inner_state_locked(self, reading) -> InnerStateSnapshot:
        if any(
            component is None
            for component in (self._state_machine, self._homeostasis, self._attention, self._affect)
        ):
            raise RuntimeError("LifeService reducers are unavailable")
        assert self._state_machine is not None
        assert self._homeostasis is not None
        assert self._attention is not None
        assert self._affect is not None
        life = self._state_machine.snapshot()
        attention = self._attention.snapshot(reading.utc_timestamp)
        homeostasis = self._homeostasis.snapshot(reading)
        affects = self._affect.snapshot(reading)
        active = attention.mode is not AttentionMode.IDLE
        presence = PresenceSnapshot(
            mode=attention.mode,
            intensity=(
                max(homeostasis.social_presence, homeostasis.activation)
                if active
                else homeostasis.social_presence
            ),
            session_id=(
                life.active_session_id or self._presence_session_id if active else None
            ),
            source_observation_id=(
                attention.source_observation_id if active else None
            ),
            reason_code=(
                self._last_reason_code if active else ReasonCode.QUIET_BASELINE
            ),
            since_utc=attention.since_utc,
            expires_at_utc=attention.expires_at_utc,
        )
        return InnerStateSnapshot(
            schema_version=1,
            source_life_snapshot_revision=life.revision,
            identity_id=life.identity.identity_id,
            instance_id=life.instance.instance_id,
            generated_at_utc=reading.utc_timestamp,
            phase=attention.mode.value,
            attention=attention,
            homeostasis=homeostasis,
            affects=affects,
            presence=presence,
            last_observation_id=self._homeostasis.last_observation_id,
            degraded=life.lifecycle_state
            in {LifeCycleState.DEGRADED, LifeCycleState.RECOVERING},
        )

    def _cancel_expiry_timer_locked(self) -> None:
        self._expiry_generation += 1
        timer = self._expiry_timer
        self._expiry_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_l1_expiry_locked(self, reading) -> None:
        self._cancel_expiry_timer_locked()
        if not self._accepting or self._inner_state is None:
            return
        expiries = [
            value
            for value in (
                self._inner_state.attention.expires_at_utc,
                *(affect.valid_until_utc for affect in self._inner_state.affects),
            )
            if value is not None
        ]
        if not expiries:
            return
        expires_at = min(coerce_utc(value) for value in expiries)
        delay = max(0.001, (expires_at - reading.utc).total_seconds())
        generation = self._expiry_generation
        timer = threading.Timer(delay, self._expire_l1_state, args=(generation,))
        timer.name = "javis-life-l1-expiry"
        timer.daemon = True
        self._expiry_timer = timer
        timer.start()

    def _expire_l1_state(self, generation: int) -> None:
        listeners: tuple[LifeListener, ...] = ()
        snapshot: LifeSnapshot | None = None
        expression: ExpressionIntent | None = None
        with self._lock:
            if (
                generation != self._expiry_generation
                or not self._accepting
                or self._state_machine is None
                or self._homeostasis is None
                or self._attention is None
                or self._affect is None
                or self._inner_state is None
            ):
                return
            self._expiry_timer = None
            reading = self._l1_clock.read()
            previous = self._inner_state
            homeostasis_changed = self._homeostasis.tick(reading)
            attention = self._attention.snapshot(reading.utc_timestamp)
            affects = self._affect.snapshot(reading)
            changed = (
                homeostasis_changed
                or attention != previous.attention
                or affects != previous.affects
            )
            if changed:
                self._expiry_sequence += 1
                self._state_machine.mark_semantic_change(
                    event_id=(
                        f"timer:{self._boot_id or 'boot'}:{self._expiry_sequence}"
                    ),
                    now=reading.utc.timestamp(),
                    explanation="life.expiry",
                )
                self._inner_state = self._build_inner_state_locked(reading)
                self._expression = self._project_expression_locked(reading)
                snapshot = self._state_machine.snapshot()
                expression = self._expression
                listeners = tuple(self._listeners)
            self._schedule_l1_expiry_locked(reading)
        if snapshot is not None and expression is not None:
            for listener in listeners:
                try:
                    listener(snapshot, expression)
                except Exception as exc:
                    logger.warning("LifeService listener failed: %s", exc)

    def _project_expression_locked(self, reading) -> ExpressionIntent:
        if self._state_machine is None or self._inner_state is None:
            raise RuntimeError("LifeService has not started")
        life_snapshot = self._state_machine.snapshot()
        mode = self._inner_state.attention.mode
        if life_snapshot.lifecycle_state is LifeCycleState.OFFLINE:
            base_state = ExpressionBaseState.OFFLINE
        elif life_snapshot.lifecycle_state in {
            LifeCycleState.DEGRADED,
            LifeCycleState.RECOVERING,
        }:
            base_state = ExpressionBaseState.ERROR
        elif life_snapshot.lifecycle_state is LifeCycleState.STOPPING:
            base_state = ExpressionBaseState.IDLE
        else:
            base_state = {
                AttentionMode.IDLE: ExpressionBaseState.IDLE,
                AttentionMode.PRESENT: ExpressionBaseState.ATTENTION,
                AttentionMode.LISTENING: ExpressionBaseState.LISTENING,
                AttentionMode.ENGAGED: (
                    ExpressionBaseState.ATTENTION
                    if life_snapshot.activity == "attention"
                    else ExpressionBaseState.EXECUTING
                    if life_snapshot.activity == "executing"
                    else ExpressionBaseState.THINKING
                ),
                AttentionMode.SPEAKING: ExpressionBaseState.SPEAKING,
                AttentionMode.AWAITING_APPROVAL: ExpressionBaseState.ATTENTION,
                AttentionMode.BLOCKED: ExpressionBaseState.BLOCKED,
                AttentionMode.RECOVERING: ExpressionBaseState.ERROR,
            }[mode]
        gaze_target = {
            ExpressionBaseState.IDLE: GazeTarget.NONE,
            ExpressionBaseState.ATTENTION: GazeTarget.USER,
            ExpressionBaseState.LISTENING: GazeTarget.USER,
            ExpressionBaseState.THINKING: GazeTarget.CONTENT,
            ExpressionBaseState.SPEAKING: GazeTarget.USER,
            ExpressionBaseState.EXECUTING: GazeTarget.TASK,
            ExpressionBaseState.BLOCKED: GazeTarget.TASK,
            ExpressionBaseState.ERROR: GazeTarget.NONE,
            ExpressionBaseState.OFFLINE: GazeTarget.NONE,
        }[base_state]
        voice_activity = {
            ExpressionBaseState.LISTENING: VoiceActivity.LISTENING,
            ExpressionBaseState.SPEAKING: VoiceActivity.SPEAKING,
        }.get(base_state, VoiceActivity.SILENT)
        return self._expression_projector.project(
            life_snapshot,
            reading.utc.timestamp(),
            intensity=max(
                self._inner_state.homeostasis.activation,
                self._inner_state.presence.intensity,
            ),
            base_state=base_state,
            gaze_target=gaze_target,
            voice_activity=voice_activity,
            interrupt=base_state
            in {
                ExpressionBaseState.LISTENING,
                ExpressionBaseState.BLOCKED,
                ExpressionBaseState.ERROR,
                ExpressionBaseState.OFFLINE,
            },
            explanation_code=f"life.{self._inner_state.presence.reason_code.value}",
        )

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
