from __future__ import annotations

import ast
import copy
import dataclasses
import inspect

import pytest

import core.life.memory as memory
import core.life.memory.contracts as contracts_module
from core.life.memory.contracts import (
    AccessContext,
    ConfirmSharedMemory,
    CorrectMemory,
    CreateJournalEntry,
    DeletionRequest,
    DerivationEdge,
    ExperienceEpisode,
    ForgetMemory,
    JournalEntry,
    MigrateLegacyBatch,
    ProjectTerminal,
    ProposeSharedMemory,
    RecallBundle,
    RecallQuery,
    RelationshipEvent,
    SessionParticipant,
    SharedMemory,
    Subject,
    UserModelClaim,
    canonical_hash,
    canonical_json_bytes,
)


NOW = "2026-08-20T10:00:00.000Z"
LATER = "2026-08-20T10:05:00.000Z"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _access_context(purpose: str = "manage") -> dict:
    return {
        "schema_version": 1,
        "context_id": f"context-{purpose}",
        "runtime_boot_id": "boot-1",
        "client_id_hash": HASH_A,
        "capability_scopes": [f"memory.{purpose}"],
        "actor_subject_id": "subject-user",
        "actor_kind": "primary_user",
        "session_id": "session-1",
        "participant_subject_ids": ["subject-user", "subject-javis"],
        "audience_ceiling": "explicit_shared",
        "identity_assurance": "desktop_confirmed",
        "purpose": purpose,
        "acl_epoch": 3,
        "issued_at_utc": NOW,
        "expires_at_utc": LATER,
    }


ACCESS = _access_context()

EPISODE = {
    "schema_version": 1,
    "episode_id": "episode-1",
    "revision": 1,
    "owner_subject_id": "subject-user",
    "audience": "owner_private",
    "privacy_class": "user_private",
    "session_id": "session-1",
    "request_id": "request-1",
    "participant_subject_ids": ["subject-user", "subject-javis"],
    "started_at_utc": NOW,
    "ended_at_utc": LATER,
    "outcome": "completed",
    "what_happened": "The user asked Javis to preserve a verified project decision.",
    "javis_attention": "The request carried an explicit remember intent.",
    "intent_summary": "Preserve a project boundary.",
    "action_summary": "Javis recorded the evidence-backed decision.",
    "verified_result_summary": "The completed terminal evidence was available.",
    "meaning_for_user": "Keeps the project boundary available across sessions.",
    "meaning_for_javis": "Use the boundary only as governed context.",
    "source_terminal_event_id": "event-terminal-1",
    "source_terminal_sequence": 7,
    "source_sequence_domain": "conversation.session-1",
    "source_message_ids": ["message-user-1", "message-assistant-1"],
    "source_event_ids": ["event-accepted-1", "event-terminal-1"],
    "source_digest": HASH_A,
    "extractor_version": "deterministic.v1",
    "confidence": 0.95,
    "status": "active",
    "retention_class": "memory_candidate",
    "expires_at_utc": None,
    "created_at_utc": NOW,
    "updated_at_utc": LATER,
}

JOURNAL = {
    "schema_version": 1,
    "entry_id": "journal-1",
    "revision": 1,
    "owner_subject_id": "subject-user",
    "audience": "owner_private",
    "privacy_class": "user_private",
    "range_started_at_utc": NOW,
    "range_ended_at_utc": LATER,
    "title": "Project boundary",
    "body": "Javis interprets the completed episode as a continuity boundary.",
    "source_episode_ids": ["episode-1"],
    "entry_kind": "boundary_reflection",
    "source_digest": HASH_A,
    "status": "active",
    "retention_class": "continuity",
    "expires_at_utc": None,
    "created_at_utc": NOW,
    "updated_at_utc": LATER,
}

