"""Deterministic minimization boundary for L4 environment sources."""

from __future__ import annotations

import math
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping

from core.environment.contracts import AttributeValue, SourceKind


class EnvironmentMinimizationError(ValueError):
    pass


_SENSITIVE_KEY_PARTS = frozenset(
    {
        "audio",
        "biometric",
        "bytes",
        "commandline",
        "content",
        "frame",
        "image",
        "ocrtext",
        "path",
        "pixel",
        "prompt",
        "raw",
        "secret",
        "screenshot",
        "text",
        "token",
        "transcript",
        "video",
        "windowtitle",
    }
)
_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_LEVELS = frozenset({"unknown", "low", "normal", "high", "critical"})
_AVAILABILITY = frozenset({"unknown", "available", "busy", "missing", "unavailable"})
_APP_CATEGORIES = frozenset(
    {
        "browser",
        "communication",
        "creative",
        "development",
        "document",
        "media",
        "productivity",
        "system",
        "unknown",
    }
)
_TASK_STATES = frozenset({"unknown", "ready", "busy", "blocked", "complete", "error"})
_OBJECT_CLASSES = frozenset(
    {
        "browser",
        "button",
        "code_editor",
        "dialog",
        "document",
        "error",
        "image",
        "progress",
        "terminal",
        "warning",
    }
)


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _reject_sensitive_key(key: str) -> None:
    normalized = _normalized_key(key)
    if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        raise EnvironmentMinimizationError(f"sensitive source field is forbidden: {key}")


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise EnvironmentMinimizationError(f"{field} must be a boolean")
    return value


def _count(value: Any, field: str, maximum: int = 1_000_000) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise EnvironmentMinimizationError(f"{field} must be an integer from 0 to {maximum}")
    return value


def _finite_unit(value: Any, field: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise EnvironmentMinimizationError(f"{field} must be finite")
    normalized = float(value)
    if normalized < 0 or normalized > 1:
        raise EnvironmentMinimizationError(f"{field} must be from 0 to 1")
    return normalized


def _choice(options: frozenset[str]) -> Callable[[Any, str], str]:
    def validate(value: Any, field: str) -> str:
        if type(value) is not str or value not in options:
            raise EnvironmentMinimizationError(f"{field} contains an unsupported category")
        return value

    return validate


def _alias(value: Any, field: str) -> str:
    if type(value) is not str or not value or len(value) > 64:
        raise EnvironmentMinimizationError(f"{field} must contain 1 to 64 characters")
    if _ABSOLUTE_PATH.match(value) or "\\" in value or "/" in value or ".." in value:
        raise EnvironmentMinimizationError(f"{field} must not contain a filesystem path")
    if any(ord(character) < 32 for character in value):
        raise EnvironmentMinimizationError(f"{field} must not contain control characters")
    return value


def _object_classes(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 12:
        raise EnvironmentMinimizationError(f"{field} must contain at most 12 classes")
    classes: list[str] = []
    for item in value:
        if type(item) is not str or item not in _OBJECT_CLASSES:
            raise EnvironmentMinimizationError(f"{field} contains an unsupported object class")
        if item not in classes:
            classes.append(item)
    return tuple(classes)


Validator = Callable[[Any, str], AttributeValue]
_SOURCE_FIELDS: Mapping[SourceKind, Mapping[str, Validator]] = MappingProxyType(
    {
        SourceKind.FOREGROUND_APP: MappingProxyType(
            {
                "app_category": _choice(_APP_CATEGORIES),
                "availability": _choice(_AVAILABILITY),
                "is_foreground": _boolean,
            }
        ),
        SourceKind.PROCESS_HEALTH: MappingProxyType(
            {
                "cpu_level": _choice(_LEVELS),
                "memory_level": _choice(_LEVELS),
                "process_count": _count,
                "responsive": _boolean,
            }
        ),
        SourceKind.DEVICE_HEALTH: MappingProxyType(
            {
                "availability": _choice(_AVAILABILITY),
                "battery_level": _choice(_LEVELS),
                "disk_level": _choice(_LEVELS),
                "network_level": _choice(_LEVELS),
                "thermal_level": _choice(_LEVELS),
            }
        ),
        SourceKind.WORKSPACE_METADATA: MappingProxyType(
            {
                "availability": _choice(_AVAILABILITY),
                "change_count": _count,
                "dirty": _boolean,
                "file_count": _count,
                "workspace_alias": _alias,
            }
        ),
        SourceKind.SCREEN_OCR: MappingProxyType(
            {
                "confidence": _finite_unit,
                "has_blocker": _boolean,
                "match_count": _count,
                "target_present": _boolean,
                "task_state": _choice(_TASK_STATES),
            }
        ),
        SourceKind.SCREEN_OBJECT: MappingProxyType(
            {
                "confidence": _finite_unit,
                "object_classes": _object_classes,
                "object_count": _count,
                "target_present": _boolean,
            }
        ),
        SourceKind.USER_DEFINED_ALIAS: MappingProxyType(
            {
                "alias": _alias,
                "availability": _choice(_AVAILABILITY),
            }
        ),
    }
)


class EnvironmentMinimizer:
    """Convert untrusted source payloads into bounded categorical facts."""

    def minimize(
        self,
        source_kind: SourceKind | str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, AttributeValue]:
        try:
            kind = source_kind if isinstance(source_kind, SourceKind) else SourceKind(source_kind)
        except (TypeError, ValueError) as exc:
            raise EnvironmentMinimizationError("source_kind is unsupported") from exc
        if kind is SourceKind.RAW_MEDIA:
            raise EnvironmentMinimizationError("raw media cannot cross the minimization boundary")
        if not isinstance(payload, Mapping):
            raise EnvironmentMinimizationError("source payload must be an object")
        if not payload:
            raise EnvironmentMinimizationError("source payload must not be empty")

        field_policy = _SOURCE_FIELDS.get(kind)
        if field_policy is None:
            raise EnvironmentMinimizationError("source_kind has no minimization policy")

        minimized: dict[str, AttributeValue] = {}
        for key, value in payload.items():
            if type(key) is not str:
                raise EnvironmentMinimizationError("source field names must be strings")
            _reject_sensitive_key(key)
            validator = field_policy.get(key)
            if validator is None:
                raise EnvironmentMinimizationError(f"source field is not allowlisted: {key}")
            minimized[key] = validator(value, key)
        return MappingProxyType(minimized)


__all__ = ["EnvironmentMinimizationError", "EnvironmentMinimizer"]
