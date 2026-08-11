"""Immutable in-memory contracts for the Javis life kernel.

This module deliberately contains no storage, runtime integration, hardware
inspection, or state projection.  It only defines the versioned wire boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeVar


_SCHEMA_VERSION = 1
_RFC3339_MILLISECONDS = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")

FrozenJsonObject = Mapping[str, Any]
_ContractType = TypeVar("_ContractType", bound="_WireContract")


class LifeCycleState(str, Enum):
    BOOTING = "booting"
    AWAKE = "awake"
    QUIET = "quiet"
    ENGAGED = "engaged"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    STOPPING = "stopping"
    OFFLINE = "offline"


class PrivacyClass(str, Enum):
    PUBLIC_SURFACE = "public_surface"
    LOCAL_INTERNAL = "local_internal"
    USER_PRIVATE = "user_private"
    SECRET = "secret"
    BIOMETRIC = "biometric"
    RESTRICTED_SYSTEM = "restricted_system"


class RetentionClass(str, Enum):
    EPHEMERAL = "ephemeral"
    SESSION = "session"
    OPERATIONAL = "operational"
    CONTINUITY = "continuity"
    MEMORY_CANDIDATE = "memory_candidate"
    AUDIT = "audit"
    NEVER_PERSIST = "never_persist"


class ExpressionBaseState(str, Enum):
    IDLE = "idle"
    ATTENTION = "attention"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    EXECUTING = "executing"
    BLOCKED = "blocked"
    ERROR = "error"
    OFFLINE = "offline"


class GazeTarget(str, Enum):
    NONE = "none"
    USER = "user"
    CONTENT = "content"
    TASK = "task"


class VoiceActivity(str, Enum):
    SILENT = "silent"
    LISTENING = "listening"
    SPEAKING = "speaking"


def _field_error(field_name: str, requirement: str) -> ValueError:
    return ValueError(f"{field_name}: {requirement}")


def _validate_schema_version(value: Any) -> None:
    if type(value) is not int or value != _SCHEMA_VERSION:
        raise _field_error(
            "schema_version",
            f"unknown schema_version {value!r}; expected {_SCHEMA_VERSION}",
        )


def _validate_string(value: Any, field_name: str, *, non_empty: bool = True) -> str:
    if type(value) is not str:
        raise _field_error(field_name, "must be a string")
    if non_empty and not value:
        raise _field_error(field_name, "must be non-empty")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _field_error(field_name, "must be valid UTF-8") from exc
    return value


def _validate_id(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _validate_string(value, field_name)
    if len(text) > 256:
        raise _field_error(field_name, "must contain at most 256 characters")
    if any(unicodedata.category(character) == "Cc" for character in text):
        raise _field_error(field_name, "must not contain control characters")
    return text


def _validate_hash(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _validate_string(value, field_name)
    if _SHA256_HEX.fullmatch(text) is None:
        raise _field_error(field_name, "must be lowercase SHA-256 hex")
    return text


def _validate_timestamp(
    value: Any, field_name: str, *, optional: bool = False
) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _RFC3339_MILLISECONDS.fullmatch(value) is None:
        raise _field_error(field_name, "must be RFC3339 UTC with millisecond precision")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise _field_error(field_name, "must be a valid RFC3339 UTC timestamp") from exc
    return value


def _epoch_to_timestamp(value: Any, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _field_error(field_name, "must be a finite epoch number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise _field_error(field_name, "must be a finite epoch number")
    try:
        return datetime.fromtimestamp(numeric, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError) as exc:
        raise _field_error(field_name, "is outside the supported epoch range") from exc


def _validate_non_negative_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise _field_error(field_name, "must be a non-negative integer")
    return value


def _validate_bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise _field_error(field_name, "must be a boolean")
    return value


def _validate_unit_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _field_error(field_name, "must be a finite float in [0.0, 1.0]")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise _field_error(field_name, "must be a finite float in [0.0, 1.0]")
    return numeric


def _validate_string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _field_error(field_name, "must be a string collection")
    normalized = []
    for index, item in enumerate(value):
        normalized.append(_validate_string(item, f"{field_name}[{index}]"))
    return tuple(normalized)


def _coerce_enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise _field_error(field_name, f"must be a {enum_type.__name__} wire string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _field_error(field_name, f"unknown {enum_type.__name__} value {value!r}") from exc


def _freeze_json(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _field_error(field_name, "JSON object keys must be strings")
            frozen[key] = _freeze_json(item, f"{field_name}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    if value is None or type(value) in (bool, int, str):
        if type(value) is str:
            _validate_string(value, field_name, non_empty=False)
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _field_error(field_name, "JSON numbers must be finite")
        return value
    raise _field_error(field_name, f"unsupported JSON value {type(value).__name__}")


def _freeze_json_object(value: Any, field_name: str) -> FrozenJsonObject:
    if not isinstance(value, Mapping):
        raise _field_error(field_name, "must be a JSON object")
    return _freeze_json(value, field_name)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def canonical_json_bytes(payload_without_content_hash: Mapping[str, Any]) -> bytes:
    """Return the frozen canonical UTF-8 JSON representation of an object."""

    frozen = _freeze_json_object(payload_without_content_hash, "payload")
    payload = _thaw_json(frozen)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_content_hash(payload_without_content_hash: Mapping[str, Any]) -> str:
    """Return lowercase SHA-256 hex over canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(payload_without_content_hash)).hexdigest()


