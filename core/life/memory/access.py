"""Server-owned access contexts and minimal L2 subject binding.

This module deliberately accepts no conversation payload when resolving identity.
Callers first turn an already-authorized runtime capability into a
``ServerPrincipal`` and then resolve that principal against persisted session
participants. Any missing or inconsistent evidence produces a guest context.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from .contracts import (
    AccessContext,
    AccessPurpose,
    ActorKind,
    Audience,
    BindingSource,
    BindingStatus,
    HandoffLease,
    IdentityAssurance,
    ParticipantRole,
    ParticipantStatus,
    SessionParticipant,
    SessionGenerationState,
    Subject,
    SubjectBinding,
    SubjectKind,
    SubjectStatus,
)
from .subjects import (
    BindSession,
    BootstrapPrimary,
    CreateKnownPerson,
    DisableSubject,
    HandoffSession,
    LockSession,
    SetGuestPresent,
)


_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_MAX_CONTEXT_TTL_SECONDS = 60.0
_GUEST_CONTEXT_TTL_SECONDS = 30.0
_MAX_PARTICIPANTS = 16

_PURPOSE_SCOPE = {
    AccessPurpose.CONVERSATION: "conversation",
    AccessPurpose.RECALL: "memory.read",
    AccessPurpose.MANAGE: "memory.manage",
    AccessPurpose.DELETE: "memory.delete",
    AccessPurpose.MIGRATION: "memory.migrate",
}

RESERVED_CLIENT_ACCESS_FIELDS = frozenset(
    {
        "access_context",
        "actor",
        "actor_kind",
        "actor_subject_id",
        "audience",
        "audience_ceiling",
        "owner",
        "owner_subject_id",
        "participant",
        "participant_ids",
        "participant_subject_ids",
        "principal",
        "subject",
        "subject_id",
    }
)


class PrincipalBindingSource(str, Enum):
    """Server-observed surface that supplied an authorized capability."""

    PACKAGED_DESKTOP = "packaged_desktop"
    DEVELOPMENT_WEB = "development_web"
    LOOPBACK_WEB = "loopback_web"


class AccessBindingError(RuntimeError):
    """Stable failure raised only by explicit server-side binding operations."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class ReservedAccessFieldError(ValueError):
    """Protocol error for client attempts to provide server-owned identity."""

    reason_code = "reserved_access_field"

    def __init__(self, field_name: str) -> None:
        super().__init__(self.reason_code)
        self.field_name = field_name


class _MemoryAccessStore(Protocol):
    def status(self) -> Mapping[str, Any]: ...

    def metadata(self) -> Mapping[str, Any]: ...

    def get_subject(self, subject_id: str) -> Subject | None: ...

    def active_primary_subject(self) -> Subject | None: ...

    def get_subject_binding(self, binding_id: str) -> SubjectBinding | None: ...

    def active_subject_binding(
        self, runtime_boot_id: str, client_id_hash: str
    ) -> SubjectBinding | None: ...

    def active_session_participants(
        self, session_id: str
    ) -> tuple[SessionParticipant, ...]: ...

    def get_session_generation(self, session_id: str) -> SessionGenerationState | None: ...

    def get_session_transition_receipt(self, command_id: str) -> Mapping[str, Any] | None: ...

    def session_access_snapshot(
        self, session_id: str, runtime_boot_id: str, client_id_hash: str
    ) -> Mapping[str, Any]: ...


class _MemoryBindingStore(_MemoryAccessStore, Protocol):
    def put_subject(self, subject: Subject, *, writer_token: object) -> bool: ...

    def put_session_participant(
        self, participant: SessionParticipant, *, writer_token: object
    ) -> bool: ...

    def put_subject_binding(
        self, binding: SubjectBinding, *, writer_token: object
    ) -> bool: ...

    def retire_subject_bindings(
        self, runtime_boot_id: str, at_utc: str, *, writer_token: object
    ) -> int: ...

    def revoke_subject_binding(
        self, binding_id: str, revoked_at_utc: str, *, writer_token: object
    ) -> bool: ...

    def revoke_subject_bindings(
        self, subject_id: str, revoked_at_utc: str, *, writer_token: object
    ) -> int: ...

    def session_generations(self) -> tuple[SessionGenerationState, ...]: ...

    def get_handoff_lease(self, lease_id: str) -> HandoffLease | None: ...

    def put_handoff_lease(
        self, lease: HandoffLease, *, writer_token: object
    ) -> bool: ...

    def ensure_guest_session(
        self,
        state: SessionGenerationState,
        guest_subject: Subject,
        participant: SessionParticipant,
        *,
        writer_token: object,
    ) -> SessionGenerationState: ...

    def fence_session_transition(self, *, writer_token: object, **values: Any) -> dict[str, Any]: ...

    def complete_session_transition(
        self,
        command_id: str,
        state: SessionGenerationState,
        participants: tuple[SessionParticipant, ...],
        *,
        at_utc: str,
        writer_token: object,
    ) -> dict[str, Any]: ...


