"""Backend-owned conversation execution, replay, and interruption hub."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

from core.agent_run_recorder import AgentRunRecorder
from core.agent_runs import AgentRunStore
from core.cancellation import CancellationToken, RequestCancelled
from core.conversation_store import ConversationStore, ConversationStoreError


Runner = Callable[["ConversationRequest", CancellationToken], AsyncIterator[dict[str, Any]]]


@dataclass(frozen=True)
class ConversationRequest:
    session_id: str
    request_id: str
    text: str
    interaction_mode: str = ""
    idempotency_key: str = ""


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
    ):
        self.store = store
        self.run_store = run_store
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

    async def submit(
        self,
        request: ConversationRequest,
        runner: Runner,
    ) -> dict[str, Any]:
        normalized = self._validate_request(request)
        claimed_request, created = self.store.claim_idempotency_key(
            normalized.session_id,
            normalized.idempotency_key,
            normalized.request_id,
        )
        if not created:
            return {"accepted": False, "request_id": claimed_request, "duplicate": True}

        self.store.append_message(
            normalized.session_id,
            normalized.request_id,
            "user",
            normalized.text,
        )
        loop = asyncio.get_running_loop()
        active = _ActiveRequest(
            request=normalized,
            token=CancellationToken(),
            completion=loop.create_future(),
        )

        async with self._lock:
            if normalized.request_id in self._request_index:
                raise ConversationStoreError(
                    f"request_id is already active: {normalized.request_id}"
                )
            previous = self._active.get(normalized.session_id)
            if previous is not None:
                cancelled = self._request_cancel_locked(previous, "replacement request")
                if cancelled:
                    await self._publish(
                        previous.request.session_id,
                        previous.request.request_id,
                        "request.cancellation_pending",
                        {"reason": "replacement request"},
                    )
            await self._publish(
                normalized.session_id,
                normalized.request_id,
                "request.accepted",
                {
                    "text": normalized.text,
                    "interaction_mode": normalized.interaction_mode,
                    "replaces_request_id": previous.request.request_id if previous else "",
                },
            )
            active.task = asyncio.create_task(
                self._run_after_previous(active, previous.task if previous else None, runner)
            )
            self._active[normalized.session_id] = active
            self._request_index[normalized.request_id] = active
        return {"accepted": True, "request_id": normalized.request_id, "duplicate": False}

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
                    terminal = await self._publish(
                        request.session_id,
                        request.request_id,
                        "request.failed",
                        {"error": str(message.get("message") or "request failed")[:500]},
                    )
                    break
                elif message_type == "done":
                    terminal = await self._publish(
                        request.session_id,
                        request.request_id,
                        "request.completed",
                        {"detail": str(message.get("detail") or "completed")[:500]},
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
        for queue in tuple(self._subscribers.get(session_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(dict(event))
        return event

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
        for field, value in (
            ("session_id", session_id),
            ("request_id", request_id),
            ("idempotency_key", idempotency_key),
        ):
            if not value or len(value) > 256:
                raise ConversationStoreError(f"invalid {field}")
        if not text or len(text) > 100_000:
            raise ConversationStoreError("invalid conversation text")
        return ConversationRequest(
            session_id,
            request_id,
            text,
            interaction_mode,
            idempotency_key,
        )
