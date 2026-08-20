"""Strict, immutable wire contracts for the governed computer twin.

This module is deliberately limited to deterministic value validation. It has
no model, DOM, hardware, storage, grant-store, or runtime dependencies.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Collection, Mapping
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeVar

from core.life.contracts import PrivacyClass, RetentionClass


SCHEMA_VERSION = 1
MAX_ID_CHARS = 256
MAX_CODE_CHARS = 96
MAX_ATTRIBUTE_KEY_CHARS = 64
MAX_ATTRIBUTE_STRING_CHARS = 256
MAX_ATTRIBUTE_ARRAY_ITEMS = 16
MAX_ATTRIBUTES = 24
MAX_ATTRIBUTES_BYTES = 4_096
MAX_PROVENANCE_FIELDS = 16
MAX_PROVENANCE_BYTES = 2_048
MAX_OBSERVATION_BYTES = 8_192
MAX_FACT_BYTES = 4_096
MAX_FACTS = 256
MAX_STALE_KEYS = 512
MAX_SNAPSHOT_BYTES = 65_536
MAX_SNAPSHOT_TTL_SECONDS = 300
MAX_FUTURE_SKEW_SECONDS = 5
OCR_MAX_RETENTION_SECONDS = 10
RAW_MEDIA_RETENTION_SECONDS = 0

_RFC3339_MILLISECONDS = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[a-zA-Z]:[\\/]")
_UNC_PATH = re.compile(r"^(?:\\\\|//)[^\\/]+[\\/][^\\/]+")
_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S{8,}|(?:api[_-]?key|password|secret|token|credential)"
    r"\s*[:=]\s*\S+)"
)
_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "absolute_path",
        "api_key",
        "argv",
        "audio_bytes",
        "audio_data",
        "authorization",
        "bearer_token",
        "camera_frame",
        "cmdline",
        "command_line",
        "credential",
        "document_content",
        "document_text",
        "file_content",
        "frame_buffer",
        "full_ocr_text",
        "full_text",
        "image_bytes",
        "image_data",
        "media_bytes",
        "ocr_content",
        "ocr_full_text",
        "ocr_text",
        "ocr_transcript",
        "password",
        "pixel",
        "pixel_buffer",
        "pixel_data",
        "pixels",
        "process_args",
        "process_command_line",
        "raw_audio",
        "raw_frame",
        "raw_image",
        "raw_media",
        "raw_video",
        "screen_capture",
        "screenshot",
        "secret",
        "secrets",
        "token",
        "video_bytes",
        "video_data",
        "window_title",
    }
)

_Contract = TypeVar("_Contract", bound="_WireContract")
AttributeValue = str | int | float | bool | tuple[str | int | float | bool, ...]
FactKey = tuple[str, str, str]


class SourceKind(str, Enum):
    FOREGROUND_APP = "foreground_app"
    PROCESS_HEALTH = "process_health"
    DEVICE_HEALTH = "device_health"
    WORKSPACE_METADATA = "workspace_metadata"
    SCREEN_OCR = "screen_ocr"
    SCREEN_OBJECT = "screen_object"
    USER_DEFINED_ALIAS = "user_defined_alias"
    RAW_MEDIA = "raw_media"


class SourceHealth(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class EnvironmentStatus(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


# L4 retains the L0 privacy/retention vocabulary instead of creating a second
# incompatible taxonomy. These aliases make the environment boundary explicit.
EnvironmentSourceKind = SourceKind
EnvironmentPrivacyClass = PrivacyClass
EnvironmentRetentionClass = RetentionClass
EnvironmentSourceHealth = SourceHealth
EnvironmentFactStatus = EnvironmentStatus
FactStatus = EnvironmentStatus
HealthStatus = SourceHealth


SOURCE_MAX_TTL_SECONDS: Mapping[SourceKind, int | None] = MappingProxyType(
    {
        SourceKind.FOREGROUND_APP: 5,
        SourceKind.PROCESS_HEALTH: 30,
        SourceKind.DEVICE_HEALTH: 300,
        SourceKind.WORKSPACE_METADATA: 60,
        SourceKind.SCREEN_OCR: OCR_MAX_RETENTION_SECONDS,
        SourceKind.SCREEN_OBJECT: OCR_MAX_RETENTION_SECONDS,
        SourceKind.USER_DEFINED_ALIAS: None,
        SourceKind.RAW_MEDIA: RAW_MEDIA_RETENTION_SECONDS,
    }
)

SOURCE_RETENTION_CLASS: Mapping[SourceKind, RetentionClass] = MappingProxyType(
    {
        SourceKind.FOREGROUND_APP: RetentionClass.SESSION,
        SourceKind.PROCESS_HEALTH: RetentionClass.OPERATIONAL,
        SourceKind.DEVICE_HEALTH: RetentionClass.OPERATIONAL,
        SourceKind.WORKSPACE_METADATA: RetentionClass.SESSION,
        SourceKind.SCREEN_OCR: RetentionClass.NEVER_PERSIST,
        SourceKind.SCREEN_OBJECT: RetentionClass.NEVER_PERSIST,
        SourceKind.USER_DEFINED_ALIAS: RetentionClass.CONTINUITY,
        SourceKind.RAW_MEDIA: RetentionClass.NEVER_PERSIST,
    }
)


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
    allow_empty: bool = False,
) -> str:
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


def _id(value: Any, field_name: str) -> str:
    return _string(value, field_name, max_chars=MAX_ID_CHARS)


def _code(value: Any, field_name: str, *, max_chars: int = MAX_CODE_CHARS) -> str:
    text = _string(value, field_name, max_chars=max_chars)
    if _CODE.fullmatch(text) is None:
        raise _field_error(field_name, "must be a lowercase machine code")
    return text


def _non_negative_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise _field_error(field_name, "must be a non-negative integer")
    return value


def _unit(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _field_error(field_name, "must be a finite number in [0.0, 1.0]")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise _field_error(field_name, "must be a finite number in [0.0, 1.0]")
    return result


def _enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise _field_error(field_name, f"must be a {enum_type.__name__} wire string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _field_error(
            field_name, f"unknown {enum_type.__name__} value {value!r}"
        ) from exc


def _hash(value: Any, field_name: str) -> str:
    if type(value) is not str or _SHA256_HEX.fullmatch(value) is None:
        raise _field_error(field_name, "must be lowercase SHA-256 hex")
    if value == "0" * 64:
        raise _field_error(field_name, "must identify an issued grant")
    return value


def _timestamp(value: Any, field_name: str) -> tuple[str, datetime]:
    if type(value) is not str or _RFC3339_MILLISECONDS.fullmatch(value) is None:
        raise _field_error(field_name, "must be RFC3339 UTC with millisecond precision")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise _field_error(field_name, "must be a valid UTC timestamp") from exc
    return value, parsed


def _reference_time(value: str | datetime, field_name: str) -> datetime:
    if isinstance(value, str):
        return _timestamp(value, field_name)[1]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise _field_error(field_name, "must be an aware UTC datetime or canonical timestamp")
    if value.utcoffset() != timedelta(0):
        raise _field_error(field_name, "must be UTC")
    return value.astimezone(timezone.utc)


def _is_absolute_path(value: str) -> bool:
    stripped = value.strip()
    return bool(
        stripped.startswith(("/", "file://", "~/", "~\\"))
        or _WINDOWS_ABSOLUTE_PATH.match(stripped)
        or _UNC_PATH.match(stripped)
    )


def _normalized_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _reject_sensitive_field_name(value: str, field_name: str) -> None:
    normalized = _normalized_field_name(value)
    tokens = frozenset(part for part in normalized.split("_") if part)
    if normalized in _FORBIDDEN_FIELD_NAMES or tokens.intersection(
        {"password", "secret", "secrets", "credential", "authorization", "pixels"}
    ):
        raise _field_error(field_name, f"sensitive field {value!r} is forbidden")
    if "ocr" in tokens and tokens.intersection({"content", "full", "text", "transcript"}):
        raise _field_error(field_name, f"OCR full-text field {value!r} is forbidden")
    if "command" in tokens and "line" in tokens:
        raise _field_error(field_name, f"command-line field {value!r} is forbidden")
    if "raw" in tokens and tokens.intersection(
        {"audio", "frame", "image", "media", "video"}
    ):
        raise _field_error(field_name, f"raw-media field {value!r} is forbidden")


def _safe_text(value: Any, field_name: str, *, max_chars: int) -> str:
    text = _string(value, field_name, max_chars=max_chars, allow_empty=True)
    if _is_absolute_path(text):
        raise _field_error(field_name, "absolute filesystem paths are forbidden")
    if _SECRET_VALUE.search(text):
        raise _field_error(field_name, "secret-bearing text is forbidden")
    return text


def _attribute_scalar(value: Any, field_name: str) -> str | int | float | bool:
    if type(value) is str:
        return _safe_text(value, field_name, max_chars=MAX_ATTRIBUTE_STRING_CHARS)
    if type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise _field_error(field_name, "must be a finite JSON scalar")


def _attribute_value(value: Any, field_name: str) -> AttributeValue:
    if isinstance(value, (list, tuple)):
        if not value:
            raise _field_error(field_name, "arrays must be non-empty")
        if len(value) > MAX_ATTRIBUTE_ARRAY_ITEMS:
            raise _field_error(
                field_name,
                f"arrays must contain at most {MAX_ATTRIBUTE_ARRAY_ITEMS} scalars",
            )
        return tuple(
            _attribute_scalar(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    return _attribute_scalar(value, field_name)


def _attributes(value: Any, field_name: str = "attributes") -> Mapping[str, AttributeValue]:
    if not isinstance(value, Mapping):
        raise _field_error(field_name, "must be a JSON object")
    if not value:
        raise _field_error(field_name, "must contain at least one attribute")
    if len(value) > MAX_ATTRIBUTES:
        raise _field_error(field_name, f"must contain at most {MAX_ATTRIBUTES} attributes")
    normalized: dict[str, AttributeValue] = {}
    for key, item in value.items():
        if type(key) is not str:
            raise _field_error(field_name, "attribute keys must be strings")
        attribute = _code(key, f"{field_name}.key", max_chars=MAX_ATTRIBUTE_KEY_CHARS)
        _reject_sensitive_field_name(attribute, f"{field_name}.{attribute}")
        normalized[attribute] = _attribute_value(item, f"{field_name}.{attribute}")
    frozen = MappingProxyType(dict(sorted(normalized.items())))
    if len(canonical_json_bytes(frozen)) > MAX_ATTRIBUTES_BYTES:
        raise _field_error(
            field_name, f"canonical JSON must be at most {MAX_ATTRIBUTES_BYTES} bytes"
        )
    return frozen


def _freeze_metadata(value: Any, field_name: str, *, depth: int = 0) -> Any:
    if depth > 3:
        raise _field_error(field_name, "must have at most three nested levels")
    if isinstance(value, Mapping):
        if len(value) > MAX_PROVENANCE_FIELDS:
            raise _field_error(
                field_name, f"must contain at most {MAX_PROVENANCE_FIELDS} fields"
            )
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise _field_error(field_name, "object keys must be strings")
            safe_key = _code(key, f"{field_name}.key")
            _reject_sensitive_field_name(safe_key, f"{field_name}.{safe_key}")
            normalized[safe_key] = _freeze_metadata(
                item, f"{field_name}.{safe_key}", depth=depth + 1
            )
        return MappingProxyType(dict(sorted(normalized.items())))
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ATTRIBUTE_ARRAY_ITEMS:
            raise _field_error(
                field_name, f"must contain at most {MAX_ATTRIBUTE_ARRAY_ITEMS} values"
            )
        return tuple(
            _freeze_metadata(item, f"{field_name}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        )
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _field_error(field_name, "numbers must be finite")
        return value
    if type(value) is str:
        return _safe_text(value, field_name, max_chars=MAX_ATTRIBUTE_STRING_CHARS)
    raise _field_error(field_name, f"unsupported JSON value {type(value).__name__}")


def _provenance(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise _field_error("provenance", "must be a non-empty JSON object")
    frozen = _freeze_metadata(value, "provenance")
    if len(canonical_json_bytes(frozen)) > MAX_PROVENANCE_BYTES:
        raise _field_error(
            "provenance", f"canonical JSON must be at most {MAX_PROVENANCE_BYTES} bytes"
        )
    return frozen


def _wire(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, _WireContract):
        return value.to_dict()
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            wire_key = key.value if isinstance(key, Enum) else key
            if type(wire_key) is not str:
                raise ValueError("canonical JSON object keys must be strings")
            normalized[wire_key] = _wire(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def canonical_json_bytes(value: Mapping[str, Any] | "_WireContract") -> bytes:
    """Return compact, sorted UTF-8 JSON for hashing and size accounting."""

    if isinstance(value, _WireContract):
        payload = value.to_dict()
    elif isinstance(value, Mapping):
        payload = _wire(value)
    else:
        raise ValueError("canonical JSON root must be an environment contract or object")
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("canonical JSON contains an unsupported value") from exc


def canonical_hash(value: Mapping[str, Any] | "_WireContract") -> str:
    """Return lowercase SHA-256 over :func:`canonical_json_bytes`."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class _WireContract:
    @classmethod
    def from_dict(cls: type[_Contract], data: Mapping[str, Any]) -> _Contract:
        if not isinstance(data, Mapping):
            raise ValueError(f"{cls.__name__}: wire value must be an object")
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

    def to_dict(self) -> dict[str, Any]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def canonical_hash(self) -> str:
        return canonical_hash(self)


