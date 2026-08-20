from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from core.life.l8.contracts import (
    CANONICAL_CBOR_PROFILE,
    CANONICAL_JSON_PROFILE,
    CONTENT_HASH_PROFILE,
    MAX_ENVELOPE_RECORDS,
    SCHEMA_VERSION,
    SIGNATURE_INPUT_PROFILE,
    BranchHeadV1,
    BranchSyncRunV1,
    ContinuityEntryV1,
    ContinuityManifestV1,
    LineageOpV1,
    MigrationPhaseReceiptV1,
    MigrationReceiptV1,
    SyncConflictV1,
    SyncEnvelopeV1,
    TerminationPlanV1,
    TerminationReceiptV1,
    canonical_cbor_bytes,
    canonical_hash,
    canonical_json_bytes,
    compute_content_hash,
    content_hash_input,
    signature_input,
    validate_relative_path,
    verify_content_hash,
)


H0 = "0" * 64
H1 = hashlib.sha256(b"fixture-one").hexdigest()
H2 = hashlib.sha256(b"fixture-two").hexdigest()
H3 = hashlib.sha256(b"fixture-three").hexdigest()
SIGNATURE = base64.urlsafe_b64encode(bytes(range(64))).rstrip(b"=").decode("ascii")
OTHER_SIGNATURE = base64.urlsafe_b64encode(bytes(reversed(range(64)))).rstrip(b"=").decode("ascii")
PUBLIC_KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
NONCE = base64.urlsafe_b64encode(bytes(range(24))).rstrip(b"=").decode("ascii")
CIPHERTEXT_BYTES = b"synthetic-payload"
CIPHERTEXT = base64.urlsafe_b64encode(CIPHERTEXT_BYTES).rstrip(b"=").decode("ascii")
STARTED = "2026-08-20T01:02:03.004Z"
UPDATED = "2026-08-20T01:02:04.004Z"
COMPLETED = "2026-08-20T01:02:05.004Z"
EXPIRES = "2026-08-20T01:07:03.004Z"


def entry_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "domain": "life",
        "relative_path": "life/state.sqlite3",
        "entry_kind": "sqlite",
        "size_bytes": 32,
        "sha256": H1,
        "logical_record_count": 3,
        "sqlite_checkpoint": "checkpointed",
        "owner_subject_id": "javis-owner",
        "audience": ["primary-user"],
        "privacy_class": "user_private",
        "retention_class": "continuity",
        "expires_at_utc": None,
        "derivation_parent_ids": [],
        "required": True,
        "encryption_ref": {
            "algorithm": "aes-256-gcm",
            "key_id": "wrapped-key-001",
        },
    }
    value.update(overrides)
    return value


def manifest_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": "manifest-001",
        "purpose": "migration",
        "identity_id": "identity-001",
        "lineage_id": "lineage-001",
        "branch_id": "branch-001",
        "instance_id": "instance-001",
        "source_root_id": "root-001",
        "source_release_id": "release-001",
        "source_runtime_hash": H1,
        "model_refs": [{"model_id": "model-001", "sha256": H1}],
        "body_deployment_refs": [{"deployment_id": "body-001", "sha256": H2}],
        "skill_deployment_refs": [{"deployment_id": "skill-001", "sha256": H3}],
        "domain_schemas": {"life": 1},
        "event_cursors": {"life": 9},
        "tombstone_high_watermarks": {"life": 2},
        "entries": [entry_wire()],
        "external_asset_refs": [{"asset_id": "model-001", "sha256": H1}],
        "key_refs": [{"algorithm": "aes-256-gcm", "key_id": "wrapped-key-001"}],
        "total_entries": 1,
        "total_bytes": 32,
        "created_at_utc": STARTED,
        "expires_at_utc": EXPIRES,
        "created_by_version": "release-001",
        "approval_ref": "approval-001",
        "previous_manifest_hash": None,
        "privacy_class": "user_private",
        "retention_class": "continuity",
        "signing_key_id": "device-key-001",
        "signature_algorithm": "ed25519",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


def phase_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "phase": "copied",
        "input_set_hash": H1,
        "output_set_hash": H2,
        "started_at_utc": STARTED,
        "completed_at_utc": UPDATED,
        "processed_entries": 1,
        "processed_bytes": 32,
        "reason_code": None,
    }
    value.update(overrides)
    return value


