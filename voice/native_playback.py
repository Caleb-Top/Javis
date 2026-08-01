"""Native Windows speech playback with a synchronized echo-reference feed."""

from __future__ import annotations

from array import array
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


class NativeWavePlayer(Protocol):
    def play(self, path: Path) -> None: ...
    def stop(self) -> None: ...


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

    def __init__(self, *, service, player: NativeWavePlayer | None = None) -> None:
        self.service = service
        self.player = player or WindowsWavePlayer()
        self._lock = threading.RLock()
        self._stop_event: threading.Event | None = None
        self._reference_thread: threading.Thread | None = None
        self._path: Path | None = None
        self._active = False
        self._generation = 0
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

    def play_wav(self, audio: bytes) -> dict:
        sample_rate, channels, pcm, duration_ms = self._inspect_wav(audio)
        mono = _pcm16_mono(pcm, channels)
        self.stop()
        return self.play_reserved_wav(audio, mono, sample_rate, duration_ms, self._generation)

    def reserve(self) -> int:
        """Stop current speech and return a token invalidated by the next stop."""
        self.stop()
        with self._lock:
            return self._generation

    def play_reserved_wav(
        self,
        audio: bytes,
        mono: bytes | None = None,
        sample_rate: int | None = None,
        duration_ms: int | None = None,
        reservation: int | None = None,
    ) -> dict:
        if mono is None or sample_rate is None or duration_ms is None:
            sample_rate, channels, pcm, duration_ms = self._inspect_wav(audio)
            mono = _pcm16_mono(pcm, channels)
        with self._lock:
            if reservation is not None and reservation != self._generation:
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
            self._path = path
            self._stop_event = stop_event
            self._active = True
            self._started_at = time.time()
            self._duration_ms = duration_ms
        try:
            self.player.play(path)
        except Exception:
            with self._lock:
                self._active = False
            path.unlink(missing_ok=True)
            raise
        thread = threading.Thread(
            target=self._feed_reference,
            args=(generation, stop_event, mono, sample_rate, path),
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
        }

    def _feed_reference(
        self,
        generation: int,
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
                if generation == self._generation:
                    self._active = False
                    self._path = None
                    self._stop_event = None
            path.unlink(missing_ok=True)

    def stop(self) -> dict:
        with self._lock:
            event = self._stop_event
            thread = self._reference_thread
            path = self._path
            self._generation += 1
            self._stop_event = None
            self._reference_thread = None
            self._path = None
            self._active = False
        if event is not None:
            event.set()
        self.player.stop()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.4)
        if path is not None:
            path.unlink(missing_ok=True)
        return {"ok": True, "active": False, "native": True}

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
                "raw_microphone_audio_persisted": False,
            }
