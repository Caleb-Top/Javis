from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.life.l7.contracts import (
    CandidateState,
    CandidateTransitionV1,
    GrowthCandidateV1,
    GrowthDecisionV1,
    SkillArtifactV1,
    SkillDeploymentV1,
    canonical_content_hash,
    canonical_json_bytes,
)
from core.life.l7.growth_store import (
    GrowthStore,
    GrowthStoreAuthorizationError,
    GrowthStoreCASMismatchError,
    GrowthStoreConflictError,
    GrowthStoreIntegrityError,
)
from core.runtime_access import RuntimeAccessPrincipal


T0 = "2026-08-20T01:00:00.000Z"
T1 = "2026-08-20T01:00:01.000Z"
T2 = "2026-08-20T01:00:02.000Z"
T3 = "2026-08-20T01:00:03.000Z"
T4 = "2026-08-20T01:00:04.000Z"
T5 = "2026-08-20T01:00:05.000Z"
T6 = "2026-08-20T01:00:06.000Z"
T7 = "2026-08-20T01:00:07.000Z"
T8 = "2026-08-20T01:00:08.000Z"
END = "2026-08-21T01:00:00.000Z"
HASH_A = "a" * 64
PERSISTENCE = {
    "privacy_class": "local_internal",
    "retention_class": "operational",
    "provenance": {"adapter": "test", "source_ids": ["source-1"]},
}
PAYLOAD = b"immutable local skill archive"
MANIFEST = b'{"entrypoint":"main"}'
PAYLOAD_HASH = hashlib.sha256(PAYLOAD).hexdigest()
MANIFEST_HASH = hashlib.sha256(MANIFEST).hexdigest()
CLIENT_HASH = hashlib.sha256(b"desktop-client").hexdigest()
OWNER = "subject-primary-" + CLIENT_HASH[:32]


def _signed(value: dict) -> dict:
    result = dict(value)
    result["content_hash"] = canonical_content_hash(result)
    return result


def _candidate_wire(version: int = 1, **overrides) -> dict:
    value = {
        "schema_version": 1,
        "candidate_id": "candidate-1",
        "candidate_version_id": f"candidate-version-{version}",
        "version_number": version,
        "previous_version_id": None,
        "previous_content_hash": None,
        "identity_id": "javis-1",
        "instance_id": "instance-1",
        "owner_subject_id": OWNER,
        "candidate_kind": "skill",
        "title": "Bounded local skill",
        "bounded_summary": f"Immutable candidate version {version}.",
        "source_evidence_ids": ["evidence-1"],
        "source_evidence_hash": HASH_A,
        "minimum_evidence_count": 1,
        "evidence_window": {"start_at_utc": T0, "end_at_utc": T1},
        "expected_benefit": {"metric": "latency", "direction": "lower"},
        "applicability": {"platforms": ["windows"]},
        "assumptions": ["local runtime available"],
        "risk_level": "medium",
        "risk_reasons": ["requests one local capability"],
        "permission_delta": {"added": ["workspace.read"], "removed": []},
        "validation_plan": {"test_suites": ["contract"]},
        "acceptance_thresholds": {"pass_rate": 1.0},
        "rollback_plan": {"kind": "pointer_restore"},
        "rollback_artifact_refs": [],
        "requires_user_approval": True,
        "created_by": "growth-proposer-1",
        "created_at_utc": T0 if version == 1 else T8,
        "expires_at_utc": END,
        **PERSISTENCE,
    }
    value.update(overrides)
    return _signed(value)


def _candidate(version: int = 1, **overrides) -> GrowthCandidateV1:
    return GrowthCandidateV1.from_dict(_candidate_wire(version, **overrides))


def _transition(
    *,
    from_state: str,
    to_state: str,
    revision: int,
    timestamp: str,
    actor_kind: str = "system",
    actor_id: str = "growth-store-1",
    decision_id: str | None = None,
    receipt_ids: list[str] | None = None,
    artifact_id: str | None = None,
    suffix: str | None = None,
) -> CandidateTransitionV1:
    name = suffix or to_state
    return CandidateTransitionV1.from_dict(
        _signed(
            {
                "schema_version": 1,
                "transition_id": f"transition-{name}",
                "candidate_version_id": "candidate-version-1",
                "from_state": from_state,
                "to_state": to_state,
                "expected_revision": revision,
                "actor_kind": actor_kind,
                "actor_id": actor_id,
                "decision_id": decision_id,
                "reason_code": f"test.{name}",
                "receipt_ids": receipt_ids or [],
                "artifact_id": artifact_id,
                "created_at_utc": timestamp,
                "idempotency_key": f"idem-{name}",
            }
        )
    )