def _wire_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, _WireContract):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {key: _wire_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire_value(item) for item in value]
    return value


class _WireContract:
    @classmethod
    def from_dict(cls: type[_ContractType], data: Mapping[str, Any]) -> _ContractType:
        if not isinstance(data, Mapping):
            raise ValueError(f"{cls.__name__}: wire value must be an object")
        expected = tuple(field.name for field in fields(cls))
        actual = set(data.keys())
        missing = set(expected) - actual
        if missing:
            names = ", ".join(sorted(str(name) for name in missing))
            raise ValueError(f"{cls.__name__}: missing field(s): {names}")
        unexpected = actual - set(expected)
        if unexpected:
            names = ", ".join(sorted(repr(name) for name in unexpected))
            raise ValueError(f"{cls.__name__}: unexpected field(s): {names}")
        return cls(**{name: data[name] for name in expected})

    def to_dict(self) -> dict[str, Any]:
        return {
            field.name: _wire_value(getattr(self, field.name))
            for field in fields(self)
        }


def _coerce_contract(value: Any, contract_type: type[_ContractType], field_name: str) -> _ContractType:
    if isinstance(value, contract_type):
        return value
    if isinstance(value, Mapping):
        try:
            return contract_type.from_dict(value)
        except ValueError as exc:
            raise _field_error(field_name, str(exc)) from exc
    raise _field_error(field_name, f"must be a {contract_type.__name__}")


