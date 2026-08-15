"""Request-scoped native playback lifecycle publication."""

from __future__ import annotations

import inspect
import logging
import math
import threading
from collections.abc import Callable
from typing import Any

from core.life.l1.clock import Clock, SystemClock
from core.life.l1.contracts import PlaybackLifecycleEvent, PlaybackOutcome


logger = logging.getLogger("jarvis.voice.playback")


class PlaybackLifecyclePublisher:
    """Build strict lifecycle contracts and isolate the playback path from sinks."""

    def __init__(
        self,
        *,
        runtime_boot_id: str,
        sink: Callable[[PlaybackLifecycleEvent], Any] | None = None,
        duration_sink: Callable[[PlaybackLifecycleEvent, float | None], Any]
        | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.runtime_boot_id = _identifier(runtime_boot_id, "runtime_boot_id")
        if (sink is None) == (duration_sink is None):
            raise TypeError("exactly one playback lifecycle sink is required")
        if sink is not None and not callable(sink):
            raise TypeError("sink must be callable")
        if duration_sink is not None and not callable(duration_sink):
            raise TypeError("duration_sink must be callable")
        self._sink = sink
        self._duration_sink = duration_sink
        self._clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._published = 0
        self._sink_failures = 0

    def publish(
        self,
        *,
        playback_id: str,
        generation: int,
        session_id: str,
        request_id: str,
        outcome: PlaybackOutcome | str,
        reason_code: str,
        playback_duration_seconds: float | None = None,
    ) -> PlaybackLifecycleEvent:
        duration = _optional_duration(playback_duration_seconds)
        event = PlaybackLifecycleEvent(
            schema_version=1,
            playback_id=_identifier(playback_id, "playback_id"),
            generation=_generation(generation),
            runtime_boot_id=self.runtime_boot_id,
            session_id=_identifier(session_id, "session_id"),
            request_id=_identifier(request_id, "request_id"),
            outcome=outcome,
            occurred_at_utc=self._clock.read().utc_timestamp,
            reason_code=reason_code,
        )
        try:
            if self._duration_sink is not None:
                result = self._duration_sink(event, duration)
            else:
                assert self._sink is not None
                result = self._sink(event)
            if inspect.isawaitable(result):
                close = getattr(result, "close", None)
                if callable(close):
                    close()
                raise TypeError("playback lifecycle sink must be synchronous")
        except Exception as exc:
            with self._lock:
                self._sink_failures += 1
            logger.warning("Playback lifecycle sink failed: %s", type(exc).__name__)
        else:
            with self._lock:
                self._published += 1
        return event

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "published": self._published,
                "sink_failures": self._sink_failures,
            }


def _identifier(value: Any, field_name: str) -> str:
    if type(value) is not str or not value or len(value) > 256:
        raise ValueError(f"invalid {field_name}")
    if any(character in value for character in "\r\n\x00"):
        raise ValueError(f"invalid {field_name}")
    return value


def _generation(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid generation")
    return value


def _optional_duration(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid playback_duration_seconds")
    duration = float(value)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("invalid playback_duration_seconds")
    return duration


__all__ = ["PlaybackLifecyclePublisher"]