def _source_policy(
    *,
    source_kind: SourceKind,
    privacy_class: PrivacyClass,
    retention_class: RetentionClass,
    observed_at: datetime,
    valid_until: datetime,
) -> None:
    if source_kind is SourceKind.RAW_MEDIA:
        raise _field_error(
            "source_kind", "raw media is pipeline-only and cannot enter the fact contract"
        )
    if privacy_class in {PrivacyClass.SECRET, PrivacyClass.BIOMETRIC}:
        raise _field_error(
            "privacy_class", "secret and biometric values cannot enter environment facts"
        )
    if retention_class is RetentionClass.MEMORY_CANDIDATE:
        raise _field_error(
            "retention_class", "environment facts cannot become memory candidates"
        )
    required_retention = SOURCE_RETENTION_CLASS[source_kind]
    if retention_class is not required_retention:
        raise _field_error(
            "retention_class",
            f"{source_kind.value} requires {required_retention.value}",
        )
    if valid_until <= observed_at:
        raise _field_error("valid_until_utc", "must be later than observed_at_utc")
    maximum_ttl = SOURCE_MAX_TTL_SECONDS[source_kind]
    if maximum_ttl is not None and valid_until - observed_at > timedelta(seconds=maximum_ttl):
        raise _field_error(
            "valid_until_utc",
            f"{source_kind.value} TTL must be at most {maximum_ttl} seconds",
        )


