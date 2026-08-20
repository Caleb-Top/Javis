import logging

import pytest

from core.runtime_access import (
    ENVIRONMENT_RUNTIME_ACCESS_SCOPES,
    RuntimeAccessAuthority,
)


BOOT_ID = "environment-boot-1"
TAURI_ORIGIN = "http://tauri.localhost"
L4_SCOPES = frozenset(
    {
        "environment.read",
        "environment.observe",
        "workspace.read",
        "screen.capture",
        "camera.capture",
    }
)


def _validate(authority, token, scope, *, origin=TAURI_ORIGIN, host="127.0.0.1"):
    return authority.validate(
        token,
        scope=scope,
        origin=origin,
        peer_host=host,
    )


def test_l4_scope_catalog_is_exact_and_each_scope_requires_explicit_authority():
    assert ENVIRONMENT_RUNTIME_ACCESS_SCOPES == L4_SCOPES

    for granted_scope in L4_SCOPES:
        authority = RuntimeAccessAuthority(BOOT_ID)
        issued = authority.issue("desktop-main", (granted_scope,))

        assert _validate(authority, issued.token, granted_scope).allowed
        for denied_scope in L4_SCOPES - {granted_scope}:
            decision = _validate(authority, issued.token, denied_scope)
            assert decision.allowed is False
            assert decision.reason_code == "scope_denied"


def test_capability_issued_for_legacy_scopes_never_gains_l4_permissions():
    authority = RuntimeAccessAuthority(BOOT_ID)
    legacy = authority.issue(
        "desktop-main",
        (
            "conversation",
            "diagnostics.read",
            "life.read",
            "playback",
            "voice.capture",
        ),
    )

    for scope in L4_SCOPES:
        decision = _validate(authority, legacy.token, scope)
        assert decision.allowed is False
        assert decision.reason_code == "scope_denied"


@pytest.mark.parametrize("scope", ["*", "environment.*", "*.read", "screen.capture.*"])
def test_scope_wildcards_are_never_issued_or_accepted(scope):
    authority = RuntimeAccessAuthority(BOOT_ID)
    with pytest.raises(ValueError, match="scope"):
        authority.issue("desktop-main", (scope,))

    issued = authority.issue("desktop-main", ("environment.read",))
    with pytest.raises(ValueError, match="scope"):
        _validate(authority, issued.token, scope)


def test_l4_scopes_preserve_origin_loopback_boot_and_ttl_guards():
    now = [100.0]
    authority = RuntimeAccessAuthority(BOOT_ID, now=lambda: now[0])
    issued = authority.issue("desktop-main", L4_SCOPES, ttl_seconds=5)

    assert _validate(
        authority,
        issued.token,
        "environment.read",
        origin="http://evil.local",
    ).reason_code == "origin_denied"
    assert _validate(
        authority,
        issued.token,
        "environment.observe",
        host="192.168.1.20",
    ).reason_code == "peer_not_loopback"

    now[0] = 105.0
    assert _validate(
        authority,
        issued.token,
        "workspace.read",
    ).reason_code == "capability_expired"

    current = authority.issue("desktop-main", ("screen.capture",), ttl_seconds=5)
    authority.rotate_boot("environment-boot-2")
    assert _validate(
        authority,
        current.token,
        "screen.capture",
    ).reason_code == "capability_invalid"


def test_l4_authorization_audit_and_logs_never_expose_bearer(caplog):
    caplog.set_level(logging.DEBUG)
    authority = RuntimeAccessAuthority(BOOT_ID)
    issued = authority.issue("desktop-main", L4_SCOPES)

    assert _validate(authority, issued.token, "camera.capture").allowed
    assert _validate(
        authority,
        issued.token,
        "environment.read",
        origin="http://evil.local",
    ).reason_code == "origin_denied"

    diagnostics = authority.diagnostics()
    assert all(
        set(record) == {"reason_code", "scope", "client_id_hash"}
        for record in diagnostics["recent_audit"]
    )

    observable_output = f"{diagnostics!r}\n{caplog.text}"
    assert issued.token not in observable_output
    assert issued.nonce not in observable_output
    assert "javis-capability." not in observable_output
