from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from typing import get_args, get_type_hints

import pytest

from core.action.contracts import (
    ActionPhase,
    ActionReceiptV1,
    ActionRequestV1,
    ActionSourceKind,
    ActionState,
    ApprovalAuthorityHealth,
    ApprovalDecision,
    ApprovalRequestV1,
    ApprovalResolutionV1,
    ApprovalState,
    AuthorizationGrantV1,
    EffectAdmission,
    EffectClass,
    GENESIS_RECEIPT_HASH,
    GrantIssuerKind,
    GrantState,
    JAVIS_ACTION_PARAMETERS_V1,
    JournalHealth,
    MAX_PREVIEW_BYTES,
    ReceiptChainHealth,
    RecoveryMethod,
    RecoveryReceiptV1,
    RecoveryRequestedBy,
    RecoveryResult,
    RiskClass,
    SafetyMode,
    SafetySnapshotV1,
    action_parameters_hash,
    canonical_content_hash,
    canonical_json_bytes,
    canonical_preview_hash,
    canonical_target_scope_hash,
)


T0 = "2026-08-20T12:00:00.000Z"
T30S = "2026-08-20T12:00:30.000Z"
T60S = "2026-08-20T12:01:00.000Z"
T5M = "2026-08-20T12:05:00.000Z"
H = "a" * 64
H2 = "b" * 64
H3 = "c" * 64
SECRET_DIGEST = "d" * 64


def _seal(payload: dict[str, object]) -> dict[str, object]:
    wire = copy.deepcopy(payload)
    wire.pop("content_hash", None)
    wire["content_hash"] = canonical_content_hash(wire)
    return wire


def _request_payload() -> dict[str, object]:
    parameters = {"relative_path": "notes/today.txt", "content": "status ready"}
    target = {"workspace_root": "G:/Javis", "relative_path": "notes/today.txt"}
    return {
        "schema_version": 1,
        "action_request_id": "action-1",
        "javis_identity_id": "identity-1",
        "instance_id": "instance-1",
        "intention_id": "intention-1",
        "intention_revision": 4,
        "commitment_id": "commitment-1",
        "owner_subject_id": "owner-1",
        "runtime_boot_id": "boot-1",
        "source_kind": "conversation_ws",
        "source_ref": "request-1",
        "action_name": "workspace.file.write",
        "capability": "workspace.write",
        "normalized_parameters": parameters,
        "parameters_hash": action_parameters_hash(
            action_name="workspace.file.write",
            parameters=parameters,
            target_scope=target,
            intention_id="intention-1",
            intention_revision=4,
        ),
        "target_scope": target,
        "effect_class": "reversible",
        "risk_class": "low",
        "preconditions": [{"code": "path.within_workspace", "expected": True}],
        "expected_observations": [{"code": "file.hash", "required": True}],
        "idempotency_key": "conversation-1-action-1",
        "timeout_seconds": 30,
        "requested_at_utc": T0,
        "expires_at_utc": T5M,
        "policy_version": "policy.v1",
    }


def _grant_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "grant_id": "grant-1",
        "grant_secret_digest": SECRET_DIGEST,
        "approval_id": "approval-1",
        "policy_decision_id": "decision-1",
        "owner_subject_id": "owner-1",
        "client_instance_hash": H2,
        "runtime_boot_id": "boot-1",
        "intention_id": "intention-1",
        "intention_revision": 4,
        "action_name": "workspace.file.write",
        "parameters_hash": H3,
        "capability": "workspace.write",
        "target_scope_hash": H,
        "risk_class": "high",
        "issued_at_utc": T0,
        "expires_at_utc": T60S,
        "max_uses": 1,
        "uses": 0,
        "issuer_kind": "local_user_approval",
        "policy_version": "policy.v1",
        "safety_revision": 2,
        "state": "issued",
    }


def _approval_payload() -> dict[str, object]:
    preview = {
        "operation": "write file",
        "target": "notes/today.txt",
        "change_summary": "Replace the requested text file.",
    }
    return {
        "schema_version": 1,
        "approval_id": "approval-1",
        "action_request_id": "action-1",
        "action_name": "workspace.file.write",
        "parameters_hash": H,
        "target_summary": "Workspace file notes/today.txt",
        "risk_class": "high",
        "reversibility": "reversible",
        "preview": preview,
        "preview_hash": canonical_preview_hash(preview),
        "owner_subject_id": "owner-1",
        "client_instance_hash": H2,
        "runtime_boot_id": "boot-1",
        "requested_at_utc": T0,
        "expires_at_utc": T5M,
        "state": "pending",
    }