@dataclass(frozen=True)
class IdentityConstitution(_WireContract):
    schema_version: int
    identity_id: str
    name: str
    kind: str
    relationship_role: str
    persona_invariants: tuple[str, ...]
    values: tuple[str, ...]
    hard_boundaries: tuple[str, ...]
    created_at: str
    version: int
    previous_version_hash: str | None
    content_hash: str
    approved_by: str
    approved_at: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_id(self.identity_id, "identity_id")
        _validate_string(self.name, "name")
        _validate_string(self.kind, "kind")
        _validate_string(self.relationship_role, "relationship_role")
        object.__setattr__(
            self,
            "persona_invariants",
            _validate_string_tuple(self.persona_invariants, "persona_invariants"),
        )
        object.__setattr__(self, "values", _validate_string_tuple(self.values, "values"))
        object.__setattr__(
            self,
            "hard_boundaries",
            _validate_string_tuple(self.hard_boundaries, "hard_boundaries"),
        )
        _validate_timestamp(self.created_at, "created_at")
        _validate_non_negative_int(self.version, "version")
        _validate_hash(
            self.previous_version_hash,
            "previous_version_hash",
            optional=True,
        )
        _validate_hash(self.content_hash, "content_hash")
        _validate_string(self.approved_by, "approved_by")
        _validate_timestamp(self.approved_at, "approved_at")
        if not self.verify_hash():
            raise _field_error("content_hash", "does not match canonical constitution content")

    @classmethod
    def create_default(cls, *, identity_id: str, now: float) -> "IdentityConstitution":
        timestamp = _epoch_to_timestamp(now, "now")
        payload: dict[str, Any] = {
            "schema_version": 1,
            "identity_id": identity_id,
            "name": "Javis",
            "kind": "local_digital_life_partner",
            "relationship_role": "partner",
            "persona_invariants": ["quiet", "focused", "measured", "truthful"],
            "values": [
                "user_data_ownership",
                "honesty",
                "least_privilege",
                "reversibility",
                "respect",
                "continuity",
            ],
            "hard_boundaries": [
                "no_implicit_identity_changes",
                "no_fabricated_shared_history",
                "no_secret_storage",
            ],
            "created_at": timestamp,
            "version": 1,
            "previous_version_hash": None,
            "approved_by": "built_in_constitution",
            "approved_at": timestamp,
        }
        payload["content_hash"] = canonical_content_hash(payload)
        return cls.from_dict(payload)

    def verify_hash(self) -> bool:
        payload = self.to_dict()
        content_hash = payload.pop("content_hash")
        return canonical_content_hash(payload) == content_hash


@dataclass(frozen=True)
class InstanceRecord(_WireContract):
    schema_version: int
    identity_id: str
    lineage_id: str
    instance_id: str
    parent_instance_id: str | None
    generation: int
    environment_fingerprint_hash: str
    created_at: str
    last_started_at: str | None
    last_clean_shutdown_at: str | None
    fork_pending_review: bool

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_id(self.identity_id, "identity_id")
        _validate_id(self.lineage_id, "lineage_id")
        _validate_id(self.instance_id, "instance_id")
        _validate_id(self.parent_instance_id, "parent_instance_id", optional=True)
        _validate_non_negative_int(self.generation, "generation")
        _validate_hash(self.environment_fingerprint_hash, "environment_fingerprint_hash")
        _validate_timestamp(self.created_at, "created_at")
        _validate_timestamp(self.last_started_at, "last_started_at", optional=True)
        _validate_timestamp(
            self.last_clean_shutdown_at,
            "last_clean_shutdown_at",
            optional=True,
        )
        _validate_bool(self.fork_pending_review, "fork_pending_review")


@dataclass(frozen=True)
class ContinuityCheckpoint(_WireContract):
    schema_version: int
    instance_id: str
    boot_id: str
    started_at: str
    clean_shutdown_at: str | None
    last_event_cursor: int
    active_request_id: str | None
    temporary_authority_valid: bool
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_id(self.instance_id, "instance_id")
        _validate_id(self.boot_id, "boot_id")
        _validate_timestamp(self.started_at, "started_at")
        _validate_timestamp(self.clean_shutdown_at, "clean_shutdown_at", optional=True)
        _validate_non_negative_int(self.last_event_cursor, "last_event_cursor")
        _validate_id(self.active_request_id, "active_request_id", optional=True)
        _validate_bool(self.temporary_authority_valid, "temporary_authority_valid")
        _validate_hash(self.content_hash, "content_hash")
        if not self.verify_hash():
            raise _field_error("content_hash", "does not match canonical checkpoint content")

    def verify_hash(self) -> bool:
        payload = self.to_dict()
        content_hash = payload.pop("content_hash")
        return canonical_content_hash(payload) == content_hash