def migration_receipt_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "receipt_id": "migration-receipt-001",
        "operation_id": "operation-001",
        "operation_kind": "data_root_move",
        "manifest_id": "manifest-001",
        "manifest_hash": H1,
        "identity_id": "identity-001",
        "lineage_id": "lineage-001",
        "branch_id": "branch-001",
        "source_root_id": "root-001",
        "target_root_id": "root-002",
        "source_instance_id": "instance-001",
        "target_instance_id": "instance-001",
        "state": "completed",
        "phase_receipts": [phase_wire()],
        "copied_entries": 1,
        "copied_bytes": 32,
        "verified_entries": 1,
        "rebuilt_entries": 0,
        "skipped_entries": 0,
        "schema_migration_ids": [],
        "pointer_before_hash": H1,
        "pointer_after_hash": H2,
        "activation_journal_hash": H3,
        "checkpoint_id": "checkpoint-001",
        "rollback_target": "root-001",
        "rollback_receipt_id": None,
        "started_at_utc": STARTED,
        "completed_at_utc": COMPLETED,
        "failure_reason_code": None,
        "residuals": [],
        "signing_key_id": "device-key-001",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


def lineage_op_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "op_id": "op-001",
        "op_sequence": 1,
        "op_kind": "birth",
        "identity_id": "identity-001",
        "lineage_id": "lineage-001",
        "branch_id": "branch-001",
        "instance_id": "instance-001",
        "parent_op_ids": [],
        "source_branch_id": None,
        "target_branch_id": None,
        "source_instance_id": None,
        "target_instance_id": None,
        "fork_point_manifest_hash": None,
        "continuity_manifest_hash": H1,
        "release_before": None,
        "release_after": "release-001",
        "model_before": None,
        "model_after": "model-001",
        "reason_code": "initial_birth",
        "user_decision_ref": "approval-001",
        "migration_receipt_id": None,
        "sync_receipt_ids": [],
        "created_at_utc": STARTED,
        "previous_op_hashes": [],
        "signing_key_id": "device-key-001",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


def branch_head_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "identity_id": "identity-001",
        "lineage_id": "lineage-001",
        "branch_id": "branch-001",
        "head_op_id": "op-001",
        "head_op_hash": H1,
        "revision": 1,
        "previous_head_hash": None,
        "updated_at_utc": UPDATED,
        "signing_key_id": "device-key-001",
        "signature_algorithm": "ed25519",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


def branch_sync_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "sync_run_id": "sync-run-001",
        "sync_session_id": "sync-session-001",
        "identity_id": "identity-001",
        "lineage_id": "lineage-001",
        "local_branch_id": "branch-001",
        "remote_branch_id": "branch-002",
        "state": "synced",
        "allowed_domains": ["life", "memory"],
        "base_heads": {"branch-001": H1},
        "advertised_heads": {"branch-002": H2},
        "envelope_ids": ["envelope-001"],
        "conflict_ids": [],
        "applied_record_count": 2,
        "tombstone_count": 1,
        "started_at_utc": STARTED,
        "updated_at_utc": UPDATED,
        "completed_at_utc": COMPLETED,
        "failure_reason_code": None,
        "signing_key_id": "device-key-001",
        "signature_algorithm": "ed25519",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


def envelope_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "envelope_id": "envelope-001",
        "sync_session_id": "sync-session-001",
        "identity_id": "identity-001",
        "lineage_id": "lineage-001",
        "sender_branch_id": "branch-001",
        "sender_instance_id": "instance-001",
        "recipient_device_key_id": "device-key-002",
        "sender_device_key_id": "device-key-001",
        "sender_sequence": 7,
        "base_heads": {"branch-001": H1},
        "advertised_heads": {"branch-001": H2},
        "domain": "memory",
        "record_count": 1,
        "tombstone_count": 0,
        "payload_algorithm": "xchacha20-poly1305-v1",
        "nonce": NONCE,
        "ciphertext": CIPHERTEXT,
        "payload_sha256": hashlib.sha256(CIPHERTEXT_BYTES).hexdigest(),
        "key_epoch": 1,
        "created_at_utc": STARTED,
        "expires_at_utc": EXPIRES,
        "signing_key_id": "device-key-001",
        "signature_algorithm": "ed25519",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


