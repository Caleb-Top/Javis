"""Governed L5 intention commands and deterministic state transitions.

The service is the only policy layer allowed to turn an authenticated command
into a store mutation. It deliberately accepts typed contracts instead of raw
request bodies and records bounded reason codes rather than user or model text.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.life.memory.contracts import (
    AccessContext,
    AccessPurpose,
    ActorKind,
    Audience as MemoryAudience,
)

from .contracts import (
    AudienceScope,
    BlockerV1,
    CommitmentState,
    CommitmentV1,
    CriterionOutcome,
    IntentionState,
    IntentionTransitionEventV1,
    IntentionV1,
    ResumePolicy,
    TERMINAL_INTENTION_STATES,
    VerificationRecordV1,
    VerificationResult,
    canonical_content_hash,
    canonical_json_bytes,
    is_valid_intention_transition,
)
from .store import (
    IntentionStore,
    IntentionStoreConflictError,
    IntentionStoreIdempotencyConflictError,
    PersistCommitmentCommand,
    PersistIntentionCommand,
    PersistVerificationCommand,
)


_AUDIENCE_RANK = {
    MemoryAudience.GUEST: 0,
    MemoryAudience.OWNER_PRIVATE: 1,
    MemoryAudience.PARTICIPANTS: 2,
    MemoryAudience.EXPLICIT_SHARED: 3,
}
_INTENTION_AUDIENCE = {
    AudienceScope.GUEST: MemoryAudience.GUEST,
    AudienceScope.OWNER_PRIVATE: MemoryAudience.OWNER_PRIVATE,
    AudienceScope.PARTICIPANTS: MemoryAudience.PARTICIPANTS,
    AudienceScope.EXPLICIT_SHARED: MemoryAudience.EXPLICIT_SHARED,
}
_COMMITMENT_STATE = {
    IntentionState.ACCEPTED: CommitmentState.ACTIVE,
    IntentionState.ACTIVE: CommitmentState.ACTIVE,
    IntentionState.WAITING: CommitmentState.WAITING,
    IntentionState.BLOCKED: CommitmentState.BLOCKED,
    IntentionState.VERIFYING: CommitmentState.VERIFYING,
    IntentionState.COMPLETED: CommitmentState.FULFILLED,
    IntentionState.FAILED: CommitmentState.FAILED,
    IntentionState.CANCELLED: CommitmentState.CANCELLED,
    IntentionState.EXPIRED: CommitmentState.EXPIRED,
}
_MAX_IDEMPOTENCY_CACHE = 1024


class IntentionServiceError(RuntimeError):
    """Stable command rejection carrying no user or model body text."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class IntentionAuditCode:
    command_code: str
    outcome_code: str
    occurred_at_utc: str


@dataclass(frozen=True, slots=True)
class IntentionCommandResult:
    intention: IntentionV1
    commitment: CommitmentV1 | None
    transition: IntentionTransitionEventV1


@dataclass(frozen=True, slots=True)
class ActionAttemptReservation:
    intention_id: str
    runtime_boot_id: str
    attempt_number: int
    remaining_attempts: int
    intention_revision: int


@dataclass(frozen=True, slots=True)
class _CachedResult:
    fingerprint: str
    result: object


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    milliseconds = normalized.microsecond // 1000
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def _sealed(contract_type: type[Any], value: Any, **changes: object) -> Any:
    wire = value.to_dict() if hasattr(value, "to_dict") else dict(value)
    wire.update(changes)
    wire.pop("content_hash", None)
    wire["content_hash"] = canonical_content_hash(wire)
    return contract_type.from_dict(wire)


def _fingerprint(command: str, payload: Mapping[str, object]) -> str:
    wire = {"command": command, **payload}
    return hashlib.sha256(canonical_json_bytes(wire)).hexdigest()


