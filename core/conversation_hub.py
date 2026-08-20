"""Backend-owned conversation execution, replay, and interruption hub."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from core.agent_run_recorder import AgentRunRecorder
from core.agent_runs import AgentRunStore
from core.cancellation import CancellationToken, RequestCancelled
from core.conversation_store import ConversationStore, ConversationStoreError
from core.events import EventBus
from core.life.l1.contracts import InputProvenance
from core.life.l1.wake import DETERMINISTIC_LOCAL_LANE, EXCLUSIVE_LANE
from core.life.memory.access import AccessContextFactory, safe_access_projection
from core.life.memory.contracts import AccessContext


Runner = Callable[["ConversationRequest", CancellationToken], AsyncIterator[dict[str, Any]]]

_SYSTEM_EVENT_TYPES = frozenset(
    {"life.snapshot", "life.expression", "life.inner_state.changed"}
)
_RUNTIME_REQUEST_EVENTS = frozenset(
    {
        "request.accepted",
        "request.cancellation_pending",
        "request.completed",
        "request.cancelled",
        "request.failed",
    }
)
_RUNTIME_ACTIVITY_EVENTS = frozenset(
    {
        "activity.understanding",
        "activity.thinking",
        "activity.tool_started",
        "activity.tool_completed",
        "activity.blocked",
        "activity.error",
    }
)
_RUNTIME_OBSERVATION_EVENTS = (
    _RUNTIME_REQUEST_EVENTS
    | _RUNTIME_ACTIVITY_EVENTS
    | {
        "approval.required",
        "approval.resolved",
        "interaction.interrupted",
        "user.invoked",
    }
)


@dataclass(frozen=True)
class ConversationRequest:
    session_id: str
    request_id: str
    text: str
    interaction_mode: str = ""
    idempotency_key: str = ""
    input_provenance: InputProvenance = field(default_factory=InputProvenance.unknown)
    execution_lane: str = EXCLUSIVE_LANE
    access_context: AccessContext | None = None


@dataclass
class _ActiveRequest:
    request: ConversationRequest
    token: CancellationToken
    completion: asyncio.Future
    task: asyncio.Task | None = None
    recorder: AgentRunRecorder | None = None


class ConversationSubscription:
    def __init__(
        self,
        hub: "ConversationHub",
        session_id: str,
        queue: asyncio.Queue,
        replay: list[dict[str, Any]],
    ):
        self._hub = hub
        self.session_id = session_id
        self._queue = queue
        self.replay = replay
        self._closed = False

    async def get(self) -> dict[str, Any]:
        return await self._queue.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._hub._detach(self.session_id, self._queue)


class ConversationHub:
    """Coordinates one active request per conversation and broadcasts its events."""

    def __init__(
        self,
        store: ConversationStore,
        run_store: AgentRunStore,
        *,
        resolve_confirmation: Callable[[bool], None] | None = None,
        event_bus: EventBus | None = None,
        terminal_wakeup: Callable[[], Any] | None = None,
    ):
        self.store = store
        self.run_store = run_store
        self.event_bus = event_bus
        self._terminal_wakeup = terminal_wakeup
        self._resolve_confirmation = resolve_confirmation
        self._lock = asyncio.Lock()
        self._execution_lock = asyncio.Lock()
        self._active: dict[str, _ActiveRequest] = {}
        self._request_index: dict[str, _ActiveRequest] = {}
        self._terminal: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._pending_approvals: dict[str, tuple[str, str, AgentRunRecorder]] = {}

    async def attach(
        self,
        session_id: str,
        *,
        after_sequence: int = 0,
    ) -> ConversationSubscription:
        session = self.store.ensure_session(session_id)["session_id"]
        replay = self.store.events_after(session, sequence=after_sequence)
        queue: asyncio.Queue = asyncio.Queue(maxsize=512)
        async with self._lock:
            self._subscribers.setdefault(session, set()).add(queue)
        return ConversationSubscription(self, session, queue, replay)

    def subscribed_sessions(self) -> tuple[str, ...]:
        """Return session identifiers without exposing subscriber queues."""

        return tuple(sorted(self._subscribers))

    async def publish_system_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if event_type not in _SYSTEM_EVENT_TYPES:
            raise ConversationStoreError(
                f"system conversation event is not registered: {event_type}"
            )
        if not isinstance(payload, dict):
            raise ConversationStoreError("system event payload must be an object")
        return await self._publish(session_id, "", event_type, payload)

    async def submit(
        self,
        request: ConversationRequest,
        runner: Runner,
    ) -> dict[str, Any]:
        normalized = self._validate_request(request)
        loop = asyncio.get_running_loop()
        active = _ActiveRequest(
            request=normalized,
            token=CancellationToken(),
            completion=loop.create_future(),
        )

        async with self._lock:
            previous = self._active.get(normalized.session_id)
            should_cancel_previous = bool(
                previous is not None and not previous.token.cancelled
            )
            preceding_events = (
                ((
                    previous.request.request_id,
                    "request.cancellation_pending",
                    {"reason": "replacement request"},
                ),)
                if should_cancel_previous and previous is not None
                else ()
            )
            acceptance = self.store.accept_request(
                normalized.session_id,
                normalized.request_id,
                normalized.idempotency_key,
                normalized.text,
                {
                    "text": normalized.text,
                    "interaction_mode": normalized.interaction_mode,
                    "replaces_request_id": previous.request.request_id if previous else "",
                    "input_provenance": normalized.input_provenance.to_dict(),
                    "execution_lane": normalized.execution_lane,
                    "access_projection": safe_access_projection(
                        normalized.access_context
                    ),
                },
                preceding_events=preceding_events,
            )
            if not acceptance["accepted"]:
                return {
                    "accepted": False,
                    "request_id": acceptance["request_id"],
                    "duplicate": True,
                    "event": acceptance["event"],
                }
            if should_cancel_previous and previous is not None:
                self._request_cancel_locked(previous, "replacement request")
            invoked_event = None
            if normalized.execution_lane == DETERMINISTIC_LOCAL_LANE:
                invoked_event = self.store.append_event(
                    normalized.session_id,
                    normalized.request_id,
                    "user.invoked",
                    {"execution_lane": normalized.execution_lane},
                )
            self._active[normalized.session_id] = active
            self._request_index[normalized.request_id] = active
            for event in acceptance["preceding_events"]:
                self._broadcast_persisted(event)
            self._broadcast_persisted(acceptance["event"])
            if invoked_event is not None:
                self._broadcast_persisted(invoked_event)
            active.task = asyncio.create_task(
                self._run_after_previous(active, previous.task if previous else None, runner)
            )
        return {
            "accepted": True,
            "request_id": normalized.request_id,
            "duplicate": False,
            "event": acceptance["event"],
        }

    async def cancel(
        self,
        session_id: str,
        request_id: str = "",
        *,
        reason: str = "user interrupt",
    ) -> bool:
        async with self._lock:
            active = self._active.get(str(session_id or "").strip())
            if active is None:
                return False
            if request_id and active.request.request_id != request_id:
                return False
            cancelled = self._request_cancel_locked(active, reason)
            if not cancelled:
                return False
            await self._publish(
                active.request.session_id,
                active.request.request_id,
                "interaction.interrupted",
                {
                    "reason": str(reason or "cancelled")[:160],
                    "execution_lane": active.request.execution_lane,
                },
            )
            await self._publish(
                active.request.session_id,
                active.request.request_id,
                "request.cancellation_pending",
                {"reason": str(reason or "cancelled")},
            )
            return True

    async def confirm(
        self,
        session_id: str,
        request_id: str,
        approval_id: str,
        confirmed: bool,
    ) -> bool:
        async with self._lock:
            pending = self._pending_approvals.get(str(session_id or "").strip())
            expected = (str(request_id or "").strip(), str(approval_id or "").strip())
            if pending is None or pending[:2] != expected:
                return False
            _, _, recorder = pending
            recorder.resolve_confirmation(
                bool(confirmed),
                response={"source": "conversation_hub"},
            )
            self._pending_approvals.pop(str(session_id or "").strip(), None)
            if self._resolve_confirmation is not None:
                self._resolve_confirmation(bool(confirmed))
            await self._publish(
                str(session_id).strip(),
                str(request_id).strip(),
                "approval.resolved",
                {"approval_id": str(approval_id).strip(), "confirmed": bool(confirmed)},
            )
            return True

    async def wait_for_terminal(
        self,
        request_id: str,
        *,
        timeout: float = 5,
    ) -> dict[str, Any]:
        normalized = str(request_id or "").strip()
        terminal = self._terminal.get(normalized)
        if terminal is not None:
            return terminal
        active = self._request_index.get(normalized)
        if active is None:
            raise KeyError(normalized)
        return await asyncio.wait_for(asyncio.shield(active.completion), timeout=timeout)

    def stats(self) -> dict[str, int]:
        return {
            "active_requests": len(self._active),
            "known_requests": len(self._request_index),
            "subscribers": sum(len(queues) for queues in self._subscribers.values()),
            "pending_approvals": len(self._pending_approvals),
        }

    async def shutdown(self) -> None:
        async with self._lock:
            active_requests = list(self._active.values())
            for active in active_requests:
                if self._request_cancel_locked(active, "hub shutdown"):
                    await self._publish(
                        active.request.session_id,
                        active.request.request_id,
                        "request.cancellation_pending",
                        {"reason": "hub shutdown"},
                    )
            tasks = [active.task for active in active_requests if active.task is not None]
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=2)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                if not task.cancelled():
                    task.exception()

    async def _run_after_previous(
        self,
        active: _ActiveRequest,
        previous_task: asyncio.Task | None,
        runner: Runner,
    ) -> None:
        if active.request.execution_lane == DETERMINISTIC_LOCAL_LANE:
            await self._run_request(active, runner)
            return
        if previous_task is not None:
            await asyncio.gather(previous_task, return_exceptions=True)
        async with self._execution_lock:
            await self._run_request(active, runner)

    async def _run_request(self, active: _ActiveRequest, runner: Runner) -> None:
        request = active.request
        response_parts: list[str] = []
        terminal: dict[str, Any] | None = None
        recorder = AgentRunRecorder(
            self.run_store,
            request.text,
            session_id=request.session_id,
            interaction_mode=request.interaction_mode,
            metadata={"request_id": request.request_id},
        )
        active.recorder = recorder
        try:
            await active.token.checkpoint()
            async for message in runner(request, active.token):
                message_type = str(message.get("type", ""))
                if message_type != "tool_result":
                    await active.token.checkpoint()
                recorder.record(message)
                if message_type == "thinking":
                    await self._publish(
                        request.session_id,
                        request.request_id,
                        "activity.understanding",
                        {"detail": str(message.get("content") or "Understanding")[:500]},
                    )
                elif message_type == "activity":
                    activity = str(message.get("activity") or "understanding").strip().lower()
                    if not activity.replace("_", "").replace("-", "").isalnum():
                        activity = "understanding"
                    await self._publish(
                        request.session_id,
                        request.request_id,
                        f"activity.{activity}",
                        {"detail": str(message.get("detail") or activity)[:500]},
                    )
                elif message_type == "tool_start":
                    await self._publish(
                        request.session_id,
                        request.request_id,
                        "activity.tool_started",
                        {
                            "tool": str(message.get("tool") or "unknown"),
                            "params": message.get("params") if isinstance(message.get("params"), dict) else {},
                        },
                    )
                elif message_type == "tool_result":
                    await self._publish(
                        request.session_id,
                        request.request_id,
                        "activity.tool_completed",
                        {
                            "tool": str(message.get("tool") or "unknown"),
                            "success": bool(message.get("success", False)),
                            "data": str(message.get("data") or "")[:2000],
                        },
                    )
                    await active.token.checkpoint()
                elif message_type == "confirm_required":
                    approval_id = recorder.pending_approval_id
                    if approval_id is None:
                        raise RuntimeError("approval was not recorded")
                    async with self._lock:
                        self._pending_approvals[request.session_id] = (
                            request.request_id,
                            approval_id,
                            recorder,
                        )
                    await self._publish(
                        request.session_id,
                        request.request_id,
                        "approval.required",
                        {
                            "approval_id": approval_id,
                            "tool": str(message.get("tool") or "unknown"),
                            "reason": str(message.get("reason") or "")[:500],
                            "params": message.get("params") if isinstance(message.get("params"), dict) else {},
                        },
                    )
                elif message_type == "text_delta":
                    text = str(message.get("text") or "")
                    if text:
                        response_parts.append(text)
                        await self._publish(
                            request.session_id,
                            request.request_id,
                            "response.delta",
                            {"text": text},
                        )
                elif message_type == "error":
                    failure_payload = {
                        "error": str(message.get("message") or "request failed")[:500]
                    }
                    for field in ("code", "recovery_action", "route", "reason"):
                        value = message.get(field)
                        if isinstance(value, str) and value.strip():
                            failure_payload[field] = value.strip()[:160]
                    terminal = await self._publish(
                        request.session_id,
                        request.request_id,
                        "request.failed",
                        failure_payload,
                    )
                    break
                elif message_type == "done":
                    success = message.get("success") is not False
                    detail = str(
                        message.get("detail")
                        or ("completed" if success else "request failed")
                    )[:500]
                    terminal = await self._publish(
                        request.session_id,
                        request.request_id,
                        "request.completed" if success else "request.failed",
                        {"detail": detail} if success else {"error": detail},
                    )
                    break

            if terminal is None:
                recorder.finalize("completed")
                terminal = await self._publish(
                    request.session_id,
                    request.request_id,
                    "request.completed",
                    {"detail": "completed"},
                )
            if terminal["type"] == "request.completed" and response_parts:
                self.store.append_message(
                    request.session_id,
                    request.request_id,
                    "assistant",
                    "".join(response_parts),
                )
            elif terminal["type"] == "request.failed" and response_parts:
                self.store.append_message(
                    request.session_id,
                    request.request_id,
                    "assistant",
                    "".join(response_parts),
                    status="interrupted",
                )
        except RequestCancelled as exc:
            recorder.cancel(exc.reason)
            if response_parts:
                self.store.append_message(
                    request.session_id,
                    request.request_id,
                    "assistant",
                    "".join(response_parts),
                    status="interrupted",
                )
            terminal = await self._publish(
                request.session_id,
                request.request_id,
                "request.cancelled",
                {"reason": exc.reason},
            )
        except asyncio.CancelledError:
            recorder.cancel(active.token.reason or "task cancelled")
            terminal = await self._publish(
                request.session_id,
                request.request_id,
                "request.cancelled",
                {"reason": active.token.reason or "task cancelled"},
            )
        except Exception as exc:
            recorder.finalize("failed", error=str(exc))
            terminal = await self._publish(
                request.session_id,
                request.request_id,
                "request.failed",
                {"error": str(exc)[:500]},
            )
        finally:
            async with self._lock:
                pending = self._pending_approvals.get(request.session_id)
                if pending and pending[0] == request.request_id:
                    self._pending_approvals.pop(request.session_id, None)
                current = self._active.get(request.session_id)
                if current is active:
                    self._active.pop(request.session_id, None)
            if terminal is None:
                terminal = await self._publish(
                    request.session_id,
                    request.request_id,
                    "request.failed",
                    {"error": "request ended without terminal state"},
                )
            self._terminal[request.request_id] = terminal
            if not active.completion.done():
                active.completion.set_result(terminal)

    def _request_cancel_locked(self, active: _ActiveRequest, reason: str) -> bool:
        if active.token.cancelled:
            return False
        active.token.cancel(reason)
        pending = self._pending_approvals.get(active.request.session_id)
        if pending and pending[0] == active.request.request_id:
            _, _, recorder = pending
            recorder.resolve_confirmation(False, response={"reason": str(reason)})
            self._pending_approvals.pop(active.request.session_id, None)
            if self._resolve_confirmation is not None:
                self._resolve_confirmation(False)
        return True

    async def _publish(
        self,
        session_id: str,
        request_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = self.store.append_event(session_id, request_id, event_type, payload)
        self._broadcast_persisted(event)
        return event

    def _broadcast_persisted(self, event: dict[str, Any]) -> None:
        self._publish_runtime_observation(event)
        if event.get("type") in {
            "request.completed",
            "request.failed",
            "request.cancelled",
        } and self._terminal_wakeup is not None:
            try:
                self._terminal_wakeup()
            except Exception:
                # Durable cursor reconciliation recovers a dropped low-latency wakeup.
                pass
        session_id = str(event.get("session_id") or "")
        for queue in tuple(self._subscribers.get(session_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(dict(event))

    def _publish_runtime_observation(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if self.event_bus is None or event_type not in _RUNTIME_OBSERVATION_EVENTS:
            return
        session_id = str(event.get("session_id") or "")
        request_id = str(event.get("request_id") or "")
        if not session_id or not request_id:
            return
        canonical_payload = event.get("payload")
        if not isinstance(canonical_payload, dict):
            canonical_payload = {}
        active = self._request_index.get(request_id)
        interaction_mode = (
            active.request.interaction_mode
            if active is not None
            else str(canonical_payload.get("interaction_mode") or "")[:32]
        )
        execution_lane = (
            active.request.execution_lane
            if active is not None
            else str(canonical_payload.get("execution_lane") or EXCLUSIVE_LANE)[:32]
        )
        observation: dict[str, Any] = {
            "source_event_id": str(event.get("event_id") or ""),
            "source_sequence": int(event.get("sequence") or 0),
            "source_sequence_domain": f"conversation_store:{session_id}",
            "session_id": session_id,
            "request_id": request_id,
            "correlation_id": request_id,
            "interaction_mode": interaction_mode,
            "execution_lane": execution_lane,
        }
        if event_type in {
            "activity.tool_started",
            "activity.tool_completed",
            "approval.required",
        }:
            tool = str(canonical_payload.get("tool") or "").strip()[:128]
            if tool:
                observation["tool"] = tool
        if event_type == "activity.tool_completed":
            observation["success"] = bool(canonical_payload.get("success", False))
        if event_type == "approval.required":
            approval_id = str(canonical_payload.get("approval_id") or "").strip()[:256]
            if approval_id:
                observation["approval_id"] = approval_id
        if event_type == "approval.resolved":
            confirmed = canonical_payload.get("confirmed")
            if type(confirmed) is bool:
                observation["confirmed"] = confirmed
        diagnostic_code = str(canonical_payload.get("code") or "").strip()[:80]
        if diagnostic_code and all(
            char.isalnum() or char in "._-" for char in diagnostic_code
        ):
            observation["code"] = diagnostic_code
        self.event_bus.publish(
            event_type,
            observation,
            source="conversation",
            correlation_id=request_id,
            causation_id=observation["source_event_id"],
        )

    async def _detach(self, session_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if subscribers is None:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(session_id, None)

    @staticmethod
    def _validate_request(request: ConversationRequest) -> ConversationRequest:
        session_id = str(request.session_id or "").strip()
        request_id = str(request.request_id or "").strip()
        text = str(request.text or "").strip()
        interaction_mode = str(request.interaction_mode or "").strip()[:32]
        idempotency_key = str(request.idempotency_key or request_id).strip()
        input_provenance = request.input_provenance
        execution_lane = str(request.execution_lane or "").strip()
        access_context = request.access_context
        for field, value in (
            ("session_id", session_id),
            ("request_id", request_id),
            ("idempotency_key", idempotency_key),
        ):
            if not value or len(value) > 256:
                raise ConversationStoreError(f"invalid {field}")
        if not text or len(text) > 100_000:
            raise ConversationStoreError("invalid conversation text")
        if not isinstance(input_provenance, InputProvenance):
            raise ConversationStoreError("invalid input provenance")
        if execution_lane not in {EXCLUSIVE_LANE, DETERMINISTIC_LOCAL_LANE}:
            raise ConversationStoreError("invalid execution lane")
        if access_context is None:
            access_context = AccessContextFactory(
                None,
                runtime_boot_id="runtime-compatibility",
            ).for_session(session_id, principal=None)
        if not isinstance(access_context, AccessContext):
            raise ConversationStoreError("invalid access context")
        if access_context.session_id != session_id:
            raise ConversationStoreError("access context session mismatch")
        return ConversationRequest(
            session_id,
            request_id,
            text,
            interaction_mode,
            idempotency_key,
            input_provenance,
            execution_lane,
            access_context,
        )
