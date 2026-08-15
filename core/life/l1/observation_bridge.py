"""Privacy-minimal projection of governed runtime facts into L1 observations."""

from __future__ import annotations

import hashlib
import math
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.events import Event
from core.life.contracts import PrivacyClass, RetentionClass

from .clock import Clock, ClockReading, SystemClock, format_utc_milliseconds
from .contracts import (
    InputModality,
    InputProvenance,
    InputVerification,
    LifeObservation,
    ObservationKind,
    ObservationOutcome,
    PlaybackLifecycleEvent,
    PlaybackOutcome,
    RiskLevel,
)


class ProjectionRejected(ValueError):
    """A recognized source failed the governed metadata contract."""


@dataclass(frozen=True)
class _Envelope:
    event_id: str
    event_type: str
    payload: Mapping[str, Any]
    source: str
    timestamp: Any
    monotonic_offset_ms: int | None
    sequence: int
    session_id: str | None
    request_id: str | None
    correlation_id: str | None
    causation_id: str | None


_CONVERSATION_RULES = {
    "request.accepted": (
        ObservationKind.REQUEST_STARTED,
        ObservationOutcome.STARTED,
    ),
    "activity.understanding": (
        ObservationKind.REQUEST_ACTIVITY,
        ObservationOutcome.NONE,
    ),
    "activity.thinking": (
        ObservationKind.REQUEST_ACTIVITY,
        ObservationOutcome.NONE,
    ),
    "activity.blocked": (
        ObservationKind.REQUEST_ACTIVITY,
        ObservationOutcome.NONE,
    ),
    "activity.error": (
        ObservationKind.REQUEST_ACTIVITY,
        ObservationOutcome.NONE,
    ),
    "activity.tool_started": (
        ObservationKind.TOOL_STARTED,
        ObservationOutcome.STARTED,
    ),
    "activity.tool_completed": (
        ObservationKind.TOOL_COMPLETED,
        ObservationOutcome.COMPLETED,
    ),
    "approval.required": (
        ObservationKind.APPROVAL_REQUIRED,
        ObservationOutcome.NONE,
    ),
    "approval.resolved": (
        ObservationKind.APPROVAL_RESOLVED,
        ObservationOutcome.APPROVED,
    ),
    "request.completed": (
        ObservationKind.REQUEST_COMPLETED,
        ObservationOutcome.COMPLETED,
    ),
    "request.failed": (
        ObservationKind.REQUEST_FAILED,
        ObservationOutcome.FAILED,
    ),
    "request.cancelled": (
        ObservationKind.REQUEST_CANCELLED,
        ObservationOutcome.CANCELLED,
    ),
    "user.invoked": (
        ObservationKind.USER_INVOKED,
        ObservationOutcome.STARTED,
    ),
    "interaction.interrupted": (
        ObservationKind.INTERACTION_INTERRUPTED,
        ObservationOutcome.INTERRUPTED,
    ),
    "goal.verified": (
        ObservationKind.GOAL_VERIFIED,
        ObservationOutcome.VERIFIED,
    ),
}

_TERMINAL_REQUEST_TYPES = frozenset(
    {"request.completed", "request.failed", "request.cancelled"}
)


