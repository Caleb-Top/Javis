"""Strict immutable wire contracts for L7 sleep, growth, and body planning.

L7 deliberately reuses the L0 canonical JSON, ID, UTC, hash, privacy, and
retention vocabulary.  This module adds only the bounded schemas and state
vocabularies owned by the L7 boundary.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.life.contracts import (
    PrivacyClass,
    RetentionClass,
    _WireContract as _L0WireContract,
    _coerce_enum,
    _field_error,
    _freeze_json,
    _freeze_json_object,
    _thaw_json,
    _validate_bool,
    _validate_hash,
    _validate_id,
    _validate_schema_version,
    _validate_string,
    _validate_timestamp,
    canonical_content_hash as _l0_canonical_content_hash,
    canonical_json_bytes,
)


SCHEMA_VERSION = 1
MAX_TEXT_CHARS = 4_096
MAX_COLLECTION_ITEMS = 128
MAX_NESTED_BYTES = 65_536
MAX_NESTING_DEPTH = 12
MAX_EXPRESSION_TRACKS = 24
MAX_KEYFRAMES_PER_TRACK = 64
MAX_EXPRESSION_DURATION_MS = 120_000

_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_LOCAL_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S{8,}|(?:api[_-]?key|password|secret|token|credential)"
    r"\s*[:=]\s*\S+)"
)
_FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "audio_bytes",
        "audio_data",
        "authorization",
        "bearer_token",
        "camera_frame",
        "chain_of_thought",
        "credential",
        "file_content",
        "full_screen",
        "hidden_reasoning",
        "image_bytes",
        "password",
        "raw_audio",
        "raw_screen",
        "raw_screenshot",
        "screen_capture",
        "screen_pixels",
        "secret",
        "secrets",
        "source_body",
        "source_code",
        "source_text",
        "token",
        "video_bytes",
    }
)

_ContractT = TypeVar("_ContractT", bound="_StrictContract")


class ScheduleMode(str, Enum):
    MANUAL = "manual"
    QUIET_HOURS = "quiet_hours"
    IDLE = "idle"


class NetworkPolicy(str, Enum):
    DENY = "deny"
    EXPLICIT_JOB_GRANTS = "explicit_job_grants"


class SleepJobKind(str, Enum):
    INDEX_VERIFY = "index_verify"
    CONTRADICTION_SCAN = "contradiction_scan"
    RETENTION_APPLY = "retention_apply"
    SUMMARY_BUILD = "summary_build"
    GROWTH_PROPOSE = "growth_propose"
    ARTIFACT_REVALIDATE = "artifact_revalidate"


class SleepTriggerKind(str, Enum):
    MANUAL = "manual"
    QUIET_HOURS = "quiet_hours"
    IDLE = "idle"
    RECOVERY = "recovery"


class SleepRunState(str, Enum):
    SCHEDULED = "scheduled"
    PREFLIGHT = "preflight"
    SKIPPED = "skipped"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    CHECKPOINTING = "checkpointing"
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


class SleepJobStatus(str, Enum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class CandidateKind(str, Enum):
    FACT = "fact"
    RELATIONSHIP = "relationship"
    PROCEDURE = "procedure"
    EXPRESSION_PROFILE = "expression_profile"
    SKILL = "skill"
    SYSTEM_CHANGE = "system_change"


class CandidateState(str, Enum):
    PROPOSED = "proposed"
    EVIDENCE_READY = "evidence_ready"
    SANDBOXING = "sandboxing"
    VALIDATION_FAILED = "validation_failed"
    VALIDATED = "validated"
    AWAITING_APPROVAL = "awaiting_approval"
    REJECTED = "rejected"
    EXPIRED = "expired"
    APPROVED = "approved"
    DEPLOYING = "deploying"
    DEPLOYMENT_FAILED = "deployment_failed"
    ACTIVE = "active"
    REVOKED = "revoked"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


class GrowthActorKind(str, Enum):
    USER = "user"
    MODEL_PROPOSER = "model_proposer"
    SYSTEM = "system"
    FORGE = "forge"


class GrowthDecisionKind(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REVOKE = "revoke"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ArtifactKind(str, Enum):
    SKILL = "skill"
    EXPRESSION_PROFILE = "expression_profile"
    SYSTEM_CHANGE = "system_change"


class CatalogStatus(str, Enum):
    ACTIVE = "active"


class RendererKind(str, Enum):
    PROCEDURAL = "procedural"
    VRM = "vrm"
    GLTF = "gltf"
    SPRITE_2D = "sprite_2d"
    ORB = "orb"


class TimingSource(str, Enum):
    NONE = "none"
    ENERGY = "energy"
    WORD = "word"
    PHONEME = "phoneme"


_SLEEP_TRANSITIONS: Mapping[SleepRunState, frozenset[SleepRunState]] = MappingProxyType(
    {
        SleepRunState.SCHEDULED: frozenset({SleepRunState.PREFLIGHT}),
        SleepRunState.PREFLIGHT: frozenset(
            {SleepRunState.SKIPPED, SleepRunState.RUNNING, SleepRunState.FAILED}
        ),
        SleepRunState.RUNNING: frozenset(
            {
                SleepRunState.CANCELLING,
                SleepRunState.CHECKPOINTING,
                SleepRunState.COMMITTING,
                SleepRunState.FAILED,
                SleepRunState.TIMED_OUT,
                SleepRunState.INTERRUPTED,
            }
        ),
        SleepRunState.CANCELLING: frozenset(
            {SleepRunState.CANCELLED, SleepRunState.FAILED, SleepRunState.INTERRUPTED}
        ),
        SleepRunState.CHECKPOINTING: frozenset(
            {SleepRunState.RUNNING, SleepRunState.FAILED, SleepRunState.INTERRUPTED}
        ),
        SleepRunState.COMMITTING: frozenset(
            {SleepRunState.COMPLETED, SleepRunState.FAILED, SleepRunState.INTERRUPTED}
        ),
        SleepRunState.INTERRUPTED: frozenset(
            {SleepRunState.PREFLIGHT, SleepRunState.CANCELLED, SleepRunState.FAILED}
        ),
    }
)

_CANDIDATE_TRANSITIONS: Mapping[CandidateState, frozenset[CandidateState]] = MappingProxyType(
    {
        CandidateState.PROPOSED: frozenset({CandidateState.EVIDENCE_READY}),
        CandidateState.EVIDENCE_READY: frozenset({CandidateState.SANDBOXING}),
        CandidateState.SANDBOXING: frozenset(
            {CandidateState.VALIDATION_FAILED, CandidateState.VALIDATED}
        ),
        CandidateState.VALIDATED: frozenset({CandidateState.AWAITING_APPROVAL}),
        CandidateState.AWAITING_APPROVAL: frozenset(
            {CandidateState.REJECTED, CandidateState.EXPIRED, CandidateState.APPROVED}
        ),
        CandidateState.APPROVED: frozenset({CandidateState.DEPLOYING}),
        CandidateState.DEPLOYING: frozenset(
            {CandidateState.DEPLOYMENT_FAILED, CandidateState.ACTIVE}
        ),
        CandidateState.ACTIVE: frozenset(
            {CandidateState.REVOKED, CandidateState.ROLLING_BACK}
        ),
        CandidateState.ROLLING_BACK: frozenset({CandidateState.ROLLED_BACK}),
    }
)


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _scan_sensitive(value: Any, field_name: str = "payload", *, depth: int = 0) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise _field_error(field_name, f"must be at most {MAX_NESTING_DEPTH} levels deep")
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise _field_error(field_name, f"must contain at most {MAX_COLLECTION_ITEMS} fields")
        for key, item in value.items():
            if type(key) is not str:
                raise _field_error(field_name, "JSON object keys must be strings")
            normalized = _normalized_key(key)
            if normalized in _FORBIDDEN_KEYS:
                raise _field_error(f"{field_name}.{key}", "sensitive or source-body field is forbidden")
            _scan_sensitive(item, f"{field_name}.{key}", depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise _field_error(field_name, f"must contain at most {MAX_COLLECTION_ITEMS} items")
        for index, item in enumerate(value):
            _scan_sensitive(item, f"{field_name}[{index}]", depth=depth + 1)
        return
    if type(value) is str:
        _text(value, field_name, allow_empty=True)
        if _SECRET_VALUE.search(value):
            raise _field_error(field_name, "must not contain credential material")
    elif type(value) is float and not math.isfinite(value):
        raise _field_error(field_name, "JSON numbers must be finite")


def _text(value: Any, field_name: str, *, allow_empty: bool = False, maximum: int = MAX_TEXT_CHARS) -> str:
    result = _validate_string(value, field_name, non_empty=not allow_empty)
    if len(result) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} characters")
    if any(unicodedata.category(character) == "Cc" for character in result):
        raise _field_error(field_name, "must not contain control characters")
    return result


def _code(value: Any, field_name: str) -> str:
    result = _text(value, field_name, maximum=128)
    if _CODE.fullmatch(result) is None:
        raise _field_error(field_name, "must be a lowercase machine code")
    return result


def _optional_id(value: Any, field_name: str) -> str | None:
    return _validate_id(value, field_name, optional=True)


def _optional_hash(value: Any, field_name: str) -> str | None:
    return _validate_hash(value, field_name, optional=True)


def _optional_timestamp(value: Any, field_name: str) -> str | None:
    return _validate_timestamp(value, field_name, optional=True)


def _positive_int(value: Any, field_name: str, *, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _field_error(field_name, f"must be an integer in [1, {maximum}]")
    return value


def _non_negative_int(value: Any, field_name: str, *, maximum: int = 2**63 - 1) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise _field_error(field_name, f"must be an integer in [0, {maximum}]")
    return value


def _bounded_number(value: Any, field_name: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _field_error(field_name, f"must be a finite number in [{minimum}, {maximum}]")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise _field_error(field_name, f"must be a finite number in [{minimum}, {maximum}]")
    return result


def _collection(value: Any, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, (list, tuple)):
        raise _field_error(field_name, "must be a JSON array")
    if len(value) > MAX_COLLECTION_ITEMS:
        raise _field_error(field_name, f"must contain at most {MAX_COLLECTION_ITEMS} items")
    return value


def _id_tuple(value: Any, field_name: str, *, non_empty: bool = False) -> tuple[str, ...]:
    values = _collection(value, field_name)
    if non_empty and not values:
        raise _field_error(field_name, "must be non-empty")
    result = tuple(_validate_id(item, f"{field_name}[{index}]") for index, item in enumerate(values))
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must contain unique IDs")
    return result  # type: ignore[return-value]


def _code_tuple(value: Any, field_name: str, *, non_empty: bool = False) -> tuple[str, ...]:
    values = _collection(value, field_name)
    if non_empty and not values:
        raise _field_error(field_name, "must be non-empty")
    result = tuple(_code(item, f"{field_name}[{index}]") for index, item in enumerate(values))
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must contain unique codes")
    return result


def _text_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    values = _collection(value, field_name)
    return tuple(_text(item, f"{field_name}[{index}]") for index, item in enumerate(values))


def _enum_tuple(value: Any, enum_type: type[Enum], field_name: str, *, non_empty: bool = False) -> tuple[Enum, ...]:
    values = _collection(value, field_name)
    if non_empty and not values:
        raise _field_error(field_name, "must be non-empty")
    result = tuple(_coerce_enum(item, enum_type, f"{field_name}[{index}]") for index, item in enumerate(values))
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must contain unique values")
    return result


def _frozen_map(value: Any, field_name: str) -> Mapping[str, Any]:
    _scan_sensitive(value, field_name)
    result = _freeze_json_object(value, field_name)
    if len(canonical_json_bytes(_thaw_json(result))) > MAX_NESTED_BYTES:
        raise _field_error(field_name, f"must encode to at most {MAX_NESTED_BYTES} bytes")
    return result


def _frozen_json_tuple(value: Any, field_name: str) -> tuple[Any, ...]:
    values = _collection(value, field_name)
    _scan_sensitive(values, field_name)
    result = tuple(_freeze_json(item, f"{field_name}[{index}]") for index, item in enumerate(values))
    wrapper = {"items": result}
    if len(canonical_json_bytes(_thaw_json(wrapper))) > MAX_NESTED_BYTES:
        raise _field_error(field_name, f"must encode to at most {MAX_NESTED_BYTES} bytes")
    return result


def canonical_content_hash(value: Mapping[str, Any] | _L0WireContract) -> str:
    """Hash an L7 object after excluding its top-level ``content_hash``."""

    payload = value.to_dict() if isinstance(value, _L0WireContract) else dict(value)
    payload.pop("content_hash", None)
    return _l0_canonical_content_hash(payload)


class _StrictContract(_L0WireContract):
    @classmethod
    def from_dict(cls: type[_ContractT], data: Mapping[str, Any]) -> _ContractT:
        _scan_sensitive(data)
        return super().from_dict(data)  # type: ignore[return-value]

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def canonical_content_hash(self) -> str:
        return canonical_content_hash(self)

    def _verify_content_hash(self) -> None:
        _validate_hash(getattr(self, "content_hash"), "content_hash")
        if self.canonical_content_hash() != getattr(self, "content_hash"):
            raise _field_error("content_hash", "does not match canonical contract content")


def _common_persistence(contract: _StrictContract) -> None:
    object.__setattr__(contract, "privacy_class", _coerce_enum(contract.privacy_class, PrivacyClass, "privacy_class"))
    object.__setattr__(contract, "retention_class", _coerce_enum(contract.retention_class, RetentionClass, "retention_class"))
    object.__setattr__(contract, "provenance", _frozen_map(contract.provenance, "provenance"))


@dataclass(frozen=True, slots=True)
class SleepPolicyV1(_StrictContract):
    schema_version: int
    policy_id: str
    identity_id: str
    owner_subject_id: str
    revision: int
    enabled: bool
    approval_id: str
    approved_at_utc: str
    schedule_mode: ScheduleMode
    timezone: str
    quiet_start_local: str | None
    quiet_end_local: str | None
    idle_min_seconds: int
    require_ac_power: bool
    minimum_battery_percent: int
    maximum_cpu_percent: int
    minimum_free_bytes: int
    network_policy: NetworkPolicy
    allowed_job_kinds: tuple[SleepJobKind, ...]
    max_run_seconds: int
    max_job_seconds: int
    max_input_records: int
    max_output_bytes: int
    wake_on_user_activity: bool
    effective_at_utc: str
    expires_at_utc: str
    created_at_utc: str
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    provenance: Mapping[str, Any]
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("policy_id", "identity_id", "owner_subject_id", "approval_id"):
            _validate_id(getattr(self, name), name)
        _positive_int(self.revision, "revision")
        _validate_bool(self.enabled, "enabled")
        _validate_timestamp(self.approved_at_utc, "approved_at_utc")
        object.__setattr__(self, "schedule_mode", _coerce_enum(self.schedule_mode, ScheduleMode, "schedule_mode"))
        timezone_name = _text(self.timezone, "timezone", maximum=128)
        try:
            ZoneInfo(timezone_name)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise _field_error("timezone", "must be an IANA timezone") from exc
        for name in ("quiet_start_local", "quiet_end_local"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or _LOCAL_TIME.fullmatch(value) is None):
                raise _field_error(name, "must be HH:MM local time or null")
        if self.schedule_mode is ScheduleMode.QUIET_HOURS and (
            self.quiet_start_local is None or self.quiet_end_local is None
        ):
            raise _field_error("quiet_start_local", "quiet_hours requires both local boundaries")
        _non_negative_int(self.idle_min_seconds, "idle_min_seconds", maximum=604_800)
        _validate_bool(self.require_ac_power, "require_ac_power")
        _non_negative_int(self.minimum_battery_percent, "minimum_battery_percent", maximum=100)
        _non_negative_int(self.maximum_cpu_percent, "maximum_cpu_percent", maximum=100)
        _non_negative_int(self.minimum_free_bytes, "minimum_free_bytes")
        object.__setattr__(self, "network_policy", _coerce_enum(self.network_policy, NetworkPolicy, "network_policy"))
        object.__setattr__(self, "allowed_job_kinds", _enum_tuple(self.allowed_job_kinds, SleepJobKind, "allowed_job_kinds", non_empty=True))
        for name, maximum in (
            ("max_run_seconds", 86_400),
            ("max_job_seconds", 21_600),
            ("max_input_records", 1_000_000),
            ("max_output_bytes", 1_073_741_824),
        ):
            _positive_int(getattr(self, name), name, maximum=maximum)
        if self.max_job_seconds > self.max_run_seconds:
            raise _field_error("max_job_seconds", "must not exceed max_run_seconds")
        _validate_bool(self.wake_on_user_activity, "wake_on_user_activity")
        for name in ("effective_at_utc", "expires_at_utc", "created_at_utc"):
            _validate_timestamp(getattr(self, name), name)
        if self.expires_at_utc <= self.effective_at_utc:
            raise _field_error("expires_at_utc", "must follow effective_at_utc")
        _common_persistence(self)
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class SleepRunV1(_StrictContract):
    schema_version: int
    run_id: str
    policy_id: str
    policy_revision: int
    identity_id: str
    instance_id: str
    owner_subject_id: str
    trigger_kind: SleepTriggerKind
    trigger_event_id: str
    idempotency_key: str
    state: SleepRunState
    revision: int
    scheduled_at_utc: str
    started_at_utc: str | None
    ended_at_utc: str | None
    preflight_snapshot_hash: str | None
    source_checkpoint_ids: tuple[str, ...]
    job_specs: tuple[Any, ...]
    current_job_id: str | None
    completed_job_ids: tuple[str, ...]
    receipt_ids: tuple[str, ...]
    candidate_version_ids: tuple[str, ...]
    cancel_reason_code: str | None
    failure_reason_code: str | None
    resource_usage: Mapping[str, Any]
    created_at_utc: str
    updated_at_utc: str
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    provenance: Mapping[str, Any]
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("run_id", "policy_id", "identity_id", "instance_id", "owner_subject_id", "trigger_event_id", "idempotency_key"):
            _validate_id(getattr(self, name), name)
        _positive_int(self.policy_revision, "policy_revision")
        object.__setattr__(self, "trigger_kind", _coerce_enum(self.trigger_kind, SleepTriggerKind, "trigger_kind"))
        object.__setattr__(self, "state", _coerce_enum(self.state, SleepRunState, "state"))
        _positive_int(self.revision, "revision")
        _validate_timestamp(self.scheduled_at_utc, "scheduled_at_utc")
        _optional_timestamp(self.started_at_utc, "started_at_utc")
        _optional_timestamp(self.ended_at_utc, "ended_at_utc")
        _optional_hash(self.preflight_snapshot_hash, "preflight_snapshot_hash")
        object.__setattr__(self, "source_checkpoint_ids", _id_tuple(self.source_checkpoint_ids, "source_checkpoint_ids"))
        object.__setattr__(self, "job_specs", _frozen_json_tuple(self.job_specs, "job_specs"))
        _optional_id(self.current_job_id, "current_job_id")
        for name in ("completed_job_ids", "receipt_ids", "candidate_version_ids"):
            object.__setattr__(self, name, _id_tuple(getattr(self, name), name))
        for name in ("cancel_reason_code", "failure_reason_code"):
            value = getattr(self, name)
            if value is not None:
                _code(value, name)
        object.__setattr__(self, "resource_usage", _frozen_map(self.resource_usage, "resource_usage"))
        _validate_timestamp(self.created_at_utc, "created_at_utc")
        _validate_timestamp(self.updated_at_utc, "updated_at_utc")
        if self.updated_at_utc < self.created_at_utc:
            raise _field_error("updated_at_utc", "must not precede created_at_utc")
        terminal = {SleepRunState.SKIPPED, SleepRunState.CANCELLED, SleepRunState.COMPLETED, SleepRunState.FAILED, SleepRunState.TIMED_OUT}
        if self.state in terminal and self.ended_at_utc is None:
            raise _field_error("ended_at_utc", "terminal runs require an end time")
        _common_persistence(self)
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class SleepRunTransitionV1(_StrictContract):
    schema_version: int
    transition_id: str
    run_id: str
    from_state: SleepRunState
    to_state: SleepRunState
    expected_revision: int
    reason_code: str
    checkpoint_id: str | None
    receipt_ids: tuple[str, ...]
    created_at_utc: str
    idempotency_key: str
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("transition_id", "run_id", "idempotency_key"):
            _validate_id(getattr(self, name), name)
        object.__setattr__(self, "from_state", _coerce_enum(self.from_state, SleepRunState, "from_state"))
        object.__setattr__(self, "to_state", _coerce_enum(self.to_state, SleepRunState, "to_state"))
        allowed = _SLEEP_TRANSITIONS.get(self.from_state, frozenset())
        if self.to_state not in allowed:
            raise _field_error("to_state", f"illegal transition from {self.from_state.value}")
        _positive_int(self.expected_revision, "expected_revision")
        _code(self.reason_code, "reason_code")
        _optional_id(self.checkpoint_id, "checkpoint_id")
        object.__setattr__(self, "receipt_ids", _id_tuple(self.receipt_ids, "receipt_ids"))
        _validate_timestamp(self.created_at_utc, "created_at_utc")
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class SleepJobReceiptV1(_StrictContract):
    schema_version: int
    receipt_id: str
    run_id: str
    job_id: str
    job_kind: SleepJobKind
    status: SleepJobStatus
    input_set_hash: str
    output_set_hash: str | None
    processed_count: int
    skipped_count: int
    reason_code: str | None
    error_code: str | None
    checkpoint_id: str | None
    source_ids: tuple[str, ...]
    started_at_utc: str
    ended_at_utc: str
    duration_ms: int
    output_bytes: int
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    provenance: Mapping[str, Any]
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("receipt_id", "run_id", "job_id"):
            _validate_id(getattr(self, name), name)
        object.__setattr__(self, "job_kind", _coerce_enum(self.job_kind, SleepJobKind, "job_kind"))
        object.__setattr__(self, "status", _coerce_enum(self.status, SleepJobStatus, "status"))
        _validate_hash(self.input_set_hash, "input_set_hash")
        _optional_hash(self.output_set_hash, "output_set_hash")
        _non_negative_int(self.processed_count, "processed_count")
        _non_negative_int(self.skipped_count, "skipped_count")
        for name in ("reason_code", "error_code"):
            value = getattr(self, name)
            if value is not None:
                _code(value, name)
        _optional_id(self.checkpoint_id, "checkpoint_id")
        object.__setattr__(self, "source_ids", _id_tuple(self.source_ids, "source_ids"))
        _validate_timestamp(self.started_at_utc, "started_at_utc")
        _validate_timestamp(self.ended_at_utc, "ended_at_utc")
        if self.ended_at_utc < self.started_at_utc:
            raise _field_error("ended_at_utc", "must not precede started_at_utc")
        _non_negative_int(self.duration_ms, "duration_ms")
        _non_negative_int(self.output_bytes, "output_bytes")
        _common_persistence(self)
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class GrowthCandidateV1(_StrictContract):
    schema_version: int
    candidate_id: str
    candidate_version_id: str
    version_number: int
    previous_version_id: str | None
    previous_content_hash: str | None
    identity_id: str
    instance_id: str
    owner_subject_id: str
    candidate_kind: CandidateKind
    title: str
    bounded_summary: str
    source_evidence_ids: tuple[str, ...]
    source_evidence_hash: str
    minimum_evidence_count: int
    evidence_window: Mapping[str, Any]
    expected_benefit: Mapping[str, Any]
    applicability: Mapping[str, Any]
    assumptions: tuple[str, ...]
    risk_level: RiskLevel
    risk_reasons: tuple[str, ...]
    permission_delta: Mapping[str, Any]
    validation_plan: Mapping[str, Any]
    acceptance_thresholds: Mapping[str, Any]
    rollback_plan: Mapping[str, Any]
    rollback_artifact_refs: tuple[str, ...]
    requires_user_approval: bool
    created_by: str
    created_at_utc: str
    expires_at_utc: str
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    provenance: Mapping[str, Any]
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("candidate_id", "candidate_version_id", "identity_id", "instance_id", "owner_subject_id", "created_by"):
            _validate_id(getattr(self, name), name)
        _positive_int(self.version_number, "version_number")
        _optional_id(self.previous_version_id, "previous_version_id")
        _optional_hash(self.previous_content_hash, "previous_content_hash")
        if self.version_number == 1 and (self.previous_version_id is not None or self.previous_content_hash is not None):
            raise _field_error("previous_version_id", "first version must not reference a predecessor")
        if self.version_number > 1 and (self.previous_version_id is None or self.previous_content_hash is None):
            raise _field_error("previous_version_id", "later versions require predecessor ID and hash")
        object.__setattr__(self, "candidate_kind", _coerce_enum(self.candidate_kind, CandidateKind, "candidate_kind"))
        _text(self.title, "title", maximum=160)
        _text(self.bounded_summary, "bounded_summary", maximum=2_048)
        object.__setattr__(self, "source_evidence_ids", _id_tuple(self.source_evidence_ids, "source_evidence_ids", non_empty=True))
        _validate_hash(self.source_evidence_hash, "source_evidence_hash")
        _positive_int(self.minimum_evidence_count, "minimum_evidence_count", maximum=1_000_000)
        if self.minimum_evidence_count > len(self.source_evidence_ids):
            raise _field_error("minimum_evidence_count", "must not exceed supplied source evidence count")
        for name in ("evidence_window", "expected_benefit", "applicability", "permission_delta", "validation_plan", "acceptance_thresholds", "rollback_plan"):
            object.__setattr__(self, name, _frozen_map(getattr(self, name), name))
        object.__setattr__(self, "assumptions", _text_tuple(self.assumptions, "assumptions"))
        object.__setattr__(self, "risk_level", _coerce_enum(self.risk_level, RiskLevel, "risk_level"))
        object.__setattr__(self, "risk_reasons", _text_tuple(self.risk_reasons, "risk_reasons"))
        object.__setattr__(self, "rollback_artifact_refs", _id_tuple(self.rollback_artifact_refs, "rollback_artifact_refs"))
        _validate_bool(self.requires_user_approval, "requires_user_approval")
        _validate_timestamp(self.created_at_utc, "created_at_utc")
        _validate_timestamp(self.expires_at_utc, "expires_at_utc")
        if self.expires_at_utc <= self.created_at_utc:
            raise _field_error("expires_at_utc", "must follow created_at_utc")
        _common_persistence(self)
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class CandidateTransitionV1(_StrictContract):
    schema_version: int
    transition_id: str
    candidate_version_id: str
    from_state: CandidateState
    to_state: CandidateState
    expected_revision: int
    actor_kind: GrowthActorKind
    actor_id: str
    decision_id: str | None
    reason_code: str
    receipt_ids: tuple[str, ...]
    artifact_id: str | None
    created_at_utc: str
    idempotency_key: str
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("transition_id", "candidate_version_id", "actor_id", "idempotency_key"):
            _validate_id(getattr(self, name), name)
        object.__setattr__(self, "from_state", _coerce_enum(self.from_state, CandidateState, "from_state"))
        object.__setattr__(self, "to_state", _coerce_enum(self.to_state, CandidateState, "to_state"))
        if self.to_state not in _CANDIDATE_TRANSITIONS.get(self.from_state, frozenset()):
            raise _field_error("to_state", f"illegal transition from {self.from_state.value}")
        _positive_int(self.expected_revision, "expected_revision")
        object.__setattr__(self, "actor_kind", _coerce_enum(self.actor_kind, GrowthActorKind, "actor_kind"))
        _optional_id(self.decision_id, "decision_id")
        _code(self.reason_code, "reason_code")
        object.__setattr__(self, "receipt_ids", _id_tuple(self.receipt_ids, "receipt_ids"))
        _optional_id(self.artifact_id, "artifact_id")
        if self.actor_kind is GrowthActorKind.MODEL_PROPOSER and self.to_state in {
            CandidateState.APPROVED, CandidateState.DEPLOYING, CandidateState.ACTIVE
        }:
            raise _field_error("actor_kind", "model proposers cannot approve or deploy")
        if self.to_state in {CandidateState.REJECTED, CandidateState.APPROVED, CandidateState.REVOKED} and self.decision_id is None:
            raise _field_error("decision_id", "decision transition requires a decision")
        _validate_timestamp(self.created_at_utc, "created_at_utc")
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class GrowthDecisionV1(_StrictContract):
    schema_version: int
    decision_id: str
    candidate_version_id: str
    decision_kind: GrowthDecisionKind
    actor_subject_id: str
    expected_revision: int
    candidate_content_hash: str
    artifact_id: str | None
    artifact_payload_sha256: str | None
    test_receipt_ids: tuple[str, ...]
    permission_delta_hash: str
    reason_code: str
    created_at_utc: str
    idempotency_key: str
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("decision_id", "candidate_version_id", "actor_subject_id", "idempotency_key"):
            _validate_id(getattr(self, name), name)
        object.__setattr__(self, "decision_kind", _coerce_enum(self.decision_kind, GrowthDecisionKind, "decision_kind"))
        _positive_int(self.expected_revision, "expected_revision")
        _validate_hash(self.candidate_content_hash, "candidate_content_hash")
        _optional_id(self.artifact_id, "artifact_id")
        _optional_hash(self.artifact_payload_sha256, "artifact_payload_sha256")
        object.__setattr__(self, "test_receipt_ids", _id_tuple(self.test_receipt_ids, "test_receipt_ids"))
        _validate_hash(self.permission_delta_hash, "permission_delta_hash")
        _code(self.reason_code, "reason_code")
        if self.decision_kind is GrowthDecisionKind.APPROVE and (
            self.artifact_id is None or self.artifact_payload_sha256 is None or not self.test_receipt_ids
        ):
            raise _field_error("artifact_id", "approval requires exact artifact hash and test receipts")
        _validate_timestamp(self.created_at_utc, "created_at_utc")
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class SkillArtifactV1(_StrictContract):
    schema_version: int
    artifact_id: str
    artifact_kind: ArtifactKind
    candidate_version_id: str
    skill_name: str
    skill_version: str
    payload_sha256: str
    manifest_sha256: str
    total_bytes: int
    file_count: int
    entrypoints: Mapping[str, Any]
    source_manifest: Mapping[str, Any]
    source_archive_sha256: str
    license_spdx: str
    attribution_refs: tuple[str, ...]
    sbom_ref: str
    dependency_lock_hash: str
    required_capabilities: tuple[str, ...]
    sandbox_policy_version: str
    build_receipt_id: str
    test_receipt_ids: tuple[str, ...]
    compatibility_range: Mapping[str, Any]
    created_at_utc: str
    created_by_forge_version: str
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("artifact_id", "candidate_version_id", "build_receipt_id", "sbom_ref"):
            _validate_id(getattr(self, name), name)
        object.__setattr__(self, "artifact_kind", _coerce_enum(self.artifact_kind, ArtifactKind, "artifact_kind"))
        _code(self.skill_name, "skill_name")
        _text(self.skill_version, "skill_version", maximum=128)
        for name in ("payload_sha256", "manifest_sha256", "source_archive_sha256", "dependency_lock_hash"):
            _validate_hash(getattr(self, name), name)
        _non_negative_int(self.total_bytes, "total_bytes", maximum=1_073_741_824)
        _positive_int(self.file_count, "file_count", maximum=100_000)
        object.__setattr__(self, "entrypoints", _frozen_map(self.entrypoints, "entrypoints"))
        object.__setattr__(self, "source_manifest", _frozen_map(self.source_manifest, "source_manifest"))
        _text(self.license_spdx, "license_spdx", maximum=256)
        object.__setattr__(self, "attribution_refs", _id_tuple(self.attribution_refs, "attribution_refs"))
        object.__setattr__(self, "required_capabilities", _code_tuple(self.required_capabilities, "required_capabilities"))
        _text(self.sandbox_policy_version, "sandbox_policy_version", maximum=128)
        object.__setattr__(self, "test_receipt_ids", _id_tuple(self.test_receipt_ids, "test_receipt_ids", non_empty=True))
        object.__setattr__(self, "compatibility_range", _frozen_map(self.compatibility_range, "compatibility_range"))
        _validate_timestamp(self.created_at_utc, "created_at_utc")
        _text(self.created_by_forge_version, "created_by_forge_version", maximum=128)
        object.__setattr__(self, "privacy_class", _coerce_enum(self.privacy_class, PrivacyClass, "privacy_class"))
        object.__setattr__(self, "retention_class", _coerce_enum(self.retention_class, RetentionClass, "retention_class"))
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class SkillDeploymentV1(_StrictContract):
    schema_version: int
    deployment_id: str
    skill_name: str
    artifact_id: str
    artifact_payload_sha256: str
    candidate_version_id: str
    catalog_record_id: str
    catalog_status_required: CatalogStatus
    approval_id: str
    required_capabilities: tuple[str, ...]
    granted_capability_refs: tuple[str, ...]
    previous_deployment_id: str | None
    deployed_at_utc: str
    deployed_by: str
    rollback_receipt_id: str | None
    revision: int
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("deployment_id", "artifact_id", "candidate_version_id", "catalog_record_id", "approval_id", "deployed_by"):
            _validate_id(getattr(self, name), name)
        _code(self.skill_name, "skill_name")
        _validate_hash(self.artifact_payload_sha256, "artifact_payload_sha256")
        object.__setattr__(self, "catalog_status_required", _coerce_enum(self.catalog_status_required, CatalogStatus, "catalog_status_required"))
        object.__setattr__(self, "required_capabilities", _code_tuple(self.required_capabilities, "required_capabilities"))
        object.__setattr__(self, "granted_capability_refs", _id_tuple(self.granted_capability_refs, "granted_capability_refs"))
        if len(self.required_capabilities) != len(self.granted_capability_refs):
            raise _field_error("granted_capability_refs", "must bind every required capability")
        _optional_id(self.previous_deployment_id, "previous_deployment_id")
        _validate_timestamp(self.deployed_at_utc, "deployed_at_utc")
        _optional_id(self.rollback_receipt_id, "rollback_receipt_id")
        _positive_int(self.revision, "revision")
        self._verify_content_hash()


@dataclass(frozen=True, slots=True)
class BodyCapabilityV1(_StrictContract):
    schema_version: int
    capability_id: str
    body_id: str
    body_version: str
    manifest_sha256: str
    renderer_kind: RendererKind
    supported_channels: tuple[str, ...]
    channel_ranges: Mapping[str, Any]
    viseme_set: tuple[str, ...]
    supported_gestures: tuple[str, ...]
    supported_postures: tuple[str, ...]
    supports_gaze: bool
    supports_blink: bool
    supports_energy_mouth: bool
    supports_word_timing: bool
    supports_phonemes: bool
    interrupt_latency_budget_ms: int
    performance_tiers: tuple[str, ...]
    reduced_motion_substitutions: Mapping[str, Any]
    fallback_body_id: str
    license_ref: str
    created_at_utc: str
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("capability_id", "body_id", "fallback_body_id", "license_ref"):
            _validate_id(getattr(self, name), name)
        _text(self.body_version, "body_version", maximum=128)
        _validate_hash(self.manifest_sha256, "manifest_sha256")
        object.__setattr__(self, "renderer_kind", _coerce_enum(self.renderer_kind, RendererKind, "renderer_kind"))
        for name in ("supported_channels", "viseme_set", "supported_gestures", "supported_postures", "performance_tiers"):
            object.__setattr__(self, name, _code_tuple(getattr(self, name), name))
        object.__setattr__(self, "channel_ranges", _frozen_map(self.channel_ranges, "channel_ranges"))
        for name in ("supports_gaze", "supports_blink", "supports_energy_mouth", "supports_word_timing", "supports_phonemes"):
            _validate_bool(getattr(self, name), name)
        if self.supports_phonemes and not self.supports_word_timing:
            raise _field_error("supports_phonemes", "phoneme timing requires word timing support")
        _positive_int(self.interrupt_latency_budget_ms, "interrupt_latency_budget_ms", maximum=5_000)
        object.__setattr__(self, "reduced_motion_substitutions", _frozen_map(self.reduced_motion_substitutions, "reduced_motion_substitutions"))
        _validate_timestamp(self.created_at_utc, "created_at_utc")
        self._verify_content_hash()


def _freeze_channel_tracks(value: Any) -> Mapping[str, Any]:
    frozen = _frozen_map(value, "channel_tracks")
    if len(frozen) > MAX_EXPRESSION_TRACKS:
        raise _field_error("channel_tracks", f"must contain at most {MAX_EXPRESSION_TRACKS} tracks")
    for channel, track in frozen.items():
        _code(channel, f"channel_tracks.{channel}")
        if not isinstance(track, tuple) or len(track) > MAX_KEYFRAMES_PER_TRACK:
            raise _field_error(f"channel_tracks.{channel}", f"must contain at most {MAX_KEYFRAMES_PER_TRACK} keyframes")
        previous_ms = -1
        for index, keyframe in enumerate(track):
            path = f"channel_tracks.{channel}[{index}]"
            if not isinstance(keyframe, Mapping) or set(keyframe) != {"at_ms", "value", "interpolation"}:
                raise _field_error(path, "must contain exactly at_ms, value, interpolation")
            at_ms = _non_negative_int(keyframe["at_ms"], f"{path}.at_ms", maximum=MAX_EXPRESSION_DURATION_MS)
            if at_ms < previous_ms:
                raise _field_error(f"{path}.at_ms", "must be monotonic")
            previous_ms = at_ms
            _bounded_number(keyframe["value"], f"{path}.value", minimum=-1.0, maximum=1.0)
            if keyframe["interpolation"] not in {"step", "linear", "cubic"}:
                raise _field_error(f"{path}.interpolation", "must be step, linear, or cubic")
    return frozen


@dataclass(frozen=True, slots=True)
class ExpressionPlanV1(_StrictContract):
    schema_version: int
    plan_id: str
    body_id: str
    capability_id: str
    source_expression_id: str
    source_expression_revision: int
    source_snapshot_revision: int
    request_id: str
    playback_generation: int
    generated_at_utc: str
    expires_at_utc: str
    priority: int
    interrupt: bool
    timing_source: TimingSource
    channel_tracks: Mapping[str, Any]
    reduced_motion_applied: bool
    unsupported_channels: tuple[str, ...]
    fallback_actions: tuple[str, ...]
    transition_ms: int
    content_hash: str

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version)
        for name in ("plan_id", "body_id", "capability_id", "source_expression_id", "request_id"):
            _validate_id(getattr(self, name), name)
        _positive_int(self.source_expression_revision, "source_expression_revision")
        _non_negative_int(self.source_snapshot_revision, "source_snapshot_revision")
        _non_negative_int(self.playback_generation, "playback_generation")
        _validate_timestamp(self.generated_at_utc, "generated_at_utc")
        _validate_timestamp(self.expires_at_utc, "expires_at_utc")
        if self.expires_at_utc <= self.generated_at_utc:
            raise _field_error("expires_at_utc", "must follow generated_at_utc")
        _non_negative_int(self.priority, "priority", maximum=1_000)
        _validate_bool(self.interrupt, "interrupt")
        object.__setattr__(self, "timing_source", _coerce_enum(self.timing_source, TimingSource, "timing_source"))
        object.__setattr__(self, "channel_tracks", _freeze_channel_tracks(self.channel_tracks))
        _validate_bool(self.reduced_motion_applied, "reduced_motion_applied")
        object.__setattr__(self, "unsupported_channels", _code_tuple(self.unsupported_channels, "unsupported_channels"))
        object.__setattr__(self, "fallback_actions", _code_tuple(self.fallback_actions, "fallback_actions"))
        _non_negative_int(self.transition_ms, "transition_ms", maximum=10_000)
        self._verify_content_hash()


__all__ = [
    "ArtifactKind",
    "BodyCapabilityV1",
    "CandidateKind",
    "CandidateState",
    "CandidateTransitionV1",
    "CatalogStatus",
    "ExpressionPlanV1",
    "GrowthActorKind",
    "GrowthCandidateV1",
    "GrowthDecisionKind",
    "GrowthDecisionV1",
    "NetworkPolicy",
    "RendererKind",
    "RiskLevel",
    "ScheduleMode",
    "SkillArtifactV1",
    "SkillDeploymentV1",
    "SleepJobKind",
    "SleepJobReceiptV1",
    "SleepJobStatus",
    "SleepPolicyV1",
    "SleepRunState",
    "SleepRunTransitionV1",
    "SleepRunV1",
    "SleepTriggerKind",
    "TimingSource",
    "canonical_content_hash",
    "canonical_json_bytes",
]