def _resolution_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "decision": "approve",
        "approval_id": "approval-1",
        "action_request_id": "action-1",
        "parameters_hash": H,
        "runtime_boot_id": "boot-1",
        "client_instance_hash": H2,
        "decided_at_utc": T30S,
        "idempotency_key": "approval-resolution-1",
    }


def _receipt_payload() -> dict[str, object]:
    return _seal(
        {
            "schema_version": 1,
            "receipt_id": "receipt-1",
            "chain_id": "identity-1-instance-1-actions",
            "chain_sequence": 1,
            "previous_receipt_hash": GENESIS_RECEIPT_HASH,
            "action_request_id": "action-1",
            "intention_id": "intention-1",
            "intention_revision": 4,
            "source_kind": "conversation_ws",
            "source_ref": "request-1",
            "action_name": "workspace.file.write",
            "parameters_hash": H,
            "target_scope_hash": H2,
            "authorization_decision_id": "decision-1",
            "grant_id_hash": H3,
            "policy_version": "policy.v1",
            "safety_revision": 2,
            "runtime_boot_id": "boot-1",
            "phase": "effect_succeeded",
            "effect_observation": {"code": "file.hash", "value_hash": H},
            "started_at_utc": T0,
            "recorded_at_utc": T30S,
            "duration_ms": 30000,
            "reversibility": "reversible",
            "recovery_material_ref": "snapshot-1",
            "error_code": None,
            "impact_summary": "One workspace file was updated and read back.",
        }
    )


def _recovery_payload(*, method: str = "observe_only") -> dict[str, object]:
    effectful = method in {"compensate", "restore_snapshot"}
    return _seal(
        {
            "schema_version": 1,
            "recovery_receipt_id": "recovery-receipt-1",
            "action_request_id": "action-1",
            "trigger_receipt_id": "receipt-uncertain-1",
            "runtime_boot_id": "boot-2",
            "recovery_attempt_id": "recovery-attempt-1",
            "requested_by": "startup_reconcile",
            "method": method,
            "pre_recovery_observation_hash": H,
            "post_recovery_observation_hash": H2,
            "recovery_action_request_id": "action-recovery-2" if effectful else None,
            "authorization_grant_id_hash": H3 if effectful else None,
            "result": "recovered" if effectful else "no_effect_observed",
            "reexecution_performed": False,
            "limitations": ["Remote state was observable only through its operation identifier."],
            "recorded_at_utc": T30S,
        }
    )


def _safety_payload(*, mode: str = "normal") -> dict[str, object]:
    normal = mode == "normal"
    return _seal(
        {
            "schema_version": 1,
            "safety_snapshot_id": "safety-1",
            "javis_identity_id": "identity-1",
            "instance_id": "instance-1",
            "safety_revision": 3,
            "runtime_boot_id": "boot-1",
            "mode": mode,
            "reason_codes": [] if normal else ["manual_fuse_trip"],
            "effect_admission": "open" if normal else "closed",
            "fuse_tripped": not normal,
            "fuse_reason_code": None if normal else "manual_fuse_trip",
            "journal_health": "healthy",
            "receipt_chain_health": "healthy",
            "approval_authority_health": "healthy",
            "uncertain_action_ids": [],
            "reconcile_cursor": None,
            "last_reconciled_at_utc": None,
            "permission_revision": 8,
            "policy_version": "policy.v1",
            "created_at_utc": T0,
        }
    )


def test_frozen_enum_values_match_the_design() -> None:
    assert {item.value for item in EffectClass} == {
        "none",
        "reversible",
        "compensatable",
        "irreversible",
    }
    assert {item.value for item in RiskClass} == {"low", "medium", "high", "critical"}
    assert "effect_succeeded" in {item.value for item in ActionPhase}
    assert "recovery_required" in {item.value for item in ActionState}
    assert {item.value for item in ApprovalDecision} == {"approve", "deny"}


def test_action_parameters_hash_uses_the_exact_domain_and_canonical_payload() -> None:
    payload = _request_payload()
    canonical = canonical_json_bytes(
        {
            "action_name": payload["action_name"],
            "parameters": payload["normalized_parameters"],
            "target_scope": payload["target_scope"],
            "intention_id": payload["intention_id"],
            "intention_revision": payload["intention_revision"],
        }
    )
    expected = hashlib.sha256(
        f"{JAVIS_ACTION_PARAMETERS_V1}\0".encode("ascii") + canonical
    ).hexdigest()
    assert payload["parameters_hash"] == expected
    changed = copy.deepcopy(payload["normalized_parameters"])
    assert isinstance(changed, dict)
    changed["content"] = "different"
    assert action_parameters_hash(
        action_name=str(payload["action_name"]),
        parameters=changed,
        target_scope=payload["target_scope"],  # type: ignore[arg-type]
        intention_id=str(payload["intention_id"]),
        intention_revision=int(payload["intention_revision"]),
    ) != expected