SHARED = {
    "schema_version": 1,
    "shared_memory_id": "shared-1",
    "revision": 1,
    "proposal_revision": 1,
    "owner_subject_id": "subject-user",
    "audience": "explicit_shared",
    "privacy_class": "user_private",
    "source_episode_ids": ["episode-1"],
    "proposed_text": "We agreed to keep the project boundary explicit.",
    "participant_subject_ids": ["subject-user", "subject-javis"],
    "confirmation_receipts": [],
    "status": "proposed",
    "confirmed_at_utc": None,
    "revoked_at_utc": None,
    "audience_subject_ids": ["subject-user", "subject-javis"],
    "source_digest": HASH_A,
    "retention_class": "continuity",
    "expires_at_utc": None,
    "created_at_utc": NOW,
    "updated_at_utc": LATER,
}

CLAIM = {
    "schema_version": 1,
    "claim_id": "claim-1",
    "revision": 1,
    "owner_subject_id": "subject-user",
    "audience": "owner_private",
    "privacy_class": "user_private",
    "subject_id": "subject-user",
    "predicate": "project.boundary",
    "value_type": "string",
    "value": "Keep ownership explicit",
    "epistemic_class": "explicit_statement",
    "confidence": 0.9,
    "sensitivity": "personal",
    "status": "candidate",
    "source_evidence_ids": ["message-user-1"],
    "contradiction_claim_ids": [],
    "supersedes_claim_id": None,
    "acl_subject_ids": ["subject-user"],
    "source_digest": HASH_A,
    "confirmed_at_utc": None,
    "expires_at_utc": None,
    "created_at_utc": NOW,
    "updated_at_utc": LATER,
}

RELATIONSHIP = {
    "schema_version": 1,
    "relationship_event_id": "relationship-1",
    "revision": 1,
    "owner_subject_id": "subject-user",
    "audience": "participants",
    "privacy_class": "user_private",
    "subject_ids": ["subject-user", "subject-javis"],
    "event_kind": "boundary",
    "summary": "The user explicitly set a project boundary.",
    "source_evidence_ids": ["message-user-1"],
    "source_digest": HASH_A,
    "occurred_at_utc": NOW,
    "status": "candidate",
    "retention_class": "continuity",
    "expires_at_utc": None,
    "created_at_utc": NOW,
    "updated_at_utc": LATER,
}

SUBJECT = {
    "schema_version": 1,
    "subject_id": "subject-user",
    "revision": 1,
    "subject_kind": "primary_user",
    "display_name": "Primary user",
    "status": "active",
    "identity_assurance": "desktop_confirmed",
    "credential_reference_hash": HASH_B,
    "merged_into_subject_id": None,
    "session_scope_id": None,
    "created_at_utc": NOW,
    "updated_at_utc": LATER,
}

PARTICIPANT = {
    "schema_version": 1,
    "participant_id": "participant-1",
    "revision": 1,
    "session_id": "session-1",
    "subject_id": "subject-user",
    "participant_role": "primary",
    "identity_assurance": "desktop_confirmed",
    "joined_at_utc": NOW,
    "left_at_utc": None,
    "server_binding_source": "desktop.binding",
    "status": "active",
    "created_at_utc": NOW,
    "updated_at_utc": LATER,
}

EDGE = {
    "schema_version": 1,
    "edge_id": "edge-1",
    "source_kind": "experience_episode",
    "source_id": "episode-1",
    "target_kind": "journal_entry",
    "target_id": "journal-1",
    "relation": "derived_from",
    "extractor": "journal",
    "extractor_version": "deterministic.v1",
    "source_digest": HASH_A,
    "created_at_utc": NOW,
    "active": True,
}

DELETION = {
    "schema_version": 1,
    "deletion_request_id": "deletion-1",
    "revision": 1,
    "actor_subject_id": "subject-user",
    "scope": "item",
    "target_selector": {
        "item_kind": "journal_entry",
        "item_id": "journal-1",
        "subject_id": None,
        "session_id": None,
        "range_started_at_utc": None,
        "range_ended_at_utc": None,
    },
    "source_handling": "derived_only",
    "state": "accepted",
    "progress_cursor": None,
    "attempt": 0,
    "last_reason_code": None,
    "created_at_utc": NOW,
    "updated_at_utc": NOW,
    "completed_at_utc": None,
}

