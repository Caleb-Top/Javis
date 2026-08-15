"""WebSocket transport for the native continuous microphone stream."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
import hashlib
import hmac
import inspect
import math
import secrets
import threading
import time
from typing import Any

from fastapi import WebSocketDisconnect

from core.runtime_access import (
    websocket_access_context,
    websocket_access_remaining,
)


MAX_DIAGNOSTIC_COUNTER = (1 << 63) - 1
GATEWAY_ERROR_CATEGORIES = (
    "socket_disconnect",
    "invalid_request",
    "owner_conflict",
    "capture_failure",
    "receive_failure",
    "send_failure",
    "event_pump_failure",
    "internal_error",
)


def _bounded_increment(value: int, amount: int = 1) -> int:
    return min(MAX_DIAGNOSTIC_COUNTER, max(0, int(value)) + max(0, int(amount)))


class VoiceGatewayDiagnostics:
    """Process-local, privacy-safe counters for the voice WebSocket gateway."""

    def __init__(
        self,
        *,
        recovery_sample_limit: int = 128,
        pending_recovery_limit: int = 128,
        clock=time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._clock = clock
        self._secret = secrets.token_bytes(32)
        self._recovery_samples = deque(maxlen=max(1, min(int(recovery_sample_limit), 512)))
        self._connection_state_limit = max(1, min(int(pending_recovery_limit), 512))
        self._connection_states: OrderedDict[str, dict] = OrderedDict()
        self._active_handlers = 0
        self._active_child_tasks = 0
        self._connections_total = 0
        self._reconnect_attempts_total = 0
        self._recoveries_total = 0
        self._recoverable_errors = 0
        self._nonrecoverable_errors = 0
        self._errors_by_category = {
            category: 0 for category in GATEWAY_ERROR_CATEGORIES
        }

    def _fingerprint(self, session_id: str) -> str:
        return hmac.new(
            self._secret,
            str(session_id or "").encode("utf-8", errors="replace"),
            hashlib.sha256,
        ).hexdigest()[:16]

    def handler_started(self) -> None:
        with self._lock:
            self._active_handlers = _bounded_increment(self._active_handlers)
            self._connections_total = _bounded_increment(self._connections_total)

    def handler_finished(self) -> None:
        with self._lock:
            self._active_handlers = max(0, self._active_handlers - 1)

    def create_task(self, awaitable):
        with self._lock:
            self._active_child_tasks = _bounded_increment(self._active_child_tasks)
        try:
            task = asyncio.create_task(awaitable)
        except BaseException:
            with self._lock:
                self._active_child_tasks = max(0, self._active_child_tasks - 1)
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise

        def finished(_task) -> None:
            with self._lock:
                self._active_child_tasks = max(0, self._active_child_tasks - 1)

        task.add_done_callback(finished)
        return task

    @staticmethod
    def _generation(owner_generation: int | None) -> int:
        return max(0, int(owner_generation or 0))

    def _bound_connection_states(self) -> None:
        while len(self._connection_states) > self._connection_state_limit:
            self._connection_states.popitem(last=False)

    def note_connection(
        self,
        session_id: str,
        owner_generation: int | None = None,
    ) -> None:
        fingerprint = self._fingerprint(session_id)
        generation = self._generation(owner_generation)
        now = self._clock()
        with self._lock:
            previous = self._connection_states.get(fingerprint)
            if previous is not None:
                previous_generation = int(previous.get("generation") or 0)
                if generation and previous_generation and generation < previous_generation:
                    return
                reconnect = bool(
                    previous.get("connected")
                    or previous.get("recovery_started") is not None
                )
            else:
                reconnect = False
            recovery_started = (
                previous.get("recovery_started") if previous is not None else None
            )
            if reconnect:
                self._reconnect_attempts_total = _bounded_increment(
                    self._reconnect_attempts_total
                )
                if recovery_started is None:
                    recovery_started = now
            self._connection_states[fingerprint] = {
                "generation": generation,
                "connected": True,
                "ready": False,
                "recovery_started": recovery_started,
            }
            self._connection_states.move_to_end(fingerprint)
            self._bound_connection_states()

    def note_ready(
        self,
        session_id: str,
        owner_generation: int | None = None,
    ) -> None:
        fingerprint = self._fingerprint(session_id)
        generation = self._generation(owner_generation)
        now = self._clock()
        with self._lock:
            state = self._connection_states.get(fingerprint)
            if state is None:
                return
            current_generation = int(state.get("generation") or 0)
            if generation and current_generation and generation != current_generation:
                return
            if state.get("ready"):
                return
            state["ready"] = True
            started = state.get("recovery_started")
            state["recovery_started"] = None
            self._connection_states.move_to_end(fingerprint)
            if started is None:
                return
            elapsed_ms = max(0, round((now - started) * 1000))
            self._recoveries_total = _bounded_increment(self._recoveries_total)
            self._recovery_samples.append(elapsed_ms)

    def note_disconnect(
        self,
        session_id: str,
        owner_generation: int | None = None,
    ) -> None:
        fingerprint = self._fingerprint(session_id)
        generation = self._generation(owner_generation)
        now = self._clock()
        with self._lock:
            state = self._connection_states.get(fingerprint)
            if state is not None:
                current_generation = int(state.get("generation") or 0)
                if generation and current_generation and generation != current_generation:
                    return
            else:
                state = {
                    "generation": generation,
                    "connected": False,
                    "ready": False,
                    "recovery_started": None,
                }
                self._connection_states[fingerprint] = state
            state["connected"] = False
            state["ready"] = False
            if state.get("recovery_started") is None:
                state["recovery_started"] = now
            self._connection_states.move_to_end(fingerprint)
            self._bound_connection_states()

    def note_closed(
        self,
        session_id: str,
        owner_generation: int | None = None,
    ) -> None:
        """Forget an intentional stop so a later start is not a reconnect."""
        fingerprint = self._fingerprint(session_id)
        generation = self._generation(owner_generation)
        with self._lock:
            state = self._connection_states.get(fingerprint)
            if state is None:
                return
            current_generation = int(state.get("generation") or 0)
            if generation and current_generation and generation != current_generation:
                return
            self._connection_states.pop(fingerprint, None)

    def record_error(self, category: str, *, recoverable: bool) -> None:
        normalized = (
            category if category in self._errors_by_category else "internal_error"
        )
        with self._lock:
            self._errors_by_category[normalized] = _bounded_increment(
                self._errors_by_category[normalized]
            )
            if recoverable:
                self._recoverable_errors = _bounded_increment(
                    self._recoverable_errors
                )
            else:
                self._nonrecoverable_errors = _bounded_increment(
                    self._nonrecoverable_errors
                )

    @staticmethod
    def _percentile(samples: list[int], percentile: float) -> int | None:
        if not samples:
            return None
        rank = max(1, math.ceil(len(samples) * percentile))
        return samples[rank - 1]

    def snapshot(self) -> dict:
        with self._lock:
            samples = sorted(self._recovery_samples)
            active_handlers = self._active_handlers
            active_child_tasks = self._active_child_tasks
            errors = dict(self._errors_by_category)
            return {
                "active_handlers": active_handlers,
                "active_child_tasks": active_child_tasks,
                "active_tasks": active_handlers + active_child_tasks,
                "connections_total": self._connections_total,
                "reconnect_attempts_total": self._reconnect_attempts_total,
                "recoveries_total": self._recoveries_total,
                "recovery_ms": {
                    "last": self._recovery_samples[-1] if self._recovery_samples else None,
                    "p50": self._percentile(samples, 0.50),
                    "p95": self._percentile(samples, 0.95),
                    "samples": len(samples),
                    "sample_limit": self._recovery_samples.maxlen,
                },
                "errors": {
                    "recoverable_total": self._recoverable_errors,
                    "nonrecoverable_total": self._nonrecoverable_errors,
                    "by_category": errors,
                },
            }


_gateway_diagnostics = VoiceGatewayDiagnostics()


def get_gateway_diagnostics() -> dict:
    """Return a stable, read-only snapshot for /api/voice/diagnostics."""
    return _gateway_diagnostics.snapshot()


def _payload(message: Any) -> dict:
    if not isinstance(message, dict):
        raise ValueError("audio stream command must be an object")
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("audio stream payload must be an object")
    return payload


def _session_id(payload: dict) -> str:
    value = str(payload.get("session_id") or "").strip()
    if not value or len(value) > 256 or any(char in value for char in "\r\n\x00"):
        raise ValueError("invalid audio stream session_id")
    return value


def _is_socket_closing(error: BaseException) -> bool:
    if isinstance(error, WebSocketDisconnect):
        return True
    return isinstance(error, RuntimeError) and str(error) in {
        'Cannot call "receive" once a disconnect message has been received.',
        'Cannot call "send" once a close message has been sent.',
    }


def _is_ownership_revoked(error: BaseException) -> bool:
    return isinstance(error, RuntimeError) and str(error) in {
        "microphone stream belongs to another conversation",
        "microphone stream belongs to another owner generation",
    }


def _classify_acquisition_error(error: Exception) -> tuple[str, bool]:
    if isinstance(error, ValueError):
        return "invalid_request", False
    if (
        _is_ownership_revoked(error)
    ):
        return "owner_conflict", True
    if isinstance(error, RuntimeError):
        return "capture_failure", True
    return "internal_error", False


async def serve_continuous_voice_stream(
    ws,
    manager,
    *,
    diagnostics: VoiceGatewayDiagnostics | None = None,
    authorize=None,
    voice_turn_registry=None,
) -> None:
    metrics = diagnostics or _gateway_diagnostics
    metrics.handler_started()
    try:
        if authorize is not None:
            allowed = authorize(ws, "voice.capture")
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                return
            await _serve_continuous_voice_stream(
                ws,
                manager,
                metrics,
                accept_subprotocol="javis-runtime-v1",
                voice_turn_registry=voice_turn_registry,
            )
        else:
            await _serve_continuous_voice_stream(
                ws,
                manager,
                metrics,
                voice_turn_registry=voice_turn_registry,
            )
    finally:
        metrics.handler_finished()


async def _serve_continuous_voice_stream(
    ws,
    manager,
    metrics,
    *,
    accept_subprotocol=None,
    voice_turn_registry=None,
) -> None:
    if accept_subprotocol:
        await ws.accept(subprotocol=accept_subprotocol)
    else:
        await ws.accept()
    closed = asyncio.Event()
    stream_acquired = False
    lease_was_acquired = False
    explicit_stop = False
    failure_recorded = False
    session_id = ""
    expected_owner_generation = 0
    release_task = None

    def record_failure(category: str, *, recoverable: bool) -> None:
        nonlocal failure_recorded
        if failure_recorded:
            return
        metrics.record_error(category, recoverable=recoverable)
        failure_recorded = True

    async def send_json(message: dict) -> None:
        try:
            await ws.send_json(message)
        except Exception as error:
            if _is_socket_closing(error):
                closed.set()
            raise

    async def send_error(error: Exception) -> None:
        if closed.is_set():
            return
        try:
            await send_json({"type": "audio.error", "message": str(error)[:500]})
        except Exception as send_failure:
            if not _is_socket_closing(send_failure):
                record_failure("send_failure", recoverable=True)
                raise

    async def drain_task(task) -> bool:
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                return cancelled
            except asyncio.CancelledError:
                cancelled = True
                if task.done():
                    task.result()
                    return cancelled

    async def stop_owned_stream(session_id: str) -> None:
        try:
            await asyncio.to_thread(
                manager.stop,
                session_id=session_id,
                owner_generation=expected_owner_generation,
            )
        except RuntimeError as error:
            if not _is_ownership_revoked(error):
                raise

    def ensure_release(session_id: str):
        nonlocal release_task, stream_acquired
        if release_task is None:
            if not stream_acquired:
                return None
            stream_acquired = False
            release_task = metrics.create_task(stop_owned_stream(session_id))
        return release_task

    async def close_expired_access() -> None:
        closed.set()
        try:
            await ws.close(code=4401, reason="capability_expired")
        except Exception as error:
            if not _is_socket_closing(error):
                raise

    async def release_stream(session_id: str) -> None:
        task = ensure_release(session_id)
        if task is None:
            return
        cancelled = await drain_task(task)
        if cancelled:
            raise asyncio.CancelledError

    remaining = websocket_access_remaining(ws)
    if remaining is not None and remaining <= 0:
        await close_expired_access()
        return

    try:
        first = await ws.receive_json()
    except Exception as error:
        if _is_socket_closing(error):
            record_failure("socket_disconnect", recoverable=True)
        else:
            record_failure("receive_failure", recoverable=True)
            await send_error(error)
        return

    try:
        payload = _payload(first)
        session_id = _session_id(payload)
        command = str(first.get("type") or "")
        if command not in {"audio.stream.start", "audio.stream.attach"}:
            raise ValueError("first audio stream command must start or attach")
        profile = str(payload.get("noise_profile") or "standard")
        if profile not in {"off", "standard", "strong"}:
            raise ValueError("invalid noise profile")
        cursor = max(0, int(payload.get("after_sequence") or 0))
        if command == "audio.stream.start":
            acquisition = metrics.create_task(
                asyncio.to_thread(
                    manager.start,
                    session_id=session_id,
                    noise_profile=profile,
                    device_index=payload.get("device_index"),
                )
            )
        else:
            acquisition = metrics.create_task(
                asyncio.to_thread(manager.attach, session_id=session_id)
            )
        cancelled = await drain_task(acquisition)
        acquisition_status = acquisition.result()
        if isinstance(acquisition_status, dict):
            expected_owner_generation = max(
                0,
                int(acquisition_status.get("owner_generation") or 0),
            )
        stream_acquired = True
        lease_was_acquired = True
        remaining = websocket_access_remaining(ws)
        if remaining is not None and remaining <= 0:
            await release_stream(session_id)
            await close_expired_access()
            return
        access_context = websocket_access_context(ws)
        socket_scope = getattr(ws, "scope", None)
        if access_context is not None and isinstance(socket_scope, dict):
            socket_scope["javis.capture_lease"] = {
                "session_id": session_id,
                "owner_generation": expected_owner_generation,
                "client_id_hash": access_context.get("client_id_hash", ""),
                "nonce_digest": access_context.get("nonce_digest", ""),
                "deadline_monotonic": access_context.get("deadline_monotonic", 0.0),
            }
        if cancelled:
            await release_stream(session_id)
            raise asyncio.CancelledError
        metrics.note_connection(session_id, expected_owner_generation)
    except Exception as error:
        category, recoverable = _classify_acquisition_error(error)
        record_failure(category, recoverable=recoverable)
        await send_error(error)
        return

    async def receive_controls() -> None:
        nonlocal explicit_stop
        while True:
            try:
                message = await ws.receive_json()
            except Exception as error:
                if _is_socket_closing(error):
                    record_failure("socket_disconnect", recoverable=True)
                else:
                    record_failure("receive_failure", recoverable=True)
                    await send_error(error)
                closed.set()
                return
            try:
                payload = _payload(message)
                control_session_id = _session_id(payload)
                if control_session_id != session_id:
                    raise ValueError("audio stream command session does not own this lease")
                command = str(message.get("type") or "")
                if command == "audio.stream.stop":
                    explicit_stop = True
                    await release_stream(session_id)
                    closed.set()
                    return
                if command != "audio.stream.attach":
                    metrics.record_error("invalid_request", recoverable=False)
                    await send_json(
                        {"type": "audio.error", "message": "unsupported audio stream command"}
                    )
            except Exception as error:
                if isinstance(error, ValueError):
                    record_failure("invalid_request", recoverable=False)
                elif not failure_recorded:
                    record_failure("internal_error", recoverable=False)
                await send_error(error)
                closed.set()
                return

    async def send_events() -> None:
        nonlocal cursor
        while True:
            try:
                events = await asyncio.to_thread(
                    manager.events_after,
                    cursor,
                    session_id=session_id,
                    owner_generation=expected_owner_generation,
                    timeout=0.1,
                )
            except Exception as error:
                if _is_ownership_revoked(error):
                    return
                record_failure("event_pump_failure", recoverable=True)
                raise
            for event in events:
                outbound = event
                if event.get("type") == "transcript.final" and voice_turn_registry is not None:
                    outbound = dict(event)
                    try:
                        outbound["voice_provenance"] = voice_turn_registry.register(
                            session_id=session_id,
                            owner_generation=expected_owner_generation,
                            voice_sequence=int(event.get("sequence") or 0),
                            voice_turn=int(event.get("turn") or 0),
                            transcript=str(event.get("text") or ""),
                        )
                    except ValueError:
                        outbound["voice_provenance_status"] = "unavailable"
                try:
                    await send_json(outbound)
                except Exception as error:
                    if _is_socket_closing(error):
                        record_failure("socket_disconnect", recoverable=True)
                    else:
                        record_failure("send_failure", recoverable=True)
                    raise
                ready_generation = max(
                    0,
                    int(event.get("owner_generation") or 0),
                )
                if (
                    event.get("type") == "audio.stream.ready"
                    and (
                        expected_owner_generation <= 0
                        or ready_generation == expected_owner_generation
                    )
                ):
                    metrics.note_ready(session_id, expected_owner_generation)
                cursor = max(cursor, int(event.get("sequence") or 0))
            if closed.is_set() and not events:
                return

    receiver = metrics.create_task(receive_controls())
    sender = metrics.create_task(send_events())
    access_expiry = None
    remaining = websocket_access_remaining(ws)
    if remaining is not None:
        async def expire_access() -> None:
            await asyncio.sleep(remaining)
            if not closed.is_set():
                await close_expired_access()

        access_expiry = metrics.create_task(expire_access())
    tasks = tuple(
        task for task in (receiver, sender, access_expiry) if task is not None
    )
    done = set()
    try:
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )
        closed.set()
        for task in pending:
            task.cancel()
    finally:
        closed.set()
        if lease_was_acquired:
            if explicit_stop:
                metrics.note_closed(session_id, expected_owner_generation)
            else:
                metrics.note_disconnect(session_id, expected_owner_generation)
        active_release = ensure_release(session_id)
        for task in tasks:
            if not task.done():
                task.cancel()
        ordered_tasks = tuple(done) + tuple(task for task in tasks if task not in done)

        async def collect_task_results():
            return await asyncio.gather(*ordered_tasks, return_exceptions=True)

        child_cleanup = metrics.create_task(collect_task_results())
        cleanup_cancelled = await drain_task(child_cleanup)
        results = child_cleanup.result()
        if active_release is not None:
            cleanup_cancelled = (
                await drain_task(active_release) or cleanup_cancelled
            )
        unexpected_result = None
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException) and not _is_socket_closing(result):
                unexpected_result = result
                break
        if lease_was_acquired and not explicit_stop and not failure_recorded:
            record_failure("socket_disconnect", recoverable=True)
        if unexpected_result is not None:
            raise unexpected_result
        if cleanup_cancelled:
            raise asyncio.CancelledError