@dataclass(frozen=True)
class StartupAssessment(_WireContract):
    schema_version: int
    instance_id: str
    previous_boot_id: str | None
    unclean_shutdown: bool
    temporary_authority_valid: bool
    last_event_cursor: int
    active_request_id: str | None
    reason_code: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_id(self.instance_id, "instance_id")
        _validate_id(self.previous_boot_id, "previous_boot_id", optional=True)
        _validate_bool(self.unclean_shutdown, "unclean_shutdown")
        _validate_bool(self.temporary_authority_valid, "temporary_authority_valid")
        _validate_non_negative_int(self.last_event_cursor, "last_event_cursor")
        _validate_id(self.active_request_id, "active_request_id", optional=True)
        _validate_string(self.reason_code, "reason_code")


@dataclass(frozen=True)
class LifeEvent(_WireContract):
    schema_version: int
    event_id: str
    event_type: str
    timestamp_utc: str
    monotonic_offset_ms: int
    source: str
    source_event_id: str | None
    session_id: str | None
    request_id: str | None
    correlation_id: str | None
    causation_id: str | None
    sequence: int
    identity_id: str
    instance_id: str
    payload: FrozenJsonObject
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    confidence: float
    provenance: FrozenJsonObject
    redaction_summary: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_id(self.event_id, "event_id")
        _validate_string(self.event_type, "event_type")
        _validate_timestamp(self.timestamp_utc, "timestamp_utc")
        _validate_non_negative_int(self.monotonic_offset_ms, "monotonic_offset_ms")
        _validate_string(self.source, "source")
        _validate_id(self.source_event_id, "source_event_id", optional=True)
        _validate_id(self.session_id, "session_id", optional=True)
        _validate_id(self.request_id, "request_id", optional=True)
        _validate_id(self.correlation_id, "correlation_id", optional=True)
        _validate_id(self.causation_id, "causation_id", optional=True)
        _validate_non_negative_int(self.sequence, "sequence")
        _validate_id(self.identity_id, "identity_id")
        _validate_id(self.instance_id, "instance_id")
        object.__setattr__(self, "payload", _freeze_json_object(self.payload, "payload"))
        object.__setattr__(
            self,
            "privacy_class",
            _coerce_enum(self.privacy_class, PrivacyClass, "privacy_class"),
        )
        object.__setattr__(
            self,
            "retention_class",
            _coerce_enum(self.retention_class, RetentionClass, "retention_class"),
        )
        object.__setattr__(
            self,
            "confidence",
            _validate_unit_float(self.confidence, "confidence"),
        )
        object.__setattr__(
            self,
            "provenance",
            _freeze_json_object(self.provenance, "provenance"),
        )
        object.__setattr__(
            self,
            "redaction_summary",
            _validate_string_tuple(self.redaction_summary, "redaction_summary"),
        )
        if (
            self.privacy_class is PrivacyClass.SECRET
            and self.retention_class is not RetentionClass.NEVER_PERSIST
        ):
            raise ValueError("secret events require retention_class never_persist")


@dataclass(frozen=True)
class IdentitySummary(_WireContract):
    identity_id: str
    name: str
    kind: str
    relationship_role: str
    version: int
    content_hash: str

    def __post_init__(self) -> None:
        _validate_id(self.identity_id, "identity_id")
        _validate_string(self.name, "name")
        _validate_string(self.kind, "kind")
        _validate_string(self.relationship_role, "relationship_role")
        _validate_non_negative_int(self.version, "version")
        _validate_hash(self.content_hash, "content_hash")


@dataclass(frozen=True)
class InstanceSummary(_WireContract):
    lineage_id: str
    instance_id: str
    parent_instance_id: str | None
    generation: int
    fork_pending_review: bool

    def __post_init__(self) -> None:
        _validate_id(self.lineage_id, "lineage_id")
        _validate_id(self.instance_id, "instance_id")
        _validate_id(self.parent_instance_id, "parent_instance_id", optional=True)
        _validate_non_negative_int(self.generation, "generation")
        _validate_bool(self.fork_pending_review, "fork_pending_review")


