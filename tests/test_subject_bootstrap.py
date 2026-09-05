import hashlib
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
from core.life.memory.contracts import AccessPurpose, ActorKind, Subject
from core.life.memory.service import MemoryService
from core.life.memory.subjects import (
    BindSession,
    BootstrapPrimary,
    CreateKnownPerson,
    DisableSubject,
    LockSession,
)
from core.runtime_access import RuntimeAccessAuthority


BOOT_1 = "boot-subjects-1"
BOOT_2 = "boot-subjects-2"
IDENTITY_ID = "identity-javis-stable"
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
    scopes: tuple[str, ...] = SCOPES,
    source: PrincipalBindingSource = PrincipalBindingSource.PACKAGED_DESKTOP,
    issued_at: float | None = None,
    expires_at: float | None = None,
) -> ServerPrincipal:
    now = time.time()
    return ServerPrincipal(
        runtime_boot_id=boot,
        session_id="session-1",
        client_id_hash=client_hash,
        capability_scopes=scopes,
        issued_at_epoch=now - 1 if issued_at is None else issued_at,
        expires_at_epoch=now + 300 if expires_at is None else expires_at,
        binding_source=source,
    )


def access_factory(service: MemoryService, boot: str = BOOT_1, **kwargs):
    return AccessContextFactory(
        MemoryServiceAccessView(service, timeout=5),
        runtime_boot_id=boot,
        **kwargs,
    )


def bootstrap_command(
    context,
    *,
    command_id: str = "command-bootstrap-1",
    display_name: str = "Primary user",
):
    return BootstrapPrimary.from_dict(
        {
            "schema_version": 1,
            "command_id": command_id,
            "access_context": context.to_dict(),
            "display_name": display_name,
            "aliases": ["Owner"],
            "explicit_confirmation": True,
            "idempotency_key": command_id,
            "issued_at_utc": utc(),
        }
    )


def bootstrap(service: MemoryService, candidate: ServerPrincipal | None = None):
    candidate = candidate or principal()
    context = access_factory(service, candidate.runtime_boot_id).for_identity_management(
        "session-1", principal=candidate
    )
    command = bootstrap_command(context)
    return service.bootstrap_primary(command, candidate).result(timeout=10), command


def stable_subject_id(prefix: str, command_id: str) -> str:
    digest = hashlib.sha256(f"{prefix}\0{command_id}".encode()).hexdigest()
    return f"{prefix}-{digest[:32]}"


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


def test_startup_links_javis_subject_to_l0_identity(service: MemoryService):
    javis = service.get_subject("subject-javis").result(timeout=5)

    assert javis is not None
    assert javis.subject_kind.value == "javis"
    assert javis.credential_reference_hash == hashlib.sha256(
        IDENTITY_ID.encode()
    ).hexdigest()


def test_explicit_bootstrap_is_idempotent_and_unlocks_memory_context(service: MemoryService):
    first, command = bootstrap(service)
    replay, _ = bootstrap(service)

    assert first["state"] == "bound"
    assert first["replayed"] is False
    assert replay["replayed"] is True
    primary = service.active_primary_subject().result(timeout=5)
    assert primary is not None
    assert primary.subject_id == stable_subject_id("subject-primary", command.command_id)
    assert primary.identity_assurance.value == "desktop_confirmed"

    context = access_factory(service).for_session(
        "session-1",
        principal=principal(),
        purpose=AccessPurpose.RECALL,
    )
    assert context.actor_kind is ActorKind.PRIMARY_USER
    assert context.guest_present is False
    assert context.binding_id is not None
    assert context.binding_assurance.value == "desktop_confirmed"


