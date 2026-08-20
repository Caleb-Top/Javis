from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent_runs import AgentRunStore
from core.conversation_hub import ConversationHub, ConversationRequest
from core.conversation_protocol import ConversationProtocolError, normalize_client_message
from core.conversation_store import ConversationStore
from core.life.memory.access import AccessContextFactory, PrincipalBindingSource
from core.life.memory.contracts import ActorKind, Audience
from core.runtime_access import (
    RuntimeAccessAuthority,
    capability_from_websocket_protocols,
    create_websocket_authorizer,
    websocket_runtime_principal,
)
from gateway.conversation_ws import ConversationWebSocketGateway


class FakeSocket:
    def __init__(self, token: str) -> None:
        self.headers = {
            "origin": "http://tauri.localhost",
            "sec-websocket-protocol": f"javis-runtime-v1, javis-capability.{token}",
        }
        self.client = SimpleNamespace(host="127.0.0.1")
        self.scope = {}
        self.closed = []

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))


def test_authorized_websocket_exposes_a_bearer_free_immutable_principal():
    authority = RuntimeAccessAuthority("boot-memory-access")
    issued = authority.issue(
        "desktop-main",
        ("conversation", "memory.read"),
        ttl_seconds=60,
    )
    socket = FakeSocket(issued.token)

    assert asyncio.run(create_websocket_authorizer(authority)(socket, "conversation"))
    principal = websocket_runtime_principal(socket)

    assert principal is not None
    assert principal.runtime_boot_id == issued.runtime_boot_id
    assert len(principal.client_id_hash) == 64
    assert principal.binding_source == PrincipalBindingSource.PACKAGED_DESKTOP.value
    assert principal.scopes == ("conversation", "memory.read")
    observable = repr(principal.safe_projection())
    assert issued.token not in observable
    assert issued.nonce not in observable
    assert capability_from_websocket_protocols(socket.headers["sec-websocket-protocol"]) == issued.token


@pytest.mark.parametrize(
    "field",
    ["owner", "subject_id", "audience", "participant_subject_ids", "access_context"],
)
def test_conversation_protocol_rejects_client_identity_injection(field: str):
    with pytest.raises(ConversationProtocolError) as captured:
        normalize_client_message(
            {
                "type": "conversation.message",
                "payload": {
                    "session_id": "session-access",
                    "request_id": "request-access",
                    "protocol_version": 2,
                    "text": "hello",
                    field: "forged",
                },
            }
        )

    assert captured.value.code == "reserved_access_field"


def test_gateway_attaches_only_the_server_principal_to_internal_command():
    authority = RuntimeAccessAuthority("boot-memory-access")
    issued = authority.issue("desktop-main", ("conversation",), ttl_seconds=60)
    socket = FakeSocket(issued.token)
    asyncio.run(create_websocket_authorizer(authority)(socket, "conversation"))
    command = normalize_client_message(
        {
            "type": "conversation.message",
            "payload": {
                "session_id": "session-access",
                "request_id": "request-access",
                "protocol_version": 2,
                "text": "hello",
            },
        }
    )

    attached = ConversationWebSocketGateway._attach_server_principal(socket, command)

    assert command.server_principal is None
    assert attached.server_principal is not None
    assert attached.server_principal.session_id == "session-access"
    assert attached.server_principal.client_id_hash == websocket_runtime_principal(socket).client_id_hash


def test_hub_persists_a_safe_guest_projection_for_legacy_requests(tmp_path: Path):
    conversation_store = ConversationStore(tmp_path / "conversations.sqlite3")
    run_store = AgentRunStore(tmp_path / "runs.sqlite3")
    hub = ConversationHub(conversation_store, run_store)

    async def runner(_request, _token):
        yield {"type": "text_delta", "text": "response"}
        yield {"type": "done", "success": True}

    async def scenario():
        result = await hub.submit(
            ConversationRequest(
                session_id="session-guest",
                request_id="request-guest",
                text="hello",
            ),
            runner,
        )
        assert result["accepted"]
        await hub.wait_for_terminal("request-guest")
        await hub.shutdown()

    try:
        asyncio.run(scenario())
        evidence = conversation_store.read_request_evidence(
            "session-guest",
            "request-guest",
        )
    finally:
        run_store.close()

    projection = evidence["access_projection"]
    assert projection is not None
    assert projection["actor_kind"] == ActorKind.GUEST.value
    assert projection["audience_ceiling"] == Audience.GUEST.value
    assert projection["session_id"] == "session-guest"
    assert "token" not in repr(projection).casefold()
    assert "nonce" not in repr(projection).casefold()


def test_hub_rejects_cross_session_internal_access_context(tmp_path: Path):
    conversation_store = ConversationStore(tmp_path / "conversations.sqlite3")
    run_store = AgentRunStore(tmp_path / "runs.sqlite3")
    hub = ConversationHub(conversation_store, run_store)
    context = AccessContextFactory(
        None,
        runtime_boot_id="boot-memory-access",
    ).for_session("other-session", principal=None)

    async def runner(_request, _token):
        yield {"type": "done", "success": True}

    try:
        with pytest.raises(ValueError, match="access context session mismatch"):
            asyncio.run(
                hub.submit(
                    ConversationRequest(
                        session_id="session-access",
                        request_id="request-access",
                        text="hello",
                        access_context=context,
                    ),
                    runner,
                )
            )
    finally:
        asyncio.run(hub.shutdown())
        run_store.close()