def test_action_hash_normalizes_nfc_and_rejects_semantic_duplicate_keys() -> None:
    kwargs = {
        "action_name": "workspace.file.write",
        "target_scope": {"relative_path": "notes/a.txt"},
        "intention_id": "intention-1",
        "intention_revision": 1,
    }
    assert action_parameters_hash(parameters={"title": "Cafe\u0301"}, **kwargs) == (
        action_parameters_hash(parameters={"title": "Caf\u00e9"}, **kwargs)
    )
    with pytest.raises(ValueError, match="duplicate semantic keys"):
        action_parameters_hash(parameters={"Name": "a", "name": "b"}, **kwargs)


def test_action_request_client_wire_injects_owner_and_rejects_client_owner() -> None:
    payload = _request_payload()
    client = {key: value for key, value in payload.items() if key != "owner_subject_id"}
    contract = ActionRequestV1.from_client_dict(client, owner_subject_id="trusted-owner")
    assert contract.owner_subject_id == "trusted-owner"
    client["owner_subject_id"] = "attacker"
    with pytest.raises(ValueError, match="server-injected"):
        ActionRequestV1.from_client_dict(client, owner_subject_id="trusted-owner")


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("confirmed", True),
        ("approved", True),
        ("root", True),
        ("permission_override", "all"),
        ("goal_completed", True),
        ("grant_secret", "hidden"),
    ],
)
def test_action_request_rejects_authority_override_fields(field_name: str, value: object) -> None:
    payload = _request_payload()
    parameters = copy.deepcopy(payload["normalized_parameters"])
    assert isinstance(parameters, dict)
    parameters[field_name] = value
    payload["normalized_parameters"] = parameters
    with pytest.raises(ValueError, match="forbidden|secret-bearing"):
        ActionRequestV1.from_dict(payload)


def test_action_request_rejects_unknown_fields_and_hash_tampering() -> None:
    payload = _request_payload()
    payload["unexpected"] = "value"
    with pytest.raises(ValueError, match="unexpected field"):
        ActionRequestV1.from_dict(payload)
    payload = _request_payload()
    payload["parameters_hash"] = H
    with pytest.raises(ValueError, match="does not match"):
        ActionRequestV1.from_dict(payload)


