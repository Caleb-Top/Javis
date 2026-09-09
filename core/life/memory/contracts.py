"""Strict immutable contracts for governed autobiographical memory.

The module is intentionally limited to value objects and validation. It has no
runtime authorization, tool registry, approval, storage, or service imports.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from core.life.contracts import PrivacyClass, RetentionClass


SCHEMA_VERSION = 1
MAX_ID_CHARS = 256
MAX_CODE_CHARS = 128
MAX_TITLE_CHARS = 160
MAX_SUMMARY_CHARS = 1024
MAX_BODY_CHARS = 4096
MAX_PROPOSED_TEXT_CHARS = 2048
MAX_QUERY_CHARS = 512
MAX_RECALL_ITEMS = 8
MAX_RECALL_ITEM_CHARS = 512
MAX_RECALL_TOTAL_BYTES = 4096

_RFC3339_MILLISECONDS = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_Contract = TypeVar("_Contract", bound="_WireContract")


class ActorKind(str, Enum):
    PRIMARY_USER = "primary_user"
    KNOWN_PERSON = "known_person"
    GUEST = "guest"
    JAVIS = "javis"


class Audience(str, Enum):
    GUEST = "guest"
    OWNER_PRIVATE = "owner_private"
    PARTICIPANTS = "participants"
    EXPLICIT_SHARED = "explicit_shared"


class IdentityAssurance(str, Enum):
    GUEST = "guest"
    DESKTOP_CONFIRMED = "desktop_confirmed"
    OWNER_ATTESTED = "owner_attested"
    VERIFIED = "verified"


class AccessPurpose(str, Enum):
    CONVERSATION = "conversation"
    RECALL = "recall"
    MANAGE = "manage"
    DELETE = "delete"
    MIGRATION = "migration"


class EpisodeOutcome(str, Enum):
    COMPLETED = "completed"


class MemoryItemStatus(str, Enum):
    QUARANTINED = "quarantined"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETION_FENCED = "deletion_fenced"


class JournalEntryKind(str, Enum):
    DAILY_NOTE = "daily_note"
    BOUNDARY_REFLECTION = "boundary_reflection"
    CONTINUITY_NOTE = "continuity_note"


class SharedMemoryStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    REVOKED = "revoked"
    DELETION_FENCED = "deletion_fenced"


class ClaimEpistemicClass(str, Enum):
    EXPLICIT_STATEMENT = "explicit_statement"
    OBSERVED = "observed"
    INFERRED = "inferred"
    CONFIRMED = "confirmed"


class ClaimValueType(str, Enum):
    STRING = "string"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    NUMBER = "number"
    STRING_SET = "string_set"
    TIME_WINDOW = "time_window"


class ClaimSensitivity(str, Enum):
    ORDINARY = "ordinary"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    HIGHLY_SENSITIVE = "highly_sensitive"
    RESTRICTED = "restricted"


class ClaimStatus(str, Enum):
    QUARANTINED = "quarantined"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    EXPIRED = "expired"
    DELETION_FENCED = "deletion_fenced"


class RelationshipEventKind(str, Enum):
    BOUNDARY = "boundary"
    AGREEMENT = "agreement"
    COMMITMENT = "commitment"
    CORRECTION = "correction"
    SHARED_CONFIRMATION = "shared_confirmation"
    BOUNDARY_CONFIRMED = "boundary_confirmed"
    BOUNDARY_CHANGED = "boundary_changed"
    BOUNDARY_REVOKED = "boundary_revoked"
    COMMITMENT_CREATED = "commitment_created"
    COMMITMENT_FULFILLED = "commitment_fulfilled"
    COMMITMENT_MISSED = "commitment_missed"
    COMMITMENT_CANCELLED = "commitment_cancelled"
    SHARED_MEMORY_CONFIRMED = "shared_memory_confirmed"
    SHARED_MEMORY_REVOKED = "shared_memory_revoked"
    CORRECTION_ACKNOWLEDGED = "correction_acknowledged"
    REPAIR_ACKNOWLEDGED = "repair_acknowledged"
    MILESTONE_CONFIRMED = "milestone_confirmed"


class RelationshipEventStatus(str, Enum):
    QUARANTINED = "quarantined"
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    DELETION_FENCED = "deletion_fenced"


class SubjectKind(str, Enum):
    JAVIS = "javis"
    PRIMARY_USER = "primary_user"
    KNOWN_PERSON = "known_person"
    SESSION_GUEST = "session_guest"
    GUEST = "guest"


class SubjectStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"
    MERGED = "merged"
    DELETED = "deleted"


class ParticipantRole(str, Enum):
    PRIMARY = "primary"
    OWNER = "owner"
    PARTICIPANT = "participant"
    JAVIS = "javis"
    GUEST = "guest"


class ParticipantStatus(str, Enum):
    ACTIVE = "active"
    LEFT = "left"
    REVOKED = "revoked"
    FENCED = "fenced"


class BindingSource(str, Enum):
    DESKTOP_PROFILE = "desktop_profile"
    OWNER_HANDOFF = "owner_handoff"
    GUEST_DEFAULT = "guest_default"


class BindingStatus(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class RelationshipDirection(str, Enum):
    JAVIS_TO_SUBJECT = "javis_to_subject"
    SUBJECT_TO_JAVIS = "subject_to_javis"
    MUTUAL = "mutual"


class GuidanceVerbosity(str, Enum):
    BRIEF = "brief"
    BALANCED = "balanced"
    DETAILED = "detailed"


class GuidanceDirectness(str, Enum):
    DIRECT = "direct"
    NEUTRAL = "neutral"
    GENTLE = "gentle"


class GuidanceFormality(str, Enum):
    CASUAL = "casual"
    NEUTRAL = "neutral"
    FORMAL = "formal"


class MemoryItemKind(str, Enum):
    EXPERIENCE_EPISODE = "experience_episode"
    JOURNAL_ENTRY = "journal_entry"
    SHARED_MEMORY = "shared_memory"
    USER_MODEL_CLAIM = "user_model_claim"
    RELATIONSHIP_EVENT = "relationship_event"


class DerivationRelation(str, Enum):
    DERIVED_FROM = "derived_from"
    CORRECTS = "corrects"
    SUPERSEDES = "supersedes"


class DeletionScope(str, Enum):
    ITEM = "item"
    EPISODE = "episode"
    SHARED_MEMORY = "shared_memory"
    SUBJECT_OWNED = "subject_owned"
    SESSION_DERIVED = "session_derived"
    TIME_RANGE = "time_range"
    ALL_MEMORY = "all_memory"


class SourceHandling(str, Enum):
    DERIVED_ONLY = "derived_only"
    SOURCE_AND_DERIVED = "source_and_derived"


class DeletionState(str, Enum):
    ACCEPTED = "accepted"
    FENCED = "fenced"
    SOURCE_PENDING = "source_pending"
    SOURCE_RETAINED = "source_retained"
    PRIMARY_ROWS_DELETED = "primary_rows_deleted"
    DERIVATIONS_DELETED = "derivations_deleted"
    FTS_DELETED = "fts_deleted"
    CACHES_INVALIDATED = "caches_invalidated"
    PROMPT_INVALIDATED = "prompt_invalidated"
    VERIFIED = "verified"


class TerminalOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecallOwnerLabel(str, Enum):
    ACTOR = "actor"
    JAVIS = "javis"
    SHARED = "shared"


class EpistemicLabel(str, Enum):
    EVIDENCE_DERIVED = "evidence_derived"
    INTERPRETATION = "interpretation"
    EXPLICIT_STATEMENT = "explicit_statement"
    CONFIRMED_SHARED = "confirmed_shared"


def _field_error(field_name: str, requirement: str) -> ValueError:
    return ValueError(f"{field_name}: {requirement}")


def _schema(value: Any) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise _field_error("schema_version", f"must equal {SCHEMA_VERSION}")


def _string(
    value: Any,
    field_name: str,
    *,
    max_chars: int,
    optional: bool = False,
    allow_empty: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise _field_error(field_name, "must be a string")
    if not value and not allow_empty:
        raise _field_error(field_name, "must be non-empty")
    if len(value) > max_chars:
        raise _field_error(field_name, f"must contain at most {max_chars} characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _field_error(field_name, "must be valid UTF-8") from exc
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise _field_error(field_name, "must not contain control characters")
    return value


def _id(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    return _string(value, field_name, max_chars=MAX_ID_CHARS, optional=optional)


def _code(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    text = _string(value, field_name, max_chars=MAX_CODE_CHARS, optional=optional)
    if text is not None and _CODE.fullmatch(text) is None:
        raise _field_error(field_name, "must be a lowercase machine code")
    return text


def _text(
    value: Any,
    field_name: str,
    *,
    max_chars: int,
    optional: bool = False,
    allow_empty: bool = False,
) -> str | None:
    return _string(
        value,
        field_name,
        max_chars=max_chars,
        optional=optional,
        allow_empty=allow_empty,
    )


def _timestamp(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _RFC3339_MILLISECONDS.fullmatch(value) is None:
        raise _field_error(field_name, "must be RFC3339 UTC with millisecond precision")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise _field_error(field_name, "must be a valid UTC timestamp") from exc
    return value


def _timestamp_value(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


def _hash(value: Any, field_name: str) -> str:
    if type(value) is not str or _SHA256_HEX.fullmatch(value) is None:
        raise _field_error(field_name, "must be lowercase SHA-256 hex")
    return value


def _enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise _field_error(field_name, f"must be a {enum_type.__name__} wire string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _field_error(field_name, f"unknown {enum_type.__name__} value {value!r}") from exc


def _positive_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise _field_error(field_name, "must be a positive integer")
    return value


def _non_negative_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise _field_error(field_name, "must be a non-negative integer")
    return value


def _bounded_int(value: Any, field_name: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _field_error(field_name, f"must be an integer in [{minimum}, {maximum}]")
    return value


def _unit(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _field_error(field_name, "must be a finite number in [0.0, 1.0]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise _field_error(field_name, "must be a finite number in [0.0, 1.0]")
    return result


def _boolean(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise _field_error(field_name, "must be a boolean")
    return value


def _id_tuple(
    value: Any,
    field_name: str,
    *,
    non_empty: bool = False,
    maximum: int = 64,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _field_error(field_name, "must be an ID collection")
    if non_empty and not value:
        raise _field_error(field_name, "must be non-empty")
    if len(value) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} IDs")
    result = tuple(_id(item, f"{field_name}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must contain unique IDs")
    return result


def _code_tuple(value: Any, field_name: str, *, maximum: int = 32) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} codes")
    result = tuple(_code(item, f"{field_name}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must contain unique codes")
    return result


def _text_tuple(
    value: Any,
    field_name: str,
    *,
    maximum: int = 32,
    max_chars: int = MAX_SUMMARY_CHARS,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} text values")
    result = tuple(
        _text(item, f"{field_name}[{index}]", max_chars=max_chars)
        for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must contain unique values")
    return result


def _enum_tuple(
    value: Any,
    enum_type: type[Enum],
    field_name: str,
    *,
    maximum: int = 16,
) -> tuple[Enum, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} values")
    result = tuple(
        _enum(item, enum_type, f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must contain unique values")
    return result


def _contract(value: Any, contract_type: type[_Contract], field_name: str) -> _Contract:
    if isinstance(value, contract_type):
        return value
    if isinstance(value, Mapping):
        try:
            return contract_type.from_dict(value)
        except ValueError as exc:
            raise _field_error(field_name, str(exc)) from exc
    raise _field_error(field_name, f"must be a {contract_type.__name__}")


def _contract_tuple(
    value: Any,
    contract_type: type[_Contract],
    field_name: str,
    *,
    maximum: int,
) -> tuple[_Contract, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} values")
    return tuple(
        _contract(item, contract_type, f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )


def _wire(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, _WireContract):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class _WireContract:
    @classmethod
    def from_dict(cls: type[_Contract], data: Mapping[str, Any]) -> _Contract:
        if not isinstance(data, Mapping):
            raise ValueError(f"{cls.__name__}: wire value must be an object")
        contract_fields = tuple(fields(cls))
        expected = tuple(field.name for field in contract_fields)
        actual = set(data)
        required = {
            field.name
            for field in contract_fields
            if field.default is MISSING and field.default_factory is MISSING
        }
        missing = required - actual
        if missing:
            raise ValueError(
                f"{cls.__name__}: missing field(s): {', '.join(sorted(missing))}"
            )
        unexpected = actual - set(expected)
        if unexpected:
            rendered = ", ".join(sorted(repr(item) for item in unexpected))
            raise ValueError(f"{cls.__name__}: unexpected field(s): {rendered}")
        return cls(**{name: data[name] for name in expected if name in data})

    def to_dict(self) -> dict[str, Any]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def canonical_hash(self) -> str:
        return canonical_hash(self)


def canonical_json_bytes(value: Mapping[str, Any] | _WireContract) -> bytes:
    """Serialize a contract or mapping as stable compact UTF-8 JSON."""

    payload = value.to_dict() if isinstance(value, _WireContract) else _wire(value)
    if not isinstance(payload, Mapping):
        raise ValueError("canonical JSON root must be an object")
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must contain only finite JSON values") from exc
    return encoded.encode("utf-8")


def canonical_hash(value: Mapping[str, Any] | _WireContract) -> str:
    """Return lowercase SHA-256 over canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


