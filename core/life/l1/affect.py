"""Evidence-bound projection of short-lived L1 functional affect."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Iterable
from datetime import datetime
from threading import RLock
from typing import Final

from .clock import ClockReading, coerce_utc, format_utc_milliseconds
from .contracts import (
    AffectEvidence,
    AppraisalResult,
    FunctionalAffect,
    FunctionalAffectKind,
    ReasonCode,
)


DEFAULT_EVIDENCE_CAPACITY: Final = 256

_REASON_BINDINGS: Final = {
    FunctionalAffectKind.CURIOUS: frozenset(
        {
            ReasonCode.USER_INVOKED,
            ReasonCode.VOICE_LISTENING_STARTED,
            ReasonCode.REQUEST_ACTIVITY,
        }
    ),
    FunctionalAffectKind.CAUTIOUS: frozenset(
        {
            ReasonCode.APPROVAL_REQUIRED,
            ReasonCode.APPROVAL_DENIED,
            ReasonCode.TOOL_STARTED,
            ReasonCode.TOOL_RISK_OBSERVED,
            ReasonCode.REQUEST_FAILED,
            ReasonCode.RUNTIME_DEGRADED,
        }
    ),
    FunctionalAffectKind.BLOCKED: frozenset(
        {
            ReasonCode.APPROVAL_DENIED,
            ReasonCode.BLOCKED_BY_FAILURE,
            ReasonCode.REQUEST_FAILED,
        }
    ),
    FunctionalAffectKind.RELIEVED: frozenset(
        {
            ReasonCode.REQUEST_COMPLETED,
            ReasonCode.RUNTIME_RECOVERED,
            ReasonCode.LOAD_REDUCED,
        }
    ),
    FunctionalAffectKind.SATISFIED: frozenset({ReasonCode.GOAL_VERIFIED}),
}
_KIND_ORDER: Final = {
    kind: index for index, kind in enumerate(FunctionalAffectKind)
}


def _utc(value: str | datetime | ClockReading) -> tuple[str, datetime]:
    if isinstance(value, ClockReading):
        return value.utc_timestamp, value.utc
    parsed = coerce_utc(value)
    return format_utc_milliseconds(parsed), parsed


def _evidence_values(
    source: AppraisalResult | AffectEvidence | Iterable[AffectEvidence],
) -> tuple[AffectEvidence, ...]:
    if isinstance(source, AppraisalResult):
        return source.affect_evidence
    if isinstance(source, AffectEvidence):
        return (source,)
    if isinstance(source, (str, bytes)) or not isinstance(source, Iterable):
        raise TypeError("evidence must contain AffectEvidence values")
    values = tuple(source)
    if any(not isinstance(item, AffectEvidence) for item in values):
        raise TypeError("evidence must contain AffectEvidence values")
    return values


def _validate_binding(item: AffectEvidence) -> None:
    if item.reason_code not in _REASON_BINDINGS[item.kind]:
        raise ValueError(
            f"affect evidence {item.evidence_id!r} has an unsupported "
            f"{item.kind.value}/{item.reason_code.value} binding"
        )


class FunctionalAffectProjector:
    """Keep only live evidence and expose deterministic read-only projections."""

    def __init__(self, *, evidence_capacity: int = DEFAULT_EVIDENCE_CAPACITY) -> None:
        if type(evidence_capacity) is not int or evidence_capacity <= 0:
            raise ValueError("evidence_capacity must be a positive integer")
        self._capacity = evidence_capacity
        self._evidence: OrderedDict[str, AffectEvidence] = OrderedDict()
        self._lock = RLock()

    @classmethod
    def from_restart(
        cls,
        *,
        persisted_evidence: Iterable[AffectEvidence] | None = None,
        evidence_capacity: int = DEFAULT_EVIDENCE_CAPACITY,
    ) -> "FunctionalAffectProjector":
        """Start empty even if a caller presents stale pre-restart evidence."""

        if persisted_evidence is not None:
            _evidence_values(persisted_evidence)
        return cls(evidence_capacity=evidence_capacity)

    def apply(
        self,
        evidence: AppraisalResult | AffectEvidence | Iterable[AffectEvidence],
        now_utc: str | datetime | ClockReading,
    ) -> tuple[FunctionalAffect, ...]:
        """Atomically accept valid live evidence and return current projection."""

        values = _evidence_values(evidence)
        _, now = _utc(now_utc)
        batch: dict[str, AffectEvidence] = {}
        for item in values:
            _validate_binding(item)
            _, occurred = _utc(item.occurred_at_utc)
            if occurred > now:
                raise ValueError("affect evidence cannot occur in the future")
            duplicate = batch.get(item.evidence_id)
            if duplicate is not None and duplicate != item:
                raise ValueError(
                    f"affect evidence ID {item.evidence_id!r} was reused"
                )
            batch[item.evidence_id] = item

        with self._lock:
            self._expire(now)
            for item in batch.values():
                existing = self._evidence.get(item.evidence_id)
                if existing is not None and existing != item:
                    raise ValueError(
                        f"affect evidence ID {item.evidence_id!r} was reused"
                    )
            for item in batch.values():
                _, valid_until = _utc(item.valid_until_utc)
                if valid_until <= now:
                    continue
                self._evidence[item.evidence_id] = item
            self._bound_capacity()
            return self._project()

    def snapshot(
        self, now_utc: str | datetime | ClockReading
    ) -> tuple[FunctionalAffect, ...]:
        """Expire stale evidence lazily and return immutable projections."""

        _, now = _utc(now_utc)
        with self._lock:
            self._expire(now)
            return self._project()

    def reset_for_restart(self) -> None:
        with self._lock:
            self._evidence.clear()

    def _expire(self, now: datetime) -> None:
        expired = [
            evidence_id
            for evidence_id, item in self._evidence.items()
            if _utc(item.valid_until_utc)[1] <= now
        ]
        for evidence_id in expired:
            self._evidence.pop(evidence_id, None)

    def _bound_capacity(self) -> None:
        while len(self._evidence) > self._capacity:
            evidence_id = min(
                self._evidence,
                key=lambda candidate: (
                    _utc(self._evidence[candidate].valid_until_utc)[1],
                    _utc(self._evidence[candidate].occurred_at_utc)[1],
                    candidate,
                ),
            )
            self._evidence.pop(evidence_id, None)

    def _project(self) -> tuple[FunctionalAffect, ...]:
        grouped: dict[FunctionalAffectKind, list[AffectEvidence]] = defaultdict(list)
        for item in self._evidence.values():
            grouped[item.kind].append(item)

        projections: list[FunctionalAffect] = []
        for kind in sorted(grouped, key=_KIND_ORDER.__getitem__):
            items = sorted(
                grouped[kind],
                key=lambda item: (item.occurred_at_utc, item.evidence_id),
            )
            strongest = max(
                items,
                key=lambda item: (
                    item.intensity,
                    item.confidence,
                    item.occurred_at_utc,
                    item.evidence_id,
                ),
            )
            projections.append(
                FunctionalAffect(
                    kind=kind,
                    intensity=max(item.intensity for item in items),
                    confidence=max(item.confidence for item in items),
                    reason_code=strongest.reason_code,
                    evidence_ids=tuple(item.evidence_id for item in items),
                    valid_until_utc=min(item.valid_until_utc for item in items),
                )
            )
        return tuple(projections)


def project_functional_affects(
    evidence: AppraisalResult | AffectEvidence | Iterable[AffectEvidence],
    now_utc: str | datetime | ClockReading,
) -> tuple[FunctionalAffect, ...]:
    """Pure one-shot projection for callers that do not retain evidence state."""

    return FunctionalAffectProjector().apply(evidence, now_utc)


__all__ = [
    "DEFAULT_EVIDENCE_CAPACITY",
    "FunctionalAffectProjector",
    "project_functional_affects",
]
