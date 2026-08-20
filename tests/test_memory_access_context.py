from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.life.memory.access import (
    AccessBindingError,
    AccessContextFactory,
    MemorySubjectBinder,
    PrincipalBindingSource,
    ReservedAccessFieldError,
    ServerPrincipal,
    reject_reserved_client_access_fields,
    safe_access_projection,
)
from core.life.memory.contracts import (
    AccessPurpose,
    ActorKind,
    Audience,
    IdentityAssurance,
)
from core.life.memory.store import MemoryStore


NOW = 1_800_000_000.0
HASH_A = "a" * 64
HASH_B = "b" * 64


def principal(
    *,
    session_id: str = "session-1",
    runtime_boot_id: str = "boot-1",
    client_id_hash: str = HASH_A,
    scopes: tuple[str, ...] = (
        "conversation",
        "memory.delete",
        "memory.manage",
        "memory.migrate",
        "memory.read",
    ),
    issued_at: float = NOW - 10,
    expires_at: float = NOW + 300,
    binding_source: PrincipalBindingSource = PrincipalBindingSource.PACKAGED_DESKTOP,
) -> ServerPrincipal:
    return ServerPrincipal(
        runtime_boot_id=runtime_boot_id,
        session_id=session_id,
        client_id_hash=client_id_hash,
        capability_scopes=scopes,
        issued_at_epoch=issued_at,
        expires_at_epoch=expires_at,
        binding_source=binding_source,
    )


@pytest.fixture
def store_and_binder(tmp_path: Path):
    writer_token = object()
    store = MemoryStore(tmp_path, writer_token=writer_token)
    binder = MemorySubjectBinder(
        store,
        writer_token=writer_token,
        runtime_boot_id="boot-1",
        now=lambda: NOW,
    )
    try:
        yield store, binder
    finally:
        store.close()


def factory(store, *, boot: str = "boot-1") -> AccessContextFactory:
    return AccessContextFactory(
        store,
        runtime_boot_id=boot,
        now=lambda: NOW,
        context_id_factory=lambda: "context-fixed",
    )


def test_server_principal_is_frozen_bounded_and_bearer_free():
    value = principal(scopes=("memory.read", "conversation", "memory.read"))

    assert value.capability_scopes == ("conversation", "memory.read")
    with pytest.raises(FrozenInstanceError):
        value.session_id = "session-2"

    projection = value.safe_projection()
    encoded = json.dumps(projection, sort_keys=True)
    assert projection["client_id_hash"] == HASH_A
    assert "token" not in encoded.casefold()
    assert "nonce" not in encoded.casefold()
    assert "client_instance_id" not in encoded


@pytest.mark.parametrize(
    "kwargs",
    [
        {"client_id_hash": "short"},
        {"scopes": ()},
        {"issued_at": NOW, "expires_at": NOW},
        {"session_id": "session\nforged"},
    ],
)
def test_server_principal_rejects_unbounded_or_invalid_authority(kwargs):
    with pytest.raises(ValueError):
        principal(**kwargs)


def test_packaged_desktop_binding_creates_javis_primary_and_active_session_pair(
    store_and_binder,
):
    store, binder = store_and_binder

    binding = binder.bind_primary_user("session-1", principal())
    repeated = binder.bind_primary_user("session-1", principal())

    assert repeated == binding
    assert binding.primary_subject.credential_reference_hash == HASH_A
    assert binding.primary_subject.identity_assurance is IdentityAssurance.DESKTOP_CONFIRMED
    assert binding.javis_subject.subject_id == "subject-javis"
    participants = store.active_session_participants("session-1")
    assert {item.subject_id for item in participants} == {
        binding.primary_subject.subject_id,
        "subject-javis",
    }
    assert len(participants) == 2


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (PrincipalBindingSource.DEVELOPMENT_WEB, "packaged_desktop_required"),
        (PrincipalBindingSource.LOOPBACK_WEB, "packaged_desktop_required"),
    ],
)
def test_arbitrary_local_web_surfaces_cannot_bind_primary_user(
    store_and_binder, source, reason
):
    store, binder = store_and_binder

    with pytest.raises(AccessBindingError, match=reason):
        binder.bind_primary_user("session-1", principal(binding_source=source))

    assert store.active_session_participants("session-1") == ()


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (principal(session_id="session-2"), "session_mismatch"),
        (principal(runtime_boot_id="boot-old"), "runtime_boot_mismatch"),
        (principal(expires_at=NOW - 1, issued_at=NOW - 10), "principal_expired"),
        (principal(scopes=("memory.read",)), "scope_denied"),
    ],
)
def test_explicit_binding_rejects_session_boot_expiry_and_scope_mismatch(
    store_and_binder, candidate, reason
):
    _, binder = store_and_binder

    with pytest.raises(AccessBindingError, match=reason):
        binder.bind_primary_user("session-1", candidate)


