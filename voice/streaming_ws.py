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


async def serve_continuous_voice_stream(ws, manager) -> None:
    await ws.accept()
    closed = asyncio.Event()
    stream_acquired = False

    async def send_json(message: dict) -> None:
        try:
            await ws.send_json(message)
        except Exception:
            closed.set()
            raise

    async def send_error(error: Exception) -> None:
        if closed.is_set():
            return
        try:
            await send_json({"type": "audio.error", "message": str(error)[:500]})
        except Exception:
            pass

    async def release_stream(session_id: str) -> None:
        nonlocal stream_acquired
        if not stream_acquired:
            return
        try:
            await asyncio.to_thread(manager.stop, session_id=session_id)
        except RuntimeError as error:
            if str(error) != "microphone stream belongs to another conversation":
                raise
        stream_acquired = False

    try:
        first = await ws.receive_json()
    except (WebSocketDisconnect, RuntimeError):
        return
    except Exception as error:
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
        try:
            await asyncio.shield(acquisition)
        except asyncio.CancelledError:
            try:
                await acquisition
            except Exception:
                pass
            else:
                stream_acquired = True
                await release_stream(session_id)
            raise
        stream_acquired = True
    except Exception as error:
        await send_error(error)
        return

    async def receive_controls() -> None:
        while True:
            try:
                message = await ws.receive_json()
            except (WebSocketDisconnect, RuntimeError):
                closed.set()
                return
            except Exception as error:
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
    try:
        _, pending = await asyncio.wait(
            (receiver, sender),
            return_when=asyncio.FIRST_COMPLETED,
        )
        closed.set()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        closed.set()
        for task in (receiver, sender):
            if not task.done():
                task.cancel()
        await asyncio.gather(receiver, sender, return_exceptions=True)
        await release_stream(session_id)
