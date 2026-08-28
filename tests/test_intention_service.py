from __future__ import annotations

import copy
import threading
from datetime import datetime, timezone

import pytest

from core.intention.contracts import (
    BlockerV1,
    IntentionV1,
    VerificationRecordV1,
    canonical_content_hash,
)
from core.intention.service import IntentionService, IntentionServiceError
from core.intention.store import IntentionStore
from core.life.memory.contracts import AccessContext


T0 = "2026-08-20T12:00:00.000Z"
T05 = "2026-08-20T12:05:00.000Z"
T10 = "2026-08-20T12:10:00.000Z"
T20 = "2026-08-20T12:20:00.000Z"
T30 = "2026-08-20T12:30:00.000Z"
T60 = "2026-08-20T13:00:00.000Z"
T120 = "2026-08-20T14:00:00.000Z"


def _seal(payload: dict[str, object]) -> dict[str, object]:
    wire = copy.deepcopy(payload)
    wire.pop("content_hash", None)
    wire["content_hash"] = canonical_content_hash(wire)
    return wire


def _intention(
    *,
    intention_id: str = "intention-1",
    state: str = "candidate",
    owner: str = "subject-owner",
    boot: str = "boot-1",
    max_attempts: int = 3,
    audience: str = "owner_private",
) -> IntentionV1:
    return IntentionV1.from_dict(
        _seal(
            {
                "schema_version": 1,
                "intention_id": intention_id,
                "javis_identity_id": "identity-1",
                "instance_id": "instance-1",
                "owner_subject_id": owner,
                "participant_ids": [owner],
                "audience": audience,
                "source_event_ids": [f"event-{intention_id}"],
                "runtime_boot_id": boot,
                "created_at_utc": T0,
                "updated_at_utc": T0,
                "expires_at_utc": T120,
                "privacy_class": "user_private",
                "retention_class": "continuity",
                "state": state,
                "revision": 1,
                "provenance": "conversation",
                "kind": "request",
                "trigger_kind": "explicit_command",
                "why_summary": "The owner explicitly requested a bounded result.",
                "expected_user_value": "The result is verified.",
                "goal_statement": "Produce and verify the requested result.",
                "success_criteria": [
                    {
                        "schema_version": 1,
                        "criterion_id": "criterion-required",
                        "description": "Read-back evidence matches the requested result.",
                        "required": True,
                        "verifier_kind": "deterministic",
                        "evidence_requirements": ["file.readback"],
                        "freshness_seconds": 3600,
                        "subjective": False,
                        "user_confirmation_required": None,
                    },
                    {
                        "schema_version": 1,
                        "criterion_id": "criterion-optional",
                        "description": "An optional secondary observation is available.",
                        "required": False,
                        "verifier_kind": "trusted_adapter",
                        "evidence_requirements": ["adapter.observation"],
                        "freshness_seconds": 3600,
                        "subjective": False,
                        "user_confirmation_required": None,
                    },
                ],
                "execution_budget": {
                    "schema_version": 1,
                    "max_plan_steps": 8,
                    "max_action_attempts": max_attempts,
                    "max_wall_seconds": 900,
                    "max_model_tokens": 8192,
                },
                "risk_ceiling": "low",
                "capability_hints": ["workspace.write", "workspace.read"],
                "urgency": "normal",
                "interruption_cost": "low",
                "valid_until_utc": T120,
                "cancellation_conditions": ["user.cancelled"],
                "commitment_id": None,
                "latest_checkpoint_id": None,
                "latest_verification_id": None,
                "state_reason_code": f"state.{state}",
                "supersedes_intention_id": None,
            }
        )
    )


def _access(
    *,
    owner: str = "subject-owner",
    boot: str = "boot-1",
    audience: str = "owner_private",
    scopes: tuple[str, ...] = ("intent.write",),
    expires: str = T120,
) -> AccessContext:
    return AccessContext.from_dict(
        {
            "schema_version": 1,
            "context_id": "access-1",
            "runtime_boot_id": boot,
            "client_id_hash": "a" * 64,
            "capability_scopes": list(scopes),
            "actor_subject_id": owner,
            "actor_kind": "primary_user",
            "session_id": "session-1",
            "participant_subject_ids": [owner],
            "audience_ceiling": audience,
            "identity_assurance": "desktop_confirmed",
            "purpose": "manage",
            "acl_epoch": 1,
            "issued_at_utc": T0,
            "expires_at_utc": expires,
        }
    )