canonical_content_hash = canonical_hash


def _validate_revision_and_times(
    revision: Any,
    created_at_utc: Any,
    updated_at_utc: Any,
    expires_at_utc: Any,
) -> None:
    _positive_int(revision, "revision")
    created = _timestamp(created_at_utc, "created_at_utc")
    updated = _timestamp(updated_at_utc, "updated_at_utc")
    expires = _timestamp(expires_at_utc, "expires_at_utc", optional=True)
    if _timestamp_value(updated) < _timestamp_value(created):
        raise _field_error("updated_at_utc", "must not precede created_at_utc")
    if expires is not None and _timestamp_value(expires) <= _timestamp_value(created):
        raise _field_error("expires_at_utc", "must follow created_at_utc")


@dataclass(frozen=True)
class AccessContext(_WireContract):
    schema_version: int
    context_id: str
    runtime_boot_id: str
    client_id_hash: str
    capability_scopes: tuple[str, ...]
    actor_subject_id: str
    actor_kind: ActorKind
    session_id: str
    participant_subject_ids: tuple[str, ...]
    audience_ceiling: Audience
    identity_assurance: IdentityAssurance
    purpose: AccessPurpose
    acl_epoch: int
    issued_at_utc: str
    expires_at_utc: str
    session_generation: int = 0
    guest_present: bool = True
    binding_id: str | None = None
    binding_assurance: IdentityAssurance = IdentityAssurance.GUEST

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("context_id", "runtime_boot_id", "actor_subject_id", "session_id"):
            _id(getattr(self, field_name), field_name)
        _hash(self.client_id_hash, "client_id_hash")
        object.__setattr__(
            self, "capability_scopes", _code_tuple(self.capability_scopes, "capability_scopes")
        )
        object.__setattr__(self, "actor_kind", _enum(self.actor_kind, ActorKind, "actor_kind"))
        object.__setattr__(
            self,
            "participant_subject_ids",
            _id_tuple(self.participant_subject_ids, "participant_subject_ids", maximum=16),
        )
        object.__setattr__(
            self, "audience_ceiling", _enum(self.audience_ceiling, Audience, "audience_ceiling")
        )
        object.__setattr__(
            self,
            "identity_assurance",
            _enum(self.identity_assurance, IdentityAssurance, "identity_assurance"),
        )
        object.__setattr__(self, "purpose", _enum(self.purpose, AccessPurpose, "purpose"))
        _non_negative_int(self.acl_epoch, "acl_epoch")
        _non_negative_int(self.session_generation, "session_generation")
        _boolean(self.guest_present, "guest_present")
        _id(self.binding_id, "binding_id", optional=True)
        object.__setattr__(
            self,
            "binding_assurance",
            _enum(self.binding_assurance, IdentityAssurance, "binding_assurance"),
        )
        issued = _timestamp(self.issued_at_utc, "issued_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        if _timestamp_value(expires) <= _timestamp_value(issued):
            raise _field_error("expires_at_utc", "must follow issued_at_utc")
        if self.actor_kind is ActorKind.GUEST:
            if self.identity_assurance is not IdentityAssurance.GUEST:
                raise _field_error("identity_assurance", "guest actor requires guest assurance")
            if self.audience_ceiling is not Audience.GUEST:
                raise _field_error("audience_ceiling", "guest actor requires guest ceiling")


@dataclass(frozen=True)
class ExperienceEpisode(_WireContract):
    schema_version: int
    episode_id: str
    revision: int
    owner_subject_id: str
    audience: Audience
    privacy_class: PrivacyClass
    session_id: str
    request_id: str
    participant_subject_ids: tuple[str, ...]
    started_at_utc: str
    ended_at_utc: str
    outcome: EpisodeOutcome
    what_happened: str
    javis_attention: str
    intent_summary: str
    action_summary: str
    verified_result_summary: str
    meaning_for_user: str
    meaning_for_javis: str
    source_terminal_event_id: str
    source_terminal_sequence: int
    source_sequence_domain: str
    source_message_ids: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    source_digest: str
    extractor_version: str
    confidence: float
    status: MemoryItemStatus
    retention_class: RetentionClass
    expires_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "episode_id",
            "owner_subject_id",
            "session_id",
            "request_id",
            "source_terminal_event_id",
        ):
            _id(getattr(self, field_name), field_name)
        object.__setattr__(self, "audience", _enum(self.audience, Audience, "audience"))
        object.__setattr__(
            self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class")
        )
        object.__setattr__(
            self,
            "participant_subject_ids",
            _id_tuple(
                self.participant_subject_ids,
                "participant_subject_ids",
                non_empty=True,
                maximum=16,
            ),
        )
        started = _timestamp(self.started_at_utc, "started_at_utc")
        ended = _timestamp(self.ended_at_utc, "ended_at_utc")
        if _timestamp_value(ended) < _timestamp_value(started):
            raise _field_error("ended_at_utc", "must not precede started_at_utc")
        object.__setattr__(self, "outcome", _enum(self.outcome, EpisodeOutcome, "outcome"))
        _text(self.what_happened, "what_happened", max_chars=MAX_PROPOSED_TEXT_CHARS)
        for field_name in (
            "javis_attention",
            "intent_summary",
            "action_summary",
            "verified_result_summary",
        ):
            _text(getattr(self, field_name), field_name, max_chars=MAX_SUMMARY_CHARS)
        for field_name in ("meaning_for_user", "meaning_for_javis"):
            _text(
                getattr(self, field_name),
                field_name,
                max_chars=MAX_SUMMARY_CHARS,
                allow_empty=True,
            )
        _non_negative_int(self.source_terminal_sequence, "source_terminal_sequence")
        _string(self.source_sequence_domain, "source_sequence_domain", max_chars=MAX_ID_CHARS)
        object.__setattr__(
            self,
            "source_message_ids",
            _id_tuple(self.source_message_ids, "source_message_ids", maximum=64),
        )
        object.__setattr__(
            self,
            "source_event_ids",
            _id_tuple(self.source_event_ids, "source_event_ids", maximum=64),
        )
        if not self.source_message_ids and not self.source_event_ids:
            raise _field_error("source_message_ids", "message or event evidence is required")
        _hash(self.source_digest, "source_digest")
        _code(self.extractor_version, "extractor_version")
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(self, "status", _enum(self.status, MemoryItemStatus, "status"))
        object.__setattr__(
            self, "retention_class", _enum(self.retention_class, RetentionClass, "retention_class")
        )
        _validate_revision_and_times(
            self.revision, self.created_at_utc, self.updated_at_utc, self.expires_at_utc
        )


