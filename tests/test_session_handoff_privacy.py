from __future__ import annotations

import asyncio
import hashlib
import io
import math
import struct
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.agent_runs import AgentRunStore
from core.conversation_hub import ConversationHub, ConversationRequest
from core.conversation_store import ConversationStore, ConversationStoreError
from core.life.memory.access import (
    AccessBindingError,
    AccessContextFactory,
    MemoryServiceAccessView,
    PrincipalBindingSource,
    ServerPrincipal,
    safe_access_projection,
)
from core.life.memory.contracts import AccessContext, ActorKind, IdentityAssurance
from core.life.memory.service import MemoryService
from core.life.memory.subjects import (
    BootstrapPrimary,
    CreateKnownPerson,
    HandoffSession,
    LockSession,
    SetGuestPresent,
)
from voice.native_playback import NativePlaybackManager


BOOT = "boot-handoff-1"
SESSION = "session-handoff-1"
CLIENT_HASH = "a" * 64
SCOPES = (
    "conversation",
    "identity.manage",
    "memory.read",
    "participants.manage",
)


def utc() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def principal() -> ServerPrincipal:
    now = time.time()
    return ServerPrincipal(
        runtime_boot_id=BOOT,
        session_id=SESSION,
        client_id_hash=CLIENT_HASH,
        capability_scopes=SCOPES,
        issued_at_epoch=now - 1,
        expires_at_epoch=now + 300,
        binding_source=PrincipalBindingSource.PACKAGED_DESKTOP,
    )


def factory(service: MemoryService) -> AccessContextFactory:
    return AccessContextFactory(
        MemoryServiceAccessView(service, timeout=5),
        runtime_boot_id=BOOT,
    )


def bootstrap(service: MemoryService, candidate: ServerPrincipal) -> None:
    context = factory(service).for_identity_management(SESSION, principal=candidate)
    command = BootstrapPrimary.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-bootstrap-handoff",
            "access_context": context.to_dict(),
            "display_name": "Primary user",
            "aliases": [],
            "explicit_confirmation": True,
            "idempotency_key": "bootstrap-handoff",
            "issued_at_utc": utc(),
        }
    )
    service.bootstrap_primary(command, candidate).result(timeout=10)


@pytest.fixture
def service(tmp_path: Path):
    value = MemoryService(
        tmp_path,
        runtime_boot_id=BOOT,
        javis_identity_id="identity-javis-handoff",
    ).start()
    try:
        yield value
    finally:
        value.shutdown()


def test_handoff_revokes_old_binding_and_mints_owner_attested_generation(
    service: MemoryService,
):
    candidate = principal()
    bootstrap(service, candidate)
    identity_context = factory(service).for_identity_management(
        SESSION, principal=candidate
    )
    create = CreateKnownPerson.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-create-handoff-target",
            "access_context": identity_context.to_dict(),
            "display_name": "Known person",
            "aliases": [],
            "explicit_confirmation": True,
            "idempotency_key": "create-handoff-target",
            "issued_at_utc": utc(),
        }
    )
    service.create_known_person(create, candidate).result(timeout=10)
    target_id = "subject-known-" + hashlib.sha256(
        b"subject-known\0command-create-handoff-target"
    ).hexdigest()[:32]
    before = factory(service).for_participant_management(SESSION, principal=candidate)
    old_binding_id = before.binding_id
    command = HandoffSession.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-handoff-known",
            "access_context": before.to_dict(),
            "target_subject_id": target_id,
            "lease_id": "lease-handoff-known",
            "expected_generation": before.session_generation,
            "explicit_confirmation": True,
            "idempotency_key": "handoff-known",
            "issued_at_utc": utc(),
        }
    )

    result = service.handoff_session(command, candidate).result(timeout=10)
    replay = service.handoff_session(command, candidate).result(timeout=10)
    after = factory(service).for_session(SESSION, principal=candidate)
    old_binding = service.get_subject_binding(old_binding_id).result(timeout=5)

    assert result["generation"] == before.session_generation + 1
    assert replay["generation"] == result["generation"]
    assert replay["replayed"] is True
    assert after.session_generation == result["generation"]
    assert after.actor_kind is ActorKind.KNOWN_PERSON
    assert after.actor_subject_id == target_id
    assert after.identity_assurance is IdentityAssurance.OWNER_ATTESTED
    assert old_binding.status.value == "revoked"
    assert before.acl_epoch != after.acl_epoch


