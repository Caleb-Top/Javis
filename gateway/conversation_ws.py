"""Non-blocking WebSocket gateway for shared Javis conversations."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from contextlib import suppress
from typing import Any, Callable

from fastapi import WebSocketDisconnect

from core.conversation_hub import ConversationRequest
from core.conversation_protocol import (
    ClientCommand,
    ConversationProtocolError,
    legacy_wire_events,
    normalize_client_message,
)
from gateway.conversation_stall_harness import ConversationStallHarness, StallMode


logger = logging.getLogger("jarvis.conversation.gateway")


class ConversationWebSocketGateway:
    def __init__(
        self,
        runtime,
        *,
        transcribe: Callable[[str], str] | None = None,
        local_action_resolver: Callable[[str, dict[str, Any]], Any] | None = None,
        command_handlers: dict[str, Callable[[ClientCommand, Any], Any]] | None = None,
        stall_harness: ConversationStallHarness | None = None,
    ):
        self.runtime = runtime
        self.transcribe = transcribe
        self.local_action_resolver = local_action_resolver
        self.command_handlers = dict(command_handlers or {})
        self.stall_harness = stall_harness

    async def serve(self, ws) -> None:
        await ws.accept()
        subscription = None
        sender: asyncio.Task | None = None
        attached_session = ""
        legacy = True
        unacknowledged_stalls: set[tuple[str, str]] = set()
        submitted_stalls: set[tuple[str, str]] = set()

        async def detach() -> None:
            nonlocal subscription, sender, attached_session
            try:
                if sender is not None:
                    sender.cancel()
                    with suppress(asyncio.CancelledError):
                        await sender
            finally:
                try:
                    if subscription is not None:
                        await subscription.close()
                finally:
                    sender = None
                    subscription = None
                    attached_session = ""

        async def attach(command: ClientCommand, *, acknowledge: bool) -> None:
            nonlocal subscription, sender, attached_session, legacy
            if subscription is not None and attached_session == command.session_id:
                legacy = command.legacy
                return
            await detach()
            subscription = await self.runtime.conversation_hub.attach(
                command.session_id,
                after_sequence=command.after_sequence,
            )
            attached_session = command.session_id
            legacy = command.legacy
            if acknowledge and not legacy:
                await ws.send_json({
                    "type": "conversation.attached",
                    "session_id": attached_session,
                    "after_sequence": command.after_sequence,
                    "protocol_version": 2,
                })
            sender = asyncio.create_task(
                self._send_events(ws, subscription, legacy=legacy)
            )

        async def cleanup_stalls() -> None:
            pending = tuple(submitted_stalls)
            submitted_stalls.clear()
            unacknowledged_stalls.clear()
            for session_id, request_id in pending:
                await self.runtime.conversation_hub.cancel(
                    session_id,
                    request_id,
                    reason="stall harness websocket disconnected",
                )
            for _, request_id in pending:
                with suppress(KeyError, asyncio.TimeoutError):
                    await self.runtime.conversation_hub.wait_for_terminal(
                        request_id,
                        timeout=1,
                    )

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    message = json.loads(raw)
                    command = normalize_client_message(message, default_session=attached_session)
                    if command.type == "conversation.attach":
                        await attach(command, acknowledge=True)
                    elif command.type == "conversation.message":
                        await attach(command, acknowledge=False)
                        text = str(command.payload.get("text") or "").strip()
                        stall_mode = (
                            self.stall_harness.mode_for(ws, text)
                            if self.stall_harness is not None
                            else None
                        )
                        request_key = (command.session_id, command.request_id)
                        if stall_mode is StallMode.NO_ACK:
                            unacknowledged_stalls.add(request_key)
                            continue
                        if stall_mode is None and await self._dispatch_local_action(ws, command):
                            continue
                        await self._submit(command, stall_mode=stall_mode)
                        if stall_mode is not None:
                            submitted_stalls.add(request_key)
                    elif command.type == "conversation.cancel":
                        request_key = (command.session_id, command.request_id)
                        if request_key in unacknowledged_stalls:
                            unacknowledged_stalls.discard(request_key)
                            continue
                        cancelled = await self.runtime.conversation_hub.cancel(
                            command.session_id,
                            command.request_id,
                            reason=str(command.payload.get("reason") or "user interrupt"),
                        )
                        if not cancelled:
                            await self._send_protocol_error(
                                ws,
                                "request_not_active",
                                "request is not active",
                            )
                    elif command.type == "conversation.confirm":
                        accepted = await self.runtime.conversation_hub.confirm(
                            command.session_id,
                            command.request_id,
                            str(command.payload.get("approval_id") or ""),
                            bool(command.payload.get("confirmed", False)),
                        )
                        if not accepted:
                            await self._send_protocol_error(
                                ws,
                                "approval_mismatch",
                                "approval does not match the active request",
                            )
                    elif command.type == "voice":
                        await attach(command, acknowledge=False)
                        await self._handle_voice(ws, command, legacy=legacy)
                    elif command.type == "ping":
                        await ws.send_json({
                            "type": "pong",
                            "tools": self.runtime.registry.count,
                            "model": self.runtime.llm.model,
                        })
                    elif command.type in self.command_handlers:
                        result = self.command_handlers[command.type](command, ws)
                        if inspect.isawaitable(result):
                            await result
                    else:
                        await self._send_protocol_error(
                            ws,
                            "unsupported_command",
                            f"unsupported command: {command.type}",
                        )
                except ConversationProtocolError as exc:
                    await ws.send_json(exc.to_event())
                except json.JSONDecodeError:
                    await self._send_protocol_error(ws, "invalid_json", "message is not valid JSON")
        except WebSocketDisconnect:
            pass
        finally:
            try:
                await detach()
            finally:
                await cleanup_stalls()

    async def _submit(
        self,
        command: ClientCommand,
        *,
        text: str | None = None,
        stall_mode: StallMode | None = None,
    ) -> None:
        user_text = str(text if text is not None else command.payload.get("text") or "").strip()
        request = ConversationRequest(
            session_id=command.session_id,
            request_id=command.request_id,
            text=user_text,
            interaction_mode=str(command.payload.get("interaction_mode") or ""),
            idempotency_key=command.idempotency_key or command.request_id,
        )

        async def runner(active_request, token):
            if stall_mode is not None:
                if self.stall_harness is None:
                    raise RuntimeError("stall mode requires an injected harness")
                async for event in self.stall_harness.run(stall_mode, token):
                    yield event
                return
            engine = getattr(self.runtime, "engine", None)
            use_route = getattr(engine, "use_route", None)
            if callable(use_route):
                use_route(active_request.interaction_mode)
            else:
                llm = getattr(self.runtime, "llm", None)
                select_route = getattr(llm, "use_route", None)
                if callable(select_route):
                    select_route(active_request.interaction_mode)
            history = [
                card
                for card in self.runtime.conversation_store.history(
                    active_request.session_id,
                    limit=80,
                )
                if card.get("request_id") != active_request.request_id
            ]
            async for event in self.runtime.agent.chat(
                active_request.text,
                session_id=active_request.session_id,
                conversation_cards=history,
                interaction_mode=active_request.interaction_mode,
                cancellation=token,
            ):
                yield event

        await self.runtime.conversation_hub.submit(request, runner)

    async def _dispatch_local_action(self, ws, command: ClientCommand) -> bool:
        if self.local_action_resolver is None:
            return False
        if str(command.payload.get("interaction_mode") or "") != "live":
            return False
        action = self.local_action_resolver(
            str(command.payload.get("text") or ""),
            command.payload,
        )
        if inspect.isawaitable(action):
            action = await action
        if not action:
            return False
        await ws.send_json({
            "type": "app_action",
            "action": str(action),
            "session_id": command.session_id,
            "request_id": command.request_id,
        })
        await ws.send_json({
            "type": "done",
            "detail": f"{action} opened",
            "session_id": command.session_id,
            "request_id": command.request_id,
        })
        return True

    async def _handle_voice(self, ws, command: ClientCommand, *, legacy: bool) -> None:
        audio = str(command.payload.get("audio") or "").strip()
        if not audio:
            await self._send_protocol_error(ws, "empty_audio", "audio is empty")
            return
        if self.transcribe is None:
            await self._send_protocol_error(ws, "stt_unavailable", "speech recognition is unavailable")
            return
        if inspect.iscoroutinefunction(self.transcribe):
            transcript = await self.transcribe(audio)
        else:
            transcript = await asyncio.to_thread(self.transcribe, audio)
        transcript = str(transcript or "").strip()
        if not transcript:
            await ws.send_json({
                "type": "done" if legacy else "voice.transcript.empty",
                "detail": "No speech recognized",
                "session_id": command.session_id,
                "request_id": command.request_id,
            })
            return
        if legacy:
            await ws.send_json({
                "type": "voice_transcript",
                "text": transcript,
                "session_id": command.session_id,
                "request_id": command.request_id,
            })
        else:
            await ws.send_json({
                "type": "voice.transcript.final",
                "session_id": command.session_id,
                "request_id": command.request_id,
                "payload": {"text": transcript},
            })
        await self._submit(command, text=transcript)

    @staticmethod
    async def _send_events(ws, subscription, *, legacy: bool) -> None:
        async def send(event: dict[str, Any]) -> None:
            if legacy:
                for projected in legacy_wire_events(event):
                    await ws.send_json(projected)
            else:
                await ws.send_json(event)

        for event in subscription.replay:
            await send(event)
        while True:
            await send(await subscription.get())

    @staticmethod
    async def _send_protocol_error(ws, code: str, message: str) -> None:
        await ws.send_json({
            "type": "protocol.error",
            "payload": {"code": str(code), "message": str(message)},
        })