@dataclass(frozen=True)
class HealthSummary(_WireContract):
    status: str
    degraded_components: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_string(self.status, "status")
        object.__setattr__(
            self,
            "degraded_components",
            _validate_string_tuple(self.degraded_components, "degraded_components"),
        )
        object.__setattr__(
            self,
            "reason_codes",
            _validate_string_tuple(self.reason_codes, "reason_codes"),
        )


@dataclass(frozen=True)
class LifeSnapshot(_WireContract):
    schema_version: int
    revision: int
    identity: IdentitySummary
    instance: InstanceSummary
    lifecycle_state: LifeCycleState
    active_session_id: str | None
    active_request_id: str | None
    activity: str
    health: HealthSummary
    degradation_level: int
    recovery_required: bool
    last_event_id: str | None
    last_sequence: int
    updated_at: str
    explanation: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_negative_int(self.revision, "revision")
        object.__setattr__(
            self,
            "identity",
            _coerce_contract(self.identity, IdentitySummary, "identity"),
        )
        object.__setattr__(
            self,
            "instance",
            _coerce_contract(self.instance, InstanceSummary, "instance"),
        )
        object.__setattr__(
            self,
            "lifecycle_state",
            _coerce_enum(self.lifecycle_state, LifeCycleState, "lifecycle_state"),
        )
        _validate_id(self.active_session_id, "active_session_id", optional=True)
        _validate_id(self.active_request_id, "active_request_id", optional=True)
        _validate_string(self.activity, "activity")
        object.__setattr__(
            self,
            "health",
            _coerce_contract(self.health, HealthSummary, "health"),
        )
        _validate_non_negative_int(self.degradation_level, "degradation_level")
        _validate_bool(self.recovery_required, "recovery_required")
        _validate_id(self.last_event_id, "last_event_id", optional=True)
        _validate_non_negative_int(self.last_sequence, "last_sequence")
        _validate_timestamp(self.updated_at, "updated_at")
        _validate_string(self.explanation, "explanation")


@dataclass(frozen=True)
class ExpressionIntent(_WireContract):
    schema_version: int
    revision: int
    base_state: ExpressionBaseState
    intensity: float
    gaze_target: GazeTarget
    voice_activity: VoiceActivity
    transition_ms: int
    interrupt: bool
    source_snapshot_revision: int
    generated_at: str
    expires_at: str
    explanation_code: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        _validate_non_negative_int(self.revision, "revision")
        object.__setattr__(
            self,
            "base_state",
            _coerce_enum(self.base_state, ExpressionBaseState, "base_state"),
        )
        object.__setattr__(
            self,
            "intensity",
            _validate_unit_float(self.intensity, "intensity"),
        )
        object.__setattr__(
            self,
            "gaze_target",
            _coerce_enum(self.gaze_target, GazeTarget, "gaze_target"),
        )
        object.__setattr__(
            self,
            "voice_activity",
            _coerce_enum(self.voice_activity, VoiceActivity, "voice_activity"),
        )
        _validate_non_negative_int(self.transition_ms, "transition_ms")
        _validate_bool(self.interrupt, "interrupt")
        _validate_non_negative_int(
            self.source_snapshot_revision,
            "source_snapshot_revision",
        )
        _validate_timestamp(self.generated_at, "generated_at")
        _validate_timestamp(self.expires_at, "expires_at")
        _validate_string(self.explanation_code, "explanation_code")


__all__ = [
    "LifeCycleState",
    "PrivacyClass",
    "RetentionClass",
    "ExpressionBaseState",
    "GazeTarget",
    "VoiceActivity",
    "IdentityConstitution",
    "InstanceRecord",
    "ContinuityCheckpoint",
    "StartupAssessment",
    "LifeEvent",
    "IdentitySummary",
    "InstanceSummary",
    "HealthSummary",
    "LifeSnapshot",
    "ExpressionIntent",
    "canonical_json_bytes",
    "canonical_content_hash",
]
