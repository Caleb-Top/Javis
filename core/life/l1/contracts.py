"""Strict immutable wire contracts for deterministic L1 presence state."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeVar

from core.life.contracts import (
    PrivacyClass,
    RetentionClass,
    canonical_content_hash,
)


_SCHEMA_VERSION = 1
_RFC3339_MILLISECONDS = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_FORBIDDEN_FIELDS = frozenset(
    {
        "text",
        "transcript",
        "audio",
        "audio_base64",
        "file_content",
        "tool_args",
        "tool_arguments",
        "tool_result",
        "tool_output",
        "api_key",
        "authorization",
        "token",
        "prompt",
        "response",
        "reasoning",
        "hidden_reasoning",
    }
)
_Contract = TypeVar("_Contract", bound="_WireContract")


class InputModality(str, Enum):
    TEXT = "text"
    VOICE = "voice"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class InputVerification(str, Enum):
    SERVER_VERIFIED = "server_verified"
    SERVER_TRANSCRIBED = "server_transcribed"
    CLIENT_CLAIMED = "client_claimed"
    UNKNOWN = "unknown"


class ObservationKind(str, Enum):
    USER_INVOKED = "user.invoked"
    VOICE_LISTENING_STARTED = "voice.listening_started"
    VOICE_LISTENING_STOPPED = "voice.listening_stopped"
    REQUEST_STARTED = "request.started"
    REQUEST_ACTIVITY = "request.activity"
    APPROVAL_REQUIRED = "approval.required"
    APPROVAL_RESOLVED = "approval.resolved"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    REQUEST_COMPLETED = "request.completed"
    REQUEST_FAILED = "request.failed"
    REQUEST_CANCELLED = "request.cancelled"
    SPEECH_STARTED = "speech.started"
    SPEECH_STOPPED = "speech.stopped"
    INTERACTION_INTERRUPTED = "interaction.interrupted"
    GOAL_VERIFIED = "goal.verified"
    RUNTIME_DEGRADED = "runtime.degraded"
    RUNTIME_RECOVERED = "runtime.recovered"


class ObservationOutcome(str, Enum):
    NONE = "none"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    APPROVED = "approved"
    DENIED = "denied"
    VERIFIED = "verified"
    INTERRUPTED = "interrupted"


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StateDimension(str, Enum):
    ACTIVATION = "activation"
    COGNITIVE_LOAD = "cognitive_load"
    CERTAINTY = "certainty"
    CAUTION = "caution"
    CURIOSITY = "curiosity"
    BLOCKEDNESS = "blockedness"
    SOCIAL_PRESENCE = "social_presence"


class AttentionMode(str, Enum):
    IDLE = "idle"
    PRESENT = "present"
    LISTENING = "listening"
    ENGAGED = "engaged"
    SPEAKING = "speaking"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    RECOVERING = "recovering"


class FunctionalAffectKind(str, Enum):
    CURIOUS = "curious"
    CAUTIOUS = "cautious"
    BLOCKED = "blocked"
    RELIEVED = "relieved"
    SATISFIED = "satisfied"


class ReasonCode(str, Enum):
    QUIET_BASELINE = "quiet_baseline"
    USER_INVOKED = "user_invoked"
    VOICE_LISTENING_STARTED = "voice_listening_started"
    VOICE_LISTENING_STOPPED = "voice_listening_stopped"
    REQUEST_STARTED = "request_started"
    REQUEST_ACTIVITY = "request_activity"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_DENIED = "approval_denied"
    TOOL_STARTED = "tool_started"
    TOOL_RISK_OBSERVED = "tool_risk_observed"
    TOOL_COMPLETED = "tool_completed"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"
    REQUEST_CANCELLED = "request_cancelled"
    SPEECH_STARTED = "speech_started"
    SPEECH_STOPPED = "speech_stopped"
    INTERACTION_INTERRUPTED = "interaction_interrupted"
    GOAL_VERIFIED = "goal_verified"
    RUNTIME_DEGRADED = "runtime_degraded"
    RUNTIME_RECOVERED = "runtime_recovered"
    LOAD_REDUCED = "load_reduced"
    BLOCKED_BY_FAILURE = "blocked_by_failure"


class PlaybackOutcome(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    STOPPED = "stopped"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TurnOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class ApprovalOutcome(str, Enum):
    NONE = "none"
    APPROVED = "approved"
    DENIED = "denied"
    UNKNOWN = "unknown"


class ResponsePath(str, Enum):
    DETERMINISTIC_LOCAL = "deterministic_local"
    MODEL = "model"
    TOOL = "tool"
    MIXED = "mixed"


class ReceiptCompleteness(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    INTERRUPTED_BY_RESTART = "interrupted_by_restart"


def _field_error(field_name: str, requirement: str) -> ValueError:
    return ValueError(f"{field_name}: {requirement}")


def _scan_forbidden(value: Any, path: str = "wire") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise _field_error(path, "object keys must be strings")
            if key.casefold() in _FORBIDDEN_FIELDS:
                raise _field_error(f"{path}.{key}", "forbidden field")
            _scan_forbidden(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_forbidden(item, f"{path}[{index}]")


def _schema(value: Any) -> None:
    if type(value) is not int or value != _SCHEMA_VERSION:
        raise _field_error("schema_version", "must equal 1")


def _string(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value or len(value) > 256:
        raise _field_error(field_name, "must be a bounded non-empty string")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise _field_error(field_name, "must not contain control characters")
    return value


def _code(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    text = _string(value, field_name, optional=optional)
    if text is None:
        return None
    if _CODE.fullmatch(text) is None:
        raise _field_error(field_name, "must be a bounded machine code")
    return text


def _timestamp(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _RFC3339_MILLISECONDS.fullmatch(value) is None:
        raise _field_error(field_name, "must be UTC with millisecond precision")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise _field_error(field_name, "must be a valid timestamp") from exc
    return value


def _timestamp_value(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


def _non_negative_int(value: Any, field_name: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or value < 0:
        raise _field_error(field_name, "must be a non-negative integer")
    return value


def _unit(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _field_error(field_name, "must be a finite unit value")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise _field_error(field_name, "must be a finite unit value")
    return result


def _signed_unit(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _field_error(field_name, "must be a finite signed unit value")
    result = float(value)
    if not math.isfinite(result) or not -1.0 <= result <= 1.0:
        raise _field_error(field_name, "must be a finite signed unit value")
    return result


def _boolean(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise _field_error(field_name, "must be a boolean")
    return value


def _enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise _field_error(field_name, f"must be {enum_type.__name__}")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _field_error(field_name, f"unknown {enum_type.__name__} value") from exc


def _contract(value: Any, contract_type: type[_Contract], field_name: str) -> _Contract:
    if isinstance(value, contract_type):
        return value
    if isinstance(value, Mapping):
        try:
            return contract_type.from_dict(value)
        except ValueError as exc:
            raise _field_error(field_name, str(exc)) from exc
    raise _field_error(field_name, f"must be {contract_type.__name__}")


def _contract_tuple(value: Any, contract_type: type[_Contract], field_name: str) -> tuple[_Contract, ...]:
    if not isinstance(value, (list, tuple)):
        raise _field_error(field_name, "must be a contract collection")
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
        return {
            (_wire(key) if isinstance(key, Enum) else key): _wire(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class _WireContract:
    @classmethod
    def from_dict(cls: type[_Contract], data: Mapping[str, Any]) -> _Contract:
        if not isinstance(data, Mapping):
            raise ValueError(f"{cls.__name__}: wire value must be an object")
        _scan_forbidden(data)
        expected = tuple(field.name for field in fields(cls))
        actual = set(data)
        missing = set(expected) - actual
        if missing:
            raise ValueError(
                f"{cls.__name__}: missing field(s): {', '.join(sorted(missing))}"
            )
        unexpected = actual - set(expected)
        if unexpected:
            raise ValueError(
                f"{cls.__name__}: unexpected field(s): {', '.join(sorted(unexpected))}"
            )
        return cls(**{name: data[name] for name in expected})

    def to_dict(self) -> dict[str, Any]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}


@dataclass(frozen=True)
class InputProvenance(_WireContract):
    modality: InputModality
    verification: InputVerification
    runtime_boot_id: str | None
    source_session_id: str | None
    owner_generation: int | None
    voice_sequence: int | None
    voice_turn: int | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "modality", _enum(self.modality, InputModality, "modality"))
        object.__setattr__(self, "verification", _enum(self.verification, InputVerification, "verification"))
        _string(self.runtime_boot_id, "runtime_boot_id", optional=True)
        _string(self.source_session_id, "source_session_id", optional=True)
        _non_negative_int(self.owner_generation, "owner_generation", optional=True)
        _non_negative_int(self.voice_sequence, "voice_sequence", optional=True)
        _non_negative_int(self.voice_turn, "voice_turn", optional=True)
        if self.verification is InputVerification.SERVER_VERIFIED:
            required = (
                self.runtime_boot_id,
                self.source_session_id,
                self.owner_generation,
                self.voice_sequence,
                self.voice_turn,
            )
            if self.modality is not InputModality.VOICE or any(item is None for item in required):
                raise _field_error("verification", "server_verified requires complete voice provenance")
        if (
            self.verification is InputVerification.SERVER_TRANSCRIBED
            and self.modality is not InputModality.VOICE
        ):
            raise _field_error("verification", "server_transcribed requires voice modality")

    @classmethod
    def unknown(cls) -> "InputProvenance":
        return cls(InputModality.UNKNOWN, InputVerification.UNKNOWN, None, None, None, None, None)


@dataclass(frozen=True)
class LifeObservation(_WireContract):
    schema_version: int
    observation_id: str
    kind: ObservationKind
    occurred_at_utc: str
    monotonic_offset_ms: int
    source: str
    source_event_id: str
    source_boot_id: str
    source_generation: int | None
    session_id: str | None
    request_id: str | None
    correlation_id: str | None
    causation_id: str | None
    sequence: int
    sequence_domain: str
    input_provenance: InputProvenance
    outcome: ObservationOutcome
    risk_level: RiskLevel
    confidence: float
    privacy_class: PrivacyClass
    retention_class: RetentionClass

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _string(self.observation_id, "observation_id")
        object.__setattr__(self, "kind", _enum(self.kind, ObservationKind, "kind"))
        _timestamp(self.occurred_at_utc, "occurred_at_utc")
        _non_negative_int(self.monotonic_offset_ms, "monotonic_offset_ms")
        _code(self.source, "source")
        for field_name in ("source_event_id", "source_boot_id"):
            _string(getattr(self, field_name), field_name)
        _non_negative_int(self.source_generation, "source_generation", optional=True)
        for field_name in ("session_id", "request_id", "correlation_id", "causation_id"):
            _string(getattr(self, field_name), field_name, optional=True)
        _non_negative_int(self.sequence, "sequence")
        _string(self.sequence_domain, "sequence_domain")
        object.__setattr__(self, "input_provenance", _contract(self.input_provenance, InputProvenance, "input_provenance"))
        object.__setattr__(self, "outcome", _enum(self.outcome, ObservationOutcome, "outcome"))
        object.__setattr__(self, "risk_level", _enum(self.risk_level, RiskLevel, "risk_level"))
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class"))
        object.__setattr__(self, "retention_class", _enum(self.retention_class, RetentionClass, "retention_class"))
        provenance = self.input_provenance
        if provenance.runtime_boot_id is not None and provenance.runtime_boot_id != self.source_boot_id:
            raise _field_error("input_provenance", "runtime boot must match observation source")
        if provenance.source_session_id is not None and provenance.source_session_id != self.session_id:
            raise _field_error("input_provenance", "session must match observation source")


@dataclass(frozen=True)
class AttentionClaim(_WireContract):
    claim_id: str
    target_kind: str
    target_id: str
    priority: int
    source_observation_id: str
    acquired_at_utc: str
    expires_at_utc: str
    interruptible: bool

    def __post_init__(self) -> None:
        for field_name in ("claim_id", "target_id", "source_observation_id"):
            _string(getattr(self, field_name), field_name)
        _code(self.target_kind, "target_kind")
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise _field_error("priority", "must be an integer in [0, 100]")
        _timestamp(self.acquired_at_utc, "acquired_at_utc")
        _timestamp(self.expires_at_utc, "expires_at_utc")
        if _timestamp_value(self.expires_at_utc) <= _timestamp_value(self.acquired_at_utc):
            raise _field_error("expires_at_utc", "must follow acquired_at_utc")
        _boolean(self.interruptible, "interruptible")


@dataclass(frozen=True)
class AttentionSnapshot(_WireContract):
    mode: AttentionMode
    target_kind: str | None
    target_id: str | None
    priority: int
    since_utc: str
    expires_at_utc: str | None
    source_observation_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum(self.mode, AttentionMode, "mode"))
        _code(self.target_kind, "target_kind", optional=True)
        _string(self.target_id, "target_id", optional=True)
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise _field_error("priority", "must be an integer in [0, 100]")
        _timestamp(self.since_utc, "since_utc")
        _timestamp(self.expires_at_utc, "expires_at_utc", optional=True)
        _string(self.source_observation_id, "source_observation_id", optional=True)
        target_fields = (self.target_kind, self.target_id, self.source_observation_id)
        if any(item is None for item in target_fields) != all(item is None for item in target_fields):
            raise _field_error("target_id", "attention target fields must be all present or all absent")
        if self.expires_at_utc is not None and _timestamp_value(self.expires_at_utc) <= _timestamp_value(self.since_utc):
            raise _field_error("expires_at_utc", "must follow since_utc")


@dataclass(frozen=True)
class AffectEvidence(_WireContract):
    evidence_id: str
    kind: FunctionalAffectKind
    source_observation_id: str
    reason_code: ReasonCode
    intensity: float
    confidence: float
    occurred_at_utc: str
    valid_until_utc: str

    def __post_init__(self) -> None:
        _string(self.evidence_id, "evidence_id")
        object.__setattr__(self, "kind", _enum(self.kind, FunctionalAffectKind, "kind"))
        _string(self.source_observation_id, "source_observation_id")
        object.__setattr__(self, "reason_code", _enum(self.reason_code, ReasonCode, "reason_code"))
        object.__setattr__(self, "intensity", _unit(self.intensity, "intensity"))
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        _timestamp(self.occurred_at_utc, "occurred_at_utc")
        _timestamp(self.valid_until_utc, "valid_until_utc")
        if self.kind is FunctionalAffectKind.SATISFIED and self.reason_code is not ReasonCode.GOAL_VERIFIED:
            raise _field_error("reason_code", "satisfied requires goal_verified evidence")
        if _timestamp_value(self.valid_until_utc) <= _timestamp_value(self.occurred_at_utc):
            raise _field_error("valid_until_utc", "must follow occurred_at_utc")


@dataclass(frozen=True)
class AppraisalResult(_WireContract):
    schema_version: int
    observation_id: str
    deltas: Mapping[StateDimension, float]
    attention_claim: AttentionClaim | None
    affect_evidence: tuple[AffectEvidence, ...]
    reason_code: ReasonCode
    confidence: float
    expires_at_utc: str | None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _string(self.observation_id, "observation_id")
        if not isinstance(self.deltas, Mapping):
            raise _field_error("deltas", "must be a mapping")
        normalized: dict[StateDimension, float] = {}
        for key, value in self.deltas.items():
            dimension = _enum(key, StateDimension, "deltas")
            normalized[dimension] = _signed_unit(value, f"deltas.{dimension.value}")
        object.__setattr__(self, "deltas", MappingProxyType(normalized))
        if self.attention_claim is not None:
            object.__setattr__(self, "attention_claim", _contract(self.attention_claim, AttentionClaim, "attention_claim"))
        object.__setattr__(self, "affect_evidence", _contract_tuple(self.affect_evidence, AffectEvidence, "affect_evidence"))
        object.__setattr__(self, "reason_code", _enum(self.reason_code, ReasonCode, "reason_code"))
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        _timestamp(self.expires_at_utc, "expires_at_utc", optional=True)


@dataclass(frozen=True)
class HomeostasisSnapshot(_WireContract):
    updated_at_utc: str
    activation: float
    cognitive_load: float
    certainty: float
    caution: float
    curiosity: float
    blockedness: float
    social_presence: float

    def __post_init__(self) -> None:
        _timestamp(self.updated_at_utc, "updated_at_utc")
        for field_name in tuple(field.name for field in fields(self))[1:]:
            object.__setattr__(self, field_name, _unit(getattr(self, field_name), field_name))


@dataclass(frozen=True)
class FunctionalAffect(_WireContract):
    kind: FunctionalAffectKind
    intensity: float
    confidence: float
    reason_code: ReasonCode
    evidence_ids: tuple[str, ...]
    valid_until_utc: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _enum(self.kind, FunctionalAffectKind, "kind"))
        object.__setattr__(self, "intensity", _unit(self.intensity, "intensity"))
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(self, "reason_code", _enum(self.reason_code, ReasonCode, "reason_code"))
        if not isinstance(self.evidence_ids, (list, tuple)) or not self.evidence_ids:
            raise _field_error("evidence_ids", "must be a non-empty collection")
        evidence_ids = tuple(_string(value, "evidence_ids") for value in self.evidence_ids)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise _field_error("evidence_ids", "must be unique")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        _timestamp(self.valid_until_utc, "valid_until_utc")
        if self.kind is FunctionalAffectKind.SATISFIED and self.reason_code is not ReasonCode.GOAL_VERIFIED:
            raise _field_error("reason_code", "satisfied requires goal_verified evidence")


@dataclass(frozen=True)
class PresenceSnapshot(_WireContract):
    mode: AttentionMode
    intensity: float
    session_id: str | None
    source_observation_id: str | None
    reason_code: ReasonCode
    since_utc: str
    expires_at_utc: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _enum(self.mode, AttentionMode, "mode"))
        object.__setattr__(self, "intensity", _unit(self.intensity, "intensity"))
        _string(self.session_id, "session_id", optional=True)
        _string(self.source_observation_id, "source_observation_id", optional=True)
        object.__setattr__(self, "reason_code", _enum(self.reason_code, ReasonCode, "reason_code"))
        _timestamp(self.since_utc, "since_utc")
        _timestamp(self.expires_at_utc, "expires_at_utc", optional=True)
        if self.expires_at_utc is not None and _timestamp_value(self.expires_at_utc) <= _timestamp_value(self.since_utc):
            raise _field_error("expires_at_utc", "must follow since_utc")


@dataclass(frozen=True)
class InnerStateSnapshot(_WireContract):
    schema_version: int
    source_life_snapshot_revision: int
    identity_id: str
    instance_id: str
    generated_at_utc: str
    phase: str
    attention: AttentionSnapshot
    homeostasis: HomeostasisSnapshot
    affects: tuple[FunctionalAffect, ...]
    presence: PresenceSnapshot
    last_observation_id: str | None
    degraded: bool

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _non_negative_int(self.source_life_snapshot_revision, "source_life_snapshot_revision")
        _string(self.identity_id, "identity_id")
        _string(self.instance_id, "instance_id")
        _timestamp(self.generated_at_utc, "generated_at_utc")
        _code(self.phase, "phase")
        object.__setattr__(self, "attention", _contract(self.attention, AttentionSnapshot, "attention"))
        object.__setattr__(self, "homeostasis", _contract(self.homeostasis, HomeostasisSnapshot, "homeostasis"))
        object.__setattr__(self, "affects", _contract_tuple(self.affects, FunctionalAffect, "affects"))
        object.__setattr__(self, "presence", _contract(self.presence, PresenceSnapshot, "presence"))
        _string(self.last_observation_id, "last_observation_id", optional=True)
        _boolean(self.degraded, "degraded")


@dataclass(frozen=True)
class PlaybackLifecycleEvent(_WireContract):
    schema_version: int
    playback_id: str
    generation: int
    runtime_boot_id: str
    session_id: str
    request_id: str
    outcome: PlaybackOutcome
    occurred_at_utc: str
    reason_code: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("playback_id", "runtime_boot_id", "session_id", "request_id"):
            _string(getattr(self, field_name), field_name)
        _non_negative_int(self.generation, "generation")
        object.__setattr__(self, "outcome", _enum(self.outcome, PlaybackOutcome, "outcome"))
        _timestamp(self.occurred_at_utc, "occurred_at_utc")
        _code(self.reason_code, "reason_code")


@dataclass(frozen=True)
class TurnExperienceReceipt(_WireContract):
    schema_version: int
    receipt_id: str
    session_id: str
    request_id: str
    input_provenance: InputProvenance
    source_boot_id: str
    first_event_id: str
    first_event_sequence: int
    first_event_sequence_domain: str
    started_at_utc: str
    ended_at_utc: str
    outcome: TurnOutcome
    terminal_event_id: str | None
    terminal_event_sequence: int | None
    terminal_event_sequence_domain: str | None
    recovery_event_id: str | None
    recovered_at_utc: str | None
    activity_kinds: tuple[str, ...]
    tool_count: int
    approval_outcome: ApprovalOutcome
    interruption_count: int
    goal_verified: bool
    model_route: str | None
    response_path: ResponsePath
    completeness: ReceiptCompleteness
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    content_hash: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("receipt_id", "session_id", "request_id", "source_boot_id", "first_event_id"):
            _string(getattr(self, field_name), field_name)
        object.__setattr__(self, "input_provenance", _contract(self.input_provenance, InputProvenance, "input_provenance"))
        _non_negative_int(self.first_event_sequence, "first_event_sequence")
        _string(self.first_event_sequence_domain, "first_event_sequence_domain")
        _timestamp(self.started_at_utc, "started_at_utc")
        _timestamp(self.ended_at_utc, "ended_at_utc")
        if _timestamp_value(self.ended_at_utc) < _timestamp_value(self.started_at_utc):
            raise _field_error("ended_at_utc", "must not precede started_at_utc")
        object.__setattr__(self, "outcome", _enum(self.outcome, TurnOutcome, "outcome"))
        _string(self.terminal_event_id, "terminal_event_id", optional=True)
        _non_negative_int(self.terminal_event_sequence, "terminal_event_sequence", optional=True)
        _string(self.terminal_event_sequence_domain, "terminal_event_sequence_domain", optional=True)
        terminal = (self.terminal_event_id, self.terminal_event_sequence, self.terminal_event_sequence_domain)
        if any(item is None for item in terminal) != all(item is None for item in terminal):
            raise _field_error("terminal_event_id", "terminal event fields must be all present or all absent")
        _string(self.recovery_event_id, "recovery_event_id", optional=True)
        _timestamp(self.recovered_at_utc, "recovered_at_utc", optional=True)
        if (self.recovery_event_id is None) != (self.recovered_at_utc is None):
            raise _field_error("recovery_event_id", "recovery event and time must appear together")
        if not isinstance(self.activity_kinds, (list, tuple)) or len(self.activity_kinds) > 32:
            raise _field_error("activity_kinds", "must contain at most 32 codes")
        activities = tuple(_code(item, "activity_kinds") for item in self.activity_kinds)
        object.__setattr__(self, "activity_kinds", activities)
        _non_negative_int(self.tool_count, "tool_count")
        object.__setattr__(self, "approval_outcome", _enum(self.approval_outcome, ApprovalOutcome, "approval_outcome"))
        _non_negative_int(self.interruption_count, "interruption_count")
        _boolean(self.goal_verified, "goal_verified")
        _code(self.model_route, "model_route", optional=True)
        object.__setattr__(self, "response_path", _enum(self.response_path, ResponsePath, "response_path"))
        object.__setattr__(self, "completeness", _enum(self.completeness, ReceiptCompleteness, "completeness"))
        object.__setattr__(self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class"))
        object.__setattr__(self, "retention_class", _enum(self.retention_class, RetentionClass, "retention_class"))
        if self.completeness is ReceiptCompleteness.INTERRUPTED_BY_RESTART:
            if self.outcome is not TurnOutcome.INTERRUPTED or any(item is not None for item in terminal):
                raise _field_error("terminal_event_id", "restart interruption cannot contain a terminal event")
        elif self.outcome in {TurnOutcome.COMPLETED, TurnOutcome.FAILED, TurnOutcome.CANCELLED} and all(item is None for item in terminal):
            raise _field_error("terminal_event_id", "terminal outcome requires a canonical terminal event")
        if type(self.content_hash) is not str or _SHA256_HEX.fullmatch(self.content_hash) is None:
            raise _field_error("content_hash", "must be lowercase SHA-256 hex")
        if not self.verify_hash():
            raise _field_error("content_hash", "does not match canonical receipt content")

    def verify_hash(self) -> bool:
        payload = self.to_dict()
        expected = payload.pop("content_hash")
        return canonical_content_hash(payload) == expected


__all__ = [
    "AffectEvidence",
    "AppraisalResult",
    "AttentionClaim",
    "AttentionMode",
    "AttentionSnapshot",
    "FunctionalAffect",
    "FunctionalAffectKind",
    "HomeostasisSnapshot",
    "InnerStateSnapshot",
    "InputModality",
    "InputProvenance",
    "InputVerification",
    "LifeObservation",
    "ObservationKind",
    "PlaybackLifecycleEvent",
    "PresenceSnapshot",
    "ReasonCode",
    "StateDimension",
    "TurnExperienceReceipt",
]
