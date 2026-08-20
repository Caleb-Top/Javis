"""Strict immutable wire contracts for governed Javis actions.

This module is deliberately storage- and transport-free. It defines the L6
wire boundary, deterministic hashes, and the fail-closed validation shared by
the approval, journal, executor, and recovery layers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, TypeVar

from core.intention.contracts import JAVIS_CANONICAL_JSON_V1


SCHEMA_VERSION = 1
JAVIS_ACTION_PARAMETERS_V1 = "JAVIS_ACTION_PARAMETERS_V1"

MAX_ID_BYTES = 256
MAX_CODE_BYTES = 128
MAX_SOURCE_REF_BYTES = 256
MAX_PARAMETERS_BYTES = 64 * 1024
MAX_TARGET_SCOPE_BYTES = 8 * 1024
MAX_PREVIEW_BYTES = 8 * 1024
MAX_RECEIPT_BYTES = 16 * 1024
MAX_TEXT_BYTES = 2 * 1024
MAX_OBSERVATION_BYTES = 4 * 1024
MAX_PREDICATES = 16
MAX_EXPECTED_OBSERVATIONS = 16
MAX_REASON_CODES = 16
MAX_UNCERTAIN_ACTIONS = 128
MAX_LIMITATIONS = 16
GENESIS_RECEIPT_HASH = "0" * 64

_RFC3339_MILLISECONDS = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_SECRET_MARKER = re.compile(
    r"(?:\bbearer\s+[a-z0-9._~+/=-]+|"
    r"(?:api[_-]?key|access[_-]?token|grant[_-]?secret|password)\s*[:=]|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.IGNORECASE,
)
_PATH_KEY_TOKENS = frozenset(
    {
        "path",
        "filepath",
        "directory",
        "directorypath",
        "relativepath",
        "workspace",
        "workspaceroot",
        "targetpath",
        "sourcepath",
        "destinationpath",
    }
)
_SECRET_KEY_TOKENS = frozenset(
    {
        "secret",
        "grantsecret",
        "bearer",
        "bearertoken",
        "token",
        "accesstoken",
        "refreshtoken",
        "password",
        "passphrase",
        "apikey",
        "accesskey",
        "privatekey",
        "credential",
        "credentials",
    }
)
_AUTHORITY_BYPASS_TOKENS = frozenset(
    {
        "confirmed",
        "approved",
        "root",
        "rootoverride",
        "permissionoverride",
        "goalcompleted",
        "grantsecret",
        "bearergrant",
    }
)
_CLIENT_OWNER_TOKENS = frozenset(
    {"owner", "ownerid", "ownersubject", "ownersubjectid", "subjectowner"}
)
_EVENT_PRIVATE_FIELDS = frozenset(
    {
        "grant_secret_digest",
        "grant_id_hash",
        "authorization_grant_id_hash",
        "normalized_parameters",
        "target_scope",
        "preconditions",
        "expected_observations",
        "preview",
        "target_summary",
        "effect_observation",
        "impact_summary",
        "limitations",
        "recovery_material_ref",
    }
)

_ContractT = TypeVar("_ContractT", bound="_WireContract")
_EnumT = TypeVar("_EnumT", bound=Enum)


class EffectClass(str, Enum):
    NONE = "none"
    REVERSIBLE = "reversible"
    COMPENSATABLE = "compensatable"
    IRREVERSIBLE = "irreversible"


class RiskClass(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionPhase(str, Enum):
    DENIED = "denied"
    PREPARED = "prepared"
    STARTED = "started"
    EFFECT_SUCCEEDED = "effect_succeeded"
    EFFECT_FAILED = "effect_failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"
    RECONCILED = "reconciled"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_SUCCEEDED = "recovery_succeeded"
    RECOVERY_FAILED = "recovery_failed"


class ActionState(str, Enum):
    PROPOSED = "proposed"
    DENIED = "denied"
    AWAITING_APPROVAL = "awaiting_approval"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    AUTHORIZED = "authorized"
    PREPARED = "prepared"
    EXECUTING = "executing"
    EFFECT_SUCCEEDED = "effect_succeeded"
    EFFECT_FAILED = "effect_failed"
    UNCERTAIN = "uncertain"
    RECONCILING = "reconciling"
    RECONCILED_NO_EFFECT = "reconciled_no_effect"
    RECONCILED_EFFECT = "reconciled_effect"
    RECOVERY_REQUIRED = "recovery_required"
    RECOVERED = "recovered"
    RECOVERY_FAILED = "recovery_failed"
    MANUAL_REQUIRED = "manual_required"


class ActionSourceKind(str, Enum):
    AGENT = "agent"
    CONTROL_HTTP = "control_http"
    CONVERSATION_WS = "conversation_ws"
    SUBAGENT = "subagent"
    CRON = "cron"
    RECOVERY = "recovery"
    SYSTEM_STOP = "system_stop"


class GrantIssuerKind(str, Enum):
    POLICY = "policy"
    LOCAL_USER_APPROVAL = "local_user_approval"
    RECOVERY_APPROVAL = "recovery_approval"


class GrantState(str, Enum):
    ISSUED = "issued"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    DENY = "deny"


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RecoveryMethod(str, Enum):
    OBSERVE_ONLY = "observe_only"
    COMPENSATE = "compensate"
    RESTORE_SNAPSHOT = "restore_snapshot"
    MANUAL = "manual"


class RecoveryResult(str, Enum):
    NO_EFFECT_OBSERVED = "no_effect_observed"
    EFFECT_OBSERVED = "effect_observed"
    PARTIAL_EFFECT = "partial_effect"
    RECOVERED = "recovered"
    RECOVERY_FAILED = "recovery_failed"
    MANUAL_REQUIRED = "manual_required"
    INCONCLUSIVE = "inconclusive"


class RecoveryRequestedBy(str, Enum):
    STARTUP_RECONCILE = "startup_reconcile"
    LOCAL_USER = "local_user"
    SAFETY_CONTROLLER = "safety_controller"


class SafetyMode(str, Enum):
    NORMAL = "normal"
    SAFE = "safe"
    RECONCILING = "reconciling"
    RECOVERY_ONLY = "recovery_only"
    SHUTDOWN = "shutdown"


class EffectAdmission(str, Enum):
    OPEN = "open"
    RECOVERY_ONLY = "recovery_only"
    CLOSED = "closed"


class JournalHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CORRUPT = "corrupt"
    UNAVAILABLE = "unavailable"


class ReceiptChainHealth(str, Enum):
    HEALTHY = "healthy"
    GAP = "gap"
    TAMPERED = "tampered"
    UNAVAILABLE = "unavailable"


class ApprovalAuthorityHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


SAFETY_REASON_CODES = frozenset(
    {
        "adapter_permit_violation",
        "approval_authority_unavailable",
        "journal_unavailable",
        "manual_fuse_trip",
        "policy_unavailable",
        "receipt_chain_gap",
        "receipt_chain_tampered",
        "recovery_material_incomplete",
        "shutdown_requested",
        "startup_reconciliation",
        "unknown_inflight_effect",
        "wal_barrier_failure",
    }
)


def _field_error(field_name: str, requirement: str) -> ValueError:
    return ValueError(f"{field_name}: {requirement}")


def _key_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", value).casefold())


def _schema(value: Any) -> int:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise _field_error("schema_version", f"must be literal {SCHEMA_VERSION}")
    return value


def _safe_text(
    value: Any,
    field_name: str,
    *,
    minimum: int = 1,
    maximum: int = MAX_TEXT_BYTES,
    optional: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        raise _field_error(field_name, "must be a string")
    text = unicodedata.normalize("NFC", value)
    size = len(text.encode("utf-8"))
    if not minimum <= size <= maximum:
        raise _field_error(field_name, f"must contain {minimum}..{maximum} UTF-8 bytes")
    if any(unicodedata.category(character) == "Cc" for character in text):
        raise _field_error(field_name, "must not contain control characters")
    if _SECRET_MARKER.search(text) is not None:
        raise _field_error(field_name, "must not contain secret material")
    return text


def _id(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    return _safe_text(value, field_name, maximum=MAX_ID_BYTES, optional=optional)


def _code(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    text = _safe_text(value, field_name, maximum=MAX_CODE_BYTES, optional=optional)
    if text is None:
        return None
    if _CODE.fullmatch(text) is None:
        raise _field_error(field_name, "must be a stable lowercase code")
    return text


def _hash(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _SHA256_HEX.fullmatch(value) is None:
        raise _field_error(field_name, "must be lowercase SHA-256 hex")
    return value


def _timestamp(value: Any, field_name: str, *, optional: bool = False) -> datetime | None:
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


def _integer(value: Any, field_name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _field_error(field_name, f"must be an integer in [{minimum}, {maximum}]")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise _field_error(field_name, "must be a boolean")
    return value


def _enum(value: Any, enum_type: type[_EnumT], field_name: str) -> _EnumT:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise _field_error(field_name, f"must be a {enum_type.__name__} wire string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _field_error(field_name, f"unknown {enum_type.__name__} value {value!r}") from exc


def _json_value(value: Any, path: str, semantic_keys: set[str]) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        local_tokens: set[str] = set()
        for key, item in value.items():
            if type(key) is not str:
                raise _field_error(path, "JSON object keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            token = _key_token(normalized_key)
            if normalized_key in normalized or token in local_tokens:
                raise _field_error(path, "contains duplicate semantic keys")
            if token in _SECRET_KEY_TOKENS:
                raise _field_error(f"{path}.{normalized_key}", "secret-bearing keys are forbidden")
            if token in _AUTHORITY_BYPASS_TOKENS:
                raise _field_error(f"{path}.{normalized_key}", "authority override fields are forbidden")
            local_tokens.add(token)
            semantic_keys.add(token)
            child = _json_value(item, f"{path}.{normalized_key}", semantic_keys)
            if token in _PATH_KEY_TOKENS and isinstance(child, str):
                _validate_path_text(child, f"{path}.{normalized_key}")
            normalized[normalized_key] = child
        return MappingProxyType(normalized)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            _json_value(item, f"{path}[{index}]", semantic_keys)
            for index, item in enumerate(value)
        )
    if type(value) is str:
        return _safe_text(value, path, minimum=0, maximum=MAX_PARAMETERS_BYTES)
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _field_error(path, "JSON numbers must be finite")
        return 0.0 if value == 0.0 else value
    raise _field_error(path, f"unsupported JSON value {type(value).__name__}")


def _validate_path_text(value: str, field_name: str) -> None:
    normalized = value.replace("\\", "/")
    if any(segment == ".." for segment in normalized.split("/")):
        raise _field_error(field_name, "path traversal is forbidden")
    if normalized.startswith("//") or re.match(r"^[a-zA-Z]:[^/]", normalized):
        raise _field_error(field_name, "path target must be unambiguous")


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


def _safe_json_object(value: Any, field_name: str, *, maximum: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _field_error(field_name, "must be a JSON object")
    frozen = _json_value(value, field_name, set())
    assert isinstance(frozen, Mapping)
    size = len(canonical_json_bytes(frozen))
    if size > maximum:
        raise _field_error(field_name, f"canonical JSON must not exceed {maximum} bytes")
    return frozen


def _safe_json_tuple(
    value: Any,
    field_name: str,
    *,
    maximum_items: int,
    maximum_bytes: int,
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _field_error(field_name, "must be a JSON array")
    if len(value) > maximum_items:
        raise _field_error(field_name, f"must contain at most {maximum_items} items")
    items = tuple(
        _safe_json_object(item, f"{field_name}[{index}]", maximum=maximum_bytes)
        for index, item in enumerate(value)
    )
    if len(canonical_json_bytes({"items": items})) > maximum_bytes:
        raise _field_error(field_name, f"canonical JSON must not exceed {maximum_bytes} bytes")
    return items


def _id_tuple(value: Any, field_name: str, *, maximum: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _field_error(field_name, "must be a JSON array")
    if len(value) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} items")
    result = tuple(_id(item, f"{field_name}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must not contain duplicates")
    return result  # type: ignore[return-value]


def _code_tuple(
    value: Any,
    field_name: str,
    *,
    maximum: int,
    allowlist: frozenset[str] | None = None,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _field_error(field_name, "must be a JSON array")
    if len(value) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} items")
    result = tuple(_code(item, f"{field_name}[{index}]") for index, item in enumerate(value))
    if len(set(result)) != len(result):
        raise _field_error(field_name, "must not contain duplicates")
    if allowlist is not None and any(item not in allowlist for item in result):
        raise _field_error(field_name, "contains a code outside the frozen allowlist")
    return result  # type: ignore[return-value]


def _text_tuple(value: Any, field_name: str, *, maximum: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise _field_error(field_name, "must be a JSON array")
    if len(value) > maximum:
        raise _field_error(field_name, f"must contain at most {maximum} items")
    return tuple(
        _safe_text(item, f"{field_name}[{index}]", maximum=MAX_TEXT_BYTES)
        for index, item in enumerate(value)
    )  # type: ignore[return-value]


def canonical_json_bytes(value: Mapping[str, Any] | "_WireContract") -> bytes:
    payload = value.to_dict() if isinstance(value, _WireContract) else _wire(value)
    if not isinstance(payload, Mapping):
        raise ValueError("canonical JSON root must be an object")
    normalized = _json_value(payload, "payload", set())
    return json.dumps(
        _wire(normalized),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_content_hash(value: Mapping[str, Any] | "_WireContract") -> str:
    payload = value.to_dict() if isinstance(value, _WireContract) else dict(value)
    payload.pop("content_hash", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def canonical_target_scope_hash(target_scope: Mapping[str, Any]) -> str:
    normalized = _safe_json_object(
        target_scope,
        "target_scope",
        maximum=MAX_TARGET_SCOPE_BYTES,
    )
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def canonical_preview_hash(preview: Mapping[str, Any]) -> str:
    normalized = _safe_json_object(preview, "preview", maximum=MAX_PREVIEW_BYTES)
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def action_parameters_hash(
    *,
    action_name: str,
    parameters: Mapping[str, Any],
    target_scope: Mapping[str, Any],
    intention_id: str,
    intention_revision: int,
) -> str:
    """Return the domain-separated JAVIS_ACTION_PARAMETERS_V1 digest."""

    normalized_parameters = _safe_json_object(
        parameters,
        "normalized_parameters",
        maximum=MAX_PARAMETERS_BYTES,
    )
    normalized_target = _safe_json_object(
        target_scope,
        "target_scope",
        maximum=MAX_TARGET_SCOPE_BYTES,
    )
    payload = {
        "action_name": _code(action_name, "action_name"),
        "parameters": normalized_parameters,
        "target_scope": normalized_target,
        "intention_id": _id(intention_id, "intention_id"),
        "intention_revision": _integer(
            intention_revision,
            "intention_revision",
            1,
            2**63 - 1,
        ),
    }
    domain = f"{JAVIS_ACTION_PARAMETERS_V1}\0".encode("ascii")
    return hashlib.sha256(domain + canonical_json_bytes(payload)).hexdigest()


class _WireContract:
    @classmethod
    def from_dict(cls: type[_ContractT], data: Mapping[str, Any]) -> _ContractT:
        if not isinstance(data, Mapping):
            raise ValueError(f"{cls.__name__}: wire value must be an object")
        expected = tuple(item.name for item in fields(cls))
        actual = set(data)
        missing = set(expected) - actual
        if missing:
            raise ValueError(f"{cls.__name__}: missing field(s): {', '.join(sorted(missing))}")
        unexpected = actual - set(expected)
        if unexpected:
            rendered = ", ".join(sorted(repr(item) for item in unexpected))
            raise ValueError(f"{cls.__name__}: unexpected field(s): {rendered}")
        return cls(**{name: data[name] for name in expected})

    def to_dict(self) -> dict[str, object]:
        return {item.name: _wire(getattr(self, item.name)) for item in fields(self)}

    def to_event_dict(self) -> dict[str, object]:
        """Return the deliberately narrow projection allowed on EventBus."""

        return {
            item.name: _wire(getattr(self, item.name))
            for item in fields(self)
            if item.name not in _EVENT_PRIVATE_FIELDS
        }

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def canonical_content_hash(self) -> str:
        return canonical_content_hash(self)


def _verify_content_hash(contract: _WireContract, *, maximum: int) -> None:
    supplied = _hash(getattr(contract, "content_hash"), "content_hash")
    calculated = canonical_content_hash(contract)
    if not hmac.compare_digest(supplied, calculated):
        raise _field_error("content_hash", "does not match canonical contract content")
    if len(canonical_json_bytes(contract)) > maximum:
        raise _field_error("contract", f"canonical JSON must not exceed {maximum} bytes")


@dataclass(frozen=True, slots=True)
class ActionRequestV1(_WireContract):
    schema_version: int
    action_request_id: str
    javis_identity_id: str
    instance_id: str
    intention_id: str
    intention_revision: int
    commitment_id: str | None
    owner_subject_id: str
    runtime_boot_id: str
    source_kind: ActionSourceKind
    source_ref: str
    action_name: str
    capability: str
    normalized_parameters: Mapping[str, Any] = field(repr=False)
    parameters_hash: str
    target_scope: Mapping[str, Any] = field(repr=False)
    effect_class: EffectClass
    risk_class: RiskClass
    preconditions: tuple[Mapping[str, Any], ...] = field(repr=False)
    expected_observations: tuple[Mapping[str, Any], ...] = field(repr=False)
    idempotency_key: str
    timeout_seconds: int
    requested_at_utc: str
    expires_at_utc: str
    policy_version: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for name in (
            "action_request_id",
            "javis_identity_id",
            "instance_id",
            "intention_id",
            "owner_subject_id",
            "runtime_boot_id",
            "idempotency_key",
        ):
            _id(getattr(self, name), name)
        _id(self.commitment_id, "commitment_id", optional=True)
        _integer(self.intention_revision, "intention_revision", 1, 2**63 - 1)
        object.__setattr__(
            self,
            "source_kind",
            _enum(self.source_kind, ActionSourceKind, "source_kind"),
        )
        object.__setattr__(
            self,
            "source_ref",
            _safe_text(self.source_ref, "source_ref", maximum=MAX_SOURCE_REF_BYTES),
        )
        object.__setattr__(self, "action_name", _code(self.action_name, "action_name"))
        object.__setattr__(self, "capability", _code(self.capability, "capability"))
        parameters = _safe_json_object(
            self.normalized_parameters,
            "normalized_parameters",
            maximum=MAX_PARAMETERS_BYTES,
        )
        target = _safe_json_object(
            self.target_scope,
            "target_scope",
            maximum=MAX_TARGET_SCOPE_BYTES,
        )
        object.__setattr__(self, "normalized_parameters", parameters)
        object.__setattr__(self, "target_scope", target)
        supplied = _hash(self.parameters_hash, "parameters_hash")
        calculated = action_parameters_hash(
            action_name=self.action_name,
            parameters=parameters,
            target_scope=target,
            intention_id=self.intention_id,
            intention_revision=self.intention_revision,
        )
        if not hmac.compare_digest(supplied, calculated):
            raise _field_error("parameters_hash", "does not match normalized action parameters")
        object.__setattr__(
            self,
            "effect_class",
            _enum(self.effect_class, EffectClass, "effect_class"),
        )
        object.__setattr__(self, "risk_class", _enum(self.risk_class, RiskClass, "risk_class"))
        object.__setattr__(
            self,
            "preconditions",
            _safe_json_tuple(
                self.preconditions,
                "preconditions",
                maximum_items=MAX_PREDICATES,
                maximum_bytes=MAX_OBSERVATION_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "expected_observations",
            _safe_json_tuple(
                self.expected_observations,
                "expected_observations",
                maximum_items=MAX_EXPECTED_OBSERVATIONS,
                maximum_bytes=MAX_OBSERVATION_BYTES,
            ),
        )
        _integer(self.timeout_seconds, "timeout_seconds", 1, 3600)
        requested = _timestamp(self.requested_at_utc, "requested_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        assert requested is not None and expires is not None
        if not requested < expires or (expires - requested).total_seconds() > 3600:
            raise _field_error("expires_at_utc", "must be within one hour after requested_at_utc")
        object.__setattr__(self, "policy_version", _code(self.policy_version, "policy_version"))

    @classmethod
    def from_client_dict(
        cls,
        data: Mapping[str, Any],
        *,
        owner_subject_id: str,
    ) -> "ActionRequestV1":
        """Parse a client request while injecting authoritative owner context."""

        if not isinstance(data, Mapping):
            raise ValueError("ActionRequestV1: client wire value must be an object")
        for key in data:
            if type(key) is not str:
                raise ValueError("ActionRequestV1: client field names must be strings")
            if _key_token(key) in _CLIENT_OWNER_TOKENS:
                raise ValueError("ActionRequestV1: owner is server-injected and forbidden on client wire")
        expected = {item.name for item in fields(cls)} - {"owner_subject_id"}
        actual = set(data)
        missing = expected - actual
        if missing:
            raise ValueError(f"ActionRequestV1: missing field(s): {', '.join(sorted(missing))}")
        unexpected = actual - expected
        if unexpected:
            rendered = ", ".join(sorted(repr(item) for item in unexpected))
            raise ValueError(f"ActionRequestV1: unexpected field(s): {rendered}")
        payload = dict(data)
        payload["owner_subject_id"] = _id(owner_subject_id, "owner_subject_id")
        return cls.from_dict(payload)


@dataclass(frozen=True, slots=True)
class AuthorizationGrantV1(_WireContract):
    schema_version: int
    grant_id: str
    grant_secret_digest: str = field(repr=False)
    approval_id: str | None
    policy_decision_id: str
    owner_subject_id: str
    client_instance_hash: str
    runtime_boot_id: str
    intention_id: str
    intention_revision: int
    action_name: str
    parameters_hash: str
    capability: str
    target_scope_hash: str
    risk_class: RiskClass
    issued_at_utc: str
    expires_at_utc: str
    max_uses: Literal[1]
    uses: int
    issuer_kind: GrantIssuerKind
    policy_version: str
    safety_revision: int
    state: GrantState

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for name in (
            "grant_id",
            "policy_decision_id",
            "owner_subject_id",
            "runtime_boot_id",
            "intention_id",
        ):
            _id(getattr(self, name), name)
        _id(self.approval_id, "approval_id", optional=True)
        for name in (
            "grant_secret_digest",
            "client_instance_hash",
            "parameters_hash",
            "target_scope_hash",
        ):
            _hash(getattr(self, name), name)
        _integer(self.intention_revision, "intention_revision", 1, 2**63 - 1)
        object.__setattr__(self, "action_name", _code(self.action_name, "action_name"))
        object.__setattr__(self, "capability", _code(self.capability, "capability"))
        object.__setattr__(self, "risk_class", _enum(self.risk_class, RiskClass, "risk_class"))
        issued = _timestamp(self.issued_at_utc, "issued_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        assert issued is not None and expires is not None
        maximum_ttl = 60 if self.risk_class in (RiskClass.HIGH, RiskClass.CRITICAL) else 300
        if not issued < expires or (expires - issued).total_seconds() > maximum_ttl:
            raise _field_error("expires_at_utc", f"must be within {maximum_ttl} seconds of issue")
        if type(self.max_uses) is not int or self.max_uses != 1:
            raise _field_error("max_uses", "must be literal integer 1")
        _integer(self.uses, "uses", 0, 1)
        object.__setattr__(
            self,
            "issuer_kind",
            _enum(self.issuer_kind, GrantIssuerKind, "issuer_kind"),
        )
        object.__setattr__(self, "policy_version", _code(self.policy_version, "policy_version"))
        _integer(self.safety_revision, "safety_revision", 1, 2**63 - 1)
        object.__setattr__(self, "state", _enum(self.state, GrantState, "state"))
        if self.state is GrantState.ISSUED and self.uses != 0:
            raise _field_error("uses", "issued grants must have zero uses")
        if self.state is GrantState.CONSUMED and self.uses != 1:
            raise _field_error("uses", "consumed grants must have exactly one use")
        if self.issuer_kind is GrantIssuerKind.POLICY and self.approval_id is not None:
            raise _field_error("approval_id", "must be null for policy grants")
        if self.issuer_kind is not GrantIssuerKind.POLICY and self.approval_id is None:
            raise _field_error("approval_id", "is required for approval-issued grants")


@dataclass(frozen=True, slots=True)
class ApprovalRequestV1(_WireContract):
    schema_version: int
    approval_id: str
    action_request_id: str
    action_name: str
    parameters_hash: str
    target_summary: str = field(repr=False)
    risk_class: RiskClass
    reversibility: EffectClass
    preview: Mapping[str, Any] = field(repr=False)
    preview_hash: str
    owner_subject_id: str
    client_instance_hash: str
    runtime_boot_id: str
    requested_at_utc: str
    expires_at_utc: str
    state: ApprovalState

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for name in (
            "approval_id",
            "action_request_id",
            "owner_subject_id",
            "runtime_boot_id",
        ):
            _id(getattr(self, name), name)
        object.__setattr__(self, "action_name", _code(self.action_name, "action_name"))
        for name in ("parameters_hash", "preview_hash", "client_instance_hash"):
            _hash(getattr(self, name), name)
        object.__setattr__(
            self,
            "target_summary",
            _safe_text(self.target_summary, "target_summary", maximum=MAX_TEXT_BYTES),
        )
        object.__setattr__(self, "risk_class", _enum(self.risk_class, RiskClass, "risk_class"))
        object.__setattr__(
            self,
            "reversibility",
            _enum(self.reversibility, EffectClass, "reversibility"),
        )
        preview = _safe_json_object(self.preview, "preview", maximum=MAX_PREVIEW_BYTES)
        object.__setattr__(self, "preview", preview)
        if not hmac.compare_digest(self.preview_hash, canonical_preview_hash(preview)):
            raise _field_error("preview_hash", "does not match canonical preview")
        requested = _timestamp(self.requested_at_utc, "requested_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        assert requested is not None and expires is not None
        if not requested < expires or (expires - requested).total_seconds() > 300:
            raise _field_error("expires_at_utc", "must be within five minutes after request")
        object.__setattr__(self, "state", _enum(self.state, ApprovalState, "state"))


@dataclass(frozen=True, slots=True)
class ApprovalResolutionV1(_WireContract):
    schema_version: int
    decision: ApprovalDecision
    approval_id: str
    action_request_id: str
    parameters_hash: str
    runtime_boot_id: str
    client_instance_hash: str
    decided_at_utc: str
    idempotency_key: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        object.__setattr__(
            self,
            "decision",
            _enum(self.decision, ApprovalDecision, "decision"),
        )
        for name in (
            "approval_id",
            "action_request_id",
            "runtime_boot_id",
            "idempotency_key",
        ):
            _id(getattr(self, name), name)
        _hash(self.parameters_hash, "parameters_hash")
        _hash(self.client_instance_hash, "client_instance_hash")
        _timestamp(self.decided_at_utc, "decided_at_utc")


@dataclass(frozen=True, slots=True)
class ActionReceiptV1(_WireContract):
    schema_version: int
    receipt_id: str
    chain_id: str
    chain_sequence: int
    previous_receipt_hash: str
    action_request_id: str
    intention_id: str
    intention_revision: int
    source_kind: ActionSourceKind
    source_ref: str
    action_name: str
    parameters_hash: str
    target_scope_hash: str
    authorization_decision_id: str | None
    grant_id_hash: str | None = field(repr=False)
    policy_version: str
    safety_revision: int
    runtime_boot_id: str
    phase: ActionPhase
    effect_observation: Mapping[str, Any] = field(repr=False)
    started_at_utc: str | None
    recorded_at_utc: str
    duration_ms: int | None
    reversibility: EffectClass
    recovery_material_ref: str | None = field(repr=False)
    error_code: str | None
    impact_summary: str | None = field(repr=False)
    content_hash: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for name in (
            "receipt_id",
            "chain_id",
            "action_request_id",
            "intention_id",
            "runtime_boot_id",
        ):
            _id(getattr(self, name), name)
        _integer(self.chain_sequence, "chain_sequence", 1, 2**63 - 1)
        _hash(self.previous_receipt_hash, "previous_receipt_hash")
        if self.chain_sequence == 1 and self.previous_receipt_hash != GENESIS_RECEIPT_HASH:
            raise _field_error("previous_receipt_hash", "genesis receipt must use the zero hash")
        _integer(self.intention_revision, "intention_revision", 1, 2**63 - 1)
        object.__setattr__(
            self,
            "source_kind",
            _enum(self.source_kind, ActionSourceKind, "source_kind"),
        )
        object.__setattr__(
            self,
            "source_ref",
            _safe_text(self.source_ref, "source_ref", maximum=MAX_SOURCE_REF_BYTES),
        )
        object.__setattr__(self, "action_name", _code(self.action_name, "action_name"))
        _hash(self.parameters_hash, "parameters_hash")
        _hash(self.target_scope_hash, "target_scope_hash")
        _id(self.authorization_decision_id, "authorization_decision_id", optional=True)
        _hash(self.grant_id_hash, "grant_id_hash", optional=True)
        object.__setattr__(self, "policy_version", _code(self.policy_version, "policy_version"))
        _integer(self.safety_revision, "safety_revision", 1, 2**63 - 1)
        object.__setattr__(self, "phase", _enum(self.phase, ActionPhase, "phase"))
        observation = _safe_json_object(
            self.effect_observation,
            "effect_observation",
            maximum=MAX_OBSERVATION_BYTES,
        )
        object.__setattr__(self, "effect_observation", observation)
        started = _timestamp(self.started_at_utc, "started_at_utc", optional=True)
        recorded = _timestamp(self.recorded_at_utc, "recorded_at_utc")
        assert recorded is not None
        if started is not None and started > recorded:
            raise _field_error("started_at_utc", "must not follow recorded_at_utc")
        if self.duration_ms is not None:
            _integer(self.duration_ms, "duration_ms", 0, 86_400_000)
        if started is None and self.duration_ms is not None:
            raise _field_error("duration_ms", "requires started_at_utc")
        object.__setattr__(
            self,
            "reversibility",
            _enum(self.reversibility, EffectClass, "reversibility"),
        )
        _id(self.recovery_material_ref, "recovery_material_ref", optional=True)
        object.__setattr__(self, "error_code", _code(self.error_code, "error_code", optional=True))
        object.__setattr__(
            self,
            "impact_summary",
            _safe_text(
                self.impact_summary,
                "impact_summary",
                maximum=MAX_TEXT_BYTES,
                optional=True,
            ),
        )
        if self.phase is ActionPhase.DENIED and self.grant_id_hash is not None:
            raise _field_error("grant_id_hash", "denied receipts must not claim a grant")
        _verify_content_hash(self, maximum=MAX_RECEIPT_BYTES)


@dataclass(frozen=True, slots=True)
class RecoveryReceiptV1(_WireContract):
    schema_version: int
    recovery_receipt_id: str
    action_request_id: str
    trigger_receipt_id: str
    runtime_boot_id: str
    recovery_attempt_id: str
    requested_by: RecoveryRequestedBy
    method: RecoveryMethod
    pre_recovery_observation_hash: str
    post_recovery_observation_hash: str
    recovery_action_request_id: str | None
    authorization_grant_id_hash: str | None = field(repr=False)
    result: RecoveryResult
    reexecution_performed: Literal[False]
    limitations: tuple[str, ...] = field(repr=False)
    recorded_at_utc: str
    content_hash: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for name in (
            "recovery_receipt_id",
            "action_request_id",
            "trigger_receipt_id",
            "runtime_boot_id",
            "recovery_attempt_id",
        ):
            _id(getattr(self, name), name)
        object.__setattr__(
            self,
            "requested_by",
            _enum(self.requested_by, RecoveryRequestedBy, "requested_by"),
        )
        object.__setattr__(self, "method", _enum(self.method, RecoveryMethod, "method"))
        _hash(self.pre_recovery_observation_hash, "pre_recovery_observation_hash")
        _hash(self.post_recovery_observation_hash, "post_recovery_observation_hash")
        _id(self.recovery_action_request_id, "recovery_action_request_id", optional=True)
        _hash(
            self.authorization_grant_id_hash,
            "authorization_grant_id_hash",
            optional=True,
        )
        object.__setattr__(self, "result", _enum(self.result, RecoveryResult, "result"))
        if type(self.reexecution_performed) is not bool or self.reexecution_performed is not False:
            raise _field_error("reexecution_performed", "must be literal false in v1")
        object.__setattr__(
            self,
            "limitations",
            _text_tuple(self.limitations, "limitations", maximum=MAX_LIMITATIONS),
        )
        _timestamp(self.recorded_at_utc, "recorded_at_utc")
        effectful = self.method in (RecoveryMethod.COMPENSATE, RecoveryMethod.RESTORE_SNAPSHOT)
        if effectful and (
            self.recovery_action_request_id is None
            or self.authorization_grant_id_hash is None
        ):
            raise _field_error(
                "recovery_action_request_id",
                "effectful recovery requires a new request and one-use grant hash",
            )
        if not effectful and (
            self.recovery_action_request_id is not None
            or self.authorization_grant_id_hash is not None
        ):
            raise _field_error(
                "recovery_action_request_id",
                "non-effectful recovery must not claim a recovery action or grant",
            )
        if self.recovery_action_request_id == self.action_request_id:
            raise _field_error("recovery_action_request_id", "must be a new ActionRequest")
        _verify_content_hash(self, maximum=MAX_RECEIPT_BYTES)


@dataclass(frozen=True, slots=True)
class SafetySnapshotV1(_WireContract):
    schema_version: int
    safety_snapshot_id: str
    javis_identity_id: str
    instance_id: str
    safety_revision: int
    runtime_boot_id: str
    mode: SafetyMode
    reason_codes: tuple[str, ...]
    effect_admission: EffectAdmission
    fuse_tripped: bool
    fuse_reason_code: str | None
    journal_health: JournalHealth
    receipt_chain_health: ReceiptChainHealth
    approval_authority_health: ApprovalAuthorityHealth
    uncertain_action_ids: tuple[str, ...]
    reconcile_cursor: str | None
    last_reconciled_at_utc: str | None
    permission_revision: int
    policy_version: str
    created_at_utc: str
    content_hash: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for name in (
            "safety_snapshot_id",
            "javis_identity_id",
            "instance_id",
            "runtime_boot_id",
        ):
            _id(getattr(self, name), name)
        _integer(self.safety_revision, "safety_revision", 1, 2**63 - 1)
        object.__setattr__(self, "mode", _enum(self.mode, SafetyMode, "mode"))
        object.__setattr__(
            self,
            "reason_codes",
            _code_tuple(
                self.reason_codes,
                "reason_codes",
                maximum=MAX_REASON_CODES,
                allowlist=SAFETY_REASON_CODES,
            ),
        )
        object.__setattr__(
            self,
            "effect_admission",
            _enum(self.effect_admission, EffectAdmission, "effect_admission"),
        )
        expected_admission = {
            SafetyMode.NORMAL: EffectAdmission.OPEN,
            SafetyMode.SAFE: EffectAdmission.CLOSED,
            SafetyMode.RECONCILING: EffectAdmission.CLOSED,
            SafetyMode.RECOVERY_ONLY: EffectAdmission.RECOVERY_ONLY,
            SafetyMode.SHUTDOWN: EffectAdmission.CLOSED,
        }[self.mode]
        if self.effect_admission is not expected_admission:
            raise _field_error("effect_admission", "does not match fail-closed safety mode")
        _boolean(self.fuse_tripped, "fuse_tripped")
        object.__setattr__(
            self,
            "fuse_reason_code",
            _code(self.fuse_reason_code, "fuse_reason_code", optional=True),
        )
        if self.fuse_tripped != (self.fuse_reason_code is not None):
            raise _field_error("fuse_reason_code", "must be present exactly when fuse is tripped")
        object.__setattr__(
            self,
            "journal_health",
            _enum(self.journal_health, JournalHealth, "journal_health"),
        )
        object.__setattr__(
            self,
            "receipt_chain_health",
            _enum(self.receipt_chain_health, ReceiptChainHealth, "receipt_chain_health"),
        )
        object.__setattr__(
            self,
            "approval_authority_health",
            _enum(
                self.approval_authority_health,
                ApprovalAuthorityHealth,
                "approval_authority_health",
            ),
        )
        object.__setattr__(
            self,
            "uncertain_action_ids",
            _id_tuple(
                self.uncertain_action_ids,
                "uncertain_action_ids",
                maximum=MAX_UNCERTAIN_ACTIONS,
            ),
        )
        object.__setattr__(
            self,
            "reconcile_cursor",
            _id(self.reconcile_cursor, "reconcile_cursor", optional=True),
        )
        _timestamp(self.last_reconciled_at_utc, "last_reconciled_at_utc", optional=True)
        _integer(self.permission_revision, "permission_revision", 0, 2**63 - 1)
        object.__setattr__(self, "policy_version", _code(self.policy_version, "policy_version"))
        _timestamp(self.created_at_utc, "created_at_utc")
        all_healthy = (
            self.journal_health is JournalHealth.HEALTHY
            and self.receipt_chain_health is ReceiptChainHealth.HEALTHY
            and self.approval_authority_health is ApprovalAuthorityHealth.HEALTHY
        )
        if self.mode is SafetyMode.NORMAL and (
            not all_healthy
            or self.fuse_tripped
            or self.uncertain_action_ids
            or self.reason_codes
        ):
            raise _field_error("mode", "normal/open requires healthy authorities and no active reasons")
        if self.mode is not SafetyMode.NORMAL and not self.reason_codes:
            raise _field_error("reason_codes", "closed modes require at least one stable reason")
        if self.mode is SafetyMode.RECONCILING and self.reconcile_cursor is None:
            raise _field_error("reconcile_cursor", "is required while reconciling")
        _verify_content_hash(self, maximum=MAX_RECEIPT_BYTES)


ActionEffectClass = EffectClass
ActionRiskClass = RiskClass
ReceiptPhase = ActionPhase
ActionLifecycleState = ActionState


__all__ = [
    "ActionEffectClass",
    "ActionLifecycleState",
    "ActionPhase",
    "ActionReceiptV1",
    "ActionRequestV1",
    "ActionRiskClass",
    "ActionSourceKind",
    "ActionState",
    "ApprovalAuthorityHealth",
    "ApprovalDecision",
    "ApprovalRequestV1",
    "ApprovalResolutionV1",
    "ApprovalState",
    "AuthorizationGrantV1",
    "EffectAdmission",
    "EffectClass",
    "GENESIS_RECEIPT_HASH",
    "GrantIssuerKind",
    "GrantState",
    "JAVIS_ACTION_PARAMETERS_V1",
    "JAVIS_CANONICAL_JSON_V1",
    "JournalHealth",
    "MAX_PREVIEW_BYTES",
    "MAX_RECEIPT_BYTES",
    "MAX_TARGET_SCOPE_BYTES",
    "ReceiptChainHealth",
    "ReceiptPhase",
    "RecoveryMethod",
    "RecoveryReceiptV1",
    "RecoveryRequestedBy",
    "RecoveryResult",
    "RiskClass",
    "SAFETY_REASON_CODES",
    "SCHEMA_VERSION",
    "SafetyMode",
    "SafetySnapshotV1",
    "action_parameters_hash",
    "canonical_content_hash",
    "canonical_json_bytes",
    "canonical_preview_hash",
    "canonical_target_scope_hash",
]