@dataclass(frozen=True)
class JournalEntry(_WireContract):
    schema_version: int
    entry_id: str
    revision: int
    owner_subject_id: str
    audience: Audience
    privacy_class: PrivacyClass
    range_started_at_utc: str
    range_ended_at_utc: str
    title: str
    body: str
    source_episode_ids: tuple[str, ...]
    entry_kind: JournalEntryKind
    source_digest: str
    status: MemoryItemStatus
    retention_class: RetentionClass
    expires_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.entry_id, "entry_id")
        _id(self.owner_subject_id, "owner_subject_id")
        object.__setattr__(self, "audience", _enum(self.audience, Audience, "audience"))
        object.__setattr__(
            self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class")
        )
        started = _timestamp(self.range_started_at_utc, "range_started_at_utc")
        ended = _timestamp(self.range_ended_at_utc, "range_ended_at_utc")
        if _timestamp_value(ended) < _timestamp_value(started):
            raise _field_error("range_ended_at_utc", "must not precede range_started_at_utc")
        _text(self.title, "title", max_chars=MAX_TITLE_CHARS)
        _text(self.body, "body", max_chars=MAX_BODY_CHARS)
        object.__setattr__(
            self,
            "source_episode_ids",
            _id_tuple(self.source_episode_ids, "source_episode_ids", non_empty=True, maximum=64),
        )
        object.__setattr__(
            self, "entry_kind", _enum(self.entry_kind, JournalEntryKind, "entry_kind")
        )
        _hash(self.source_digest, "source_digest")
        object.__setattr__(self, "status", _enum(self.status, MemoryItemStatus, "status"))
        object.__setattr__(
            self, "retention_class", _enum(self.retention_class, RetentionClass, "retention_class")
        )
        _validate_revision_and_times(
            self.revision, self.created_at_utc, self.updated_at_utc, self.expires_at_utc
        )


@dataclass(frozen=True)
class SharedConfirmationReceipt(_WireContract):
    receipt_id: str
    subject_id: str
    proposal_revision: int
    confirmed_at_utc: str

    def __post_init__(self) -> None:
        _id(self.receipt_id, "receipt_id")
        _id(self.subject_id, "subject_id")
        _positive_int(self.proposal_revision, "proposal_revision")
        _timestamp(self.confirmed_at_utc, "confirmed_at_utc")


