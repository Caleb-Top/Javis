from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.life.memory.access import (
    AccessBindingError,
    AccessContextFactory,
    MemoryServiceAccessView,
    PrincipalBindingSource,
    ServerPrincipal,
)
from core.life.memory.contracts import ActorKind, Audience, ParticipantRole
from core.life.memory.service import MemoryService
from core.life.memory.subjects import BootstrapPrimary, SetGuestPresent


BOOT_1 = "boot-participants-1"
BOOT_2 = "boot-participants-2"
IDENTITY_ID = "identity-javis-participants"
SESSION_ID = "session-participants-1"
HASH_A = "a" * 64
HASH_B = "b" * 64
SCOPES = (
    "conversation",
    "identity.manage",
    "memory.read",
    "participants.manage",
)


def utc(epoch: float | None = None) -> str:
    value = datetime.fromtimestamp(epoch or time.time(), timezone.utc)
    milliseconds = value.microsecond // 1000
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def principal(
    *,
    boot: str = BOOT_1,
    client_hash: str = HASH_A,
) -> ServerPrincipal:
    now = time.time()
    return ServerPrincipal(
        runtime_boot_id=boot,
        session_id=SESSION_ID,
        client_id_hash=client_hash,
        capability_scopes=SCOPES,
        issued_at_epoch=now - 1,
        expires_at_epoch=now + 300,
        binding_source=PrincipalBindingSource.PACKAGED_DESKTOP,
    )


def access_factory(service: MemoryService, *, boot: str = BOOT_1) -> AccessContextFactory:
    return AccessContextFactory(
        MemoryServiceAccessView(service, timeout=5),
        runtime_boot_id=boot,
    )


def bootstrap_primary(
    service: MemoryService,
    candidate: ServerPrincipal,
    *,
    command_id: str = "command-bootstrap-participants",
):
    context = access_factory(
        service, boot=candidate.runtime_boot_id
    ).for_identity_management(SESSION_ID, principal=candidate)
    command = BootstrapPrimary.from_dict(
        {
            "schema_version": 1,
            "command_id": command_id,
            "access_context": context.to_dict(),
            "display_name": "Primary user",
            "aliases": [],
            "explicit_confirmation": True,
            "idempotency_key": command_id,
            "issued_at_utc": utc(),
        }
    )
    result = service.bootstrap_primary(command, candidate).result(timeout=10)
    return result, command


def guest_present_command(
    context,
    *,
    guest_present: bool,
    command_id: str,
    idempotency_key: str | None = None,
    explicit_owner_confirmation: bool = False,
):
    return SetGuestPresent.from_dict(
        {
            "schema_version": 1,
            "command_id": command_id,
            "access_context": context.to_dict(),
            "guest_present": guest_present,
            "expected_generation": context.session_generation,
            "explicit_owner_confirmation": explicit_owner_confirmation,
            "idempotency_key": idempotency_key or command_id,
            "issued_at_utc": utc(),
        }
    )


@pytest.fixture
def service(tmp_path: Path):
    value = MemoryService(
        tmp_path,
        runtime_boot_id=BOOT_1,
        javis_identity_id=IDENTITY_ID,
    ).start()
    try:
        yield value
    finally:
        value.shutdown()


def test_unbound_session_materializes_initial_guest_generation(service: MemoryService):
    context = access_factory(service).for_session(
        SESSION_ID,
        principal=principal(),
    )

    assert context.session_generation == 0
    assert context.actor_kind is ActorKind.GUEST
    assert context.guest_present is True
    assert context.audience_ceiling is Audience.GUEST
    assert context.binding_id is None

    participants = service.active_session_participants(SESSION_ID).result(timeout=5)
    assert len(participants) == 1
    guest = participants[0]
    assert guest.subject_id == context.actor_subject_id
    assert guest.participant_role is ParticipantRole.GUEST
    assert guest.session_generation == context.session_generation
    assert guest.server_binding_source == "guest_default"
    assert guest.active is True

    subject = service.get_subject(guest.subject_id).result(timeout=5)
    assert subject is not None
    assert subject.subject_kind.value == "session_guest"
    assert subject.session_scope_id == SESSION_ID


def test_explicit_primary_bind_replaces_guest_with_frozen_participant_snapshot(
    service: MemoryService,
):
    candidate = principal()
    before = access_factory(service).for_session(SESSION_ID, principal=candidate)
    guest_snapshot = before.participant_subject_ids

    bootstrap_primary(service, candidate)

    after = access_factory(service).for_session(SESSION_ID, principal=candidate)
    primary = service.active_primary_subject().result(timeout=5)
    participants = service.active_session_participants(SESSION_ID).result(timeout=5)

    assert primary is not None
    assert after.session_generation == before.session_generation + 1
    assert after.actor_subject_id == primary.subject_id
    assert after.actor_kind is ActorKind.PRIMARY_USER
    assert after.guest_present is False
    assert after.participant_subject_ids == tuple(
        sorted((primary.subject_id, "subject-javis"))
    )
    assert {item.subject_id for item in participants} == set(
        after.participant_subject_ids
    )
    assert all(item.session_generation == after.session_generation for item in participants)
    assert guest_snapshot == before.participant_subject_ids
    assert not set(guest_snapshot) & set(after.participant_subject_ids)


