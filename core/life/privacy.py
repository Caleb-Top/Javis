"""Bounded privacy classification and payload redaction for life events."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import PrivacyClass, RetentionClass


_SECRET_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "cookie",
        "password",
        "secret",
        "token",
    }
)
_BIOMETRIC_EVENT_PREFIXES = (
    "voice.audio",
    "voice.embedding",
    "camera.frame",
    "face.embedding",
    "biometric.",
)
_RESTRICTED_KEYS = frozenset(
    {
        "path",
        "root",
        "cwd",
        "process",
        "pid",
        "command",
        "environment",
        "env",
    }
)


class PayloadRejected(ValueError):
    """Raised when a payload cannot be handled within privacy bounds."""


@dataclass(frozen=True)
class RedactionResult:
    payload: dict[str, Any]
    summary: tuple[str, ...]


class PrivacyPolicy:
    """Classify event payload risk before any broad payload copy."""

    def __init__(
        self,
        *,
        max_depth: int = 4,
        max_collection_items: int = 32,
        max_string_chars: int = 256,
        max_total_bytes: int = 8_192,
    ) -> None:
        if min(max_depth, max_collection_items, max_string_chars, max_total_bytes) < 1:
            raise ValueError("privacy bounds must be positive integers")
        self.max_depth = max_depth
        self.max_collection_items = max_collection_items
        self.max_string_chars = max_string_chars
        self.max_total_bytes = max_total_bytes

    def classify(
        self,
        event_type: str,
        payload: Any,
    ) -> tuple[PrivacyClass, RetentionClass]:
        if type(event_type) is not str:
            return PrivacyClass.LOCAL_INTERNAL, RetentionClass.OPERATIONAL
        event_name = event_type.casefold()
        if event_name.startswith(_BIOMETRIC_EVENT_PREFIXES):
            return PrivacyClass.BIOMETRIC, RetentionClass.NEVER_PERSIST
        if self._contains_secret(payload):
            return PrivacyClass.SECRET, RetentionClass.NEVER_PERSIST
        if event_name.startswith("approval."):
            return PrivacyClass.RESTRICTED_SYSTEM, RetentionClass.AUDIT
        if event_name.startswith("tool."):
            retention = (
                RetentionClass.AUDIT
                if event_name.endswith("failed")
                else RetentionClass.OPERATIONAL
            )
            return PrivacyClass.RESTRICTED_SYSTEM, retention
        if event_name.startswith(("request.", "activity.", "response.")):
            return PrivacyClass.USER_PRIVATE, RetentionClass.SESSION
        if event_name == "runtime.created":
            return PrivacyClass.LOCAL_INTERNAL, RetentionClass.CONTINUITY
        if event_name.startswith(("runtime.", "subsystem.", "event_store.")):
            return PrivacyClass.LOCAL_INTERNAL, RetentionClass.OPERATIONAL
        if event_name.startswith(("agent_", "memory.", "skill.", "evolution.")):
            return PrivacyClass.LOCAL_INTERNAL, RetentionClass.OPERATIONAL
        return PrivacyClass.LOCAL_INTERNAL, RetentionClass.OPERATIONAL

    def redact_payload(self, event_type: str, payload: Any) -> dict[str, Any]:
        privacy_class, _ = self.classify(event_type, payload)
        if privacy_class in {PrivacyClass.SECRET, PrivacyClass.BIOMETRIC}:
            return {"diagnostic": "payload_rejected"}
        return self.redact_allowlisted(payload, allowed_fields=None).payload

    def redact_allowlisted(
        self,
        payload: Any,
        *,
        allowed_fields: frozenset[str] | None,
    ) -> RedactionResult:
        if not isinstance(payload, Mapping):
            raise PayloadRejected("payload must be an object")
        self.validate_structure(payload)
        summary: set[str] = set()
        active_ids: set[int] = set()
        result: dict[str, Any] = {}
        items = list(payload.items())
        if len(items) > self.max_collection_items:
            summary.add("collection_truncated")
            items = items[: self.max_collection_items]
        for key, value in items:
            if type(key) is not str:
                summary.add("non_string_key_removed")
                continue
            normalized_key = key.casefold()
            if self._is_secret_key(normalized_key):
                summary.add("secret_field_removed")
                continue
            if allowed_fields is not None and key not in allowed_fields:
                summary.add("field_not_allowlisted")
                continue
            result[key] = self._bounded_value(
                value,
                depth=1,
                active_ids=active_ids,
                summary=summary,
            )
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > self.max_total_bytes:
            raise PayloadRejected("payload exceeds the serialized byte budget")
        return RedactionResult(result, tuple(sorted(summary)))

    def validate_structure(self, payload: Any) -> None:
        """Reject cycles and unserializable objects without retaining values."""

        self._validate_structure(
            payload,
            depth=0,
            active_ids=set(),
            visited_nodes=[0],
        )

    def index_summary(self, event_type: str, payload: Any) -> str:
        privacy_class, _ = self.classify(event_type, payload)
        if privacy_class in {
            PrivacyClass.SECRET,
            PrivacyClass.BIOMETRIC,
            PrivacyClass.USER_PRIVATE,
            PrivacyClass.RESTRICTED_SYSTEM,
        }:
            return ""
        try:
            safe = self.redact_allowlisted(payload, allowed_fields=frozenset()).payload
        except PayloadRejected:
            return ""
        return json.dumps(safe, ensure_ascii=False, sort_keys=True) if safe else ""

    def _bounded_value(
        self,
        value: Any,
        *,
        depth: int,
        active_ids: set[int],
        summary: set[str],
    ) -> Any:
        if depth > self.max_depth:
            summary.add("depth_truncated")
            return "[truncated]"
        if value is None or type(value) in {bool, int}:
            return value
        if type(value) is float:
            if not math.isfinite(value):
                summary.add("non_finite_number_removed")
                return None
            return value
        if type(value) is str:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise PayloadRejected("payload contains invalid UTF-8") from exc
            if len(value) > self.max_string_chars:
                summary.add("string_truncated")
                return value[: self.max_string_chars]
            return value
        if isinstance(value, Mapping):
            marker = id(value)
            if marker in active_ids:
                raise PayloadRejected("payload contains a cyclic object")
            active_ids.add(marker)
            try:
                result: dict[str, Any] = {}
                items = list(value.items())
                if len(items) > self.max_collection_items:
                    summary.add("collection_truncated")
                    items = items[: self.max_collection_items]
                for key, item in items:
                    if type(key) is not str:
                        summary.add("non_string_key_removed")
                        continue
                    if self._is_secret_key(key.casefold()):
                        summary.add("secret_field_removed")
                        continue
                    result[key] = self._bounded_value(
                        item,
                        depth=depth + 1,
                        active_ids=active_ids,
                        summary=summary,
                    )
                return result
            finally:
                active_ids.remove(marker)
        if isinstance(value, (list, tuple)):
            marker = id(value)
            if marker in active_ids:
                raise PayloadRejected("payload contains a cyclic collection")
            active_ids.add(marker)
            try:
                items = list(value)
                if len(items) > self.max_collection_items:
                    summary.add("collection_truncated")
                    items = items[: self.max_collection_items]
                return [
                    self._bounded_value(
                        item,
                        depth=depth + 1,
                        active_ids=active_ids,
                        summary=summary,
                    )
                    for item in items
                ]
            finally:
                active_ids.remove(marker)
        raise PayloadRejected(f"payload contains unsupported {type(value).__name__}")

    def _validate_structure(
        self,
        value: Any,
        *,
        depth: int,
        active_ids: set[int],
        visited_nodes: list[int],
    ) -> None:
        visited_nodes[0] += 1
        node_budget = self.max_collection_items * (self.max_depth + 1)
        if visited_nodes[0] > node_budget or depth > self.max_depth:
            return
        if value is None or type(value) in {bool, int}:
            return
        if type(value) is float:
            if not math.isfinite(value):
                raise PayloadRejected("payload contains a non-finite number")
            return
        if type(value) is str:
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise PayloadRejected("payload contains invalid UTF-8") from exc
            return
        if isinstance(value, Mapping):
            marker = id(value)
            if marker in active_ids:
                raise PayloadRejected("payload contains a cyclic object")
            active_ids.add(marker)
            try:
                for key, item in list(value.items())[: self.max_collection_items]:
                    if type(key) is not str:
                        continue
                    self._validate_structure(
                        item,
                        depth=depth + 1,
                        active_ids=active_ids,
                        visited_nodes=visited_nodes,
                    )
            finally:
                active_ids.remove(marker)
            return
        if isinstance(value, (list, tuple)):
            marker = id(value)
            if marker in active_ids:
                raise PayloadRejected("payload contains a cyclic collection")
            active_ids.add(marker)
            try:
                for item in list(value)[: self.max_collection_items]:
                    self._validate_structure(
                        item,
                        depth=depth + 1,
                        active_ids=active_ids,
                        visited_nodes=visited_nodes,
                    )
            finally:
                active_ids.remove(marker)
            return
        raise PayloadRejected(f"payload contains unsupported {type(value).__name__}")

    def _contains_secret(
        self,
        value: Any,
        *,
        depth: int = 0,
        seen: set[int] | None = None,
    ) -> bool:
        if seen is None:
            seen = set()
        if isinstance(value, Mapping):
            marker = id(value)
            if marker in seen:
                return False
            seen.add(marker)
            try:
                for key, item in list(value.items())[: self.max_collection_items]:
                    if type(key) is str and self._is_secret_key(key.casefold()):
                        return True
                    if depth < self.max_depth and self._contains_secret(
                        item,
                        depth=depth + 1,
                        seen=seen,
                    ):
                        return True
            finally:
                seen.remove(marker)
        elif isinstance(value, (list, tuple)):
            marker = id(value)
            if marker in seen:
                return False
            seen.add(marker)
            try:
                return depth < self.max_depth and any(
                    self._contains_secret(item, depth=depth + 1, seen=seen)
                    for item in list(value)[: self.max_collection_items]
                )
            finally:
                seen.remove(marker)
        return False

    @staticmethod
    def _is_secret_key(key: str) -> bool:
        return any(part in key for part in _SECRET_KEY_PARTS)


__all__ = [
    "PayloadRejected",
    "PrivacyPolicy",
    "RedactionResult",
]
