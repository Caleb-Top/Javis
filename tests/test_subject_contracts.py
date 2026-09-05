import copy
import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from core.life.memory.contracts import (
    AccessContext,
    CommunicationGuidance,
    HandoffLease,
    RelationshipView,
    SessionGenerationState,
    SessionParticipant,
    Subject,
    SubjectBinding,
)
from core.life.memory.store import MemoryStore, MemoryStoreConflictError
from core.life.memory.subjects import BootstrapPrimary, SetGuestPresent


NOW = "2026-08-20T10:00:00.000Z"
LATER = "2026-08-20T10:05:00.000Z"
LATEST = "2026-08-20T10:10:00.000Z"
HASH_A = "a" * 64
HASH_B = "b" * 64


def access_wire(*scopes: str) -> dict:
    return {
        "schema_version": 1,
        "context_id": "context-subjects",
        "runtime_boot_id": "boot-1",
        "client_id_hash": HASH_A,
        "capability_scopes": list(scopes),
        "actor_subject_id": "subject-primary",
        "actor_kind": "primary_user",
        "session_id": "session-1",
        "participant_subject_ids": ["subject-primary", "subject-javis"],
        "audience_ceiling": "owner_private",
        "identity_assurance": "desktop_confirmed",
        "purpose": "manage",
        "acl_epoch": 1,
        "issued_at_utc": NOW,
        "expires_at_utc": LATEST,
    }


def subject_wire(subject_id: str, kind: str, *, display_name: str) -> dict:
    return {
        "schema_version": 1,
        "subject_id": subject_id,
        "revision": 1,
        "subject_kind": kind,
        "display_name": display_name,
        "status": "active",
        "identity_assurance": (
            "desktop_confirmed" if kind == "primary_user" else "guest"
        ),
        "credential_reference_hash": None,
        "merged_into_subject_id": None,
        "session_scope_id": None,
        "created_at_utc": NOW,
        "updated_at_utc": NOW,
        "aliases": [],
        "created_by_subject_id": None,
        "assurance_ceiling": (
            "desktop_confirmed" if kind == "primary_user" else "guest"
        ),
        "privacy_class": "user_private",
    }


def binding_wire(
    binding_id: str = "binding-1",
    *,
    subject_id: str = "subject-primary",
    client_hash: str = HASH_A,
) -> dict:
    return {
        "schema_version": 1,
        "binding_id": binding_id,
        "revision": 1,
        "subject_id": subject_id,
        "runtime_boot_id": "boot-1",
        "client_id_hash": client_hash,
        "assurance": "desktop_confirmed",
        "binding_source": "desktop_profile",
        "status": "active",
        "issued_at_utc": NOW,
        "expires_at_utc": LATER,
        "revoked_at_utc": None,
    }


def generation_wire(*, generation: int = 1, fenced: bool = False) -> dict:
    return {
        "schema_version": 1,
        "session_id": "session-1",
        "generation": generation,
        "guest_present": fenced,
        "privacy_fenced": fenced,
        "owner_subject_id": None if fenced else "subject-primary",
        "active_binding_id": None if fenced else "binding-1",
        "revision": 1,
        "created_at_utc": NOW,
        "updated_at_utc": NOW,
    }


def participant_wire(
    participant_id: str,
    subject_id: str,
    *,
    generation: int,
    role: str,
) -> dict:
    return {
        "schema_version": 1,
        "participant_id": participant_id,
        "revision": 1,
        "session_id": "session-1",
        "subject_id": subject_id,
        "participant_role": role,
        "identity_assurance": "desktop_confirmed",
        "joined_at_utc": NOW,
        "left_at_utc": None,
        "server_binding_source": "desktop_profile",
        "status": "active",
        "created_at_utc": NOW,
        "updated_at_utc": NOW,
        "session_generation": generation,
        "binding_id": None,
        "lease_expires_at_utc": None,
        "active": True,
    }


def test_legacy_access_context_decodes_to_guest_generation_defaults():
    context = AccessContext.from_dict(access_wire("identity.manage"))

    assert context.session_generation == 0
    assert context.guest_present is True
    assert context.binding_id is None
    assert context.binding_assurance.value == "guest"
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.session_generation = 1

    bad = access_wire("identity.manage")
    bad["session_generation"] = True
    with pytest.raises(ValueError, match="session_generation"):
        AccessContext.from_dict(bad)