def conflict_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "conflict_id": "conflict-001",
        "sync_run_id": "sync-run-001",
        "domain": "memory",
        "identity_id": "identity-001",
        "lineage_id": "lineage-001",
        "local_branch_id": "branch-001",
        "remote_branch_id": "branch-002",
        "source_head_hashes": [H1, H2],
        "record_hashes": [H3],
        "reason_code": "divergent_fact",
        "state": "open",
        "resolution_code": None,
        "user_decision_ref": None,
        "created_at_utc": STARTED,
        "resolved_at_utc": None,
        "signing_key_id": "device-key-001",
        "signature_algorithm": "ed25519",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


def termination_plan_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": "termination-plan-001",
        "scope": "identity_all_local",
        "identity_id": "identity-001",
        "lineage_id": "lineage-001",
        "branch_ids": ["branch-001"],
        "instance_ids": ["instance-001"],
        "root_id": "root-001",
        "manifest_id": "manifest-001",
        "manifest_hash": H1,
        "target_entry_hashes": [H2],
        "external_asset_actions": [{"asset_id_hash": H3, "action_code": "preserve"}],
        "owned_processes": [{"process_id_hash": H2, "kind": "sidecar"}],
        "database_checkpoint_ids": ["checkpoint-001"],
        "tombstone_plan": {"domain": "memory", "high_watermark": 8},
        "sync_outbox_policy": "discard_pending",
        "key_ids_to_destroy": ["wrapped-key-001"],
        "key_epoch_after": 2,
        "preserve_user_exports": True,
        "preserve_installed_app": True,
        "receipt_export_target_hash": H3,
        "receipt_ephemeral_public_key": PUBLIC_KEY,
        "receipt_ephemeral_key_id": "ephemeral-key-001",
        "confirmation_challenge_hash": H2,
        "authorization_id": "authorization-001",
        "created_at_utc": STARTED,
        "expires_at_utc": EXPIRES,
        "irreversible_after_state": "deleting",
        "signing_key_id": "device-key-001",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


def termination_receipt_wire(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "receipt_id": "termination-receipt-001",
        "plan_id": "termination-plan-001",
        "plan_hash": H1,
        "scope": "identity_all_local",
        "identity_id_hash": H1,
        "lineage_id_hash": H2,
        "root_id_hash": H3,
        "started_at_utc": STARTED,
        "completed_at_utc": COMPLETED,
        "state": "completed",
        "quiesce_receipt_hash": H1,
        "tombstone_high_watermarks": {"memory": 8},
        "destroyed_key_ids_hash": H2,
        "key_destroy_results": [{"key_id_hash": H1, "result_code": "destroyed"}],
        "deleted_entry_count": 1,
        "deleted_bytes": 32,
        "external_asset_results": [{"asset_id_hash": H3, "result_code": "preserved"}],
        "verification_checks": [{"check_id": "root_absence", "result_code": "passed"}],
        "residual_count": 0,
        "residual_reason_codes": [],
        "assurance_level": "crypto_erasure_plus_absence",
        "content_free": True,
        "verifier_version": "verifier-001",
        "previous_receipt_hash": None,
        "receipt_ephemeral_key_id": "ephemeral-key-001",
        "signature_algorithm": "ed25519",
        "signature": SIGNATURE,
    }
    value.update(overrides)
    return value


SEALED_CASES = (
    (ContinuityManifestV1, manifest_wire),
    (MigrationReceiptV1, migration_receipt_wire),
    (LineageOpV1, lineage_op_wire),
    (BranchHeadV1, branch_head_wire),
    (BranchSyncRunV1, branch_sync_wire),
    (SyncEnvelopeV1, envelope_wire),
    (SyncConflictV1, conflict_wire),
    (TerminationPlanV1, termination_plan_wire),
    (TerminationReceiptV1, termination_receipt_wire),
)

ALL_CASES = (
    (ContinuityEntryV1, entry_wire, False),
    (ContinuityManifestV1, manifest_wire, True),
    (MigrationPhaseReceiptV1, phase_wire, False),
    (MigrationReceiptV1, migration_receipt_wire, True),
    (LineageOpV1, lineage_op_wire, True),
    (BranchHeadV1, branch_head_wire, True),
    (BranchSyncRunV1, branch_sync_wire, True),
    (SyncEnvelopeV1, envelope_wire, True),
    (SyncConflictV1, conflict_wire, True),
    (TerminationPlanV1, termination_plan_wire, True),
    (TerminationReceiptV1, termination_receipt_wire, True),
)


