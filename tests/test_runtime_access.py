import asyncio
from types import SimpleNamespace

import pytest

from core.runtime_access import (
    RuntimeAccessAuthority,
    capability_from_websocket_protocols,
    create_http_authorizer,
    create_websocket_authorizer,
)


BOOT_ID = "boot-1"
TAURI_ORIGIN = "http://tauri.localhost"


class _Socket:
    def __init__(self, *, token="", origin=TAURI_ORIGIN, host="127.0.0.1"):
        protocols = "javis-runtime-v1"
        if token:
            protocols += f", javis-capability.{token}"
        self.headers = {
            "origin": origin,
            "sec-websocket-protocol": protocols,
        }
        self.client = SimpleNamespace(host=host)
        self.scope = {}
        self.closed = []
        self.accepted = False

    async def close(self, code, reason=""):
        self.closed.append((code, reason))

    async def accept(self, subprotocol=None):
        self.accepted = True


def _authority(*, now=None, allow_development_origins=False):
    return RuntimeAccessAuthority(
        BOOT_ID,
        now=now,
        allow_development_origins=allow_development_origins,
    )


def test_capability_is_scoped_bounded_and_never_exposed_by_diagnostics():
    now = [100.0]
    authority = _authority(now=lambda: now[0])

    issued = authority.issue(
        "desktop-main",
        ("conversation", "life.read"),
        ttl_seconds=30,
    )

    assert len(issued.token) >= 43
    assert issued.runtime_boot_id == BOOT_ID
    assert issued.client_instance_id == "desktop-main"
    assert issued.scopes == ("conversation", "life.read")
    assert issued.expires_at_epoch == 130.0
    assert authority.validate(
        issued.token,
        scope="conversation",
        origin=TAURI_ORIGIN,
        peer_host="127.0.0.1",
    ).allowed
    assert not authority.validate(
        issued.token,
        scope="voice.capture",
        origin=TAURI_ORIGIN,
        peer_host="127.0.0.1",
    ).allowed

    diagnostics = authority.diagnostics()
    assert diagnostics["active_capabilities"] == 1
    assert issued.token not in repr(diagnostics)
    assert issued.nonce not in repr(diagnostics)


def test_expiry_boot_rotation_and_client_revocation_fail_closed():
    now = [100.0]
    authority = _authority(now=lambda: now[0])
    expired = authority.issue("desktop-main", ("conversation",), ttl_seconds=5)
    now[0] = 105.0
    assert authority.validate(
        expired.token,
        scope="conversation",
        origin=TAURI_ORIGIN,
        peer_host="::1",
    ).reason_code == "capability_expired"

    active = authority.issue("desktop-main", ("conversation",), ttl_seconds=30)
    authority.revoke_client("desktop-main")
    assert authority.validate(
        active.token,
        scope="conversation",
        origin=TAURI_ORIGIN,
        peer_host="127.0.0.1",
    ).reason_code == "capability_invalid"

    rotated = authority.issue("desktop-main", ("conversation",), ttl_seconds=30)
    authority.rotate_boot("boot-2")
    assert authority.validate(
        rotated.token,
        scope="conversation",
        origin=TAURI_ORIGIN,
        peer_host="127.0.0.1",
    ).reason_code == "capability_invalid"


@pytest.mark.parametrize(
    ("origin", "host", "reason"),
    [
        ("http://evil.local", "127.0.0.1", "origin_denied"),
        (TAURI_ORIGIN, "192.168.1.20", "peer_not_loopback"),
        ("http://localhost:5173", "127.0.0.1", "origin_denied"),
    ],
)
def test_origin_and_peer_policy_rejects_untrusted_local_callers(origin, host, reason):
    authority = _authority()
    issued = authority.issue("desktop-main", ("conversation",))
    decision = authority.validate(
        issued.token,
        scope="conversation",
        origin=origin,
        peer_host=host,
    )
    assert decision.allowed is False
    assert decision.reason_code == reason


def test_fixed_development_origin_requires_explicit_flag():
    authority = _authority(allow_development_origins=True)
    issued = authority.issue("desktop-main", ("conversation",))
    assert authority.validate(
        issued.token,
        scope="conversation",
        origin="http://localhost:5173",
        peer_host="127.0.0.1",
    ).allowed
    assert not authority.validate(
        issued.token,
        scope="conversation",
        origin="http://localhost:5174",
        peer_host="127.0.0.1",
    ).allowed


def test_websocket_protocol_parser_rejects_ambiguous_or_malformed_tokens():
    assert capability_from_websocket_protocols(
        "javis-runtime-v1, javis-capability.abc_DEF-123456789"
    ) == "abc_DEF-123456789"
    assert capability_from_websocket_protocols("javis-capability.one, javis-capability.two") == ""
    assert capability_from_websocket_protocols("javis-capability.bad token") == ""
    assert capability_from_websocket_protocols("unrelated") == ""


def test_websocket_authorizer_closes_before_accept_with_stable_codes():
    authority = _authority()
    authorize = create_websocket_authorizer(authority)

    missing = _Socket()
    assert asyncio.run(authorize(missing, "conversation")) is False
    assert missing.closed == [(4401, "missing_capability")]
    assert missing.accepted is False

    wrong_scope = authority.issue("desktop-main", ("life.read",))
    denied = _Socket(token=wrong_scope.token)
    assert asyncio.run(authorize(denied, "conversation")) is False
    assert denied.closed == [(4403, "scope_denied")]
    assert denied.accepted is False

    valid = authority.issue("desktop-main", ("conversation",))
    allowed = _Socket(token=valid.token)
    assert asyncio.run(authorize(allowed, "conversation")) is True
    assert allowed.closed == []
    assert allowed.accepted is False
    context = allowed.scope["javis.runtime_access"]
    assert context["scope"] == "conversation"
    assert context["client_id_hash"]
    assert context["nonce_digest"]
    assert valid.token not in repr(context)


def test_http_authorizer_uses_the_same_origin_peer_and_scope_policy():
    authority = _authority()
    issued = authority.issue("desktop-main", ("life.read",))
    authorize = create_http_authorizer(authority)
    request = SimpleNamespace(
        headers={
            "origin": TAURI_ORIGIN,
            "x-javis-runtime-capability": issued.token,
        },
        client=SimpleNamespace(host="127.0.0.1"),
    )

    assert authorize(request, "life.read").allowed
    assert authorize(request, "diagnostics.read").reason_code == "scope_denied"
    request.headers.pop("x-javis-runtime-capability")
    assert authorize(request, "life.read").reason_code == "missing_capability"


def test_invalid_issue_requests_and_capacity_are_bounded():
    authority = RuntimeAccessAuthority(BOOT_ID, max_active=2)
    with pytest.raises(ValueError, match="scope"):
        authority.issue("desktop-main", ("unknown",))
    with pytest.raises(ValueError, match="ttl"):
        authority.issue("desktop-main", ("conversation",), ttl_seconds=0)
    with pytest.raises(ValueError, match="client_instance_id"):
        authority.issue("", ("conversation",))

    first = authority.issue("first", ("conversation",))
    authority.issue("second", ("conversation",))
    authority.issue("third", ("conversation",))
    assert authority.validate(
        first.token,
        scope="conversation",
        origin=TAURI_ORIGIN,
        peer_host="127.0.0.1",
    ).reason_code == "capability_invalid"