class GovernedObservationBridge:
    """Create bounded observations from production-owned source envelopes.

    The bridge keeps only a bounded request-to-provenance index. It never keeps
    source payload bodies, transcript text, audio, tool arguments, or results.
    """

    def __init__(
        self,
        *,
        runtime_boot_id: str,
        clock: Clock | None = None,
        boot_monotonic_seconds: float | None = None,
        provenance_capacity: int = 2048,
        playback_lifecycle_capacity: int = 4096,
    ) -> None:
        self.runtime_boot_id = _required_id(runtime_boot_id, "runtime_boot_id")
        self._clock = clock or SystemClock()
        reading = self._read_clock()
        baseline = (
            reading.monotonic_seconds
            if boot_monotonic_seconds is None
            else _finite_non_negative(
                boot_monotonic_seconds,
                "boot_monotonic_seconds",
            )
        )
        if baseline > reading.monotonic_seconds:
            raise ValueError("boot_monotonic_seconds cannot follow current monotonic time")
        if type(provenance_capacity) is not int or not 1 <= provenance_capacity <= 65_536:
            raise ValueError("provenance_capacity must be in [1, 65536]")
        if (
            type(playback_lifecycle_capacity) is not int
            or not 1 <= playback_lifecycle_capacity <= 65_536
        ):
            raise ValueError("playback_lifecycle_capacity must be in [1, 65536]")
        self._boot_monotonic_seconds = baseline
        self._provenance_capacity = provenance_capacity
        self._playback_lifecycle_capacity = playback_lifecycle_capacity
        self._request_provenance: OrderedDict[
            tuple[str, str], InputProvenance
        ] = OrderedDict()
        self._playback_lifecycles: OrderedDict[
            tuple[str, str, int], str
        ] = OrderedDict()
        self._lock = threading.RLock()
        self._accepted = 0
        self._ignored = 0
        self._provenance_evictions = 0
        self._playback_lifecycle_evictions = 0

    def project_conversation(
        self,
        event: Event | Mapping[str, Any],
    ) -> LifeObservation | None:
        envelope = self._envelope(event, default_source="conversation")
        rule = _CONVERSATION_RULES.get(envelope.event_type)
        if rule is None:
            self._note_ignored()
            return None
        if envelope.source != "conversation":
            raise ProjectionRejected(
                "conversation observation requires conversation source"
            )
        session_id = _required_id(envelope.session_id, "session_id")
        request_id = _required_id(envelope.request_id, "request_id")
        source_event_id, sequence, sequence_domain, causation_id = (
            self._canonical_conversation_metadata(envelope, session_id)
        )
        key = (session_id, request_id)
        provenance = self._request_input_provenance(envelope, key)
        kind, outcome = rule
        if envelope.event_type == "approval.resolved":
            confirmed = envelope.payload.get("confirmed")
            if type(confirmed) is not bool:
                raise ProjectionRejected("approval.resolved requires boolean confirmed")
            outcome = (
                ObservationOutcome.APPROVED
                if confirmed
                else ObservationOutcome.DENIED
            )
        elif envelope.event_type == "activity.tool_completed":
            success = envelope.payload.get("success")
            if success is not None:
                if type(success) is not bool:
                    raise ProjectionRejected("tool completion success must be boolean")
                outcome = (
                    ObservationOutcome.COMPLETED
                    if success
                    else ObservationOutcome.FAILED
                )
        observation = self._build(
            kind=kind,
            source="conversation",
            source_event_id=source_event_id,
            source_generation=provenance.owner_generation,
            session_id=session_id,
            request_id=request_id,
            correlation_id=envelope.correlation_id or request_id,
            causation_id=causation_id,
            sequence=sequence,
            sequence_domain=sequence_domain,
            input_provenance=provenance,
            outcome=outcome,
            risk_level=self._risk(envelope.payload),
            confidence=1.0,
            privacy_class=PrivacyClass.USER_PRIVATE,
            retention_class=RetentionClass.SESSION,
            timestamp=envelope.timestamp,
            supplied_monotonic_offset_ms=envelope.monotonic_offset_ms,
        )
        with self._lock:
            if envelope.event_type == "request.accepted":
                self._remember_provenance_locked(key, provenance)
            elif envelope.event_type in _TERMINAL_REQUEST_TYPES:
                self._request_provenance.pop(key, None)
        return observation

    def project_voice(
        self,
        event: Mapping[str, Any],
        *,
        session_id: str,
        owner_generation: int,
    ) -> LifeObservation | None:
        if not isinstance(event, Mapping):
            raise ProjectionRejected("voice event must be an object")
        event_type = str(event.get("type") or "")
        voice_rule = {
            "audio.stream.ready": (
                ObservationKind.VOICE_LISTENING_STARTED,
                ObservationOutcome.STARTED,
            ),
            "audio.stream.stopped": (
                ObservationKind.VOICE_LISTENING_STOPPED,
                ObservationOutcome.COMPLETED,
            ),
            "audio.error": (
                ObservationKind.VOICE_LISTENING_STOPPED,
                ObservationOutcome.FAILED,
            ),
        }.get(event_type)
        if voice_rule is None:
            self._note_ignored()
            return None
        normalized_session = _required_id(session_id, "session_id")
        generation = _non_negative_int(owner_generation, "owner_generation")
        supplied_generation = event.get("owner_generation")
        if supplied_generation is not None:
            if _non_negative_int(
                supplied_generation,
                "event owner_generation",
            ) != generation:
                raise ProjectionRejected("voice owner generation does not match lease")
        sequence = _non_negative_int(event.get("sequence"), "sequence")
        source_event_id = _stable_id(
            "voice",
            self.runtime_boot_id,
            normalized_session,
            str(generation),
            str(sequence),
            event_type,
        )
        provenance = InputProvenance(
            modality=InputModality.VOICE,
            verification=InputVerification.UNKNOWN,
            runtime_boot_id=self.runtime_boot_id,
            source_session_id=normalized_session,
            owner_generation=generation,
            voice_sequence=None,
            voice_turn=None,
        )
        kind, outcome = voice_rule
        return self._build(
            kind=kind,
            source="voice",
            source_event_id=source_event_id,
            source_generation=generation,
            session_id=normalized_session,
            request_id=None,
            correlation_id=normalized_session,
            causation_id=None,
            sequence=sequence,
            sequence_domain=(
                f"voice:{_digest(normalized_session)[:16]}:{generation}"
            ),
            input_provenance=provenance,
            outcome=outcome,
            risk_level=RiskLevel.NONE,
            confidence=1.0,
            privacy_class=PrivacyClass.USER_PRIVATE,
            retention_class=RetentionClass.EPHEMERAL,
            timestamp=event.get("timestamp"),
            supplied_monotonic_offset_ms=event.get("monotonic_offset_ms"),
        )

    def project_playback(
        self,
        event: PlaybackLifecycleEvent | Mapping[str, Any],
    ) -> LifeObservation:
        try:
            lifecycle = (
                event
                if isinstance(event, PlaybackLifecycleEvent)
                else PlaybackLifecycleEvent.from_dict(event)
            )
        except (TypeError, ValueError) as exc:
            raise ProjectionRejected(f"invalid playback lifecycle event: {exc}") from exc
        if lifecycle.runtime_boot_id != self.runtime_boot_id:
            raise ProjectionRejected("playback runtime boot does not match bridge")
        lifecycle_key = (
            self.runtime_boot_id,
            lifecycle.playback_id,
            lifecycle.generation,
        )
        started_source_id = _playback_source_id(
            lifecycle.playback_id,
            lifecycle.generation,
            PlaybackOutcome.STARTED,
        )
        source_event_id = _playback_source_id(
            lifecycle.playback_id,
            lifecycle.generation,
            lifecycle.outcome,
        )
        if lifecycle.outcome is PlaybackOutcome.STARTED:
            kind = ObservationKind.SPEECH_STARTED
            outcome = ObservationOutcome.STARTED
            phase = 0
            causation_id = lifecycle.request_id
        else:
            kind = ObservationKind.SPEECH_STOPPED
            outcome = {
                PlaybackOutcome.COMPLETED: ObservationOutcome.COMPLETED,
                PlaybackOutcome.STOPPED: ObservationOutcome.CANCELLED,
                PlaybackOutcome.CANCELLED: ObservationOutcome.CANCELLED,
                PlaybackOutcome.FAILED: ObservationOutcome.FAILED,
            }[lifecycle.outcome]
            phase = 1
            causation_id = started_source_id
        provenance = InputProvenance(
            modality=InputModality.SYSTEM,
            verification=InputVerification.UNKNOWN,
            runtime_boot_id=self.runtime_boot_id,
            source_session_id=lifecycle.session_id,
            owner_generation=None,
            voice_sequence=None,
            voice_turn=None,
        )
        with self._lock:
            current_phase = self._playback_lifecycles.get(lifecycle_key)
            eviction_key: tuple[str, str, int] | None = None
            if lifecycle.outcome is PlaybackOutcome.STARTED:
                if current_phase == "terminal":
                    raise ProjectionRejected("playback lifecycle is already terminal")
                if current_phase == "started":
                    raise ProjectionRejected("duplicate STARTED playback lifecycle")
                if len(self._playback_lifecycles) >= self._playback_lifecycle_capacity:
                    eviction_key = next(
                        (
                            key
                            for key, state in self._playback_lifecycles.items()
                            if state == "terminal"
                        ),
                        None,
                    )
                    if eviction_key is None:
                        raise ProjectionRejected("playback lifecycle capacity exhausted")
                next_phase = "started"
            else:
                if current_phase == "terminal":
                    raise ProjectionRejected("playback lifecycle is already terminal")
                if current_phase != "started":
                    raise ProjectionRejected("terminal playback lifecycle requires STARTED")
                next_phase = "terminal"

            observation = self._build(
                kind=kind,
                source="playback",
                source_event_id=source_event_id,
                source_generation=lifecycle.generation,
                session_id=lifecycle.session_id,
                request_id=lifecycle.request_id,
                correlation_id=lifecycle.request_id,
                causation_id=causation_id,
                sequence=lifecycle.generation * 2 + phase,
                sequence_domain=f"playback:{self.runtime_boot_id}",
                input_provenance=provenance,
                outcome=outcome,
                risk_level=RiskLevel.NONE,
                confidence=1.0,
                privacy_class=PrivacyClass.LOCAL_INTERNAL,
                retention_class=RetentionClass.EPHEMERAL,
                timestamp=lifecycle.occurred_at_utc,
                supplied_monotonic_offset_ms=None,
            )
            if eviction_key is not None:
                self._playback_lifecycles.pop(eviction_key)
                self._playback_lifecycle_evictions += 1
            self._playback_lifecycles[lifecycle_key] = next_phase
            self._playback_lifecycles.move_to_end(lifecycle_key)
            return observation

    def project_runtime(
        self,
        event: Event | Mapping[str, Any],
    ) -> LifeObservation | None:
        envelope = self._envelope(event, default_source="runtime")
        runtime_rule = {
            "runtime.degraded": (
                ObservationKind.RUNTIME_DEGRADED,
                ObservationOutcome.FAILED,
            ),
            "runtime.recovered": (
                ObservationKind.RUNTIME_RECOVERED,
                ObservationOutcome.COMPLETED,
            ),
        }.get(envelope.event_type)
        if runtime_rule is None:
            self._note_ignored()
            return None
        if envelope.source != "runtime":
            raise ProjectionRejected("runtime observation requires runtime source")
        kind, outcome = runtime_rule
        return self._build(
            kind=kind,
            source="runtime",
            source_event_id=envelope.event_id,
            source_generation=None,
            session_id=None,
            request_id=None,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            sequence=envelope.sequence,
            sequence_domain=f"runtime:{self.runtime_boot_id}",
            input_provenance=InputProvenance(
                InputModality.SYSTEM,
                InputVerification.UNKNOWN,
                self.runtime_boot_id,
                None,
                None,
                None,
                None,
            ),
            outcome=outcome,
            risk_level=RiskLevel.NONE,
            confidence=1.0,
            privacy_class=PrivacyClass.LOCAL_INTERNAL,
            retention_class=RetentionClass.OPERATIONAL,
            timestamp=envelope.timestamp,
            supplied_monotonic_offset_ms=envelope.monotonic_offset_ms,
        )

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "accepted": self._accepted,
                "ignored": self._ignored,
                "tracked_request_provenance": len(self._request_provenance),
                "provenance_evictions": self._provenance_evictions,
                "tracked_playback_lifecycles": len(self._playback_lifecycles),
                "playback_lifecycle_evictions": self._playback_lifecycle_evictions,
            }

    def _request_input_provenance(
        self,
        envelope: _Envelope,
        key: tuple[str, str],
    ) -> InputProvenance:
        wire = envelope.payload.get("input_provenance")
        if wire is not None:
            try:
                return (
                    wire
                    if isinstance(wire, InputProvenance)
                    else InputProvenance.from_dict(wire)
                )
            except (TypeError, ValueError) as exc:
                raise ProjectionRejected(f"invalid input provenance: {exc}") from exc
        with self._lock:
            provenance = self._request_provenance.get(key)
            if provenance is not None:
                self._request_provenance.move_to_end(key)
                return provenance
        return InputProvenance.unknown()

    def _remember_provenance_locked(
        self,
        key: tuple[str, str],
        provenance: InputProvenance,
    ) -> None:
        self._request_provenance[key] = provenance
        self._request_provenance.move_to_end(key)
        while len(self._request_provenance) > self._provenance_capacity:
            self._request_provenance.popitem(last=False)
            self._provenance_evictions += 1

    def _canonical_conversation_metadata(
        self,
        envelope: _Envelope,
        session_id: str,
    ) -> tuple[str, int, str, str | None]:
        expected_domain = f"conversation_store:{session_id}"
        bridged_source_id = envelope.payload.get("source_event_id")
        bridged_sequence = envelope.payload.get("source_sequence")
        bridged_domain = envelope.payload.get("source_sequence_domain")
        if any(
            value is not None
            for value in (bridged_source_id, bridged_sequence, bridged_domain)
        ):
            source_event_id = _required_id(bridged_source_id, "source_event_id")
            sequence = _non_negative_int(bridged_sequence, "source_sequence")
            if bridged_domain != expected_domain:
                raise ProjectionRejected("conversation source sequence domain mismatch")
            if envelope.causation_id not in (None, source_event_id):
                raise ProjectionRejected("conversation source causation mismatch")
            return source_event_id, sequence, expected_domain, None
        causation = envelope.causation_id
        if causation == envelope.event_id:
            causation = None
        return envelope.event_id, envelope.sequence, expected_domain, causation

    def _build(
        self,
        *,
        kind: ObservationKind,
        source: str,
        source_event_id: str,
        source_generation: int | None,
        session_id: str | None,
        request_id: str | None,
        correlation_id: str | None,
        causation_id: str | None,
        sequence: int,
        sequence_domain: str,
        input_provenance: InputProvenance,
        outcome: ObservationOutcome,
        risk_level: RiskLevel,
        confidence: float,
        privacy_class: PrivacyClass,
        retention_class: RetentionClass,
        timestamp: Any,
        supplied_monotonic_offset_ms: Any,
    ) -> LifeObservation:
        reading = self._read_clock()
        monotonic_offset_ms = self._monotonic_offset(
            reading,
            supplied_monotonic_offset_ms,
        )
        occurred_at_utc = _timestamp(timestamp, reading)
        source_id = _required_id(source_event_id, "source_event_id")
        observation_id = _stable_id(
            "observation",
            self.runtime_boot_id,
            source,
            source_id,
            kind.value,
        )
        try:
            observation = LifeObservation(
                schema_version=1,
                observation_id=observation_id,
                kind=kind,
                occurred_at_utc=occurred_at_utc,
                monotonic_offset_ms=monotonic_offset_ms,
                source=source,
                source_event_id=source_id,
                source_boot_id=self.runtime_boot_id,
                source_generation=source_generation,
                session_id=session_id,
                request_id=request_id,
                correlation_id=correlation_id,
                causation_id=causation_id,
                sequence=sequence,
                sequence_domain=sequence_domain,
                input_provenance=input_provenance,
                outcome=outcome,
                risk_level=risk_level,
                confidence=confidence,
                privacy_class=privacy_class,
                retention_class=retention_class,
            )
        except (TypeError, ValueError) as exc:
            raise ProjectionRejected(f"invalid governed observation: {exc}") from exc
        with self._lock:
            self._accepted += 1
        return observation

    def _envelope(
        self,
        event: Event | Mapping[str, Any],
        *,
        default_source: str,
    ) -> _Envelope:
        if isinstance(event, Event):
            payload = event.payload
            event_id = event.id
            event_type = event.type
            source = event.source
            timestamp = event.timestamp
            monotonic_offset_ms = None
            sequence = event.sequence
            session_id = payload.get("session_id")
            request_id = payload.get("request_id")
            correlation_id = event.correlation_id or payload.get("correlation_id")
            causation_id = event.causation_id or payload.get("causation_id")
        elif isinstance(event, Mapping):
            payload = event.get("payload", {})
            event_id = event.get("event_id") or event.get("id")
            event_type = event.get("type")
            source = event.get("source") or default_source
            timestamp = event.get("timestamp")
            monotonic_offset_ms = event.get("monotonic_offset_ms")
            sequence = event.get("sequence")
            session_id = event.get("session_id")
            request_id = event.get("request_id")
            correlation_id = event.get("correlation_id")
            causation_id = event.get("causation_id")
            if isinstance(payload, Mapping):
                session_id = session_id or payload.get("session_id")
                request_id = request_id or payload.get("request_id")
                correlation_id = correlation_id or payload.get("correlation_id")
                causation_id = causation_id or payload.get("causation_id")
        else:
            raise ProjectionRejected("source event must be Event or object")
        if not isinstance(payload, Mapping):
            raise ProjectionRejected("source event payload must be an object")
        try:
            return _Envelope(
                event_id=_required_id(event_id, "event_id"),
                event_type=_required_code(event_type, "event type"),
                payload=payload,
                source=_required_code(source, "source"),
                timestamp=timestamp,
                monotonic_offset_ms=(
                    None
                    if monotonic_offset_ms is None
                    else _non_negative_int(
                        monotonic_offset_ms,
                        "monotonic_offset_ms",
                    )
                ),
                sequence=_non_negative_int(sequence, "sequence"),
                session_id=_optional_id(session_id, "session_id"),
                request_id=_optional_id(request_id, "request_id"),
                correlation_id=_optional_id(correlation_id, "correlation_id"),
                causation_id=_optional_id(causation_id, "causation_id"),
            )
        except (TypeError, ValueError) as exc:
            raise ProjectionRejected(str(exc)) from exc

    def _monotonic_offset(
        self,
        reading: ClockReading,
        supplied: Any,
    ) -> int:
        if supplied is not None:
            return _non_negative_int(supplied, "monotonic_offset_ms")
        return max(
            0,
            round(
                (reading.monotonic_seconds - self._boot_monotonic_seconds) * 1000
            ),
        )

    @staticmethod
    def _risk(payload: Mapping[str, Any]) -> RiskLevel:
        value = payload.get("risk_level", RiskLevel.NONE.value)
        try:
            return value if isinstance(value, RiskLevel) else RiskLevel(value)
        except (TypeError, ValueError) as exc:
            raise ProjectionRejected("unknown risk_level") from exc

    def _read_clock(self) -> ClockReading:
        reading = self._clock.read()
        if not isinstance(reading, ClockReading):
            raise TypeError("clock.read() must return ClockReading")
        return reading

    def _note_ignored(self) -> None:
        with self._lock:
            self._ignored += 1