def _validate_source_attributes(
    source_kind: SourceKind, attributes: Collection[str]
) -> None:
    if source_kind is not SourceKind.SCREEN_OCR:
        return
    for attribute in attributes:
        tokens = frozenset(_normalized_field_name(attribute).split("_"))
        if tokens.intersection({"content", "line", "lines", "text", "transcript", "words"}):
            raise _field_error(
                f"attributes.{attribute}",
                "screen OCR may contain only minimized facts, never recognized text",
            )


@dataclass(frozen=True)
class EnvironmentObservationV1(_WireContract):
    schema_version: int
    observation_id: str
    runtime_boot_id: str
    source_event_id: str
    source_kind: SourceKind
    subject_kind: str
    subject_key: str
    attributes: Mapping[str, AttributeValue]
    observed_at_utc: str
    valid_until_utc: str
    sequence: int
    confidence: float
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    grant_id_hash: str
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "observation_id",
            "runtime_boot_id",
            "source_event_id",
            "subject_key",
        ):
            _id(getattr(self, field_name), field_name)
            if _is_absolute_path(getattr(self, field_name)):
                raise _field_error(field_name, "absolute filesystem paths are forbidden")
        object.__setattr__(
            self, "source_kind", _enum(self.source_kind, SourceKind, "source_kind")
        )
        _code(self.subject_kind, "subject_kind")
        object.__setattr__(self, "attributes", _attributes(self.attributes))
        _, observed_at = _timestamp(self.observed_at_utc, "observed_at_utc")
        _, valid_until = _timestamp(self.valid_until_utc, "valid_until_utc")
        _non_negative_int(self.sequence, "sequence")
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(
            self,
            "privacy_class",
            _enum(self.privacy_class, PrivacyClass, "privacy_class"),
        )
        object.__setattr__(
            self,
            "retention_class",
            _enum(self.retention_class, RetentionClass, "retention_class"),
        )
        _hash(self.grant_id_hash, "grant_id_hash")
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        _validate_source_attributes(self.source_kind, self.attributes)
        _source_policy(
            source_kind=self.source_kind,
            privacy_class=self.privacy_class,
            retention_class=self.retention_class,
            observed_at=observed_at,
            valid_until=valid_until,
        )
        if len(canonical_json_bytes(self)) > MAX_OBSERVATION_BYTES:
            raise _field_error(
                "observation", f"canonical wire size must be at most {MAX_OBSERVATION_BYTES} bytes"
            )

    def to_facts(self) -> tuple["EnvironmentFact", ...]:
        return tuple(
            EnvironmentFact.from_observation(self, attribute)
            for attribute in sorted(self.attributes)
        )