def test_bound_primary_session_generates_bounded_server_context(store_and_binder):
    store, binder = store_and_binder
    binding = binder.bind_primary_user("session-1", principal())

    context = factory(store).for_session(
        "session-1", principal=principal(), purpose=AccessPurpose.RECALL
    )

    assert context.actor_kind is ActorKind.PRIMARY_USER
    assert context.actor_subject_id == binding.primary_subject.subject_id
    assert context.session_id == "session-1"
    assert context.participant_subject_ids == tuple(
        sorted((binding.primary_subject.subject_id, "subject-javis"))
    )
    assert context.audience_ceiling is Audience.EXPLICIT_SHARED
    assert context.identity_assurance is IdentityAssurance.DESKTOP_CONFIRMED
    assert context.purpose is AccessPurpose.RECALL
    assert context.acl_epoch == store.metadata()["acl_epoch"]
    assert context.expires_at_utc == "2027-01-15T08:01:00.000Z"


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        principal(session_id="session-2"),
        principal(runtime_boot_id="boot-old"),
        principal(expires_at=NOW - 1, issued_at=NOW - 10),
        principal(binding_source=PrincipalBindingSource.LOOPBACK_WEB),
    ],
)
def test_unknown_expired_cross_session_or_nonpackaged_principal_is_guest(
    store_and_binder, candidate
):
    store, binder = store_and_binder
    binder.bind_primary_user("session-1", principal())

    context = factory(store).for_session("session-1", principal=candidate)

    assert context.actor_kind is ActorKind.GUEST
    assert context.audience_ceiling is Audience.GUEST
    assert context.identity_assurance is IdentityAssurance.GUEST
    assert context.capability_scopes == ()
    assert context.actor_subject_id.startswith("subject-guest-")
    assert context.participant_subject_ids == (context.actor_subject_id,)


@pytest.mark.parametrize(
    ("purpose", "scopes"),
    [
        (AccessPurpose.RECALL, ("conversation",)),
        (AccessPurpose.MANAGE, ("conversation", "memory.read")),
        (AccessPurpose.DELETE, ("conversation", "memory.manage")),
        (AccessPurpose.MIGRATION, ("conversation", "memory.delete")),
    ],
)
def test_purpose_requires_its_exact_capability_scope(store_and_binder, purpose, scopes):
    store, binder = store_and_binder
    binder.bind_primary_user("session-1", principal())

    context = factory(store).for_session(
        "session-1", principal=principal(scopes=scopes), purpose=purpose
    )

    assert context.actor_kind is ActorKind.GUEST
    assert context.audience_ceiling is Audience.GUEST


def test_session_without_active_binding_and_store_failure_both_fail_closed(store_and_binder):
    store, _ = store_and_binder
    unbound = factory(store).for_session("session-1", principal=principal())
    unavailable = factory(None).for_session("session-1", principal=principal())

    assert unbound.actor_kind is ActorKind.GUEST
    assert unavailable.actor_kind is ActorKind.GUEST
    assert unavailable.acl_epoch == 0


def test_degraded_store_cannot_reuse_stale_primary_binding(store_and_binder):
    store, binder = store_and_binder
    binder.bind_primary_user("session-1", principal())

    class DegradedStore:
        def status(self):
            return {"state": "degraded"}

        def metadata(self):
            return store.metadata()

        def get_subject(self, subject_id):
            return store.get_subject(subject_id)

        def active_session_participants(self, session_id):
            return store.active_session_participants(session_id)

    context = factory(DegradedStore()).for_session("session-1", principal=principal())

    assert context.actor_kind is ActorKind.GUEST
    assert context.acl_epoch == 0


def test_different_client_hash_cannot_claim_an_existing_primary_binding(store_and_binder):
    store, binder = store_and_binder
    binder.bind_primary_user("session-1", principal())

    context = factory(store).for_session(
        "session-1", principal=principal(client_id_hash=HASH_B)
    )

    assert context.actor_kind is ActorKind.GUEST
    assert context.actor_subject_id != "subject-primary-" + HASH_A[:32]


@pytest.mark.parametrize(
    "field_name",
    [
        "owner",
        "owner_subject_id",
        "subject",
        "subject_id",
        "actor_subject_id",
        "audience",
        "audience_ceiling",
        "participant_subject_ids",
        "access_context",
    ],
)
def test_client_cannot_supply_owner_audience_subject_or_access_context(field_name):
    with pytest.raises(ReservedAccessFieldError) as captured:
        reject_reserved_client_access_fields(
            {"session_id": "session-1", "text": "hello", field_name: "forged"}
        )

    assert captured.value.reason_code == "reserved_access_field"
    assert captured.value.field_name == field_name


def test_client_message_text_is_not_mistaken_for_reserved_envelope_fields():
    reject_reserved_client_access_fields(
        {
            "session_id": "session-1",
            "text": "Please explain owner, audience and subject fields.",
            "metadata": {"owner": "ordinary nested application data"},
        }
    )


def test_access_context_factory_has_no_owner_audience_or_subject_override_parameters(
    store_and_binder,
):
    store, binder = store_and_binder
    binder.bind_primary_user("session-1", principal())

    with pytest.raises(TypeError):
        factory(store).for_session(
            "session-1",
            principal=principal(),
            owner_subject_id="subject-attacker",
        )


def test_safe_access_projection_contains_identity_snapshot_but_no_secret(
    store_and_binder,
):
    store, binder = store_and_binder
    binder.bind_primary_user("session-1", principal())
    context = factory(store).for_session("session-1", principal=principal())

    projection = safe_access_projection(context)
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["context_id"] == "context-fixed"
    assert projection["actor_kind"] == "primary_user"
    assert projection["participant_subject_ids"] == list(context.participant_subject_ids)
    assert "token" not in encoded.casefold()
    assert "nonce" not in encoded.casefold()
    assert "client_instance_id" not in encoded


def test_unknown_purpose_is_rejected_instead_of_guessed(store_and_binder):
    store, _ = store_and_binder

    with pytest.raises(ValueError, match="purpose"):
        factory(store).for_session("session-1", principal=None, purpose="admin")
