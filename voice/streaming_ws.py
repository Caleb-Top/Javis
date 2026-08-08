"""WebSocket transport for the native continuous microphone stream."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocketDisconnect


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


async def serve_continuous_voice_stream(ws, manager) -> None:
    await ws.accept()
    closed = asyncio.Event()
    stream_acquired = False
    release_task = None

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
            await asyncio.to_thread(manager.stop, session_id=session_id)
        except RuntimeError as error:
            if str(error) != "microphone stream belongs to another conversation":
                raise

    def ensure_release(session_id: str):
        nonlocal release_task, stream_acquired
        if release_task is None:
            if not stream_acquired:
                return None
            stream_acquired = False
            release_task = asyncio.create_task(stop_owned_stream(session_id))
        return release_task

    async def release_stream(session_id: str) -> None:
        task = ensure_release(session_id)
        if task is None:
            return
        cancelled = await drain_task(task)
        if cancelled:
            raise asyncio.CancelledError

    try:
        first = await ws.receive_json()
    except Exception as error:
        if not _is_socket_closing(error):
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
            acquisition = asyncio.create_task(
                asyncio.to_thread(
                    manager.start,
                    session_id=session_id,
                    noise_profile=profile,
                    device_index=payload.get("device_index"),
                )
            )
        else:
            acquisition = asyncio.create_task(
                asyncio.to_thread(manager.attach, session_id=session_id)
            )
        cancelled = await drain_task(acquisition)
        stream_acquired = True
        if cancelled:
            await release_stream(session_id)
            raise asyncio.CancelledError
    except Exception as error:
        await send_error(error)
        return

    async def receive_controls() -> None:
        while True:
            try:
                message = await ws.receive_json()
            except Exception as error:
                if not _is_socket_closing(error):
                    await send_error(error)
                closed.set()
                return
            try:
                payload = _payload(message)
                _session_id(payload)
                command = str(message.get("type") or "")
                if command == "audio.stream.stop":
                    await release_stream(session_id)
                    closed.set()
                    return
                if command != "audio.stream.attach":
                    await send_json(
                        {"type": "audio.error", "message": "unsupported audio stream command"}
                    )
            except Exception as error:
                await send_error(error)
                closed.set()
                return

    async def send_events() -> None:
        nonlocal cursor
        while True:
            events = await asyncio.to_thread(
                manager.events_after,
                cursor,
                session_id=session_id,
                timeout=0.1,
            )
            for event in events:
                await send_json(event)
                cursor = max(cursor, int(event.get("sequence") or 0))
            if closed.is_set() and not events:
                return

    receiver = asyncio.create_task(receive_controls())
    sender = asyncio.create_task(send_events())
    tasks = (receiver, sender)
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
        active_release = ensure_release(session_id)
        for task in tasks:
            if not task.done():
                task.cancel()
        ordered_tasks = tuple(done) + tuple(task for task in tasks if task not in done)

        async def collect_task_results():
            return await asyncio.gather(*ordered_tasks, return_exceptions=True)

        child_cleanup = asyncio.create_task(collect_task_results())
        cleanup_cancelled = await drain_task(child_cleanup)
        results = child_cleanup.result()
        if active_release is not None:
            cleanup_cancelled = (
                await drain_task(active_release) or cleanup_cancelled
            )
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException) and not _is_socket_closing(result):
                raise result
        if cleanup_cancelled:
            raise asyncio.CancelledError