def test_guest_present_fences_owner_memory_and_only_confirmed_owner_can_clear(
    service: MemoryService,
):
    owner_principal = principal()
    bootstrap_primary(service, owner_principal)
    factory = access_factory(service)
    owner_before = factory.for_participant_management(
        SESSION_ID, principal=owner_principal
    )

    enable = guest_present_command(
        owner_before,
        guest_present=True,
        command_id="command-guest-present-enable",
    )
    service.set_guest_present(enable, owner_principal).result(timeout=10)

    fenced = factory.for_session(SESSION_ID, principal=owner_principal)
    assert fenced.session_generation == owner_before.session_generation + 1
    assert fenced.actor_kind is ActorKind.PRIMARY_USER
    assert fenced.actor_subject_id == owner_before.actor_subject_id
    assert fenced.guest_present is True
    assert fenced.audience_ceiling is Audience.GUEST

    unconfirmed = {
        **guest_present_command(
            factory.for_participant_management(SESSION_ID, principal=owner_principal),
            guest_present=True,
            command_id="command-guest-present-unconfirmed-template",
        ).to_dict(),
        "guest_present": False,
    }
    with pytest.raises(ValueError, match="confirmation"):
        SetGuestPresent.from_dict(unconfirmed)

    guest_principal = principal(client_hash=HASH_B)
    guest_context = factory.for_participant_management(
        SESSION_ID, principal=guest_principal
    )
    guest_clear = guest_present_command(
        guest_context,
        guest_present=False,
        command_id="command-guest-present-guest-clear",
        explicit_owner_confirmation=True,
    )
    with pytest.raises(AccessBindingError, match="owner"):
        service.set_guest_present(guest_clear, guest_principal).result(timeout=10)

    owner_context = factory.for_participant_management(
        SESSION_ID, principal=owner_principal
    )
    clear = guest_present_command(
        owner_context,
        guest_present=False,
        command_id="command-guest-present-clear",
        explicit_owner_confirmation=True,
    )
    service.set_guest_present(clear, owner_principal).result(timeout=10)

    restored = factory.for_session(SESSION_ID, principal=owner_principal)
    assert restored.session_generation == fenced.session_generation + 1
    assert restored.guest_present is False
    assert restored.audience_ceiling is Audience.EXPLICIT_SHARED


def test_restart_fences_prior_owner_and_starts_next_guest_generation(tmp_path: Path):
    first = MemoryService(
        tmp_path,
        runtime_boot_id=BOOT_1,
        javis_identity_id=IDENTITY_ID,
    ).start()
    first_principal = principal()
    bootstrap_primary(first, first_principal)
    owner_context = access_factory(first).for_session(
        SESSION_ID, principal=first_principal
    )
    primary = first.active_primary_subject().result(timeout=5)
    assert primary is not None
    first.shutdown()

    second = MemoryService(
        tmp_path,
        runtime_boot_id=BOOT_2,
        javis_identity_id=IDENTITY_ID,
    ).start()
    try:
        second_principal = principal(boot=BOOT_2)
        restarted = access_factory(second, boot=BOOT_2).for_session(
            SESSION_ID, principal=second_principal
        )
        participants = second.active_session_participants(SESSION_ID).result(timeout=5)

        assert restarted.session_generation == owner_context.session_generation + 1
        assert restarted.actor_kind is ActorKind.GUEST
        assert restarted.guest_present is True
        assert restarted.audience_ceiling is Audience.GUEST
        assert primary.subject_id not in restarted.participant_subject_ids
        assert {item.subject_id for item in participants} == set(
            restarted.participant_subject_ids
        )
        assert all(item.participant_role is ParticipantRole.GUEST for item in participants)
        assert all(
            item.session_generation == restarted.session_generation
            for item in participants
        )
    finally:
        second.shutdown()


def test_generation_compare_and_swap_allows_only_one_transition(
    service: MemoryService,
):
    owner_principal = principal()
    bootstrap_primary(service, owner_principal)
    factory = access_factory(service)
    expected = factory.for_participant_management(
        SESSION_ID, principal=owner_principal
    )
    winner = guest_present_command(
        expected,
        guest_present=True,
        command_id="command-generation-winner",
    )
    loser = guest_present_command(
        expected,
        guest_present=True,
        command_id="command-generation-loser",
    )

    service.set_guest_present(winner, owner_principal).result(timeout=10)
    with pytest.raises(AccessBindingError, match="stale_session_generation"):
        service.set_guest_present(loser, owner_principal).result(timeout=10)

    current = factory.for_participant_management(
        SESSION_ID, principal=owner_principal
    )
    assert current.session_generation == expected.session_generation + 1


def test_exact_replay_precedes_stale_check_but_key_reuse_conflicts(
    service: MemoryService,
):
    owner_principal = principal()
    bootstrap_primary(service, owner_principal)
    factory = access_factory(service)
    original_context = factory.for_participant_management(
        SESSION_ID, principal=owner_principal
    )
    original = guest_present_command(
        original_context,
        guest_present=True,
        command_id="command-idempotent-toggle",
        idempotency_key="idempotent-toggle",
    )

    first = service.set_guest_present(original, owner_principal).result(timeout=10)
    replay = service.set_guest_present(original, owner_principal).result(timeout=10)
    after_replay = factory.for_participant_management(
        SESSION_ID, principal=owner_principal
    )

    assert first["command_id"] == original.command_id
    assert replay["command_id"] == original.command_id
    assert after_replay.session_generation == original_context.session_generation + 1

    changed_payload = guest_present_command(
        after_replay,
        guest_present=False,
        command_id=original.command_id,
        idempotency_key=original.idempotency_key,
        explicit_owner_confirmation=True,
    )
    with pytest.raises(AccessBindingError, match="idempotency"):
        service.set_guest_present(changed_payload, owner_principal).result(timeout=10)

    stale = guest_present_command(
        original_context,
        guest_present=True,
        command_id="command-stale-toggle",
    )
    with pytest.raises(AccessBindingError, match="stale_session_generation"):
        service.set_guest_present(stale, owner_principal).result(timeout=10)
