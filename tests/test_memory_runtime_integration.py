from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import core.runtime as runtime_module
from core.life.memory.access import (
    AccessContextFactory,
    MemoryServiceAccessView,
    PrincipalBindingSource,
    ServerPrincipal,
)
from core.life.memory.contracts import AccessPurpose, ActorKind
from core.life.memory.service import MemoryService
from core.runtime_access import RuntimeAccessAuthority


BOOT_ID = "boot-memory-binding"
TAURI_ORIGIN = "http://tauri.localhost"


def _server_principal(
    authority: RuntimeAccessAuthority,
    token: str,
    *,
    session_id: str,
    scope: str,
) -> ServerPrincipal:
    decision = authority.validate(
        token,
        scope=scope,
        origin=TAURI_ORIGIN,
        peer_host="127.0.0.1",
    )
    assert decision.allowed and decision.principal is not None
    principal = decision.principal
    return ServerPrincipal(
        runtime_boot_id=principal.runtime_boot_id,
        session_id=session_id,
        client_id_hash=principal.client_id_hash,
        capability_scopes=principal.scopes,
        issued_at_epoch=principal.issued_at_epoch,
        expires_at_epoch=principal.expires_at_epoch,
        binding_source=PrincipalBindingSource(principal.binding_source),
    )


def _runtime(tmp_path: Path):
    return runtime_module.create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )


def test_runtime_starts_one_memory_owner_and_exposes_content_free_status(tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        status = runtime.memory_service.status()
        assert status["state"] == "ready"
        assert status["store"]["state"] == "ready"
        assert status["writer_alive"] is True
        assert runtime.get_runtime_status()["memory"] == status
        assert runtime.conversation_hub._terminal_wakeup.__self__ is runtime.memory_service
        assert runtime.conversation_hub._terminal_wakeup.__func__ is MemoryService.wake_reconcile
        assert (tmp_path / "data" / "memory" / "autobiographical.sqlite3").is_file()
        assert "prompt_text" not in repr(status)
        assert "query_text" not in repr(status)
    finally:
        runtime.close()


def test_runtime_access_factory_reads_through_bounded_service_view(tmp_path: Path):
    runtime = _runtime(tmp_path)
    try:
        assert isinstance(runtime.memory_access_factory._store, MemoryServiceAccessView)
        access = runtime.memory_access_factory.for_session(
            "unbound-session",
            principal=None,
        )
        assert access.actor_kind is ActorKind.GUEST
        assert access.acl_epoch == runtime.memory_service.status()["store"]["acl_epoch"]
    finally:
        runtime.close()


def test_memory_startup_failure_degrades_only_memory(tmp_path: Path, monkeypatch):
    original = MemoryService

    def degraded_service(data_root, **kwargs):
        def broken_store(*args, **store_kwargs):
            raise OSError("memory offline")

        return original(data_root, store_factory=broken_store, **kwargs)

    monkeypatch.setattr(runtime_module, "MemoryService", degraded_service)
    runtime = _runtime(tmp_path)
    try:
        assert runtime.memory_service.status()["state"] == "degraded"
        assert runtime.life.status()["state"] == "running"
        assert runtime.conversation_hub.store is runtime.conversation_store
        access = runtime.memory_access_factory.for_session("session-1", principal=None)
        assert access.actor_kind is ActorKind.GUEST
    finally:
        runtime.close()


def test_async_close_stops_admission_before_draining_memory(tmp_path: Path):
    runtime = _runtime(tmp_path)
    events: list[str] = []
    hub_shutdown = runtime.conversation_hub.shutdown
    memory_shutdown = runtime.memory_service.shutdown

    async def tracked_hub_shutdown():
        events.append("conversation")
        await hub_shutdown()

    def tracked_memory_shutdown(*, timeout=5.0, drain=True):
        events.append("memory")
        return memory_shutdown(timeout=timeout, drain=drain)

    runtime.conversation_hub.shutdown = tracked_hub_shutdown
    runtime.memory_service.shutdown = tracked_memory_shutdown

    asyncio.run(runtime.aclose())

    assert events[:2] == ["conversation", "memory"]
    assert runtime.memory_service.status()["state"] == "stopped"


def test_primary_session_bind_is_idempotent_and_unlocks_only_matching_client(tmp_path: Path):
    session_id = "session-primary-bind"
    service = MemoryService(tmp_path / "memory", runtime_boot_id=BOOT_ID).start()
    authority = RuntimeAccessAuthority(BOOT_ID)
    issued = authority.issue("desktop-main", ("conversation", "memory.read"))
    principal = _server_principal(
        authority,
        issued.token,
        session_id=session_id,
        scope="conversation",
    )
    access_factory = AccessContextFactory(
        MemoryServiceAccessView(service, timeout=5),
        runtime_boot_id=BOOT_ID,
    )
    try:
        before = access_factory.for_session(
            session_id,
            principal=_server_principal(
                authority,
                issued.token,
                session_id=session_id,
                scope="memory.read",
            ),
            purpose=AccessPurpose.RECALL,
        )
        assert before.actor_kind is ActorKind.GUEST

        first = service.bind_primary_session(session_id, principal).result(timeout=10)
        replay = service.bind_primary_session(session_id, principal).result(timeout=10)
        assert first == replay == {"session_id": session_id, "state": "bound"}
        assert "subject" not in repr(first)
        assert issued.token not in repr(first)

        after = access_factory.for_session(
            session_id,
            principal=_server_principal(
                authority,
                issued.token,
                session_id=session_id,
                scope="memory.read",
            ),
            purpose=AccessPurpose.RECALL,
        )
        assert after.actor_kind is ActorKind.PRIMARY_USER
        assert len(after.participant_subject_ids) == 2

        other = authority.issue("desktop-other", ("conversation", "memory.read"))
        conflicting = _server_principal(
            authority,
            other.token,
            session_id=session_id,
            scope="conversation",
        )
        with pytest.raises(Exception, match="session_primary_conflict"):
            service.bind_primary_session(session_id, conflicting).result(timeout=10)

        other_context = access_factory.for_session(
            session_id,
            principal=_server_principal(
                authority,
                other.token,
                session_id=session_id,
                scope="memory.read",
            ),
            purpose=AccessPurpose.RECALL,
        )
        assert other_context.actor_kind is ActorKind.GUEST
    finally:
        assert service.shutdown(timeout=10)