def build(contract_type, factory, sealed):
    wire = factory()
    return contract_type.seal(**wire) if sealed else contract_type.from_dict(wire)


def test_exact_spec_field_sets_are_frozen():
    assert {field.name for field in fields(ContinuityEntryV1)} == {
        "schema_version",
        "domain",
        "relative_path",
        "entry_kind",
        "size_bytes",
        "sha256",
        "logical_record_count",
        "sqlite_checkpoint",
        "owner_subject_id",
        "audience",
        "privacy_class",
        "retention_class",
        "expires_at_utc",
        "derivation_parent_ids",
        "required",
        "encryption_ref",
    }
    assert {field.name for field in fields(ContinuityManifestV1)} == {
        "schema_version",
        "manifest_id",
        "purpose",
        "identity_id",
        "lineage_id",
        "branch_id",
        "instance_id",
        "source_root_id",
        "source_release_id",
        "source_runtime_hash",
        "model_refs",
        "body_deployment_refs",
        "skill_deployment_refs",
        "domain_schemas",
        "event_cursors",
        "tombstone_high_watermarks",
        "entries",
        "external_asset_refs",
        "key_refs",
        "total_entries",
        "total_bytes",
        "created_at_utc",
        "expires_at_utc",
        "created_by_version",
        "approval_ref",
        "previous_manifest_hash",
        "privacy_class",
        "retention_class",
        "content_hash",
        "signing_key_id",
        "signature_algorithm",
        "signature",
    }
    assert {field.name for field in fields(MigrationReceiptV1)} == {
        "schema_version",
        "receipt_id",
        "operation_id",
        "operation_kind",
        "manifest_id",
        "manifest_hash",
        "identity_id",
        "lineage_id",
        "branch_id",
        "source_root_id",
        "target_root_id",
        "source_instance_id",
        "target_instance_id",
        "state",
        "phase_receipts",
        "copied_entries",
        "copied_bytes",
        "verified_entries",
        "rebuilt_entries",
        "skipped_entries",
        "schema_migration_ids",
        "pointer_before_hash",
        "pointer_after_hash",
        "activation_journal_hash",
        "checkpoint_id",
        "rollback_target",
        "rollback_receipt_id",
        "started_at_utc",
        "completed_at_utc",
        "failure_reason_code",
        "residuals",
        "content_hash",
        "signing_key_id",
        "signature",
    }
    assert {field.name for field in fields(TerminationReceiptV1)} == {
        "schema_version",
        "receipt_id",
        "plan_id",
        "plan_hash",
        "scope",
        "identity_id_hash",
        "lineage_id_hash",
        "root_id_hash",
        "started_at_utc",
        "completed_at_utc",
        "state",
        "quiesce_receipt_hash",
        "tombstone_high_watermarks",
        "destroyed_key_ids_hash",
        "key_destroy_results",
        "deleted_entry_count",
        "deleted_bytes",
        "external_asset_results",
        "verification_checks",
        "residual_count",
        "residual_reason_codes",
        "assurance_level",
        "content_free",
        "verifier_version",
        "previous_receipt_hash",
        "content_hash",
        "receipt_ephemeral_key_id",
        "signature_algorithm",
        "signature",
    }


@pytest.mark.parametrize("contract_type,factory,sealed", ALL_CASES)
def test_every_contract_round_trips_strict_wire(contract_type, factory, sealed):
    contract = build(contract_type, factory, sealed)
    wire = contract.to_wire()
    assert contract_type.from_wire(wire) == contract
    assert contract.canonical_json_bytes() == canonical_json_bytes(wire)
    assert contract.canonical_cbor_bytes() == canonical_cbor_bytes(wire)
    with pytest.raises(FrozenInstanceError):
        contract.schema_version = 2


@pytest.mark.parametrize("contract_type,factory,sealed", ALL_CASES)
def test_every_contract_rejects_missing_extra_and_future_schema(contract_type, factory, sealed):
    contract = build(contract_type, factory, sealed)
    valid = contract.to_dict()
    missing = dict(valid)
    missing.pop(next(iter(valid)))
    with pytest.raises(ValueError, match="missing field"):
        contract_type.from_dict(missing)
    extra = dict(valid, unexpected=True)
    with pytest.raises(ValueError, match="unexpected field"):
        contract_type.from_dict(extra)
    future = dict(valid, schema_version=SCHEMA_VERSION + 1)
    with pytest.raises(ValueError, match="unknown schema_version"):
        contract_type.from_dict(future)