RECALL_ITEM = {
    "item_id": "episode-1",
    "item_kind": "experience_episode",
    "prompt_text": "The user asked Javis to preserve a verified project decision.",
    "occurred_at_utc": NOW,
    "epistemic_label": "evidence_derived",
    "source_citation_token": "citation-1",
    "owner_label": "actor",
    "audience": "owner_private",
    "confidence": 0.95,
}


CORE_OBJECT_CASES = (
    (ExperienceEpisode, EPISODE),
    (JournalEntry, JOURNAL),
    (SharedMemory, SHARED),
    (UserModelClaim, CLAIM),
    (RelationshipEvent, RELATIONSHIP),
    (Subject, SUBJECT),
    (SessionParticipant, PARTICIPANT),
    (DerivationEdge, EDGE),
    (DeletionRequest, DELETION),
)

COMMAND_RESULT_CASES = (
    (
        ProjectTerminal,
        {
            "schema_version": 1,
            "command_id": "command-project",
            "source_store_id": "conversation-store-1",
            "terminal_row_id": 7,
            "session_id": "session-1",
            "request_id": "request-1",
            "terminal_event_id": "event-terminal-1",
            "outcome": "completed",
            "idempotency_key": "project-terminal-1",
            "issued_at_utc": NOW,
        },
    ),
    (
        CreateJournalEntry,
        {
            "schema_version": 1,
            "command_id": "command-journal",
            "access_context": _access_context("manage"),
            "source_episode_ids": ["episode-1"],
            "entry_kind": "daily_note",
            "title": "Daily continuity",
            "body": "Interpret only the cited active episode.",
            "idempotency_key": "create-journal-1",
            "issued_at_utc": NOW,
        },
    ),
    (
        ProposeSharedMemory,
        {
            "schema_version": 1,
            "command_id": "command-propose",
            "access_context": _access_context("manage"),
            "source_episode_ids": ["episode-1"],
            "proposed_text": "We agreed to keep ownership explicit.",
            "idempotency_key": "propose-shared-1",
            "issued_at_utc": NOW,
        },
    ),
    (
        ConfirmSharedMemory,
        {
            "schema_version": 1,
            "command_id": "command-confirm",
            "access_context": _access_context("manage"),
            "shared_memory_id": "shared-1",
            "proposal_revision": 1,
            "idempotency_key": "confirm-shared-1",
            "issued_at_utc": NOW,
        },
    ),
    (
        CorrectMemory,
        {
            "schema_version": 1,
            "command_id": "command-correct",
            "access_context": _access_context("manage"),
            "target_kind": "journal_entry",
            "target_id": "journal-1",
            "expected_revision": 1,
            "corrected_text": "The boundary applies only to this project.",
            "source_evidence_ids": ["message-user-2"],
            "idempotency_key": "correct-memory-1",
            "issued_at_utc": NOW,
        },
    ),
    (
        ForgetMemory,
        {
            "schema_version": 1,
            "command_id": "command-forget",
            "access_context": _access_context("delete"),
            "deletion_request_id": "deletion-1",
            "scope": "item",
            "target_selector": copy.deepcopy(DELETION["target_selector"]),
            "source_handling": "derived_only",
            "reason_code": "user_requested",
            "idempotency_key": "forget-memory-1",
            "issued_at_utc": NOW,
        },
    ),
    (
        MigrateLegacyBatch,
        {
            "schema_version": 1,
            "command_id": "command-migrate",
            "access_context": _access_context("migration"),
            "migration_id": "migration-1",
            "manifest_digest": HASH_A,
            "batch_index": 0,
            "candidate_ids": ["legacy-candidate-1"],
            "idempotency_key": "migrate-batch-1",
            "issued_at_utc": NOW,
        },
    ),
    (
        RecallQuery,
        {
            "schema_version": 1,
            "query_id": "query-1",
            "access_context": _access_context("recall"),
            "query_text": "project boundary",
            "item_kinds": ["experience_episode", "journal_entry"],
            "limit": 8,
            "max_item_chars": 512,
            "max_total_bytes": 4096,
            "occurred_after_utc": None,
            "occurred_before_utc": None,
            "issued_at_utc": NOW,
        },
    ),
    (
        RecallBundle,
        {
            "schema_version": 1,
            "query_id": "query-1",
            "context_id": "context-recall",
            "items": [RECALL_ITEM],
            "acl_epoch": 3,
            "index_generation": 2,
            "generated_at_utc": NOW,
            "truncated": False,
            "reason_code": None,
        },
    ),
)

