"""Non-blocking assembly for the bounded voice runtime diagnostics contract."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import time
from collections.abc import Callable
from typing import Any


DiagnosticsGetter = Callable[[], dict[str, Any]]


class VoiceDiagnosticsCollector:
    """Collect voice diagnostics without blocking the FastAPI event loop.

    Native device discovery may launch a helper process and take several
    seconds.  Its result is therefore shared behind a short-lived cache while
    the inexpensive in-memory snapshots remain fresh on every request.
    """

    def __init__(
        self,
        *,
        capture_getter: DiagnosticsGetter,
        continuous_getter: DiagnosticsGetter,
        gateway_getter: DiagnosticsGetter,
        playback_getter: DiagnosticsGetter,
        stt_getter: DiagnosticsGetter,
        tts_getter: DiagnosticsGetter,
        capture_ttl: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capture_getter = capture_getter
        self._continuous_getter = continuous_getter
        self._gateway_getter = gateway_getter
        self._playback_getter = playback_getter
        self._stt_getter = stt_getter
        self._tts_getter = tts_getter
        self._capture_ttl = max(0.0, float(capture_ttl))
        self._clock = clock
        self._capture_lock = asyncio.Lock()
        self._capture_cache: dict[str, Any] | None = None
        self._capture_expires_at = 0.0

    @staticmethod
    def _copy_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise TypeError("voice diagnostics getter must return a dict")
        return deepcopy(value)

    async def _capture(self) -> dict[str, Any]:
        now = self._clock()
        if self._capture_cache is not None and now < self._capture_expires_at:
            return deepcopy(self._capture_cache)
        async with self._capture_lock:
            now = self._clock()
            if self._capture_cache is None or now >= self._capture_expires_at:
                value = await asyncio.to_thread(self._capture_getter)
                self._capture_cache = self._copy_mapping(value)
                self._capture_expires_at = self._clock() + self._capture_ttl
            return deepcopy(self._capture_cache)

    async def collect(self) -> dict[str, Any]:
        capture, continuous, gateway, playback, stt, tts = await asyncio.gather(
            self._capture(),
            asyncio.to_thread(self._continuous_getter),
            asyncio.to_thread(self._gateway_getter),
            asyncio.to_thread(self._playback_getter),
            asyncio.to_thread(self._stt_getter),
            asyncio.to_thread(self._tts_getter),
        )
        return {
            "schema_version": 1,
            "capture": self._copy_mapping(capture),
            "continuous": self._copy_mapping(continuous),
            "gateway": self._copy_mapping(gateway),
            "playback": self._copy_mapping(playback),
            "stt": self._copy_mapping(stt),
            "tts": self._copy_mapping(tts),
        }
