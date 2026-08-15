"""Pure deterministic appraisal of governed L1 observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Mapping

from .clock import ClockReading, add_seconds, coerce_utc, format_utc_milliseconds
from .contracts import (
    AffectEvidence,
    AppraisalResult,
    AttentionClaim,
    FunctionalAffectKind,
    LifeObservation,
    ObservationKind,
    ObservationOutcome,
    ReasonCode,
    RiskLevel,
    StateDimension,
)


_MEDIUM_OR_HIGH_RISK = frozenset(
    {RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}
)
_HIGH_RISK = frozenset({RiskLevel.HIGH, RiskLevel.CRITICAL})


@dataclass(frozen=True)
class _AttentionRule:
    target_kind: str
    priority: int
    ttl_seconds: float
    interruptible: bool = True


@dataclass(frozen=True)
class _AffectRule:
    kind: FunctionalAffectKind
    intensity: float
    reason_code: ReasonCode
    ttl_seconds: float


_REASON_CODES: Mapping[ObservationKind, ReasonCode] = {
    ObservationKind.USER_INVOKED: ReasonCode.USER_INVOKED,
    ObservationKind.VOICE_LISTENING_STARTED: ReasonCode.VOICE_LISTENING_STARTED,
    ObservationKind.VOICE_LISTENING_STOPPED: ReasonCode.VOICE_LISTENING_STOPPED,
    ObservationKind.REQUEST_STARTED: ReasonCode.REQUEST_STARTED,
    ObservationKind.REQUEST_ACTIVITY: ReasonCode.REQUEST_ACTIVITY,
    ObservationKind.APPROVAL_REQUIRED: ReasonCode.APPROVAL_REQUIRED,
    ObservationKind.TOOL_STARTED: ReasonCode.TOOL_STARTED,
    ObservationKind.TOOL_COMPLETED: ReasonCode.TOOL_COMPLETED,
    ObservationKind.REQUEST_COMPLETED: ReasonCode.REQUEST_COMPLETED,
    ObservationKind.REQUEST_FAILED: ReasonCode.REQUEST_FAILED,
    ObservationKind.REQUEST_CANCELLED: ReasonCode.REQUEST_CANCELLED,
    ObservationKind.SPEECH_STARTED: ReasonCode.SPEECH_STARTED,
    ObservationKind.SPEECH_STOPPED: ReasonCode.SPEECH_STOPPED,
    ObservationKind.INTERACTION_INTERRUPTED: ReasonCode.INTERACTION_INTERRUPTED,
    ObservationKind.GOAL_VERIFIED: ReasonCode.GOAL_VERIFIED,
    ObservationKind.RUNTIME_DEGRADED: ReasonCode.RUNTIME_DEGRADED,
    ObservationKind.RUNTIME_RECOVERED: ReasonCode.RUNTIME_RECOVERED,
}

_DELTAS: Mapping[ObservationKind, Mapping[StateDimension, float]] = {
    ObservationKind.USER_INVOKED: {
        StateDimension.ACTIVATION: 0.25,
        StateDimension.SOCIAL_PRESENCE: 0.55,
    },
    ObservationKind.VOICE_LISTENING_STARTED: {
        StateDimension.ACTIVATION: 0.15,
        StateDimension.SOCIAL_PRESENCE: 0.30,
    },
    ObservationKind.REQUEST_STARTED: {
        StateDimension.ACTIVATION: 0.20,
        StateDimension.COGNITIVE_LOAD: 0.20,
    },
    ObservationKind.REQUEST_ACTIVITY: {StateDimension.COGNITIVE_LOAD: 0.05},
    ObservationKind.APPROVAL_REQUIRED: {
        StateDimension.CAUTION: 0.30,
        StateDimension.CERTAINTY: -0.15,
    },
    ObservationKind.REQUEST_FAILED: {
        StateDimension.CERTAINTY: -0.25,
        StateDimension.BLOCKEDNESS: 0.35,
        StateDimension.CAUTION: 0.20,
    },
    ObservationKind.REQUEST_CANCELLED: {
        StateDimension.COGNITIVE_LOAD: -0.20,
        StateDimension.ACTIVATION: -0.10,
    },
    ObservationKind.REQUEST_COMPLETED: {
        StateDimension.COGNITIVE_LOAD: -0.25,
        StateDimension.BLOCKEDNESS: -0.20,
    },
    ObservationKind.GOAL_VERIFIED: {
        StateDimension.CERTAINTY: 0.25,
        StateDimension.BLOCKEDNESS: -0.35,
    },
    ObservationKind.RUNTIME_DEGRADED: {
        StateDimension.CAUTION: 0.50,
        StateDimension.CERTAINTY: -0.40,
    },
    ObservationKind.RUNTIME_RECOVERED: {
        StateDimension.CAUTION: -0.20,
        StateDimension.BLOCKEDNESS: -0.20,
    },
}

_ATTENTION_RULES: Mapping[ObservationKind, _AttentionRule] = {
    ObservationKind.USER_INVOKED: _AttentionRule("presence", 55, 3.0),
    ObservationKind.VOICE_LISTENING_STARTED: _AttentionRule("voice_session", 85, 15.0),
    ObservationKind.REQUEST_STARTED: _AttentionRule("request", 70, 60.0),
    ObservationKind.REQUEST_ACTIVITY: _AttentionRule("request", 70, 60.0),
    ObservationKind.APPROVAL_REQUIRED: _AttentionRule("approval", 90, 120.0, False),
    # Playback stop normally closes this claim; this ceiling is only a fault bound.
    ObservationKind.SPEECH_STARTED: _AttentionRule("playback", 60, 120.0),
    ObservationKind.INTERACTION_INTERRUPTED: _AttentionRule("interruption", 100, 5.0, False),
    ObservationKind.RUNTIME_DEGRADED: _AttentionRule("recovery", 95, 30.0, False),
    ObservationKind.RUNTIME_RECOVERED: _AttentionRule("recovery_observation", 50, 10.0),
}


class AppraisalReducer:
    """Map one validated observation to one immutable appraisal result."""

    def reduce(
        self,
        observation: LifeObservation,
        now: ClockReading | datetime | str,
    ) -> AppraisalResult:
        if not isinstance(observation, LifeObservation):
            raise TypeError("observation must be a LifeObservation")
        if not isinstance(observation.kind, ObservationKind):
            raise ValueError("unsupported observation kind")

        now_utc = now.utc if isinstance(now, ClockReading) else coerce_utc(now)
        now_timestamp = format_utc_milliseconds(now_utc)
        reason_code = self._reason_code(observation)
        deltas = dict(_DELTAS.get(observation.kind, {}))

        if observation.kind is ObservationKind.TOOL_STARTED:
            deltas[StateDimension.COGNITIVE_LOAD] = 0.10
            if observation.risk_level in _MEDIUM_OR_HIGH_RISK:
                deltas[StateDimension.CAUTION] = 0.20

        if (
            observation.kind is ObservationKind.APPROVAL_RESOLVED
            and observation.outcome is ObservationOutcome.DENIED
        ):
            deltas = {
                StateDimension.CAUTION: 0.10,
                StateDimension.BLOCKEDNESS: 0.10,
            }

        attention_claim = self._attention_claim(observation, now_timestamp)
        affect_rules = self._affect_rules(observation)
        affect_evidence = tuple(
            self._affect_evidence(observation, now_timestamp, rule)
            for rule in affect_rules
        )
        expiration_candidates = [
            item
            for item in (
                attention_claim.expires_at_utc if attention_claim else None,
                *(evidence.valid_until_utc for evidence in affect_evidence),
            )
            if item is not None
        ]

        return AppraisalResult(
            schema_version=1,
            observation_id=observation.observation_id,
            deltas=deltas,
            attention_claim=attention_claim,
            affect_evidence=affect_evidence,
            reason_code=reason_code,
            confidence=observation.confidence,
            expires_at_utc=max(expiration_candidates) if expiration_candidates else None,
        )

    @staticmethod
    def _reason_code(observation: LifeObservation) -> ReasonCode:
        if observation.kind is ObservationKind.APPROVAL_RESOLVED:
            if observation.outcome is ObservationOutcome.APPROVED:
                return ReasonCode.APPROVAL_APPROVED
            if observation.outcome is ObservationOutcome.DENIED:
                return ReasonCode.APPROVAL_DENIED
            raise ValueError("approval.resolved requires approved or denied outcome")
        try:
            return _REASON_CODES[observation.kind]
        except KeyError as exc:
            raise ValueError("unsupported observation kind") from exc

    @staticmethod
    def _target_id(observation: LifeObservation, target_kind: str) -> str:
        if target_kind in {"request", "approval", "playback", "interruption"}:
            target_id = observation.request_id
        elif target_kind in {"presence", "voice_session"}:
            target_id = observation.session_id
        else:
            target_id = observation.source_boot_id
        if target_id is None:
            raise ValueError(f"{observation.kind.value} requires {target_kind} target identity")
        return target_id

    def _attention_claim(
        self,
        observation: LifeObservation,
        now_timestamp: str,
    ) -> AttentionClaim | None:
        rule = _ATTENTION_RULES.get(observation.kind)
        if (
            observation.kind is ObservationKind.APPROVAL_REQUIRED
            and observation.risk_level in _HIGH_RISK
        ):
            rule = _AttentionRule("approval", 95, 30.0, False)
        if rule is None:
            return None
        target_id = self._target_id(observation, rule.target_kind)
        return AttentionClaim(
            claim_id=self._derived_id("claim", observation.observation_id),
            target_kind=rule.target_kind,
            target_id=target_id,
            priority=rule.priority,
            source_observation_id=observation.observation_id,
            acquired_at_utc=now_timestamp,
            expires_at_utc=add_seconds(now_timestamp, rule.ttl_seconds),
            interruptible=rule.interruptible,
        )

    @staticmethod
    def _affect_rules(observation: LifeObservation) -> tuple[_AffectRule, ...]:
        kind = observation.kind
        if kind is ObservationKind.TOOL_STARTED and observation.risk_level in _MEDIUM_OR_HIGH_RISK:
            return (
                _AffectRule(
                    FunctionalAffectKind.CAUTIOUS,
                    0.20,
                    ReasonCode.TOOL_RISK_OBSERVED,
                    30.0,
                ),
            )
        if kind is ObservationKind.APPROVAL_REQUIRED:
            return (
                _AffectRule(
                    FunctionalAffectKind.CAUTIOUS,
                    0.30,
                    ReasonCode.APPROVAL_REQUIRED,
                    120.0,
                ),
            )
        if kind is ObservationKind.APPROVAL_RESOLVED and observation.outcome is ObservationOutcome.DENIED:
            return (
                _AffectRule(
                    FunctionalAffectKind.BLOCKED,
                    0.10,
                    ReasonCode.APPROVAL_DENIED,
                    30.0,
                ),
            )
        if kind is ObservationKind.REQUEST_FAILED:
            return (
                _AffectRule(
                    FunctionalAffectKind.BLOCKED,
                    0.35,
                    ReasonCode.BLOCKED_BY_FAILURE,
                    30.0,
                ),
                _AffectRule(
                    FunctionalAffectKind.CAUTIOUS,
                    0.20,
                    ReasonCode.REQUEST_FAILED,
                    45.0,
                ),
            )
        if kind is ObservationKind.REQUEST_COMPLETED:
            return (
                _AffectRule(
                    FunctionalAffectKind.RELIEVED,
                    0.25,
                    ReasonCode.LOAD_REDUCED,
                    30.0,
                ),
            )
        if kind is ObservationKind.GOAL_VERIFIED:
            return (
                _AffectRule(
                    FunctionalAffectKind.SATISFIED,
                    0.25,
                    ReasonCode.GOAL_VERIFIED,
                    30.0,
                ),
            )
        if kind is ObservationKind.RUNTIME_DEGRADED:
            return (
                _AffectRule(
                    FunctionalAffectKind.CAUTIOUS,
                    0.50,
                    ReasonCode.RUNTIME_DEGRADED,
                    30.0,
                ),
            )
        if kind is ObservationKind.RUNTIME_RECOVERED:
            return (
                _AffectRule(
                    FunctionalAffectKind.RELIEVED,
                    0.20,
                    ReasonCode.RUNTIME_RECOVERED,
                    10.0,
                ),
            )
        return ()

    @staticmethod
    def _affect_evidence(
        observation: LifeObservation,
        now_timestamp: str,
        rule: _AffectRule,
    ) -> AffectEvidence:
        return AffectEvidence(
            evidence_id=AppraisalReducer._derived_id(
                f"affect:{rule.kind.value}", observation.observation_id
            ),
            kind=rule.kind,
            source_observation_id=observation.observation_id,
            reason_code=rule.reason_code,
            intensity=rule.intensity,
            confidence=observation.confidence,
            occurred_at_utc=now_timestamp,
            valid_until_utc=add_seconds(now_timestamp, rule.ttl_seconds),
        )

    @staticmethod
    def _derived_id(prefix: str, observation_id: str) -> str:
        candidate = f"{prefix}:{observation_id}"
        if len(candidate) <= 256:
            return candidate
        digest = sha256(observation_id.encode("utf-8")).hexdigest()
        return f"{prefix}:{digest}"


__all__ = ["AppraisalReducer"]