def _verification(intention: IntentionV1, *, outcome: str = "satisfied") -> VerificationRecordV1:
    criterion_outcome = "satisfied" if outcome == "satisfied" else "unsatisfied"
    evidence = ["receipt-readback"] if criterion_outcome == "satisfied" else []
    return VerificationRecordV1.from_dict(
        _seal(
            {
                "schema_version": 1,
                "verification_id": "verification-1",
                "javis_identity_id": intention.javis_identity_id,
                "instance_id": intention.instance_id,
                "owner_subject_id": intention.owner_subject_id,
                "participant_ids": list(intention.participant_ids),
                "audience": intention.audience.value,
                "source_event_ids": list(intention.source_event_ids),
                "runtime_boot_id": intention.runtime_boot_id,
                "created_at_utc": T20,
                "updated_at_utc": T20,
                "expires_at_utc": T60,
                "privacy_class": "user_private",
                "retention_class": "audit",
                "state": outcome,
                "revision": 1,
                "provenance": "goal.verifier",
                "intention_id": intention.intention_id,
                "intention_revision": intention.revision,
                "commitment_id": intention.commitment_id,
                "requested_by": "intention_service",
                "verifier_kind": "deterministic",
                "verifier_version": "exact.readback.v1",
                "criterion_results": [
                    {
                        "schema_version": 1,
                        "criterion_id": "criterion-required",
                        "outcome": criterion_outcome,
                        "evidence_refs": evidence,
                        "fresh_until_utc": T60,
                        "method": "exact.content.readback",
                        "explanation_code": "content.matches",
                    }
                ],
                "evidence_refs": evidence,
                "observed_at_utc": T10,
                "result": outcome,
                "limitations": [],
                "conflicting_evidence_refs": [],
            }
        )
    )


@pytest.fixture
def clock() -> list[datetime]:
    return [datetime(2026, 8, 20, 12, 10, tzinfo=timezone.utc)]


@pytest.fixture
def service(tmp_path, clock):
    store = IntentionStore(tmp_path)
    counter = iter(range(1, 1000))
    value = IntentionService(
        store,
        runtime_boot_id="boot-1",
        now=lambda: clock[0],
        id_factory=lambda prefix: f"{prefix}-{next(counter)}",
    )
    try:
        yield value
    finally:
        store.close()


def _accept_and_activate(service: IntentionService, *, max_attempts: int = 3):
    accepted = service.accept_request(
        _intention(state="accepted", max_attempts=max_attempts),
        access=_access(),
        idempotency_key="accept-1",
    )
    return service.activate(
        accepted.intention.intention_id,
        expected_revision=1,
        access=_access(),
        idempotency_key="activate-1",
    )


def test_candidate_acceptance_creates_commitment_in_same_store_command(service):
    created = service.create_candidate(
        _intention(), access=_access(), idempotency_key="candidate-1"
    )
    accepted = service.accept_candidate(
        created.intention.intention_id,
        expected_revision=1,
        access=_access(),
        idempotency_key="candidate-accept-1",
        evidence_refs=("event-intention-1",),
    )

    assert accepted.intention.state.value == "accepted"
    assert accepted.intention.commitment_id == accepted.commitment.commitment_id
    assert accepted.commitment.intention_id == accepted.intention.intention_id
    assert service._store.scan_integrity().ok


def test_full_lifecycle_completes_only_through_fresh_required_verification(service):
    active = _accept_and_activate(service)
    waiting = service.set_waiting(
        active.intention.intention_id,
        expected_revision=2,
        access=_access(),
        idempotency_key="waiting-1",
        next_review_at_utc=T30,
    )
    resumed = service.resume_after_evidence(
        waiting.intention.intention_id,
        expected_revision=3,
        access=_access(),
        idempotency_key="resume-1",
        evidence_refs=("receipt-user-ready",),
    )
    verifying = service.begin_verification(
        resumed.intention.intention_id,
        expected_revision=4,
        access=_access(),
        idempotency_key="verify-1",
        evidence_refs=("receipt-action",),
    )
    completed = service.apply_verification(
        _verification(verifying.intention),
        expected_revision=5,
        access=_access(),
        idempotency_key="verification-apply-1",
    )

    assert completed.intention.state.value == "completed"
    assert completed.commitment.state.value == "fulfilled"
    assert completed.intention.latest_verification_id == "verification-1"
    assert completed.commitment.verification_record_ids == ("verification-1",)
    assert service._store.get_verification("verification-1") is not None
    assert service._store.scan_integrity().ok


def test_unsatisfied_verification_cannot_complete_or_mutate_state(service):
    active = _accept_and_activate(service)
    verifying = service.begin_verification(
        active.intention.intention_id,
        expected_revision=2,
        access=_access(),
        idempotency_key="verify-1",
        evidence_refs=("receipt-action",),
    )

    with pytest.raises(IntentionServiceError, match="verification_not_satisfied"):
        service.apply_verification(
            _verification(verifying.intention, outcome="unsatisfied"),
            expected_revision=3,
            access=_access(),
            idempotency_key="verification-bad-1",
        )

    current = service._store.get_intention(verifying.intention.intention_id)
    assert current.state.value == "verifying"
    assert current.latest_verification_id is None