@pytest.mark.parametrize("contract_type,factory", SEALED_CASES)
def test_every_immutable_record_detects_hash_tamper(contract_type, factory):
    contract = contract_type.seal(**factory())
    wire = contract.to_dict()
    key_field = (
        "receipt_ephemeral_key_id"
        if contract_type is TerminationReceiptV1
        else "signing_key_id"
    )
    wire[key_field] = "device-key-099"
    with pytest.raises(ValueError, match="content_hash"):
        contract_type.from_dict(wire)
    assert verify_content_hash(contract)


def test_canonical_profiles_match_cross_language_golden_fixture():
    fixture_path = Path(__file__).parent / "fixtures" / "l8" / "canonical_v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    payload = fixture["payload"]
    assert fixture["profiles"] == {
        "cbor": CANONICAL_CBOR_PROFILE,
        "json": CANONICAL_JSON_PROFILE,
        "signature_input": SIGNATURE_INPUT_PROFILE,
    }
    assert canonical_json_bytes(payload).hex() == fixture["canonical_json_hex"]
    assert canonical_cbor_bytes(payload).hex() == fixture["canonical_cbor_hex"]
    assert canonical_hash(payload) == fixture["canonical_hash"]
    assert signature_input(payload).hex() == fixture["signature_input_hex"]


def test_canonical_json_and_cbor_are_key_order_independent_and_finite():
    left = {"z": 7, "a": [True, None, 1.5], "m": {"b": 2, "a": 1}}
    right = {"m": {"a": 1, "b": 2}, "a": [True, None, 1.5], "z": 7}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert canonical_cbor_bytes(left) == canonical_cbor_bytes(right)
    with pytest.raises(ValueError, match="unsupported"):
        canonical_json_bytes({"value": float("nan")})
    with pytest.raises(ValueError, match="finite"):
        canonical_cbor_bytes({"value": float("inf")})


def test_canonical_cbor_rejects_unbounded_nesting():
    nested = {}
    for _ in range(15):
        nested = {"next": nested}
    with pytest.raises(ValueError, match="nesting"):
        canonical_cbor_bytes(nested)


def test_hash_and_signature_inputs_have_fixed_domain_separation():
    first = {"record_id": "synthetic-001", "content_hash": H1, "signature": SIGNATURE}
    second = dict(first, signature=OTHER_SIGNATURE)
    third = dict(first, content_hash=H2)
    assert content_hash_input(first).startswith(CONTENT_HASH_PROFILE.encode("ascii") + b"\0")
    assert signature_input(first).startswith(SIGNATURE_INPUT_PROFILE.encode("ascii") + b"\0")
    assert compute_content_hash(first) == compute_content_hash(second)
    assert signature_input(first) == signature_input(second)
    assert signature_input(first) != signature_input(third)


@pytest.mark.parametrize(
    "path",
    (
        "C:/Users/name/state.db",
        "C:\\Users\\name\\state.db",
        "//server/share/state.db",
        "\\\\server\\share\\state.db",
        "/absolute/state.db",
        "../state.db",
        "life/../state.db",
        "life//state.db",
        "life/./state.db",
        "life\\state.db",
        "life/state.db:stream",
        "life/NUL.txt",
        "life/com1",
        "life/trailing.",
        "life/trailing ",
        "life/\x00state.db",
        ".",
        "",
    ),
)
def test_relative_path_corpus_rejects_unsafe_windows_and_escape_forms(path):
    with pytest.raises(ValueError):
        validate_relative_path(path)


@pytest.mark.parametrize(
    "path",
    (
        "life/state.sqlite3",
        "continuity/manifests/manifest-001.json",
        "models/model-001/index.json",
        "用户/连续性.json",
    ),
)
def test_relative_path_corpus_accepts_canonical_wire_paths(path):
    assert validate_relative_path(path) == path


def test_manifest_rejects_path_collision_absolute_nested_path_and_wrong_totals():
    duplicate = entry_wire(relative_path="LIFE/STATE.sqlite3")
    with pytest.raises(ValueError, match="case collisions"):
        ContinuityManifestV1.seal(
            **manifest_wire(entries=[entry_wire(), duplicate], total_entries=2, total_bytes=64)
        )
    with pytest.raises(ValueError, match="absolute"):
        ContinuityManifestV1.seal(
            **manifest_wire(model_refs=[{"model_id": "model-001", "local_path": "C:/models/a"}])
        )
    with pytest.raises(ValueError, match="sum of entry sizes"):
        ContinuityManifestV1.seal(**manifest_wire(total_bytes=33))


