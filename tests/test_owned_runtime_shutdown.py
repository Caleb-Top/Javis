import asyncio
import hashlib
import importlib
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException


def _load_main(tmp_path, monkeypatch):
    monkeypatch.setenv("JAVIS_TEST_MODE", "1")
    workspace = Path(__file__).resolve().parents[1]
    isolated = hashlib.sha256(str(tmp_path).encode("utf-8")).hexdigest()[:16]
    monkeypatch.setenv(
        "JAVIS_DATA_ROOT",
        str(workspace.parent / ".javis-test-data" / isolated),
    )
    monkeypatch.setenv(
        "JAVIS_EVENT_STORE_PATH",
        str(tmp_path / "session-events.sqlite3"),
    )
    return importlib.import_module("main")


def test_missing_wrong_and_non_loopback_requests_are_forbidden(
    tmp_path,
    monkeypatch,
):
    main = _load_main(tmp_path, monkeypatch)
    closed = []
    controller = main.OwnedRuntimeShutdown(
        configured_token="owned-token",
        close_runtime=lambda: closed.append("closed"),
    )
    controller.set_exit_callback(lambda: closed.append("exit"))

    async def exercise():
        for host, token in (
            ("127.0.0.1", ""),
            ("127.0.0.1", "wrong-token"),
            ("127.0.0.1", "错误令牌"),
            ("192.168.1.20", "owned-token"),
        ):
            with pytest.raises(PermissionError):
                await controller.request(host, token)

    asyncio.run(exercise())
    assert closed == []