@pytest.mark.parametrize(
    ("contract", "wire"),
    (
        (SubjectBinding, binding_wire()),
        (
            HandoffLease,
            {
                "schema_version": 1,
                "lease_id": "lease-1",
                "revision": 1,
                "issuer_subject_id": "subject-primary",
                "target_subject_id": "subject-known",
                "session_id": "session-1",
                "session_generation": 1,
                "assurance": "owner_attested",
                "status": "active",
                "issued_at_utc": NOW,
                "expires_at_utc": LATER,
                "consumed_at_utc": None,
                "revoked_at_utc": None,
            },
        ),
        (SessionGenerationState, generation_wire()),
        (
            RelationshipView,
            {
                "schema_version": 1,
                "subject_id": "subject-primary",
                "address_name": "Caleb",
                "boundaries": ["Ask before discussing private finances."],
                "commitments": [],
                "recent_milestones": [],
                "shared_memory_ids": [],
                "unresolved_conflicts": [],
                "source_event_ids": ["relationship-event-1"],
                "acl_epoch": 1,
                "generated_at_utc": NOW,
                "expires_at_utc": LATER,
            },
        ),
        (
            CommunicationGuidance,
            {
                "schema_version": 1,
                "subject_id": "subject-primary",
                "language": None,
                "address_name": None,
                "verbosity": "balanced",
                "directness": "neutral",
                "formality": "neutral",
                "ask_before_sensitive_topic": True,
                "speech_rate": 1.0,
                "source_claim_ids": [],
                "acl_epoch": 1,
                "expires_at_utc": LATER,
            },
        ),
    ),
)
def test_l3_contracts_are_strict_immutable_and_round_trip(contract, wire):
    value = contract.from_dict(copy.deepcopy(wire))
    assert value.to_dict() == wire
    assert contract.from_dict(value.to_dict()) == value
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.schema_version = 2

    unexpected = copy.deepcopy(wire)
    unexpected["permission_override"] = True
    with pytest.raises(ValueError, match="unexpected field"):
        contract.from_dict(unexpected)


@pytest.mark.parametrize("rate", (True, float("nan"), 0.89, 1.11))
def test_communication_guidance_rejects_unsafe_speech_rates(rate):
    wire = {
        "schema_version": 1,
        "subject_id": "subject-primary",
        "language": None,
        "address_name": None,
        "verbosity": "balanced",
        "directness": "neutral",
        "formality": "neutral",
        "ask_before_sensitive_topic": True,
        "speech_rate": rate,
        "source_claim_ids": [],
        "acl_epoch": 1,
        "expires_at_utc": LATER,
    }
    with pytest.raises(ValueError, match="speech_rate"):
        CommunicationGuidance.from_dict(wire)


def test_subject_commands_require_scopes_and_explicit_confirmation():
    command = {
        "schema_version": 1,
        "command_id": "bootstrap-1",
        "access_context": access_wire("identity.manage"),
        "display_name": "Primary user",
        "aliases": ["Caleb"],
        "explicit_confirmation": True,
        "idempotency_key": "bootstrap-idempotency-1",
        "issued_at_utc": NOW,
    }
    parsed = BootstrapPrimary.from_dict(command)
    assert BootstrapPrimary.from_dict(parsed.to_dict()) == parsed
    assert parsed.access_context.binding_assurance.value == "guest"

    missing_scope = copy.deepcopy(command)
    missing_scope["access_context"] = access_wire("memory.manage")
    with pytest.raises(ValueError, match="identity.manage"):
        BootstrapPrimary.from_dict(missing_scope)

    unconfirmed = copy.deepcopy(command)
    unconfirmed["explicit_confirmation"] = False
    with pytest.raises(ValueError, match="explicit"):
        BootstrapPrimary.from_dict(unconfirmed)

    clear_guest = {
        "schema_version": 1,
        "command_id": "guest-present-1",
        "access_context": access_wire("participants.manage"),
        "guest_present": False,
        "expected_generation": 1,
        "explicit_owner_confirmation": False,
        "idempotency_key": "guest-present-idempotency-1",
        "issued_at_utc": NOW,
    }
    with pytest.raises(ValueError, match="confirmation"):
        SetGuestPresent.from_dict(clear_guest)


