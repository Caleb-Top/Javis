"""Native Windows speech playback with a synchronized echo-reference feed."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid
import wave
from typing import Protocol

from core.life.l1.contracts import PlaybackOutcome
from voice.playback_events import PlaybackLifecyclePublisher


class NativeWavePlayer(Protocol):
    def play(self, path: Path) -> None: ...
    def stop(self) -> None: ...


@dataclass(frozen=True)
class _PlaybackContext:
    playback_id: str
    generation: int
    session_id: str
    request_id: str


class WindowsWavePlayer:
    def play(self, path: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("native speech playback requires Windows")
        import winsound

        winsound.PlaySound(
            str(path),
            winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )

    def stop(self) -> None:
        if os.name != "nt":
            return
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)


def _pcm16_mono(pcm: bytes, channels: int) -> bytes:
    if channels == 1:
        return pcm
    if channels < 1:
        raise ValueError("WAV channel count must be positive")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    mono = array("h")
    for offset in range(0, len(samples) - channels + 1, channels):
        mono.append(int(sum(samples[offset:offset + channels]) / channels))
    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


class NativePlaybackManager:
    """Plays local WAV speech and mirrors it into the microphone AEC path."""

    def __init__(
        self,
        *,
        service,
        player: NativeWavePlayer | None = None,
        lifecycle: PlaybackLifecyclePublisher | None = None,
    ) -> None:
        self.service = service
        self.player = player or WindowsWavePlayer()
        self.lifecycle = lifecycle
        self._lock = threading.RLock()
        self._stop_event: threading.Event | None = None
        self._reference_thread: threading.Thread | None = None
        self._path: Path | None = None
        self._active = False
        self._generation = 0
        self._reservation_consumed = False
        self._playback: _PlaybackContext | None = None
        self._started_at = 0.0
        self._duration_ms = 0

    @staticmethod
    def _playback_path() -> Path:
        root = Path(tempfile.gettempdir()) / "javis-native-playback"
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{uuid.uuid4().hex}.wav"

    @staticmethod
    def _inspect_wav(audio: bytes) -> tuple[int, int, bytes, int]:
        try:
            with wave.open(io.BytesIO(audio), "rb") as stream:
                channels = stream.getnchannels()
                sample_width = stream.getsampwidth()
                sample_rate = stream.getframerate()
                frame_count = stream.getnframes()
                pcm = stream.readframes(frame_count)
        except (wave.Error, EOFError) as error:
            raise ValueError(f"invalid WAV speech data: {error}") from error
        if sample_width != 2:
            raise ValueError("native speech playback requires PCM16 WAV")
        if sample_rate < 8_000 or sample_rate > 48_000:
            raise ValueError("native speech WAV sample rate is unsupported")
        duration_ms = round(frame_count * 1000 / max(1, sample_rate))
        return sample_rate, channels, pcm, duration_ms

    def play_wav(
        self,
        audio: bytes,
        *,
        session_id: str = "",
        request_id: str = "",
    ) -> dict:
        sample_rate, channels, pcm, duration_ms = self._inspect_wav(audio)
        mono = _pcm16_mono(pcm, channels)
        self._validate_lifecycle_identity(session_id, request_id)
        reservation = self.reserve()
        return self.play_reserved_wav(
            audio,
            mono,
            sample_rate,
            duration_ms,
            reservation,
            session_id=session_id,
            request_id=request_id,
        )

    def reserve(self) -> int:
        """Stop current speech and return a token invalidated by the next stop."""
        self._stop_current(
            outcome=PlaybackOutcome.CANCELLED,
            reason_code="playback_superseded",
        )
        with self._lock:
            return self._generation

    def play_reserved_wav(
        self,
        audio: bytes,
        mono: bytes | None = None,
        sample_rate: int | None = None,
        duration_ms: int | None = None,
        reservation: int | None = None,
        *,
        session_id: str = "",
        request_id: str = "",
    ) -> dict:
        self._validate_lifecycle_identity(session_id, request_id)
        if mono is None or sample_rate is None or duration_ms is None:
            sample_rate, channels, pcm, duration_ms = self._inspect_wav(audio)
            mono = _pcm16_mono(pcm, channels)
        with self._lock:
            if reservation is not None and (
                reservation != self._generation or self._reservation_consumed
            ):
                return {
                    "ok": False,
                    "active": False,
                    "cancelled": True,
                    "native": True,
                }
            generation = self._generation
        path = self._playback_path()
        path.write_bytes(audio)
        stop_event = threading.Event()
        with self._lock:
            if generation != self._generation:
                path.unlink(missing_ok=True)
                return {
                    "ok": False,
                    "active": False,
                    "cancelled": True,
                    "native": True,
                }
            if reservation is not None:
                self._reservation_consumed = True
            playback = _PlaybackContext(
                playback_id=uuid.uuid4().hex,
                generation=generation,
                session_id=session_id,
                request_id=request_id,
            )
            self._path = path
            self._stop_event = stop_event
            self._active = True
            self._playback = playback
            self._started_at = time.time()
            self._duration_ms = duration_ms
        try:
            self.player.play(path)
        except Exception:
            with self._lock:
                if self._playback == playback:
                    self._clear_active_locked()
                    self._publish_locked(
                        playback,
                        PlaybackOutcome.FAILED,
                        "playback_failed",
                    )
            path.unlink(missing_ok=True)
            raise
        with self._lock:
            if self._playback != playback or generation != self._generation:
                self.player.stop()
                path.unlink(missing_ok=True)
                return {
                    "ok": False,
                    "active": False,
                    "cancelled": True,
                    "native": True,
                }
            self._publish_locked(
                playback,
                PlaybackOutcome.STARTED,
                "playback_started",
                playback_duration_seconds=duration_ms / 1000.0,
            )
        thread = threading.Thread(
            target=self._feed_reference,
            args=(playback, stop_event, mono, sample_rate, path),
            name="javis-native-playback-reference",
            daemon=True,
        )
        with self._lock:
            self._reference_thread = thread
        thread.start()
        return {
            "ok": True,
            "active": True,
            "native": True,
            "duration_ms": duration_ms,
            "aec_reference": True,
            "playback_id": playback.playback_id,
            "generation": playback.generation,
        }

    def _feed_reference(
        self,
        playback: _PlaybackContext,
        stop_event: threading.Event,
        pcm: bytes,
        sample_rate: int,
        path: Path,
    ) -> None:
        frame_bytes = max(1, sample_rate // 50) * 2
        try:
            for offset in range(0, len(pcm), frame_bytes):
                if stop_event.is_set():
                    break
                frame = pcm[offset:offset + frame_bytes]
                if len(frame) < frame_bytes:
                    frame += b"\0" * (frame_bytes - len(frame))
                self.service.ingest_playback_pcm(frame, sample_rate)
                if stop_event.wait(0.02):
                    break
        finally:
            with self._lock:
                if self._playback == playback:
                    self._clear_active_locked()
                    self._publish_locked(
                        playback,
                        PlaybackOutcome.COMPLETED,
                        "playback_completed",
                    )
            path.unlink(missing_ok=True)

    def stop(
        self,
        *,
        playback_id: str | None = None,
        generation: int | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        scoped = (playback_id, generation, session_id, request_id)
        expected: _PlaybackContext | None = None
        if any(value is not None for value in scoped):
            if any(value is None for value in scoped):
                raise ValueError("scoped stop requires complete playback identity")
            if type(generation) is not int or generation < 0:
                raise ValueError("invalid playback generation")
            assert playback_id is not None
            assert session_id is not None
            assert request_id is not None
            self._validate_stop_id(playback_id, "playback_id")
            self._validate_stop_id(session_id, "session_id")
            self._validate_stop_id(request_id, "request_id")
            expected = _PlaybackContext(
                playback_id=playback_id,
                generation=generation,
                session_id=session_id,
                request_id=request_id,
            )
        return self._stop_current(
            outcome=PlaybackOutcome.STOPPED,
            reason_code="playback_stopped",
            expected=expected,
        )

    def _stop_current(
        self,
        *,
        outcome: PlaybackOutcome,
        reason_code: str,
        expected: _PlaybackContext | None = None,
    ) -> dict:
        with self._lock:
            if expected is not None and self._playback != expected:
                return {
                    "ok": True,
                    "active": self._active,
                    "native": True,
                    "stopped": False,
                    "stale": True,
                }
            event = self._stop_event
            thread = self._reference_thread
            path = self._path
            playback = self._playback
            self._generation += 1
            self._reservation_consumed = False
            self._clear_active_locked()
            if playback is not None:
                self._publish_locked(playback, outcome, reason_code)
        if event is not None:
            event.set()
        self.player.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.4)
        if path is not None:
            path.unlink(missing_ok=True)
        return {
            "ok": True,
            "active": False,
            "native": True,
            "stopped": playback is not None,
            "stale": False,
        }

    def _clear_active_locked(self) -> None:
        self._stop_event = None
        self._reference_thread = None
        self._path = None
        self._active = False
        self._playback = None

    def _publish_locked(
        self,
        playback: _PlaybackContext,
        outcome: PlaybackOutcome,
        reason_code: str,
        *,
        playback_duration_seconds: float | None = None,
    ) -> None:
        if self.lifecycle is None:
            return
        self.lifecycle.publish(
            playback_id=playback.playback_id,
            generation=playback.generation,
            session_id=playback.session_id,
            request_id=playback.request_id,
            outcome=outcome,
            reason_code=reason_code,
            playback_duration_seconds=playback_duration_seconds,
        )

    def _validate_lifecycle_identity(
        self,
        session_id: str,
        request_id: str,
    ) -> None:
        if self.lifecycle is None:
            return
        for field_name, value in (
            ("session_id", session_id),
            ("request_id", request_id),
        ):
            if type(value) is not str or not value or len(value) > 256:
                raise ValueError(f"invalid {field_name}")
            if any(character in value for character in "\r\n\x00"):
                raise ValueError(f"invalid {field_name}")

    @staticmethod
    def _validate_stop_id(value: object, field_name: str) -> None:
        if type(value) is not str or not value or len(value) > 256 or any(
            character in value for character in "\r\n\x00"
        ):
            raise ValueError(f"invalid {field_name}")

    def status(self) -> dict:
        with self._lock:
            return {
                "active": self._active,
                "native": os.name == "nt" or not isinstance(self.player, WindowsWavePlayer),
                "backend": "windows-winsound" if os.name == "nt" else "unavailable",
                "webview_audio_used": False,
                "aec_reference": True,
                "started_at": self._started_at,
                "duration_ms": self._duration_ms,
                "playback_id": (
                    self._playback.playback_id if self._playback is not None else None
                ),
                "generation": (
                    self._playback.generation
                    if self._playback is not None
                    else self._generation
                ),
                "session_id": (
                    self._playback.session_id if self._playback is not None else None
                ),
                "request_id": (
                    self._playback.request_id if self._playback is not None else None
                ),
                "raw_microphone_audio_persisted": False,
            }