def _artifact(**overrides) -> SkillArtifactV1:
    value = {
        "schema_version": 1,
        "artifact_id": "artifact-1",
        "artifact_kind": "skill",
        "candidate_version_id": "candidate-version-1",
        "skill_name": "local_skill",
        "skill_version": "1.0.0",
        "payload_sha256": PAYLOAD_HASH,
        "manifest_sha256": MANIFEST_HASH,
        "total_bytes": len(PAYLOAD),
        "file_count": 2,
        "entrypoints": {"python": "main"},
        "source_manifest": {"files": ["skill.py", "manifest.json"]},
        "source_archive_sha256": HASH_A,
        "license_spdx": "MIT",
        "attribution_refs": ["attribution-1"],
        "sbom_ref": "sbom-1",
        "dependency_lock_hash": HASH_A,
        "required_capabilities": ["workspace.read"],
        "sandbox_policy_version": "1",
        "build_receipt_id": "build-receipt-1",
        "test_receipt_ids": ["test-receipt-1"],
        "compatibility_range": {"minimum_release": "1.0.0"},
        "created_at_utc": T3,
        "created_by_forge_version": "1.0.0",
        "privacy_class": "local_internal",
        "retention_class": "audit",
    }
    value.update(overrides)
    return SkillArtifactV1.from_dict(_signed(value))


def _principal(*, scopes=("evolution.write",), binding_source="packaged_desktop"):
    return RuntimeAccessPrincipal(
        runtime_boot_id="boot-1",
        client_id_hash=CLIENT_HASH,
        scopes=scopes,
        issued_at_epoch=1.0,
        expires_at_epoch=100.0,
        binding_source=binding_source,
    )


def _permission_hash(candidate: GrowthCandidateV1) -> str:
    return hashlib.sha256(
        canonical_json_bytes(candidate.to_dict()["permission_delta"])
    ).hexdigest()


def _decision(candidate: GrowthCandidateV1, *, kind="approve", revision=5, **overrides):
    value = {
        "schema_version": 1,
        "decision_id": f"decision-{kind}",
        "candidate_version_id": candidate.candidate_version_id,
        "decision_kind": kind,
        "actor_subject_id": OWNER,
        "expected_revision": revision,
        "candidate_content_hash": candidate.content_hash,
        "artifact_id": "artifact-1",
        "artifact_payload_sha256": PAYLOAD_HASH,
        "test_receipt_ids": ["test-receipt-1"],
        "permission_delta_hash": _permission_hash(candidate),
        "reason_code": f"user.{kind}",
        "created_at_utc": T5 if kind != "revoke" else T8,
        "idempotency_key": f"idem-decision-{kind}",
    }
    value.update(overrides)
    return GrowthDecisionV1.from_dict(_signed(value))


def _deployment(**overrides) -> SkillDeploymentV1:
    value = {
        "schema_version": 1,
        "deployment_id": "deployment-1",
        "skill_name": "local_skill",
        "artifact_id": "artifact-1",
        "artifact_payload_sha256": PAYLOAD_HASH,
        "candidate_version_id": "candidate-version-1",
        "catalog_record_id": "catalog-1",
        "catalog_status_required": "active",
        "approval_id": "decision-approve",
        "required_capabilities": ["workspace.read"],
        "granted_capability_refs": ["grant-1"],
        "previous_deployment_id": None,
        "deployed_at_utc": T7,
        "deployed_by": OWNER,
        "rollback_receipt_id": None,
        "revision": 7,
    }
    value.update(overrides)
    return SkillDeploymentV1.from_dict(_signed(value))


def _advance_to_sandbox(store: GrowthStore) -> GrowthCandidateV1:
    candidate = _candidate()
    store.create_candidate(candidate)
    store.append_transition(
        _transition(from_state="proposed", to_state="evidence_ready", revision=1, timestamp=T1)
    )
    store.append_transition(
        _transition(from_state="evidence_ready", to_state="sandboxing", revision=2, timestamp=T2)
    )
    return candidate