@dataclass(frozen=True)
class SharedMemory(_WireContract):
    schema_version: int
    shared_memory_id: str
    revision: int
    proposal_revision: int
    owner_subject_id: str
    audience: Audience
    privacy_class: PrivacyClass
    source_episode_ids: tuple[str, ...]
    proposed_text: str
    participant_subject_ids: tuple[str, ...]
    confirmation_receipts: tuple[SharedConfirmationReceipt, ...]
    status: SharedMemoryStatus
    confirmed_at_utc: str | None
    revoked_at_utc: str | None
    audience_subject_ids: tuple[str, ...]
    source_digest: str
    retention_class: RetentionClass
    expires_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str
    confirmation_set_revision: int = 1
    required_confirmer_subject_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.shared_memory_id, "shared_memory_id")
        _positive_int(self.proposal_revision, "proposal_revision")
        _id(self.owner_subject_id, "owner_subject_id")
        object.__setattr__(self, "audience", _enum(self.audience, Audience, "audience"))
        if self.audience is not Audience.EXPLICIT_SHARED:
            raise _field_error("audience", "shared memory requires explicit_shared")
        object.__setattr__(
            self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class")
        )
        object.__setattr__(
            self,
            "source_episode_ids",
            _id_tuple(self.source_episode_ids, "source_episode_ids", non_empty=True, maximum=64),
        )
        _text(self.proposed_text, "proposed_text", max_chars=MAX_PROPOSED_TEXT_CHARS)
        object.__setattr__(
            self,
            "participant_subject_ids",
            _id_tuple(
                self.participant_subject_ids,
                "participant_subject_ids",
                non_empty=True,
                maximum=16,
            ),
        )
        object.__setattr__(
            self,
            "confirmation_receipts",
            _contract_tuple(
                self.confirmation_receipts,
                SharedConfirmationReceipt,
                "confirmation_receipts",
                maximum=16,
            ),
        )
        receipt_subjects = tuple(receipt.subject_id for receipt in self.confirmation_receipts)
        if len(set(receipt_subjects)) != len(receipt_subjects):
            raise _field_error("confirmation_receipts", "subjects must be unique")
        if any(receipt.proposal_revision != self.proposal_revision for receipt in self.confirmation_receipts):
            raise _field_error("confirmation_receipts", "proposal revisions must match")
        if any(subject not in self.participant_subject_ids for subject in receipt_subjects):
            raise _field_error("confirmation_receipts", "confirmers must be participants")
        _positive_int(self.confirmation_set_revision, "confirmation_set_revision")
        object.__setattr__(
            self,
            "required_confirmer_subject_ids",
            _id_tuple(
                self.required_confirmer_subject_ids,
                "required_confirmer_subject_ids",
                maximum=16,
            ),
        )
        if not set(self.required_confirmer_subject_ids).issubset(
            self.participant_subject_ids
        ):
            raise _field_error(
                "required_confirmer_subject_ids", "must be a participant subset"
            )
        object.__setattr__(self, "status", _enum(self.status, SharedMemoryStatus, "status"))
        confirmed = _timestamp(self.confirmed_at_utc, "confirmed_at_utc", optional=True)
        revoked = _timestamp(self.revoked_at_utc, "revoked_at_utc", optional=True)
        object.__setattr__(
            self,
            "audience_subject_ids",
            _id_tuple(
                self.audience_subject_ids,
                "audience_subject_ids",
                non_empty=True,
                maximum=16,
            ),
        )
        if not set(self.audience_subject_ids).issubset(self.participant_subject_ids):
            raise _field_error("audience_subject_ids", "must be a participant subset")
        if self.status is SharedMemoryStatus.PROPOSED:
            if self.confirmation_receipts or confirmed is not None or revoked is not None:
                raise _field_error("status", "proposed memory cannot contain confirmation state")
        elif self.status is SharedMemoryStatus.CONFIRMED:
            if not self.confirmation_receipts or confirmed is None or revoked is not None:
                raise _field_error("status", "confirmed memory requires receipts and confirmed time")
        elif self.status is SharedMemoryStatus.REJECTED:
            if self.confirmation_receipts or confirmed is not None or revoked is not None:
                raise _field_error("status", "rejected memory cannot contain confirmation state")
        elif self.status is SharedMemoryStatus.REVOKED:
            if not self.confirmation_receipts or confirmed is None or revoked is None:
                raise _field_error("status", "revoked memory requires prior confirmation")
        if confirmed is not None and revoked is not None:
            if _timestamp_value(revoked) < _timestamp_value(confirmed):
                raise _field_error("revoked_at_utc", "must not precede confirmed_at_utc")
        _hash(self.source_digest, "source_digest")
        object.__setattr__(
            self, "retention_class", _enum(self.retention_class, RetentionClass, "retention_class")
        )
        _validate_revision_and_times(
            self.revision, self.created_at_utc, self.updated_at_utc, self.expires_at_utc
        )


@dataclass(frozen=True)
class UserModelClaim(_WireContract):
    schema_version: int
    claim_id: str
    revision: int
    owner_subject_id: str
    audience: Audience
    privacy_class: PrivacyClass
    subject_id: str
    predicate: str
    value_type: ClaimValueType
    value: str | bool | int | float | tuple[str, ...]
    epistemic_class: ClaimEpistemicClass
    confidence: float
    sensitivity: ClaimSensitivity
    status: ClaimStatus
    source_evidence_ids: tuple[str, ...]
    contradiction_claim_ids: tuple[str, ...]
    supersedes_claim_id: str | None
    acl_subject_ids: tuple[str, ...]
    source_digest: str
    confirmed_at_utc: str | None
    expires_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str
    confirmation_event_id: str | None = None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("claim_id", "owner_subject_id", "subject_id"):
            _id(getattr(self, field_name), field_name)
        object.__setattr__(self, "audience", _enum(self.audience, Audience, "audience"))
        object.__setattr__(
            self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class")
        )
        _code(self.predicate, "predicate")
        object.__setattr__(self, "value_type", _enum(self.value_type, ClaimValueType, "value_type"))
        if self.value_type is ClaimValueType.STRING:
            _text(self.value, "value", max_chars=MAX_SUMMARY_CHARS)
        elif self.value_type is ClaimValueType.BOOLEAN:
            if type(self.value) is not bool:
                raise _field_error("value", "must be a boolean")
        elif self.value_type is ClaimValueType.INTEGER:
            if type(self.value) is not int:
                raise _field_error("value", "must be an integer")
        elif self.value_type is ClaimValueType.NUMBER:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise _field_error("value", "must be a finite number")
            numeric = float(self.value)
            if not math.isfinite(numeric):
                raise _field_error("value", "must be a finite number")
            object.__setattr__(self, "value", numeric)
        elif self.value_type is ClaimValueType.STRING_SET:
            object.__setattr__(
                self,
                "value",
                _text_tuple(self.value, "value", maximum=32, max_chars=MAX_TITLE_CHARS),
            )
        else:
            _text(self.value, "value", max_chars=256)
        object.__setattr__(
            self,
            "epistemic_class",
            _enum(self.epistemic_class, ClaimEpistemicClass, "epistemic_class"),
        )
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(
            self, "sensitivity", _enum(self.sensitivity, ClaimSensitivity, "sensitivity")
        )
        object.__setattr__(self, "status", _enum(self.status, ClaimStatus, "status"))
        object.__setattr__(
            self,
            "source_evidence_ids",
            _id_tuple(self.source_evidence_ids, "source_evidence_ids", non_empty=True, maximum=64),
        )
        object.__setattr__(
            self,
            "contradiction_claim_ids",
            _id_tuple(self.contradiction_claim_ids, "contradiction_claim_ids", maximum=64),
        )
        _id(self.supersedes_claim_id, "supersedes_claim_id", optional=True)
        object.__setattr__(
            self,
            "acl_subject_ids",
            _id_tuple(self.acl_subject_ids, "acl_subject_ids", maximum=64),
        )
        _hash(self.source_digest, "source_digest")
        confirmed = _timestamp(self.confirmed_at_utc, "confirmed_at_utc", optional=True)
        if self.status is ClaimStatus.CONFIRMED and confirmed is None:
            raise _field_error("confirmed_at_utc", "confirmed claim requires confirmation time")
        if self.status is not ClaimStatus.CONFIRMED and confirmed is not None:
            raise _field_error("confirmed_at_utc", "only confirmed claims carry confirmation time")
        _id(self.confirmation_event_id, "confirmation_event_id", optional=True)
        if self.status is ClaimStatus.CONFIRMED and self.confirmation_event_id is None:
            raise _field_error(
                "confirmation_event_id", "confirmed claim requires confirmation evidence"
            )
        _validate_revision_and_times(
            self.revision, self.created_at_utc, self.updated_at_utc, self.expires_at_utc
        )