ALL_CASES = ((AccessContext, ACCESS),) + CORE_OBJECT_CASES + COMMAND_RESULT_CASES


@pytest.mark.parametrize(("contract", "wire"), ALL_CASES)
def test_contracts_round_trip_reject_unknown_fields_and_are_frozen(contract, wire):
    value = contract.from_dict(copy.deepcopy(wire))
    assert value.to_dict() == wire
    assert contract.from_dict(value.to_dict()) == value
    with pytest.raises(dataclasses.FrozenInstanceError):
        value.schema_version = 2

    unexpected = copy.deepcopy(wire)
    unexpected["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected field"):
        contract.from_dict(unexpected)


def test_nine_core_objects_are_dataclasses_with_strict_wire_coverage():
    assert len(CORE_OBJECT_CASES) == 9
    assert {contract.__name__ for contract, _ in CORE_OBJECT_CASES} == {
        "ExperienceEpisode",
        "JournalEntry",
        "SharedMemory",
        "UserModelClaim",
        "RelationshipEvent",
        "Subject",
        "SessionParticipant",
        "DerivationEdge",
        "DeletionRequest",
    }
    for contract, wire in CORE_OBJECT_CASES:
        assert dataclasses.is_dataclass(contract)
        assert contract.__dataclass_params__.frozen
        assert tuple(field.name for field in dataclasses.fields(contract)) == tuple(wire)


def test_canonical_json_and_hash_are_stable_across_round_trip_and_key_order():
    episode = ExperienceEpisode.from_dict(copy.deepcopy(EPISODE))
    reversed_wire = dict(reversed(list(episode.to_dict().items())))
    assert canonical_json_bytes(episode) == canonical_json_bytes(reversed_wire)
    assert canonical_hash(episode) == canonical_hash(reversed_wire)
    assert episode.canonical_hash() == canonical_hash(episode.to_dict())
    assert len(episode.canonical_hash()) == 64
    assert b"\n" not in canonical_json_bytes(episode)


@pytest.mark.parametrize(
    ("contract", "wire", "field", "bad_value"),
    (
        (ExperienceEpisode, EPISODE, "audience", "everyone"),
        (ExperienceEpisode, EPISODE, "privacy_class", "private-ish"),
        (ExperienceEpisode, EPISODE, "status", "published"),
        (SharedMemory, SHARED, "status", "approved"),
        (RelationshipEvent, RELATIONSHIP, "event_kind", "friendship_score"),
    ),
)
def test_bad_enums_are_rejected(contract, wire, field, bad_value):
    invalid = copy.deepcopy(wire)
    invalid[field] = bad_value
    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


def test_bounded_text_rejects_overlong_body_and_recall_item():
    journal = copy.deepcopy(JOURNAL)
    journal["body"] = "x" * 4097
    with pytest.raises(ValueError, match="body"):
        JournalEntry.from_dict(journal)

    bundle = COMMAND_RESULT_CASES[-1][1]
    invalid = copy.deepcopy(bundle)
    invalid["items"][0]["prompt_text"] = "x" * 513
    with pytest.raises(ValueError, match="prompt_text"):
        RecallBundle.from_dict(invalid)