@dataclass(frozen=True)
class EnvironmentFact(_WireContract):
    runtime_boot_id: str
    observation_id: str
    source_event_id: str
    source_kind: SourceKind
    subject_kind: str
    subject_key: str
    attribute: str
    value: AttributeValue
    observed_at_utc: str
    valid_until_utc: str
    sequence: int
    confidence: float
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    grant_id_hash: str
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field_name in (
            "runtime_boot_id",
            "observation_id",
            "source_event_id",
            "subject_key",
        ):
            _id(getattr(self, field_name), field_name)
            if _is_absolute_path(getattr(self, field_name)):
                raise _field_error(field_name, "absolute filesystem paths are forbidden")
        object.__setattr__(
            self, "source_kind", _enum(self.source_kind, SourceKind, "source_kind")
        )
        _code(self.subject_kind, "subject_kind")
        _code(self.attribute, "attribute", max_chars=MAX_ATTRIBUTE_KEY_CHARS)
        _reject_sensitive_field_name(self.attribute, "attribute")
        object.__setattr__(self, "value", _attribute_value(self.value, "value"))
        _, observed_at = _timestamp(self.observed_at_utc, "observed_at_utc")
        _, valid_until = _timestamp(self.valid_until_utc, "valid_until_utc")
        _non_negative_int(self.sequence, "sequence")
        object.__setattr__(self, "confidence", _unit(self.confidence, "confidence"))
        object.__setattr__(
            self,
            "privacy_class",
            _enum(self.privacy_class, PrivacyClass, "privacy_class"),
        )
        object.__setattr__(
            self,
            "retention_class",
            _enum(self.retention_class, RetentionClass, "retention_class"),
        )
        _hash(self.grant_id_hash, "grant_id_hash")
        object.__setattr__(self, "provenance", _provenance(self.provenance))
        _validate_source_attributes(self.source_kind, (self.attribute,))
        _source_policy(
            source_kind=self.source_kind,
            privacy_class=self.privacy_class,
            retention_class=self.retention_class,
            observed_at=observed_at,
            valid_until=valid_until,
        )
        if len(canonical_json_bytes(self)) > MAX_FACT_BYTES:
            raise _field_error(
                "fact", f"canonical wire size must be at most {MAX_FACT_BYTES} bytes"
            )

    @property
    def key(self) -> FactKey:
        return (self.subject_kind, self.subject_key, self.attribute)

    @classmethod
    def from_observation(
        cls, observation: EnvironmentObservationV1, attribute: str
    ) -> "EnvironmentFact":
        if not isinstance(observation, EnvironmentObservationV1):
            raise _field_error("observation", "must be an EnvironmentObservationV1")
        if attribute not in observation.attributes:
            raise _field_error("attribute", "must exist in observation.attributes")
        return cls(
            runtime_boot_id=observation.runtime_boot_id,
            observation_id=observation.observation_id,
            source_event_id=observation.source_event_id,
            source_kind=observation.source_kind,
            subject_kind=observation.subject_kind,
            subject_key=observation.subject_key,
            attribute=attribute,
            value=observation.attributes[attribute],
            observed_at_utc=observation.observed_at_utc,
            valid_until_utc=observation.valid_until_utc,
            sequence=observation.sequence,
            confidence=observation.confidence,
            privacy_class=observation.privacy_class,
            retention_class=observation.retention_class,
            grant_id_hash=observation.grant_id_hash,
            provenance=observation.provenance,
        )