@dataclass(frozen=True)
class RelationshipEvent(_WireContract):
    schema_version: int
    relationship_event_id: str
    revision: int
    owner_subject_id: str
    audience: Audience
    privacy_class: PrivacyClass
    subject_ids: tuple[str, ...]
    event_kind: RelationshipEventKind
    summary: str
    source_evidence_ids: tuple[str, ...]
    source_digest: str
    occurred_at_utc: str
    status: RelationshipEventStatus
    retention_class: RetentionClass
    expires_at_utc: str | None
    created_at_utc: str
    updated_at_utc: str
    direction: RelationshipDirection = RelationshipDirection.MUTUAL
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.relationship_event_id, "relationship_event_id")
        _id(self.owner_subject_id, "owner_subject_id")
        object.__setattr__(self, "audience", _enum(self.audience, Audience, "audience"))
        object.__setattr__(
            self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class")
        )
        object.__setattr__(
            self,
            "subject_ids",
            _id_tuple(self.subject_ids, "subject_ids", non_empty=True, maximum=16),
        )
        object.__setattr__(
            self, "event_kind", _enum(self.event_kind, RelationshipEventKind, "event_kind")
        )
        object.__setattr__(
            self, "direction", _enum(self.direction, RelationshipDirection, "direction")
        )
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        _text(self.summary, "summary", max_chars=MAX_PROPOSED_TEXT_CHARS)
        object.__setattr__(
            self,
            "source_evidence_ids",
            _id_tuple(self.source_evidence_ids, "source_evidence_ids", non_empty=True, maximum=64),
        )
        _hash(self.source_digest, "source_digest")
        _timestamp(self.occurred_at_utc, "occurred_at_utc")
        object.__setattr__(
            self, "status", _enum(self.status, RelationshipEventStatus, "status")
        )
        object.__setattr__(
            self, "retention_class", _enum(self.retention_class, RetentionClass, "retention_class")
        )
        _validate_revision_and_times(
            self.revision, self.created_at_utc, self.updated_at_utc, self.expires_at_utc
        )


@dataclass(frozen=True)
class Subject(_WireContract):
    schema_version: int
    subject_id: str
    revision: int
    subject_kind: SubjectKind
    display_name: str | None
    status: SubjectStatus
    identity_assurance: IdentityAssurance
    credential_reference_hash: str | None
    merged_into_subject_id: str | None
    session_scope_id: str | None
    created_at_utc: str
    updated_at_utc: str
    aliases: tuple[str, ...] = ()
    created_by_subject_id: str | None = None
    assurance_ceiling: IdentityAssurance = IdentityAssurance.GUEST
    privacy_class: PrivacyClass = PrivacyClass.USER_PRIVATE

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.subject_id, "subject_id")
        _positive_int(self.revision, "revision")
        object.__setattr__(
            self, "subject_kind", _enum(self.subject_kind, SubjectKind, "subject_kind")
        )
        _text(self.display_name, "display_name", max_chars=MAX_TITLE_CHARS, optional=True)
        object.__setattr__(self, "status", _enum(self.status, SubjectStatus, "status"))
        object.__setattr__(
            self,
            "identity_assurance",
            _enum(self.identity_assurance, IdentityAssurance, "identity_assurance"),
        )
        object.__setattr__(
            self,
            "aliases",
            _text_tuple(self.aliases, "aliases", maximum=16, max_chars=MAX_TITLE_CHARS),
        )
        _id(self.created_by_subject_id, "created_by_subject_id", optional=True)
        object.__setattr__(
            self,
            "assurance_ceiling",
            _enum(self.assurance_ceiling, IdentityAssurance, "assurance_ceiling"),
        )
        object.__setattr__(
            self,
            "privacy_class",
            _enum(self.privacy_class, PrivacyClass, "privacy_class"),
        )
        if self.subject_kind is not SubjectKind.JAVIS and (
            self.identity_assurance is IdentityAssurance.VERIFIED
            or self.assurance_ceiling is IdentityAssurance.VERIFIED
        ):
            raise _field_error(
                "identity_assurance", "verified assurance is unavailable for human subjects"
            )
        if self.credential_reference_hash is not None:
            _hash(self.credential_reference_hash, "credential_reference_hash")
        _id(self.merged_into_subject_id, "merged_into_subject_id", optional=True)
        _id(self.session_scope_id, "session_scope_id", optional=True)
        if self.subject_kind in {SubjectKind.SESSION_GUEST, SubjectKind.GUEST}:
            if self.session_scope_id is None or self.identity_assurance is not IdentityAssurance.GUEST:
                raise _field_error("session_scope_id", "session guest requires guest session binding")
        elif self.session_scope_id is not None:
            raise _field_error("session_scope_id", "only session guests are session scoped")
        if self.status is SubjectStatus.MERGED:
            if self.merged_into_subject_id is None:
                raise _field_error("merged_into_subject_id", "merged subject requires a target")
        elif self.merged_into_subject_id is not None:
            raise _field_error("merged_into_subject_id", "only merged subjects carry a target")
        created = _timestamp(self.created_at_utc, "created_at_utc")
        updated = _timestamp(self.updated_at_utc, "updated_at_utc")
        if _timestamp_value(updated) < _timestamp_value(created):
            raise _field_error("updated_at_utc", "must not precede created_at_utc")


@dataclass(frozen=True)
class SessionParticipant(_WireContract):
    schema_version: int
    participant_id: str
    revision: int
    session_id: str
    subject_id: str
    participant_role: ParticipantRole
    identity_assurance: IdentityAssurance
    joined_at_utc: str
    left_at_utc: str | None
    server_binding_source: str
    status: ParticipantStatus
    created_at_utc: str
    updated_at_utc: str
    session_generation: int = 0
    binding_id: str | None = None
    lease_expires_at_utc: str | None = None
    active: bool = False

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("participant_id", "session_id", "subject_id"):
            _id(getattr(self, field_name), field_name)
        _positive_int(self.revision, "revision")
        _non_negative_int(self.session_generation, "session_generation")
        _id(self.binding_id, "binding_id", optional=True)
        object.__setattr__(
            self,
            "participant_role",
            _enum(self.participant_role, ParticipantRole, "participant_role"),
        )
        object.__setattr__(
            self,
            "identity_assurance",
            _enum(self.identity_assurance, IdentityAssurance, "identity_assurance"),
        )
        joined = _timestamp(self.joined_at_utc, "joined_at_utc")
        left = _timestamp(self.left_at_utc, "left_at_utc", optional=True)
        lease_expires = _timestamp(
            self.lease_expires_at_utc, "lease_expires_at_utc", optional=True
        )
        if left is not None and _timestamp_value(left) < _timestamp_value(joined):
            raise _field_error("left_at_utc", "must not precede joined_at_utc")
        if lease_expires is not None and _timestamp_value(lease_expires) <= _timestamp_value(joined):
            raise _field_error("lease_expires_at_utc", "must follow joined_at_utc")
        _code(self.server_binding_source, "server_binding_source")
        object.__setattr__(self, "status", _enum(self.status, ParticipantStatus, "status"))
        _boolean(self.active, "active")
        if self.active and self.status is not ParticipantStatus.ACTIVE:
            raise _field_error("active", "only active status may be current")
        if self.status is ParticipantStatus.ACTIVE and left is not None:
            raise _field_error("left_at_utc", "active participant cannot have left time")
        if self.status is ParticipantStatus.LEFT and left is None:
            raise _field_error("left_at_utc", "left participant requires left time")
        created = _timestamp(self.created_at_utc, "created_at_utc")
        updated = _timestamp(self.updated_at_utc, "updated_at_utc")
        if _timestamp_value(updated) < _timestamp_value(created):
            raise _field_error("updated_at_utc", "must not precede created_at_utc")