@pytest.mark.parametrize(
    "parameters",
    [
        {"path": "../outside.txt"},
        {"temperature": float("nan")},
        {"password": "visible"},
        {"note": "Authorization: Bearer abc.def.ghi"},
    ],
)
def test_action_parameters_reject_traversal_nonfinite_and_secret_material(
    parameters: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        action_parameters_hash(
            action_name="workspace.file.write",
            parameters=parameters,
            target_scope={"relative_path": "notes/a.txt"},
            intention_id="intention-1",
            intention_revision=1,
        )


def test_action_request_is_deeply_immutable_and_repr_hides_raw_parameters() -> None:
    contract = ActionRequestV1.from_dict(_request_payload())
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        contract.timeout_seconds = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        contract.normalized_parameters["content"] = "changed"  # type: ignore[index]
    rendered = repr(contract)
    assert "status ready" not in rendered
    event = contract.to_event_dict()
    assert "normalized_parameters" not in event
    assert "target_scope" not in event


@pytest.mark.parametrize("bad_value", [0, 2, True, False])
def test_authorization_grant_max_uses_is_literal_integer_one(bad_value: object) -> None:
    payload = _grant_payload()
    payload["max_uses"] = bad_value
    with pytest.raises(ValueError, match="literal integer 1"):
        AuthorizationGrantV1.from_dict(payload)
    assert get_args(get_type_hints(AuthorizationGrantV1)["max_uses"]) == (1,)


def test_authorization_grant_enforces_use_state_and_ttl() -> None:
    grant = AuthorizationGrantV1.from_dict(_grant_payload())
    assert grant.state is GrantState.ISSUED
    payload = _grant_payload()
    payload["uses"] = 1
    with pytest.raises(ValueError, match="issued grants"):
        AuthorizationGrantV1.from_dict(payload)
    payload = _grant_payload()
    payload["risk_class"] = "high"
    payload["expires_at_utc"] = "2026-08-20T12:01:01.000Z"
    with pytest.raises(ValueError, match="within 60 seconds"):
        AuthorizationGrantV1.from_dict(payload)


def test_authorization_grant_never_contains_or_emits_bearer_secret() -> None:
    grant = AuthorizationGrantV1.from_dict(_grant_payload())
    assert "grant_secret" not in {item.name for item in dataclasses.fields(grant)}
    assert SECRET_DIGEST not in repr(grant)
    assert "grant_secret_digest" not in grant.to_event_dict()
    payload = _grant_payload()
    payload["grant_secret"] = "actual-bearer"
    with pytest.raises(ValueError, match="unexpected field"):
        AuthorizationGrantV1.from_dict(payload)


def test_policy_grant_cannot_claim_approval_and_approval_grant_requires_one() -> None:
    payload = _grant_payload()
    payload["issuer_kind"] = "policy"
    with pytest.raises(ValueError, match="must be null"):
        AuthorizationGrantV1.from_dict(payload)
    payload = _grant_payload()
    payload["approval_id"] = None
    with pytest.raises(ValueError, match="required"):
        AuthorizationGrantV1.from_dict(payload)


def test_approval_request_binds_bounded_preview_hash() -> None:
    approval = ApprovalRequestV1.from_dict(_approval_payload())
    assert approval.state is ApprovalState.PENDING
    payload = _approval_payload()
    payload["preview_hash"] = H
    with pytest.raises(ValueError, match="canonical preview"):
        ApprovalRequestV1.from_dict(payload)
    payload = _approval_payload()
    payload["preview"] = {"summary": "x" * (MAX_PREVIEW_BYTES + 1)}
    with pytest.raises(ValueError, match="must not exceed|UTF-8 bytes"):
        ApprovalRequestV1.from_dict(payload)


def test_approval_resolution_uses_decision_enum_not_boolean_authority() -> None:
    resolution = ApprovalResolutionV1.from_dict(_resolution_payload())
    assert resolution.decision is ApprovalDecision.APPROVE
    assert "approved" not in {item.name for item in dataclasses.fields(resolution)}
    payload = _resolution_payload()
    payload.pop("decision")
    payload["approved"] = True
    with pytest.raises(ValueError, match="missing field|unexpected field"):
        ApprovalResolutionV1.from_dict(payload)


def test_action_receipt_hash_is_immutable_and_tamper_evident() -> None:
    receipt = ActionReceiptV1.from_dict(_receipt_payload())
    assert receipt.phase is ActionPhase.EFFECT_SUCCEEDED
    assert canonical_content_hash(receipt) == receipt.content_hash
    payload = _receipt_payload()
    payload["impact_summary"] = "Different impact"
    with pytest.raises(ValueError, match="content_hash"):
        ActionReceiptV1.from_dict(payload)


def test_action_receipt_event_projection_has_no_sensitive_payload() -> None:
    receipt = ActionReceiptV1.from_dict(_receipt_payload())
    event = receipt.to_event_dict()
    assert "effect_observation" not in event
    assert "impact_summary" not in event
    assert "grant_id_hash" in receipt.to_dict()
    assert "grant_id_hash" not in event


def test_genesis_receipt_requires_zero_previous_hash() -> None:
    payload = _receipt_payload()
    payload["previous_receipt_hash"] = H
    payload = _seal(payload)
    with pytest.raises(ValueError, match="genesis"):
        ActionReceiptV1.from_dict(payload)


@pytest.mark.parametrize("method", ["compensate", "restore_snapshot"])
def test_effectful_recovery_requires_new_request_and_grant(method: str) -> None:
    recovery = RecoveryReceiptV1.from_dict(_recovery_payload(method=method))
    assert recovery.recovery_action_request_id == "action-recovery-2"
    payload = _recovery_payload(method=method)
    payload["recovery_action_request_id"] = None
    payload = _seal(payload)
    with pytest.raises(ValueError, match="requires a new request"):
        RecoveryReceiptV1.from_dict(payload)


@pytest.mark.parametrize("bad_value", [True, 0, 1, None])
def test_recovery_v1_forbids_reexecution(bad_value: object) -> None:
    payload = _recovery_payload()
    payload["reexecution_performed"] = bad_value
    payload = _seal(payload)
    with pytest.raises(ValueError, match="literal false"):
        RecoveryReceiptV1.from_dict(payload)


def test_recovery_receipt_rejects_original_request_as_recovery_action() -> None:
    payload = _recovery_payload(method="compensate")
    payload["recovery_action_request_id"] = "action-1"
    payload = _seal(payload)
    with pytest.raises(ValueError, match="new ActionRequest"):
        RecoveryReceiptV1.from_dict(payload)


@pytest.mark.parametrize(
    "mode,admission",
    [
        ("normal", "open"),
        ("safe", "closed"),
        ("reconciling", "closed"),
        ("recovery_only", "recovery_only"),
        ("shutdown", "closed"),
    ],
)
def test_safety_mode_has_one_fail_closed_admission(mode: str, admission: str) -> None:
    payload = _safety_payload(mode="safe" if mode != "normal" else "normal")
    payload["mode"] = mode
    payload["effect_admission"] = admission
    if mode == "reconciling":
        payload["reconcile_cursor"] = "receipt-10"
    payload = _seal(payload)
    snapshot = SafetySnapshotV1.from_dict(payload)
    assert snapshot.effect_admission.value == admission


def test_safety_snapshot_rejects_open_mode_with_unhealthy_authority() -> None:
    payload = _safety_payload()
    payload["approval_authority_health"] = "degraded"
    payload = _seal(payload)
    with pytest.raises(ValueError, match="normal/open"):
        SafetySnapshotV1.from_dict(payload)


def test_safety_snapshot_rejects_unknown_reason_and_hash_tamper() -> None:
    payload = _safety_payload(mode="safe")
    payload["reason_codes"] = ["invented_bypass"]
    payload = _seal(payload)
    with pytest.raises(ValueError, match="allowlist"):
        SafetySnapshotV1.from_dict(payload)
    payload = _safety_payload()
    payload["permission_revision"] = 9
    with pytest.raises(ValueError, match="content_hash"):
        SafetySnapshotV1.from_dict(payload)


@pytest.mark.parametrize(
    "contract_type,payload_factory",
    [
        (ActionRequestV1, _request_payload),
        (AuthorizationGrantV1, _grant_payload),
        (ApprovalRequestV1, _approval_payload),
        (ApprovalResolutionV1, _resolution_payload),
        (ActionReceiptV1, _receipt_payload),
        (RecoveryReceiptV1, _recovery_payload),
        (SafetySnapshotV1, _safety_payload),
    ],
)
def test_all_contracts_are_frozen_strict_and_json_round_trip(
    contract_type: type[object],
    payload_factory: object,
) -> None:
    payload = payload_factory()  # type: ignore[operator]
    contract = contract_type.from_dict(payload)  # type: ignore[attr-defined]
    assert json.loads(contract.canonical_json_bytes()) == contract.to_dict()  # type: ignore[attr-defined]
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        contract.schema_version = 2  # type: ignore[attr-defined]
    unexpected = dict(payload)
    unexpected["unknown"] = 1
    with pytest.raises(ValueError, match="unexpected field"):
        contract_type.from_dict(unexpected)  # type: ignore[attr-defined]


def test_contract_public_forms_have_no_confirmed_or_root_override_fields() -> None:
    forbidden = {"confirmed", "approved", "root", "root_override", "permission_override"}
    for contract_type in (
        ActionRequestV1,
        AuthorizationGrantV1,
        ApprovalRequestV1,
        ApprovalResolutionV1,
        ActionReceiptV1,
        RecoveryReceiptV1,
        SafetySnapshotV1,
    ):
        assert forbidden.isdisjoint({item.name for item in dataclasses.fields(contract_type)})


def test_secret_markers_never_survive_preview_receipt_or_recovery_text() -> None:
    approval = _approval_payload()
    approval["target_summary"] = "password=visible"
    with pytest.raises(ValueError, match="secret material"):
        ApprovalRequestV1.from_dict(approval)
    receipt = _receipt_payload()
    receipt["impact_summary"] = "Bearer abc.def.ghi"
    with pytest.raises(ValueError, match="secret material"):
        ActionReceiptV1.from_dict(receipt)
    recovery = _recovery_payload()
    recovery["limitations"] = ["api_key=visible"]
    with pytest.raises(ValueError, match="secret material"):
        RecoveryReceiptV1.from_dict(recovery)


def test_target_scope_hash_is_canonical_and_path_safe() -> None:
    first = canonical_target_scope_hash({"relative_path": "Cafe\u0301/file.txt"})
    second = canonical_target_scope_hash({"relative_path": "Caf\u00e9/file.txt"})
    assert first == second
    with pytest.raises(ValueError, match="traversal"):
        canonical_target_scope_hash({"relative_path": "safe/../../outside"})
