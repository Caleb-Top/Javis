from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

from core.action.route_policy import DEFAULT_ROUTE_POLICY_REGISTRY
from core.conversation_protocol import CANONICAL_COMMANDS
from core.runtime_access import (
    RuntimeAccessAuthority,
    create_websocket_authorizer,
    create_websocket_command_authorizer,
    validate_websocket_command,
)


ORIGIN = "http://tauri.localhost"


class _Socket:
    def __init__(self, token: str, *, origin: str = ORIGIN, host: str = "127.0.0.1"):
        self.headers = {
            "origin": origin,
            "sec-websocket-protocol": f"javis-runtime-v1, javis-capability.{token}",
        }
        self.client = SimpleNamespace(host=host)
        self.scope = {}
        self.closed = []

    async def close(self, *, code: int, reason: str = "") -> None:
        self.closed.append((code, reason))


def _open(*scopes: str):
    authority = RuntimeAccessAuthority("boot-ws-command")
    issued = authority.issue("desktop-main", scopes, ttl_seconds=60)
    socket = _Socket(issued.token)
    assert asyncio.run(create_websocket_authorizer(authority)(socket, "conversation"))
    return authority, issued, socket


def test_websocket_inventory_covers_every_current_canonical_command():
    report = DEFAULT_ROUTE_POLICY_REGISTRY.inventory(
        SimpleNamespace(routes=()),
        websocket_commands=CANONICAL_COMMANDS,
    )
    assert report.healthy
    assert report.unknown_websocket_commands == ()

    expected = {
        "conversation.attach": ("conversation.read",),
        "ping": ("conversation.read",),
        "conversation.message": ("conversation.write",),
        "voice": ("conversation.write", "voice.capture"),
        "conversation.cancel": ("conversation.cancel",),
        "conversation.confirm": ("action.approve",),
        "tool": ("action.execute",),
        "folder_file": ("workspace.write",),
        "permission_change": ("permission.write",),
    }
    for command, scopes in expected.items():
        assert DEFAULT_ROUTE_POLICY_REGISTRY.websocket_policy(command).required_scopes == scopes


def test_read_only_socket_cannot_approve_execute_or_change_permission():
    authority, _, socket = _open("conversation.read")

    assert validate_websocket_command(
        authority, socket, "conversation.attach"
    ).allowed
    assert validate_websocket_command(authority, socket, "ping").allowed
    for command in (
        "conversation.approval.resolve",
        "conversation.confirm",
        "tool",
        "permission_change",
    ):
        decision = validate_websocket_command(authority, socket, command)
        assert decision.allowed is False
        assert decision.reason_code == "scope_denied"
        assert decision.close_code == 4403


def test_message_voice_cancel_and_file_scopes_do_not_inherit_each_other():
    authority, _, socket = _open("conversation.read", "conversation.write")

    assert validate_websocket_command(
        authority, socket, "conversation.message"
    ).allowed
    assert validate_websocket_command(
        authority, socket, "voice"
    ).reason_code == "scope_denied"
    assert validate_websocket_command(
        authority, socket, "conversation.cancel"
    ).reason_code == "scope_denied"
    assert validate_websocket_command(
        authority, socket, "folder_file"
    ).reason_code == "scope_denied"


def test_command_authorizer_rechecks_revocation_before_command_effect():
    authority, _, socket = _open("conversation.read", "action.execute")
    gate = create_websocket_command_authorizer(authority)

    assert asyncio.run(gate(socket, "tool")) is True
    authority.revoke_client("desktop-main")
    assert asyncio.run(gate(socket, "tool")) is False
    assert socket.closed == [(4401, "capability_revoked")]


def test_unknown_command_policy_fails_closed_without_scope_confusion():
    authority, _, socket = _open("conversation.read", "action.execute")
    decision = validate_websocket_command(authority, socket, "tool.extra")
    assert decision.allowed is False
    assert decision.reason_code == "route_policy_missing"
    assert decision.close_code == 4403


def test_legacy_conversation_scope_remains_explicitly_compatible_only_for_chat():
    authority, _, socket = _open("conversation")

    assert validate_websocket_command(authority, socket, "conversation.attach").allowed
    assert validate_websocket_command(authority, socket, "conversation.message").allowed
    assert validate_websocket_command(authority, socket, "tool").reason_code == "scope_denied"


def test_session_scope_and_diagnostics_do_not_leak_bearer_or_digest():
    authority, issued, socket = _open("conversation.read")
    digest = hashlib.sha256(issued.token.encode("ascii")).hexdigest()
    observable = repr(
        {
            "scope": socket.scope,
            "diagnostics": authority.diagnostics(),
        }
    )
    assert issued.token not in observable
    assert digest not in observable
    assert issued.nonce not in observable