def test_untrusted_principal_wrong_scope_and_client_injection_are_rejected(
    service: MemoryService,
):
    valid = principal()
    context = access_factory(service).for_identity_management(
        "session-1", principal=valid
    )
    command = bootstrap_command(context)

    with pytest.raises(AccessBindingError, match="packaged_desktop_required"):
        service.bootstrap_primary(
            command,
            principal(source=PrincipalBindingSource.LOOPBACK_WEB),
        ).result(timeout=5)
    with pytest.raises(AccessBindingError, match="scope_denied"):
        service.bootstrap_primary(
            command,
            principal(scopes=("memory.read",)),
        ).result(timeout=5)

    forged = command.to_dict()
    forged["subject_id"] = "subject-attacker"
    with pytest.raises(ValueError, match="unexpected field"):
        BootstrapPrimary.from_dict(forged)


def test_runtime_authority_requires_packaged_origin_and_identity_scope():
    authority = RuntimeAccessAuthority(BOOT_1)
    issued = authority.issue("desktop-main", ("identity.manage",))

    denied_origin = authority.validate(
        issued.token,
        scope="identity.manage",
        origin="http://localhost:5173",
        peer_host="127.0.0.1",
    )
    denied_scope = authority.validate(
        issued.token,
        scope="participants.manage",
        origin="http://tauri.localhost",
        peer_host="127.0.0.1",
    )
    allowed = authority.validate(
        issued.token,
        scope="identity.manage",
        origin="http://tauri.localhost",
        peer_host="127.0.0.1",
    )

    assert denied_origin.reason_code == "origin_denied"
    assert denied_scope.reason_code == "scope_denied"
    assert allowed.allowed is True
    assert allowed.principal is not None
    assert allowed.principal.binding_source == "packaged_desktop"


def test_second_primary_is_rejected_but_alias_collision_does_not_merge(
    service: MemoryService,
):
    bootstrap(service)
    bound_context = access_factory(service).for_identity_management(
        "session-1", principal=principal()
    )

    second = bootstrap_command(
        bound_context,
        command_id="command-bootstrap-2",
        display_name="Second primary",
    )
    with pytest.raises(AccessBindingError, match="primary_already_exists"):
        service.bootstrap_primary(second, principal()).result(timeout=5)

    created_ids = []
    for suffix in ("one", "two"):
        command_id = f"command-known-{suffix}"
        command = CreateKnownPerson.from_dict(
            {
                "schema_version": 1,
                "command_id": command_id,
                "access_context": bound_context.to_dict(),
                "display_name": "Alex",
                "aliases": ["Shared alias"],
                "explicit_confirmation": True,
                "idempotency_key": command_id,
                "issued_at_utc": utc(),
            }
        )
        service.create_known_person(command, principal()).result(timeout=5)
        created_ids.append(stable_subject_id("subject-known", command_id))

    assert created_ids[0] != created_ids[1]
    assert all(service.get_subject(value).result(timeout=5) for value in created_ids)
    assert all(
        service.get_subject(value).result(timeout=5).identity_assurance.value == "guest"
        for value in created_ids
    )


def test_lock_revokes_binding_and_disabled_subject_cannot_rebind(service: MemoryService):
    _, _ = bootstrap(service)
    primary = service.active_primary_subject().result(timeout=5)
    assert primary is not None
    bound_context = access_factory(service).for_identity_management(
        "session-1", principal=principal()
    )
    disable = DisableSubject.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-disable-primary",
            "access_context": bound_context.to_dict(),
            "target_subject_id": primary.subject_id,
            "expected_revision": primary.revision,
            "explicit_confirmation": True,
            "idempotency_key": "disable-primary-1",
            "issued_at_utc": utc(),
        }
    )
    service.disable_subject(disable, principal()).result(timeout=5)

    assert access_factory(service).for_session(
        "session-1", principal=principal()
    ).actor_kind is ActorKind.GUEST
    unbound_context = access_factory(service).for_participant_management(
        "session-1", principal=principal()
    )
    rebind = BindSession.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-rebind-disabled",
            "access_context": unbound_context.to_dict(),
            "target_subject_id": primary.subject_id,
            "expected_generation": 0,
            "explicit_confirmation": True,
            "idempotency_key": "rebind-disabled-1",
            "issued_at_utc": utc(),
        }
    )
    with pytest.raises(AccessBindingError, match="primary_subject_unavailable"):
        service.rebind_primary(rebind, principal()).result(timeout=5)


