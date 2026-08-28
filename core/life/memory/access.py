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
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from .contracts import (
    AccessContext,
    AccessPurpose,
    ActorKind,
    Audience,
    IdentityAssurance,
    ParticipantRole,
    ParticipantStatus,
    SessionParticipant,
    Subject,
    SubjectKind,
    SubjectStatus,
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

    def active_session_participants(
        self, session_id: str
    ) -> tuple[SessionParticipant, ...]: ...


class _MemoryBindingStore(_MemoryAccessStore, Protocol):
    def put_subject(self, subject: Subject, *, writer_token: object) -> bool: ...

    def put_session_participant(
        self, participant: SessionParticipant, *, writer_token: object
    ) -> bool: ...


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
    primary_participant: SessionParticipant
    javis_participant: SessionParticipant


class MemorySubjectBinder:
    """Minimal L2 binding path callable only after server desktop authorization."""

    JAVIS_SUBJECT_ID = "subject-javis"

    def __init__(
        self,
        store: _MemoryBindingStore,
        *,
        writer_token: object,
        runtime_boot_id: str,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._writer_token = writer_token
        self._runtime_boot_id = _bounded_identifier(runtime_boot_id, "runtime_boot_id")
        self._now = now or time.time

    def ensure_javis_subject(self) -> Subject:
        existing = self._store.get_subject(self.JAVIS_SUBJECT_ID)
        if existing is not None:
            if (
                existing.subject_kind is not SubjectKind.JAVIS
                or existing.status is not SubjectStatus.ACTIVE
                or existing.identity_assurance is not IdentityAssurance.VERIFIED
            ):
                raise AccessBindingError("javis_subject_conflict")
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
            credential_reference_hash=None,
            merged_into_subject_id=None,
            session_scope_id=None,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
        )
        self._store.put_subject(subject, writer_token=self._writer_token)
        return subject

    def bind_primary_user(
        self,
        session_id: str,
        principal: ServerPrincipal,
        *,
        display_name: str = "Primary user",
    ) -> PrimarySessionBinding:
        session = _bounded_identifier(session_id, "session_id")
        now = self._clock()
        if not isinstance(principal, ServerPrincipal):
            raise AccessBindingError("server_principal_required")
        if principal.binding_source is not PrincipalBindingSource.PACKAGED_DESKTOP:
            raise AccessBindingError("packaged_desktop_required")
        if principal.runtime_boot_id != self._runtime_boot_id:
            raise AccessBindingError("runtime_boot_mismatch")
        if principal.session_id != session:
            raise AccessBindingError("session_mismatch")
        if not (principal.issued_at_epoch <= now < principal.expires_at_epoch):
            raise AccessBindingError("principal_expired")
        if "conversation" not in principal.capability_scopes:
            raise AccessBindingError("scope_denied")

        existing_participants = self._read_participants(session)
        existing_primary = tuple(
            participant
            for participant in existing_participants
            if participant.participant_role is ParticipantRole.PRIMARY
        )
        expected_subject_id = "subject-primary-" + principal.client_id_hash[:32]
        if existing_primary and (
            len(existing_primary) != 1
            or existing_primary[0].subject_id != expected_subject_id
        ):
            raise AccessBindingError("session_primary_conflict")

        javis_subject = self.ensure_javis_subject()
        primary_subject = self._store.get_subject(expected_subject_id)
        if primary_subject is None:
            timestamp = _utc_timestamp(now)
            primary_subject = Subject(
                schema_version=1,
                subject_id=expected_subject_id,
                revision=1,
                subject_kind=SubjectKind.PRIMARY_USER,
                display_name=display_name,
                status=SubjectStatus.ACTIVE,
                identity_assurance=IdentityAssurance.DESKTOP_CONFIRMED,
                credential_reference_hash=principal.client_id_hash,
                merged_into_subject_id=None,
                session_scope_id=None,
                created_at_utc=timestamp,
                updated_at_utc=timestamp,
            )
            self._store.put_subject(primary_subject, writer_token=self._writer_token)
        elif not self._primary_matches(primary_subject, principal):
            raise AccessBindingError("primary_subject_conflict")

        primary_participant = self._existing_or_new_participant(
            existing_participants,
            session_id=session,
            subject_id=primary_subject.subject_id,
            role=ParticipantRole.PRIMARY,
            assurance=IdentityAssurance.DESKTOP_CONFIRMED,
            now=now,
        )
        javis_participant = self._existing_or_new_participant(
            existing_participants,
            session_id=session,
            subject_id=javis_subject.subject_id,
            role=ParticipantRole.JAVIS,
            assurance=IdentityAssurance.VERIFIED,
            now=now,
        )
        return PrimarySessionBinding(
            primary_subject=primary_subject,
            javis_subject=javis_subject,
            primary_participant=primary_participant,
            javis_participant=javis_participant,
        )

    def _existing_or_new_participant(
        self,
        participants: tuple[SessionParticipant, ...],
        *,
        session_id: str,
        subject_id: str,
        role: ParticipantRole,
        assurance: IdentityAssurance,
        now: float,
    ) -> SessionParticipant:
        matches = tuple(item for item in participants if item.subject_id == subject_id)
        if matches:
            if (
                len(matches) != 1
                or matches[0].participant_role is not role
                or matches[0].identity_assurance is not assurance
                or matches[0].server_binding_source != PrincipalBindingSource.PACKAGED_DESKTOP.value
            ):
                raise AccessBindingError("session_participant_conflict")
            return matches[0]
        timestamp = _utc_timestamp(now)
        digest = _sha256(f"participant\0{session_id}\0{subject_id}")[:32]
        participant = SessionParticipant(
            schema_version=1,
            participant_id=f"participant-{digest}",
            revision=1,
            session_id=session_id,
            subject_id=subject_id,
            participant_role=role,
            identity_assurance=assurance,
            joined_at_utc=timestamp,
            left_at_utc=None,
            server_binding_source=PrincipalBindingSource.PACKAGED_DESKTOP.value,
            status=ParticipantStatus.ACTIVE,
            created_at_utc=timestamp,
            updated_at_utc=timestamp,
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

    @staticmethod
    def _primary_matches(subject: Subject, principal: ServerPrincipal) -> bool:
        return (
            subject.subject_kind is SubjectKind.PRIMARY_USER
            and subject.status is SubjectStatus.ACTIVE
            and subject.identity_assurance is IdentityAssurance.DESKTOP_CONFIRMED
            and subject.credential_reference_hash == principal.client_id_hash
        )

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
            resolved = self._resolve_primary(principal, session)
            if resolved is not None:
                actor, participant_ids = resolved
                expires = min(principal.expires_at_epoch, now + _MAX_CONTEXT_TTL_SECONDS)
                return self._context(
                    session_id=session,
                    principal=principal,
                    purpose=normalized_purpose,
                    actor_subject_id=actor.subject_id,
                    actor_kind=ActorKind.PRIMARY_USER,
                    participant_subject_ids=participant_ids,
                    audience_ceiling=Audience.EXPLICIT_SHARED,
                    identity_assurance=IdentityAssurance.DESKTOP_CONFIRMED,
                    acl_epoch=acl_epoch,
                    issued_at=now,
                    expires_at=expires,
                )
        return self._guest_context(
            session_id=session,
            principal=principal,
            purpose=normalized_purpose,
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
        return bool(
            isinstance(principal, ServerPrincipal)
            and principal.runtime_boot_id == self._runtime_boot_id
            and principal.session_id == session_id
            and principal.binding_source is PrincipalBindingSource.PACKAGED_DESKTOP
            and principal.issued_at_epoch <= now < principal.expires_at_epoch
            and _PURPOSE_SCOPE[purpose] in principal.capability_scopes
        )

    def _resolve_primary(
        self, principal: ServerPrincipal, session_id: str
    ) -> tuple[Subject, tuple[str, ...]] | None:
        if not self._store_ready():
            return None
        try:
            participants = self._store.active_session_participants(session_id)
            if not participants or len(participants) > _MAX_PARTICIPANTS:
                return None
            subjects = tuple(self._store.get_subject(item.subject_id) for item in participants)
        except Exception:
            return None
        if any(subject is None for subject in subjects):
            return None
        pairs = tuple(zip(participants, subjects))
        if any(
            participant.status is not ParticipantStatus.ACTIVE
            or subject.status is not SubjectStatus.ACTIVE
            or subject.subject_kind not in {SubjectKind.PRIMARY_USER, SubjectKind.JAVIS}
            for participant, subject in pairs
        ):
            return None
        primary_pairs = tuple(
            (participant, subject)
            for participant, subject in pairs
            if participant.participant_role is ParticipantRole.PRIMARY
            and subject.subject_kind is SubjectKind.PRIMARY_USER
            and participant.identity_assurance is IdentityAssurance.DESKTOP_CONFIRMED
            and participant.server_binding_source
            == PrincipalBindingSource.PACKAGED_DESKTOP.value
            and subject.identity_assurance is IdentityAssurance.DESKTOP_CONFIRMED
            and subject.credential_reference_hash == principal.client_id_hash
        )
        javis_pairs = tuple(
            (participant, subject)
            for participant, subject in pairs
            if participant.participant_role is ParticipantRole.JAVIS
            and subject.subject_kind is SubjectKind.JAVIS
            and subject.identity_assurance is IdentityAssurance.VERIFIED
        )
        if len(primary_pairs) != 1 or len(javis_pairs) != 1 or len(pairs) != 2:
            return None
        primary_subject = primary_pairs[0][1]
        participant_ids = tuple(sorted(subject.subject_id for _, subject in pairs))
        return primary_subject, participant_ids

    def _guest_context(
        self,
        *,
        session_id: str,
        principal: ServerPrincipal | None,
        purpose: AccessPurpose,
        acl_epoch: int,
        issued_at: float,
    ) -> AccessContext:
        valid_hash = (
            principal.client_id_hash
            if isinstance(principal, ServerPrincipal)
            else _sha256(f"guest-client\0{self._runtime_boot_id}\0{session_id}")
        )
        guest_id = "subject-guest-" + _sha256(
            f"guest-subject\0{self._runtime_boot_id}\0{session_id}\0{valid_hash}"
        )[:32]
        return self._context(
            session_id=session_id,
            principal=None,
            purpose=purpose,
            actor_subject_id=guest_id,
            actor_kind=ActorKind.GUEST,
            participant_subject_ids=(guest_id,),
            audience_ceiling=Audience.GUEST,
            identity_assurance=IdentityAssurance.GUEST,
            acl_epoch=acl_epoch,
            issued_at=issued_at,
            expires_at=issued_at + _GUEST_CONTEXT_TTL_SECONDS,
            client_id_hash=valid_hash,
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
