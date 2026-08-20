"""Strict immutable wire contracts for L8 continuity.

The module is intentionally transport and storage neutral.  It freezes the
Python side of the JSON/CBOR boundary shared with Rust and TypeScript and does
not perform I/O, key management, migration, synchronization, or deletion.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import struct
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, TypeVar

from core.life.contracts import PrivacyClass, RetentionClass


SCHEMA_VERSION = 1
CANONICAL_JSON_PROFILE = "javis.canonical-json.v1"
CANONICAL_CBOR_PROFILE = "javis.canonical-cbor.v1"
CONTENT_HASH_PROFILE = "javis.content-sha256.v1"
SIGNATURE_INPUT_PROFILE = "javis.signature-input.v1"
HASH_ALGORITHM = "sha256"

MAX_ID_CHARS = 160
MAX_CODE_CHARS = 96
MAX_RELATIVE_PATH_BYTES = 1_024
MAX_PATH_COMPONENT_BYTES = 255
MAX_COLLECTION_ITEMS = 512
MAX_TREE_ITEMS = 4_096
MAX_NESTING_DEPTH = 12
MAX_METADATA_BYTES = 65_536
MAX_RECORD_BYTES = 1_048_576
MAX_MANIFEST_ENTRIES = 100_000
MAX_MANIFEST_BYTES = 1 << 44
MAX_ENVELOPE_RECORDS = 10_000
MAX_ENVELOPE_CIPHERTEXT_BYTES = 8 * 1024 * 1024
MAX_REASON_CODE_CHARS = 96

_RFC3339_MILLISECONDS = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d\.\d{3}Z"
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,159}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,95}")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_WINDOWS_RESERVED = re.compile(r"(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])")
_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_-]?key|password|secret|token|credential)"
    r"\s*[:=]\s*\S+)"
)
_FORBIDDEN_TREE_KEYS = frozenset(
    {
        "api_key",
        "authorization_header",
        "bearer_token",
        "credential",
        "password",
        "private_key",
        "private_key_bytes",
        "raw_audio",
        "raw_image",
        "raw_media",
        "raw_video",
        "secret",
        "secrets",
        "token",
    }
)
_CONTENT_FREE_FORBIDDEN_TOKENS = frozenset(
    {
        "absolute",
        "argv",
        "audio",
        "command",
        "content",
        "credential",
        "document",
        "file",
        "filename",
        "image",
        "media",
        "password",
        "path",
        "private",
        "secret",
        "text",
        "token",
        "transcript",
        "user",
        "username",
        "video",
    }
)

_Contract = TypeVar("_Contract", bound="_WireContract")


class SignatureAlgorithm(str, Enum):
    ED25519 = "ed25519"


class ManifestPurpose(str, Enum):
    MIGRATION = "migration"
    UPDATE = "update"
    COPY = "copy"
    SYNC = "sync"
    TERMINATION = "termination"
    RECOVERY = "recovery"


class EntryKind(str, Enum):
    FILE = "file"
    SQLITE = "sqlite"
    DIRECTORY_MARKER = "directory_marker"


class SQLiteCheckpoint(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    CHECKPOINTED = "checkpointed"
    REBUILD_REQUIRED = "rebuild_required"


class MigrationOperationKind(str, Enum):
    UPGRADE = "upgrade"
    RUNTIME_SWITCH = "runtime_switch"
    DATA_ROOT_MOVE = "data_root_move"
    DEVICE_MIGRATE = "device_migrate"
    RESTORE_IN_PLACE = "restore_in_place"
    COPY = "copy"
    LEGACY_IMPORT = "legacy_import"


class MigrationPhase(str, Enum):
    PLANNED = "planned"
    PREPARING = "preparing"
    PREPARED = "prepared"
    COPYING = "copying"
    COPIED = "copied"
    VERIFYING = "verifying"
    READY_TO_ACTIVATE = "ready_to_activate"
    ACTIVATING = "activating"
    CHECKPOINTING = "checkpointing"
    COMPLETED = "completed"
    FAILED_PRE_ACTIVATION = "failed_pre_activation"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    RECOVERING_READ_ONLY = "recovering_read_only"
    CANCELLED = "cancelled"


class LineageOpKind(str, Enum):
    BIRTH = "birth"
    RESTART = "restart"
    UPGRADE = "upgrade"
    RUNTIME_SWITCH = "runtime_switch"
    MODEL_SWITCH = "model_switch"
    DATA_ROOT_MOVE = "data_root_move"
    DEVICE_MIGRATE = "device_migrate"
    RESTORE_IN_PLACE = "restore_in_place"
    COPY = "copy"
    SYNC = "sync"
    MERGE = "merge"
    REVOKE = "revoke"
    TERMINATE = "terminate"


class BranchSyncState(str, Enum):
    CREATED = "created"
    PAIRING = "pairing"
    AUTHENTICATED = "authenticated"
    EXCHANGING = "exchanging"
    RECONCILING = "reconciling"
    AWAITING_RESOLUTION = "awaiting_resolution"
    APPLYING = "applying"
    VERIFYING = "verifying"
    SYNCED = "synced"
    CANCELLED = "cancelled"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class SyncConflictState(str, Enum):
    OPEN = "open"
    AWAITING_USER = "awaiting_user"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class PayloadAlgorithm(str, Enum):
    XCHACHA20_POLY1305_V1 = "xchacha20-poly1305-v1"
    AES_256_GCM_V1 = "aes-256-gcm-v1"


class TerminationScope(str, Enum):
    INSTANCE = "instance"
    BRANCH = "branch"
    IDENTITY_ALL_LOCAL = "identity_all_local"


class TerminationState(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    CHALLENGED = "challenged"
    AUTHORIZED = "authorized"
    QUIESCING = "quiescing"
    SEALING = "sealing"
    DELETING = "deleting"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED_RECOVERABLE = "failed_recoverable"


class TerminationAssuranceLevel(str, Enum):
    LOGICAL_DELETE = "logical_delete"
    CRYPTO_ERASURE_PLUS_ABSENCE = "crypto_erasure_plus_absence"


def _error(field_name: str, requirement: str) -> ValueError:
    return ValueError(f"{field_name}: {requirement}")


def _schema(value: Any) -> int:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise _error(
            "schema_version",
            f"unknown schema_version {value!r}; expected {SCHEMA_VERSION}",
        )
    return value


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
        raise _error(field_name, "must be a string")
    if not value and not allow_empty:
        raise _error(field_name, "must be non-empty")
    if len(value) > max_chars:
        raise _error(field_name, f"must contain at most {max_chars} characters")
    if unicodedata.normalize("NFC", value) != value:
        raise _error(field_name, "must use Unicode NFC")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _error(field_name, "must be valid UTF-8") from exc
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise _error(field_name, "must not contain control characters")
    return value


def _id(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    text = _string(value, field_name, max_chars=MAX_ID_CHARS, optional=optional)
    if text is not None and _ID.fullmatch(text) is None:
        raise _error(field_name, "must be a bounded opaque ID")
    return text


def _code(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    text = _string(value, field_name, max_chars=MAX_CODE_CHARS, optional=optional)
    if text is not None and _CODE.fullmatch(text) is None:
        raise _error(field_name, "must be a lowercase machine code")
    return text


def _hash(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _SHA256_HEX.fullmatch(value) is None:
        raise _error(field_name, "must be lowercase SHA-256 hex")
    return value


def _timestamp(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _RFC3339_MILLISECONDS.fullmatch(value) is None:
        raise _error(field_name, "must be RFC3339 UTC with millisecond precision")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise _error(field_name, "must be a valid UTC timestamp") from exc
    return value


def _timestamp_value(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


def _ordered_times(
    started: str,
    completed: str | None,
    *,
    completed_field: str = "completed_at_utc",
) -> None:
    if completed is not None and _timestamp_value(completed) < _timestamp_value(started):
        raise _error(completed_field, "must not precede the start timestamp")


def _bool(value: Any, field_name: str) -> bool:
    if type(value) is not bool:
        raise _error(field_name, "must be a boolean")
    return value


def _non_negative_int(value: Any, field_name: str, *, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0:
        raise _error(field_name, "must be a non-negative integer")
    if maximum is not None and value > maximum:
        raise _error(field_name, f"must be at most {maximum}")
    return value


def _positive_int(value: Any, field_name: str, *, maximum: int | None = None) -> int:
    if type(value) is not int or value < 1:
        raise _error(field_name, "must be a positive integer")
    if maximum is not None and value > maximum:
        raise _error(field_name, f"must be at most {maximum}")
    return value


def _enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise _error(field_name, f"must be a {enum_type.__name__} wire string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _error(field_name, f"unknown {enum_type.__name__} value {value!r}") from exc


def _relative_path(value: Any, field_name: str = "relative_path") -> str:
    text = _string(value, field_name, max_chars=MAX_RELATIVE_PATH_BYTES)
    assert text is not None
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:  # covered by _string; retained for clarity
        raise _error(field_name, "must be valid UTF-8") from exc
    if len(encoded) > MAX_RELATIVE_PATH_BYTES:
        raise _error(field_name, f"must be at most {MAX_RELATIVE_PATH_BYTES} UTF-8 bytes")
    if re.match(r"^[A-Za-z]:[\\/]", text):
        raise _error(field_name, "absolute drive paths are forbidden")
    if text.startswith(("/", "\\")) or "\\" in text:
        raise _error(field_name, "must use a relative path with '/' separators")
    if ":" in text:
        raise _error(field_name, "drive and alternate data stream syntax is forbidden")
    components = text.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise _error(field_name, "empty, dot, and parent components are forbidden")
    for component in components:
        if component.endswith((" ", ".")):
            raise _error(field_name, "components must not end with space or dot")
        if len(component.encode("utf-8")) > MAX_PATH_COMPONENT_BYTES:
            raise _error(field_name, "a component exceeds the UTF-8 byte limit")
        stem = component.split(".", 1)[0]
        if _WINDOWS_RESERVED.fullmatch(stem) is not None:
            raise _error(field_name, "Windows reserved names are forbidden")
    return text


def validate_relative_path(value: Any) -> str:
    """Validate and return a canonical wire-relative path."""

    return _relative_path(value)


def _base64url_bytes(
    value: Any,
    field_name: str,
    *,
    min_bytes: int = 1,
    max_bytes: int,
) -> bytes:
    text = _string(
        value,
        field_name,
        max_chars=((max_bytes + 2) // 3) * 4,
    )
    assert text is not None
    if "=" in text or _BASE64URL.fullmatch(text) is None:
        raise _error(field_name, "must be unpadded canonical base64url")
    if len(text) > ((max_bytes + 2) // 3) * 4:
        raise _error(field_name, f"must encode at most {max_bytes} bytes")
    try:
        decoded = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise _error(field_name, "must be valid base64url") from exc
    if not min_bytes <= len(decoded) <= max_bytes:
        raise _error(field_name, f"must encode {min_bytes}..{max_bytes} bytes")
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != text:
        raise _error(field_name, "must use canonical base64url encoding")
    return decoded


def _signature(value: Any, field_name: str = "signature") -> str:
    _base64url_bytes(value, field_name, min_bytes=64, max_bytes=64)
    return value


def _signature_algorithm(value: Any) -> SignatureAlgorithm:
    return _enum(value, SignatureAlgorithm, "signature_algorithm")  # type: ignore[return-value]


def _reason(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    return _code(value, field_name, optional=optional)


def _string_tuple(
    value: Any,
    field_name: str,
    validator: Callable[[Any, str], str | None],
    *,
    max_items: int = MAX_COLLECTION_ITEMS,
    unique: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise _error(field_name, "must be an array")
    if len(value) > max_items:
        raise _error(field_name, f"must contain at most {max_items} items")
    normalized: list[str] = []
    for index, item in enumerate(value):
        result = validator(item, f"{field_name}[{index}]")
        assert result is not None
        normalized.append(result)
    if unique and len(normalized) != len(set(normalized)):
        raise _error(field_name, "must not contain duplicates")
    return tuple(normalized)


def _ids(value: Any, field_name: str, *, max_items: int = MAX_COLLECTION_ITEMS) -> tuple[str, ...]:
    return _string_tuple(value, field_name, _id, max_items=max_items)


def _hashes(value: Any, field_name: str, *, max_items: int = MAX_COLLECTION_ITEMS) -> tuple[str, ...]:
    return _string_tuple(value, field_name, _hash, max_items=max_items)


def _codes(value: Any, field_name: str, *, max_items: int = MAX_COLLECTION_ITEMS) -> tuple[str, ...]:
    return _string_tuple(value, field_name, _code, max_items=max_items)


def _wire(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, _WireContract):
        return value.to_dict()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("wire object keys must be strings")
            result[key] = _wire(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def _freeze_tree(
    value: Any,
    field_name: str,
    *,
    depth: int = 0,
    counter: list[int] | None = None,
    reject_secrets: bool = True,
) -> Any:
    if depth > MAX_NESTING_DEPTH:
        raise _error(field_name, f"nesting exceeds {MAX_NESTING_DEPTH}")
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_TREE_ITEMS:
        raise _error(field_name, f"tree exceeds {MAX_TREE_ITEMS} values")
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise _error(field_name, f"object exceeds {MAX_COLLECTION_ITEMS} fields")
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = _string(key, f"{field_name}.<key>", max_chars=MAX_ID_CHARS)
            assert normalized_key is not None
            lowered = normalized_key.casefold()
            if reject_secrets and lowered in _FORBIDDEN_TREE_KEYS:
                raise _error(f"{field_name}.{normalized_key}", "secret-bearing field is forbidden")
            if lowered.endswith("path") or lowered.endswith("_path"):
                _relative_path(item, f"{field_name}.{normalized_key}")
            frozen[normalized_key] = _freeze_tree(
                item,
                f"{field_name}.{normalized_key}",
                depth=depth + 1,
                counter=counter,
                reject_secrets=reject_secrets,
            )
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise _error(field_name, f"array exceeds {MAX_COLLECTION_ITEMS} items")
        return tuple(
            _freeze_tree(
                item,
                f"{field_name}[{index}]",
                depth=depth + 1,
                counter=counter,
                reject_secrets=reject_secrets,
            )
            for index, item in enumerate(value)
        )
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _error(field_name, "numbers must be finite")
        return value
    if type(value) is str:
        text = _string(value, field_name, max_chars=MAX_METADATA_BYTES, allow_empty=True)
        assert text is not None
        if reject_secrets and _SECRET_VALUE.search(text):
            raise _error(field_name, "secret-like values are forbidden")
        if (
            text.startswith(("/", "\\\\", "\\?\\", "\\.\\"))
            or re.match(r"^[A-Za-z]:[\\/]", text)
        ):
            raise _error(field_name, "absolute and device paths are forbidden")
        return text
    raise _error(field_name, "must contain JSON-compatible values")


def _metadata(value: Any, field_name: str, *, max_bytes: int = MAX_METADATA_BYTES) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _error(field_name, "must be an object")
    frozen = _freeze_tree(value, field_name)
    if len(canonical_json_bytes(frozen)) > max_bytes:
        raise _error(field_name, f"canonical JSON must be at most {max_bytes} bytes")
    return frozen


def _metadata_tuple(
    value: Any,
    field_name: str,
    *,
    max_items: int = MAX_COLLECTION_ITEMS,
    item_bytes: int = MAX_METADATA_BYTES,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise _error(field_name, "must be an array of objects")
    if len(value) > max_items:
        raise _error(field_name, f"must contain at most {max_items} items")
    return tuple(
        _metadata(item, f"{field_name}[{index}]", max_bytes=item_bytes)
        for index, item in enumerate(value)
    )


def _int_map(value: Any, field_name: str, *, max_items: int = MAX_COLLECTION_ITEMS) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise _error(field_name, "must be an object")
    if len(value) > max_items:
        raise _error(field_name, f"must contain at most {max_items} entries")
    normalized: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        key = _code(raw_key, f"{field_name}.<key>")
        assert key is not None
        normalized[key] = _non_negative_int(raw_value, f"{field_name}.{key}")
    return MappingProxyType(normalized)


def _hash_map(value: Any, field_name: str, *, max_items: int = MAX_COLLECTION_ITEMS) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise _error(field_name, "must be an object")
    if len(value) > max_items:
        raise _error(field_name, f"must contain at most {max_items} entries")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _id(raw_key, f"{field_name}.<key>")
        assert key is not None
        digest = _hash(raw_value, f"{field_name}.{key}")
        assert digest is not None
        normalized[key] = digest
    return MappingProxyType(normalized)


def canonical_json_bytes(value: Mapping[str, Any] | "_WireContract") -> bytes:
    """Return Javis canonical JSON v1 bytes.

    The profile is UTF-8, NFC input, sorted keys, no insignificant whitespace,
    JSON literals only, and finite numbers only.
    """

    payload = value.to_dict() if isinstance(value, _WireContract) else _wire(value)
    if not isinstance(payload, Mapping):
        raise ValueError("canonical JSON root must be an object")
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


def _cbor_head(major: int, value: int) -> bytes:
    if value < 0:
        raise ValueError("CBOR length/value must be non-negative")
    prefix = major << 5
    if value < 24:
        return bytes((prefix | value,))
    if value <= 0xFF:
        return bytes((prefix | 24, value))
    if value <= 0xFFFF:
        return bytes((prefix | 25,)) + struct.pack(">H", value)
    if value <= 0xFFFFFFFF:
        return bytes((prefix | 26,)) + struct.pack(">I", value)
    if value <= 0xFFFFFFFFFFFFFFFF:
        return bytes((prefix | 27,)) + struct.pack(">Q", value)
    raise ValueError("integer is outside the canonical CBOR v1 range")


def _cbor_encode(value: Any, *, depth: int = 0) -> bytes:
    if depth > MAX_NESTING_DEPTH:
        raise ValueError(f"canonical CBOR nesting exceeds {MAX_NESTING_DEPTH}")
    if value is None:
        return b"\xf6"
    if value is False:
        return b"\xf4"
    if value is True:
        return b"\xf5"
    if type(value) is int:
        if value >= 0:
            return _cbor_head(0, value)
        return _cbor_head(1, -1 - value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("canonical CBOR numbers must be finite")
        # This profile intentionally fixes floats to IEEE-754 binary64 so each
        # implementation does not need cross-language shortest-float probing.
        return b"\xfb" + struct.pack(">d", value)
    if type(value) is str:
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError("canonical CBOR strings must use Unicode NFC")
        encoded = value.encode("utf-8")
        return _cbor_head(3, len(encoded)) + encoded
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ValueError("canonical CBOR array exceeds the item limit")
        return _cbor_head(4, len(value)) + b"".join(
            _cbor_encode(item, depth=depth + 1) for item in value
        )
    if isinstance(value, Mapping):
        if len(value) > MAX_COLLECTION_ITEMS:
            raise ValueError("canonical CBOR map exceeds the item limit")
        pairs: list[tuple[bytes, bytes]] = []
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("canonical CBOR map keys must be strings")
            encoded_key = _cbor_encode(key, depth=depth + 1)
            pairs.append((encoded_key, _cbor_encode(item, depth=depth + 1)))
        pairs.sort(key=lambda pair: (len(pair[0]), pair[0]))
        return _cbor_head(5, len(pairs)) + b"".join(
            key + item for key, item in pairs
        )
    raise ValueError(f"unsupported canonical CBOR value: {type(value).__name__}")


def canonical_cbor_bytes(value: Mapping[str, Any] | "_WireContract") -> bytes:
    """Return deterministic CBOR bytes for the JSON-compatible L8 profile."""

    payload = value.to_dict() if isinstance(value, _WireContract) else _wire(value)
    if not isinstance(payload, Mapping):
        raise ValueError("canonical CBOR root must be an object")
    return _cbor_encode(payload)


def canonical_hash(value: Mapping[str, Any] | "_WireContract") -> str:
    return hashlib.sha256(canonical_cbor_bytes(value)).hexdigest()


def _without_fields(value: Mapping[str, Any] | "_WireContract", names: set[str]) -> dict[str, Any]:
    payload = value.to_dict() if isinstance(value, _WireContract) else _wire(value)
    if not isinstance(payload, Mapping):
        raise ValueError("hash/signature input must be an object")
    return {key: item for key, item in payload.items() if key not in names}


def content_hash_input(value: Mapping[str, Any] | "_WireContract") -> bytes:
    payload = _without_fields(value, {"content_hash", "signature"})
    return CONTENT_HASH_PROFILE.encode("ascii") + b"\x00" + canonical_cbor_bytes(payload)


def compute_content_hash(value: Mapping[str, Any] | "_WireContract") -> str:
    return hashlib.sha256(content_hash_input(value)).hexdigest()


def signature_input(value: Mapping[str, Any] | "_WireContract") -> bytes:
    payload = _without_fields(value, {"signature"})
    return SIGNATURE_INPUT_PROFILE.encode("ascii") + b"\x00" + canonical_cbor_bytes(payload)


def verify_content_hash(value: Mapping[str, Any] | "_WireContract") -> bool:
    payload = value.to_dict() if isinstance(value, _WireContract) else _wire(value)
    if not isinstance(payload, Mapping):
        return False
    content_hash = payload.get("content_hash")
    return type(content_hash) is str and content_hash == compute_content_hash(payload)


class _WireContract:
    @classmethod
    def from_dict(cls: type[_Contract], data: Mapping[str, Any]) -> _Contract:
        if not isinstance(data, Mapping):
            raise ValueError(f"{cls.__name__}: wire value must be an object")
        expected = tuple(field.name for field in fields(cls))
        actual = set(data)
        missing = set(expected) - actual
        if missing:
            raise ValueError(f"{cls.__name__}: missing field(s): {', '.join(sorted(missing))}")
        unexpected = actual - set(expected)
        if unexpected:
            rendered = ", ".join(sorted(repr(item) for item in unexpected))
            raise ValueError(f"{cls.__name__}: unexpected field(s): {rendered}")
        return cls(**{name: data[name] for name in expected})

    from_wire = from_dict

    @classmethod
    def seal(cls: type[_Contract], **data: Any) -> _Contract:
        """Fill and verify ``content_hash`` for a complete unsigned mapping."""

        if "content_hash" not in {field.name for field in fields(cls)}:
            return cls.from_dict(data)
        candidate = dict(data)
        candidate["content_hash"] = compute_content_hash(candidate)
        return cls.from_dict(candidate)

    def to_dict(self) -> dict[str, Any]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}

    def to_wire(self) -> dict[str, Any]:
        return self.to_dict()

    def canonical_json_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def canonical_cbor_bytes(self) -> bytes:
        return canonical_cbor_bytes(self)

    def canonical_hash(self) -> str:
        return canonical_hash(self)

    def signature_input(self) -> bytes:
        return signature_input(self)

    def verify_content_hash(self) -> bool:
        return verify_content_hash(self)


def _set(instance: Any, name: str, value: Any) -> None:
    object.__setattr__(instance, name, value)


def _verify_record_hash(instance: _WireContract) -> None:
    actual = _hash(getattr(instance, "content_hash"), "content_hash")
    expected = compute_content_hash(instance)
    if actual != expected:
        raise _error("content_hash", f"does not match canonical record hash {expected}")


canonical_content_hash = compute_content_hash
signature_input_bytes = signature_input


def _normalize_signature(
    instance: Any,
    *,
    explicit_algorithm: bool,
    key_field: str = "signing_key_id",
) -> None:
    _id(getattr(instance, key_field), key_field)
    if explicit_algorithm:
        _set(instance, "signature_algorithm", _signature_algorithm(instance.signature_algorithm))
    _signature(instance.signature)


@dataclass(frozen=True, slots=True)
class ContinuityEntryV1(_WireContract):
    schema_version: int
    domain: str
    relative_path: str
    entry_kind: EntryKind
    size_bytes: int
    sha256: str
    logical_record_count: int | None
    sqlite_checkpoint: SQLiteCheckpoint
    owner_subject_id: str
    audience: tuple[str, ...]
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    expires_at_utc: str | None
    derivation_parent_ids: tuple[str, ...]
    required: bool
    encryption_ref: Mapping[str, Any] | None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _code(self.domain, "domain")
        _relative_path(self.relative_path)
        _set(self, "entry_kind", _enum(self.entry_kind, EntryKind, "entry_kind"))
        _non_negative_int(self.size_bytes, "size_bytes", maximum=MAX_MANIFEST_BYTES)
        _hash(self.sha256, "sha256")
        if self.logical_record_count is not None:
            _non_negative_int(self.logical_record_count, "logical_record_count", maximum=1 << 48)
        _set(
            self,
            "sqlite_checkpoint",
            _enum(self.sqlite_checkpoint, SQLiteCheckpoint, "sqlite_checkpoint"),
        )
        _id(self.owner_subject_id, "owner_subject_id")
        _set(self, "audience", _ids(self.audience, "audience", max_items=64))
        _set(self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class"))
        _set(self, "retention_class", _enum(self.retention_class, RetentionClass, "retention_class"))
        _timestamp(self.expires_at_utc, "expires_at_utc", optional=True)
        _set(self, "derivation_parent_ids", _ids(self.derivation_parent_ids, "derivation_parent_ids"))
        _bool(self.required, "required")
        if self.encryption_ref is not None:
            _set(self, "encryption_ref", _metadata(self.encryption_ref, "encryption_ref", max_bytes=4_096))
        if self.entry_kind is EntryKind.DIRECTORY_MARKER and self.size_bytes != 0:
            raise _error("size_bytes", "directory markers must have zero size")
        if self.entry_kind is not EntryKind.SQLITE and self.sqlite_checkpoint is not SQLiteCheckpoint.NOT_APPLICABLE:
            raise _error("sqlite_checkpoint", "non-SQLite entries must use not_applicable")


@dataclass(frozen=True, slots=True)
class ContinuityManifestV1(_WireContract):
    schema_version: int
    manifest_id: str
    purpose: ManifestPurpose
    identity_id: str
    lineage_id: str
    branch_id: str
    instance_id: str
    source_root_id: str
    source_release_id: str
    source_runtime_hash: str
    model_refs: tuple[Mapping[str, Any], ...]
    body_deployment_refs: tuple[Mapping[str, Any], ...]
    skill_deployment_refs: tuple[Mapping[str, Any], ...]
    domain_schemas: Mapping[str, int]
    event_cursors: Mapping[str, int]
    tombstone_high_watermarks: Mapping[str, int]
    entries: tuple[ContinuityEntryV1, ...]
    external_asset_refs: tuple[Mapping[str, Any], ...]
    key_refs: tuple[Mapping[str, Any], ...]
    total_entries: int
    total_bytes: int
    created_at_utc: str
    expires_at_utc: str
    created_by_version: str
    approval_ref: str | None
    previous_manifest_hash: str | None
    privacy_class: PrivacyClass
    retention_class: RetentionClass
    content_hash: str
    signing_key_id: str
    signature_algorithm: SignatureAlgorithm
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "manifest_id",
            "identity_id",
            "lineage_id",
            "branch_id",
            "instance_id",
            "source_root_id",
            "source_release_id",
        ):
            _id(getattr(self, field_name), field_name)
        _set(self, "purpose", _enum(self.purpose, ManifestPurpose, "purpose"))
        _hash(self.source_runtime_hash, "source_runtime_hash")
        _set(self, "model_refs", _metadata_tuple(self.model_refs, "model_refs"))
        _set(self, "body_deployment_refs", _metadata_tuple(self.body_deployment_refs, "body_deployment_refs"))
        _set(self, "skill_deployment_refs", _metadata_tuple(self.skill_deployment_refs, "skill_deployment_refs"))
        _set(self, "domain_schemas", _int_map(self.domain_schemas, "domain_schemas"))
        _set(self, "event_cursors", _int_map(self.event_cursors, "event_cursors"))
        _set(
            self,
            "tombstone_high_watermarks",
            _int_map(self.tombstone_high_watermarks, "tombstone_high_watermarks"),
        )
        if not isinstance(self.entries, (list, tuple)):
            raise _error("entries", "must be an array")
        if len(self.entries) > MAX_MANIFEST_ENTRIES:
            raise _error("entries", f"must contain at most {MAX_MANIFEST_ENTRIES} items")
        entries = tuple(
            item if isinstance(item, ContinuityEntryV1) else ContinuityEntryV1.from_dict(item)
            for item in self.entries
        )
        _set(self, "entries", entries)
        normalized_paths = [entry.relative_path.casefold() for entry in entries]
        if len(normalized_paths) != len(set(normalized_paths)):
            raise _error("entries", "relative paths must be unique without case collisions")
        _set(self, "external_asset_refs", _metadata_tuple(self.external_asset_refs, "external_asset_refs"))
        _set(self, "key_refs", _metadata_tuple(self.key_refs, "key_refs"))
        _non_negative_int(self.total_entries, "total_entries", maximum=MAX_MANIFEST_ENTRIES)
        _non_negative_int(self.total_bytes, "total_bytes", maximum=MAX_MANIFEST_BYTES)
        if self.total_entries != len(entries):
            raise _error("total_entries", "must equal the entries array length")
        if self.total_bytes != sum(entry.size_bytes for entry in entries):
            raise _error("total_bytes", "must equal the sum of entry sizes")
        created = _timestamp(self.created_at_utc, "created_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        assert created is not None and expires is not None
        if _timestamp_value(expires) <= _timestamp_value(created):
            raise _error("expires_at_utc", "must be later than created_at_utc")
        _id(self.created_by_version, "created_by_version")
        _id(self.approval_ref, "approval_ref", optional=True)
        _hash(self.previous_manifest_hash, "previous_manifest_hash", optional=True)
        _set(self, "privacy_class", _enum(self.privacy_class, PrivacyClass, "privacy_class"))
        _set(self, "retention_class", _enum(self.retention_class, RetentionClass, "retention_class"))
        if self.privacy_class in {PrivacyClass.SECRET, PrivacyClass.BIOMETRIC}:
            raise _error("privacy_class", "manifest metadata cannot carry secret or biometric material")
        _normalize_signature(self, explicit_algorithm=True)
        _verify_record_hash(self)


@dataclass(frozen=True, slots=True)
class MigrationPhaseReceiptV1(_WireContract):
    schema_version: int
    phase: MigrationPhase
    input_set_hash: str
    output_set_hash: str
    started_at_utc: str
    completed_at_utc: str | None
    processed_entries: int
    processed_bytes: int
    reason_code: str | None

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _set(self, "phase", _enum(self.phase, MigrationPhase, "phase"))
        _hash(self.input_set_hash, "input_set_hash")
        _hash(self.output_set_hash, "output_set_hash")
        started = _timestamp(self.started_at_utc, "started_at_utc")
        completed = _timestamp(self.completed_at_utc, "completed_at_utc", optional=True)
        assert started is not None
        _ordered_times(started, completed)
        _non_negative_int(self.processed_entries, "processed_entries", maximum=MAX_MANIFEST_ENTRIES)
        _non_negative_int(self.processed_bytes, "processed_bytes", maximum=MAX_MANIFEST_BYTES)
        _reason(self.reason_code, "reason_code", optional=True)


@dataclass(frozen=True, slots=True)
class MigrationReceiptV1(_WireContract):
    schema_version: int
    receipt_id: str
    operation_id: str
    operation_kind: MigrationOperationKind
    manifest_id: str
    manifest_hash: str
    identity_id: str
    lineage_id: str
    branch_id: str
    source_root_id: str
    target_root_id: str
    source_instance_id: str
    target_instance_id: str
    state: MigrationPhase
    phase_receipts: tuple[MigrationPhaseReceiptV1, ...]
    copied_entries: int
    copied_bytes: int
    verified_entries: int
    rebuilt_entries: int
    skipped_entries: int
    schema_migration_ids: tuple[str, ...]
    pointer_before_hash: str | None
    pointer_after_hash: str | None
    activation_journal_hash: str | None
    checkpoint_id: str | None
    rollback_target: str | None
    rollback_receipt_id: str | None
    started_at_utc: str
    completed_at_utc: str | None
    failure_reason_code: str | None
    residuals: tuple[Mapping[str, Any], ...]
    content_hash: str
    signing_key_id: str
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "receipt_id",
            "operation_id",
            "manifest_id",
            "identity_id",
            "lineage_id",
            "branch_id",
            "source_root_id",
            "target_root_id",
            "source_instance_id",
            "target_instance_id",
        ):
            _id(getattr(self, field_name), field_name)
        _set(self, "operation_kind", _enum(self.operation_kind, MigrationOperationKind, "operation_kind"))
        _hash(self.manifest_hash, "manifest_hash")
        _set(self, "state", _enum(self.state, MigrationPhase, "state"))
        if not isinstance(self.phase_receipts, (list, tuple)):
            raise _error("phase_receipts", "must be an array")
        if len(self.phase_receipts) > 32:
            raise _error("phase_receipts", "must contain at most 32 items")
        _set(
            self,
            "phase_receipts",
            tuple(
                item if isinstance(item, MigrationPhaseReceiptV1) else MigrationPhaseReceiptV1.from_dict(item)
                for item in self.phase_receipts
            ),
        )
        for field_name in (
            "copied_entries",
            "verified_entries",
            "rebuilt_entries",
            "skipped_entries",
        ):
            _non_negative_int(getattr(self, field_name), field_name, maximum=MAX_MANIFEST_ENTRIES)
        _non_negative_int(self.copied_bytes, "copied_bytes", maximum=MAX_MANIFEST_BYTES)
        _set(self, "schema_migration_ids", _ids(self.schema_migration_ids, "schema_migration_ids"))
        for field_name in (
            "pointer_before_hash",
            "pointer_after_hash",
            "activation_journal_hash",
        ):
            _hash(getattr(self, field_name), field_name, optional=True)
        for field_name in ("checkpoint_id", "rollback_target", "rollback_receipt_id"):
            _id(getattr(self, field_name), field_name, optional=True)
        started = _timestamp(self.started_at_utc, "started_at_utc")
        completed = _timestamp(self.completed_at_utc, "completed_at_utc", optional=True)
        assert started is not None
        _ordered_times(started, completed)
        _reason(self.failure_reason_code, "failure_reason_code", optional=True)
        _set(self, "residuals", _metadata_tuple(self.residuals, "residuals", max_items=MAX_COLLECTION_ITEMS, item_bytes=4_096))
        _normalize_signature(self, explicit_algorithm=False)
        _verify_record_hash(self)


@dataclass(frozen=True, slots=True)
class LineageOpV1(_WireContract):
    schema_version: int
    op_id: str
    op_sequence: int
    op_kind: LineageOpKind
    identity_id: str
    lineage_id: str
    branch_id: str
    instance_id: str
    parent_op_ids: tuple[str, ...]
    source_branch_id: str | None
    target_branch_id: str | None
    source_instance_id: str | None
    target_instance_id: str | None
    fork_point_manifest_hash: str | None
    continuity_manifest_hash: str | None
    release_before: str | None
    release_after: str | None
    model_before: str | None
    model_after: str | None
    reason_code: str
    user_decision_ref: str | None
    migration_receipt_id: str | None
    sync_receipt_ids: tuple[str, ...]
    created_at_utc: str
    previous_op_hashes: tuple[str, ...]
    content_hash: str
    signing_key_id: str
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("op_id", "identity_id", "lineage_id", "branch_id", "instance_id"):
            _id(getattr(self, field_name), field_name)
        _positive_int(self.op_sequence, "op_sequence", maximum=1 << 63)
        _set(self, "op_kind", _enum(self.op_kind, LineageOpKind, "op_kind"))
        _set(self, "parent_op_ids", _ids(self.parent_op_ids, "parent_op_ids", max_items=2))
        for field_name in (
            "source_branch_id",
            "target_branch_id",
            "source_instance_id",
            "target_instance_id",
            "release_before",
            "release_after",
            "model_before",
            "model_after",
            "user_decision_ref",
            "migration_receipt_id",
        ):
            _id(getattr(self, field_name), field_name, optional=True)
        _hash(self.fork_point_manifest_hash, "fork_point_manifest_hash", optional=True)
        _hash(self.continuity_manifest_hash, "continuity_manifest_hash", optional=True)
        _reason(self.reason_code, "reason_code")
        _set(self, "sync_receipt_ids", _ids(self.sync_receipt_ids, "sync_receipt_ids"))
        _timestamp(self.created_at_utc, "created_at_utc")
        _set(self, "previous_op_hashes", _hashes(self.previous_op_hashes, "previous_op_hashes", max_items=2))
        if self.op_kind is LineageOpKind.MERGE and len(self.parent_op_ids) != 2:
            raise _error("parent_op_ids", "merge operations require exactly two parents")
        if self.op_kind is LineageOpKind.COPY:
            if not self.source_branch_id or not self.target_branch_id:
                raise _error("target_branch_id", "copy requires source and target branch IDs")
            if self.source_branch_id == self.target_branch_id:
                raise _error("target_branch_id", "copy must create a distinct branch")
        _normalize_signature(self, explicit_algorithm=False)
        _verify_record_hash(self)


@dataclass(frozen=True, slots=True)
class BranchHeadV1(_WireContract):
    schema_version: int
    identity_id: str
    lineage_id: str
    branch_id: str
    head_op_id: str
    head_op_hash: str
    revision: int
    previous_head_hash: str | None
    updated_at_utc: str
    content_hash: str
    signing_key_id: str
    signature_algorithm: SignatureAlgorithm
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("identity_id", "lineage_id", "branch_id", "head_op_id"):
            _id(getattr(self, field_name), field_name)
        _hash(self.head_op_hash, "head_op_hash")
        _positive_int(self.revision, "revision", maximum=1 << 63)
        _hash(self.previous_head_hash, "previous_head_hash", optional=True)
        _timestamp(self.updated_at_utc, "updated_at_utc")
        _normalize_signature(self, explicit_algorithm=True)
        _verify_record_hash(self)


@dataclass(frozen=True, slots=True)
class BranchSyncRunV1(_WireContract):
    schema_version: int
    sync_run_id: str
    sync_session_id: str
    identity_id: str
    lineage_id: str
    local_branch_id: str
    remote_branch_id: str
    state: BranchSyncState
    allowed_domains: tuple[str, ...]
    base_heads: Mapping[str, str]
    advertised_heads: Mapping[str, str]
    envelope_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    applied_record_count: int
    tombstone_count: int
    started_at_utc: str
    updated_at_utc: str
    completed_at_utc: str | None
    failure_reason_code: str | None
    content_hash: str
    signing_key_id: str
    signature_algorithm: SignatureAlgorithm
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "sync_run_id",
            "sync_session_id",
            "identity_id",
            "lineage_id",
            "local_branch_id",
            "remote_branch_id",
        ):
            _id(getattr(self, field_name), field_name)
        _set(self, "state", _enum(self.state, BranchSyncState, "state"))
        _set(self, "allowed_domains", _codes(self.allowed_domains, "allowed_domains"))
        _set(self, "base_heads", _hash_map(self.base_heads, "base_heads"))
        _set(self, "advertised_heads", _hash_map(self.advertised_heads, "advertised_heads"))
        _set(self, "envelope_ids", _ids(self.envelope_ids, "envelope_ids"))
        _set(self, "conflict_ids", _ids(self.conflict_ids, "conflict_ids"))
        _non_negative_int(self.applied_record_count, "applied_record_count", maximum=MAX_ENVELOPE_RECORDS)
        _non_negative_int(self.tombstone_count, "tombstone_count", maximum=MAX_ENVELOPE_RECORDS)
        started = _timestamp(self.started_at_utc, "started_at_utc")
        updated = _timestamp(self.updated_at_utc, "updated_at_utc")
        completed = _timestamp(self.completed_at_utc, "completed_at_utc", optional=True)
        assert started is not None and updated is not None
        _ordered_times(started, updated, completed_field="updated_at_utc")
        _ordered_times(started, completed)
        _reason(self.failure_reason_code, "failure_reason_code", optional=True)
        _normalize_signature(self, explicit_algorithm=True)
        _verify_record_hash(self)


@dataclass(frozen=True, slots=True)
class SyncEnvelopeV1(_WireContract):
    schema_version: int
    envelope_id: str
    sync_session_id: str
    identity_id: str
    lineage_id: str
    sender_branch_id: str
    sender_instance_id: str
    recipient_device_key_id: str
    sender_device_key_id: str
    sender_sequence: int
    base_heads: Mapping[str, str]
    advertised_heads: Mapping[str, str]
    domain: str
    record_count: int
    tombstone_count: int
    payload_algorithm: PayloadAlgorithm
    nonce: str
    ciphertext: str
    payload_sha256: str
    key_epoch: int
    created_at_utc: str
    expires_at_utc: str
    content_hash: str
    signing_key_id: str
    signature_algorithm: SignatureAlgorithm
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "envelope_id",
            "sync_session_id",
            "identity_id",
            "lineage_id",
            "sender_branch_id",
            "sender_instance_id",
            "recipient_device_key_id",
            "sender_device_key_id",
        ):
            _id(getattr(self, field_name), field_name)
        _positive_int(self.sender_sequence, "sender_sequence", maximum=1 << 63)
        _set(self, "base_heads", _hash_map(self.base_heads, "base_heads"))
        _set(self, "advertised_heads", _hash_map(self.advertised_heads, "advertised_heads"))
        _code(self.domain, "domain")
        _non_negative_int(self.record_count, "record_count", maximum=MAX_ENVELOPE_RECORDS)
        _non_negative_int(self.tombstone_count, "tombstone_count", maximum=MAX_ENVELOPE_RECORDS)
        if self.record_count + self.tombstone_count > MAX_ENVELOPE_RECORDS:
            raise _error("record_count", "record and tombstone counts exceed the envelope limit")
        _set(self, "payload_algorithm", _enum(self.payload_algorithm, PayloadAlgorithm, "payload_algorithm"))
        nonce = _base64url_bytes(self.nonce, "nonce", min_bytes=12, max_bytes=24)
        if self.payload_algorithm is PayloadAlgorithm.XCHACHA20_POLY1305_V1 and len(nonce) != 24:
            raise _error("nonce", "XChaCha20-Poly1305 requires 24 bytes")
        if self.payload_algorithm is PayloadAlgorithm.AES_256_GCM_V1 and len(nonce) != 12:
            raise _error("nonce", "AES-256-GCM requires 12 bytes")
        ciphertext = _base64url_bytes(
            self.ciphertext,
            "ciphertext",
            min_bytes=16,
            max_bytes=MAX_ENVELOPE_CIPHERTEXT_BYTES,
        )
        _hash(self.payload_sha256, "payload_sha256")
        if hashlib.sha256(ciphertext).hexdigest() != self.payload_sha256:
            raise _error("payload_sha256", "does not match ciphertext bytes")
        _positive_int(self.key_epoch, "key_epoch", maximum=1 << 31)
        created = _timestamp(self.created_at_utc, "created_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        assert created is not None and expires is not None
        if _timestamp_value(expires) <= _timestamp_value(created):
            raise _error("expires_at_utc", "must be later than created_at_utc")
        _normalize_signature(self, explicit_algorithm=True)
        _verify_record_hash(self)


@dataclass(frozen=True, slots=True)
class SyncConflictV1(_WireContract):
    schema_version: int
    conflict_id: str
    sync_run_id: str
    domain: str
    identity_id: str
    lineage_id: str
    local_branch_id: str
    remote_branch_id: str
    source_head_hashes: tuple[str, ...]
    record_hashes: tuple[str, ...]
    reason_code: str
    state: SyncConflictState
    resolution_code: str | None
    user_decision_ref: str | None
    created_at_utc: str
    resolved_at_utc: str | None
    content_hash: str
    signing_key_id: str
    signature_algorithm: SignatureAlgorithm
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in (
            "conflict_id",
            "sync_run_id",
            "identity_id",
            "lineage_id",
            "local_branch_id",
            "remote_branch_id",
        ):
            _id(getattr(self, field_name), field_name)
        _code(self.domain, "domain")
        _set(self, "source_head_hashes", _hashes(self.source_head_hashes, "source_head_hashes", max_items=8))
        _set(self, "record_hashes", _hashes(self.record_hashes, "record_hashes"))
        _reason(self.reason_code, "reason_code")
        _set(self, "state", _enum(self.state, SyncConflictState, "state"))
        _reason(self.resolution_code, "resolution_code", optional=True)
        _id(self.user_decision_ref, "user_decision_ref", optional=True)
        created = _timestamp(self.created_at_utc, "created_at_utc")
        resolved = _timestamp(self.resolved_at_utc, "resolved_at_utc", optional=True)
        assert created is not None
        _ordered_times(created, resolved, completed_field="resolved_at_utc")
        if self.state is SyncConflictState.RESOLVED and (not self.resolution_code or not self.resolved_at_utc):
            raise _error("resolution_code", "resolved conflicts require resolution evidence")
        _normalize_signature(self, explicit_algorithm=True)
        _verify_record_hash(self)


@dataclass(frozen=True, slots=True)
class TerminationPlanV1(_WireContract):
    schema_version: int
    plan_id: str
    scope: TerminationScope
    identity_id: str
    lineage_id: str
    branch_ids: tuple[str, ...]
    instance_ids: tuple[str, ...]
    root_id: str
    manifest_id: str
    manifest_hash: str
    target_entry_hashes: tuple[str, ...]
    external_asset_actions: tuple[Mapping[str, Any], ...]
    owned_processes: tuple[Mapping[str, Any], ...]
    database_checkpoint_ids: tuple[str, ...]
    tombstone_plan: Mapping[str, Any]
    sync_outbox_policy: str
    key_ids_to_destroy: tuple[str, ...]
    key_epoch_after: int
    preserve_user_exports: bool
    preserve_installed_app: bool
    receipt_export_target_hash: str
    receipt_ephemeral_public_key: str
    receipt_ephemeral_key_id: str
    confirmation_challenge_hash: str
    authorization_id: str
    created_at_utc: str
    expires_at_utc: str
    irreversible_after_state: TerminationState
    content_hash: str
    signing_key_id: str
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        for field_name in ("plan_id", "identity_id", "lineage_id", "root_id", "manifest_id"):
            _id(getattr(self, field_name), field_name)
        _set(self, "scope", _enum(self.scope, TerminationScope, "scope"))
        _set(self, "branch_ids", _ids(self.branch_ids, "branch_ids"))
        _set(self, "instance_ids", _ids(self.instance_ids, "instance_ids"))
        if not self.branch_ids or not self.instance_ids:
            raise _error("branch_ids", "termination must identify at least one branch and instance")
        _hash(self.manifest_hash, "manifest_hash")
        _set(self, "target_entry_hashes", _hashes(self.target_entry_hashes, "target_entry_hashes", max_items=MAX_MANIFEST_ENTRIES))
        _set(self, "external_asset_actions", _metadata_tuple(self.external_asset_actions, "external_asset_actions"))
        _set(self, "owned_processes", _metadata_tuple(self.owned_processes, "owned_processes"))
        _set(self, "database_checkpoint_ids", _ids(self.database_checkpoint_ids, "database_checkpoint_ids"))
        _set(self, "tombstone_plan", _metadata(self.tombstone_plan, "tombstone_plan"))
        _code(self.sync_outbox_policy, "sync_outbox_policy")
        _set(self, "key_ids_to_destroy", _ids(self.key_ids_to_destroy, "key_ids_to_destroy"))
        _non_negative_int(self.key_epoch_after, "key_epoch_after", maximum=1 << 31)
        _bool(self.preserve_user_exports, "preserve_user_exports")
        _bool(self.preserve_installed_app, "preserve_installed_app")
        _hash(self.receipt_export_target_hash, "receipt_export_target_hash")
        _base64url_bytes(
            self.receipt_ephemeral_public_key,
            "receipt_ephemeral_public_key",
            min_bytes=32,
            max_bytes=32,
        )
        _id(self.receipt_ephemeral_key_id, "receipt_ephemeral_key_id")
        _hash(self.confirmation_challenge_hash, "confirmation_challenge_hash")
        _id(self.authorization_id, "authorization_id")
        created = _timestamp(self.created_at_utc, "created_at_utc")
        expires = _timestamp(self.expires_at_utc, "expires_at_utc")
        assert created is not None and expires is not None
        if _timestamp_value(expires) <= _timestamp_value(created):
            raise _error("expires_at_utc", "must be later than created_at_utc")
        _set(
            self,
            "irreversible_after_state",
            _enum(self.irreversible_after_state, TerminationState, "irreversible_after_state"),
        )
        if self.irreversible_after_state is not TerminationState.DELETING:
            raise _error("irreversible_after_state", "must equal deleting")
        _normalize_signature(self, explicit_algorithm=False)
        _verify_record_hash(self)


def _content_free_key_is_allowed(key: str) -> bool:
    lowered = key.casefold()
    if lowered in {"content_free", "content_hash"} or lowered.endswith("_hash"):
        return True
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", lowered)))
    return tokens.isdisjoint(_CONTENT_FREE_FORBIDDEN_TOKENS)


def _scan_content_free(value: Any, field_name: str, *, depth: int = 0) -> None:
    if depth > MAX_NESTING_DEPTH:
        raise _error(field_name, f"nesting exceeds {MAX_NESTING_DEPTH}")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if type(key) is not str or not _content_free_key_is_allowed(key):
                raise _error(f"{field_name}.{key}", "content-bearing field names are forbidden")
            _scan_content_free(item, f"{field_name}.{key}", depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_content_free(item, f"{field_name}[{index}]", depth=depth + 1)
        return
    if type(value) is str:
        _string(value, field_name, max_chars=MAX_METADATA_BYTES, allow_empty=True)
        if _SECRET_VALUE.search(value):
            raise _error(field_name, "secret-like values are forbidden")
        if "/" in value or "\\" in value or re.match(r"^[A-Za-z]:", value):
            raise _error(field_name, "path-like values are forbidden")
        return
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    raise _error(field_name, "must contain finite JSON-compatible values")


@dataclass(frozen=True, slots=True)
class TerminationReceiptV1(_WireContract):
    schema_version: int
    receipt_id: str
    plan_id: str
    plan_hash: str
    scope: TerminationScope
    identity_id_hash: str
    lineage_id_hash: str
    root_id_hash: str
    started_at_utc: str
    completed_at_utc: str
    state: TerminationState
    quiesce_receipt_hash: str
    tombstone_high_watermarks: Mapping[str, int]
    destroyed_key_ids_hash: str
    key_destroy_results: tuple[Mapping[str, Any], ...]
    deleted_entry_count: int
    deleted_bytes: int
    external_asset_results: tuple[Mapping[str, Any], ...]
    verification_checks: tuple[Mapping[str, Any], ...]
    residual_count: int
    residual_reason_codes: tuple[str, ...]
    assurance_level: TerminationAssuranceLevel
    content_free: bool
    verifier_version: str
    previous_receipt_hash: str | None
    content_hash: str
    receipt_ephemeral_key_id: str
    signature_algorithm: SignatureAlgorithm
    signature: str

    def __post_init__(self) -> None:
        _schema(self.schema_version)
        _id(self.receipt_id, "receipt_id")
        _id(self.plan_id, "plan_id")
        for field_name in (
            "plan_hash",
            "identity_id_hash",
            "lineage_id_hash",
            "root_id_hash",
            "quiesce_receipt_hash",
            "destroyed_key_ids_hash",
        ):
            _hash(getattr(self, field_name), field_name)
        _set(self, "scope", _enum(self.scope, TerminationScope, "scope"))
        started = _timestamp(self.started_at_utc, "started_at_utc")
        completed = _timestamp(self.completed_at_utc, "completed_at_utc")
        assert started is not None and completed is not None
        _ordered_times(started, completed)
        _set(self, "state", _enum(self.state, TerminationState, "state"))
        if self.state not in {TerminationState.COMPLETED, TerminationState.PARTIAL}:
            raise _error("state", "termination receipts require completed or partial")
        _set(
            self,
            "tombstone_high_watermarks",
            _int_map(self.tombstone_high_watermarks, "tombstone_high_watermarks"),
        )
        _set(self, "key_destroy_results", _metadata_tuple(self.key_destroy_results, "key_destroy_results", item_bytes=4_096))
        _non_negative_int(self.deleted_entry_count, "deleted_entry_count", maximum=MAX_MANIFEST_ENTRIES)
        _non_negative_int(self.deleted_bytes, "deleted_bytes", maximum=MAX_MANIFEST_BYTES)
        _set(self, "external_asset_results", _metadata_tuple(self.external_asset_results, "external_asset_results", item_bytes=4_096))
        _set(self, "verification_checks", _metadata_tuple(self.verification_checks, "verification_checks", item_bytes=4_096))
        _non_negative_int(self.residual_count, "residual_count", maximum=MAX_MANIFEST_ENTRIES)
        _set(self, "residual_reason_codes", _codes(self.residual_reason_codes, "residual_reason_codes"))
        if self.residual_count == 0 and self.residual_reason_codes:
            raise _error("residual_reason_codes", "must be empty when residual_count is zero")
        if self.state is TerminationState.COMPLETED and self.residual_count != 0:
            raise _error("residual_count", "completed receipts cannot report residuals")
        _set(
            self,
            "assurance_level",
            _enum(self.assurance_level, TerminationAssuranceLevel, "assurance_level"),
        )
        if not _bool(self.content_free, "content_free"):
            raise _error("content_free", "must be true")
        _id(self.verifier_version, "verifier_version")
        _hash(self.previous_receipt_hash, "previous_receipt_hash", optional=True)
        _id(self.receipt_ephemeral_key_id, "receipt_ephemeral_key_id")
        _normalize_signature(
            self,
            explicit_algorithm=True,
            key_field="receipt_ephemeral_key_id",
        )
        for field_name in (
            "tombstone_high_watermarks",
            "key_destroy_results",
            "external_asset_results",
            "verification_checks",
            "residual_reason_codes",
        ):
            _scan_content_free(getattr(self, field_name), field_name)
        _verify_record_hash(self)


__all__ = [
    "SCHEMA_VERSION",
    "CANONICAL_JSON_PROFILE",
    "CANONICAL_CBOR_PROFILE",
    "CONTENT_HASH_PROFILE",
    "SIGNATURE_INPUT_PROFILE",
    "HASH_ALGORITHM",
    "MAX_ENVELOPE_CIPHERTEXT_BYTES",
    "SignatureAlgorithm",
    "ManifestPurpose",
    "EntryKind",
    "SQLiteCheckpoint",
    "MigrationOperationKind",
    "MigrationPhase",
    "LineageOpKind",
    "BranchSyncState",
    "SyncConflictState",
    "PayloadAlgorithm",
    "TerminationScope",
    "TerminationState",
    "TerminationAssuranceLevel",
    "ContinuityEntryV1",
    "ContinuityManifestV1",
    "MigrationPhaseReceiptV1",
    "MigrationReceiptV1",
    "LineageOpV1",
    "BranchHeadV1",
    "BranchSyncRunV1",
    "SyncEnvelopeV1",
    "SyncConflictV1",
    "TerminationPlanV1",
    "TerminationReceiptV1",
    "canonical_json_bytes",
    "canonical_cbor_bytes",
    "canonical_hash",
    "content_hash_input",
    "compute_content_hash",
    "canonical_content_hash",
    "signature_input",
    "signature_input_bytes",
    "verify_content_hash",
    "validate_relative_path",
]