def test_store_enforces_single_primary_binding_and_generation_owner(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    try:
        primary = Subject.from_dict(
            subject_wire("subject-primary", "primary_user", display_name="Primary")
        )
        known = Subject.from_dict(
            subject_wire("subject-known", "known_person", display_name="Known")
        )
        assert store.put_subject(primary, writer_token=token)
        assert store.put_subject(known, writer_token=token)

        duplicate_primary_wire = subject_wire(
            "subject-primary-2", "primary_user", display_name="Another primary"
        )
        with pytest.raises(MemoryStoreConflictError, match="active primary"):
            store.put_subject(
                Subject.from_dict(duplicate_primary_wire), writer_token=token
            )

        binding = SubjectBinding.from_dict(binding_wire())
        assert store.put_subject_binding(binding, writer_token=token)
        with pytest.raises(MemoryStoreConflictError, match="active subject binding"):
            store.put_subject_binding(
                SubjectBinding.from_dict(
                    binding_wire("binding-2", subject_id="subject-known")
                ),
                writer_token=token,
            )

        assert store.put_session_generation(
            SessionGenerationState.from_dict(generation_wire()), writer_token=token
        )
        assert store.put_session_participant(
            SessionParticipant.from_dict(
                participant_wire(
                    "participant-primary", "subject-primary", generation=1, role="primary"
                )
            ),
            writer_token=token,
        )
        with pytest.raises(MemoryStoreConflictError, match="active owner"):
            store.put_session_participant(
                SessionParticipant.from_dict(
                    participant_wire(
                        "participant-known", "subject-known", generation=1, role="owner"
                    )
                ),
                writer_token=token,
            )

        assert store.get_subject_binding("binding-1") == binding
        assert store.get_session_generation("session-1") == SessionGenerationState.from_dict(
            generation_wire()
        )
    finally:
        store.close()


def test_foreign_key_failure_rolls_back_and_store_reopens(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    try:
        missing = SubjectBinding.from_dict(
            binding_wire(subject_id="subject-missing")
        )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            store.put_subject_binding(missing, writer_token=token)
        assert store.get_subject_binding("binding-1") is None

        primary = Subject.from_dict(
            subject_wire("subject-primary", "primary_user", display_name="Primary")
        )
        assert store.put_subject(primary, writer_token=token)
        assert store.put_subject_binding(
            SubjectBinding.from_dict(binding_wire()), writer_token=token
        )
    finally:
        store.close()

    reopened = MemoryStore(tmp_path, writer_token=token)
    try:
        assert reopened.status()["state"] == "ready"
        assert reopened.get_subject_binding("binding-1") is not None
    finally:
        reopened.close()


def test_v5_rows_upgrade_to_guest_fenced_generation_without_elevation(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    path = store.path
    primary_wire = subject_wire(
        "subject-primary", "primary_user", display_name="Legacy primary"
    )
    participant = participant_wire(
        "participant-primary", "subject-primary", generation=0, role="primary"
    )
    participant["active"] = False
    assert store.put_subject(Subject.from_dict(primary_wire), writer_token=token)
    assert store.put_session_participant(
        SessionParticipant.from_dict(participant), writer_token=token
    )
    store.close()

    legacy_subject = {
        key: value
        for key, value in primary_wire.items()
        if key
        not in {
            "aliases",
            "created_by_subject_id",
            "assurance_ceiling",
            "privacy_class",
        }
    }
    legacy_participant = {
        key: value
        for key, value in participant.items()
        if key
        not in {
            "session_generation",
            "binding_id",
            "lease_expires_at_utc",
            "active",
        }
    }
    db = sqlite3.connect(path)
    try:
        db.execute(
            "UPDATE subjects SET payload_json = ? WHERE subject_id = 'subject-primary'",
            (json.dumps(legacy_subject, separators=(",", ":")),),
        )
        db.execute(
            "UPDATE session_participants SET payload_json = ? "
            "WHERE participant_id = 'participant-primary'",
            (json.dumps(legacy_participant, separators=(",", ":")),),
        )
        for table_name in (
            "handoff_leases",
            "session_generations",
            "subject_bindings",
            "claim_decisions",
            "claim_source_suppressions",
            "relationship_view_meta",
            "shared_confirmation_sets",
        ):
            db.execute(f"DROP TABLE {table_name}")
        db.execute("DELETE FROM schema_migrations WHERE version = 6")
        db.execute("UPDATE memory_meta SET schema_version = 5")
        db.execute("PRAGMA user_version = 5")
        db.commit()
    finally:
        db.close()

    upgraded = MemoryStore(tmp_path, writer_token=token)
    try:
        assert upgraded.status()["state"] == "ready"
        migrated = upgraded.get_session_generation("session-1")
        assert migrated is not None
        assert migrated.generation == 0
        assert migrated.guest_present is True
        assert migrated.privacy_fenced is True
        assert migrated.owner_subject_id is None
        assert migrated.active_binding_id is None

        decoded_subject = upgraded.get_subject("subject-primary")
        assert decoded_subject is not None
        assert decoded_subject.aliases == ()
        assert decoded_subject.assurance_ceiling.value == "guest"
        decoded_participant = upgraded.active_session_participants("session-1")[0]
        assert decoded_participant.session_generation == 0
        assert decoded_participant.active is False
    finally:
        upgraded.close()
