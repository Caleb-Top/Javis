"""Injected clock readings and deterministic L1 decay helpers."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Mapping, Protocol

from .contracts import StateDimension


UTC = timezone.utc

HOMEOSTASIS_BASELINES: Mapping[StateDimension, float] = MappingProxyType(
    {
        StateDimension.ACTIVATION: 0.20,
        StateDimension.COGNITIVE_LOAD: 0.05,
        StateDimension.CERTAINTY: 0.50,
        StateDimension.CAUTION: 0.10,
        StateDimension.CURIOSITY: 0.25,
        StateDimension.BLOCKEDNESS: 0.00,
        StateDimension.SOCIAL_PRESENCE: 0.00,
    }
)

HOMEOSTASIS_HALF_LIVES_SECONDS: Mapping[StateDimension, float] = MappingProxyType(
    {
        StateDimension.ACTIVATION: 30.0,
        StateDimension.COGNITIVE_LOAD: 20.0,
        StateDimension.CERTAINTY: 90.0,
        StateDimension.CAUTION: 45.0,
        StateDimension.CURIOSITY: 60.0,
        StateDimension.BLOCKEDNESS: 30.0,
        StateDimension.SOCIAL_PRESENCE: 15.0,
    }
)


def parse_utc_milliseconds(value: str) -> datetime:
    """Parse the strict UTC millisecond form used by L1 wire contracts."""

    if type(value) is not str:
        raise TypeError("UTC timestamp must be a string")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise ValueError("UTC timestamp must use RFC3339 millisecond precision") from exc
    if len(value) != 24 or value[-5] != ".":
        raise ValueError("UTC timestamp must use RFC3339 millisecond precision")
    return parsed.replace(tzinfo=UTC)


def coerce_utc(value: datetime | str) -> datetime:
    """Return a timezone-aware UTC datetime without consulting wall time."""

    if isinstance(value, str):
        return parse_utc_milliseconds(value)
    if not isinstance(value, datetime):
        raise TypeError("UTC time must be a datetime or contract timestamp")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("UTC time must be timezone-aware")
    return value.astimezone(UTC)


def format_utc_milliseconds(value: datetime | str) -> str:
    """Format UTC with exactly three fractional digits."""

    normalized = coerce_utc(value)
    milliseconds = normalized.microsecond // 1000
    return normalized.strftime("%Y-%m-%dT%H:%M:%S") + f".{milliseconds:03d}Z"


def add_seconds(value: datetime | str, seconds: float) -> str:
    """Add a finite non-negative TTL and return contract timestamp form."""

    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise TypeError("seconds must be a finite number")
    numeric_seconds = float(seconds)
    if not math.isfinite(numeric_seconds) or numeric_seconds < 0.0:
        raise ValueError("seconds must be finite and non-negative")
    return format_utc_milliseconds(coerce_utc(value) + timedelta(seconds=numeric_seconds))


def elapsed_monotonic_seconds(previous: float, current: float) -> float:
    """Compute elapsed monotonic time; rollback never produces reverse decay."""

    values = (previous, current)
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in values):
        raise TypeError("monotonic values must be finite numbers")
    start, end = (float(item) for item in values)
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError("monotonic values must be finite")
    return max(0.0, end - start)


def decay_toward_baseline(
    value: float,
    baseline: float,
    elapsed_seconds: float,
    half_life_seconds: float,
) -> float:
    """Exponentially decay a bounded value toward its bounded baseline."""

    inputs = (value, baseline, elapsed_seconds, half_life_seconds)
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in inputs):
        raise TypeError("decay inputs must be finite numbers")
    current, resting, elapsed, half_life = (float(item) for item in inputs)
    if not all(math.isfinite(item) for item in (current, resting, elapsed, half_life)):
        raise ValueError("decay inputs must be finite")
    if not 0.0 <= current <= 1.0 or not 0.0 <= resting <= 1.0:
        raise ValueError("value and baseline must be in [0, 1]")
    if elapsed < 0.0:
        raise ValueError("elapsed_seconds must be non-negative")
    if half_life <= 0.0:
        raise ValueError("half_life_seconds must be positive")
    decayed = resting + (current - resting) * math.exp2(-elapsed / half_life)
    return min(1.0, max(0.0, decayed))


@dataclass(frozen=True)
class ClockReading:
    """One coherent injected UTC and monotonic clock sample."""

    utc: datetime
    monotonic_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "utc", coerce_utc(self.utc))
        if isinstance(self.monotonic_seconds, bool) or not isinstance(
            self.monotonic_seconds, (int, float)
        ):
            raise TypeError("monotonic_seconds must be a finite number")
        monotonic = float(self.monotonic_seconds)
        if not math.isfinite(monotonic) or monotonic < 0.0:
            raise ValueError("monotonic_seconds must be finite and non-negative")
        object.__setattr__(self, "monotonic_seconds", monotonic)

    @property
    def utc_timestamp(self) -> str:
        return format_utc_milliseconds(self.utc)


class Clock(Protocol):
    def read(self) -> ClockReading:
        """Return one coherent clock sample."""


class SystemClock:
    """Production adapter; reducers receive its reading, never this clock."""

    def read(self) -> ClockReading:
        return ClockReading(datetime.now(UTC), time.monotonic())


class ManualClock:
    """Mutable deterministic clock for replay and tests."""

    def __init__(self, utc: datetime | str, monotonic_seconds: float = 0.0) -> None:
        self._reading = ClockReading(coerce_utc(utc), monotonic_seconds)

    def read(self) -> ClockReading:
        return self._reading

    def advance(self, seconds: float) -> ClockReading:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise TypeError("seconds must be a finite number")
        elapsed = float(seconds)
        if not math.isfinite(elapsed) or elapsed < 0.0:
            raise ValueError("seconds must be finite and non-negative")
        self._reading = ClockReading(
            self._reading.utc + timedelta(seconds=elapsed),
            self._reading.monotonic_seconds + elapsed,
        )
        return self._reading

    def set_utc(self, utc: datetime | str) -> ClockReading:
        """Move display UTC independently without changing monotonic time."""

        self._reading = ClockReading(coerce_utc(utc), self._reading.monotonic_seconds)
        return self._reading


__all__ = [
    "Clock",
    "ClockReading",
    "HOMEOSTASIS_BASELINES",
    "HOMEOSTASIS_HALF_LIVES_SECONDS",
    "ManualClock",
    "SystemClock",
    "add_seconds",
    "coerce_utc",
    "decay_toward_baseline",
    "elapsed_monotonic_seconds",
    "format_utc_milliseconds",
    "parse_utc_milliseconds",
]
