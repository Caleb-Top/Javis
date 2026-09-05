"""Server-owned access contexts and minimal L2 subject binding.

This module deliberately accepts no conversation payload when resolving identity.
Callers first turn an already-authorized runtime capability into a
``ServerPrincipal`` and then resolve that principal against persisted session
participants. Any missing or inconsistent evidence produces a guest context.
"""

from __future__ import annotations

import hashlib
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
    IdentityAssurance,
    ParticipantRole,
    ParticipantStatus,
    SessionParticipant,
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
    LockSession,
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
        self._bind_compatibility_participants(
            command.access_context.session_id,
            primary_subject,
            javis_subject,
            binding,
            now,
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
        javis = self.ensure_javis_subject()
        self._bind_compatibility_participants(
            command.access_context.session_id,
            subject,
            javis,
            binding,
            now,
        )
        return binding

    def _bind_compatibility_participants(
        self,
        session_id: str,
        primary: Subject,
        javis: Subject,
        binding: SubjectBinding,
        now: float,
    ) -> None:
        participants = self._read_participants(session_id)
        self._upsert_participant(
            participants,
            session_id=session_id,
            subject=primary,
            role=ParticipantRole.PRIMARY,
            assurance=IdentityAssurance.DESKTOP_CONFIRMED,
            binding_id=binding.binding_id,
            now=now,
        )
        self._upsert_participant(
            participants,
            session_id=session_id,
            subject=javis,
            role=ParticipantRole.JAVIS,
            assurance=IdentityAssurance.VERIFIED,
            binding_id=None,
            now=now,
        )

    def _upsert_participant(
        self,
        participants: tuple[SessionParticipant, ...],
        *,
        session_id: str,
        subject: Subject,
        role: ParticipantRole,
        assurance: IdentityAssurance,
        binding_id: str | None,
        now: float,
    ) -> SessionParticipant:
        matches = tuple(item for item in participants if item.subject_id == subject.subject_id)
        timestamp = _utc_timestamp(now)
        if matches:
            current = matches[0]
            if len(matches) != 1 or current.participant_role is not role:
                raise AccessBindingError("session_participant_conflict")
            requested = replace(
                current,
                revision=current.revision + 1,
                identity_assurance=assurance,
                left_at_utc=None,
                server_binding_source=PrincipalBindingSource.PACKAGED_DESKTOP.value,
                status=ParticipantStatus.ACTIVE,
                updated_at_utc=timestamp,
                session_generation=0,
                binding_id=binding_id,
                lease_expires_at_utc=None,
                active=True,
            )
            if replace(requested, revision=current.revision, updated_at_utc=current.updated_at_utc) == current:
                return current
            self._store.put_session_participant(
                requested, writer_token=self._writer_token
            )
            return requested
        participant = SessionParticipant(
            schema_version=1,
            participant_id=self._stable_id(
                "participant", f"{session_id}\0{subject.subject_id}"
            ),
            revision=1,
            session_id=session_id,
            subject_id=subject.subject_id,
            participant_role=role,
            identity_assurance=assurance,
            joined_at_utc=timestamp,
            left_at_utc=None,
            server_binding_source=PrincipalBindingSource.PACKAGED_DESKTOP.value,
            status=ParticipantStatus.ACTIVE,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
            session_generation=0,
            binding_id=binding_id,
            lease_expires_at_utc=None,
            active=True,
        )
        self._store.put_session_participant(participant, writer_token=self._writer_token)
        return participant

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
    ) -> bool:
        now = self._clock()
        self._authorize_command(command.access_context, principal, "participants.manage", now)
        if command.expected_generation != command.access_context.session_generation:
            raise AccessBindingError("stale_session_generation")
        binding = self._require_active_binding(command.access_context, now)
        return self._store.revoke_subject_binding(
            binding.binding_id,
            _utc_timestamp(now),
            writer_token=self._writer_token,
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
            assurance=IdentityAssurance.DESKTOP_CONFIRMED,
            binding_source=BindingSource.DESKTOP_PROFILE,
            status=BindingStatus.ACTIVE,
            issued_at_utc=_utc_timestamp(now),
            expires_at_utc=_utc_timestamp(principal.expires_at_epoch),
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
        normalized_purpose = _normalize_purpose(purpose)
        now = self._clock()
        acl_epoch = self._acl_epoch()
        if self._principal_authorized(principal, session, normalized_purpose, now):
            resolved = self._resolve_actor(principal, now)
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
                    audience_ceiling=Audience.EXPLICIT_SHARED,
                    identity_assurance=binding.assurance,
                    acl_epoch=acl_epoch,
                    issued_at=now,
                    expires_at=expires,
                    guest_present=False,
                    binding_id=binding.binding_id,
                    binding_assurance=binding.assurance,
                )
        return self._guest_context(
            session_id=session,
            principal=principal,
            purpose=normalized_purpose,
            acl_epoch=acl_epoch,
            issued_at=now,
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
        now = self._clock()
        acl_epoch = self._acl_epoch()
        if self._principal_valid_for_scope(principal, session, required_scope, now):
            assert principal is not None
            resolved = self._resolve_actor(principal, now)
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
                    audience_ceiling=Audience.OWNER_PRIVATE,
                    identity_assurance=binding.assurance,
                    acl_epoch=acl_epoch,
                    issued_at=now,
                    expires_at=min(
                        principal.expires_at_epoch, now + _MAX_CONTEXT_TTL_SECONDS
                    ),
                    guest_present=False,
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
            )
        return self._guest_context(
            session_id=session,
            principal=principal,
            purpose=AccessPurpose.MANAGE,
            acl_epoch=acl_epoch,
            issued_at=now,
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
        self, principal: ServerPrincipal, now: float
    ) -> tuple[Subject, SubjectBinding, tuple[str, ...]] | None:
        if not self._store_ready():
            return None
        try:
            binding = self._store.active_subject_binding(
                self._runtime_boot_id, principal.client_id_hash
            )
            if binding is None or self._binding_expired(binding, now):
                return None
            actor = self._store.get_subject(binding.subject_id)
            javis = self._store.get_subject(MemorySubjectBinder.JAVIS_SUBJECT_ID)
        except Exception:
            return None
        if actor is None or javis is None:
            return None
        if (
            actor.status is not SubjectStatus.ACTIVE
            or actor.subject_kind not in {SubjectKind.PRIMARY_USER, SubjectKind.KNOWN_PERSON}
            or binding.status is not BindingStatus.ACTIVE
            or binding.assurance is IdentityAssurance.VERIFIED
            or javis.status is not SubjectStatus.ACTIVE
            or javis.subject_kind is not SubjectKind.JAVIS
            or javis.identity_assurance is not IdentityAssurance.VERIFIED
        ):
            return None
        participant_ids = tuple(sorted((actor.subject_id, javis.subject_id)))
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
    ) -> AccessContext:
        valid_hash = (
            principal.client_id_hash
            if isinstance(principal, ServerPrincipal)
            else _sha256(f"guest-client\0{self._runtime_boot_id}\0{session_id}")
        )
        guest_id = "subject-guest-" + _sha256(
            f"guest-subject\0{self._runtime_boot_id}\0{session_id}\0{valid_hash}"
        )[:32]
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
            session_generation=0,
            guest_present=guest_present,
            binding_id=binding_id,
            binding_assurance=binding_assurance,
        )

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
