"""Non-blocking WebSocket gateway for shared Javis conversations."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from contextlib import suppress
from dataclasses import replace
from typing import Any, Callable

from fastapi import WebSocketDisconnect

from core.conversation_hub import ConversationRequest
from core.conversation_protocol import (
    ClientCommand,
    ConversationProtocolError,
    legacy_wire_events,
    normalize_client_message,
)
from core.life.l1.contracts import (
    InputModality,
    InputProvenance,
    InputVerification,
)
from core.life.l1.wake import DETERMINISTIC_LOCAL_LANE, PresenceResponder
from core.life.memory.access import PrincipalBindingSource, ServerPrincipal
from core.life.memory.contracts import AccessPurpose
from core.life.memory.recall import build_recall_query
from core.runtime_access import websocket_access_remaining, websocket_runtime_principal
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
        authorize: Callable[[Any, str], Any] | None = None,
        voice_turn_registry=None,
        presence_responder: PresenceResponder | None = None,
    ):
        self.runtime = runtime
        self.transcribe = transcribe
        self.local_action_resolver = local_action_resolver
        self.command_handlers = dict(command_handlers or {})
        self.stall_harness = stall_harness
        self.authorize = authorize
        self.voice_turn_registry = voice_turn_registry
        self.presence_responder = presence_responder or PresenceResponder()
        self._bound_memory_sessions: set[tuple[str, str]] = set()

    async def serve(self, ws) -> None:
        if self.authorize is not None:
            allowed = self.authorize(ws, "conversation")
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                return
            await ws.accept(subprotocol="javis-runtime-v1")
        else:
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
                remaining = websocket_access_remaining(ws)
                if remaining is not None and remaining <= 0:
                    await ws.close(code=4401, reason="capability_expired")
                    break
                try:
                    raw = await (
                        ws.receive_text()
                        if remaining is None
                        else asyncio.wait_for(ws.receive_text(), timeout=remaining)
                    )
                except asyncio.TimeoutError:
                    await ws.close(code=4401, reason="capability_expired")
                    break
                try:
                    message = json.loads(raw)
                    command = self._attach_server_principal(
                        ws,
                        normalize_client_message(message, default_session=attached_session),
                    )
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
    ) -> dict[str, Any]:
        user_text = str(text if text is not None else command.payload.get("text") or "").strip()
        voice_reference = command.voice_provenance
        reservation_active = False
        if voice_reference is not None:
            if self.voice_turn_registry is None:
                raise ConversationProtocolError(
                    "voice_provenance_unavailable",
                    "verified voice provenance is unavailable",
                )
            prior = self.runtime.conversation_store.accepted_request(
                command.session_id,
                command.idempotency_key or command.request_id,
                command.request_id,
            )
            if prior is not None:
                return {
                    "accepted": False,
                    "request_id": prior["request_id"],
                    "duplicate": True,
                    "event": prior["event"],
                }
            try:
                input_provenance = self.voice_turn_registry.reserve(
                    voice_reference,
                    transcript=user_text,
                    session_id=command.session_id,
                    request_id=command.request_id,
                )
                reservation_active = True
            except ValueError as exc:
                raise ConversationProtocolError(
                    "invalid_voice_provenance", str(exc)[:200]
                ) from exc
        elif command.type == "voice" and text is not None:
            input_provenance = InputProvenance(
                InputModality.VOICE,
                InputVerification.SERVER_TRANSCRIBED,
                None,
                None,
                None,
                None,
                None,
            )
        else:
            input_provenance = InputProvenance(
                InputModality.TEXT,
                InputVerification.CLIENT_CLAIMED,
                None,
                None,
                None,
                None,
                None,
            )
        try:
            presence_decision = self.presence_responder.decide(
                user_text,
                session_id=command.session_id,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key or command.request_id,
                input_provenance=input_provenance,
                now_monotonic_ms=time.monotonic() * 1000.0,
            )
        except (TypeError, ValueError) as exc:
            if reservation_active:
                self.voice_turn_registry.rollback(
                    voice_reference,
                    session_id=command.session_id,
                    request_id=command.request_id,
                )
            raise ConversationProtocolError(
                "invalid_presence_invocation", str(exc)[:200]
            ) from exc

        if (
            presence_decision.execution_lane != DETERMINISTIC_LOCAL_LANE
            and str(command.payload.get("interaction_mode") or "") != "live"
        ):
            await self._bind_memory_session(command)

        request = ConversationRequest(
            session_id=command.session_id,
            request_id=command.request_id,
            text=user_text,
            interaction_mode=str(command.payload.get("interaction_mode") or ""),
            idempotency_key=command.idempotency_key or command.request_id,
            input_provenance=input_provenance,
            execution_lane=presence_decision.execution_lane,
            access_context=self._access_context_for(command),
        )

        async def runner(active_request, token):
            if active_request.execution_lane == DETERMINISTIC_LOCAL_LANE:
                if presence_decision.should_respond:
                    yield {
                        "type": "text_delta",
                        "text": presence_decision.response_text,
                    }
                yield {"type": "done", "success": True, "detail": "presence"}
                return
            if stall_mode is not None:
                if self.stall_harness is None:
                    raise RuntimeError("stall mode requires an injected harness")
                async for event in self.stall_harness.run(stall_mode, token):
                    yield event
                return
            recall_bundle = None
            if active_request.interaction_mode != "live":
                memory_service = getattr(self.runtime, "memory_service", None)
                recall = getattr(memory_service, "recall", None)
                if callable(recall):
                    try:
                        recall_query = build_recall_query(
                            active_request.access_context,
                            active_request.text,
                        )
                        recall_result = recall(recall_query)
                        if inspect.isawaitable(recall_result):
                            recall_bundle = await recall_result
                        else:
                            recall_bundle = await asyncio.wrap_future(recall_result)
                    except Exception:
                        recall_bundle = None
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
            chat_kwargs = {
                "session_id": active_request.session_id,
                "conversation_cards": history,
                "interaction_mode": active_request.interaction_mode,
                "cancellation": token,
            }
            if recall_bundle is not None and recall_bundle.items:
                chat_kwargs["recall_bundle"] = recall_bundle
            async for event in self.runtime.agent.chat(active_request.text, **chat_kwargs):
                yield event

        try:
            result = await self.runtime.conversation_hub.submit(request, runner)
        except BaseException:
            if reservation_active:
                self.voice_turn_registry.rollback(
                    voice_reference,
                    session_id=command.session_id,
                    request_id=command.request_id,
                )
            raise
        if reservation_active:
            if result["accepted"]:
                self.voice_turn_registry.commit(
                    voice_reference,
                    session_id=command.session_id,
                    request_id=command.request_id,
                )
            else:
                self.voice_turn_registry.rollback(
                    voice_reference,
                    session_id=command.session_id,
                    request_id=command.request_id,
                )
        return result

    async def _bind_memory_session(self, command: ClientCommand) -> None:
        principal = command.server_principal
        if not isinstance(principal, ServerPrincipal):
            return
        key = (command.session_id, principal.client_id_hash)
        if key in self._bound_memory_sessions:
            return
        service = getattr(self.runtime, "memory_service", None)
        bind = getattr(service, "bind_primary_session", None)
        if not callable(bind):
            return
        try:
            result = bind(command.session_id, principal)
            if inspect.isawaitable(result):
                await result
            else:
                await asyncio.wrap_future(result)
        except Exception as exc:
            logger.warning("Memory identity binding degraded: %s", str(exc)[:120])
            return
        if len(self._bound_memory_sessions) >= 128:
            self._bound_memory_sessions.pop()
        self._bound_memory_sessions.add(key)

    @staticmethod
    def _attach_server_principal(ws, command: ClientCommand) -> ClientCommand:
        authorized = websocket_runtime_principal(ws)
        if authorized is None or not command.session_id:
            return command
        try:
            source = PrincipalBindingSource(authorized.binding_source)
            principal = ServerPrincipal(
                runtime_boot_id=authorized.runtime_boot_id,
                session_id=command.session_id,
                client_id_hash=authorized.client_id_hash,
                capability_scopes=authorized.scopes,
                issued_at_epoch=authorized.issued_at_epoch,
                expires_at_epoch=authorized.expires_at_epoch,
                binding_source=source,
            )
        except (TypeError, ValueError):
            return command
        return replace(command, server_principal=principal)

    def _access_context_for(self, command: ClientCommand):
        factory = getattr(self.runtime, "memory_access_factory", None)
        resolve = getattr(factory, "for_session", None)
        if not callable(resolve):
            return None
        try:
            return resolve(
                command.session_id,
                principal=command.server_principal,
                purpose=AccessPurpose.CONVERSATION,
            )
        except (TypeError, ValueError, RuntimeError):
            return None

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