class IntentionService:
    """Authorize, validate and serialize L5 intention state commands."""

    def __init__(
        self,
        store: IntentionStore,
        *,
        runtime_boot_id: str,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[str], str] | None = None,
        audit_sink: Callable[[IntentionAuditCode], None] | None = None,
    ) -> None:
        if not isinstance(store, IntentionStore):
            raise TypeError("store must be an IntentionStore")
        if type(runtime_boot_id) is not str or not runtime_boot_id:
            raise ValueError("runtime_boot_id must be a non-empty string")
        self._store = store
        self._runtime_boot_id = runtime_boot_id
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (
            lambda prefix: f"{prefix}-{uuid.uuid4().hex}"
        )
        self._audit_sink = audit_sink
        self._audit: list[IntentionAuditCode] = []
        self._attempts: dict[str, int] = {}
        self._idempotency: OrderedDict[str, _CachedResult] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def runtime_boot_id(self) -> str:
        return self._runtime_boot_id

    def audit_codes(self) -> tuple[IntentionAuditCode, ...]:
        with self._lock:
            return tuple(self._audit)

    @staticmethod
    def can_transition(
        from_state: IntentionState | str | None,
        to_state: IntentionState | str,
    ) -> bool:
        return is_valid_intention_transition(from_state, to_state)

    def create_candidate(
        self,
        intention: IntentionV1,
        *,
        access: AccessContext,
        idempotency_key: str,
    ) -> IntentionCommandResult:
        return self._create(
            "create_candidate",
            intention,
            access=access,
            idempotency_key=idempotency_key,
            accepted=False,
        )

    def accept_request(
        self,
        intention: IntentionV1,
        *,
        access: AccessContext,
        idempotency_key: str,
        promise_summary: str = "Track the accepted intention through verification.",
    ) -> IntentionCommandResult:
        return self._create(
            "accept_request",
            intention,
            access=access,
            idempotency_key=idempotency_key,
            accepted=True,
            promise_summary=promise_summary,
        )

    def accept_candidate(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        evidence_refs: Iterable[str] = (),
        promise_summary: str = "Track the accepted intention through verification.",
    ) -> IntentionCommandResult:
        return self._transition(
            "accept_candidate",
            intention_id,
            IntentionState.ACCEPTED,
            expected_revision=expected_revision,
            access=access,
            idempotency_key=idempotency_key,
            evidence_refs=evidence_refs,
            promise_summary=promise_summary,
        )

    def activate(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        evidence_refs: Iterable[str] = (),
    ) -> IntentionCommandResult:
        return self._transition(
            "activate",
            intention_id,
            IntentionState.ACTIVE,
            expected_revision=expected_revision,
            access=access,
            idempotency_key=idempotency_key,
            evidence_refs=evidence_refs,
        )

    def set_waiting(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        next_review_at_utc: str,
        evidence_refs: Iterable[str] = (),
    ) -> IntentionCommandResult:
        return self._transition(
            "set_waiting",
            intention_id,
            IntentionState.WAITING,
            expected_revision=expected_revision,
            access=access,
            idempotency_key=idempotency_key,
            evidence_refs=evidence_refs,
            next_review_at_utc=next_review_at_utc,
        )

    def set_blocked(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        blockers: Iterable[BlockerV1],
        next_review_at_utc: str,
        evidence_refs: Iterable[str],
    ) -> IntentionCommandResult:
        normalized_blockers = tuple(blockers)
        if not normalized_blockers or not all(
            isinstance(item, BlockerV1) for item in normalized_blockers
        ):
            self._reject("set_blocked", "blocker_evidence_required")
        return self._transition(
            "set_blocked",
            intention_id,
            IntentionState.BLOCKED,
            expected_revision=expected_revision,
            access=access,
            idempotency_key=idempotency_key,
            evidence_refs=evidence_refs,
            blockers=normalized_blockers,
            next_review_at_utc=next_review_at_utc,
        )

    def resume_after_evidence(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        evidence_refs: Iterable[str],
    ) -> IntentionCommandResult:
        evidence = self._evidence("resume_after_evidence", evidence_refs, required=True)
        return self._transition(
            "resume_after_evidence",
            intention_id,
            IntentionState.ACTIVE,
            expected_revision=expected_revision,
            access=access,
            idempotency_key=idempotency_key,
            evidence_refs=evidence,
        )

    def begin_verification(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        evidence_refs: Iterable[str],
    ) -> IntentionCommandResult:
        evidence = self._evidence("begin_verification", evidence_refs, required=True)
        return self._transition(
            "begin_verification",
            intention_id,
            IntentionState.VERIFYING,
            expected_revision=expected_revision,
            access=access,
            idempotency_key=idempotency_key,
            evidence_refs=evidence,
        )

    def apply_verification(
        self,
        verification: VerificationRecordV1,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
    ) -> IntentionCommandResult:
        command = "apply_verification"
        fingerprint = _fingerprint(
            command,
            {
                "verification": verification.to_dict(),
                "expected_revision": expected_revision,
            },
        )
        with self._lock:
            cached = self._cached(idempotency_key, fingerprint, command)
            if cached is not None:
                return cached  # type: ignore[return-value]
            intention = self._load_current(
                command, verification.intention_id, expected_revision, access
            )
            if intention.state is not IntentionState.VERIFYING:
                self._reject(command, "verification_state_required")
            self._validate_verification(command, intention, verification)
            record_key = self._derived_key(idempotency_key, "record")
            try:
                self._store.execute(
                    PersistVerificationCommand(verification),
                    expected_revision,
                    record_key,
                )
            except IntentionStoreIdempotencyConflictError:
                self._reject(command, "idempotency_conflict")
            except IntentionStoreConflictError:
                self._reject(command, "stale_revision")
            result = self._transition_locked(
                command,
                intention,
                IntentionState.COMPLETED,
                access=access,
                idempotency_key=idempotency_key,
                evidence_refs=verification.evidence_refs,
                reason_code="verification.required_criteria_satisfied",
                latest_verification_id=verification.verification_id,
                verification_record_id=verification.verification_id,
            )
            self._remember(idempotency_key, fingerprint, result)
            return result

    def cancel(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        evidence_refs: Iterable[str] = (),
        reason_code: str = "user.cancelled",
    ) -> IntentionCommandResult:
        return self._transition(
            "cancel",
            intention_id,
            IntentionState.CANCELLED,
            expected_revision=expected_revision,
            access=access,
            idempotency_key=idempotency_key,
            evidence_refs=evidence_refs,
            reason_code=reason_code,
        )

    def expire_due(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        evidence_refs: Iterable[str] = (),
    ) -> IntentionCommandResult:
        with self._lock:
            intention = self._store.get_intention(intention_id)
            if intention is None:
                self._reject("expire_due", "intention_not_found")
            if self._clock() < _parse_utc(intention.expires_at_utc):
                self._reject("expire_due", "intention_not_due")
        return self._transition(
            "expire_due",
            intention_id,
            IntentionState.EXPIRED,
            expected_revision=expected_revision,
            access=access,
            idempotency_key=idempotency_key,
            evidence_refs=evidence_refs,
            allow_expired=True,
        )

    def reserve_action_attempt(
        self,
        intention_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
    ) -> ActionAttemptReservation:
        command = "reserve_action_attempt"
        fingerprint = _fingerprint(
            command,
            {"intention_id": intention_id, "expected_revision": expected_revision},
        )
        with self._lock:
            cached = self._cached(idempotency_key, fingerprint, command)
            if cached is not None:
                return cached  # type: ignore[return-value]
            intention = self._load_current(
                command, intention_id, expected_revision, access
            )
            if intention.state is not IntentionState.ACTIVE:
                self._reject(command, "active_state_required")
            used = self._attempts.get(intention_id, 0)
            maximum = intention.execution_budget.max_action_attempts
            if used >= maximum:
                self._reject(command, "action_attempt_budget_exhausted")
            used += 1
            self._attempts[intention_id] = used
            result = ActionAttemptReservation(
                intention_id=intention_id,
                runtime_boot_id=self._runtime_boot_id,
                attempt_number=used,
                remaining_attempts=maximum - used,
                intention_revision=intention.revision,
            )
            self._remember(idempotency_key, fingerprint, result)
            self._record(command, "action_attempt_reserved")
            return result

    def attach_receipt(
        self,
        intention_id: str,
        receipt_id: str,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        receipt_kind: str = "action",
    ) -> CommitmentV1:
        command = "attach_receipt"
        fingerprint = _fingerprint(
            command,
            {
                "intention_id": intention_id,
                "receipt_id": receipt_id,
                "receipt_kind": receipt_kind,
                "expected_revision": expected_revision,
            },
        )
        with self._lock:
            cached = self._cached(idempotency_key, fingerprint, command)
            if cached is not None:
                return cached  # type: ignore[return-value]
            intention = self._load_current(
                command, intention_id, expected_revision, access
            )
            if intention.state in TERMINAL_INTENTION_STATES:
                self._reject(command, "terminal_state_closed")
            commitment = self._load_commitment(command, intention)
            if type(receipt_id) is not str or not receipt_id or len(receipt_id) > 256:
                self._reject(command, "receipt_id_invalid")
            if receipt_kind not in {"action", "recovery"}:
                self._reject(command, "receipt_kind_invalid")
            field = (
                "last_action_receipt_id"
                if receipt_kind == "action"
                else "last_recovery_receipt_id"
            )
            updated = _sealed(
                CommitmentV1,
                commitment,
                revision=commitment.revision + 1,
                updated_at_utc=self._timestamp(),
                **{field: receipt_id},
            )
            try:
                self._store.execute(
                    PersistCommitmentCommand(updated),
                    commitment.revision,
                    idempotency_key,
                )
            except IntentionStoreIdempotencyConflictError:
                self._reject(command, "idempotency_conflict")
            except IntentionStoreConflictError:
                self._reject(command, "stale_revision")
            self._remember(idempotency_key, fingerprint, updated)
            self._record(command, "receipt_attached")
            return updated

    def _create(
        self,
        command: str,
        intention: IntentionV1,
        *,
        access: AccessContext,
        idempotency_key: str,
        accepted: bool,
        promise_summary: str = "",
    ) -> IntentionCommandResult:
        fingerprint = _fingerprint(command, {"intention": intention.to_dict()})
        with self._lock:
            cached = self._cached(idempotency_key, fingerprint, command)
            if cached is not None:
                return cached  # type: ignore[return-value]
            expected_state = IntentionState.ACCEPTED if accepted else IntentionState.CANDIDATE
            if intention.state is not expected_state or intention.revision != 1:
                self._reject(command, "initial_state_invalid")
            if intention.commitment_id is not None:
                self._reject(command, "client_commitment_forbidden")
            self._authorize(command, access, intention, allow_expired=False)
            if self._store.get_intention(intention.intention_id) is not None:
                self._reject(command, "intention_already_exists")
            commitment = None
            persisted = intention
            if accepted:
                commitment = self._new_commitment(intention, promise_summary)
                persisted = _sealed(
                    IntentionV1,
                    intention,
                    commitment_id=commitment.commitment_id,
                )
            transition = self._new_transition(
                persisted,
                from_state=None,
                to_state=expected_state,
                expected_revision=0,
                idempotency_key=idempotency_key,
                reason_code=(
                    "request.accepted" if accepted else "candidate.created"
                ),
                evidence_refs=persisted.source_event_ids,
            )
            try:
                self._store.execute(
                    PersistIntentionCommand(persisted, transition, commitment),
                    0,
                    idempotency_key,
                )
            except IntentionStoreIdempotencyConflictError:
                self._reject(command, "idempotency_conflict")
            except IntentionStoreConflictError:
                self._reject(command, "stale_revision")
            result = IntentionCommandResult(persisted, commitment, transition)
            self._remember(idempotency_key, fingerprint, result)
            self._record(command, "accepted")
            return result

    def _transition(
        self,
        command: str,
        intention_id: str,
        target: IntentionState,
        *,
        expected_revision: int,
        access: AccessContext,
        idempotency_key: str,
        evidence_refs: Iterable[str],
        allow_expired: bool = False,
        **changes: object,
    ) -> IntentionCommandResult:
        evidence = self._evidence(command, evidence_refs)
        fingerprint = _fingerprint(
            command,
            {
                "intention_id": intention_id,
                "target": target.value,
                "expected_revision": expected_revision,
                "evidence_refs": list(evidence),
                "changes": changes,
            },
        )
        with self._lock:
            cached = self._cached(idempotency_key, fingerprint, command)
            if cached is not None:
                return cached  # type: ignore[return-value]
            intention = self._load_current(
                command,
                intention_id,
                expected_revision,
                access,
                allow_expired=allow_expired,
            )
            result = self._transition_locked(
                command,
                intention,
                target,
                access=access,
                idempotency_key=idempotency_key,
                evidence_refs=evidence,
                **changes,
            )
            self._remember(idempotency_key, fingerprint, result)
            return result

    def _transition_locked(
        self,
        command: str,
        intention: IntentionV1,
        target: IntentionState,
        *,
        access: AccessContext,
        idempotency_key: str,
        evidence_refs: Iterable[str],
        reason_code: str | None = None,
        promise_summary: str = "Track the accepted intention through verification.",
        blockers: tuple[BlockerV1, ...] = (),
        next_review_at_utc: str | None = None,
        latest_verification_id: str | None = None,
        verification_record_id: str | None = None,
    ) -> IntentionCommandResult:
        if intention.state in TERMINAL_INTENTION_STATES:
            self._reject(command, "terminal_state_closed")
        if target is IntentionState.COMPLETED and command != "apply_verification":
            self._reject(command, "completion_requires_verification")
        if not is_valid_intention_transition(intention.state, target):
            self._reject(command, "illegal_state_transition")
        evidence = self._evidence(
            command,
            evidence_refs,
            required=target
            in {IntentionState.BLOCKED, IntentionState.VERIFYING, IntentionState.COMPLETED},
        )
        timestamp = self._timestamp()
        commitment: CommitmentV1 | None
        commitment_id = intention.commitment_id
        if target is IntentionState.ACCEPTED and commitment_id is None:
            commitment = self._new_commitment(intention, promise_summary)
            commitment_id = commitment.commitment_id
        elif commitment_id is not None:
            current_commitment = self._load_commitment(command, intention)
            commitment_changes: dict[str, object] = {
                "state": _COMMITMENT_STATE[target].value,
                "revision": current_commitment.revision + 1,
                "updated_at_utc": timestamp,
                "state_reason_code": reason_code or f"intention.{target.value}",
                "current_blockers": [],
                "next_review_at_utc": None,
            }
            if target is IntentionState.WAITING:
                if next_review_at_utc is None:
                    self._reject(command, "next_review_required")
                commitment_changes["next_review_at_utc"] = next_review_at_utc
            if target is IntentionState.BLOCKED:
                if not blockers or next_review_at_utc is None:
                    self._reject(command, "blocker_evidence_required")
                commitment_changes["current_blockers"] = [
                    item.to_dict() for item in blockers
                ]
                commitment_changes["next_review_at_utc"] = next_review_at_utc
            if verification_record_id is not None:
                commitment_changes["verification_record_ids"] = [
                    *current_commitment.verification_record_ids,
                    verification_record_id,
                ]
            commitment = _sealed(
                CommitmentV1, current_commitment, **commitment_changes
            )
        else:
            commitment = None
        updated_intention = _sealed(
            IntentionV1,
            intention,
            state=target.value,
            revision=intention.revision + 1,
            updated_at_utc=timestamp,
            commitment_id=commitment_id,
            latest_verification_id=(
                latest_verification_id
                if latest_verification_id is not None
                else intention.latest_verification_id
            ),
            state_reason_code=reason_code or f"intention.{target.value}",
        )
        transition = self._new_transition(
            updated_intention,
            from_state=intention.state,
            to_state=target,
            expected_revision=intention.revision,
            idempotency_key=idempotency_key,
            reason_code=reason_code or f"intention.{target.value}",
            evidence_refs=evidence,
        )
        try:
            self._store.execute(
                PersistIntentionCommand(updated_intention, transition, commitment),
                intention.revision,
                idempotency_key,
            )
        except IntentionStoreIdempotencyConflictError:
            self._reject(command, "idempotency_conflict")
        except IntentionStoreConflictError:
            self._reject(command, "stale_revision")
        self._record(command, f"state.{target.value}")
        return IntentionCommandResult(updated_intention, commitment, transition)

    def _load_current(
        self,
        command: str,
        intention_id: str,
        expected_revision: int,
        access: AccessContext,
        *,
        allow_expired: bool = False,
    ) -> IntentionV1:
        intention = self._store.get_intention(intention_id)
        if intention is None:
            self._reject(command, "intention_not_found")
        self._authorize(command, access, intention, allow_expired=allow_expired)
        if type(expected_revision) is not int or intention.revision != expected_revision:
            self._reject(command, "stale_revision")
        return intention

    def _load_commitment(
        self, command: str, intention: IntentionV1
    ) -> CommitmentV1:
        if intention.commitment_id is None:
            self._reject(command, "commitment_required")
        commitment = self._store.get_commitment(intention.commitment_id)
        if commitment is None or commitment.intention_id != intention.intention_id:
            self._reject(command, "commitment_inconsistent")
        return commitment

    def _authorize(
        self,
        command: str,
        access: AccessContext,
        intention: IntentionV1,
        *,
        allow_expired: bool,
    ) -> None:
        if not isinstance(access, AccessContext):
            self._reject(command, "access_context_required")
        now = self._clock()
        if access.runtime_boot_id != self._runtime_boot_id:
            self._reject(command, "cross_boot_denied")
        if intention.runtime_boot_id != self._runtime_boot_id:
            self._reject(command, "cross_boot_denied")
        if not (_parse_utc(access.issued_at_utc) <= now < _parse_utc(access.expires_at_utc)):
            self._reject(command, "access_context_expired")
        if not allow_expired and now >= _parse_utc(intention.expires_at_utc):
            self._reject(command, "intention_expired")
        if access.actor_kind is ActorKind.GUEST:
            self._reject(command, "guest_write_denied")
        if access.actor_subject_id != intention.owner_subject_id:
            self._reject(command, "cross_owner_denied")
        if access.purpose not in {AccessPurpose.CONVERSATION, AccessPurpose.MANAGE}:
            self._reject(command, "access_purpose_denied")
        if "intent.write" not in access.capability_scopes:
            self._reject(command, "scope_denied")
        required_audience = _INTENTION_AUDIENCE[intention.audience]
        if _AUDIENCE_RANK[access.audience_ceiling] < _AUDIENCE_RANK[required_audience]:
            self._reject(command, "audience_denied")
        if intention.audience is AudienceScope.PARTICIPANTS and not set(
            intention.participant_ids
        ).issubset(set(access.participant_subject_ids)):
            self._reject(command, "participant_scope_denied")
        if not intention.source_event_ids:
            self._reject(command, "source_evidence_required")

    def _validate_verification(
        self,
        command: str,
        intention: IntentionV1,
        verification: VerificationRecordV1,
    ) -> None:
        if (
            verification.intention_id != intention.intention_id
            or verification.intention_revision != intention.revision
            or verification.commitment_id != intention.commitment_id
        ):
            self._reject(command, "verification_parent_mismatch")
        for field in (
            "javis_identity_id",
            "instance_id",
            "owner_subject_id",
            "participant_ids",
            "audience",
            "runtime_boot_id",
        ):
            if getattr(verification, field) != getattr(intention, field):
                self._reject(command, "verification_access_mismatch")
        if verification.result is not VerificationResult.SATISFIED:
            self._reject(command, "verification_not_satisfied")
        expected = {item.criterion_id: item for item in intention.success_criteria}
        actual = {item.criterion_id: item for item in verification.criterion_results}
        if not set(actual).issubset(expected):
            self._reject(command, "verification_unknown_criterion")
        now = self._clock()
        for criterion_id, criterion in expected.items():
            if not criterion.required:
                continue
            result = actual.get(criterion_id)
            if (
                result is None
                or result.outcome is not CriterionOutcome.SATISFIED
                or not result.evidence_refs
                or _parse_utc(result.fresh_until_utc) <= now
            ):
                self._reject(command, "required_criterion_unsatisfied")

    def _new_commitment(
        self, intention: IntentionV1, promise_summary: str
    ) -> CommitmentV1:
        timestamp = self._timestamp()
        wire: dict[str, object] = {
            "schema_version": 1,
            "commitment_id": self._id_factory("commitment"),
            "javis_identity_id": intention.javis_identity_id,
            "instance_id": intention.instance_id,
            "owner_subject_id": intention.owner_subject_id,
            "participant_ids": list(intention.participant_ids),
            "audience": intention.audience.value,
            "source_event_ids": list(intention.source_event_ids),
            "runtime_boot_id": intention.runtime_boot_id,
            "created_at_utc": timestamp,
            "updated_at_utc": timestamp,
            "expires_at_utc": intention.expires_at_utc,
            "privacy_class": intention.privacy_class.value,
            "retention_class": intention.retention_class.value,
            "state": CommitmentState.ACTIVE.value,
            "revision": 1,
            "provenance": "intention.service",
            "intention_id": intention.intention_id,
            "accepted_from_event_id": intention.source_event_ids[0],
            "promise_summary": promise_summary,
            "due_at_utc": None,
            "resume_policy": ResumePolicy.RECONCILE_THEN_ASK.value,
            "current_blockers": [],
            "next_review_at_utc": None,
            "last_checkpoint_id": None,
            "last_action_receipt_id": None,
            "last_recovery_receipt_id": None,
            "verification_record_ids": [],
            "state_reason_code": "commitment.accepted",
        }
        wire["content_hash"] = canonical_content_hash(wire)
        return CommitmentV1.from_dict(wire)

    def _new_transition(
        self,
        intention: IntentionV1,
        *,
        from_state: IntentionState | None,
        to_state: IntentionState,
        expected_revision: int,
        idempotency_key: str,
        reason_code: str,
        evidence_refs: Iterable[str],
    ) -> IntentionTransitionEventV1:
        wire: dict[str, object] = {
            "schema_version": 1,
            "transition_id": self._id_factory("transition"),
            "intention_id": intention.intention_id,
            "from_state": None if from_state is None else from_state.value,
            "to_state": to_state.value,
            "expected_revision": expected_revision,
            "resulting_revision": expected_revision + 1,
            "idempotency_key": idempotency_key,
            "reason_code": reason_code,
            "evidence_refs": list(evidence_refs),
            "runtime_boot_id": self._runtime_boot_id,
            "occurred_at_utc": self._timestamp(),
        }
        wire["content_hash"] = canonical_content_hash(wire)
        return IntentionTransitionEventV1.from_dict(wire)

    def _evidence(
        self,
        command: str,
        values: Iterable[str],
        *,
        required: bool = False,
    ) -> tuple[str, ...]:
        if isinstance(values, (str, bytes)):
            self._reject(command, "evidence_refs_invalid")
        evidence = tuple(values)
        if len(evidence) > 32 or len(set(evidence)) != len(evidence):
            self._reject(command, "evidence_refs_invalid")
        if any(type(item) is not str or not item or len(item) > 256 for item in evidence):
            self._reject(command, "evidence_refs_invalid")
        if required and not evidence:
            self._reject(command, "evidence_required")
        return evidence

    def _clock(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise RuntimeError("IntentionService clock must return an aware datetime")
        return value.astimezone(timezone.utc)

    def _timestamp(self) -> str:
        return _format_utc(self._clock())

    def _cached(
        self, key: str, fingerprint: str, command: str
    ) -> object | None:
        if type(key) is not str or not key or len(key) > 240:
            self._reject(command, "idempotency_key_invalid")
        cached = self._idempotency.get(key)
        if cached is None:
            return None
        if cached.fingerprint != fingerprint:
            self._reject(command, "idempotency_conflict")
        self._idempotency.move_to_end(key)
        return cached.result

    def _remember(self, key: str, fingerprint: str, result: object) -> None:
        self._idempotency[key] = _CachedResult(fingerprint, result)
        self._idempotency.move_to_end(key)
        while len(self._idempotency) > _MAX_IDEMPOTENCY_CACHE:
            self._idempotency.popitem(last=False)

    @staticmethod
    def _derived_key(key: str, suffix: str) -> str:
        candidate = f"{key}.{suffix}"
        if len(candidate) <= 256:
            return candidate
        return hashlib.sha256(candidate.encode("utf-8")).hexdigest()

    def _record(self, command: str, outcome: str) -> None:
        entry = IntentionAuditCode(command, outcome, self._timestamp())
        self._audit.append(entry)
        if self._audit_sink is not None:
            self._audit_sink(entry)

    def _reject(self, command: str, reason: str) -> None:
        self._record(command, reason)
        raise IntentionServiceError(reason)


__all__ = [
    "ActionAttemptReservation",
    "IntentionAuditCode",
    "IntentionCommandResult",
    "IntentionService",
    "IntentionServiceError",
]