def _bounded_identifier(value: Any, field_name: str, *, maximum: int = 256) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or _CONTROL_CHARACTER.search(normalized)
    ):
        raise ValueError(f"{field_name} must be a bounded identifier")
    return normalized


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_timestamp(epoch: float) -> str:
    value = datetime.fromtimestamp(epoch, timezone.utc)
    milliseconds = value.microsecond // 1000
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def _normalize_purpose(value: AccessPurpose | str) -> AccessPurpose:
    if isinstance(value, AccessPurpose):
        return value
    try:
        return AccessPurpose(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("purpose is not supported") from exc


def reject_reserved_client_access_fields(payload: Mapping[str, Any]) -> None:
    """Reject top-level fields whose values must come from the server.

    Message text and tool arguments are intentionally not traversed. The protocol
    boundary calls this function on the request envelope, where these names are
    reserved regardless of their value.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    for key in payload:
        if type(key) is str and key.casefold() in RESERVED_CLIENT_ACCESS_FIELDS:
            raise ReservedAccessFieldError(key)


@dataclass(frozen=True, slots=True)
class ServerPrincipal:
    """Immutable, bearer-free result of runtime capability authorization."""

    runtime_boot_id: str
    session_id: str
    client_id_hash: str
    capability_scopes: tuple[str, ...]
    issued_at_epoch: float
    expires_at_epoch: float
    binding_source: PrincipalBindingSource

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "runtime_boot_id", _bounded_identifier(self.runtime_boot_id, "runtime_boot_id")
        )
        object.__setattr__(self, "session_id", _bounded_identifier(self.session_id, "session_id"))
        if type(self.client_id_hash) is not str or not _SHA256_HEX.fullmatch(
            self.client_id_hash
        ):
            raise ValueError("client_id_hash must be lowercase SHA-256 hex")
        if isinstance(self.capability_scopes, (str, bytes)):
            raise ValueError("capability_scopes must be a tuple of scope names")
        scopes = tuple(sorted({_bounded_identifier(value, "scope", maximum=64) for value in self.capability_scopes}))
        if not scopes or len(scopes) > 16:
            raise ValueError("capability_scopes must contain between 1 and 16 scopes")
        object.__setattr__(self, "capability_scopes", scopes)
        for field_name in ("issued_at_epoch", "expires_at_epoch"):
            value = getattr(self, field_name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite")
            object.__setattr__(self, field_name, float(value))
        if self.expires_at_epoch <= self.issued_at_epoch:
            raise ValueError("principal expiry must follow issue time")
        try:
            source = PrincipalBindingSource(self.binding_source)
        except (TypeError, ValueError) as exc:
            raise ValueError("binding_source is not supported") from exc
        object.__setattr__(self, "binding_source", source)

    def safe_projection(self) -> dict[str, Any]:
        """Return bounded authorization metadata with no bearer or nonce."""

        return {
            "runtime_boot_id": self.runtime_boot_id,
            "session_id": self.session_id,
            "client_id_hash": self.client_id_hash,
            "capability_scopes": list(self.capability_scopes),
            "issued_at_utc": _utc_timestamp(self.issued_at_epoch),
            "expires_at_utc": _utc_timestamp(self.expires_at_epoch),
            "binding_source": self.binding_source.value,
        }


@dataclass(frozen=True, slots=True)
class PrimarySessionBinding:
    primary_subject: Subject
    javis_subject: Subject
    subject_binding: SubjectBinding
    replayed: bool


class MemorySubjectBinder:
    """Server-owned L3 subject lifecycle and boot-scoped human bindings."""

    JAVIS_SUBJECT_ID = "subject-javis"

    def __init__(
        self,
        store: _MemoryBindingStore,
        *,
        writer_token: object,
        runtime_boot_id: str,
        javis_identity_id: str | None = None,
        now: Callable[[], float] | None = None,
        privacy_barrier: Callable[[str, int, str], None] | None = None,
    ) -> None:
        self._store = store
        self._writer_token = writer_token
        self._runtime_boot_id = _bounded_identifier(runtime_boot_id, "runtime_boot_id")
        self._javis_identity_id = (
            None
            if javis_identity_id is None
            else _bounded_identifier(javis_identity_id, "javis_identity_id")
        )
        self._now = now or time.time
        if privacy_barrier is not None and not callable(privacy_barrier):
            raise TypeError("privacy_barrier must be callable")
        self._privacy_barrier = privacy_barrier

    def ensure_javis_subject(self) -> Subject:
        existing = self._store.get_subject(self.JAVIS_SUBJECT_ID)
        identity_hash = (
            None if self._javis_identity_id is None else _sha256(self._javis_identity_id)
        )
        if existing is not None:
            if (
                existing.subject_kind is not SubjectKind.JAVIS
                or existing.status is not SubjectStatus.ACTIVE
                or existing.identity_assurance is not IdentityAssurance.VERIFIED
                or existing.credential_reference_hash not in {None, identity_hash}
            ):
                raise AccessBindingError("javis_subject_conflict")
            if identity_hash is not None and existing.credential_reference_hash is None:
                existing = replace(
                    existing,
                    revision=existing.revision + 1,
                    credential_reference_hash=identity_hash,
                    assurance_ceiling=IdentityAssurance.VERIFIED,
                    updated_at_utc=_utc_timestamp(self._clock()),
                )
                self._store.put_subject(existing, writer_token=self._writer_token)
            return existing
        timestamp = _utc_timestamp(self._clock())
        subject = Subject(
            schema_version=1,
            subject_id=self.JAVIS_SUBJECT_ID,
            revision=1,
            subject_kind=SubjectKind.JAVIS,
            display_name="Javis",
            status=SubjectStatus.ACTIVE,
            identity_assurance=IdentityAssurance.VERIFIED,
            credential_reference_hash=identity_hash,
            merged_into_subject_id=None,
            session_scope_id=None,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            aliases=(),
            created_by_subject_id=None,
            assurance_ceiling=IdentityAssurance.VERIFIED,
        )
        self._store.put_subject(subject, writer_token=self._writer_token)
        return subject

    def bootstrap_primary(
        self, command: BootstrapPrimary, principal: ServerPrincipal
    ) -> PrimarySessionBinding:
        now = self._clock()
        self._authorize_command(command.access_context, principal, "identity.manage", now)
        self.retire_stale_bindings()
        javis_subject = self.ensure_javis_subject()
        expected_subject_id = self._stable_id("subject-primary", command.command_id)
        existing_primary = self._store.active_primary_subject()
        if existing_primary is not None and existing_primary.subject_id != expected_subject_id:
            raise AccessBindingError("primary_already_exists")
        primary_subject = self._store.get_subject(expected_subject_id)
        if primary_subject is None:
            timestamp = _utc_timestamp(now)
            primary_subject = Subject(
                schema_version=1,
                subject_id=expected_subject_id,
                revision=1,
                subject_kind=SubjectKind.PRIMARY_USER,
                display_name=command.display_name,
                status=SubjectStatus.ACTIVE,
                identity_assurance=IdentityAssurance.DESKTOP_CONFIRMED,
                credential_reference_hash=None,
                merged_into_subject_id=None,
                session_scope_id=None,
                created_at_utc=timestamp,
                updated_at_utc=timestamp,
                aliases=command.aliases,
                created_by_subject_id=None,
                assurance_ceiling=IdentityAssurance.DESKTOP_CONFIRMED,
            )
            self._store.put_subject(primary_subject, writer_token=self._writer_token)
            replayed = False
        else:
            if (
                primary_subject.subject_kind is not SubjectKind.PRIMARY_USER
                or primary_subject.status is not SubjectStatus.ACTIVE
                or primary_subject.display_name != command.display_name
                or primary_subject.aliases != command.aliases
            ):
                raise AccessBindingError("primary_subject_conflict")
            replayed = True

        binding = self._create_or_replay_binding(
            command_id=command.command_id,
            subject=primary_subject,
            principal=principal,
            now=now,
        )
        self._transition_session(
            command,
            expected_generation=command.access_context.session_generation,
            owner=primary_subject,
            owner_binding=binding,
            owner_role=ParticipantRole.PRIMARY,
            guest_present=False,
            barrier_reason="primary_bootstrap",
        )
        return PrimarySessionBinding(
            primary_subject=primary_subject,
            javis_subject=javis_subject,
            subject_binding=binding,
            replayed=replayed,
        )

    def create_known_person(
        self, command: CreateKnownPerson, principal: ServerPrincipal
    ) -> Subject:
        now = self._clock()
        self._authorize_command(command.access_context, principal, "identity.manage", now)
        self._require_active_primary(command.access_context, now)
        subject_id = self._stable_id("subject-known", command.command_id)
        existing = self._store.get_subject(subject_id)
        if existing is not None:
            if (
                existing.subject_kind is not SubjectKind.KNOWN_PERSON
                or existing.display_name != command.display_name
                or existing.aliases != command.aliases
                or existing.created_by_subject_id != command.access_context.actor_subject_id
            ):
                raise AccessBindingError("known_person_idempotency_conflict")
            return existing
        timestamp = _utc_timestamp(now)
        subject = Subject(
            schema_version=1,
            subject_id=subject_id,
            revision=1,
            subject_kind=SubjectKind.KNOWN_PERSON,
            display_name=command.display_name,
            status=SubjectStatus.ACTIVE,
            identity_assurance=IdentityAssurance.GUEST,
            credential_reference_hash=None,
            merged_into_subject_id=None,
            session_scope_id=None,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            aliases=command.aliases,
            created_by_subject_id=command.access_context.actor_subject_id,
            assurance_ceiling=IdentityAssurance.OWNER_ATTESTED,
        )
        self._store.put_subject(subject, writer_token=self._writer_token)
        return subject

    def rebind_primary(
        self, command: BindSession, principal: ServerPrincipal
    ) -> SubjectBinding:
        now = self._clock()
        self._authorize_command(command.access_context, principal, "participants.manage", now)
        if command.expected_generation != command.access_context.session_generation:
            raise AccessBindingError("stale_session_generation")
        self.retire_stale_bindings()
        subject = self._store.get_subject(command.target_subject_id)
        if (
            subject is None
            or subject.subject_kind is not SubjectKind.PRIMARY_USER
            or subject.status is not SubjectStatus.ACTIVE
        ):
            raise AccessBindingError("primary_subject_unavailable")
        binding = self._create_or_replay_binding(
            command_id=command.command_id,
            subject=subject,
            principal=principal,
            now=now,
        )
        self._transition_session(
            command,
            expected_generation=command.expected_generation,
            owner=subject,
            owner_binding=binding,
            owner_role=ParticipantRole.PRIMARY,
            guest_present=False,
            barrier_reason="primary_rebind",
        )
        return binding

    def ensure_guest_session(self, session_id: str) -> SessionGenerationState:
        session = _bounded_identifier(session_id, "session_id")
        existing = self._store.get_session_generation(session)
        if existing is not None:
            return existing
        now = self._clock()
        timestamp = _utc_timestamp(now)
        guest = self._guest_subject(session, 0, timestamp)
        participant = self._participant(
            session_id=session,
            generation=0,
            subject=guest,
            role=ParticipantRole.GUEST,
            assurance=IdentityAssurance.GUEST,
            binding_source=BindingSource.GUEST_DEFAULT,
            binding_id=None,
            lease_expires_at_utc=None,
            timestamp=timestamp,
        )
        state = SessionGenerationState(
            schema_version=1,
            session_id=session,
            generation=0,
            guest_present=True,
            privacy_fenced=False,
            owner_subject_id=None,
            active_binding_id=None,
            revision=1,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
        )
        return self._store.ensure_guest_session(
            state,
            guest,
            participant,
            writer_token=self._writer_token,
        )

    def reset_sessions_to_guest(self) -> int:
        reset = 0
        for state in self._store.session_generations():
            command_id = self._stable_id(
                "startup-session-reset", f"{self._runtime_boot_id}\0{state.session_id}"
            )
            result = self._transition_values(
                command_id=command_id,
                command_kind="startup_reset",
                idempotency_key=command_id,
                payload={"boot": self._runtime_boot_id, "session": state.session_id},
                session_id=state.session_id,
                expected_generation=state.generation,
                owner=None,
                owner_binding=None,
                owner_role=None,
                guest_present=True,
                barrier_reason="runtime_restart",
                run_barrier=False,
            )
            if not result.get("replayed", False):
                reset += 1
        return reset

    def set_guest_present(
        self, command: SetGuestPresent, principal: ServerPrincipal
    ) -> dict[str, Any]:
        now = self._clock()
        self._authorize_command(command.access_context, principal, "participants.manage", now)
        try:
            binding = self._require_active_primary(command.access_context, now)
        except AccessBindingError as exc:
            raise AccessBindingError("active_session_owner_required") from exc
        owner = self._store.get_subject(binding.subject_id)
        state = self._store.get_session_generation(command.access_context.session_id)
        replay = self._store.get_session_transition_receipt(command.command_id)
        if (
            owner is None
            or (
                replay is None
                and (
                    state is None
                    or state.owner_subject_id != owner.subject_id
                    or state.active_binding_id != binding.binding_id
                )
            )
        ):
            raise AccessBindingError("active_session_owner_required")
        return self._transition_session(
            command,
            expected_generation=command.expected_generation,
            owner=owner,
            owner_binding=binding,
            owner_role=ParticipantRole.PRIMARY,
            guest_present=command.guest_present,
            barrier_reason=("guest_present_enabled" if command.guest_present else "guest_present_cleared"),
        )

    def handoff_session(
        self, command: HandoffSession, principal: ServerPrincipal
    ) -> dict[str, Any]:
        now = self._clock()
        self._authorize_command(command.access_context, principal, "participants.manage", now)
        target = self._store.get_subject(command.target_subject_id)
        state = self._store.get_session_generation(command.access_context.session_id)
        replay = self._store.get_session_transition_receipt(command.command_id)
        if (
            target is None
            or target.subject_kind is not SubjectKind.KNOWN_PERSON
            or target.status is not SubjectStatus.ACTIVE
            or target.assurance_ceiling is not IdentityAssurance.OWNER_ATTESTED
        ):
            raise AccessBindingError("handoff_target_unavailable")
        if replay is not None and replay["state"] == "completed":
            return self._transition_session(
                command,
                expected_generation=command.expected_generation,
                owner=target,
                owner_binding=None,
                owner_role=ParticipantRole.OWNER,
                guest_present=False,
                barrier_reason="session_handoff",
            )

        target_binding: SubjectBinding | None = None
        if replay is None:
            current_binding = self._require_active_primary(command.access_context, now)
        else:
            active = self._store.active_subject_binding(
                self._runtime_boot_id, principal.client_id_hash
            )
            if (
                active is not None
                and active.subject_id == target.subject_id
                and active.status is BindingStatus.ACTIVE
                and active.assurance is IdentityAssurance.OWNER_ATTESTED
                and self._timestamp_epoch(active.expires_at_utc) > now
            ):
                current_binding = None
                target_binding = active
            else:
                current_binding = self._require_active_primary(command.access_context, now)
        if replay is None and (
            state is None
            or state.owner_subject_id != command.access_context.actor_subject_id
            or state.active_binding_id != current_binding.binding_id
        ):
            raise AccessBindingError("active_session_owner_required")
        timestamp = _utc_timestamp(now)
        lease_expiry = _utc_timestamp(min(principal.expires_at_epoch, now + 300.0))
        lease = self._store.get_handoff_lease(command.lease_id)
        if lease is None:
            lease = HandoffLease(
                schema_version=1,
                lease_id=command.lease_id,
                revision=1,
                issuer_subject_id=command.access_context.actor_subject_id,
                target_subject_id=target.subject_id,
                session_id=command.access_context.session_id,
                session_generation=command.expected_generation,
                assurance=IdentityAssurance.OWNER_ATTESTED,
                status=BindingStatus.ACTIVE,
                issued_at_utc=timestamp,
                expires_at_utc=lease_expiry,
                consumed_at_utc=None,
                revoked_at_utc=None,
            )
            self._store.put_handoff_lease(lease, writer_token=self._writer_token)
        elif (
            lease.issuer_subject_id != command.access_context.actor_subject_id
            or lease.target_subject_id != target.subject_id
            or lease.session_id != command.access_context.session_id
            or lease.session_generation != command.expected_generation
        ):
            raise AccessBindingError("handoff_lease_conflict")

        def activate_target() -> SubjectBinding:
            if current_binding is None:
                raise AccessBindingError("active_session_owner_required")
            self._store.revoke_subject_binding(
                current_binding.binding_id,
                _utc_timestamp(self._clock()),
                writer_token=self._writer_token,
            )
            binding = self._create_or_replay_binding(
                command_id=command.command_id,
                subject=target,
                principal=principal,
                now=self._clock(),
                assurance=IdentityAssurance.OWNER_ATTESTED,
                source=BindingSource.OWNER_HANDOFF,
                expires_at_epoch=self._timestamp_epoch(lease.expires_at_utc),
            )
            current_lease = self._store.get_handoff_lease(command.lease_id)
            if current_lease is not None and current_lease.status is BindingStatus.ACTIVE:
                self._store.put_handoff_lease(
                    replace(
                        current_lease,
                        revision=current_lease.revision + 1,
                        status=BindingStatus.CONSUMED,
                        consumed_at_utc=_utc_timestamp(self._clock()),
                    ),
                    writer_token=self._writer_token,
                )
            return binding

        return self._transition_session(
            command,
            expected_generation=command.expected_generation,
            owner=target,
            owner_binding=target_binding,
            owner_role=ParticipantRole.OWNER,
            guest_present=False,
            barrier_reason="session_handoff",
            binding_factory=None if target_binding is not None else activate_target,
            lease_expires_at_utc=lease.expires_at_utc,
        )

    def _transition_session(
        self,
        command: Any,
        *,
        expected_generation: int,
        owner: Subject | None,
        owner_binding: SubjectBinding | None,
        owner_role: ParticipantRole | None,
        guest_present: bool,
        barrier_reason: str,
        binding_factory: Callable[[], SubjectBinding | None] | None = None,
        lease_expires_at_utc: str | None = None,
    ) -> dict[str, Any]:
        wire = command.to_dict()
        wire.pop("access_context", None)
        wire.pop("issued_at_utc", None)
        existing = self._store.get_session_transition_receipt(command.command_id)
        receipt_generation = (
            expected_generation
            if existing is None
            else int(existing["expected_generation"])
        )
        return self._transition_values(
            command_id=command.command_id,
            command_kind=type(command).__name__,
            idempotency_key=command.idempotency_key,
            payload=wire,
            session_id=command.access_context.session_id,
            expected_generation=receipt_generation,
            owner=owner,
            owner_binding=owner_binding,
            owner_role=owner_role,
            guest_present=guest_present,
            barrier_reason=barrier_reason,
            binding_factory=binding_factory,
            lease_expires_at_utc=lease_expires_at_utc,
        )

    def _transition_values(
        self,
        *,
        command_id: str,
        command_kind: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        session_id: str,
        expected_generation: int,
        owner: Subject | None,
        owner_binding: SubjectBinding | None,
        owner_role: ParticipantRole | None,
        guest_present: bool,
        barrier_reason: str,
        run_barrier: bool = True,
        binding_factory: Callable[[], SubjectBinding | None] | None = None,
        lease_expires_at_utc: str | None = None,
    ) -> dict[str, Any]:
        timestamp = _utc_timestamp(self._clock())
        payload_digest = _sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        try:
            receipt = self._store.fence_session_transition(
                command_id=command_id,
                session_id=session_id,
                command_kind=command_kind,
                idempotency_key_hash=_sha256(idempotency_key),
                payload_digest=payload_digest,
                expected_generation=expected_generation,
                at_utc=timestamp,
                writer_token=self._writer_token,
            )
        except Exception as exc:
            reason = str(exc).casefold()
            if "stale session generation" in reason:
                raise AccessBindingError("stale_session_generation") from exc
            if "idempotency" in reason:
                raise AccessBindingError("session_transition_idempotency_conflict") from exc
            if "already in progress" in reason:
                raise AccessBindingError("session_transition_in_progress") from exc
            raise
        if receipt["state"] == "completed":
            return {**receipt["result"], "replayed": True}
        if run_barrier and self._privacy_barrier is not None:
            try:
                self._privacy_barrier(session_id, expected_generation, barrier_reason)
            except Exception as exc:
                raise AccessBindingError("privacy_barrier_failed") from exc
        if binding_factory is not None:
            owner_binding = binding_factory()
        timestamp = _utc_timestamp(self._clock())
        generation = expected_generation + 1
        participants: list[SessionParticipant] = []
        if owner is not None:
            if owner_binding is None or owner_role is None:
                raise AccessBindingError("session_owner_binding_unavailable")
            participants.append(
                self._participant(
                    session_id=session_id,
                    generation=generation,
                    subject=owner,
                    role=owner_role,
                    assurance=owner_binding.assurance,
                    binding_source=owner_binding.binding_source,
                    binding_id=owner_binding.binding_id,
                    lease_expires_at_utc=lease_expires_at_utc,
                    timestamp=timestamp,
                )
            )
            javis = self.ensure_javis_subject()
            participants.append(
                self._participant(
                    session_id=session_id,
                    generation=generation,
                    subject=javis,
                    role=ParticipantRole.JAVIS,
                    assurance=IdentityAssurance.VERIFIED,
                    binding_source=BindingSource.DESKTOP_PROFILE,
                    binding_id=None,
                    lease_expires_at_utc=None,
                    timestamp=timestamp,
                )
            )
        if guest_present or owner is None:
            guest = self._guest_subject(session_id, generation, timestamp)
            existing_guest = self._store.get_subject(guest.subject_id)
            if existing_guest is None:
                self._store.put_subject(guest, writer_token=self._writer_token)
            elif existing_guest != guest:
                raise AccessBindingError("guest_subject_conflict")
            participants.append(
                self._participant(
                    session_id=session_id,
                    generation=generation,
                    subject=guest,
                    role=ParticipantRole.GUEST,
                    assurance=IdentityAssurance.GUEST,
                    binding_source=BindingSource.GUEST_DEFAULT,
                    binding_id=None,
                    lease_expires_at_utc=None,
                    timestamp=timestamp,
                )
            )
        final_state = SessionGenerationState(
            schema_version=1,
            session_id=session_id,
            generation=generation,
            guest_present=guest_present or owner is None,
            privacy_fenced=False,
            owner_subject_id=None if owner is None else owner.subject_id,
            active_binding_id=None if owner_binding is None else owner_binding.binding_id,
            revision=2,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
        )
        try:
            return self._store.complete_session_transition(
                command_id,
                final_state,
                tuple(participants),
                at_utc=timestamp,
                writer_token=self._writer_token,
            )
        except Exception as exc:
            if "idempotency" in str(exc).casefold():
                raise AccessBindingError("session_transition_idempotency_conflict") from exc
            raise

    def _guest_subject(
        self, session_id: str, generation: int, timestamp: str
    ) -> Subject:
        return Subject(
            schema_version=1,
            subject_id=self._stable_id(
                "subject-guest", f"{self._runtime_boot_id}\0{session_id}\0{generation}"
            ),
            revision=1,
            subject_kind=SubjectKind.SESSION_GUEST,
            display_name="Guest",
            status=SubjectStatus.ACTIVE,
            identity_assurance=IdentityAssurance.GUEST,
            credential_reference_hash=None,
            merged_into_subject_id=None,
            session_scope_id=session_id,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            aliases=(),
            created_by_subject_id=None,
            assurance_ceiling=IdentityAssurance.GUEST,
        )

    def _participant(
        self,
        *,
        session_id: str,
        generation: int,
        subject: Subject,
        role: ParticipantRole,
        assurance: IdentityAssurance,
        binding_source: BindingSource,
        binding_id: str | None,
        lease_expires_at_utc: str | None,
        timestamp: str,
    ) -> SessionParticipant:
        return SessionParticipant(
            schema_version=1,
            participant_id=self._stable_id(
                "participant",
                f"{session_id}\0{generation}\0{subject.subject_id}\0{role.value}",
            ),
            revision=1,
            session_id=session_id,
            subject_id=subject.subject_id,
            participant_role=role,
            identity_assurance=assurance,
            joined_at_utc=timestamp,
            left_at_utc=None,
            server_binding_source=binding_source.value,
            status=ParticipantStatus.ACTIVE,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            session_generation=generation,
            binding_id=binding_id,
            lease_expires_at_utc=lease_expires_at_utc,
            active=True,
        )

    def _read_participants(self, session_id: str) -> tuple[SessionParticipant, ...]:
        try:
            participants = self._store.active_session_participants(session_id)
        except Exception as exc:
            raise AccessBindingError("memory_store_unavailable") from exc
        if len(participants) > _MAX_PARTICIPANTS:
            raise AccessBindingError("participant_limit_exceeded")
        return participants

    def lock_session(
        self, command: LockSession, principal: ServerPrincipal
    ) -> dict[str, Any]:
        now = self._clock()
        self._authorize_command(command.access_context, principal, "participants.manage", now)
        if command.expected_generation != command.access_context.session_generation:
            raise AccessBindingError("stale_session_generation")
        replay = self._store.get_session_transition_receipt(command.command_id)
        if replay is not None and replay["state"] == "completed":
            return self._transition_session(
                command,
                expected_generation=command.expected_generation,
                owner=None,
                owner_binding=None,
                owner_role=None,
                guest_present=True,
                barrier_reason="session_locked",
            )
        try:
            binding = self._require_active_binding(command.access_context, now)
        except AccessBindingError:
            if replay is None:
                raise
            binding = None

        def revoke_binding() -> SubjectBinding | None:
            if binding is not None:
                self._store.revoke_subject_binding(
                    binding.binding_id,
                    _utc_timestamp(self._clock()),
                    writer_token=self._writer_token,
                )
            return None

        return self._transition_session(
            command,
            expected_generation=command.expected_generation,
            owner=None,
            owner_binding=None,
            owner_role=None,
            guest_present=True,
            barrier_reason="session_locked",
            binding_factory=revoke_binding,
        )

    def disable_subject(
        self, command: DisableSubject, principal: ServerPrincipal
    ) -> Subject:
        now = self._clock()
        self._authorize_command(command.access_context, principal, "identity.manage", now)
        self._require_active_primary(command.access_context, now)
        subject = self._store.get_subject(command.target_subject_id)
        if subject is None or subject.subject_kind is SubjectKind.JAVIS:
            raise AccessBindingError("subject_unavailable")
        if subject.revision != command.expected_revision:
            raise AccessBindingError("subject_revision_conflict")
        if subject.status is SubjectStatus.DISABLED:
            return subject
        if subject.status is not SubjectStatus.ACTIVE:
            raise AccessBindingError("subject_not_active")
        timestamp = _utc_timestamp(now)
        for state in self._store.session_generations():
            if state.owner_subject_id != subject.subject_id:
                continue
            command_id = self._stable_id(
                "disable-session", f"{command.command_id}\0{state.session_id}"
            )
            self._transition_values(
                command_id=command_id,
                command_kind="DisableSubjectSession",
                idempotency_key=command_id,
                payload={
                    "disable_command_id": command.command_id,
                    "session_id": state.session_id,
                    "expected_generation": state.generation,
                },
                session_id=state.session_id,
                expected_generation=state.generation,
                owner=None,
                owner_binding=None,
                owner_role=None,
                guest_present=True,
                barrier_reason="subject_disabled",
            )
        self._store.revoke_subject_bindings(
            subject.subject_id,
            timestamp,
            writer_token=self._writer_token,
        )
        disabled = replace(
            subject,
            revision=subject.revision + 1,
            status=SubjectStatus.DISABLED,
            updated_at_utc=timestamp,
        )
        self._store.put_subject(disabled, writer_token=self._writer_token)
        return disabled

    def retire_stale_bindings(self) -> int:
        return self._store.retire_subject_bindings(
            self._runtime_boot_id,
            _utc_timestamp(self._clock()),
            writer_token=self._writer_token,
        )

    def _create_or_replay_binding(
        self,
        *,
        command_id: str,
        subject: Subject,
        principal: ServerPrincipal,
        now: float,
        assurance: IdentityAssurance = IdentityAssurance.DESKTOP_CONFIRMED,
        source: BindingSource = BindingSource.DESKTOP_PROFILE,
        expires_at_epoch: float | None = None,
    ) -> SubjectBinding:
        active = self._store.active_subject_binding(
            self._runtime_boot_id, principal.client_id_hash
        )
        if active is not None:
            if active.subject_id != subject.subject_id:
                raise AccessBindingError("client_binding_conflict")
            return active
        binding_id = self._stable_id("binding", command_id)
        existing = self._store.get_subject_binding(binding_id)
        if existing is not None:
            if (
                existing.subject_id != subject.subject_id
                or existing.runtime_boot_id != self._runtime_boot_id
                or existing.client_id_hash != principal.client_id_hash
                or existing.assurance is not assurance
                or existing.binding_source is not source
            ):
                raise AccessBindingError("binding_idempotency_conflict")
            if existing.status is not BindingStatus.ACTIVE:
                raise AccessBindingError("binding_replay_retired")
            return existing
        binding = SubjectBinding(
            schema_version=1,
            binding_id=binding_id,
            revision=1,
            subject_id=subject.subject_id,
            runtime_boot_id=self._runtime_boot_id,
            client_id_hash=principal.client_id_hash,
            assurance=assurance,
            binding_source=source,
            status=BindingStatus.ACTIVE,
            issued_at_utc=_utc_timestamp(now),
            expires_at_utc=_utc_timestamp(
                principal.expires_at_epoch
                if expires_at_epoch is None
                else min(principal.expires_at_epoch, expires_at_epoch)
            ),
            revoked_at_utc=None,
        )
        self._store.put_subject_binding(binding, writer_token=self._writer_token)
        return binding

    def _require_active_primary(
        self, context: AccessContext, now: float
    ) -> SubjectBinding:
        binding = self._require_active_binding(context, now)
        subject = self._store.get_subject(binding.subject_id)
        if (
            subject is None
            or subject.subject_kind is not SubjectKind.PRIMARY_USER
            or subject.status is not SubjectStatus.ACTIVE
            or context.actor_kind is not ActorKind.PRIMARY_USER
            or context.actor_subject_id != subject.subject_id
        ):
            raise AccessBindingError("active_primary_required")
        return binding

    def _require_active_binding(
        self, context: AccessContext, now: float
    ) -> SubjectBinding:
        if context.binding_id is None:
            raise AccessBindingError("active_binding_required")
        binding = self._store.active_subject_binding(
            self._runtime_boot_id, context.client_id_hash
        )
        if (
            binding is None
            or binding.binding_id != context.binding_id
            or binding.subject_id != context.actor_subject_id
            or self._timestamp_epoch(binding.expires_at_utc) <= now
        ):
            raise AccessBindingError("active_binding_required")
        return binding

    def _authorize_command(
        self,
        context: AccessContext,
        principal: ServerPrincipal,
        required_scope: str,
        now: float,
    ) -> None:
        if not isinstance(context, AccessContext):
            raise AccessBindingError("server_access_context_required")
        if not isinstance(principal, ServerPrincipal):
            raise AccessBindingError("server_principal_required")
        if principal.binding_source is not PrincipalBindingSource.PACKAGED_DESKTOP:
            raise AccessBindingError("packaged_desktop_required")
        if principal.runtime_boot_id != self._runtime_boot_id:
            raise AccessBindingError("runtime_boot_mismatch")
        if principal.session_id != context.session_id:
            raise AccessBindingError("session_mismatch")
        if principal.client_id_hash != context.client_id_hash:
            raise AccessBindingError("client_binding_mismatch")
        if not (principal.issued_at_epoch <= now < principal.expires_at_epoch):
            raise AccessBindingError("principal_expired")
        if required_scope not in principal.capability_scopes:
            raise AccessBindingError("scope_denied")
        if required_scope not in context.capability_scopes:
            raise AccessBindingError("context_scope_denied")
        if context.runtime_boot_id != self._runtime_boot_id:
            raise AccessBindingError("context_boot_mismatch")
        if not (
            self._timestamp_epoch(context.issued_at_utc)
            <= now
            < self._timestamp_epoch(context.expires_at_utc)
        ):
            raise AccessBindingError("access_context_expired")
        if self._timestamp_epoch(context.expires_at_utc) > principal.expires_at_epoch + 0.001:
            raise AccessBindingError("context_expiry_mismatch")

    @staticmethod
    def _stable_id(prefix: str, command_id: str) -> str:
        return f"{prefix}-{_sha256(prefix + chr(0) + command_id)[:32]}"

    @staticmethod
    def _timestamp_epoch(value: str) -> float:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        ).timestamp()

    def _clock(self) -> float:
        value = float(self._now())
        if not math.isfinite(value):
            raise AccessBindingError("clock_unavailable")
        return value


class AccessContextFactory:
    """Resolve server principals against active session participants."""

    def __init__(
        self,
        store: _MemoryAccessStore | None,
        *,
        runtime_boot_id: str,
        now: Callable[[], float] | None = None,
        context_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._runtime_boot_id = _bounded_identifier(runtime_boot_id, "runtime_boot_id")
        self._now = now or time.time
        self._context_id_factory = context_id_factory or (lambda: f"context-{uuid.uuid4().hex}")

    def for_session(
        self,
        session_id: str,
        *,
        principal: ServerPrincipal | None,
        purpose: AccessPurpose | str = AccessPurpose.CONVERSATION,
    ) -> AccessContext:
        session = _bounded_identifier(session_id, "session_id")
        self._ensure_session(session)
        normalized_purpose = _normalize_purpose(purpose)
        now = self._clock()
        snapshot = self._access_snapshot(session, principal)
        acl_epoch = int(snapshot["acl_epoch"])
        state = snapshot["generation"]
        if self._principal_authorized(principal, session, normalized_purpose, now):
            resolved = self._resolve_actor(principal, now, state, snapshot)
            if resolved is not None:
                actor, binding, participant_ids = resolved
                expires = min(principal.expires_at_epoch, now + _MAX_CONTEXT_TTL_SECONDS)
                return self._context(
                    session_id=session,
                    principal=principal,
                    purpose=normalized_purpose,
                    actor_subject_id=actor.subject_id,
                    actor_kind=(
                        ActorKind.PRIMARY_USER
                        if actor.subject_kind is SubjectKind.PRIMARY_USER
                        else ActorKind.KNOWN_PERSON
                    ),
                    participant_subject_ids=participant_ids,
                    audience_ceiling=(
                        Audience.GUEST
                        if state is not None and state.guest_present
                        else Audience.EXPLICIT_SHARED
                    ),
                    identity_assurance=binding.assurance,
                    acl_epoch=acl_epoch,
                    issued_at=now,
                    expires_at=expires,
                    session_generation=0 if state is None else state.generation,
                    guest_present=True if state is None else state.guest_present,
                    binding_id=binding.binding_id,
                    binding_assurance=binding.assurance,
                )
        return self._guest_context(
            session_id=session,
            principal=principal,
            purpose=normalized_purpose,
            acl_epoch=acl_epoch,
            issued_at=now,
            state=state,
            participants=snapshot["participants"],
        )

    def for_identity_management(
        self,
        session_id: str,
        *,
        principal: ServerPrincipal | None,
    ) -> AccessContext:
        return self._for_management_scope(
            session_id,
            principal=principal,
            required_scope="identity.manage",
        )

    def for_participant_management(
        self,
        session_id: str,
        *,
        principal: ServerPrincipal | None,
    ) -> AccessContext:
        return self._for_management_scope(
            session_id,
            principal=principal,
            required_scope="participants.manage",
        )

    def _for_management_scope(
        self,
        session_id: str,
        *,
        principal: ServerPrincipal | None,
        required_scope: str,
    ) -> AccessContext:
        session = _bounded_identifier(session_id, "session_id")
        self._ensure_session(session)
        now = self._clock()
        snapshot = self._access_snapshot(session, principal)
        acl_epoch = int(snapshot["acl_epoch"])
        state = snapshot["generation"]
        if self._principal_valid_for_scope(principal, session, required_scope, now):
            assert principal is not None
            resolved = self._resolve_actor(principal, now, state, snapshot)
            if resolved is not None:
                actor, binding, participant_ids = resolved
                return self._context(
                    session_id=session,
                    principal=principal,
                    purpose=AccessPurpose.MANAGE,
                    actor_subject_id=actor.subject_id,
                    actor_kind=(
                        ActorKind.PRIMARY_USER
                        if actor.subject_kind is SubjectKind.PRIMARY_USER
                        else ActorKind.KNOWN_PERSON
                    ),
                    participant_subject_ids=participant_ids,
                    audience_ceiling=(
                        Audience.GUEST
                        if state is not None and state.guest_present
                        else Audience.OWNER_PRIVATE
                    ),
                    identity_assurance=binding.assurance,
                    acl_epoch=acl_epoch,
                    issued_at=now,
                    expires_at=min(
                        principal.expires_at_epoch, now + _MAX_CONTEXT_TTL_SECONDS
                    ),
                    session_generation=0 if state is None else state.generation,
                    guest_present=True if state is None else state.guest_present,
                    binding_id=binding.binding_id,
                    binding_assurance=binding.assurance,
                )
            return self._guest_context(
                session_id=session,
                principal=principal,
                purpose=AccessPurpose.MANAGE,
                acl_epoch=acl_epoch,
                issued_at=now,
                retain_principal_scopes=True,
                state=state,
                participants=snapshot["participants"],
            )
        return self._guest_context(
            session_id=session,
            principal=principal,
            purpose=AccessPurpose.MANAGE,
            acl_epoch=acl_epoch,
            issued_at=now,
            state=state,
            participants=snapshot["participants"],
        )

    def _principal_authorized(
        self,
        principal: ServerPrincipal | None,
        session_id: str,
        purpose: AccessPurpose,
        now: float,
    ) -> bool:
        return self._principal_valid_for_scope(
            principal,
            session_id,
            _PURPOSE_SCOPE[purpose],
            now,
        )

    def _principal_valid_for_scope(
        self,
        principal: ServerPrincipal | None,
        session_id: str,
        required_scope: str,
        now: float,
    ) -> bool:
        return bool(
            isinstance(principal, ServerPrincipal)
            and principal.runtime_boot_id == self._runtime_boot_id
            and principal.session_id == session_id
            and principal.binding_source is PrincipalBindingSource.PACKAGED_DESKTOP
            and principal.issued_at_epoch <= now < principal.expires_at_epoch
            and required_scope in principal.capability_scopes
        )

    def _resolve_actor(
        self,
        principal: ServerPrincipal,
        now: float,
        state: SessionGenerationState | None,
        snapshot: Mapping[str, Any],
    ) -> tuple[Subject, SubjectBinding, tuple[str, ...]] | None:
        if (
            not self._store_ready()
            or state is None
            or state.privacy_fenced
            or state.owner_subject_id is None
            or state.active_binding_id is None
        ):
            return None
        try:
            binding = snapshot.get("binding")
            subjects = snapshot.get("subjects")
            participants = snapshot.get("participants")
            if (
                not isinstance(binding, SubjectBinding)
                or not isinstance(subjects, Mapping)
                or not isinstance(participants, tuple)
                or self._binding_expired(binding, now)
            ):
                return None
            actor = subjects.get(binding.subject_id)
            javis = subjects.get(MemorySubjectBinder.JAVIS_SUBJECT_ID)
        except Exception:
            return None
        if actor is None or javis is None:
            return None
        if (
            actor.status is not SubjectStatus.ACTIVE
            or actor.subject_kind not in {SubjectKind.PRIMARY_USER, SubjectKind.KNOWN_PERSON}
            or binding.status is not BindingStatus.ACTIVE
            or binding.assurance is IdentityAssurance.VERIFIED
            or binding.runtime_boot_id != self._runtime_boot_id
            or binding.client_id_hash != principal.client_id_hash
            or binding.binding_id != state.active_binding_id
            or actor.subject_id != state.owner_subject_id
            or javis.status is not SubjectStatus.ACTIVE
            or javis.subject_kind is not SubjectKind.JAVIS
            or javis.identity_assurance is not IdentityAssurance.VERIFIED
        ):
            return None
        current = tuple(
            item
            for item in participants
            if item.active and item.session_generation == state.generation
        )
        participant_ids = tuple(sorted(item.subject_id for item in current))
        owner_rows = tuple(
            item
            for item in current
            if item.subject_id == actor.subject_id
            and item.binding_id == binding.binding_id
            and item.participant_role in {ParticipantRole.PRIMARY, ParticipantRole.OWNER}
        )
        if len(owner_rows) != 1 or len(participant_ids) != len(set(participant_ids)):
            return None
        return actor, binding, participant_ids

    @staticmethod
    def _binding_expired(binding: SubjectBinding, now: float) -> bool:
        expires = datetime.strptime(
            binding.expires_at_utc, "%Y-%m-%dT%H:%M:%S.%fZ"
        ).replace(tzinfo=timezone.utc)
        return expires.timestamp() <= now

    def _guest_context(
        self,
        *,
        session_id: str,
        principal: ServerPrincipal | None,
        purpose: AccessPurpose,
        acl_epoch: int,
        issued_at: float,
        retain_principal_scopes: bool = False,
        state: SessionGenerationState | None = None,
        participants: tuple[SessionParticipant, ...] = (),
    ) -> AccessContext:
        valid_hash = (
            principal.client_id_hash
            if isinstance(principal, ServerPrincipal)
            else _sha256(f"guest-client\0{self._runtime_boot_id}\0{session_id}")
        )
        guest_ids: tuple[str, ...] = ()
        if state is not None and not state.privacy_fenced:
            guest_ids = tuple(
                sorted(
                    item.subject_id
                    for item in participants
                    if item.active
                    and item.session_generation == state.generation
                    and item.participant_role is ParticipantRole.GUEST
                )
            )
        guest_id = (
            guest_ids[0]
            if guest_ids
            else "subject-guest-"
            + _sha256(
                f"guest-subject\0{self._runtime_boot_id}\0{session_id}\0{valid_hash}"
            )[:32]
        )
        context_expiry = issued_at + _GUEST_CONTEXT_TTL_SECONDS
        if retain_principal_scopes and isinstance(principal, ServerPrincipal):
            context_expiry = min(context_expiry, principal.expires_at_epoch)
        return self._context(
            session_id=session_id,
            principal=principal if retain_principal_scopes else None,
            purpose=purpose,
            actor_subject_id=guest_id,
            actor_kind=ActorKind.GUEST,
            participant_subject_ids=(guest_id,),
            audience_ceiling=Audience.GUEST,
            identity_assurance=IdentityAssurance.GUEST,
            acl_epoch=acl_epoch,
            issued_at=issued_at,
            expires_at=context_expiry,
            client_id_hash=valid_hash,
            session_generation=0 if state is None else state.generation,
            guest_present=True,
            binding_id=None,
            binding_assurance=IdentityAssurance.GUEST,
        )

    def _context(
        self,
        *,
        session_id: str,
        principal: ServerPrincipal | None,
        purpose: AccessPurpose,
        actor_subject_id: str,
        actor_kind: ActorKind,
        participant_subject_ids: tuple[str, ...],
        audience_ceiling: Audience,
        identity_assurance: IdentityAssurance,
        acl_epoch: int,
        issued_at: float,
        expires_at: float,
        client_id_hash: str | None = None,
        session_generation: int,
        guest_present: bool,
        binding_id: str | None,
        binding_assurance: IdentityAssurance,
    ) -> AccessContext:
        context_id = _bounded_identifier(self._context_id_factory(), "context_id")
        scopes = principal.capability_scopes if principal is not None else ()
        return AccessContext(
            schema_version=1,
            context_id=context_id,
            runtime_boot_id=self._runtime_boot_id,
            client_id_hash=client_id_hash or principal.client_id_hash,
            capability_scopes=scopes,
            actor_subject_id=actor_subject_id,
            actor_kind=actor_kind,
            session_id=session_id,
            participant_subject_ids=participant_subject_ids,
            audience_ceiling=audience_ceiling,
            identity_assurance=identity_assurance,
            purpose=purpose,
            acl_epoch=acl_epoch,
            issued_at_utc=_utc_timestamp(issued_at),
            expires_at_utc=_utc_timestamp(expires_at),
            session_generation=session_generation,
            guest_present=guest_present,
            binding_id=binding_id,
            binding_assurance=binding_assurance,
        )

    def _ensure_session(self, session_id: str) -> None:
        if not self._store_ready():
            return
        ensure = getattr(self._store, "ensure_session", None)
        if not callable(ensure):
            return
        try:
            ensure(session_id)
        except Exception:
            return

    def _access_snapshot(
        self,
        session_id: str,
        principal: ServerPrincipal | None,
    ) -> dict[str, Any]:
        empty = {
            "acl_epoch": 0,
            "generation": None,
            "binding": None,
            "participants": (),
            "subjects": {},
        }
        if not self._store_ready():
            return empty
        client_id_hash = (
            principal.client_id_hash
            if isinstance(principal, ServerPrincipal)
            else _sha256(f"guest-client\0{self._runtime_boot_id}\0{session_id}")
        )
        read_snapshot = getattr(self._store, "session_access_snapshot", None)
        if callable(read_snapshot):
            try:
                value = read_snapshot(session_id, self._runtime_boot_id, client_id_hash)
            except Exception:
                return empty
            if isinstance(value, Mapping):
                acl_epoch = value.get("acl_epoch", 0)
                state = value.get("generation")
                binding = value.get("binding")
                participants = value.get("participants")
                subjects = value.get("subjects")
                return {
                    "acl_epoch": (
                        acl_epoch if type(acl_epoch) is int and acl_epoch >= 0 else 0
                    ),
                    "generation": (
                        state if isinstance(state, SessionGenerationState) else None
                    ),
                    "binding": (
                        binding if isinstance(binding, SubjectBinding) else None
                    ),
                    "participants": (
                        participants
                        if isinstance(participants, tuple)
                        and all(isinstance(item, SessionParticipant) for item in participants)
                        else ()
                    ),
                    "subjects": subjects if isinstance(subjects, Mapping) else {},
                }
            return empty
        return self._legacy_access_snapshot(session_id, principal)

    def _legacy_access_snapshot(
        self,
        session_id: str,
        principal: ServerPrincipal | None,
    ) -> dict[str, Any]:
        state = self._session_state(session_id)
        try:
            participants = self._store.active_session_participants(session_id)
            binding = (
                self._store.active_subject_binding(
                    self._runtime_boot_id, principal.client_id_hash
                )
                if isinstance(principal, ServerPrincipal)
                else None
            )
            subject_ids = {item.subject_id for item in participants}
            if binding is not None:
                subject_ids.add(binding.subject_id)
            subject_ids.add(MemorySubjectBinder.JAVIS_SUBJECT_ID)
            subjects = {
                subject_id: subject
                for subject_id in subject_ids
                if (subject := self._store.get_subject(subject_id)) is not None
            }
        except Exception:
            return {
                "acl_epoch": 0,
                "generation": None,
                "binding": None,
                "participants": (),
                "subjects": {},
            }
        return {
            "acl_epoch": self._acl_epoch(),
            "generation": state,
            "binding": binding,
            "participants": participants,
            "subjects": subjects,
        }

    def _session_state(self, session_id: str) -> SessionGenerationState | None:
        if not self._store_ready():
            return None
        try:
            return self._store.get_session_generation(session_id)
        except Exception:
            return None

    def _acl_epoch(self) -> int:
        if not self._store_ready():
            return 0
        try:
            value = self._store.metadata().get("acl_epoch", 0)
        except Exception:
            return 0
        return value if type(value) is int and value >= 0 else 0

    def _store_ready(self) -> bool:
        if self._store is None:
            return False
        try:
            status = self._store.status()
        except Exception:
            return False
        return isinstance(status, Mapping) and status.get("state") == "ready"

    def _clock(self) -> float:
        value = float(self._now())
        if not math.isfinite(value):
            raise RuntimeError("clock_unavailable")
        return value


class MemoryServiceAccessView:
    """Bounded synchronous read view over the MemoryService read lane."""

    def __init__(self, service: Any, *, timeout: float = 2.0) -> None:
        if service is None:
            raise TypeError("service is required")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        self._service = service
        self._timeout = float(timeout)

    def status(self) -> Mapping[str, Any]:
        try:
            value = self._service.status()
            store = value.get("store", {}) if isinstance(value, Mapping) else {}
        except Exception:
            return {"state": "unavailable", "read_only": True}
        return dict(store) if isinstance(store, Mapping) else {
            "state": "unavailable",
            "read_only": True,
        }

    def metadata(self) -> Mapping[str, Any]:
        status = self.status()
        return {
            "acl_epoch": _nonnegative_int(status.get("acl_epoch")),
            "index_generation": _nonnegative_int(status.get("index_generation")),
            "terminal_cursor": _nonnegative_int(status.get("terminal_cursor")),
        }

    def get_subject(self, subject_id: str) -> Subject | None:
        return self._resolve(self._service.get_subject(subject_id))

    def active_primary_subject(self) -> Subject | None:
        return self._resolve(self._service.active_primary_subject())

    def get_subject_binding(self, binding_id: str) -> SubjectBinding | None:
        return self._resolve(self._service.get_subject_binding(binding_id))

    def active_subject_binding(
        self, runtime_boot_id: str, client_id_hash: str
    ) -> SubjectBinding | None:
        return self._resolve(
            self._service.active_subject_binding(runtime_boot_id, client_id_hash)
        )

    def active_session_participants(
        self, session_id: str
    ) -> tuple[SessionParticipant, ...]:
        value = self._resolve(self._service.active_session_participants(session_id))
        return tuple(value or ())

    def get_session_generation(self, session_id: str) -> SessionGenerationState | None:
        return self._resolve(self._service.get_session_generation(session_id))

    def get_session_transition_receipt(
        self, command_id: str
    ) -> Mapping[str, Any] | None:
        value = self._resolve(self._service.get_session_transition_receipt(command_id))
        return None if value is None else dict(value)

    def ensure_session(self, session_id: str) -> SessionGenerationState:
        return self._resolve(self._service.ensure_session(session_id))

    def session_access_snapshot(
        self, session_id: str, runtime_boot_id: str, client_id_hash: str
    ) -> Mapping[str, Any]:
        value = self._resolve(
            self._service.session_access_snapshot(
                session_id, runtime_boot_id, client_id_hash
            )
        )
        return dict(value or {})

    def _resolve(self, future: Any) -> Any:
        result = getattr(future, "result", None)
        if not callable(result):
            raise RuntimeError("memory_service_read_contract_invalid")
        return result(timeout=self._timeout)


def _nonnegative_int(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def safe_access_projection(context: AccessContext) -> dict[str, Any]:
    """Return the E0 projection stored with ``request.accepted``."""

    if not isinstance(context, AccessContext):
        raise TypeError("context must be an AccessContext")
    return {
        "schema_version": context.schema_version,
        "context_id": context.context_id,
        "runtime_boot_id": context.runtime_boot_id,
        "client_id_hash": context.client_id_hash,
        "capability_scopes": list(context.capability_scopes),
        "actor_subject_id": context.actor_subject_id,
        "actor_kind": context.actor_kind.value,
        "session_id": context.session_id,
        "participant_subject_ids": list(context.participant_subject_ids),
        "audience_ceiling": context.audience_ceiling.value,
        "identity_assurance": context.identity_assurance.value,
        "purpose": context.purpose.value,
        "acl_epoch": context.acl_epoch,
        "issued_at_utc": context.issued_at_utc,
        "expires_at_utc": context.expires_at_utc,
        "session_generation": context.session_generation,
        "guest_present": context.guest_present,
        "binding_id": context.binding_id,
        "binding_assurance": context.binding_assurance.value,
    }


__all__ = [
    "AccessBindingError",
    "AccessContextFactory",
    "MemoryServiceAccessView",
    "MemorySubjectBinder",
    "PrimarySessionBinding",
    "PrincipalBindingSource",
    "RESERVED_CLIENT_ACCESS_FIELDS",
    "ReservedAccessFieldError",
    "ServerPrincipal",
    "reject_reserved_client_access_fields",
    "safe_access_projection",
]