def test_completed_lock_command_replays_after_binding_revocation(service: MemoryService):
    candidate = principal()
    bootstrap(service, candidate)
    before = factory(service).for_participant_management(SESSION, principal=candidate)
    command = LockSession.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-lock-replay",
            "access_context": before.to_dict(),
            "expected_generation": before.session_generation,
            "idempotency_key": "lock-replay",
            "issued_at_utc": utc(),
        }
    )

    first = service.lock_session(command, candidate).result(timeout=10)
    replay = service.lock_session(command, candidate).result(timeout=10)
    after = factory(service).for_session(SESSION, principal=candidate)

    assert first["generation"] == before.session_generation + 1
    assert replay["generation"] == first["generation"]
    assert replay["changed"] is False
    assert after.actor_kind is ActorKind.GUEST
    assert after.session_generation == first["generation"]


def test_barrier_failure_leaves_session_guest_fenced_until_exact_retry(
    service: MemoryService,
):
    candidate = principal()
    bootstrap(service, candidate)
    before = factory(service).for_participant_management(SESSION, principal=candidate)
    command = SetGuestPresent.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-failing-barrier",
            "access_context": before.to_dict(),
            "guest_present": True,
            "expected_generation": before.session_generation,
            "explicit_owner_confirmation": False,
            "idempotency_key": "failing-barrier",
            "issued_at_utc": utc(),
        }
    )
    service.register_session_barrier(
        "test", lambda *_args: (_ for _ in ()).throw(RuntimeError("cache failed"))
    )

    with pytest.raises(AccessBindingError, match="privacy_barrier_failed"):
        service.set_guest_present(command, candidate).result(timeout=10)
    fenced_state = service.get_session_generation(SESSION).result(timeout=5)
    fenced_context = factory(service).for_session(SESSION, principal=candidate)
    assert fenced_state.privacy_fenced is True
    assert fenced_state.owner_subject_id is None
    assert fenced_context.actor_kind is ActorKind.GUEST
    assert service.status()["privacy_barrier"] == {
        "state": "degraded",
        "fenced_session_count": 1,
    }

    service.register_session_barrier("test", lambda *_args: None)
    replay = service.set_guest_present(command, candidate).result(timeout=10)
    restored = factory(service).for_session(SESSION, principal=candidate)
    assert replay["generation"] == before.session_generation + 1
    assert restored.actor_kind is ActorKind.PRIMARY_USER
    assert restored.guest_present is True
    assert service.status()["privacy_barrier"] == {
        "state": "ready",
        "fenced_session_count": 0,
    }


def access_context(generation: int, actor: str) -> AccessContext:
    return AccessContext.from_dict(
        {
            "schema_version": 1,
            "context_id": f"context-{generation}-{actor}",
            "runtime_boot_id": BOOT,
            "client_id_hash": CLIENT_HASH,
            "capability_scopes": ["conversation"],
            "actor_subject_id": actor,
            "actor_kind": "primary_user",
            "session_id": SESSION,
            "participant_subject_ids": [actor, "subject-javis"],
            "audience_ceiling": "explicit_shared",
            "identity_assurance": "desktop_confirmed",
            "purpose": "conversation",
            "acl_epoch": generation,
            "issued_at_utc": "2026-09-09T08:00:00.000Z",
            "expires_at_utc": "2026-09-09T08:01:00.000Z",
            "session_generation": generation,
            "guest_present": False,
            "binding_id": f"binding-{generation}",
            "binding_assurance": "desktop_confirmed",
        }
    )