def test_envelope_rejects_count_overflow_bad_ciphertext_hash_and_wrong_nonce_suite():
    with pytest.raises(ValueError, match="exceed"):
        SyncEnvelopeV1.seal(
            **envelope_wire(record_count=MAX_ENVELOPE_RECORDS, tombstone_count=1)
        )
    with pytest.raises(ValueError, match="payload_sha256"):
        SyncEnvelopeV1.seal(**envelope_wire(payload_sha256=H0))
    nonce_12 = base64.urlsafe_b64encode(bytes(range(12))).rstrip(b"=").decode("ascii")
    with pytest.raises(ValueError, match="24 bytes"):
        SyncEnvelopeV1.seal(**envelope_wire(nonce=nonce_12))


def test_ids_utc_signatures_and_time_order_are_strict():
    with pytest.raises(ValueError, match="opaque ID"):
        BranchHeadV1.seal(**branch_head_wire(branch_id="../branch"))
    with pytest.raises(ValueError, match="RFC3339 UTC"):
        BranchHeadV1.seal(**branch_head_wire(updated_at_utc="2026-08-20T01:02:03Z"))
    with pytest.raises(ValueError, match="64..64 bytes"):
        BranchHeadV1.seal(**branch_head_wire(signature="YWJj"))
    with pytest.raises(ValueError, match="later"):
        ContinuityManifestV1.seal(**manifest_wire(expires_at_utc=STARTED))


def test_lineage_copy_and_merge_shape_is_fail_closed():
    with pytest.raises(ValueError, match="distinct branch"):
        LineageOpV1.seal(
            **lineage_op_wire(
                op_kind="copy",
                source_branch_id="branch-001",
                target_branch_id="branch-001",
            )
        )
    with pytest.raises(ValueError, match="exactly two"):
        LineageOpV1.seal(**lineage_op_wire(op_kind="merge", parent_op_ids=["op-parent-001"]))


@pytest.mark.parametrize(
    "field,value",
    (
        ("verification_checks", [{"result_path": "safe/result"}]),
        ("verification_checks", [{"user_name": "synthetic-user"}]),
        ("verification_checks", [{"check_id": "absence", "result_code": "C:/Users/name"}]),
        ("key_destroy_results", [{"secret": "hidden"}]),
        ("external_asset_results", [{"file_content": "hidden"}]),
    ),
)
def test_termination_receipt_recursively_rejects_content_path_and_secret(field, value):
    with pytest.raises(ValueError):
        TerminationReceiptV1.seal(**termination_receipt_wire(**{field: value}))


def test_termination_receipt_is_content_free_and_assurance_is_honest():
    receipt = TerminationReceiptV1.seal(**termination_receipt_wire())
    assert receipt.content_free is True
    encoded = receipt.canonical_json_bytes().decode("utf-8")
    assert "identity-001" not in encoded
    assert "root-001" not in encoded
    assert "state.sqlite3" not in encoded
    with pytest.raises(ValueError, match="must be true"):
        TerminationReceiptV1.seal(**termination_receipt_wire(content_free=False))
    with pytest.raises(ValueError, match="cannot report residuals"):
        TerminationReceiptV1.seal(
            **termination_receipt_wire(residual_count=1, residual_reason_codes=["locked_entry"])
        )


def test_termination_plan_has_no_arbitrary_path_and_irreversibility_is_fixed():
    with pytest.raises(ValueError, match="absolute"):
        TerminationPlanV1.seal(
            **termination_plan_wire(external_asset_actions=[{"asset_path": "D:/models/model.bin"}])
        )
    with pytest.raises(ValueError, match="must equal deleting"):
        TerminationPlanV1.seal(**termination_plan_wire(irreversible_after_state="sealing"))


def test_content_hash_is_stable_after_nested_contract_normalization():
    manifest = ContinuityManifestV1.seal(**manifest_wire())
    assert manifest.verify_content_hash()
    reparsed = ContinuityManifestV1.from_dict(manifest.to_dict())
    assert reparsed.content_hash == manifest.content_hash
    assert reparsed.signature_input() == manifest.signature_input()
