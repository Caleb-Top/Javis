"""Crash-isolated continuous native microphone service."""

from __future__ import annotations

from array import array
import base64
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable

from voice.stt import transcribe_pcm
from voice.streaming_pipeline import StreamingVoicePipeline, VoicePipelineConfig


ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "native_capture_worker.py"


def resample_pcm16_mono(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if len(pcm) % 2:
        raise ValueError("PCM16 data must contain an even byte count")
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if source_rate == target_rate or not samples:
        return pcm

    source = list(samples)
    output_count = max(1, round(len(source) * target_rate / source_rate))
    output = array("h")
    if source_rate % target_rate == 0:
        ratio = source_rate // target_rate
        for index in range(output_count):
            group = source[index * ratio:(index + 1) * ratio]
            if not group:
                break
            output.append(int(sum(group) / len(group)))
    else:
        scale = (len(source) - 1) / max(1, output_count - 1)
        for index in range(output_count):
            position = index * scale
            left = int(position)
            right = min(len(source) - 1, left + 1)
            fraction = position - left
            output.append(int(source[left] * (1.0 - fraction) + source[right] * fraction))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


class ContinuousVoiceService:
    """Turns native PCM frames into bounded, replayable UI events."""

    def __init__(
        self,
        *,
        transcribe: Callable[[bytes, int, bool], str] | None = None,
        event_limit: int = 256,
        pipeline_config: VoicePipelineConfig | None = None,
    ) -> None:
        self._transcribe = transcribe or (
            lambda pcm, sample_rate, final: transcribe_pcm(
                pcm,
                sample_rate=sample_rate,
                final=final,
                beam_size=2 if final else 1,
            )
        )
        self._event_limit = max(8, int(event_limit))
        self._base_config = pipeline_config or VoicePipelineConfig()
        self._pipeline: StreamingVoicePipeline | None = None
        self._events: deque[dict] = deque(maxlen=self._event_limit)
        self._sequence = 0
        self._condition = threading.Condition(threading.RLock())
        self._pipeline_lock = threading.RLock()
        self._pcm_buffer = bytearray()
        self._playback_buffer = bytearray()
        self._metadata: dict = {}
        self._transcription_condition = threading.Condition(threading.RLock())
        self._transcription_queue: deque[dict] = deque()
        self._transcription_limit = 3
        self._transcription_drops = 0
        self._transcription_generation = 0
        self._finalized_turn = 0
        self._last_partial: dict[int, str] = {}
        self._transcription_thread = threading.Thread(
            target=self._transcription_worker,
            name="javis-streaming-transcriber",
            daemon=True,
        )
        self._transcription_thread.start()

    def configure(self, *, noise_profile: str = "standard") -> dict:
        settings = dict(vars(self._base_config))
        settings["noise_profile"] = noise_profile
        with self._transcription_condition:
            self._transcription_generation += 1
            self._transcription_queue.clear()
            self._finalized_turn = 0
            self._last_partial.clear()
        with self._pipeline_lock:
            self._pipeline = StreamingVoicePipeline(
                config=VoicePipelineConfig(**settings),
                transcription_sink=self._enqueue_transcription,
            )
            self._pcm_buffer.clear()
            self._playback_buffer.clear()
        return self.status()

    def _enqueue_transcription(
        self,
        pcm: bytes,
        sample_rate: int,
        final: bool,
        metadata: dict,
    ) -> None:
        task = {
            "generation": self._transcription_generation,
            "pcm": bytes(pcm),
            "sample_rate": int(sample_rate),
            "final": bool(final),
            "turn": int(metadata.get("turn") or 0),
            "audio_ms": int(metadata.get("audio_ms") or 0),
            "input_rms": max(0.0, min(1.0, float(metadata.get("input_rms") or 0.0))),
            "input_peak": max(0.0, min(1.0, float(metadata.get("input_peak") or 0.0))),
        }
        with self._transcription_condition:
            if final:
                self._transcription_queue = deque(
                    queued
                    for queued in self._transcription_queue
                    if queued["final"] or queued["turn"] != task["turn"]
                )
            else:
                self._transcription_queue = deque(
                    queued
                    for queued in self._transcription_queue
                    if queued["final"] or queued["turn"] != task["turn"]
                )
            while len(self._transcription_queue) >= self._transcription_limit:
                self._transcription_queue.popleft()
                self._transcription_drops += 1
            self._transcription_queue.append(task)
            self._transcription_condition.notify()

    def _transcription_worker(self) -> None:
        while True:
            with self._transcription_condition:
                while not self._transcription_queue:
                    self._transcription_condition.wait()
                task = self._transcription_queue.popleft()
            try:
                text = str(
                    self._transcribe(
                        task["pcm"],
                        task["sample_rate"],
                        task["final"],
                    )
                    or ""
                ).strip()
            except Exception as error:
                self.mark_error(f"speech transcription failed: {error}")
                continue
            with self._transcription_condition:
                if task["generation"] != self._transcription_generation:
                    continue
                turn = task["turn"]
                if task["final"]:
                    if turn <= self._finalized_turn:
                        continue
                    self._finalized_turn = turn
                    self._last_partial.pop(turn, None)
                elif turn <= self._finalized_turn or self._last_partial.get(turn) == text:
                    continue
                else:
                    self._last_partial[turn] = text
            if task["final"]:
                if text:
                    self._publish(
                        {
                            "type": "transcript.final",
                            "text": text,
                            "turn": task["turn"],
                            "audio_ms": task["audio_ms"],
                        }
                    )
                else:
                    self._publish(
                        {
                            "type": "transcript.empty",
                            "turn": task["turn"],
                            "audio_ms": task["audio_ms"],
                            "input_rms": task["input_rms"],
                            "input_peak": task["input_peak"],
                        }
                    )
            elif text:
                self._publish(
                    {
                        "type": "transcript.partial",
                        "text": text,
                        "turn": task["turn"],
                    }
                )

    def _publish(self, event: dict) -> dict:
        public = {
            key: value
            for key, value in event.items()
            if key not in {"pcm", "audio", "audio_base64", "raw"}
        }
        with self._condition:
            self._sequence += 1
            public["sequence"] = self._sequence
            public["timestamp"] = time.time()
            self._events.append(public)
            self._condition.notify_all()
        return public

    def mark_ready(self, metadata: dict | None = None) -> dict:
        self._metadata = dict(metadata or {})
        return self._publish(
            {
                "type": "audio.stream.ready",
                "rate": int(self._metadata.get("rate") or 48_000),
                "trackLabel": str(self._metadata.get("trackLabel") or "Windows microphone"),
            }
        )

    def mark_stopped(self) -> dict:
        return self._publish({"type": "audio.stream.stopped"})

    def mark_error(self, message: str) -> dict:
        return self._publish({"type": "audio.error", "message": str(message)[:500]})

    def mark_overrun(self, dropped_frames: int) -> dict:
        return self._publish(
            {"type": "audio.overrun", "dropped_frames": max(1, int(dropped_frames))}
        )

    def ingest_pcm(self, pcm: bytes, source_rate: int) -> list[dict]:
        if self._pipeline is None:
            self.configure()
        assert self._pipeline is not None
        emitted: list[dict] = []
        with self._pipeline_lock:
            converted = resample_pcm16_mono(
                pcm,
                int(source_rate),
                self._pipeline.config.sample_rate,
            )
            self._pcm_buffer.extend(converted)
            frame_bytes = self._pipeline.frame_samples * 2
            while len(self._pcm_buffer) >= frame_bytes:
                frame = bytes(self._pcm_buffer[:frame_bytes])
                del self._pcm_buffer[:frame_bytes]
                for event in self._pipeline.push_frame(frame):
                    emitted.append(self._publish(event))
        return emitted

    def ingest_playback_pcm(self, pcm: bytes, source_rate: int) -> None:
        if self._pipeline is None:
            return
        with self._pipeline_lock:
            converted = resample_pcm16_mono(
                pcm,
                int(source_rate),
                self._pipeline.config.sample_rate,
            )
            self._playback_buffer.extend(converted)
            frame_bytes = self._pipeline.frame_samples * 2
            while len(self._playback_buffer) >= frame_bytes:
                frame = bytes(self._playback_buffer[:frame_bytes])
                del self._playback_buffer[:frame_bytes]
                self._pipeline.push_playback_frame(frame)

    def events_after(
        self,
        after_sequence: int,
        *,
        timeout: float = 0.0,
        limit: int = 64,
    ) -> list[dict]:
        after = max(0, int(after_sequence))
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while not any(event["sequence"] > after for event in self._events):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._condition.wait(remaining)
            return [
                dict(event)
                for event in self._events
                if event["sequence"] > after
            ][:max(1, min(int(limit), 256))]

    def status(self) -> dict:
        with self._pipeline_lock:
            diagnostics = self._pipeline.diagnostics() if self._pipeline else {
                "audio_processing_mode": "not-started",
                "raw_audio_persisted": False,
            }
        with self._transcription_condition:
            transcription_queue = len(self._transcription_queue)
        return {
            "configured": self._pipeline is not None,
            "last_sequence": self._sequence,
            "event_buffer": len(self._events),
            "transcription_queue": transcription_queue,
            "transcription_queue_limit": self._transcription_limit,
            "transcription_drops": self._transcription_drops,
            **self._metadata,
            **diagnostics,
        }


class NativeContinuousCaptureManager:
    """Owns the isolated worker and feeds its frames into the service."""

    def __init__(
        self,
        service: ContinuousVoiceService | None = None,
        *,
        frame_queue_size: int = 96,
    ) -> None:
        self.service = service or ContinuousVoiceService()
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._processor: threading.Thread | None = None
        self._stop_path: Path | None = None
        self._status_path: Path | None = None
        self._metadata: dict = {}
        self._session_id = ""
        self._frame_queue: queue.Queue[tuple[bytes, int] | None] = queue.Queue(
            maxsize=max(2, int(frame_queue_size))
        )
        self._dropped_capture_frames = 0

    @staticmethod
    def _creation_flags() -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    @staticmethod
    def _paths() -> tuple[Path, Path]:
        root = Path(tempfile.gettempdir()) / "javis-native-audio"
        root.mkdir(parents=True, exist_ok=True)
        capture_id = uuid.uuid4().hex
        return root / f"{capture_id}.stream.stop", root / f"{capture_id}.stream.json"

    @staticmethod
    def _read_status(path: Path) -> dict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def start(
        self,
        *,
        session_id: str,
        noise_profile: str = "standard",
        device_index: int | None = None,
    ) -> dict:
        session = str(session_id or "").strip()
        if not session:
            raise ValueError("continuous voice session_id is required")
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                if self._session_id != session:
                    raise RuntimeError("microphone stream belongs to another conversation")
                return {"ok": True, "running": True, **self.status()}
            if not WORKER.is_file():
                raise RuntimeError("native microphone worker is missing")
            stop_path, status_path = self._paths()
            command = [
                sys.executable,
                str(WORKER),
                "stream",
                str(stop_path),
                str(status_path),
                "48000",
            ]
            if device_index is not None:
                command.append(str(int(device_index)))
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=self._creation_flags(),
            )
            deadline = time.monotonic() + 8.0
            metadata = {}
            while time.monotonic() < deadline:
                metadata = self._read_status(status_path)
                if metadata:
                    break
                if process.poll() is not None:
                    error = process.stderr.read().strip() if process.stderr else ""
                    raise RuntimeError(error or "native microphone stream exited")
                time.sleep(0.04)
            if not metadata.get("ok"):
                process.kill()
                raise RuntimeError(str(metadata.get("error") or "native microphone stream timeout"))
            self._process = process
            self._stop_path = stop_path
            self._status_path = status_path
            self._metadata = metadata
            self._session_id = session
            self._frame_queue = queue.Queue(maxsize=self._frame_queue.maxsize)
            self._dropped_capture_frames = 0
            self.service.configure(noise_profile=noise_profile)
            self.service.mark_ready(metadata)
            self._processor = threading.Thread(
                target=self._process_frames,
                args=(process,),
                name="javis-continuous-audio-processor",
                daemon=True,
            )
            self._processor.start()
            self._reader = threading.Thread(
                target=self._read_worker,
                args=(process,),
                name="javis-continuous-audio",
                daemon=True,
            )
            self._reader.start()
            return {"ok": True, "running": True, **self.status()}

    def attach(self, *, session_id: str) -> dict:
        session = str(session_id or "").strip()
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            if not running:
                raise RuntimeError("native microphone stream is not running")
            if not session or self._session_id != session:
                raise RuntimeError("microphone stream belongs to another conversation")
        return {"ok": True, "running": True, **self.status()}

    def _read_worker(self, process: subprocess.Popen) -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    message = json.loads(line)
                    if message.get("type") != "frame":
                        continue
                    pcm = base64.b64decode(str(message.get("pcm") or ""), validate=True)
                    self._enqueue_frame(pcm, int(message.get("rate") or 48_000))
                except Exception as error:
                    self.service.mark_error(f"audio frame rejected: {error}")
        finally:
            with self._lock:
                active = process is self._process
            if active and process.poll() not in {None, 0}:
                self.service.mark_error("native microphone stream stopped unexpectedly")

    def _enqueue_frame(self, pcm: bytes, sample_rate: int) -> None:
        item = (bytes(pcm), int(sample_rate))
        try:
            self._frame_queue.put_nowait(item)
            return
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
            except queue.Empty:
                pass
        self._dropped_capture_frames += 1
        self.service.mark_overrun(self._dropped_capture_frames)
        try:
            self._frame_queue.put_nowait(item)
        except queue.Full:
            pass

    def _process_frames(self, process: subprocess.Popen) -> None:
        while True:
            try:
                item = self._frame_queue.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    active = process is self._process
                if not active or process.poll() is not None:
                    return
                continue
            if item is None:
                return
            with self._lock:
                if process is not self._process:
                    return
            pcm, sample_rate = item
            try:
                self.service.ingest_pcm(pcm, sample_rate)
            except Exception as error:
                self.service.mark_error(f"audio processing failed: {error}")

    def stop(self, *, session_id: str | None = None) -> dict:
        with self._lock:
            if session_id is not None and self._session_id != str(session_id).strip():
                raise RuntimeError("microphone stream belongs to another conversation")
            process = self._process
            stop_path = self._stop_path
            status_path = self._status_path
            self._process = None
            self._stop_path = None
            self._status_path = None
            self._session_id = ""
        if process is None:
            return {"ok": True, "running": False, **self.status()}
        if stop_path is not None:
            stop_path.touch(exist_ok=True)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        if self._reader is not None:
            self._reader.join(timeout=1)
        try:
            self._frame_queue.put_nowait(None)
        except queue.Full:
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.put_nowait(None)
            except (queue.Empty, queue.Full):
                pass
        if self._processor is not None:
            self._processor.join(timeout=2)
        for path in (stop_path, status_path):
            if path is not None:
                path.unlink(missing_ok=True)
        self.service.mark_stopped()
        return {"ok": True, "running": False, **self.status()}

    def status(self) -> dict:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
        return {
            "running": running,
            "session_attached": bool(self._session_id),
            "backend": "isolated-pyaudio-portaudio-stream",
            "native": True,
            "webview_permission_required": False,
            "capture_queue": self._frame_queue.qsize(),
            "capture_queue_limit": self._frame_queue.maxsize,
            "capture_dropped_frames": self._dropped_capture_frames,
            **self.service.status(),
        }

    def events_after(
        self,
        after_sequence: int,
        *,
        session_id: str | None = None,
        timeout: float = 0.0,
    ) -> list[dict]:
        if session_id is not None:
            with self._lock:
                if self._session_id != str(session_id).strip():
                    raise RuntimeError("microphone stream belongs to another conversation")
        return self.service.events_after(after_sequence, timeout=timeout)


continuous_capture_manager = NativeContinuousCaptureManager()