@dataclass(frozen=True)
class SubjectBinding(_WireContract):
    schema_version: int
    binding_id: str
    revision: int
    subject_id: str
    runtime_boot_id: str
    client_id_hash: str
    assurance: IdentityAssurance
    binding_source: BindingSource
    status: BindingStatus
    issued_at_utc: str
    expires_at_utc: str
    revoked_at_utc: str | None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("binding_id", "subject_id", "runtime_boot_id"):
            _id(getattr(self, field_name), field_name)
        _positive_int(self.revision, "revision")
        _hash(self.client_id_hash, "client_id_hash")
        object.__setattr__(
            self, "assurance", _enum(self.assurance, IdentityAssurance, "assurance")
        )
        object.__setattr__(
            self,
            "binding_source",
            _enum(self.binding_source, BindingSource, "binding_source"),
        )
        object.__setattr__(self, "status", _enum(self.status, BindingStatus, "status"))
        issued = _timestamp(self.issued_at_utc, "issued_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        revoked = _timestamp(self.revoked_at_utc, "revoked_at_utc", optional=True)
        if _timestamp_value(expires) <= _timestamp_value(issued):
            raise _field_error("expires_at_utc", "must follow issued_at_utc")
        if self.status is BindingStatus.ACTIVE and revoked is not None:
            raise _field_error("revoked_at_utc", "active binding cannot be revoked")
        if self.status is BindingStatus.REVOKED and revoked is None:
            raise _field_error("revoked_at_utc", "revoked binding requires a timestamp")


@dataclass(frozen=True)
class HandoffLease(_WireContract):
    schema_version: int
    lease_id: str
    revision: int
    issuer_subject_id: str
    target_subject_id: str
    session_id: str
    session_generation: int
    assurance: IdentityAssurance
    status: BindingStatus
    issued_at_utc: str
    expires_at_utc: str
    consumed_at_utc: str | None
    revoked_at_utc: str | None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "lease_id",
            "issuer_subject_id",
            "target_subject_id",
            "session_id",
        ):
            _id(getattr(self, field_name), field_name)
        if self.issuer_subject_id == self.target_subject_id:
            raise _field_error("target_subject_id", "handoff target must differ from issuer")
        _positive_int(self.revision, "revision")
        _non_negative_int(self.session_generation, "session_generation")
        object.__setattr__(
            self, "assurance", _enum(self.assurance, IdentityAssurance, "assurance")
        )
        if self.assurance is not IdentityAssurance.OWNER_ATTESTED:
            raise _field_error("assurance", "handoff requires owner-attested assurance")
        object.__setattr__(self, "status", _enum(self.status, BindingStatus, "status"))
        issued = _timestamp(self.issued_at_utc, "issued_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        consumed = _timestamp(self.consumed_at_utc, "consumed_at_utc", optional=True)
        revoked = _timestamp(self.revoked_at_utc, "revoked_at_utc", optional=True)
        if _timestamp_value(expires) <= _timestamp_value(issued):
            raise _field_error("expires_at_utc", "must follow issued_at_utc")
        if consumed is not None and revoked is not None:
            raise _field_error("status", "lease cannot be both consumed and revoked")
        if self.status is BindingStatus.ACTIVE and (consumed is not None or revoked is not None):
            raise _field_error("status", "active lease cannot have a terminal timestamp")
        if self.status is BindingStatus.CONSUMED and consumed is None:
            raise _field_error("consumed_at_utc", "consumed lease requires a timestamp")
        if self.status is BindingStatus.REVOKED and revoked is None:
            raise _field_error("revoked_at_utc", "revoked lease requires a timestamp")


@dataclass(frozen=True)
class SessionGenerationState(_WireContract):
    schema_version: int
    session_id: str
    generation: int
    guest_present: bool
    privacy_fenced: bool
    owner_subject_id: str | None
    active_binding_id: str | None
    revision: int
    created_at_utc: str
    updated_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.session_id, "session_id")
        _non_negative_int(self.generation, "generation")
        _boolean(self.guest_present, "guest_present")
        _boolean(self.privacy_fenced, "privacy_fenced")
        _id(self.owner_subject_id, "owner_subject_id", optional=True)
        _id(self.active_binding_id, "active_binding_id", optional=True)
        _positive_int(self.revision, "revision")
        created = _timestamp(self.created_at_utc, "created_at_utc")
        updated = _timestamp(self.updated_at_utc, "updated_at_utc")
        if _timestamp_value(updated) < _timestamp_value(created):
            raise _field_error("updated_at_utc", "must not precede created_at_utc")
        if self.privacy_fenced and (
            self.owner_subject_id is not None or self.active_binding_id is not None
        ):
            raise _field_error(
                "privacy_fenced", "fenced generation cannot retain owner authority"
            )


@dataclass(frozen=True)
class RelationshipView(_WireContract):
    schema_version: int
    subject_id: str
    address_name: str | None
    boundaries: tuple[str, ...]
    commitments: tuple[str, ...]
    recent_milestones: tuple[str, ...]
    shared_memory_ids: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    source_event_ids: tuple[str, ...]
    acl_epoch: int
    generated_at_utc: str
    expires_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.subject_id, "subject_id")
        _text(self.address_name, "address_name", max_chars=MAX_TITLE_CHARS, optional=True)
        for field_name in (
            "boundaries",
            "commitments",
            "recent_milestones",
            "unresolved_conflicts",
        ):
            object.__setattr__(
                self,
                field_name,
                _text_tuple(
                    getattr(self, field_name),
                    field_name,
                    maximum=16,
                    max_chars=MAX_SUMMARY_CHARS,
                ),
            )
        for field_name in ("shared_memory_ids", "source_event_ids"):
            object.__setattr__(
                self,
                field_name,
                _id_tuple(getattr(self, field_name), field_name, maximum=32),
            )
        _non_negative_int(self.acl_epoch, "acl_epoch")
        generated = _timestamp(self.generated_at_utc, "generated_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        if _timestamp_value(expires) <= _timestamp_value(generated):
            raise _field_error("expires_at_utc", "must follow generated_at_utc")


@dataclass(frozen=True)
class CommunicationGuidance(_WireContract):
    schema_version: int
    subject_id: str
    language: str | None
    address_name: str | None
    verbosity: GuidanceVerbosity
    directness: GuidanceDirectness
    formality: GuidanceFormality
    ask_before_sensitive_topic: bool
    speech_rate: float
    source_claim_ids: tuple[str, ...]
    acl_epoch: int
    expires_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.subject_id, "subject_id")
        _code(self.language, "language", optional=True)
        _text(self.address_name, "address_name", max_chars=MAX_TITLE_CHARS, optional=True)
        object.__setattr__(
            self, "verbosity", _enum(self.verbosity, GuidanceVerbosity, "verbosity")
        )
        object.__setattr__(
            self, "directness", _enum(self.directness, GuidanceDirectness, "directness")
        )
        object.__setattr__(
            self, "formality", _enum(self.formality, GuidanceFormality, "formality")
        )
        _boolean(self.ask_before_sensitive_topic, "ask_before_sensitive_topic")
        if isinstance(self.speech_rate, bool) or not isinstance(self.speech_rate, (int, float)):
            raise _field_error("speech_rate", "must be a finite number in [0.9, 1.1]")
        rate = float(self.speech_rate)
        if not math.isfinite(rate) or not 0.9 <= rate <= 1.1:
            raise _field_error("speech_rate", "must be a finite number in [0.9, 1.1]")
        object.__setattr__(self, "speech_rate", rate)
        object.__setattr__(
            self,
            "source_claim_ids",
            _id_tuple(self.source_claim_ids, "source_claim_ids", maximum=32),
        )
        _non_negative_int(self.acl_epoch, "acl_epoch")
        _timestamp(self.expires_at_utc, "expires_at_utc")


@dataclass(frozen=True)
class DerivationEdge(_WireContract):
    schema_version: int
    edge_id: str
    source_kind: MemoryItemKind
    source_id: str
    target_kind: MemoryItemKind
    target_id: str
    relation: DerivationRelation
    extractor: str
    extractor_version: str
    source_digest: str
    created_at_utc: str
    active: bool

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.edge_id, "edge_id")
        object.__setattr__(
            self, "source_kind", _enum(self.source_kind, MemoryItemKind, "source_kind")
        )
        _id(self.source_id, "source_id")
        object.__setattr__(
            self, "target_kind", _enum(self.target_kind, MemoryItemKind, "target_kind")
        )
        _id(self.target_id, "target_id")
        object.__setattr__(
            self, "relation", _enum(self.relation, DerivationRelation, "relation")
        )
        _code(self.extractor, "extractor")
        _code(self.extractor_version, "extractor_version")
        _hash(self.source_digest, "source_digest")
        _timestamp(self.created_at_utc, "created_at_utc")
        _boolean(self.active, "active")
        if self.source_kind is self.target_kind and self.source_id == self.target_id:
            raise _field_error("target_id", "derivation edge cannot reference itself")