def _advance_to_approval(store: GrowthStore) -> GrowthCandidateV1:
    candidate = _advance_to_sandbox(store)
    store.store_artifact(_artifact(), PAYLOAD, MANIFEST)
    store.append_transition(
        _transition(
            from_state="sandboxing",
            to_state="validated",
            revision=3,
            timestamp=T4,
            receipt_ids=["test-receipt-1"],
            artifact_id="artifact-1",
        )
    )
    store.append_transition(
        _transition(
            from_state="validated",
            to_state="awaiting_approval",
            revision=4,
            timestamp=T5,
            receipt_ids=["test-receipt-1"],
            artifact_id="artifact-1",
        )
    )
    return candidate


def _advance_to_active(store: GrowthStore) -> GrowthCandidateV1:
    candidate = _advance_to_approval(store)
    store.record_decision(_decision(candidate), principal=_principal())
    store.append_transition(
        _transition(
            from_state="awaiting_approval",
            to_state="approved",
            revision=5,
            timestamp=T6,
            actor_kind="user",
            actor_id=OWNER,
            decision_id="decision-approve",
            artifact_id="artifact-1",
        )
    )
    store.append_transition(
        _transition(
            from_state="approved",
            to_state="deploying",
            revision=6,
            timestamp=T7,
            artifact_id="artifact-1",
        )
    )
    store.record_deployment(_deployment())
    store.append_transition(
        _transition(
            from_state="deploying",
            to_state="active",
            revision=7,
            timestamp=T8,
            artifact_id="artifact-1",
        )
    )
    return candidate


def test_fixed_layout_wal_bounded_api_and_no_sql_escape(tmp_path: Path):
    store = GrowthStore(tmp_path)

    assert store.path == tmp_path / "growth" / "candidates" / "growth.sqlite3"
    assert store.connection_settings() == {
        "journal_mode": "wal",
        "foreign_keys": 1,
        "query_only": 1,
        "schema_version": 1,
    }
    assert not hasattr(store, "execute")
    with pytest.raises(ValueError, match="limit"):
        store.list_candidates(limit=501)


def test_candidate_create_is_idempotent_and_history_rejects_update(tmp_path: Path):
    store = GrowthStore(tmp_path)
    candidate = _candidate()

    assert store.create_candidate(candidate) == store.create_candidate(candidate)
    with pytest.raises(GrowthStoreConflictError, match="different content"):
        store.create_candidate(_candidate(title="Different immutable body"))

    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE candidate_versions SET content_hash = ?", ("b" * 64,))


def test_next_version_requires_exact_monotonic_head_and_cas(tmp_path: Path):
    store = GrowthStore(tmp_path)
    first = _candidate()
    store.create_candidate(first)
    second = _candidate(
        2,
        previous_version_id=first.candidate_version_id,
        previous_content_hash=first.content_hash,
    )
    snapshot = store.create_next_version(second, expected_head_revision=1)

    assert snapshot.candidate == second
    assert store.get_head("candidate-1").revision == 2
    assert [item.candidate.version_number for item in store.list_candidate_versions("candidate-1")] == [2, 1]

    third = _candidate(
        3,
        previous_version_id=second.candidate_version_id,
        previous_content_hash=second.content_hash,
    )
    with pytest.raises(GrowthStoreCASMismatchError, match="stale"):
        store.create_next_version(third, expected_head_revision=1)


def test_concurrent_next_version_has_one_head_winner(tmp_path: Path):
    store = GrowthStore(tmp_path)
    first = _candidate()
    store.create_candidate(first)

    def create(version_id: str):
        try:
            return store.create_next_version(
                _candidate(
                    2,
                    candidate_version_id=version_id,
                    previous_version_id=first.candidate_version_id,
                    previous_content_hash=first.content_hash,
                ),
                expected_head_revision=1,
            )
        except (GrowthStoreCASMismatchError, GrowthStoreConflictError):
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(create, ("candidate-version-2a", "candidate-version-2b")))
    assert len([item for item in results if item is not None]) == 1
    assert store.get_head("candidate-1").revision == 2


def test_model_may_propose_but_cannot_transition_or_decide(tmp_path: Path):
    store = GrowthStore(tmp_path)
    store.create_candidate(_candidate())
    transition = _transition(
        from_state="proposed",
        to_state="evidence_ready",
        revision=1,
        timestamp=T1,
        actor_kind="model_proposer",
        actor_id="model-1",
    )
    with pytest.raises(GrowthStoreAuthorizationError, match="only create"):
        store.append_transition(transition)