@pytest.mark.parametrize(
    ("contract", "wire", "field", "bad_value"),
    (
        (ExperienceEpisode, EPISODE, "episode_id", "episode\x00bad"),
        (JournalEntry, JOURNAL, "body", "body\nwith-control"),
        (AccessContext, ACCESS, "issued_at_utc", "2026-08-20T10:00:00.000Z\x00"),
        (AccessContext, ACCESS, "expires_at_utc", "2026-08-20T10:05:00+00:00"),
    ),
)
def test_control_characters_and_noncanonical_utc_are_rejected(
    contract, wire, field, bad_value
):
    invalid = copy.deepcopy(wire)
    invalid[field] = bad_value
    with pytest.raises(ValueError, match=field):
        contract.from_dict(invalid)


def test_nested_collections_and_contracts_are_immutable():
    episode = ExperienceEpisode.from_dict(copy.deepcopy(EPISODE))
    assert isinstance(episode.source_message_ids, tuple)
    with pytest.raises(AttributeError):
        episode.source_message_ids.append("message-3")

    deletion = DeletionRequest.from_dict(copy.deepcopy(DELETION))
    with pytest.raises(dataclasses.FrozenInstanceError):
        deletion.target_selector.item_id = "journal-2"


def test_claim_typed_value_and_shared_confirmation_invariants_are_strict():
    claim = copy.deepcopy(CLAIM)
    claim["value_type"] = "boolean"
    with pytest.raises(ValueError, match="value"):
        UserModelClaim.from_dict(claim)

    shared = copy.deepcopy(SHARED)
    shared["status"] = "confirmed"
    shared["confirmed_at_utc"] = LATER
    with pytest.raises(ValueError, match="status"):
        SharedMemory.from_dict(shared)


def test_deletion_selector_and_access_purpose_fail_closed():
    deletion = copy.deepcopy(DELETION)
    deletion["target_selector"]["subject_id"] = "subject-user"
    with pytest.raises(ValueError, match="target_selector"):
        DeletionRequest.from_dict(deletion)

    forget = copy.deepcopy(COMMAND_RESULT_CASES[5][1])
    forget["access_context"] = _access_context("manage")
    with pytest.raises(ValueError, match="delete purpose"):
        ForgetMemory.from_dict(forget)


def test_guest_access_context_cannot_claim_private_ceiling():
    guest = _access_context("recall")
    guest.update(
        {
            "actor_subject_id": "guest-session-1",
            "actor_kind": "guest",
            "participant_subject_ids": [],
            "identity_assurance": "guest",
            "audience_ceiling": "owner_private",
        }
    )
    with pytest.raises(ValueError, match="audience_ceiling"):
        AccessContext.from_dict(guest)


def test_relationship_contract_has_no_runtime_authorization_imports():
    tree = ast.parse(inspect.getsource(contracts_module))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name.casefold() for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append((node.module or "").casefold())
            imports.extend(alias.name.casefold() for alias in node.names)
    joined = " ".join(imports)
    assert "toolregistry" not in joined
    assert "permission" not in joined
    assert "approval" not in joined


def test_memory_package_exports_the_required_task_one_surface():
    required = {
        "AccessContext",
        "ExperienceEpisode",
        "JournalEntry",
        "SharedMemory",
        "UserModelClaim",
        "RelationshipEvent",
        "Subject",
        "SessionParticipant",
        "DerivationEdge",
        "DeletionRequest",
        "ProjectTerminal",
        "CreateJournalEntry",
        "ProposeSharedMemory",
        "ConfirmSharedMemory",
        "CorrectMemory",
        "ForgetMemory",
        "MigrateLegacyBatch",
        "RecallQuery",
        "RecallBundle",
    }
    assert required <= set(memory.__all__)
    assert all(getattr(memory, name).__name__ == name for name in required)
