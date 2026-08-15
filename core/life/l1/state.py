"""Deterministic, evidence-derived L1 homeostasis state."""

from __future__ import annotations

import math
from collections import OrderedDict
from datetime import datetime, timedelta
from threading import RLock
from types import MappingProxyType
from typing import Final

from .clock import (
    ClockReading,
    HOMEOSTASIS_BASELINES,
    HOMEOSTASIS_HALF_LIVES_SECONDS,
    coerce_utc,
    decay_toward_baseline,
    format_utc_milliseconds,
)
from .contracts import AppraisalResult, HomeostasisSnapshot, ReasonCode, StateDimension


BASELINES = MappingProxyType(
    {dimension.value: value for dimension, value in HOMEOSTASIS_BASELINES.items()}
)

HALF_LIVES_SECONDS = MappingProxyType(
    {
        dimension.value: value
        for dimension, value in HOMEOSTASIS_HALF_LIVES_SECONDS.items()
    }
)

DEFAULT_SEMANTIC_THRESHOLD: Final = 0.01
DEFAULT_OBSERVATION_CAPACITY: Final = 8_192
DEFAULT_ACTIVITY_WINDOW_MS: Final = 1_000
_DIMENSION_NAMES: Final = tuple(BASELINES)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _utc(value: str | datetime) -> tuple[str, datetime]:
    parsed = coerce_utc(value)
    return format_utc_milliseconds(parsed), parsed