def test_empty_configured_token_is_never_an_authority(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    controller = main.OwnedRuntimeShutdown(
        configured_token="",
        close_runtime=lambda: None,
    )
    controller.set_exit_callback(lambda: None)

    with pytest.raises(PermissionError):
        asyncio.run(controller.request("127.0.0.1", ""))


def test_runtime_closes_before_server_exit_and_only_once(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    order = []

    async def close_runtime():
        order.append("close-start")
        await asyncio.sleep(0)
        order.append("close-finished")

    controller = main.OwnedRuntimeShutdown(
        configured_token="owned-token",
        close_runtime=close_runtime,
    )
    controller.set_exit_callback(lambda: order.append("server-exit"))

    async def exercise():
        assert await controller.request("::1", "owned-token") is True
        assert await controller.request("127.0.0.1", "owned-token") is False
        await controller.close_once()

    asyncio.run(exercise())

    assert order == ["close-start", "close-finished", "server-exit"]


def test_server_exit_is_not_scheduled_when_runtime_close_fails(
    tmp_path,
    monkeypatch,
):
    main = _load_main(tmp_path, monkeypatch)
    order = []

    async def fail_close():
        order.append("close")
        raise RuntimeError("checkpoint failed")

    controller = main.OwnedRuntimeShutdown(
        configured_token="owned-token",
        close_runtime=fail_close,
    )
    controller.set_exit_callback(lambda: order.append("server-exit"))

    with pytest.raises(RuntimeError, match="checkpoint failed"):
        asyncio.run(controller.request("127.0.0.1", "owned-token"))

    assert order == ["close"]


def test_shutdown_endpoint_maps_authorization_and_availability(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    controller = main.OwnedRuntimeShutdown(
        configured_token="owned-token",
        close_runtime=lambda: None,
    )
    monkeypatch.setattr(main, "owned_runtime_shutdown", controller)

    with pytest.raises(HTTPException) as forbidden:
        asyncio.run(
            main.api_runtime_shutdown(
                request,
                x_javis_sidecar_ownership="wrong",
            )
        )
    assert forbidden.value.status_code == 403

    with pytest.raises(HTTPException) as unavailable:
        asyncio.run(
            main.api_runtime_shutdown(
                request,
                x_javis_sidecar_ownership="owned-token",
            )
        )
    assert unavailable.value.status_code == 503


def test_runtime_access_endpoint_requires_owned_loopback_process(
    tmp_path,
    monkeypatch,
):
    from core.runtime_access import RuntimeAccessAuthority

    main = _load_main(tmp_path, monkeypatch)
    controller = main.OwnedRuntimeShutdown(
        configured_token="owned-token",
        close_runtime=lambda: None,
    )
    monkeypatch.setattr(main, "owned_runtime_shutdown", controller)
    monkeypatch.setattr(
        main,
        "runtime_access_authority",
        RuntimeAccessAuthority("boot-test"),
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    payload = {
        "client_instance_id": "desktop-main",
        "scopes": ["conversation", "voice.capture"],
        "ttl_seconds": 30,
    }

    with pytest.raises(HTTPException) as forbidden:
        asyncio.run(
            main.api_runtime_access(
                request,
                payload,
                x_javis_sidecar_ownership="wrong",
            )
        )
    assert forbidden.value.status_code == 403

    issued = asyncio.run(
        main.api_runtime_access(
            request,
            payload,
            x_javis_sidecar_ownership="owned-token",
        )
    )
    assert issued["ok"] is True
    assert issued["runtime_boot_id"] == "boot-test"
    assert issued["client_instance_id"] == "desktop-main"
    assert issued["scopes"] == ["conversation", "voice.capture"]
    assert len(issued["token"]) >= 43
    assert "nonce" not in issued

    exited = []
    controller.set_exit_callback(lambda: exited.append(True))
    response = asyncio.run(
        main.api_runtime_shutdown(
            request,
            x_javis_sidecar_ownership="owned-token",
        )
    )
    assert response == {"ok": True, "state": "shutdown_requested"}
    assert exited == [True]


def test_runtime_access_development_issuer_is_explicit_and_origin_bounded(
    tmp_path,
    monkeypatch,
):
    from core.runtime_access import RuntimeAccessAuthority

    main = _load_main(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "_ALLOW_DEV_RUNTIME_ACCESS", True)
    monkeypatch.setattr(
        main,
        "runtime_access_authority",
        RuntimeAccessAuthority("boot-dev", allow_development_origins=True),
    )
    payload = {
        "client_instance_id": "browser-preview",
        "scopes": ["conversation"],
        "ttl_seconds": 30,
    }
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"origin": "http://localhost:5173"},
    )

    issued = asyncio.run(main.api_runtime_access(request, payload))
    assert issued["ok"] is True

    request.headers["origin"] = "http://localhost:5174"
    with pytest.raises(HTTPException) as forbidden:
        asyncio.run(main.api_runtime_access(request, payload))
    assert forbidden.value.status_code == 403


def test_native_capture_http_rejects_before_starting_microphone(
    tmp_path,
    monkeypatch,
):
    main = _load_main(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr(main, "start_capture", lambda *args: started.append(args))
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"origin": "http://tauri.localhost"},
    )

    with pytest.raises(HTTPException) as unauthorized:
        asyncio.run(
            main.api_voice_capture_start(
                request,
                {"source": "microphone"},
            )
        )

    assert unauthorized.value.status_code == 401
    assert started == []


def test_sidecar_shutdown_is_loopback_only_and_uses_user_data_root():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "src-tauri"
        / "src"
        / "sidecar.rs"
    ).read_text(encoding="utf-8")

    assert "SocketAddr::from(([127, 0, 0, 1], port))" in source
    assert "POST /api/runtime/shutdown HTTP/1.1" in source
    assert "X-Javis-Sidecar-Ownership: {token}" in source
    assert "self.data_root.is_absolute()" in source
    assert '.env("JAVIS_DATA_ROOT", &self.data_root)' in source
    assert "runtime-data" not in source
    assert '.env("JAVIS_DATA_ROOT", &root)' not in source


def test_shutdown_route_is_post_only(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    route = next(
        route
        for route in main.app.routes
        if getattr(route, "path", None) == "/api/runtime/shutdown"
    )

    assert route.methods == {"POST"}