def test_lock_is_explicit_and_immediately_fails_closed(service: MemoryService):
    bootstrap(service)
    context = access_factory(service).for_participant_management(
        "session-1", principal=principal()
    )
    command = LockSession.from_dict(
        {
            "schema_version": 1,
            "command_id": "command-lock-1",
            "access_context": context.to_dict(),
            "expected_generation": 0,
            "idempotency_key": "lock-1",
            "issued_at_utc": utc(),
        }
    )
    result = service.lock_session(command, principal()).result(timeout=5)

    assert result["changed"] is True
    assert access_factory(service).for_session(
        "session-1", principal=principal()
    ).actor_kind is ActorKind.GUEST


def test_restart_revokes_old_boot_then_explicit_rebind_restores_primary(tmp_path: Path):
    first = MemoryService(
        tmp_path,
        runtime_boot_id=BOOT_1,
        javis_identity_id=IDENTITY_ID,
    ).start()
    _, _ = bootstrap(first)
    primary = first.active_primary_subject().result(timeout=5)
    old_binding = first.active_subject_binding(BOOT_1, HASH_A).result(timeout=5)
    assert primary is not None and old_binding is not None
    first.shutdown()

    second = MemoryService(
        tmp_path,
        runtime_boot_id=BOOT_2,
        javis_identity_id=IDENTITY_ID,
    ).start()
    try:
        assert second.active_subject_binding(BOOT_1, HASH_A).result(timeout=5) is None
        retired = second.get_subject_binding(old_binding.binding_id).result(timeout=5)
        assert retired is not None and retired.status.value == "revoked"

        next_principal = principal(boot=BOOT_2)
        assert access_factory(second, BOOT_2).for_session(
            "session-1", principal=next_principal
        ).actor_kind is ActorKind.GUEST
        command_context = access_factory(second, BOOT_2).for_participant_management(
            "session-1", principal=next_principal
        )
        command = BindSession.from_dict(
            {
                "schema_version": 1,
                "command_id": "command-rebind-boot-2",
                "access_context": command_context.to_dict(),
                "target_subject_id": primary.subject_id,
                "expected_generation": 0,
                "explicit_confirmation": True,
                "idempotency_key": "rebind-boot-2",
                "issued_at_utc": utc(),
            }
        )
        second.rebind_primary(command, next_principal).result(timeout=5)
        assert access_factory(second, BOOT_2).for_session(
            "session-1", principal=next_principal
        ).actor_kind is ActorKind.PRIMARY_USER
    finally:
        second.shutdown()


def test_expired_binding_and_unavailable_store_are_guest(service: MemoryService):
    now = time.time()
    short = principal(issued_at=now - 1, expires_at=now + 1)
    bootstrap(service, short)
    expired_factory = access_factory(service, now=lambda: now + 2)

    assert expired_factory.for_session(
        "session-1", principal=short
    ).actor_kind is ActorKind.GUEST
    assert AccessContextFactory(None, runtime_boot_id=BOOT_1).for_session(
        "session-1", principal=principal()
    ).actor_kind is ActorKind.GUEST


def test_human_verified_assurance_has_no_contract_path():
    wire = {
        "schema_version": 1,
        "subject_id": "subject-human",
        "revision": 1,
        "subject_kind": "known_person",
        "display_name": "Human",
        "status": "active",
        "identity_assurance": "verified",
        "credential_reference_hash": None,
        "merged_into_subject_id": None,
        "session_scope_id": None,
        "created_at_utc": utc(),
        "updated_at_utc": utc(),
        "aliases": [],
        "created_by_subject_id": None,
        "assurance_ceiling": "verified",
        "privacy_class": "user_private",
    }
    with pytest.raises(ValueError, match="verified assurance"):
        Subject.from_dict(wire)