@dataclass(frozen=True)
class DeletionTargetSelector(_WireContract):
    item_kind: MemoryItemKind | None
    item_id: str | None
    subject_id: str | None
    session_id: str | None
    range_started_at_utc: str | None
    range_ended_at_utc: str | None

    def __post_init__(self) -> None:
        if self.item_kind is not None:
            object.__setattr__(
                self, "item_kind", _enum(self.item_kind, MemoryItemKind, "item_kind")
            )
        for field_name in ("item_id", "subject_id", "session_id"):
            _id(getattr(self, field_name), field_name, optional=True)
        started = _timestamp(self.range_started_at_utc, "range_started_at_utc", optional=True)
        ended = _timestamp(self.range_ended_at_utc, "range_ended_at_utc", optional=True)
        if (started is None) != (ended is None):
            raise _field_error("range_started_at_utc", "time range endpoints must appear together")
        if started is not None and _timestamp_value(ended) < _timestamp_value(started):
            raise _field_error("range_ended_at_utc", "must not precede range_started_at_utc")

    def validate_scope(self, scope: DeletionScope) -> None:
        present = {
            "item_kind": self.item_kind is not None,
            "item_id": self.item_id is not None,
            "subject_id": self.subject_id is not None,
            "session_id": self.session_id is not None,
            "range": self.range_started_at_utc is not None,
        }
        allowed: dict[DeletionScope, set[str]] = {
            DeletionScope.ITEM: {"item_kind", "item_id"},
            DeletionScope.EPISODE: {"item_id"},
            DeletionScope.SHARED_MEMORY: {"item_id"},
            DeletionScope.SUBJECT_OWNED: {"subject_id"},
            DeletionScope.SESSION_DERIVED: {"session_id"},
            DeletionScope.TIME_RANGE: {"range"},
            DeletionScope.ALL_MEMORY: set(),
        }
        actual = {name for name, is_present in present.items() if is_present}
        if actual != allowed[scope]:
            raise _field_error("target_selector", f"does not match {scope.value} scope")


@dataclass(frozen=True)
class DeletionRequest(_WireContract):
    schema_version: int
    deletion_request_id: str
    revision: int
    actor_subject_id: str
    scope: DeletionScope
    target_selector: DeletionTargetSelector
    source_handling: SourceHandling
    state: DeletionState
    progress_cursor: str | None
    attempt: int
    last_reason_code: str | None
    created_at_utc: str
    updated_at_utc: str
    completed_at_utc: str | None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.deletion_request_id, "deletion_request_id")
        _positive_int(self.revision, "revision")
        _id(self.actor_subject_id, "actor_subject_id")
        object.__setattr__(self, "scope", _enum(self.scope, DeletionScope, "scope"))
        object.__setattr__(
            self,
            "target_selector",
            _contract(self.target_selector, DeletionTargetSelector, "target_selector"),
        )
        self.target_selector.validate_scope(self.scope)
        object.__setattr__(
            self,
            "source_handling",
            _enum(self.source_handling, SourceHandling, "source_handling"),
        )
        object.__setattr__(self, "state", _enum(self.state, DeletionState, "state"))
        _string(
            self.progress_cursor,
            "progress_cursor",
            max_chars=MAX_QUERY_CHARS,
            optional=True,
        )
        _non_negative_int(self.attempt, "attempt")
        _code(self.last_reason_code, "last_reason_code", optional=True)
        created = _timestamp(self.created_at_utc, "created_at_utc")
        updated = _timestamp(self.updated_at_utc, "updated_at_utc")
        completed = _timestamp(self.completed_at_utc, "completed_at_utc", optional=True)
        if _timestamp_value(updated) < _timestamp_value(created):
            raise _field_error("updated_at_utc", "must not precede created_at_utc")
        if completed is not None and _timestamp_value(completed) < _timestamp_value(updated):
            raise _field_error("completed_at_utc", "must not precede updated_at_utc")
        if self.state is DeletionState.VERIFIED and completed is None:
            raise _field_error("completed_at_utc", "verified deletion requires completion time")
        if self.state is not DeletionState.VERIFIED and completed is not None:
            raise _field_error("completed_at_utc", "only verified deletion is complete")


