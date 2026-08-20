"""Strict immutable wire contracts for Javis L5 intentions.

The module is deliberately storage-free. Every persisted value has one typed
wire path, bounded fields, deterministic canonical JSON, and hash validation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from core.life.contracts import PrivacyClass, RetentionClass


SCHEMA_VERSION = 1
JAVIS_CANONICAL_JSON_V1 = "JAVIS_CANONICAL_JSON_V1"
MAX_CHECKPOINT_BYTES = 16 * 1024

MAX_ID_BYTES = 256
MAX_CODE_BYTES = 128
MAX_SOURCE_EVENTS = 32
MAX_PARTICIPANTS = 32
MAX_SUCCESS_CRITERIA = 16
MAX_CANCELLATION_CONDITIONS = 8
MAX_CHECKPOINT_ITEMS = 8

_RFC3339_MILLISECONDS = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_LOCAL_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_TIMEZONE_ID = re.compile(r"(?:UTC|[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)*)")

_FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "chainofthought",
        "cot",
        "developerprompt",
        "hiddenprompt",
        "hiddenreasoning",
        "hiddenstate",
        "internalprompt",
        "internalthoughts",
        "logits",
        "messages",
        "modelreasoning",
        "modelmessages",
        "prompt",
        "rawcot",
        "rawmodeloutput",
        "rawprompt",
        "rawreasoning",
        "reasoning",
        "reasoningcontent",
        "scratchpad",
        "systemprompt",
        "thinkingtrace",
        "thoughtprocess",
        "tokenprobabilities",
        "tokenprobs",
    }
)

_ContractT = TypeVar("_ContractT", bound="_WireContract")
_EnumT = TypeVar("_EnumT", bound=Enum)


class IntentionKind(str, Enum):
    REQUEST = "request"
    SUGGESTION = "suggestion"
    COMMITMENT = "commitment"
    GOAL = "goal"
    RISK_RESPONSE = "risk_response"
    MAINTENANCE = "maintenance"


class TriggerKind(str, Enum):
    CONVERSATION = "conversation"
    GOVERNED_EVENT = "governed_event"
    SCHEDULE = "schedule"
    RECOVERY = "recovery"
    EXPLICIT_COMMAND = "explicit_command"


class RiskCeiling(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    IRREVERSIBLE = "irreversible"


class Urgency(str, Enum):
    BACKGROUND = "background"
    NORMAL = "normal"
    TIME_SENSITIVE = "time_sensitive"
    SAFETY = "safety"


class InterruptionCost(str, Enum):
    SILENT = "silent"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class IntentionState(str, Enum):
    CANDIDATE = "candidate"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    WAITING = "waiting"
    BLOCKED = "blocked"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class VerifierKind(str, Enum):
    DETERMINISTIC = "deterministic"
    TRUSTED_ADAPTER = "trusted_adapter"
    USER_CONFIRMATION = "user_confirmation"
    MODEL_ASSISTED = "model_assisted"


class ResumePolicy(str, Enum):
    ASK = "ask"
    RECONCILE_THEN_ASK = "reconcile_then_ask"
    NO_EFFECT_ONLY = "no_effect_only"


class CommitmentState(str, Enum):
    ACTIVE = "active"
    WAITING = "waiting"
    BLOCKED = "blocked"
    VERIFYING = "verifying"
    FULFILLED = "fulfilled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ThinkingStage(str, Enum):
    PLANNING = "planning"
    AWAITING_ACTION = "awaiting_action"
    AWAITING_OBSERVATION = "awaiting_observation"
    AWAITING_USER = "awaiting_user"
    READY_TO_VERIFY = "ready_to_verify"
    BLOCKED = "blocked"


class VerificationRequestedBy(str, Enum):
    INTENTION_SERVICE = "intention_service"
    USER = "user"
    RECOVERY_RECONCILE = "recovery_reconcile"


class VerificationResult(str, Enum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    INCONCLUSIVE = "inconclusive"


class CriterionOutcome(str, Enum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    INCONCLUSIVE = "inconclusive"


class SuggestionCategory(str, Enum):
    SAFETY = "safety"
    REMINDER = "reminder"
    WORKFLOW = "workflow"
    WELLBEING = "wellbeing"
    MAINTENANCE = "maintenance"


class SuggestionOutcome(str, Enum):
    NONE = "none"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SuggestionBudgetState(str, Enum):
    AVAILABLE = "available"
    COOLING_DOWN = "cooling_down"
    QUIET = "quiet"
    EXHAUSTED = "exhausted"
    SUPPRESSED = "suppressed"


class AudienceScope(str, Enum):
    GUEST = "guest"
    OWNER_PRIVATE = "owner_private"
    PARTICIPANTS = "participants"
    EXPLICIT_SHARED = "explicit_shared"


class IntentionEventType(str, Enum):
    INTENTION_CREATED = "intention.created"
    INTENTION_ACCEPTED = "intention.accepted"
    INTENTION_STATE_CHANGED = "intention.state_changed"
    INTENTION_EXPIRED = "intention.expired"
    COMMITMENT_CREATED = "commitment.created"
    COMMITMENT_STATE_CHANGED = "commitment.state_changed"
    CHECKPOINT_CREATED = "intention.checkpoint.created"
    VERIFICATION_RECORDED = "goal.verification.recorded"
    SUGGESTION_BUDGET_CHANGED = "suggestion.budget.changed"


TERMINAL_INTENTION_STATES = frozenset(
    {
        IntentionState.COMPLETED,
        IntentionState.FAILED,
        IntentionState.CANCELLED,
        IntentionState.EXPIRED,
    }
)

INTENTION_STATE_TRANSITIONS: Mapping[IntentionState | None, frozenset[IntentionState]] = {
    None: frozenset({IntentionState.CANDIDATE, IntentionState.ACCEPTED}),
    IntentionState.CANDIDATE: frozenset(
        {
            IntentionState.ACCEPTED,
            IntentionState.FAILED,
            IntentionState.CANCELLED,
            IntentionState.EXPIRED,
        }
    ),
    IntentionState.ACCEPTED: frozenset(
        {
            IntentionState.ACTIVE,
            IntentionState.FAILED,
            IntentionState.CANCELLED,
            IntentionState.EXPIRED,
        }
    ),
    IntentionState.ACTIVE: frozenset(
        {
            IntentionState.WAITING,
            IntentionState.BLOCKED,
            IntentionState.VERIFYING,
            IntentionState.FAILED,
            IntentionState.CANCELLED,
            IntentionState.EXPIRED,
        }
    ),
    IntentionState.WAITING: frozenset(
        {
            IntentionState.ACTIVE,
            IntentionState.BLOCKED,
            IntentionState.VERIFYING,
            IntentionState.FAILED,
            IntentionState.CANCELLED,
            IntentionState.EXPIRED,
        }
    ),
    IntentionState.BLOCKED: frozenset(
        {
            IntentionState.ACTIVE,
            IntentionState.VERIFYING,
            IntentionState.FAILED,
            IntentionState.CANCELLED,
            IntentionState.EXPIRED,
        }
    ),
    IntentionState.VERIFYING: frozenset(
        {
            IntentionState.ACTIVE,
            IntentionState.WAITING,
            IntentionState.BLOCKED,
            IntentionState.COMPLETED,
            IntentionState.FAILED,
            IntentionState.CANCELLED,
            IntentionState.EXPIRED,
        }
    ),
    IntentionState.COMPLETED: frozenset(),
    IntentionState.FAILED: frozenset(),
    IntentionState.CANCELLED: frozenset(),
    IntentionState.EXPIRED: frozenset(),
}


def _field_error(field_name: str, requirement: str) -> ValueError:
    return ValueError(f"{field_name}: {requirement}")


def _forbidden_token(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", key).casefold())


def _scan_forbidden(value: Any, path: str = "wire") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str:
                raise _field_error(path, "JSON object keys must be strings")
            if _forbidden_token(key) in _FORBIDDEN_KEY_TOKENS:
                raise _field_error(f"{path}.{key}", "forbidden field")
            _scan_forbidden(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_forbidden(item, f"{path}[{index}]")


def _schema(value: Any) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise _field_error("schema_version", "must equal 1")


def _utf8_text(
    value: Any,
    field_name: str,
    *,
    minimum: int = 1,
    maximum: int,
    normalize: bool = True,
) -> str:
    if type(value) is not str:
        raise _field_error(field_name, "must be a string")
    text = unicodedata.normalize("NFC", value) if normalize else value
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise _field_error(field_name, "must be valid UTF-8") from exc
    if not minimum <= size <= maximum:
        raise _field_error(
            field_name,
            f"must contain {minimum}..{maximum} UTF-8 bytes",
        )
    return text


def _id(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _utf8_text(value, field_name, maximum=MAX_ID_BYTES)
    if any(unicodedata.category(character) == "Cc" for character in text):
        raise _field_error(field_name, "must not contain control characters")
    return text


def _code(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _utf8_text(value, field_name, maximum=MAX_CODE_BYTES)
    if _CODE.fullmatch(text) is None:
        raise _field_error(field_name, "must be a stable lowercase code")
    return text


def _timestamp(
    value: Any,
    field_name: str,
    *,
    optional: bool = False,
) -> datetime | None:
    if value is None and optional:
        return None
    if type(value) is not str or _RFC3339_MILLISECONDS.fullmatch(value) is None:
        raise _field_error(
            field_name,
            "must be canonical RFC3339 UTC with millisecond precision",
        )
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise _field_error(field_name, "must be a valid UTC timestamp") from exc


def _int_range(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _field_error(field_name, f"must be an integer in [{minimum}, {maximum}]")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise _field_error(field_name, "must be a boolean")
    return value


def _unit_float(value: Any, field_name: str) -> float:
    if type(value) not in (int, float):
        raise _field_error(field_name, "must be a finite number in [0, 1]")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise _field_error(field_name, "must be a finite number in [0, 1]")
    return numeric


def _enum(value: Any, enum_type: type[_EnumT], field_name: str) -> _EnumT:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise _field_error(field_name, f"must be a {enum_type.__name__} wire string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _field_error(field_name, f"unknown {enum_type.__name__} value {value!r}") from exc


def _collection(value: Any, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, (list, tuple)):
        raise _field_error(field_name, "must be a JSON array")
    return value


def _id_tuple(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> tuple[str, ...]:
    items = _collection(value, field_name)
    if not minimum <= len(items) <= maximum:
        raise _field_error(field_name, f"must contain {minimum}..{maximum} items")
    normalized = tuple(_id(item, f"{field_name}[{index}]") for index, item in enumerate(items))
    if len(set(normalized)) != len(normalized):
        raise _field_error(field_name, "must not contain duplicates")
    return normalized  # type: ignore[return-value]


def _code_tuple(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> tuple[str, ...]:
    items = _collection(value, field_name)
    if not minimum <= len(items) <= maximum:
        raise _field_error(field_name, f"must contain {minimum}..{maximum} items")
    normalized = tuple(_code(item, f"{field_name}[{index}]") for index, item in enumerate(items))
    if len(set(normalized)) != len(normalized):
        raise _field_error(field_name, "must not contain duplicates")
    return normalized  # type: ignore[return-value]


def _text_tuple(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int,
    item_bytes: int,
) -> tuple[str, ...]:
    items = _collection(value, field_name)
    if not minimum <= len(items) <= maximum:
        raise _field_error(field_name, f"must contain {minimum}..{maximum} items")
    return tuple(
        _utf8_text(item, f"{field_name}[{index}]", maximum=item_bytes)
        for index, item in enumerate(items)
    )


def _contract(value: Any, contract_type: type[_ContractT], field_name: str) -> _ContractT:
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
    contract_type: type[_ContractT],
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int,
) -> tuple[_ContractT, ...]:
    items = _collection(value, field_name)
    if not minimum <= len(items) <= maximum:
        raise _field_error(field_name, f"must contain {minimum}..{maximum} items")
    return tuple(
        _contract(item, contract_type, f"{field_name}[{index}]")
        for index, item in enumerate(items)
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


def _canonical_value(value: Any, path: str) -> Any:
    value = _wire(value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _field_error(path, "JSON object keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized:
                raise _field_error(path, "contains duplicate keys after NFC normalization")
            normalized[normalized_key] = _canonical_value(item, f"{path}.{normalized_key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if type(value) is str:
        return unicodedata.normalize("NFC", value)
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _field_error(path, "JSON numbers must be finite")
        return value
    raise _field_error(path, f"unsupported JSON value {type(value).__name__}")


def canonical_json_bytes(value: Mapping[str, Any] | "_WireContract") -> bytes:
    """Serialize one object using the JAVIS_CANONICAL_JSON_V1 profile."""

    payload = value.to_dict() if isinstance(value, _WireContract) else value
    if not isinstance(payload, Mapping):
        raise ValueError("canonical JSON root must be an object")
    canonical = _canonical_value(payload, "payload")
    return json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_content_hash(value: Mapping[str, Any] | "_WireContract") -> str:
    """Hash canonical JSON after excluding the top-level content_hash field."""

    payload = value.to_dict() if isinstance(value, _WireContract) else dict(value)
    payload.pop("content_hash", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class _WireContract:
    @classmethod
    def from_dict(cls: type[_ContractT], data: Mapping[str, Any]) -> _ContractT:
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
            rendered = ", ".join(sorted(repr(item) for item in unexpected))
            raise ValueError(f"{cls.__name__}: unexpected field(s): {rendered}")
        return cls(**{name: data[name] for name in expected})

    def to_dict(self) -> dict[str, object]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def canonical_content_hash(self) -> str:
        return canonical_content_hash(self)


def _hash(value: Any, field_name: str = "content_hash") -> str:
    if type(value) is not str or _SHA256_HEX.fullmatch(value) is None:
        raise _field_error(field_name, "must be lowercase SHA-256 hex")
    return value


def _verify_hash(contract: _WireContract) -> None:
    supplied = _hash(getattr(contract, "content_hash"))
    if canonical_content_hash(contract) != supplied:
        raise _field_error("content_hash", "does not match canonical contract content")


def _validate_common(
    contract: _WireContract,
    *,
    object_id_field: str,
    state_type: type[_EnumT],
    source_minimum: int,
) -> tuple[datetime, datetime, datetime]:
    _schema(getattr(contract, "schema_version"))
    _id(getattr(contract, object_id_field), object_id_field)
    for field_name in (
        "javis_identity_id",
        "instance_id",
        "owner_subject_id",
        "runtime_boot_id",
    ):
        _id(getattr(contract, field_name), field_name)
    object.__setattr__(
        contract,
        "participant_ids",
        _id_tuple(
            getattr(contract, "participant_ids"),
            "participant_ids",
            maximum=MAX_PARTICIPANTS,
        ),
    )
    object.__setattr__(
        contract,
        "audience",
        _enum(getattr(contract, "audience"), AudienceScope, "audience"),
    )
    object.__setattr__(
        contract,
        "source_event_ids",
        _id_tuple(
            getattr(contract, "source_event_ids"),
            "source_event_ids",
            minimum=source_minimum,
            maximum=MAX_SOURCE_EVENTS,
        ),
    )
    created = _timestamp(getattr(contract, "created_at_utc"), "created_at_utc")
    updated = _timestamp(getattr(contract, "updated_at_utc"), "updated_at_utc")
    expires = _timestamp(getattr(contract, "expires_at_utc"), "expires_at_utc")
    assert created is not None and updated is not None and expires is not None
    if updated < created:
        raise _field_error("updated_at_utc", "must not precede created_at_utc")
    if expires <= created:
        raise _field_error("expires_at_utc", "must follow created_at_utc")
    if updated > expires:
        raise _field_error("updated_at_utc", "must not follow expires_at_utc")
    object.__setattr__(
        contract,
        "privacy_class",
        _enum(getattr(contract, "privacy_class"), PrivacyClass, "privacy_class"),
    )
    object.__setattr__(
        contract,
        "retention_class",
        _enum(getattr(contract, "retention_class"), RetentionClass, "retention_class"),
    )
    object.__setattr__(
        contract,
        "state",
        _enum(getattr(contract, "state"), state_type, "state"),
    )
    _int_range(getattr(contract, "revision"), "revision", 1, 2**63 - 1)
    normalized_provenance = _code(getattr(contract, "provenance"), "provenance")
    object.__setattr__(contract, "provenance", normalized_provenance)
    _hash(getattr(contract, "content_hash"))
    return created, updated, expires


@dataclass(frozen=True, slots=True)
class SuccessCriterionV1(_WireContract):
    schema_version: int
    criterion_id: str
    description: str
    required: bool
    verifier_kind: VerifierKind
    evidence_requirements: tuple[str, ...]
    freshness_seconds: int
    subjective: bool
    user_confirmation_required: bool | None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.criterion_id, "criterion_id")
        object.__setattr__(
            self,
            "description",
            _utf8_text(self.description, "description", maximum=1024),
        )
        _boolean(self.required, "required")
        object.__setattr__(
            self,
            "verifier_kind",
            _enum(self.verifier_kind, VerifierKind, "verifier_kind"),
        )
        object.__setattr__(
            self,
            "evidence_requirements",
            _code_tuple(
                self.evidence_requirements,
                "evidence_requirements",
                minimum=1,
                maximum=8,
            ),
        )
        _int_range(self.freshness_seconds, "freshness_seconds", 1, 31_536_000)
        _boolean(self.subjective, "subjective")
        if self.user_confirmation_required is not None:
            _boolean(self.user_confirmation_required, "user_confirmation_required")
        if self.subjective and self.user_confirmation_required is not True:
            raise _field_error(
                "user_confirmation_required",
                "must be true for a subjective criterion",
            )
        if self.subjective and self.verifier_kind is not VerifierKind.USER_CONFIRMATION:
            raise _field_error(
                "verifier_kind",
                "subjective criteria require user_confirmation",
            )


@dataclass(frozen=True, slots=True)
class ExecutionBudgetV1(_WireContract):
    schema_version: int
    max_plan_steps: int
    max_action_attempts: int
    max_wall_seconds: int
    max_model_tokens: int

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _int_range(self.max_plan_steps, "max_plan_steps", 1, 256)
        _int_range(self.max_action_attempts, "max_action_attempts", 0, 128)
        _int_range(self.max_wall_seconds, "max_wall_seconds", 1, 604_800)
        _int_range(self.max_model_tokens, "max_model_tokens", 0, 10_000_000)


@dataclass(frozen=True, slots=True)
class BlockerV1(_WireContract):
    code: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _code(self.code, "code"))
        object.__setattr__(
            self,
            "evidence_refs",
            _id_tuple(self.evidence_refs, "evidence_refs", minimum=1, maximum=16),
        )


@dataclass(frozen=True, slots=True)
class DecisionSummaryV1(_WireContract):
    code: str
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _code(self.code, "code"))
        object.__setattr__(
            self,
            "summary",
            _utf8_text(self.summary, "summary", maximum=240),
        )


@dataclass(frozen=True, slots=True)
class CheckpointAssumptionV1(_WireContract):
    statement: str
    source_ref: str
    confidence: float
    invalidation_condition: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "statement",
            _utf8_text(self.statement, "statement", maximum=240),
        )
        _id(self.source_ref, "source_ref")
        object.__setattr__(self, "confidence", _unit_float(self.confidence, "confidence"))
        object.__setattr__(
            self,
            "invalidation_condition",
            _utf8_text(
                self.invalidation_condition,
                "invalidation_condition",
                maximum=240,
            ),
        )


@dataclass(frozen=True, slots=True)
class NextSafeStepV1(_WireContract):
    code: str
    summary: str
    capability_hint: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _code(self.code, "code"))
        object.__setattr__(
            self,
            "summary",
            _utf8_text(self.summary, "summary", maximum=512),
        )
        object.__setattr__(
            self,
            "capability_hint",
            _code(self.capability_hint, "capability_hint", optional=True),
        )


@dataclass(frozen=True, slots=True)
class QuietHoursV1(_WireContract):
    timezone_id: str
    starts_at_local: str
    ends_at_local: str

    def __post_init__(self) -> None:
        timezone_id = _utf8_text(self.timezone_id, "timezone_id", maximum=128)
        if _TIMEZONE_ID.fullmatch(timezone_id) is None or ".." in timezone_id:
            raise _field_error("timezone_id", "must be a canonical IANA timezone ID")
        object.__setattr__(self, "timezone_id", timezone_id)
        for field_name in ("starts_at_local", "ends_at_local"):
            value = getattr(self, field_name)
            if type(value) is not str or _LOCAL_TIME.fullmatch(value) is None:
                raise _field_error(field_name, "must be canonical local HH:MM")
        if self.starts_at_local == self.ends_at_local:
            raise _field_error("ends_at_local", "must differ from starts_at_local")


@dataclass(frozen=True, slots=True)
class IntentionV1(_WireContract):
    schema_version: int
    intention_id: str
    javis_identity_id: str
    instance_id: str
    owner_subject_id: str
    participant_ids: tuple[str, ...]
    audience: AudienceScope
    source_event_ids: tuple[str, ...]
    runtime_boot_id: str
    created_at_utc: str
    updated_at_utc: str
    expires_at_utc: str
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    state: IntentionState
    revision: int
    provenance: str
    kind: IntentionKind
    trigger_kind: TriggerKind
    why_summary: str
    expected_user_value: str
    goal_statement: str
    success_criteria: tuple[SuccessCriterionV1, ...]
    execution_budget: ExecutionBudgetV1
    risk_ceiling: RiskCeiling
    capability_hints: tuple[str, ...]
    urgency: Urgency
    interruption_cost: InterruptionCost
    valid_until_utc: str
    cancellation_conditions: tuple[str, ...]
    commitment_id: str | None
    latest_checkpoint_id: str | None
    latest_verification_id: str | None
    state_reason_code: str
    supersedes_intention_id: str | None
    content_hash: str

    def __post_init__(self) -> None:
        _, _, expires = _validate_common(
            self,
            object_id_field="intention_id",
            state_type=IntentionState,
            source_minimum=1,
        )
        object.__setattr__(self, "kind", _enum(self.kind, IntentionKind, "kind"))
        object.__setattr__(
            self,
            "trigger_kind",
            _enum(self.trigger_kind, TriggerKind, "trigger_kind"),
        )
        object.__setattr__(
            self,
            "why_summary",
            _utf8_text(self.why_summary, "why_summary", maximum=1024),
        )
        object.__setattr__(
            self,
            "expected_user_value",
            _utf8_text(
                self.expected_user_value,
                "expected_user_value",
                minimum=0,
                maximum=512,
            ),
        )
        object.__setattr__(
            self,
            "goal_statement",
            _utf8_text(self.goal_statement, "goal_statement", maximum=2048),
        )
        criteria = _contract_tuple(
            self.success_criteria,
            SuccessCriterionV1,
            "success_criteria",
            minimum=1,
            maximum=MAX_SUCCESS_CRITERIA,
        )
        if len({criterion.criterion_id for criterion in criteria}) != len(criteria):
            raise _field_error("success_criteria", "criterion_id values must be unique")
        object.__setattr__(self, "success_criteria", criteria)
        object.__setattr__(
            self,
            "execution_budget",
            _contract(self.execution_budget, ExecutionBudgetV1, "execution_budget"),
        )
        object.__setattr__(
            self,
            "risk_ceiling",
            _enum(self.risk_ceiling, RiskCeiling, "risk_ceiling"),
        )
        object.__setattr__(
            self,
            "capability_hints",
            _code_tuple(self.capability_hints, "capability_hints", maximum=32),
        )
        object.__setattr__(self, "urgency", _enum(self.urgency, Urgency, "urgency"))
        object.__setattr__(
            self,
            "interruption_cost",
            _enum(self.interruption_cost, InterruptionCost, "interruption_cost"),
        )
        valid_until = _timestamp(self.valid_until_utc, "valid_until_utc")
        if valid_until != expires:
            raise _field_error("valid_until_utc", "must equal expires_at_utc")
        object.__setattr__(
            self,
            "cancellation_conditions",
            _code_tuple(
                self.cancellation_conditions,
                "cancellation_conditions",
                maximum=MAX_CANCELLATION_CONDITIONS,
            ),
        )
        for field_name in (
            "commitment_id",
            "latest_checkpoint_id",
            "latest_verification_id",
            "supersedes_intention_id",
        ):
            _id(getattr(self, field_name), field_name, optional=True)
        object.__setattr__(
            self,
            "state_reason_code",
            _code(self.state_reason_code, "state_reason_code"),
        )
        if self.supersedes_intention_id == self.intention_id:
            raise _field_error("supersedes_intention_id", "must not reference itself")
        _verify_hash(self)


@dataclass(frozen=True, slots=True)
class CommitmentV1(_WireContract):
    schema_version: int
    commitment_id: str
    javis_identity_id: str
    instance_id: str
    owner_subject_id: str
    participant_ids: tuple[str, ...]
    audience: AudienceScope
    source_event_ids: tuple[str, ...]
    runtime_boot_id: str
    created_at_utc: str
    updated_at_utc: str
    expires_at_utc: str
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    state: CommitmentState
    revision: int
    provenance: str
    intention_id: str
    accepted_from_event_id: str
    promise_summary: str
    due_at_utc: str | None
    resume_policy: ResumePolicy
    current_blockers: tuple[BlockerV1, ...]
    next_review_at_utc: str | None
    last_checkpoint_id: str | None
    last_action_receipt_id: str | None
    last_recovery_receipt_id: str | None
    verification_record_ids: tuple[str, ...]
    state_reason_code: str
    content_hash: str

    def __post_init__(self) -> None:
        created, updated, expires = _validate_common(
            self,
            object_id_field="commitment_id",
            state_type=CommitmentState,
            source_minimum=1,
        )
        _id(self.intention_id, "intention_id")
        _id(self.accepted_from_event_id, "accepted_from_event_id")
        if self.accepted_from_event_id not in self.source_event_ids:
            raise _field_error(
                "accepted_from_event_id",
                "must be present in source_event_ids",
            )
        object.__setattr__(
            self,
            "promise_summary",
            _utf8_text(self.promise_summary, "promise_summary", maximum=1024),
        )
        due = _timestamp(self.due_at_utc, "due_at_utc", optional=True)
        if due is not None and not created < due <= expires:
            raise _field_error("due_at_utc", "must be after creation and no later than expiry")
        object.__setattr__(
            self,
            "resume_policy",
            _enum(self.resume_policy, ResumePolicy, "resume_policy"),
        )
        blockers = _contract_tuple(
            self.current_blockers,
            BlockerV1,
            "current_blockers",
            maximum=8,
        )
        if len({blocker.code for blocker in blockers}) != len(blockers):
            raise _field_error("current_blockers", "blocker codes must be unique")
        object.__setattr__(self, "current_blockers", blockers)
        next_review = _timestamp(
            self.next_review_at_utc,
            "next_review_at_utc",
            optional=True,
        )
        if next_review is not None and next_review > expires:
            raise _field_error("next_review_at_utc", "must not follow expiry")
        if next_review is not None and next_review <= updated:
            raise _field_error("next_review_at_utc", "must follow the current update")
        if self.state in (CommitmentState.WAITING, CommitmentState.BLOCKED) and next_review is None:
            raise _field_error("next_review_at_utc", "is required while waiting or blocked")
        if self.state is CommitmentState.BLOCKED and not blockers:
            raise _field_error("current_blockers", "must identify blocked-state evidence")
        for field_name in (
            "last_checkpoint_id",
            "last_action_receipt_id",
            "last_recovery_receipt_id",
        ):
            _id(getattr(self, field_name), field_name, optional=True)
        object.__setattr__(
            self,
            "verification_record_ids",
            _id_tuple(
                self.verification_record_ids,
                "verification_record_ids",
                maximum=32,
            ),
        )
        object.__setattr__(
            self,
            "state_reason_code",
            _code(self.state_reason_code, "state_reason_code"),
        )
        _verify_hash(self)


@dataclass(frozen=True, slots=True)
class ThinkingCheckpointV1(_WireContract):
    schema_version: int
    checkpoint_id: str
    intention_id: str
    commitment_id: str | None
    plan_id: str | None
    plan_revision: int | None
    stage: ThinkingStage
    completed_step_ids: tuple[str, ...]
    action_receipt_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    decision_summaries: tuple[DecisionSummaryV1, ...]
    assumptions: tuple[CheckpointAssumptionV1, ...]
    open_questions: tuple[str, ...]
    next_safe_step: NextSafeStepV1 | None
    blocker_codes: tuple[str, ...]
    budget_snapshot: ExecutionBudgetV1
    created_at_utc: str
    content_hash: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.checkpoint_id, "checkpoint_id")
        _id(self.intention_id, "intention_id")
        _id(self.commitment_id, "commitment_id", optional=True)
        _id(self.plan_id, "plan_id", optional=True)
        if (self.plan_id is None) != (self.plan_revision is None):
            raise _field_error("plan_revision", "must be present exactly when plan_id is present")
        if self.plan_revision is not None:
            _int_range(self.plan_revision, "plan_revision", 1, 2**63 - 1)
        object.__setattr__(self, "stage", _enum(self.stage, ThinkingStage, "stage"))
        for field_name, maximum in (
            ("completed_step_ids", 128),
            ("action_receipt_ids", 64),
            ("evidence_refs", 64),
        ):
            object.__setattr__(
                self,
                field_name,
                _id_tuple(getattr(self, field_name), field_name, maximum=maximum),
            )
        object.__setattr__(
            self,
            "decision_summaries",
            _contract_tuple(
                self.decision_summaries,
                DecisionSummaryV1,
                "decision_summaries",
                maximum=MAX_CHECKPOINT_ITEMS,
            ),
        )
        object.__setattr__(
            self,
            "assumptions",
            _contract_tuple(
                self.assumptions,
                CheckpointAssumptionV1,
                "assumptions",
                maximum=MAX_CHECKPOINT_ITEMS,
            ),
        )
        object.__setattr__(
            self,
            "open_questions",
            _text_tuple(
                self.open_questions,
                "open_questions",
                maximum=MAX_CHECKPOINT_ITEMS,
                item_bytes=240,
            ),
        )
        if self.next_safe_step is not None:
            object.__setattr__(
                self,
                "next_safe_step",
                _contract(self.next_safe_step, NextSafeStepV1, "next_safe_step"),
            )
        object.__setattr__(
            self,
            "blocker_codes",
            _code_tuple(self.blocker_codes, "blocker_codes", maximum=8),
        )
        if self.stage is ThinkingStage.BLOCKED and not self.blocker_codes:
            raise _field_error("blocker_codes", "must identify a blocked stage")
        object.__setattr__(
            self,
            "budget_snapshot",
            _contract(self.budget_snapshot, ExecutionBudgetV1, "budget_snapshot"),
        )
        _timestamp(self.created_at_utc, "created_at_utc")
        _verify_hash(self)
        if len(canonical_json_bytes(self)) > MAX_CHECKPOINT_BYTES:
            raise _field_error("checkpoint", "canonical JSON must not exceed 16 KiB")


@dataclass(frozen=True, slots=True)
class CriterionResultV1(_WireContract):
    schema_version: int
    criterion_id: str
    outcome: CriterionOutcome
    evidence_refs: tuple[str, ...]
    fresh_until_utc: str
    method: str
    explanation_code: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.criterion_id, "criterion_id")
        object.__setattr__(
            self,
            "outcome",
            _enum(self.outcome, CriterionOutcome, "outcome"),
        )
        object.__setattr__(
            self,
            "evidence_refs",
            _id_tuple(self.evidence_refs, "evidence_refs", maximum=32),
        )
        if self.outcome is CriterionOutcome.SATISFIED and not self.evidence_refs:
            raise _field_error("evidence_refs", "satisfied criteria require evidence")
        _timestamp(self.fresh_until_utc, "fresh_until_utc")
        object.__setattr__(self, "method", _code(self.method, "method"))
        object.__setattr__(
            self,
            "explanation_code",
            _code(self.explanation_code, "explanation_code"),
        )


@dataclass(frozen=True, slots=True)
class VerificationRecordV1(_WireContract):
    schema_version: int
    verification_id: str
    javis_identity_id: str
    instance_id: str
    owner_subject_id: str
    participant_ids: tuple[str, ...]
    audience: AudienceScope
    source_event_ids: tuple[str, ...]
    runtime_boot_id: str
    created_at_utc: str
    updated_at_utc: str
    expires_at_utc: str
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    state: VerificationResult
    revision: int
    provenance: str
    intention_id: str
    intention_revision: int
    commitment_id: str | None
    requested_by: VerificationRequestedBy
    verifier_kind: VerifierKind
    verifier_version: str
    criterion_results: tuple[CriterionResultV1, ...]
    evidence_refs: tuple[str, ...]
    observed_at_utc: str
    result: VerificationResult
    limitations: tuple[str, ...]
    conflicting_evidence_refs: tuple[str, ...]
    content_hash: str

    def __post_init__(self) -> None:
        created, updated, expires = _validate_common(
            self,
            object_id_field="verification_id",
            state_type=VerificationResult,
            source_minimum=1,
        )
        if self.revision != 1 or updated != created:
            raise _field_error("revision", "immutable verification records require revision 1")
        _id(self.intention_id, "intention_id")
        _int_range(self.intention_revision, "intention_revision", 1, 2**63 - 1)
        _id(self.commitment_id, "commitment_id", optional=True)
        object.__setattr__(
            self,
            "requested_by",
            _enum(self.requested_by, VerificationRequestedBy, "requested_by"),
        )
        object.__setattr__(
            self,
            "verifier_kind",
            _enum(self.verifier_kind, VerifierKind, "verifier_kind"),
        )
        object.__setattr__(
            self,
            "verifier_version",
            _code(self.verifier_version, "verifier_version"),
        )
        results = _contract_tuple(
            self.criterion_results,
            CriterionResultV1,
            "criterion_results",
            minimum=1,
            maximum=MAX_SUCCESS_CRITERIA,
        )
        if len({result.criterion_id for result in results}) != len(results):
            raise _field_error("criterion_results", "criterion_id values must be unique")
        object.__setattr__(self, "criterion_results", results)
        evidence = _id_tuple(self.evidence_refs, "evidence_refs", maximum=64)
        object.__setattr__(self, "evidence_refs", evidence)
        evidence_set = set(evidence)
        for result in results:
            if not set(result.evidence_refs).issubset(evidence_set):
                raise _field_error(
                    "criterion_results",
                    "criterion evidence must be present in record evidence_refs",
                )
        observed = _timestamp(self.observed_at_utc, "observed_at_utc")
        assert observed is not None
        if observed > created:
            raise _field_error("observed_at_utc", "must not follow record creation")
        for item in results:
            fresh_until = _timestamp(item.fresh_until_utc, "fresh_until_utc")
            assert fresh_until is not None
            if fresh_until <= observed:
                raise _field_error(
                    "criterion_results",
                    "fresh_until_utc must follow observed_at_utc",
                )
        object.__setattr__(
            self,
            "result",
            _enum(self.result, VerificationResult, "result"),
        )
        if self.state is not self.result:
            raise _field_error("state", "must equal result")
        object.__setattr__(
            self,
            "limitations",
            _text_tuple(
                self.limitations,
                "limitations",
                maximum=8,
                item_bytes=240,
            ),
        )
        conflicts = _id_tuple(
            self.conflicting_evidence_refs,
            "conflicting_evidence_refs",
            maximum=32,
        )
        object.__setattr__(self, "conflicting_evidence_refs", conflicts)
        if self.result is VerificationResult.SATISFIED:
            if conflicts:
                raise _field_error("result", "cannot be satisfied with conflicting evidence")
        if expires < max(
            _timestamp(item.fresh_until_utc, "fresh_until_utc") for item in results
        ):
            raise _field_error("expires_at_utc", "must cover all criterion freshness windows")
        _verify_hash(self)


@dataclass(frozen=True, slots=True)
class SuggestionBudgetV1(_WireContract):
    schema_version: int
    budget_id: str
    javis_identity_id: str
    instance_id: str
    owner_subject_id: str
    participant_ids: tuple[str, ...]
    audience: AudienceScope
    source_event_ids: tuple[str, ...]
    runtime_boot_id: str
    created_at_utc: str
    updated_at_utc: str
    expires_at_utc: str
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    state: SuggestionBudgetState
    revision: int
    provenance: str
    category: SuggestionCategory
    window_started_at_utc: str
    window_ends_at_utc: str
    max_presentations: int
    presentations_used: int
    global_budget_revision: int
    cooldown_until_utc: str | None
    quiet_hours: QuietHoursV1
    suppressed_until_utc: str | None
    last_candidate_id: str | None
    last_presented_intention_id: str | None
    last_outcome: SuggestionOutcome
    content_hash: str

    def __post_init__(self) -> None:
        _, updated, expires = _validate_common(
            self,
            object_id_field="budget_id",
            state_type=SuggestionBudgetState,
            source_minimum=0,
        )
        object.__setattr__(
            self,
            "category",
            _enum(self.category, SuggestionCategory, "category"),
        )
        window_start = _timestamp(self.window_started_at_utc, "window_started_at_utc")
        window_end = _timestamp(self.window_ends_at_utc, "window_ends_at_utc")
        assert window_start is not None and window_end is not None
        if window_end <= window_start:
            raise _field_error("window_ends_at_utc", "must follow window_started_at_utc")
        if window_end != expires:
            raise _field_error("expires_at_utc", "must equal window_ends_at_utc")
        _int_range(self.max_presentations, "max_presentations", 0, 1000)
        _int_range(self.presentations_used, "presentations_used", 0, 1000)
        if self.presentations_used > self.max_presentations:
            raise _field_error("presentations_used", "must not exceed max_presentations")
        _int_range(self.global_budget_revision, "global_budget_revision", 1, 2**63 - 1)
        cooldown = _timestamp(self.cooldown_until_utc, "cooldown_until_utc", optional=True)
        suppressed = _timestamp(
            self.suppressed_until_utc,
            "suppressed_until_utc",
            optional=True,
        )
        object.__setattr__(
            self,
            "quiet_hours",
            _contract(self.quiet_hours, QuietHoursV1, "quiet_hours"),
        )
        _id(self.last_candidate_id, "last_candidate_id", optional=True)
        _id(
            self.last_presented_intention_id,
            "last_presented_intention_id",
            optional=True,
        )
        object.__setattr__(
            self,
            "last_outcome",
            _enum(self.last_outcome, SuggestionOutcome, "last_outcome"),
        )
        if self.state is SuggestionBudgetState.EXHAUSTED and (
            self.presentations_used != self.max_presentations
        ):
            raise _field_error("state", "exhausted requires used == max")
        if self.state is SuggestionBudgetState.COOLING_DOWN and (
            cooldown is None or cooldown <= updated
        ):
            raise _field_error("cooldown_until_utc", "must be in the future while cooling down")
        if self.state is SuggestionBudgetState.SUPPRESSED and (
            suppressed is None or suppressed <= updated
        ):
            raise _field_error("suppressed_until_utc", "must be in the future while suppressed")
        _verify_hash(self)


def is_valid_intention_transition(
    from_state: IntentionState | str | None,
    to_state: IntentionState | str,
) -> bool:
    normalized_from = (
        None if from_state is None else _enum(from_state, IntentionState, "from_state")
    )
    normalized_to = _enum(to_state, IntentionState, "to_state")
    return normalized_to in INTENTION_STATE_TRANSITIONS[normalized_from]


@dataclass(frozen=True, slots=True)
class IntentionTransitionEventV1(_WireContract):
    schema_version: int
    transition_id: str
    intention_id: str
    from_state: IntentionState | None
    to_state: IntentionState
    expected_revision: int
    resulting_revision: int
    idempotency_key: str
    reason_code: str
    evidence_refs: tuple[str, ...]
    runtime_boot_id: str
    occurred_at_utc: str
    content_hash: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.transition_id, "transition_id")
        _id(self.intention_id, "intention_id")
        normalized_from = (
            None
            if self.from_state is None
            else _enum(self.from_state, IntentionState, "from_state")
        )
        object.__setattr__(self, "from_state", normalized_from)
        object.__setattr__(
            self,
            "to_state",
            _enum(self.to_state, IntentionState, "to_state"),
        )
        _int_range(self.expected_revision, "expected_revision", 0, 2**63 - 2)
        _int_range(self.resulting_revision, "resulting_revision", 1, 2**63 - 1)
        if self.resulting_revision != self.expected_revision + 1:
            raise _field_error("resulting_revision", "must equal expected_revision + 1")
        _id(self.idempotency_key, "idempotency_key")
        object.__setattr__(self, "reason_code", _code(self.reason_code, "reason_code"))
        object.__setattr__(
            self,
            "evidence_refs",
            _id_tuple(self.evidence_refs, "evidence_refs", maximum=32),
        )
        _id(self.runtime_boot_id, "runtime_boot_id")
        _timestamp(self.occurred_at_utc, "occurred_at_utc")
        if not is_valid_intention_transition(self.from_state, self.to_state):
            raise _field_error(
                "to_state",
                f"illegal transition from {self.from_state!r} to {self.to_state.value!r}",
            )
        if self.to_state in (
            IntentionState.BLOCKED,
            IntentionState.VERIFYING,
            IntentionState.COMPLETED,
        ) and not self.evidence_refs:
            raise _field_error("evidence_refs", "transition requires evidence")
        _verify_hash(self)


@dataclass(frozen=True, slots=True)
class IntentionEventV1(_WireContract):
    """Thin EventBus contract; it cannot carry goal or evidence body text."""

    schema_version: int
    event_id: str
    event_type: IntentionEventType
    object_id: str
    owner_subject_hash: str
    revision: int
    state: str
    reason_code: str
    source_event_id: str | None
    occurred_at_utc: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.event_id, "event_id")
        object.__setattr__(
            self,
            "event_type",
            _enum(self.event_type, IntentionEventType, "event_type"),
        )
        _id(self.object_id, "object_id")
        _hash(self.owner_subject_hash, "owner_subject_hash")
        _int_range(self.revision, "revision", 1, 2**63 - 1)
        object.__setattr__(self, "state", _code(self.state, "state"))
        object.__setattr__(self, "reason_code", _code(self.reason_code, "reason_code"))
        _id(self.source_event_id, "source_event_id", optional=True)
        _timestamp(self.occurred_at_utc, "occurred_at_utc")
        allowed_states: set[str]
        if self.event_type is IntentionEventType.INTENTION_CREATED:
            allowed_states = {
                IntentionState.CANDIDATE.value,
                IntentionState.ACCEPTED.value,
            }
        elif self.event_type is IntentionEventType.INTENTION_ACCEPTED:
            allowed_states = {IntentionState.ACCEPTED.value}
        elif self.event_type is IntentionEventType.INTENTION_EXPIRED:
            allowed_states = {IntentionState.EXPIRED.value}
        elif self.event_type is IntentionEventType.INTENTION_STATE_CHANGED:
            allowed_states = {item.value for item in IntentionState}
        elif self.event_type is IntentionEventType.COMMITMENT_CREATED:
            allowed_states = {CommitmentState.ACTIVE.value}
        elif self.event_type is IntentionEventType.COMMITMENT_STATE_CHANGED:
            allowed_states = {item.value for item in CommitmentState}
        elif self.event_type is IntentionEventType.CHECKPOINT_CREATED:
            allowed_states = {item.value for item in ThinkingStage}
        elif self.event_type is IntentionEventType.VERIFICATION_RECORDED:
            allowed_states = {item.value for item in VerificationResult}
        else:
            allowed_states = {item.value for item in SuggestionBudgetState}
        if self.state not in allowed_states:
            raise _field_error("state", "does not match event object type")


IntentionStateMachineEventV1 = IntentionTransitionEventV1
StateMachineEventV1 = IntentionTransitionEventV1
Audience = AudienceScope
CheckpointStage = ThinkingStage
CommitmentResumePolicy = ResumePolicy
CriterionVerifierKind = VerifierKind
IntentionInterruptionCost = InterruptionCost
IntentionRiskCeiling = RiskCeiling
IntentionTriggerKind = TriggerKind
IntentionUrgency = Urgency
SuggestionBudgetCategory = SuggestionCategory
SuggestionLastOutcome = SuggestionOutcome
VerificationOutcome = VerificationResult
VerificationRequester = VerificationRequestedBy


__all__ = [
    "AudienceScope",
    "Audience",
    "BlockerV1",
    "CheckpointAssumptionV1",
    "CheckpointStage",
    "CommitmentResumePolicy",
    "CommitmentState",
    "CommitmentV1",
    "CriterionOutcome",
    "CriterionResultV1",
    "CriterionVerifierKind",
    "DecisionSummaryV1",
    "ExecutionBudgetV1",
    "INTENTION_STATE_TRANSITIONS",
    "IntentionEventType",
    "IntentionEventV1",
    "IntentionInterruptionCost",
    "IntentionKind",
    "IntentionRiskCeiling",
    "IntentionState",
    "IntentionStateMachineEventV1",
    "IntentionTransitionEventV1",
    "IntentionTriggerKind",
    "IntentionUrgency",
    "IntentionV1",
    "InterruptionCost",
    "JAVIS_CANONICAL_JSON_V1",
    "MAX_CHECKPOINT_BYTES",
    "NextSafeStepV1",
    "PrivacyClass",
    "QuietHoursV1",
    "ResumePolicy",
    "RetentionClass",
    "RiskCeiling",
    "SCHEMA_VERSION",
    "StateMachineEventV1",
    "SuggestionBudgetState",
    "SuggestionBudgetCategory",
    "SuggestionBudgetV1",
    "SuggestionCategory",
    "SuggestionOutcome",
    "SuggestionLastOutcome",
    "SuccessCriterionV1",
    "TERMINAL_INTENTION_STATES",
    "ThinkingCheckpointV1",
    "ThinkingStage",
    "TriggerKind",
    "Urgency",
    "VerificationRecordV1",
    "VerificationOutcome",
    "VerificationRequester",
    "VerificationRequestedBy",
    "VerificationResult",
    "VerifierKind",
    "canonical_content_hash",
    "canonical_json_bytes",
    "is_valid_intention_transition",
]