def test_conversation_fence_cancels_old_request_and_blocks_late_delta(
    tmp_path: Path,
):
    async def scenario() -> None:
        store = ConversationStore(tmp_path / "conversation.sqlite3")
        runs = AgentRunStore(tmp_path / "runs.sqlite3")
        hub = ConversationHub(store, runs)
        release = asyncio.Event()
        started = asyncio.Event()

        async def old_runner(_request, _token):
            yield {"type": "text_delta", "text": "before"}
            yield {
                "type": "tool_start",
                "tool": "private_tool",
                "params": {"subject": "old"},
            }
            yield {
                "type": "confirm_required",
                "tool": "private_tool",
                "reason": "old subject approval",
                "params": {"subject": "old"},
            }
            started.set()
            await release.wait()
            yield {
                "type": "tool_result",
                "tool": "private_tool",
                "success": True,
                "data": "private-late-tool-result",
            }
            yield {"type": "text_delta", "text": "private-late-delta"}
            yield {"type": "done"}

        try:
            await hub.submit(
                ConversationRequest(
                    SESSION,
                    "request-old-generation",
                    "old",
                    idempotency_key="old-generation",
                    access_context=access_context(1, "subject-old"),
                ),
                old_runner,
            )
            await started.wait()
            fence = asyncio.create_task(hub.fence_generation(SESSION, 1))
            await asyncio.sleep(0)
            release.set()
            await fence
            terminal = await hub.wait_for_terminal("request-old-generation")
            events = store.events_after(SESSION)
            assert terminal["type"] == "request.cancelled"
            assert not any(
                event["type"] == "response.delta"
                and event["payload"].get("text") == "private-late-delta"
                for event in events
            )
            assert not any(
                event["type"] == "activity.tool_completed"
                and event["payload"].get("data") == "private-late-tool-result"
                for event in events
            )

            with pytest.raises(ConversationStoreError, match="stale session generation"):
                await hub.submit(
                    ConversationRequest(
                        SESSION,
                        "request-stale-generation",
                        "stale",
                        idempotency_key="stale-generation",
                        access_context=access_context(1, "subject-old"),
                    ),
                    old_runner,
                )

            async def fresh_runner(_request, token):
                await token.checkpoint()
                yield {"type": "text_delta", "text": "fresh"}
                yield {"type": "done"}

            accepted = await hub.submit(
                ConversationRequest(
                    SESSION,
                    "request-new-generation",
                    "new",
                    idempotency_key="new-generation",
                    access_context=access_context(2, "subject-new"),
                ),
                fresh_runner,
            )
            assert accepted["accepted"] is True
            terminal = await hub.wait_for_terminal("request-new-generation")
            assert terminal["type"] == "request.completed"
        finally:
            await hub.shutdown()
            runs.close()

    asyncio.run(scenario())


def test_history_projection_never_crosses_session_generation(tmp_path: Path):
    store = ConversationStore(tmp_path / "conversation-history.sqlite3")
    for generation, actor in ((1, "subject-old"), (2, "subject-new")):
        context = access_context(generation, actor)
        request_id = f"request-history-{generation}"
        store.accept_request(
            SESSION,
            request_id,
            request_id,
            f"user-{generation}",
            {"access_projection": safe_access_projection(context)},
        )
        store.append_message(
            SESSION,
            request_id,
            "assistant",
            f"assistant-{generation}",
        )
    history = store.history_for_access(access_context(2, "subject-new"))
    assert [item["content"] for item in history] == ["user-2", "assistant-2"]


def wav_tone(duration_ms: int = 500, rate: int = 16_000) -> bytes:
    samples = [
        int(3000 * math.sin(2 * math.pi * 220 * index / rate))
        for index in range(rate * duration_ms // 1000)
    ]
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


class FakePlayer:
    def __init__(self):
        self.played = 0
        self.stopped = 0

    def play(self, _path):
        self.played += 1

    def stop(self):
        self.stopped += 1


class FakeVoiceService:
    def ingest_playback_pcm(self, _pcm: bytes, _source_rate: int):
        return None


def test_native_playback_rejects_fenced_session_generation():
    player = FakePlayer()
    manager = NativePlaybackManager(service=FakeVoiceService(), player=player)
    first = manager.play_wav(
        wav_tone(),
        session_id=SESSION,
        request_id="request-playback-old",
        session_generation=1,
    )
    fenced = manager.fence_session(SESSION, 1)
    stale = manager.play_wav(
        wav_tone(40),
        session_id=SESSION,
        request_id="request-playback-stale",
        session_generation=1,
    )
    fresh = manager.play_wav(
        wav_tone(40),
        session_id=SESSION,
        request_id="request-playback-fresh",
        session_generation=2,
    )
    manager.stop()

    assert first["ok"] is True
    assert fenced["stopped"] is True
    assert stale["ok"] is False
    assert stale["reason_code"] == "stale_session_generation"
    assert fresh["ok"] is True
    assert fresh["session_generation"] == 2