def _fact(value: Any, field_name: str) -> EnvironmentFact:
    if isinstance(value, EnvironmentFact):
        return value
    if isinstance(value, Mapping):
        try:
            return EnvironmentFact.from_dict(value)
        except ValueError as exc:
            raise _field_error(field_name, str(exc)) from exc
    raise _field_error(field_name, "must be an EnvironmentFact")


def _fact_key(value: Any, field_name: str) -> FactKey:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _field_error(
            field_name, "must be [subject_kind, subject_key, attribute]"
        )
    subject_kind = _code(value[0], f"{field_name}[0]")
    subject_key = _id(value[1], f"{field_name}[1]")
    if _is_absolute_path(subject_key):
        raise _field_error(field_name, "absolute filesystem paths are forbidden")
    attribute = _code(
        value[2], f"{field_name}[2]", max_chars=MAX_ATTRIBUTE_KEY_CHARS
    )
    _reject_sensitive_field_name(attribute, f"{field_name}[2]")
    return (subject_kind, subject_key, attribute)


@dataclass(frozen=True)
class EnvironmentSnapshotV1(_WireContract):
    schema_version: int
    revision: int
    runtime_boot_id: str
    generated_at_utc: str
    expires_at_utc: str
    facts: tuple[EnvironmentFact, ...]
    stale_keys: tuple[FactKey, ...]
    source_health: Mapping[str, SourceHealth]
    permission_revision: int

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _non_negative_int(self.revision, "revision")
        _id(self.runtime_boot_id, "runtime_boot_id")
        _, generated_at = _timestamp(self.generated_at_utc, "generated_at_utc")
        _, expires_at = _timestamp(self.expires_at_utc, "expires_at_utc")
        if expires_at <= generated_at:
            raise _field_error("expires_at_utc", "must be later than generated_at_utc")
        if expires_at - generated_at > timedelta(seconds=MAX_SNAPSHOT_TTL_SECONDS):
            raise _field_error(
                "expires_at_utc",
                f"snapshot TTL must be at most {MAX_SNAPSHOT_TTL_SECONDS} seconds",
            )
        if not isinstance(self.facts, (list, tuple)) or len(self.facts) > MAX_FACTS:
            raise _field_error("facts", f"must contain at most {MAX_FACTS} facts")
        facts = tuple(
            _fact(value, f"facts[{index}]") for index, value in enumerate(self.facts)
        )
        facts = tuple(sorted(facts, key=lambda item: item.key))
        fact_keys = tuple(item.key for item in facts)
        if len(set(fact_keys)) != len(fact_keys):
            raise _field_error("facts", "must contain unique fact keys")
        for fact in facts:
            if fact.runtime_boot_id != self.runtime_boot_id:
                raise _field_error("facts", "fact runtime boot must match snapshot")
            fact_observed_at = _timestamp(fact.observed_at_utc, "fact.observed_at_utc")[1]
            fact_valid_until = _timestamp(
                fact.valid_until_utc, "fact.valid_until_utc"
            )[1]
            if fact_observed_at > generated_at:
                raise _field_error("facts", "fact cannot be observed after snapshot generation")
            if fact_valid_until <= generated_at:
                raise _field_error("facts", "expired facts cannot enter a snapshot")
            if expires_at > fact_valid_until:
                raise _field_error("expires_at_utc", "snapshot cannot outlive any fact")
        object.__setattr__(self, "facts", facts)

        if not isinstance(self.stale_keys, (list, tuple)) or len(self.stale_keys) > MAX_STALE_KEYS:
            raise _field_error(
                "stale_keys", f"must contain at most {MAX_STALE_KEYS} fact keys"
            )
        stale_keys = tuple(
            sorted(
                _fact_key(value, f"stale_keys[{index}]")
                for index, value in enumerate(self.stale_keys)
            )
        )
        if len(set(stale_keys)) != len(stale_keys):
            raise _field_error("stale_keys", "must contain unique fact keys")
        if set(stale_keys).intersection(fact_keys):
            raise _field_error("stale_keys", "fresh facts cannot also be stale")
        object.__setattr__(self, "stale_keys", stale_keys)

        if not isinstance(self.source_health, Mapping):
            raise _field_error("source_health", "must be a JSON object")
        if len(self.source_health) > len(SourceKind):
            raise _field_error("source_health", "contains too many sources")
        source_health: dict[str, SourceHealth] = {}
        for key, value in self.source_health.items():
            source = _enum(key, SourceKind, "source_health.key")
            health = _enum(value, SourceHealth, f"source_health.{source.value}")
            source_health[source.value] = health
        object.__setattr__(
            self, "source_health", MappingProxyType(dict(sorted(source_health.items())))
        )
        _non_negative_int(self.permission_revision, "permission_revision")
        if len(canonical_json_bytes(self)) > MAX_SNAPSHOT_BYTES:
            raise _field_error(
                "snapshot", f"canonical wire size must be at most {MAX_SNAPSHOT_BYTES} bytes"
            )

    def status_for(
        self, subject_kind: str, subject_key: str, attribute: str
    ) -> EnvironmentStatus:
        key = _fact_key((subject_kind, subject_key, attribute), "key")
        if any(fact.key == key for fact in self.facts):
            return EnvironmentStatus.FRESH
        if key in self.stale_keys:
            return EnvironmentStatus.STALE
        return EnvironmentStatus.UNKNOWN


