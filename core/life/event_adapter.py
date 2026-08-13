"""Deterministic mapping from existing runtime events to LifeEvent v1."""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.events import Event

from .contracts import LifeEvent, PrivacyClass, RetentionClass
from .privacy import PayloadRejected, PrivacyPolicy


@dataclass(frozen=True)
class _Rule:
    life_type: str
    fields: frozenset[str]
    privacy: PrivacyClass
    retention: RetentionClass


_TOOL_FIELDS = frozenset(
    {
        "tool",
        "category",
        "success",
        "duration_ms",
        "confirmed",
        "session_id",
        "request_id",
    }
)
_RULES: dict[str, _Rule] = {
    "life.identity.created": _Rule(
        "life.identity.created",
        frozenset({"version"}),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.CONTINUITY,
    ),
    "life.instance.created": _Rule(
        "life.instance.created",
        frozenset({"generation", "fork_pending_review"}),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.CONTINUITY,
    ),
    "runtime.created": _Rule(
        "life.runtime.created",
        frozenset(),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.CONTINUITY,
    ),
    "runtime.status": _Rule(
        "life.runtime.status",
        frozenset({"status"}),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.OPERATIONAL,
    ),
    "subsystem.registered": _Rule(
        "life.subsystem.registered",
        frozenset({"name"}),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.OPERATIONAL,
    ),
    "event_store.registered": _Rule(
        "life.event_store.registered",
        frozenset(),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.OPERATIONAL,
    ),
    "thinking.started": _Rule(
        "life.thinking.started",
        frozenset({"request_id"}),
        PrivacyClass.USER_PRIVATE,
        RetentionClass.SESSION,
    ),
    "thinking.completed": _Rule(
        "life.thinking.completed",
        frozenset({"request_id", "duration_ms", "success"}),
        PrivacyClass.USER_PRIVATE,
        RetentionClass.SESSION,
    ),
    "tool.started": _Rule(
        "life.tool.started",
        _TOOL_FIELDS,
        PrivacyClass.RESTRICTED_SYSTEM,
        RetentionClass.OPERATIONAL,
    ),
    "tool.completed": _Rule(
        "life.tool.completed",
        _TOOL_FIELDS,
        PrivacyClass.RESTRICTED_SYSTEM,
        RetentionClass.OPERATIONAL,
    ),
    "tool.failed": _Rule(
        "life.tool.failed",
        _TOOL_FIELDS,
        PrivacyClass.RESTRICTED_SYSTEM,
        RetentionClass.AUDIT,
    ),
    "approval.requested": _Rule(
        "life.approval.requested",
        frozenset({"approval_id", "tool", "session_id", "request_id"}),
        PrivacyClass.RESTRICTED_SYSTEM,
        RetentionClass.AUDIT,
    ),
    "approval.required": _Rule(
        "life.approval.required",
        frozenset({"approval_id", "tool", "session_id", "request_id"}),
        PrivacyClass.RESTRICTED_SYSTEM,
        RetentionClass.AUDIT,
    ),
    "approval.resolved": _Rule(
        "life.approval.resolved",
        frozenset(
            {"approval_id", "confirmed", "session_id", "request_id"}
        ),
        PrivacyClass.RESTRICTED_SYSTEM,
        RetentionClass.AUDIT,
    ),
    "health.degraded": _Rule(
        "life.health.degraded",
        frozenset({"components", "reason_codes", "degradation_level"}),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.OPERATIONAL,
    ),
    "health.recovered": _Rule(
        "life.health.recovered",
        frozenset(),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.OPERATIONAL,
    ),
    "life.recovery.required": _Rule(
        "life.recovery.required",
        frozenset({"reason_codes"}),
        PrivacyClass.LOCAL_INTERNAL,
        RetentionClass.CONTINUITY,
    ),
    "client.offline": _Rule(
        "life.client.offline",
        frozenset(),
        PrivacyClass.PUBLIC_SURFACE,
        RetentionClass.EPHEMERAL,
    ),
    "client.reconnected": _Rule(
        "life.client.reconnected",
        frozenset(),
        PrivacyClass.PUBLIC_SURFACE,
        RetentionClass.EPHEMERAL,
    ),
    "voice.listening": _Rule(
        "life.voice.listening",
        frozenset({"session_id", "request_id"}),
        PrivacyClass.USER_PRIVATE,
        RetentionClass.SESSION,
    ),
    "voice.speaking": _Rule(
        "life.voice.speaking",
        frozenset({"session_id", "request_id"}),
        PrivacyClass.USER_PRIVATE,
        RetentionClass.SESSION,
    ),
    "response.speaking": _Rule(
        "life.response.speaking",
        frozenset({"session_id", "request_id"}),
        PrivacyClass.USER_PRIVATE,
        RetentionClass.SESSION,
    ),
}