@dataclass(frozen=True)
class ProjectTerminal(_WireContract):
    schema_version: int
    command_id: str
    source_store_id: str
    terminal_row_id: int
    session_id: str
    request_id: str
    terminal_event_id: str
    outcome: TerminalOutcome
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "command_id",
            "source_store_id",
            "session_id",
            "request_id",
            "terminal_event_id",
            "idempotency_key",
        ):
            _id(getattr(self, field_name), field_name)
        _positive_int(self.terminal_row_id, "terminal_row_id")
        object.__setattr__(self, "outcome", _enum(self.outcome, TerminalOutcome, "outcome"))
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class CreateJournalEntry(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    source_episode_ids: tuple[str, ...]
    entry_kind: JournalEntryKind
    title: str
    body: str
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.command_id, "command_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose is not AccessPurpose.MANAGE:
            raise _field_error("access_context", "journal command requires manage purpose")
        object.__setattr__(
            self,
            "source_episode_ids",
            _id_tuple(self.source_episode_ids, "source_episode_ids", non_empty=True, maximum=64),
        )
        object.__setattr__(
            self, "entry_kind", _enum(self.entry_kind, JournalEntryKind, "entry_kind")
        )
        _text(self.title, "title", max_chars=MAX_TITLE_CHARS)
        _text(self.body, "body", max_chars=MAX_BODY_CHARS)
        _id(self.idempotency_key, "idempotency_key")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class ProposeSharedMemory(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    source_episode_ids: tuple[str, ...]
    proposed_text: str
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.command_id, "command_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose is not AccessPurpose.MANAGE:
            raise _field_error("access_context", "shared proposal requires manage purpose")
        object.__setattr__(
            self,
            "source_episode_ids",
            _id_tuple(self.source_episode_ids, "source_episode_ids", non_empty=True, maximum=64),
        )
        _text(self.proposed_text, "proposed_text", max_chars=MAX_PROPOSED_TEXT_CHARS)
        _id(self.idempotency_key, "idempotency_key")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class ConfirmSharedMemory(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    shared_memory_id: str
    proposal_revision: int
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.command_id, "command_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose is not AccessPurpose.MANAGE:
            raise _field_error("access_context", "shared confirmation requires manage purpose")
        _id(self.shared_memory_id, "shared_memory_id")
        _positive_int(self.proposal_revision, "proposal_revision")
        _id(self.idempotency_key, "idempotency_key")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class RejectSharedMemory(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    shared_memory_id: str
    proposal_revision: int
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.command_id, "command_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose is not AccessPurpose.MANAGE:
            raise _field_error("access_context", "shared rejection requires manage purpose")
        _id(self.shared_memory_id, "shared_memory_id")
        _positive_int(self.proposal_revision, "proposal_revision")
        _id(self.idempotency_key, "idempotency_key")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class RevokeSharedMemory(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    shared_memory_id: str
    expected_revision: int
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.command_id, "command_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose is not AccessPurpose.MANAGE:
            raise _field_error("access_context", "shared revocation requires manage purpose")
        _id(self.shared_memory_id, "shared_memory_id")
        _positive_int(self.expected_revision, "expected_revision")
        _id(self.idempotency_key, "idempotency_key")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class CorrectMemory(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    target_kind: MemoryItemKind
    target_id: str
    expected_revision: int
    corrected_text: str
    source_evidence_ids: tuple[str, ...]
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.command_id, "command_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose is not AccessPurpose.MANAGE:
            raise _field_error("access_context", "correction requires manage purpose")
        object.__setattr__(
            self, "target_kind", _enum(self.target_kind, MemoryItemKind, "target_kind")
        )
        _id(self.target_id, "target_id")
        _positive_int(self.expected_revision, "expected_revision")
        _text(self.corrected_text, "corrected_text", max_chars=MAX_BODY_CHARS)
        object.__setattr__(
            self,
            "source_evidence_ids",
            _id_tuple(self.source_evidence_ids, "source_evidence_ids", non_empty=True, maximum=64),
        )
        _id(self.idempotency_key, "idempotency_key")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class ForgetMemory(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    deletion_request_id: str
    scope: DeletionScope
    target_selector: DeletionTargetSelector
    source_handling: SourceHandling
    reason_code: str
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.command_id, "command_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose is not AccessPurpose.DELETE:
            raise _field_error("access_context", "forget command requires delete purpose")
        _id(self.deletion_request_id, "deletion_request_id")
        object.__setattr__(self, "scope", _enum(self.scope, DeletionScope, "scope"))
        object.__setattr__(
            self,
            "target_selector",
            _contract(self.target_selector, DeletionTargetSelector, "target_selector"),
        )
        self.target_selector.validate_scope(self.scope)
        object.__setattr__(
            self,
            "source_handling",
            _enum(self.source_handling, SourceHandling, "source_handling"),
        )
        _code(self.reason_code, "reason_code")
        _id(self.idempotency_key, "idempotency_key")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class MigrateLegacyBatch(_WireContract):
    schema_version: int
    command_id: str
    access_context: AccessContext
    migration_id: str
    manifest_digest: str
    batch_index: int
    candidate_ids: tuple[str, ...]
    idempotency_key: str
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.command_id, "command_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose is not AccessPurpose.MIGRATION:
            raise _field_error("access_context", "migration command requires migration purpose")
        _id(self.migration_id, "migration_id")
        _hash(self.manifest_digest, "manifest_digest")
        _non_negative_int(self.batch_index, "batch_index")
        object.__setattr__(
            self,
            "candidate_ids",
            _id_tuple(self.candidate_ids, "candidate_ids", non_empty=True, maximum=256),
        )
        _id(self.idempotency_key, "idempotency_key")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class RecallQuery(_WireContract):
    schema_version: int
    query_id: str
    access_context: AccessContext
    query_text: str
    item_kinds: tuple[MemoryItemKind, ...]
    limit: int
    max_item_chars: int
    max_total_bytes: int
    occurred_after_utc: str | None
    occurred_before_utc: str | None
    issued_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.query_id, "query_id")
        object.__setattr__(
            self, "access_context", _contract(self.access_context, AccessContext, "access_context")
        )
        if self.access_context.purpose not in {AccessPurpose.CONVERSATION, AccessPurpose.RECALL}:
            raise _field_error("access_context", "recall requires conversation or recall purpose")
        _text(self.query_text, "query_text", max_chars=MAX_QUERY_CHARS)
        object.__setattr__(
            self,
            "item_kinds",
            _enum_tuple(self.item_kinds, MemoryItemKind, "item_kinds", maximum=5),
        )
        _bounded_int(self.limit, "limit", minimum=1, maximum=MAX_RECALL_ITEMS)
        _bounded_int(
            self.max_item_chars,
            "max_item_chars",
            minimum=1,
            maximum=MAX_RECALL_ITEM_CHARS,
        )
        _bounded_int(
            self.max_total_bytes,
            "max_total_bytes",
            minimum=1,
            maximum=MAX_RECALL_TOTAL_BYTES,
        )
        after = _timestamp(self.occurred_after_utc, "occurred_after_utc", optional=True)
        before = _timestamp(self.occurred_before_utc, "occurred_before_utc", optional=True)
        if after is not None and before is not None:
            if _timestamp_value(before) < _timestamp_value(after):
                raise _field_error("occurred_before_utc", "must not precede occurred_after_utc")
        _timestamp(self.issued_at_utc, "issued_at_utc")


@dataclass(frozen=True)
class RecallItem(_WireContract):
    item_id: str
    item_kind: MemoryItemKind
    prompt_text: str
    occurred_at_utc: str
    epistemic_label: EpistemicLabel
    source_citation_token: str
    owner_label: RecallOwnerLabel
    audience: Audience
    confidence: float

    def __post_init__(self) -> None:
        _id(self.item_id, "item_id")
        object.__setattr__(
            self, "item_kind", _enum(self.item_kind, MemoryItemKind, "item_kind")
        )
        _text(self.prompt_text, "prompt_text", max_chars=MAX_RECALL_ITEM_CHARS)
        _timestamp(self.occurred_at_utc, "occurred_at_utc")
        object.__setattr__(
            self,
            "epistemic_label",
            _enum(self.epistemic_label, EpistemicLabel, "epistemic_label"),
        )
        _id(self.source_citation_token, "source_citation_token")
        object.__setattr__(
            self, "owner_label", _enum(self.owner_label, RecallOwnerLabel, "owner_label")
        )
        object.__setattr__(self, "audience", _enum(self.audience, Audience, "audience"))
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))


@dataclass(frozen=True)
class RecallBundle(_WireContract):
    schema_version: int
    query_id: str
    context_id: str
    items: tuple[RecallItem, ...]
    acl_epoch: int
    index_generation: int
    generated_at_utc: str
    truncated: bool
    reason_code: str | None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.query_id, "query_id")
        _id(self.context_id, "context_id")
        object.__setattr__(
            self,
            "items",
            _contract_tuple(self.items, RecallItem, "items", maximum=MAX_RECALL_ITEMS),
        )
        item_ids = tuple(item.item_id for item in self.items)
        if len(set(item_ids)) != len(item_ids):
            raise _field_error("items", "item IDs must be unique")
        total_bytes = sum(len(item.prompt_text.encode("utf-8")) for item in self.items)
        if total_bytes > MAX_RECALL_TOTAL_BYTES:
            raise _field_error("items", f"prompt text must total at most {MAX_RECALL_TOTAL_BYTES} bytes")
        _non_negative_int(self.acl_epoch, "acl_epoch")
        _non_negative_int(self.index_generation, "index_generation")
        _timestamp(self.generated_at_utc, "generated_at_utc")
        _boolean(self.truncated, "truncated")
        _code(self.reason_code, "reason_code", optional=True)


# Compatibility-friendly descriptive aliases for the public enum surface.
MemoryAudience = Audience
MemoryStatus = MemoryItemStatus
DeletionSourceHandling = SourceHandling
OwnerKind = ActorKind


__all__ = [
    "AccessContext",
    "AccessPurpose",
    "ActorKind",
    "Audience",
    "BindingSource",
    "BindingStatus",
    "ClaimEpistemicClass",
    "ClaimSensitivity",
    "ClaimStatus",
    "ClaimValueType",
    "ConfirmSharedMemory",
    "CommunicationGuidance",
    "CorrectMemory",
    "CreateJournalEntry",
    "DeletionRequest",
    "DeletionScope",
    "DeletionSourceHandling",
    "DeletionState",
    "DeletionTargetSelector",
    "DerivationEdge",
    "DerivationRelation",
    "EpisodeOutcome",
    "EpistemicLabel",
    "ExperienceEpisode",
    "ForgetMemory",
    "GuidanceDirectness",
    "GuidanceFormality",
    "GuidanceVerbosity",
    "HandoffLease",
    "IdentityAssurance",
    "JournalEntry",
    "JournalEntryKind",
    "MemoryAudience",
    "MemoryItemKind",
    "MemoryItemStatus",
    "MemoryStatus",
    "MigrateLegacyBatch",
    "OwnerKind",
    "ParticipantRole",
    "ParticipantStatus",
    "PrivacyClass",
    "ProjectTerminal",
    "ProposeSharedMemory",
    "RecallBundle",
    "RecallItem",
    "RecallOwnerLabel",
    "RecallQuery",
    "RejectSharedMemory",
    "RelationshipDirection",
    "RelationshipEvent",
    "RelationshipEventKind",
    "RelationshipEventStatus",
    "RetentionClass",
    "RevokeSharedMemory",
    "RelationshipView",
    "SessionParticipant",
    "SessionGenerationState",
    "SharedConfirmationReceipt",
    "SharedMemory",
    "SharedMemoryStatus",
    "SourceHandling",
    "Subject",
    "SubjectBinding",
    "SubjectKind",
    "SubjectStatus",
    "TerminalOutcome",
    "UserModelClaim",
    "canonical_content_hash",
    "canonical_hash",
    "canonical_json_bytes",
]