def _strict_json_object(value: str | bytes, *, maximum_bytes: int) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("wire JSON must be valid UTF-8") from exc
    elif isinstance(value, bytes):
        raw = value
    else:
        raise ValueError("wire value must be a JSON object, string, or bytes")
    if len(raw) > maximum_bytes:
        raise ValueError(f"wire JSON must be at most {maximum_bytes} bytes")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON field {key!r}")
            result[key] = item
        return result

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"invalid JSON number {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("wire value must be valid UTF-8 JSON") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("wire JSON root must be an object")
    return parsed


def _wire_mapping(
    value: Mapping[str, Any] | str | bytes, *, maximum_bytes: int
) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return _strict_json_object(value, maximum_bytes=maximum_bytes)


def validate_observation_for_reducer(
    observation: EnvironmentObservationV1,
    *,
    expected_runtime_boot_id: str,
    previous_sequence: int | None,
    received_at_utc: str | datetime,
    seen_observation_ids: Collection[str] = (),
    seen_source_event_ids: Collection[str] = (),
) -> EnvironmentObservationV1:
    """Validate stateful observation semantics without reading mutable state.

    Sequence, not wall-clock order, decides recency. The explicit receive time is
    used only to fail closed on implausible future or already-expired input.
    """

    if not isinstance(observation, EnvironmentObservationV1):
        raise _field_error("observation", "must be an EnvironmentObservationV1")
    _id(expected_runtime_boot_id, "expected_runtime_boot_id")
    if observation.runtime_boot_id != expected_runtime_boot_id:
        raise _field_error("runtime_boot_id", "does not match the active runtime boot")
    if previous_sequence is not None:
        _non_negative_int(previous_sequence, "previous_sequence")
        if observation.sequence <= previous_sequence:
            raise _field_error("sequence", "must strictly increase within a runtime boot")
    if observation.observation_id in seen_observation_ids:
        raise _field_error("observation_id", "duplicate observation ID")
    if observation.source_event_id in seen_source_event_ids:
        raise _field_error("source_event_id", "duplicate source event ID")
    received_at = _reference_time(received_at_utc, "received_at_utc")
    observed_at = _timestamp(observation.observed_at_utc, "observed_at_utc")[1]
    valid_until = _timestamp(observation.valid_until_utc, "valid_until_utc")[1]
    if observed_at > received_at + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
        raise _field_error(
            "observed_at_utc",
            f"must not be more than {MAX_FUTURE_SKEW_SECONDS} seconds in the future",
        )
    if valid_until <= received_at:
        raise _field_error("valid_until_utc", "observation is already expired")
    return observation