def test_content_addressed_artifact_detects_wrong_bytes_and_tamper_on_restart(tmp_path: Path):
    store = GrowthStore(tmp_path)
    _advance_to_sandbox(store)
    artifact = _artifact()

    with pytest.raises(GrowthStoreConflictError, match="payload hash"):
        store.store_artifact(artifact, b"wrong", MANIFEST)
    store.store_artifact(artifact, PAYLOAD, MANIFEST)
    assert store.read_artifact_payload("artifact-1") == PAYLOAD
    payload_path = (
        tmp_path / "growth" / "artifacts" / "sha256" / PAYLOAD_HASH[:2] / PAYLOAD_HASH / "payload.bin"
    )
    payload_path.write_bytes(b"tampered")
    with pytest.raises(GrowthStoreIntegrityError, match="integrity"):
        store.read_artifact_payload("artifact-1")
    store.close()
    with pytest.raises(GrowthStoreIntegrityError, match="integrity verification"):
        GrowthStore(tmp_path)


def test_unknown_candidate_cannot_create_artifact_content(tmp_path: Path):
    store = GrowthStore(tmp_path)
    artifact = _artifact(candidate_version_id="candidate-version-missing")

    with pytest.raises(GrowthStoreConflictError, match="does not exist"):
        store.store_artifact(artifact, PAYLOAD, MANIFEST)
    assert not (
        tmp_path / "growth" / "artifacts" / "sha256" / PAYLOAD_HASH[:2] / PAYLOAD_HASH
    ).exists()


def test_approval_requires_server_bound_local_owner_scope_and_exact_receipts(tmp_path: Path):
    store = GrowthStore(tmp_path)
    candidate = _advance_to_approval(store)
    decision = _decision(candidate)

    with pytest.raises(GrowthStoreAuthorizationError, match="scope"):
        store.record_decision(decision, principal=_principal(scopes=("life.read",)))
    with pytest.raises(GrowthStoreAuthorizationError, match="actor"):
        wrong = RuntimeAccessPrincipal(
            runtime_boot_id="boot-1",
            client_id_hash="b" * 64,
            scopes=("evolution.write",),
            issued_at_epoch=1.0,
            expires_at_epoch=100.0,
            binding_source="packaged_desktop",
        )
        store.record_decision(decision, principal=wrong)
    with pytest.raises(GrowthStoreConflictError, match="exact artifact receipts"):
        store.record_decision(
            _decision(candidate, test_receipt_ids=["other-receipt"]),
            principal=_principal(),
        )

    assert store.record_decision(decision, principal=_principal()) == decision


def test_exact_deployment_gate_and_revoke_fail_closed(tmp_path: Path):
    store = GrowthStore(tmp_path)
    candidate = _advance_to_active(store)

    accepted = store.deployment_gate(
        candidate_version_id=candidate.candidate_version_id,
        artifact_id="artifact-1",
        artifact_payload_sha256=PAYLOAD_HASH,
        catalog_record_id="catalog-1",
        approval_id="decision-approve",
    )
    assert accepted.allowed
    assert accepted.deployment == _deployment()
    assert not store.deployment_gate(
        candidate_version_id=candidate.candidate_version_id,
        artifact_id="artifact-1",
        artifact_payload_sha256=PAYLOAD_HASH,
        catalog_record_id="catalog-other",
        approval_id="decision-approve",
    ).allowed
    assert not store.deployment_gate(
        candidate_version_id=candidate.candidate_version_id,
        artifact_id="artifact-1",
        artifact_payload_sha256=PAYLOAD_HASH,
        catalog_record_id="catalog-1",
        approval_id="decision-approve",
        catalog_active=False,
    ).allowed

    revoke = _decision(candidate, kind="revoke", revision=8)
    store.record_decision(revoke, principal=_principal())
    revoked = store.append_transition(
        _transition(
            from_state="active",
            to_state="revoked",
            revision=8,
            timestamp=T8,
            actor_kind="user",
            actor_id=OWNER,
            decision_id="decision-revoke",
            suffix="revoke",
        )
    )
    assert revoked.state is CandidateState.REVOKED
    assert not store.deployment_gate(
        candidate_version_id=candidate.candidate_version_id,
        artifact_id="artifact-1",
        artifact_payload_sha256=PAYLOAD_HASH,
        catalog_record_id="catalog-1",
        approval_id="decision-approve",
    ).allowed