def _monotonic(value: int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("monotonic_ms must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError("monotonic_ms must be finite and non-negative")
    return result


def _clock_point(
    now_utc: str | datetime | ClockReading,
    monotonic_ms: int | float | None,
    *,
    default_monotonic_ms: float | None = None,
) -> tuple[str, datetime, float]:
    if isinstance(now_utc, ClockReading):
        if monotonic_ms not in (None, 0, 0.0):
            raise ValueError("monotonic_ms must be omitted with ClockReading")
        return now_utc.utc_timestamp, now_utc.utc, now_utc.monotonic_seconds * 1000.0
    if monotonic_ms is None:
        if default_monotonic_ms is None:
            raise ValueError("monotonic_ms is required without ClockReading")
        monotonic_ms = default_monotonic_ms
    rendered, parsed = _utc(now_utc)
    return rendered, parsed, _monotonic(monotonic_ms)


def thresholded_semantic_change(
    previous: HomeostasisSnapshot,
    current: HomeostasisSnapshot,
    threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
) -> bool:
    """Ignore timestamp-only and sub-threshold state movement."""

    if not isinstance(previous, HomeostasisSnapshot) or not isinstance(
        current, HomeostasisSnapshot
    ):
        raise TypeError("previous and current must be HomeostasisSnapshot values")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise TypeError("threshold must be numeric")
    threshold_value = float(threshold)
    if not math.isfinite(threshold_value) or not 0.0 <= threshold_value <= 1.0:
        raise ValueError("threshold must be in [0, 1]")
    differences = (
        abs(getattr(current, name) - getattr(previous, name))
        for name in _DIMENSION_NAMES
    )
    if threshold_value == 0.0:
        return any(difference > 0.0 for difference in differences)
    return any(difference > threshold_value for difference in differences)


class HomeostasisReducer:
    """Serialize appraisal writes and lazily decay bounded transient state.

    The reducer never reads a wall clock. Callers provide both display time and a
    process-monotonic offset for every advance. Public snapshots are immutable.
    """

    def __init__(
        self,
        now_utc: str | datetime | ClockReading,
        monotonic_ms: int | float | None = None,
        *,
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
        observation_capacity: int = DEFAULT_OBSERVATION_CAPACITY,
    ) -> None:
        rendered, parsed, monotonic_value = _clock_point(
            now_utc, monotonic_ms, default_monotonic_ms=0.0
        )
        if isinstance(semantic_threshold, bool) or not isinstance(
            semantic_threshold, (int, float)
        ):
            raise TypeError("semantic_threshold must be numeric")
        threshold = float(semantic_threshold)
        if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            raise ValueError("semantic_threshold must be in [0, 1]")
        if type(observation_capacity) is not int or observation_capacity <= 0:
            raise ValueError("observation_capacity must be a positive integer")

        self._lock = RLock()
        self._values = dict(BASELINES)
        self._updated_at_utc = rendered
        self._updated_at_datetime = parsed
        self._monotonic_ms = monotonic_value
        self._semantic_threshold = threshold
        self._semantic_anchor = dict(self._values)
        self._observation_capacity = observation_capacity
        self._observations: OrderedDict[str, None] = OrderedDict()
        self._activity_windows: OrderedDict[str, datetime] = OrderedDict()
        self._last_observation_id: str | None = None

    @classmethod
    def from_restart(
        cls,
        now_utc: str | datetime | ClockReading,
        monotonic_ms: int | float | None = None,
        *,
        persisted_snapshot: HomeostasisSnapshot | None = None,
        semantic_threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
        observation_capacity: int = DEFAULT_OBSERVATION_CAPACITY,
    ) -> "HomeostasisReducer":
        """Create a new boot at baseline; transient snapshots are never restored."""

        if persisted_snapshot is not None and not isinstance(
            persisted_snapshot, HomeostasisSnapshot
        ):
            raise TypeError("persisted_snapshot must be a HomeostasisSnapshot")
        return cls(
            now_utc,
            monotonic_ms,
            semantic_threshold=semantic_threshold,
            observation_capacity=observation_capacity,
        )

    @property
    def last_observation_id(self) -> str | None:
        with self._lock:
            return self._last_observation_id

    def apply(
        self,
        appraisal: AppraisalResult,
        now_utc: str | datetime | ClockReading,
        monotonic_ms: int | float | None = None,
    ) -> bool:
        """Apply one appraisal once and report a thresholded semantic change."""

        if not isinstance(appraisal, AppraisalResult):
            raise TypeError("appraisal must be an AppraisalResult")
        rendered, parsed, monotonic_value = _clock_point(now_utc, monotonic_ms)
        with self._lock:
            self._decay_to(rendered, parsed, monotonic_value)
            if appraisal.observation_id in self._observations:
                return self._consume_semantic_change()

            self._remember(appraisal.observation_id)
            self._last_observation_id = appraisal.observation_id
            if appraisal.expires_at_utc is not None:
                _, expiry = _utc(appraisal.expires_at_utc)
                if parsed >= expiry:
                    return self._consume_semantic_change()

            if self._aggregate_activity(appraisal):
                return self._consume_semantic_change()

            for dimension, delta in appraisal.deltas.items():
                name = dimension.value
                self._values[name] = _clamp(self._values[name] + float(delta))
            return self._consume_semantic_change()

    def tick(
        self,
        now_utc: str | datetime | ClockReading,
        monotonic_ms: int | float | None = None,
    ) -> bool:
        """Advance lazy decay and report only meaningful accumulated movement."""

        rendered, parsed, monotonic_value = _clock_point(now_utc, monotonic_ms)
        with self._lock:
            self._decay_to(rendered, parsed, monotonic_value)
            return self._consume_semantic_change()

    def snapshot(
        self,
        now_utc: str | datetime | ClockReading | None = None,
        monotonic_ms: int | float | None = None,
    ) -> HomeostasisSnapshot:
        """Return an immutable bounded snapshot, optionally advancing decay."""

        if now_utc is None and monotonic_ms is not None:
            raise ValueError("now_utc is required with monotonic_ms")
        if now_utc is not None:
            rendered, parsed, monotonic_value = _clock_point(now_utc, monotonic_ms)
            with self._lock:
                self._decay_to(rendered, parsed, monotonic_value)
                return self._snapshot_unlocked()
        with self._lock:
            return self._snapshot_unlocked()

    def has_semantic_change(
        self,
        now_utc: str | datetime | ClockReading | None = None,
        monotonic_ms: int | float | None = None,
    ) -> bool:
        """Inspect pending movement without consuming the semantic anchor."""

        if now_utc is None and monotonic_ms is not None:
            raise ValueError("now_utc is required with monotonic_ms")
        with self._lock:
            if now_utc is not None:
                rendered, parsed, monotonic_value = _clock_point(
                    now_utc, monotonic_ms
                )
                self._decay_to(rendered, parsed, monotonic_value)
            return self._semantic_changed_unlocked()

    def reset_for_restart(
        self,
        now_utc: str | datetime | ClockReading,
        monotonic_ms: int | float | None = None,
    ) -> HomeostasisSnapshot:
        """Drop all transient values, evidence identities and pending changes."""

        rendered, parsed, monotonic_value = _clock_point(
            now_utc, monotonic_ms, default_monotonic_ms=0.0
        )
        with self._lock:
            self._values = dict(BASELINES)
            self._semantic_anchor = dict(BASELINES)
            self._updated_at_utc = rendered
            self._updated_at_datetime = parsed
            self._monotonic_ms = monotonic_value
            self._observations.clear()
            self._activity_windows.clear()
            self._last_observation_id = None
            return self._snapshot_unlocked()

    def _decay_to(
        self,
        rendered_utc: str,
        parsed_utc: datetime,
        monotonic_ms: float,
    ) -> None:
        if monotonic_ms < self._monotonic_ms:
            raise ValueError("monotonic_ms cannot move backwards")
        elapsed_seconds = (monotonic_ms - self._monotonic_ms) / 1000.0
        if elapsed_seconds:
            for name in _DIMENSION_NAMES:
                self._values[name] = decay_toward_baseline(
                    self._values[name],
                    BASELINES[name],
                    elapsed_seconds,
                    HALF_LIVES_SECONDS[name],
                )
        self._monotonic_ms = monotonic_ms
        if parsed_utc >= self._updated_at_datetime:
            self._updated_at_datetime = parsed_utc
            self._updated_at_utc = rendered_utc

    def _remember(self, observation_id: str) -> None:
        self._observations[observation_id] = None
        while len(self._observations) > self._observation_capacity:
            self._observations.popitem(last=False)

    def _aggregate_activity(self, appraisal: AppraisalResult) -> bool:
        if appraisal.reason_code is not ReasonCode.REQUEST_ACTIVITY:
            return False
        claim = appraisal.attention_claim
        if claim is None or claim.target_kind != "request":
            raise ValueError("request activity requires a request attention claim")
        _, occurred = _utc(claim.acquired_at_utc)
        previous = self._activity_windows.get(claim.target_id)
        if previous is not None and occurred <= previous + timedelta(
            milliseconds=DEFAULT_ACTIVITY_WINDOW_MS
        ):
            return True
        self._activity_windows[claim.target_id] = occurred
        self._activity_windows.move_to_end(claim.target_id)
        while len(self._activity_windows) > self._observation_capacity:
            self._activity_windows.popitem(last=False)
        return False

    def _semantic_changed_unlocked(self) -> bool:
        threshold = self._semantic_threshold
        differences = (
            abs(self._values[name] - self._semantic_anchor[name])
            for name in _DIMENSION_NAMES
        )
        if threshold == 0.0:
            return any(difference > 0.0 for difference in differences)
        return any(difference > threshold for difference in differences)

    def _consume_semantic_change(self) -> bool:
        changed = self._semantic_changed_unlocked()
        if changed:
            self._semantic_anchor = dict(self._values)
        return changed

    def _snapshot_unlocked(self) -> HomeostasisSnapshot:
        return HomeostasisSnapshot(
            updated_at_utc=self._updated_at_utc,
            activation=self._values["activation"],
            cognitive_load=self._values["cognitive_load"],
            certainty=self._values["certainty"],
            caution=self._values["caution"],
            curiosity=self._values["curiosity"],
            blockedness=self._values["blockedness"],
            social_presence=self._values["social_presence"],
        )


__all__ = [
    "BASELINES",
    "DEFAULT_SEMANTIC_THRESHOLD",
    "DEFAULT_ACTIVITY_WINDOW_MS",
    "HALF_LIVES_SECONDS",
    "HomeostasisReducer",
    "decay_toward_baseline",
    "thresholded_semantic_change",
]