def parse_environment_observation_wire(
    value: Mapping[str, Any] | str | bytes,
    *,
    received_at_utc: str | datetime,
    expected_runtime_boot_id: str | None = None,
    previous_sequence: int | None = None,
    seen_observation_ids: Collection[str] = (),
    seen_source_event_ids: Collection[str] = (),
) -> EnvironmentObservationV1:
    """Parse strict wire input and, when supplied, apply reducer semantics."""

    data = _wire_mapping(value, maximum_bytes=MAX_OBSERVATION_BYTES)
    observation = EnvironmentObservationV1.from_dict(data)
    expected_boot = expected_runtime_boot_id or observation.runtime_boot_id
    return validate_observation_for_reducer(
        observation,
        expected_runtime_boot_id=expected_boot,
        previous_sequence=previous_sequence,
        received_at_utc=received_at_utc,
        seen_observation_ids=seen_observation_ids,
        seen_source_event_ids=seen_source_event_ids,
    )


def parse_environment_fact_wire(
    value: Mapping[str, Any] | str | bytes,
) -> EnvironmentFact:
    return EnvironmentFact.from_dict(
        _wire_mapping(value, maximum_bytes=MAX_FACT_BYTES)
    )


def parse_environment_snapshot_wire(
    value: Mapping[str, Any] | str | bytes,
    *,
    received_at_utc: str | datetime,
    expected_runtime_boot_id: str | None = None,
    previous_revision: int | None = None,
) -> EnvironmentSnapshotV1:
    data = _wire_mapping(value, maximum_bytes=MAX_SNAPSHOT_BYTES)
    snapshot = EnvironmentSnapshotV1.from_dict(data)
    if expected_runtime_boot_id is not None and snapshot.runtime_boot_id != expected_runtime_boot_id:
        raise _field_error("runtime_boot_id", "does not match the active runtime boot")
    if previous_revision is not None:
        _non_negative_int(previous_revision, "previous_revision")
        if snapshot.revision <= previous_revision:
            raise _field_error("revision", "must strictly increase within a runtime boot")
    received_at = _reference_time(received_at_utc, "received_at_utc")
    generated_at = _timestamp(snapshot.generated_at_utc, "generated_at_utc")[1]
    expires_at = _timestamp(snapshot.expires_at_utc, "expires_at_utc")[1]
    if generated_at > received_at + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
        raise _field_error(
            "generated_at_utc",
            f"must not be more than {MAX_FUTURE_SKEW_SECONDS} seconds in the future",
        )
    if expires_at <= received_at:
        raise _field_error("expires_at_utc", "snapshot is already expired")
    return snapshot