def test_replays_remain_idempotent_after_candidate_advances(tmp_path: Path):
    store = GrowthStore(tmp_path)
    candidate = _advance_to_active(store)

    assert store.store_artifact(_artifact(), PAYLOAD, MANIFEST) == _artifact()
    assert store.record_decision(_decision(candidate), principal=_principal()) == _decision(candidate)
    assert store.record_deployment(_deployment()) == _deployment()
    replay = store.append_transition(
        _transition(
            from_state="awaiting_approval",
            to_state="approved",
            revision=5,
            timestamp=T6,
            actor_kind="user",
            actor_id=OWNER,
            decision_id="decision-approve",
            artifact_id="artifact-1",
        )
    )
    assert replay.state is CandidateState.ACTIVE


def test_rollback_is_append_only_receipted_and_closes_deployment_gate(tmp_path: Path):
    store = GrowthStore(tmp_path)
    candidate = _advance_to_active(store)
    rolling_back = store.append_transition(
        _transition(
            from_state="active",
            to_state="rolling_back",
            revision=8,
            timestamp=T8,
            receipt_ids=["rollback-start-1"],
            artifact_id="artifact-1",
        )
    )
    assert rolling_back.state is CandidateState.ROLLING_BACK
    rollback_deployment = _deployment(
        deployment_id="deployment-rollback-1",
        previous_deployment_id="deployment-1",
        rollback_receipt_id="rollback-receipt-1",
        revision=9,
    )
    store.record_deployment(rollback_deployment)
    rolled_back = store.append_transition(
        _transition(
            from_state="rolling_back",
            to_state="rolled_back",
            revision=9,
            timestamp=T8,
            receipt_ids=["rollback-receipt-1"],
            artifact_id="artifact-1",
        )
    )
    assert rolled_back.state is CandidateState.ROLLED_BACK
    assert store.list_deployments(candidate.candidate_version_id) == (
        _deployment(),
        rollback_deployment,
    )
    assert not store.deployment_gate(
        candidate_version_id=candidate.candidate_version_id,
        artifact_id="artifact-1",
        artifact_payload_sha256=PAYLOAD_HASH,
        catalog_record_id="catalog-1",
        approval_id="decision-approve",
    ).allowed


def test_old_approval_cannot_authorize_changed_candidate_version(tmp_path: Path):
    store = GrowthStore(tmp_path)
    first = _advance_to_active(store)
    second = _candidate(
        2,
        previous_version_id=first.candidate_version_id,
        previous_content_hash=first.content_hash,
    )
    store.create_next_version(second, expected_head_revision=1)

    result = store.deployment_gate(
        candidate_version_id=second.candidate_version_id,
        artifact_id="artifact-1",
        artifact_payload_sha256=PAYLOAD_HASH,
        catalog_record_id="catalog-1",
        approval_id="decision-approve",
    )
    assert not result.allowed
    assert result.reason_code == "artifact_mismatch"


def test_restart_revalidates_hash_chain_and_projection(tmp_path: Path):
    store = GrowthStore(tmp_path)
    _advance_to_approval(store)
    assert store.verify_integrity().ok
    store.close()

    reopened = GrowthStore(tmp_path)
    assert reopened.verify_integrity().ok
    reopened.close()
    with sqlite3.connect(tmp_path / "growth" / "candidates" / "growth.sqlite3") as db:
        db.execute("DROP TRIGGER prevent_candidate_projection_update") if False else None
        db.execute(
            "UPDATE candidate_projection SET revision = revision + 1 WHERE candidate_version_id = ?",
            ("candidate-version-1",),
        )
    with pytest.raises(GrowthStoreIntegrityError, match="integrity verification"):
        GrowthStore(tmp_path)


def test_queries_are_bounded_and_append_events_are_ordered(tmp_path: Path):
    store = GrowthStore(tmp_path)
    _advance_to_approval(store)

    assert [item.to_state.value for item in store.list_transitions("candidate-version-1")] == [
        "evidence_ready",
        "sandboxing",
        "validated",
        "awaiting_approval",
    ]
    assert store.list_artifacts("candidate-version-1") == (_artifact(),)
    with pytest.raises(ValueError, match="limit"):
        store.list_decisions("candidate-version-1", limit=0)