@pytest.mark.parametrize(
    ("access", "reason"),
    [
        (_access(owner="subject-other"), "cross_owner_denied"),
        (_access(boot="boot-other"), "cross_boot_denied"),
        (_access(scopes=("intent.read",)), "scope_denied"),
        (_access(audience="guest"), "audience_denied"),
        (_access(expires=T05), "access_context_expired"),
    ],
)
def test_server_access_owner_audience_scope_boot_and_expiry_are_enforced(
    service, access, reason
):
    created = service.create_candidate(
        _intention(), access=_access(), idempotency_key="candidate-1"
    )
    with pytest.raises(IntentionServiceError, match=reason):
        service.accept_candidate(
            created.intention.intention_id,
            expected_revision=1,
            access=access,
            idempotency_key=f"denied-{reason}",
        )


def test_stale_revision_terminal_reopen_and_late_receipt_leave_code_only_audit(service):
    active = _accept_and_activate(service)
    cancelled = service.cancel(
        active.intention.intention_id,
        expected_revision=2,
        access=_access(),
        idempotency_key="cancel-1",
    )
    with pytest.raises(IntentionServiceError, match="stale_revision"):
        service.cancel(
            active.intention.intention_id,
            expected_revision=2,
            access=_access(),
            idempotency_key="cancel-stale",
        )
    with pytest.raises(IntentionServiceError, match="terminal_state_closed"):
        service.attach_receipt(
            cancelled.intention.intention_id,
            "late-receipt",
            expected_revision=3,
            access=_access(),
            idempotency_key="late-receipt-1",
        )

    serialized = repr(service.audit_codes())
    assert "Produce and verify" not in serialized
    assert "late-receipt" not in serialized
    assert "stale_revision" in serialized
    assert "terminal_state_closed" in serialized


def test_blocked_state_requires_structured_evidence_and_resumes(service):
    active = _accept_and_activate(service)
    blocked = service.set_blocked(
        active.intention.intention_id,
        expected_revision=2,
        access=_access(),
        idempotency_key="blocked-1",
        blockers=(BlockerV1("dependency.unavailable", ("receipt-dependency",)),),
        next_review_at_utc=T30,
        evidence_refs=("receipt-dependency",),
    )
    assert blocked.commitment.current_blockers[0].code == "dependency.unavailable"
    resumed = service.resume_after_evidence(
        blocked.intention.intention_id,
        expected_revision=3,
        access=_access(),
        idempotency_key="resumed-1",
        evidence_refs=("receipt-dependency-restored",),
    )
    assert resumed.intention.state.value == "active"
    assert resumed.commitment.current_blockers == ()


def test_action_attempt_budget_is_atomic_and_idempotent_under_concurrency(service):
    active = _accept_and_activate(service, max_attempts=3)
    results = []
    errors = []
    lock = threading.Lock()

    def reserve(index: int) -> None:
        try:
            result = service.reserve_action_attempt(
                active.intention.intention_id,
                expected_revision=2,
                access=_access(),
                idempotency_key=f"attempt-{index}",
            )
            with lock:
                results.append(result)
        except IntentionServiceError as exc:
            with lock:
                errors.append(exc.reason_code)

    threads = [threading.Thread(target=reserve, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(item.attempt_number for item in results) == [1, 2, 3]
    assert errors == ["action_attempt_budget_exhausted"] * 9
    replay = service.reserve_action_attempt(
        active.intention.intention_id,
        expected_revision=2,
        access=_access(),
        idempotency_key="attempt-0",
    )
    assert replay.attempt_number in {1, 2, 3}


def test_attach_receipt_updates_commitment_without_changing_intention_revision(service):
    active = _accept_and_activate(service)
    commitment = service.attach_receipt(
        active.intention.intention_id,
        "receipt-action-1",
        expected_revision=2,
        access=_access(),
        idempotency_key="attach-1",
    )
    assert commitment.last_action_receipt_id == "receipt-action-1"
    assert service._store.get_intention(active.intention.intention_id).revision == 2
    assert service._store.scan_integrity().ok


def test_idempotency_replays_same_result_and_rejects_changed_payload(service):
    active = _accept_and_activate(service)
    first = service.cancel(
        active.intention.intention_id,
        expected_revision=2,
        access=_access(),
        idempotency_key="cancel-idempotent",
    )
    replay = service.cancel(
        active.intention.intention_id,
        expected_revision=2,
        access=_access(),
        idempotency_key="cancel-idempotent",
    )
    assert replay == first
    with pytest.raises(IntentionServiceError, match="idempotency_conflict"):
        service.cancel(
            active.intention.intention_id,
            expected_revision=2,
            access=_access(),
            idempotency_key="cancel-idempotent",
            reason_code="policy.cancelled",
        )


def test_expire_due_rejects_early_and_expires_at_boundary(service, clock):
    created = service.create_candidate(
        _intention(), access=_access(), idempotency_key="candidate-1"
    )
    with pytest.raises(IntentionServiceError, match="intention_not_due"):
        service.expire_due(
            created.intention.intention_id,
            expected_revision=1,
            access=_access(),
            idempotency_key="expire-early",
        )
    clock[0] = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    access = _access(expires="2026-08-20T15:00:00.000Z")
    expired = service.expire_due(
        created.intention.intention_id,
        expected_revision=1,
        access=access,
        idempotency_key="expire-due",
    )
    assert expired.intention.state.value == "expired"
