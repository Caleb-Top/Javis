"""Deterministic, bounded arbitration for L1 attention claims."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

from .contracts import AttentionClaim, AttentionMode, AttentionSnapshot


_TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"
DEFAULT_CLAIM_CAPACITY: Final = 256


@dataclass(frozen=True)
class AttentionPolicy:
    name: str
    priority: int
    ttl_seconds: float | None
    default_mode: AttentionMode


_POLICIES = (
    AttentionPolicy("user_input", 100, 5.0, AttentionMode.PRESENT),
    AttentionPolicy("safety", 95, 30.0, AttentionMode.RECOVERING),
    AttentionPolicy("approval", 90, 120.0, AttentionMode.AWAITING_APPROVAL),
    AttentionPolicy("listening", 85, 15.0, AttentionMode.LISTENING),
    AttentionPolicy("request", 70, 60.0, AttentionMode.ENGAGED),
    AttentionPolicy("speaking", 60, None, AttentionMode.SPEAKING),
    AttentionPolicy("presence", 55, 3.0, AttentionMode.PRESENT),
    AttentionPolicy("recovery", 50, 10.0, AttentionMode.RECOVERING),
)

ATTENTION_POLICIES = MappingProxyType({policy.name: policy for policy in _POLICIES})
ATTENTION_PRIORITIES = MappingProxyType(
    {policy.name: policy.priority for policy in _POLICIES} | {"idle": 0}
)
ATTENTION_TTLS_SECONDS = MappingProxyType(
    {policy.name: policy.ttl_seconds for policy in _POLICIES} | {"idle": None}
)
_POLICY_BY_PRIORITY = MappingProxyType({policy.priority: policy for policy in _POLICIES})


@dataclass(frozen=True)
class _ClaimEntry:
    claim: AttentionClaim
    mode: AttentionMode
    session_id: str | None
    order: int


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, _TIMESTAMP_FORMAT)
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be UTC with millisecond precision") from exc


def _format_timestamp(value: datetime) -> str:
    return (
        value.strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{value.microsecond // 1000:03d}Z"
    )


def _validate_session_id(value: str | None) -> None:
    if value is None:
        return
    if type(value) is not str or not value or len(value) > 256:
        raise ValueError("session_id must be a bounded non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("session_id must not contain control characters")


def _duration_milliseconds(claim: AttentionClaim) -> int:
    acquired = _parse_timestamp(claim.acquired_at_utc)
    expires = _parse_timestamp(claim.expires_at_utc)
    return int(round((expires - acquired).total_seconds() * 1000.0))


def _mode_for(policy: AttentionPolicy, claim: AttentionClaim) -> AttentionMode:
    if policy.priority != 95:
        return policy.default_mode
    target_kind = claim.target_kind.casefold()
    if "approval" in target_kind:
        return AttentionMode.AWAITING_APPROVAL
    if "block" in target_kind:
        return AttentionMode.BLOCKED
    return AttentionMode.RECOVERING


class AttentionCoordinator:
    """Own active claims and expose one deterministic foreground snapshot.

    Time is always supplied by the caller. The coordinator performs no I/O and
    never reads a wall clock, which keeps replay and expiry deterministic.
    """

    def __init__(
        self,
        initialized_at_utc: str = "1970-01-01T00:00:00.000Z",
        *,
        claim_capacity: int = DEFAULT_CLAIM_CAPACITY,
    ) -> None:
        _parse_timestamp(initialized_at_utc)
        if type(claim_capacity) is not int or claim_capacity <= 0:
            raise ValueError("claim_capacity must be a positive integer")
        self._claims: dict[str, _ClaimEntry] = {}
        self._claim_capacity = claim_capacity
        self._foreground_session_id: str | None = None
        self._last_evaluated_at_utc = initialized_at_utc
        self._idle_since_utc = initialized_at_utc
        self._next_order = 0

    @property
    def foreground_session_id(self) -> str | None:
        return self._foreground_session_id

    @property
    def active_claim_count(self) -> int:
        return len(self._claims)

    def apply(
        self,
        claim: AttentionClaim,
        mode: AttentionMode | str | None = None,
        *,
        session_id: str | None = None,
        foreground: bool | None = None,
        now_utc: str | None = None,
        playback_duration_seconds: float | None = None,
    ) -> AttentionSnapshot:
        """Add or renew one governed claim and return the resulting snapshot."""

        if not isinstance(claim, AttentionClaim):
            raise TypeError("claim must be an AttentionClaim")
        _validate_session_id(session_id)
        if foreground is not None and type(foreground) is not bool:
            raise ValueError("foreground must be a boolean when provided")
        if foreground is True and session_id is None:
            raise ValueError("foreground claims require session_id")

        policy = _POLICY_BY_PRIORITY.get(claim.priority)
        if policy is None:
            raise ValueError(f"priority {claim.priority} is not a governed attention priority")
        resolved_mode = self._resolve_mode(policy, claim, mode)
        self._validate_ttl(policy, claim, playback_duration_seconds)

        acquired = _parse_timestamp(claim.acquired_at_utc)
        requested_now = _parse_timestamp(now_utc or claim.acquired_at_utc)
        if acquired > requested_now:
            raise ValueError("claim acquisition cannot be later than now_utc")

        existing = self._claims.get(claim.claim_id)
        if existing is not None:
            self._validate_renewal_owner(existing, claim, resolved_mode, session_id)
            existing_acquired = _parse_timestamp(existing.claim.acquired_at_utc)
            if acquired <= existing_acquired:
                self._advance_and_expire(requested_now)
                return self._snapshot_unchecked()

        effective_now = self._advance_and_expire(requested_now)
        if _parse_timestamp(claim.expires_at_utc) <= effective_now:
            return self._snapshot_unchecked()

        self._next_order += 1
        self._claims[claim.claim_id] = _ClaimEntry(
            claim=claim,
            mode=resolved_mode,
            session_id=session_id,
            order=self._next_order,
        )
        self._enforce_capacity()
        if foreground is True:
            self._foreground_session_id = session_id
        elif foreground is None and session_id is not None:
            if claim.priority == 100 or self._foreground_session_id is None:
                self._foreground_session_id = session_id
        return self._snapshot_unchecked()

    def _enforce_capacity(self) -> None:
        while len(self._claims) > self._claim_capacity:
            removable = min(
                self._claims.items(),
                key=lambda item: (
                    item[1].claim.priority,
                    _parse_timestamp(item[1].claim.acquired_at_utc),
                    item[1].order,
                ),
            )[0]
            del self._claims[removable]

    def expire(
        self,
        now_utc: str,
        *,
        claim_id: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        source_observation_id: str | None = None,
        session_id: str | None = None,
    ) -> AttentionSnapshot:
        """Expire elapsed claims or close only claims owned by a terminal event.

        Selectors are conjunctive. A delayed terminal event therefore cannot
        clear a newer claim with another claim ID or target identity.
        """

        requested_now = _parse_timestamp(now_utc)
        _validate_session_id(session_id)
        selectors = (claim_id, target_kind, target_id, source_observation_id, session_id)
        for value in selectors[:-1]:
            if value is not None and (type(value) is not str or not value):
                raise ValueError("claim selectors must be non-empty strings")

        previous = self._winner()
        effective_now = self._advance_and_expire(requested_now)
        has_selector = any(value is not None for value in selectors)
        if has_selector:
            for key, entry in tuple(self._claims.items()):
                if _parse_timestamp(entry.claim.acquired_at_utc) > requested_now:
                    continue
                if claim_id is not None and entry.claim.claim_id != claim_id:
                    continue
                if target_kind is not None and entry.claim.target_kind != target_kind:
                    continue
                if target_id is not None and entry.claim.target_id != target_id:
                    continue
                if (
                    source_observation_id is not None
                    and entry.claim.source_observation_id != source_observation_id
                ):
                    continue
                if session_id is not None and entry.session_id != session_id:
                    continue
                del self._claims[key]

        self._record_idle_transition(previous, effective_now)
        return self._snapshot_unchecked()

    def snapshot(self, now_utc: str | None = None) -> AttentionSnapshot:
        """Return the current snapshot, lazily expiring claims when time is supplied."""

        if now_utc is not None:
            return self.expire(now_utc)
        return self._snapshot_unchecked()

    def _resolve_mode(
        self,
        policy: AttentionPolicy,
        claim: AttentionClaim,
        requested: AttentionMode | str | None,
    ) -> AttentionMode:
        inferred = _mode_for(policy, claim)
        if requested is None:
            return inferred
        try:
            resolved = requested if isinstance(requested, AttentionMode) else AttentionMode(requested)
        except (TypeError, ValueError) as exc:
            raise ValueError("mode must be a governed AttentionMode") from exc
        allowed = {inferred}
        if policy.priority == 95:
            allowed.update(
                {
                    AttentionMode.AWAITING_APPROVAL,
                    AttentionMode.BLOCKED,
                    AttentionMode.RECOVERING,
                }
            )
        if resolved not in allowed:
            raise ValueError(f"mode {resolved.value} does not match priority {policy.priority}")
        return resolved

    def _validate_ttl(
        self,
        policy: AttentionPolicy,
        claim: AttentionClaim,
        playback_duration_seconds: float | None,
    ) -> None:
        actual_ms = _duration_milliseconds(claim)
        if policy.priority == 60:
            if playback_duration_seconds is None:
                if actual_ms < 2_000:
                    raise ValueError("speaking TTL must include playback duration plus 2 seconds")
                return
            if (
                isinstance(playback_duration_seconds, bool)
                or not isinstance(playback_duration_seconds, (int, float))
                or not math.isfinite(float(playback_duration_seconds))
                or playback_duration_seconds < 0
            ):
                raise ValueError("playback_duration_seconds must be finite and non-negative")
            expected_ms = int(round((float(playback_duration_seconds) + 2.0) * 1000.0))
            if actual_ms != expected_ms:
                raise ValueError("speaking TTL must equal playback duration plus 2 seconds")
            return
        expected_ms = int(round(float(policy.ttl_seconds) * 1000.0))
        if actual_ms != expected_ms:
            raise ValueError(
                f"priority {policy.priority} TTL must equal {policy.ttl_seconds:g} seconds"
            )

    @staticmethod
    def _validate_renewal_owner(
        existing: _ClaimEntry,
        claim: AttentionClaim,
        mode: AttentionMode,
        session_id: str | None,
    ) -> None:
        old = existing.claim
        if (
            old.target_kind != claim.target_kind
            or old.target_id != claim.target_id
            or old.priority != claim.priority
            or existing.mode is not mode
            or existing.session_id != session_id
        ):
            raise ValueError("claim renewal cannot change its owner or policy")

    def _advance_and_expire(self, requested_now: datetime) -> datetime:
        previous = self._winner()
        current = _parse_timestamp(self._last_evaluated_at_utc)
        effective = max(current, requested_now)
        self._last_evaluated_at_utc = _format_timestamp(effective)
        for key, entry in tuple(self._claims.items()):
            if _parse_timestamp(entry.claim.expires_at_utc) <= effective:
                del self._claims[key]
        self._record_idle_transition(previous, effective)
        return effective

    def _record_idle_transition(
        self,
        previous: _ClaimEntry | None,
        effective_now: datetime,
    ) -> None:
        if previous is not None and self._winner() is None:
            self._idle_since_utc = _format_timestamp(effective_now)

    def _winner(self) -> _ClaimEntry | None:
        if not self._claims:
            return None
        entries = tuple(self._claims.values())
        if self._foreground_session_id is not None:
            foreground = tuple(
                entry for entry in entries if entry.session_id == self._foreground_session_id
            )
            if foreground:
                entries = foreground + tuple(
                    entry
                    for entry in entries
                    if entry.session_id != self._foreground_session_id
                    and (entry.session_id is None or entry.claim.priority >= 95)
                )
        return max(
            entries,
            key=lambda entry: (
                entry.claim.priority,
                _parse_timestamp(entry.claim.acquired_at_utc),
                entry.order,
            ),
        )

    def _snapshot_unchecked(self) -> AttentionSnapshot:
        winner = self._winner()
        if winner is None:
            return AttentionSnapshot(
                mode=AttentionMode.IDLE,
                target_kind=None,
                target_id=None,
                priority=0,
                since_utc=self._idle_since_utc,
                expires_at_utc=None,
                source_observation_id=None,
            )
        claim = winner.claim
        return AttentionSnapshot(
            mode=winner.mode,
            target_kind=claim.target_kind,
            target_id=claim.target_id,
            priority=claim.priority,
            since_utc=claim.acquired_at_utc,
            expires_at_utc=claim.expires_at_utc,
            source_observation_id=claim.source_observation_id,
        )


__all__ = [
    "ATTENTION_POLICIES",
    "ATTENTION_PRIORITIES",
    "ATTENTION_TTLS_SECONDS",
    "DEFAULT_CLAIM_CAPACITY",
    "AttentionCoordinator",
    "AttentionPolicy",
]