def _timestamp(value: Any, fallback: ClockReading) -> str:
    if value is None:
        return fallback.utc_timestamp
    if isinstance(value, str):
        try:
            return format_utc_milliseconds(value)
        except (TypeError, ValueError) as exc:
            raise ProjectionRejected("invalid source timestamp") from exc
    if isinstance(value, datetime):
        try:
            return format_utc_milliseconds(value)
        except (TypeError, ValueError) as exc:
            raise ProjectionRejected("invalid source timestamp") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectionRejected("invalid source timestamp")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ProjectionRejected("invalid source timestamp")
    try:
        return format_utc_milliseconds(datetime.fromtimestamp(numeric, timezone.utc))
    except (OSError, OverflowError, ValueError) as exc:
        raise ProjectionRejected("invalid source timestamp") from exc


def _required_id(value: Any, field_name: str) -> str:
    if type(value) is not str or not value or len(value) > 256:
        raise ValueError(f"invalid {field_name}")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"invalid {field_name}")
    return value


def _optional_id(value: Any, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    return _required_id(value, field_name)


def _required_code(value: Any, field_name: str) -> str:
    text = _required_id(value, field_name)
    if not all(character.islower() or character.isdigit() or character in "._-" for character in text):
        raise ValueError(f"invalid {field_name}")
    return text


def _non_negative_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {field_name}")
    return value


def _finite_non_negative(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {field_name}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(f"invalid {field_name}")
    return numeric


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    material = "\0".join(parts)
    return f"{prefix}-{_digest(material)}"


def _playback_source_id(
    playback_id: str,
    generation: int,
    outcome: PlaybackOutcome,
) -> str:
    return _stable_id(
        "playback-event",
        playback_id,
        str(generation),
        outcome.value,
    )


__all__ = ["GovernedObservationBridge", "ProjectionRejected"]