_IGNORED_EVENTS = frozenset(
    {
        "thinking.delta",
        "agent_delta.appended",
        "response.delta",
        "voice.audio.level",
    }
)
_AGENT_RUN_SUFFIXES = frozenset(
    {"created", "updated", "completed", "failed", "cancelled"}
)
_REQUEST_SUFFIXES = frozenset(
    {
        "accepted",
        "cancellation_pending",
        "cancelled",
        "completed",
        "failed",
    }
)
_ACTIVITY_NAMES = frozenset(
    {
        "understanding",
        "thinking",
        "tool_started",
        "tool_completed",
        "blocked",
        "error",
    }
)
_ID_FIELDS = frozenset(
    {
        "event_id",
        "source_event_id",
        "session_id",
        "request_id",
        "correlation_id",
        "causation_id",
    }
)


class LifeEventAdapter:
    """Map existing events without forwarding unclassified payload fields."""

    def __init__(
        self,
        *,
        identity_id: str,
        instance_id: str,
        now,
        privacy_policy: PrivacyPolicy | None = None,
    ) -> None:
        self.identity_id = self._safe_id(identity_id, "identity_id")
        self.instance_id = self._safe_id(instance_id, "instance_id")
        self._now = now
        self.privacy_policy = privacy_policy or PrivacyPolicy()

    def map(self, event: Any) -> list[LifeEvent]:
        if not isinstance(event, Event):
            return [self._invalid_event("invalid_source_event")]
        event_type = event.type if type(event.type) is str else ""
        if event_type in _IGNORED_EVENTS:
            return []
        try:
            metadata = self._metadata(event)
        except ValueError:
            return [self._invalid_event("invalid_source_metadata")]

        try:
            privacy, retention = self.privacy_policy.classify(event_type, event.payload)
        except Exception:
            return [self._invalid_event("payload_classification_failed")]
        if privacy in {PrivacyClass.SECRET, PrivacyClass.BIOMETRIC}:
            return [
                self._build_event(
                    life_type=(
                        "life.observation.secret"
                        if privacy is PrivacyClass.SECRET
                        else "life.observation.biometric"
                    ),
                    payload={"diagnostic": "payload_rejected"},
                    privacy=privacy,
                    retention=RetentionClass.NEVER_PERSIST,
                    metadata=metadata,
                    confidence=0.0,
                    redaction_summary=("payload_rejected",),
                    source_type=event_type,
                )
            ]
        try:
            self.privacy_policy.validate_structure(event.payload)
        except (PayloadRejected, TypeError, ValueError):
            return [
                self._build_event(
                    life_type="life.observation.invalid",
                    payload={"diagnostic": "payload_rejected"},
                    privacy=privacy,
                    retention=retention,
                    metadata=metadata,
                    confidence=0.0,
                    redaction_summary=("payload_rejected",),
                    source_type=event_type,
                )
            ]

        rule = self._rule_for(event_type)
        if rule is None:
            rule = _Rule(
                "life.observation.unknown",
                frozenset(),
                PrivacyClass.LOCAL_INTERNAL,
                RetentionClass.OPERATIONAL,
            )
            unknown_payload = {"source_event_type": self._bounded_type(event_type)}
            return [
                self._build_event(
                    life_type=rule.life_type,
                    payload=unknown_payload,
                    privacy=rule.privacy,
                    retention=rule.retention,
                    metadata=metadata,
                    confidence=0.5,
                    redaction_summary=("unknown_event_payload_removed",),
                    source_type=event_type,
                )
            ]

        try:
            redacted = self.privacy_policy.redact_allowlisted(
                event.payload,
                allowed_fields=rule.fields,
            )
            payload = redacted.payload
            summary = redacted.summary
            confidence = 1.0
        except (PayloadRejected, TypeError, ValueError):
            payload = {"diagnostic": "payload_rejected"}
            summary = ("payload_rejected",)
            confidence = 0.0

        session_id = self._payload_id(event.payload, "session_id")
        request_id = self._payload_id(event.payload, "request_id")
        for field in ("session_id", "request_id"):
            payload.pop(field, None)
        return [
            self._build_event(
                life_type=rule.life_type,
                payload=payload,
                privacy=rule.privacy,
                retention=rule.retention,
                metadata=metadata | {
                    "session_id": session_id,
                    "request_id": request_id,
                },
                confidence=confidence,
                redaction_summary=summary,
                source_type=event_type,
            )
        ]

    def _rule_for(self, event_type: str) -> _Rule | None:
        direct = _RULES.get(event_type)
        if direct is not None:
            return direct
        if event_type.startswith("agent_run."):
            suffix = event_type.removeprefix("agent_run.")
            if suffix in _AGENT_RUN_SUFFIXES:
                return _Rule(
                    f"life.{event_type}",
                    frozenset({"run_id", "status", "parent_run_id"}),
                    PrivacyClass.LOCAL_INTERNAL,
                    RetentionClass.OPERATIONAL,
                )
        if event_type.startswith("request."):
            suffix = event_type.removeprefix("request.")
            if suffix in _REQUEST_SUFFIXES:
                return _Rule(
                    f"life.{event_type}",
                    frozenset(
                        {
                            "session_id",
                            "request_id",
                            "interaction_mode",
                            "replaces_request_id",
                            "recovery_action",
                            "route",
                            "code",
                        }
                    ),
                    PrivacyClass.USER_PRIVATE,
                    (
                        RetentionClass.CONTINUITY
                        if suffix in {"completed", "failed", "cancelled"}
                        else RetentionClass.SESSION
                    ),
                )
        if event_type.startswith("activity."):
            activity = event_type.removeprefix("activity.")
            if activity in _ACTIVITY_NAMES:
                return _Rule(
                    f"life.{event_type}",
                    frozenset({"session_id", "request_id", "tool", "success"}),
                    PrivacyClass.USER_PRIVATE,
                    RetentionClass.SESSION,
                )
        if event_type.startswith("memory."):
            return _Rule(
                f"life.{event_type}",
                frozenset({"candidate_id", "status", "kind", "count"}),
                PrivacyClass.LOCAL_INTERNAL,
                RetentionClass.OPERATIONAL,
            )
        if event_type.startswith("skill."):
            return _Rule(
                f"life.{event_type}",
                frozenset({"name", "status", "source", "version"}),
                PrivacyClass.LOCAL_INTERNAL,
                RetentionClass.OPERATIONAL,
            )
        if event_type.startswith("evolution."):
            return _Rule(
                f"life.{event_type}",
                frozenset({"candidate_id", "status", "ok", "rolled_back"}),
                PrivacyClass.LOCAL_INTERNAL,
                RetentionClass.OPERATIONAL,
            )
        return None

    def _metadata(self, event: Event) -> dict[str, Any]:
        source_event_id = self._safe_id(event.id, "source_event_id")
        source = self._safe_id(event.source, "source")
        correlation_id = self._safe_optional_id(event.correlation_id, "correlation_id")
        causation_id = self._safe_optional_id(event.causation_id, "causation_id")
        if type(event.sequence) is not int or event.sequence < 0:
            raise ValueError("invalid sequence")
        timestamp = self._timestamp(event.timestamp)
        metadata = {
            "source_event_id": source_event_id,
            "source": source,
            "timestamp": timestamp,
            "sequence": event.sequence,
            "correlation_id": correlation_id,
            "causation_id": causation_id,
            "session_id": None,
            "request_id": None,
        }
        if source == "conversation" and isinstance(event.payload, Mapping):
            bridge_source_id = self._safe_optional_id(
                event.payload.get("source_event_id"),
                "source_event_id",
            )
            bridge_session_id = self._safe_optional_id(
                event.payload.get("session_id"),
                "session_id",
            )
            bridge_sequence = event.payload.get("source_sequence")
            bridge_domain = event.payload.get("source_sequence_domain")
            expected_domain = (
                f"conversation_store:{bridge_session_id}"
                if bridge_session_id is not None
                else None
            )
            if (
                bridge_source_id is not None
                and causation_id == bridge_source_id
                and type(bridge_sequence) is int
                and bridge_sequence >= 0
                and bridge_domain == expected_domain
            ):
                metadata.update(
                    {
                        "source_event_id": bridge_source_id,
                        "source_sequence": bridge_sequence,
                        "source_sequence_domain": bridge_domain,
                        "transport_event_id": source_event_id,
                        "transport_sequence": event.sequence,
                    }
                )
        return metadata

    def _build_event(
        self,
        *,
        life_type: str,
        payload: Mapping[str, Any],
        privacy: PrivacyClass,
        retention: RetentionClass,
        metadata: Mapping[str, Any],
        confidence: float,
        redaction_summary: tuple[str, ...],
        source_type: str,
    ) -> LifeEvent:
        source_event_id = metadata.get("source_event_id")
        stable_source = source_event_id or self._timestamp(self._now())
        event_id = hashlib.sha256(
            f"life:{self.instance_id}:{stable_source}:{life_type}".encode("utf-8")
        ).hexdigest()
        provenance = {
            "mapper": "life_event_adapter_v1",
            "source_event_type": self._bounded_type(source_type),
        }
        for field in (
            "source_sequence",
            "source_sequence_domain",
            "transport_event_id",
            "transport_sequence",
        ):
            if field in metadata:
                provenance[field] = metadata[field]
        return LifeEvent(
            schema_version=1,
            event_id=event_id,
            event_type=life_type,
            timestamp_utc=metadata.get("timestamp") or self._timestamp(self._now()),
            monotonic_offset_ms=0,
            source=metadata.get("source") or "life_adapter",
            source_event_id=source_event_id,
            session_id=metadata.get("session_id"),
            request_id=metadata.get("request_id"),
            correlation_id=metadata.get("correlation_id"),
            causation_id=metadata.get("causation_id"),
            sequence=metadata.get("sequence", 0),
            identity_id=self.identity_id,
            instance_id=self.instance_id,
            payload=dict(payload),
            privacy_class=privacy,
            retention_class=retention,
            confidence=confidence,
            provenance=provenance,
            redaction_summary=redaction_summary,
        )

    def _invalid_event(self, reason: str) -> LifeEvent:
        return self._build_event(
            life_type="life.observation.invalid",
            payload={"diagnostic": reason},
            privacy=PrivacyClass.LOCAL_INTERNAL,
            retention=RetentionClass.OPERATIONAL,
            metadata={
                "source_event_id": None,
                "source": "life_adapter",
                "timestamp": self._timestamp(self._now()),
                "sequence": 0,
                "correlation_id": None,
                "causation_id": None,
                "session_id": None,
                "request_id": None,
            },
            confidence=0.0,
            redaction_summary=(reason,),
            source_type="invalid",
        )

    @staticmethod
    def _payload_id(payload: Any, field: str) -> str | None:
        if not isinstance(payload, Mapping):
            return None
        value = payload.get(field)
        try:
            return LifeEventAdapter._safe_optional_id(value, field)
        except ValueError:
            return None

    @staticmethod
    def _safe_id(value: Any, field_name: str) -> str:
        if type(value) is not str or not value or len(value) > 256:
            raise ValueError(f"invalid {field_name}")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"invalid {field_name}") from exc
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise ValueError(f"invalid {field_name}")
        return value

    @staticmethod
    def _safe_optional_id(value: Any, field_name: str) -> str | None:
        if value is None or value == "":
            return None
        return LifeEventAdapter._safe_id(value, field_name)

    @staticmethod
    def _timestamp(value: Any) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("invalid timestamp")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("invalid timestamp")
        try:
            return datetime.fromtimestamp(numeric, timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
        except (OSError, OverflowError, ValueError) as exc:
            raise ValueError("invalid timestamp") from exc

    @staticmethod
    def _bounded_type(value: Any) -> str:
        if type(value) is not str or not value:
            return "unknown"
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return "unknown"
        return value[:128]


__all__ = ["LifeEventAdapter"]
