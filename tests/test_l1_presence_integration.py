from __future__ import annotations

import asyncio

from core.conversation_protocol import ClientCommand
from core.runtime import create_runtime
from gateway.conversation_ws import ConversationWebSocketGateway


def _command(text: str, request_id: str) -> ClientCommand:
    return ClientCommand(
        type="conversation.message",
        session_id="session-presence",
        request_id=request_id,
        idempotency_key=request_id,
        payload={
            "text": text,
            "interaction_mode": "live",
            "protocol_version": 2,
        },
        protocol_version=2,
    )


def test_exact_invocation_uses_persisted_local_lane_without_model(tmp_path):
    async def scenario() -> None:
        runtime = create_runtime(
            tmp_path / "code",
            startup_side_effects=False,
            data_root=tmp_path / "data",
        )
        model_calls: list[str] = []

        async def model_chat(text, **kwargs):
            model_calls.append(text)
            yield {"type": "text_delta", "text": "model response"}

        runtime.agent.chat = model_chat
        gateway = ConversationWebSocketGateway(runtime)
        await runtime.conversation_hub._execution_lock.acquire()
        try:
            result = await gateway._submit(_command("Javis", "request-local"))
            terminal = await runtime.conversation_hub.wait_for_terminal(
                "request-local", timeout=0.25
            )
        finally:
            runtime.conversation_hub._execution_lock.release()

        try:
            events = runtime.conversation_store.events_after("session-presence")
            deltas = [
                event["payload"]["text"]
                for event in events
                if event["type"] == "response.delta"
            ]
            assert result["accepted"] is True
            assert terminal["type"] == "request.completed"
            assert deltas == ["\u6211\u5728"]
            assert model_calls == []
            accepted = next(
                event for event in events if event["type"] == "request.accepted"
            )
            invoked = next(event for event in events if event["type"] == "user.invoked")
            assert accepted["payload"]["execution_lane"] == "deterministic_local"
            assert invoked["payload"]["execution_lane"] == "deterministic_local"
        finally:
            runtime.close()

    asyncio.run(scenario())


def test_complete_request_containing_alias_uses_model_once(tmp_path):
    async def scenario() -> None:
        runtime = create_runtime(
            tmp_path / "code",
            startup_side_effects=False,
            data_root=tmp_path / "data",
        )
        model_calls: list[str] = []

        async def model_chat(text, **kwargs):
            model_calls.append(text)
            yield {"type": "text_delta", "text": "done"}

        runtime.agent.chat = model_chat
        gateway = ConversationWebSocketGateway(runtime)
        try:
            await gateway._submit(_command("Javis, help me", "request-model"))
            await runtime.conversation_hub.wait_for_terminal("request-model", timeout=1)
            assert model_calls == ["Javis, help me"]
        finally:
            runtime.close()

    asyncio.run(scenario())
