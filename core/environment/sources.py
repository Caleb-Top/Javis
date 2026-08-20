"""Source adapter that emits only validated L4 observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
import uuid

from core.environment.contracts import (
    EnvironmentObservationV1,
    SOURCE_MAX_TTL_SECONDS,
    SOURCE_RETENTION_CLASS,
    SourceKind,
)
from core.environment.minimizer import EnvironmentMinimizer
from core.life.contracts import PrivacyClass


_SOURCE_PRIVACY = {
    SourceKind.FOREGROUND_APP: PrivacyClass.USER_PRIVATE,
    SourceKind.PROCESS_HEALTH: PrivacyClass.RESTRICTED_SYSTEM,
    SourceKind.DEVICE_HEALTH: PrivacyClass.RESTRICTED_SYSTEM,
    SourceKind.WORKSPACE_METADATA: PrivacyClass.USER_PRIVATE,
    SourceKind.SCREEN_OCR: PrivacyClass.USER_PRIVATE,
    SourceKind.SCREEN_OBJECT: PrivacyClass.USER_PRIVATE,
    SourceKind.USER_DEFINED_ALIAS: PrivacyClass.USER_PRIVATE,
}


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be an aware datetime")
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    canonical = value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
    return f"{canonical[:-3]}Z"


@dataclass(frozen=True, slots=True)
class EnvironmentSourceEvent:
    source_event_id: str
    source_kind: SourceKind
    subject_kind: str
    subject_key: str
    payload: Mapping[str, Any]
    observed_at: datetime
    sequence: int
    confidence: float = 1.0


class EnvironmentSourceAdapter:
    def __init__(
        self,
        runtime_boot_id: str,
        *,
        minimizer: EnvironmentMinimizer | None = None,
    ) -> None:
        self._runtime_boot_id = runtime_boot_id
        self._minimizer = minimizer or EnvironmentMinimizer()

    def adapt(
        self,
        event: EnvironmentSourceEvent,
        *,
        grant_id_hash: str,
    ) -> EnvironmentObservationV1:
        if not isinstance(event, EnvironmentSourceEvent):
            raise TypeError("event must be an EnvironmentSourceEvent")
        kind = event.source_kind
        if not isinstance(kind, SourceKind):
            kind = SourceKind(kind)
        maximum_ttl = SOURCE_MAX_TTL_SECONDS[kind]
        if maximum_ttl is None:
            maximum_ttl = 365 * 24 * 60 * 60
        observed_at = _utc(event.observed_at, "observed_at")
        attributes = self._minimizer.minimize(kind, event.payload)

        return EnvironmentObservationV1(
            schema_version=1,
            observation_id=uuid.uuid4().hex,
            runtime_boot_id=self._runtime_boot_id,
            source_event_id=event.source_event_id,
            source_kind=kind,
            subject_kind=event.subject_kind,
            subject_key=event.subject_key,
            attributes=attributes,
            observed_at_utc=_wire_time(observed_at),
            valid_until_utc=_wire_time(observed_at + timedelta(seconds=maximum_ttl)),
            sequence=event.sequence,
            confidence=event.confidence,
            privacy_class=_SOURCE_PRIVACY[kind],
            retention_class=SOURCE_RETENTION_CLASS[kind],
            grant_id_hash=grant_id_hash,
            provenance={
                "adapter": "l4_minimizer",
                "source_kind": kind.value,
            },
        )


__all__ = ["EnvironmentSourceAdapter", "EnvironmentSourceEvent"]