__all__ = [
    "EnvironmentFact",
    "EnvironmentFactStatus",
    "EnvironmentObservationV1",
    "EnvironmentPrivacyClass",
    "EnvironmentRetentionClass",
    "EnvironmentSnapshotV1",
    "EnvironmentSourceHealth",
    "EnvironmentSourceKind",
    "EnvironmentStatus",
    "FactKey",
    "FactStatus",
    "HealthStatus",
    "MAX_ATTRIBUTE_ARRAY_ITEMS",
    "MAX_ATTRIBUTE_KEY_CHARS",
    "MAX_ATTRIBUTE_STRING_CHARS",
    "MAX_ATTRIBUTES",
    "MAX_ATTRIBUTES_BYTES",
    "MAX_FACTS",
    "MAX_FUTURE_SKEW_SECONDS",
    "MAX_OBSERVATION_BYTES",
    "MAX_SNAPSHOT_BYTES",
    "MAX_STALE_KEYS",
    "OCR_MAX_RETENTION_SECONDS",
    "PrivacyClass",
    "RAW_MEDIA_RETENTION_SECONDS",
    "RetentionClass",
    "SCHEMA_VERSION",
    "SOURCE_MAX_TTL_SECONDS",
    "SOURCE_RETENTION_CLASS",
    "SourceHealth",
    "SourceKind",
    "canonical_hash",
    "canonical_json_bytes",
    "parse_environment_fact_wire",
    "parse_environment_observation_wire",
    "parse_environment_snapshot_wire",
    "validate_observation_for_reducer",
]
