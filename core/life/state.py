"""Deterministic minimal lifecycle state and read-only snapshot projection."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    HealthSummary,
    IdentityConstitution,
    IdentitySummary,
    InstanceRecord,
    InstanceSummary,
    LifeCycleState,
    LifeSnapshot,
    canonical_content_hash,
)


ALLOWED_LIFECYCLE_TRANSITIONS: dict[LifeCycleState, frozenset[LifeCycleState]] = {
    LifeCycleState.BOOTING: frozenset(
        {LifeCycleState.AWAKE, LifeCycleState.DEGRADED, LifeCycleState.STOPPING}
    ),
    LifeCycleState.AWAKE: frozenset(
        {
            LifeCycleState.QUIET,
            LifeCycleState.ENGAGED,
            LifeCycleState.DEGRADED,
            LifeCycleState.STOPPING,
        }
    ),
    LifeCycleState.QUIET: frozenset(
        {
            LifeCycleState.ENGAGED,
            LifeCycleState.DEGRADED,
            LifeCycleState.STOPPING,
        }
    ),
    LifeCycleState.ENGAGED: frozenset(
        {
            LifeCycleState.QUIET,
            LifeCycleState.DEGRADED,
            LifeCycleState.STOPPING,
        }
    ),
    LifeCycleState.DEGRADED: frozenset(
        {
            LifeCycleState.AWAKE,
            LifeCycleState.RECOVERING,
            LifeCycleState.STOPPING,
        }
    ),
    LifeCycleState.RECOVERING: frozenset(
        {
            LifeCycleState.AWAKE,
            LifeCycleState.DEGRADED,
            LifeCycleState.STOPPING,
        }
    ),
    LifeCycleState.STOPPING: frozenset(),
    LifeCycleState.OFFLINE: frozenset(
        {LifeCycleState.AWAKE, LifeCycleState.DEGRADED}
    ),
}

_REQUEST_ACTIVITY_BY_EVENT = {
    "request.accepted": "attention",
    "voice.listening": "listening",
    "activity.understanding": "thinking",
    "activity.thinking": "thinking",
    "thinking.started": "thinking",
    "voice.speaking": "speaking",
    "response.speaking": "speaking",
    "tool.started": "executing",
    "activity.tool_started": "executing",
    "approval.requested": "blocked",
    "activity.blocked": "blocked",
    "activity.error": "error",
    "request.failed": "error",
}
_TERMINAL_EVENTS = frozenset(
    {
        "request.completed",
        "request.cancelled",
        "request.failed",
    }
)
_KNOWN_EVENTS = frozenset(_REQUEST_ACTIVITY_BY_EVENT) | _TERMINAL_EVENTS | frozenset(
    {
        "runtime.ready",
        "runtime.quiet",
        "runtime.stopping",
        "health.degraded",
        "health.recovered",
        "life.recovery.required",
        "client.offline",
        "client.reconnected",
    }
)


class InvalidLifeTransition(RuntimeError):
    """Raised when an event requests a forbidden lifecycle transition."""


def _epoch(value: Any, *, field_name: str = "now") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite epoch number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be a finite epoch number")
    return numeric


def _timestamp(value: float) -> str:
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError("now is outside the supported epoch range") from exc


def _id(value: Any, *, field_name: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value or len(value) > 256:
        raise ValueError(f"{field_name} must be a non-empty ID of at most 256 characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} must be valid UTF-8") from exc
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _string_collection(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a string collection")
    result: list[str] = []
    for item in value:
        if type(item) is not str or not item:
            raise ValueError(f"{field_name} must contain non-empty strings")
        result.append(item)
    return tuple(result)


def _identity_summary(
    identity: IdentityConstitution | None,
    identity_id: str | None,
) -> IdentitySummary:
    if identity is not None:
        return IdentitySummary(
            identity_id=identity.identity_id,
            name=identity.name,
            kind=identity.kind,
            relationship_role=identity.relationship_role,
            version=identity.version,
            content_hash=identity.content_hash,
        )
    resolved_id = _id(identity_id, field_name="identity_id")
    unresolved_hash = canonical_content_hash(
        {"identity_id": resolved_id, "resolution": "unresolved"}
    )
    return IdentitySummary(
        identity_id=resolved_id,
        name="Javis",
        kind="unresolved",
        relationship_role="unresolved",
        version=0,
        content_hash=unresolved_hash,
    )


def _instance_summary(
    instance: InstanceRecord | None,
    instance_id: str | None,
) -> InstanceSummary:
    if instance is not None:
        return InstanceSummary(
            lineage_id=instance.lineage_id,
            instance_id=instance.instance_id,
            parent_instance_id=instance.parent_instance_id,
            generation=instance.generation,
            fork_pending_review=instance.fork_pending_review,
        )
    return InstanceSummary(
        lineage_id="unresolved",
        instance_id=_id(instance_id, field_name="instance_id"),
        parent_instance_id=None,
        generation=0,
        fork_pending_review=True,
    )


class MinimalLifeStateMachine:
    """Single-writer L0 lifecycle reducer with stale-terminal protection."""

    def __init__(
        self,
        *,
        identity: IdentityConstitution | None = None,
        instance: InstanceRecord | None = None,
        identity_id: str | None = None,
        instance_id: str | None = None,
        initial_state: LifeCycleState | str = LifeCycleState.QUIET,
        now: float = 0.0,
    ) -> None:
        if identity is not None and identity_id not in (None, identity.identity_id):
            raise ValueError("identity_id does not match identity constitution")
        if instance is not None and instance_id not in (None, instance.instance_id):
            raise ValueError("instance_id does not match instance record")
        if identity is not None and instance is not None:
            if identity.identity_id != instance.identity_id:
                raise ValueError("identity and instance belong to different identities")
        self._identity = _identity_summary(identity, identity_id)
        self._instance = _instance_summary(instance, instance_id)
        try:
            state = (
                initial_state
                if isinstance(initial_state, LifeCycleState)
                else LifeCycleState(initial_state)
            )
        except ValueError as exc:
            raise ValueError(f"unknown initial lifecycle state: {initial_state!r}") from exc
        if state is LifeCycleState.OFFLINE:
            raise ValueError("offline is a client projection, not a backend initial state")
        initial_epoch = _epoch(now)
        self._backend_state = state
        self._client_offline = False
        self._revision = 0
        self._active_session_id: str | None = None
        self._active_request_id: str | None = None
        self._activity = self._initial_activity(state)
        self._health = HealthSummary(
            status="healthy",
            degraded_components=(),
            reason_codes=(),
        )
        self._degradation_level = 0
        self._recovery_required = False
        self._last_event_id: str | None = None
        self._last_sequence = 0
        self._has_sequence = False
        self._updated_epoch = initial_epoch
        self._explanation = "initial_state"

    @property
    def backend_lifecycle_state(self) -> LifeCycleState:
        return self._backend_state

    def apply(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None,
        now: float,
    ) -> bool:
        if type(event_type) is not str or event_type not in _KNOWN_EVENTS:
            raise ValueError(f"unsupported life state event: {event_type!r}")
        if payload is None:
            event_payload: Mapping[str, Any] = {}
        elif isinstance(payload, Mapping):
            event_payload = payload
        else:
            raise ValueError("life state payload must be an object")
        event_epoch = _epoch(now)
        event_id = _id(
            event_payload.get("event_id"),
            field_name="event_id",
            optional=True,
        )
        sequence_value = event_payload.get("sequence")
        sequence: int | None = None
        if sequence_value is not None:
            if type(sequence_value) is not int or sequence_value < 0:
                raise ValueError("sequence must be a non-negative integer")
            sequence = sequence_value
        if sequence is not None and self._has_sequence and sequence <= self._last_sequence:
            return False
        if event_id is not None and event_id == self._last_event_id:
            return False
        if self._backend_state is LifeCycleState.STOPPING and event_type not in {
            "runtime.stopping",
            "client.offline",
            "client.reconnected",
        }:
            raise InvalidLifeTransition(
                f"illegal lifecycle transition from stopping for {event_type}"
            )

        candidate = self._candidate(event_type, event_payload)
        if candidate is None:
            return False
        target_state = candidate["backend_state"]
        if target_state != self._backend_state:
            self._require_transition(self._backend_state, target_state)

        self._backend_state = target_state
        self._client_offline = candidate["client_offline"]
        self._active_session_id = candidate["active_session_id"]
        self._active_request_id = candidate["active_request_id"]
        self._activity = candidate["activity"]
        self._health = candidate["health"]
        self._degradation_level = candidate["degradation_level"]
        self._recovery_required = candidate["recovery_required"]
        self._revision += 1
        self._last_event_id = event_id
        if sequence is not None:
            self._last_sequence = sequence
            self._has_sequence = True
        self._updated_epoch = max(self._updated_epoch, event_epoch)
        self._explanation = event_type
        return True

    def snapshot(self) -> LifeSnapshot:
        lifecycle_state = (
            LifeCycleState.OFFLINE if self._client_offline else self._backend_state
        )
        activity = "offline" if self._client_offline else self._activity
        return LifeSnapshot(
            schema_version=1,
            revision=self._revision,
            identity=self._identity,
            instance=self._instance,
            lifecycle_state=lifecycle_state,
            active_session_id=self._active_session_id,
            active_request_id=self._active_request_id,
            activity=activity,
            health=self._health,
            degradation_level=self._degradation_level,
            recovery_required=self._recovery_required,
            last_event_id=self._last_event_id,
            last_sequence=self._last_sequence,
            updated_at=_timestamp(self._updated_epoch),
            explanation=self._explanation,
        )

    def _candidate(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        candidate = {
            "backend_state": self._backend_state,
            "client_offline": self._client_offline,
            "active_session_id": self._active_session_id,
            "active_request_id": self._active_request_id,
            "activity": self._activity,
            "health": self._health,
            "degradation_level": self._degradation_level,
            "recovery_required": self._recovery_required,
        }

        if event_type in _TERMINAL_EVENTS:
            request_id = _id(payload.get("request_id"), field_name="request_id")
            if request_id != self._active_request_id:
                return None
            candidate["active_request_id"] = None
            if event_type == "request.failed":
                candidate["activity"] = "error"
                if self._backend_state not in {
                    LifeCycleState.DEGRADED,
                    LifeCycleState.RECOVERING,
                }:
                    candidate["backend_state"] = LifeCycleState.DEGRADED
                candidate["health"] = HealthSummary(
                    status="degraded",
                    degraded_components=(),
                    reason_codes=("request_failed",),
                )
                candidate["degradation_level"] = max(self._degradation_level, 1)
                return candidate
            candidate["activity"] = "quiet"
            if self._backend_state is LifeCycleState.ENGAGED:
                candidate["backend_state"] = LifeCycleState.QUIET
            return candidate

        if event_type in _REQUEST_ACTIVITY_BY_EVENT:
            request_id = _id(payload.get("request_id"), field_name="request_id")
            session_id = _id(
                payload.get("session_id"),
                field_name="session_id",
                optional=True,
            )
            candidate["active_request_id"] = request_id
            if session_id is not None:
                candidate["active_session_id"] = session_id
            candidate["activity"] = _REQUEST_ACTIVITY_BY_EVENT[event_type]
            if event_type in {"activity.error", "request.failed"}:
                if self._backend_state not in {
                    LifeCycleState.DEGRADED,
                    LifeCycleState.RECOVERING,
                }:
                    candidate["backend_state"] = LifeCycleState.DEGRADED
                candidate["health"] = HealthSummary(
                    status="degraded",
                    degraded_components=(),
                    reason_codes=("request_error",),
                )
                candidate["degradation_level"] = max(self._degradation_level, 1)
            elif self._backend_state in {
                LifeCycleState.AWAKE,
                LifeCycleState.QUIET,
                LifeCycleState.ENGAGED,
            }:
                candidate["backend_state"] = LifeCycleState.ENGAGED
            return candidate

        if event_type == "runtime.ready":
            candidate["backend_state"] = LifeCycleState.AWAKE
            candidate["activity"] = "attention"
        elif event_type == "runtime.quiet":
            candidate["backend_state"] = LifeCycleState.QUIET
            candidate["activity"] = "quiet"
            candidate["active_request_id"] = None
        elif event_type == "runtime.stopping":
            candidate["backend_state"] = LifeCycleState.STOPPING
            candidate["activity"] = "quiet"
            candidate["active_request_id"] = None
        elif event_type == "health.degraded":
            components = _string_collection(
                payload.get("components"),
                field_name="components",
            )
            reasons = _string_collection(
                payload.get("reason_codes"),
                field_name="reason_codes",
            )
            level = payload.get("degradation_level", 1)
            if type(level) is not int or level < 1:
                raise ValueError("degradation_level must be a positive integer")
            candidate["backend_state"] = LifeCycleState.DEGRADED
            candidate["activity"] = "error"
            candidate["health"] = HealthSummary(
                status="degraded",
                degraded_components=components,
                reason_codes=reasons,
            )
            candidate["degradation_level"] = level
        elif event_type == "life.recovery.required":
            reasons = _string_collection(
                payload.get("reason_codes"),
                field_name="reason_codes",
            )
            candidate["backend_state"] = LifeCycleState.RECOVERING
            candidate["activity"] = "blocked"
            candidate["health"] = HealthSummary(
                status="recovering",
                degraded_components=self._health.degraded_components,
                reason_codes=reasons or ("recovery_required",),
            )
            candidate["degradation_level"] = max(self._degradation_level, 1)
            candidate["recovery_required"] = True
        elif event_type == "health.recovered":
            candidate["backend_state"] = LifeCycleState.AWAKE
            candidate["activity"] = "attention"
            candidate["health"] = HealthSummary(
                status="healthy",
                degraded_components=(),
                reason_codes=(),
            )
            candidate["degradation_level"] = 0
            candidate["recovery_required"] = False
        elif event_type == "client.offline":
            candidate["client_offline"] = True
        elif event_type == "client.reconnected":
            candidate["client_offline"] = False
        return candidate

    @staticmethod
    def _initial_activity(state: LifeCycleState) -> str:
        if state is LifeCycleState.QUIET:
            return "quiet"
        if state in {LifeCycleState.DEGRADED, LifeCycleState.RECOVERING}:
            return "blocked"
        return "attention"

    @staticmethod
    def _require_transition(
        current: LifeCycleState,
        target: LifeCycleState,
    ) -> None:
        if target not in ALLOWED_LIFECYCLE_TRANSITIONS[current]:
            raise InvalidLifeTransition(
                f"illegal lifecycle transition {current.value} -> {target.value}"
            )


__all__ = [
    "ALLOWED_LIFECYCLE_TRANSITIONS",
    "InvalidLifeTransition",
    "MinimalLifeStateMachine",
]
