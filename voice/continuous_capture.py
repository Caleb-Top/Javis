"""Crash-isolated continuous native microphone service."""

from __future__ import annotations

from array import array
import base64
from collections import deque
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import secrets
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
MAX_DIAGNOSTIC_COUNTER = (1 << 63) - 1
_OWNER_IDENTITY_SECRET = secrets.token_bytes(32)


def _bounded_increment(value: int, amount: int = 1) -> int:
    return min(MAX_DIAGNOSTIC_COUNTER, max(0, int(value)) + max(0, int(amount)))


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
        self._transcription_queue_peak = 0
        self._transcription_generation = 0
        self._finalized_turn = 0
        self._terminal_turn_ranges: list[tuple[int, int]] = []
        self._last_partial: dict[int, str] = {}
        self._event_counters = {
            "transcript_final": 0,
            "transcript_empty": 0,
            "audio_error": 0,
        }
        self._transcription_thread = threading.Thread(
            target=self._transcription_worker,
            name="javis-streaming-transcriber",
            daemon=True,
        )
        self._transcription_thread.start()

    def configure(self, *, noise_profile: str = "standard") -> dict:
        settings = dict(vars(self._base_config))
        settings["noise_profile"] = noise_profile
        with self._pipeline_lock:
            with self._transcription_condition:
                self._transcription_generation += 1
                self._transcription_queue.clear()
                self._transcription_drops = 0
                self._transcription_queue_peak = 0
                self._finalized_turn = 0
                self._terminal_turn_ranges.clear()
                self._last_partial.clear()
            with self._condition:
                self._events.clear()
                self._metadata = {}
                self._event_counters = {
                    "transcript_final": 0,
                    "transcript_empty": 0,
                    "audio_error": 0,
                }
            self._pipeline = StreamingVoicePipeline(
                config=VoicePipelineConfig(**settings),
                transcription_sink=self._enqueue_transcription,
            )
            self._pcm_buffer.clear()
            self._playback_buffer.clear()
        return self.status()

    def _is_terminal_turn(self, turn: int) -> bool:
        """Return terminal membership while the transcription condition is held."""
        target = int(turn)
        for start, end in self._terminal_turn_ranges:
            if target < start:
                return False
            if target <= end:
                return True
        return False

    def _record_terminal_turn(self, turn: int) -> bool:
        """Record one terminal turn and merge ranges under the transcription condition."""
        target = int(turn)
        if self._is_terminal_turn(target):
            return False

        merged_start = target
        merged_end = target
        merged_ranges: list[tuple[int, int]] = []
        inserted = False
        for start, end in self._terminal_turn_ranges:
            if end + 1 < merged_start:
                merged_ranges.append((start, end))
            elif merged_end + 1 < start:
                if not inserted:
                    merged_ranges.append((merged_start, merged_end))
                    inserted = True
                merged_ranges.append((start, end))
            else:
                merged_start = min(merged_start, start)
                merged_end = max(merged_end, end)
        if not inserted:
            merged_ranges.append((merged_start, merged_end))
        self._terminal_turn_ranges = merged_ranges
        return True

    def _enqueue_transcription(
        self,
        pcm: bytes,
        sample_rate: int,
        final: bool,
        metadata: dict,
    ) -> None:
        with self._transcription_condition:
            task = {
                "generation": self._transcription_generation,
                "pcm": bytes(pcm),
                "sample_rate": int(sample_rate),
                "final": bool(final),
                "turn": int(metadata.get("turn") or 0),
                "audio_ms": int(metadata.get("audio_ms") or 0),
                "input_rms": max(
                    0.0,
                    min(1.0, float(metadata.get("input_rms") or 0.0)),
                ),
                "input_peak": max(
                    0.0,
                    min(1.0, float(metadata.get("input_peak") or 0.0)),
                ),
            }
            if task["final"] and self._is_terminal_turn(task["turn"]):
                return
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
                dropped = next(
                    (
                        queued
                        for queued in self._transcription_queue
                        if not queued["final"]
                    ),
                    None,
                )
                if dropped is None:
                    dropped = self._transcription_queue.popleft()
                else:
                    self._transcription_queue.remove(dropped)
                self._transcription_drops = _bounded_increment(
                    self._transcription_drops
                )
                dropped.pop("pcm", None)
                if dropped["final"]:
                    turn = dropped["turn"]
                    if self._record_terminal_turn(turn):
                        self._finalized_turn = max(self._finalized_turn, turn)
                        self._last_partial.pop(turn, None)
                        self._publish(
                            {
                                "type": "transcript.empty",
                                "turn": turn,
                                "audio_ms": dropped["audio_ms"],
                                "input_rms": dropped["input_rms"],
                                "input_peak": dropped["input_peak"],
                            }
                        )
            self._transcription_queue.append(task)
            self._transcription_queue_peak = max(
                self._transcription_queue_peak,
                len(self._transcription_queue),
            )
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
                with self._transcription_condition:
                    if task["generation"] != self._transcription_generation:
                        continue
                    if task["final"]:
                        turn = task["turn"]
                        if not self._record_terminal_turn(turn):
                            continue
                        self._finalized_turn = max(self._finalized_turn, turn)
                        self._last_partial.pop(turn, None)
                    self.mark_error(f"speech transcription failed: {error}")
                continue
            with self._transcription_condition:
                if task["generation"] != self._transcription_generation:
                    continue
                turn = task["turn"]
                if task["final"]:
                    if not self._record_terminal_turn(turn):
                        continue
                    self._finalized_turn = max(self._finalized_turn, turn)
                    self._last_partial.pop(turn, None)
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
                elif turn <= self._finalized_turn or self._last_partial.get(turn) == text:
                    continue
                else:
                    self._last_partial[turn] = text
                    if text:
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
            event_type = str(public.get("type") or "")
            counter_name = {
                "transcript.final": "transcript_final",
                "transcript.empty": "transcript_empty",
                "audio.error": "audio_error",
            }.get(event_type)
            if counter_name is not None:
                self._event_counters[counter_name] = _bounded_increment(
                    self._event_counters[counter_name]
                )
            self._sequence += 1
            public["sequence"] = self._sequence
            public["timestamp"] = time.time()
            self._events.append(public)
            self._condition.notify_all()
        return public

    def mark_ready(self, metadata: dict | None = None) -> dict:
        current_metadata = dict(metadata or {})
        with self._condition:
            self._metadata = current_metadata
        ready = {
            "type": "audio.stream.ready",
            "rate": int(current_metadata.get("rate") or 48_000),
            "trackLabel": str(
                current_metadata.get("trackLabel") or "Windows microphone"
            ),
        }
        owner_generation = int(current_metadata.get("owner_generation") or 0)
        if owner_generation > 0:
            ready["owner_generation"] = owner_generation
        return self._publish(ready)

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
        # Match configure's lock order so one snapshot cannot mix generations.
        with self._pipeline_lock, self._transcription_condition, self._condition:
            diagnostics = self._pipeline.diagnostics() if self._pipeline else {
                "audio_processing_mode": "not-started",
                "raw_audio_persisted": False,
                "frames": 0,
                "turns": 0,
                "dropped_frames": 0,
                "buffered_frames": 0,
            }
            transcription_queue = len(self._transcription_queue)
            transcription_queue_peak = self._transcription_queue_peak
            transcription_drops = self._transcription_drops
            transcription_generation = self._transcription_generation
            event_counters = dict(self._event_counters)
            last_sequence = self._sequence
            event_buffer = len(self._events)
            metadata = dict(self._metadata)
            configured = self._pipeline is not None
        counters = {
            "frames": int(diagnostics.get("frames") or 0),
            "turns": int(diagnostics.get("turns") or 0),
            **event_counters,
        }
        transcription_diagnostics = {
            "current": transcription_queue,
            "peak": transcription_queue_peak,
            "limit": self._transcription_limit,
            "dropped": transcription_drops,
        }
        return {
            "configured": configured,
            "service_generation": transcription_generation,
            "last_sequence": last_sequence,
            "event_buffer": event_buffer,
            "transcription_queue": transcription_queue,
            "transcription_queue_peak": transcription_queue_peak,
            "transcription_queue_limit": self._transcription_limit,
            "transcription_drops": transcription_drops,
            "counters": counters,
            "queues": {"transcription": transcription_diagnostics},
            **metadata,
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
        self._owner_generation = 0
        self._owner_identity = ""
        self._frame_queue: queue.Queue[tuple[bytes, int, int] | None] = queue.Queue(
            maxsize=max(2, int(frame_queue_size))
        )
        self._dropped_capture_frames = 0
        self._capture_queue_peak = 0
        self._capture_frames_received = 0
        self._noise_profile = "standard"
        self._stopping = False
        self._lifecycle_condition = threading.Condition(self._lock)

    @staticmethod
    def _fingerprint_session(session_id: str) -> str:
        session = str(session_id or "").encode("utf-8", errors="replace")
        return hmac.new(
            _OWNER_IDENTITY_SECRET,
            session,
            hashlib.sha256,
        ).hexdigest()[:16]

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
        with self._lifecycle_condition:
            while self._stopping:
                self._lifecycle_condition.wait()
            if self._process is not None and self._process.poll() is None:
                if self._session_id != session:
                    raise RuntimeError("microphone stream belongs to another conversation")
                self._rotate_owner_locked(session, noise_profile)
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
            self._rotate_owner_locked(session, noise_profile)
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

    def _rotate_owner_locked(self, session: str, noise_profile: str) -> None:
        """Issue a fresh lease while the lifecycle lock is held."""
        self._frame_queue = queue.Queue(maxsize=self._frame_queue.maxsize)
        self._dropped_capture_frames = 0
        self._capture_queue_peak = 0
        self._capture_frames_received = 0
        next_owner_generation = _bounded_increment(self._owner_generation)
        self.service.configure(noise_profile=noise_profile)
        self._owner_generation = next_owner_generation
        self._owner_identity = self._fingerprint_session(session)
        self._noise_profile = noise_profile
        self.service.mark_ready(
            {**self._metadata, "owner_generation": self._owner_generation}
        )

    def attach(self, *, session_id: str) -> dict:
        session = str(session_id or "").strip()
        with self._lifecycle_condition:
            while self._stopping:
                self._lifecycle_condition.wait()
            running = self._process is not None and self._process.poll() is None
            if not running:
                raise RuntimeError("native microphone stream is not running")
            if not session or self._session_id != session:
                raise RuntimeError("microphone stream belongs to another conversation")
            self._rotate_owner_locked(session, self._noise_profile)
            return {"ok": True, "running": True, **self.status()}

    def _read_worker(self, process: subprocess.Popen) -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                owner_generation = 0
                try:
                    with self._lock:
                        if process is not self._process:
                            return
                        owner_generation = self._owner_generation
                    message = json.loads(line)
                    if message.get("type") != "frame":
                        continue
                    pcm = base64.b64decode(str(message.get("pcm") or ""), validate=True)
                    self._enqueue_frame(
                        pcm,
                        int(message.get("rate") or 48_000),
                        owner_generation=owner_generation,
                    )
                except Exception as error:
                    with self._lock:
                        active = (
                            process is self._process
                            and owner_generation == self._owner_generation
                        )
                    if active:
                        self.service.mark_error(f"audio frame rejected: {error}")
        finally:
            with self._lock:
                active = process is self._process
            if active and process.poll() not in {None, 0}:
                self.service.mark_error("native microphone stream stopped unexpectedly")

    def _enqueue_frame(
        self,
        pcm: bytes,
        sample_rate: int,
        *,
        owner_generation: int | None = None,
    ) -> None:
        dropped_now = 0
        with self._lock:
            generation = (
                self._owner_generation
                if owner_generation is None
                else int(owner_generation)
            )
            if generation != self._owner_generation:
                return
            item = (bytes(pcm), int(sample_rate), generation)
            self._capture_frames_received = _bounded_increment(
                self._capture_frames_received
            )
            try:
                self._frame_queue.put_nowait(item)
                self._capture_queue_peak = max(
                    self._capture_queue_peak,
                    self._frame_queue.qsize(),
                )
                return
            except queue.Full:
                try:
                    self._frame_queue.get_nowait()
                    dropped_now += 1
                except queue.Empty:
                    pass
            try:
                self._frame_queue.put_nowait(item)
                self._capture_queue_peak = max(
                    self._capture_queue_peak,
                    self._frame_queue.qsize(),
                )
            except queue.Full:
                dropped_now += 1
            if dropped_now:
                self._dropped_capture_frames = _bounded_increment(
                    self._dropped_capture_frames,
                    dropped_now,
                )
            dropped_capture_frames = self._dropped_capture_frames
        if dropped_now:
            self.service.mark_overrun(dropped_capture_frames)

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
            pcm, sample_rate, owner_generation = item
            with self._lock:
                if (
                    process is not self._process
                    or owner_generation != self._owner_generation
                ):
                    continue
                try:
                    self.service.ingest_pcm(pcm, sample_rate)
                except Exception as error:
                    self.service.mark_error(f"audio processing failed: {error}")

    def stop(
        self,
        *,
        session_id: str | None = None,
        owner_generation: int | None = None,
    ) -> dict:
        with self._lifecycle_condition:
            while self._stopping:
                self._lifecycle_condition.wait()
            if session_id is not None and self._session_id != str(session_id).strip():
                raise RuntimeError("microphone stream belongs to another conversation")
            if (
                owner_generation is not None
                and int(owner_generation) != self._owner_generation
            ):
                raise RuntimeError("microphone stream belongs to another owner generation")
            process = self._process
            stop_path = self._stop_path
            status_path = self._status_path
            reader = self._reader
            processor = self._processor
            frame_queue = self._frame_queue
            stopping_generation = self._owner_generation
            if process is None:
                self._session_id = ""
                self._owner_identity = ""
                return {"ok": True, "running": False, **self.status()}
            self._stopping = True
            self._process = None
            self._reader = None
            self._processor = None
            self._stop_path = None
            self._status_path = None
            self._session_id = ""
            self._owner_identity = ""
        cleanup_error: BaseException | None = None

        def record_cleanup_error(error: BaseException) -> None:
            nonlocal cleanup_error
            if cleanup_error is None:
                cleanup_error = error

        try:
            if stop_path is not None:
                try:
                    stop_path.touch(exist_ok=True)
                except BaseException as error:
                    record_cleanup_error(error)
            try:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            except BaseException as error:
                record_cleanup_error(error)
            if reader is not None:
                try:
                    reader.join(timeout=1)
                except BaseException as error:
                    record_cleanup_error(error)
            try:
                frame_queue.put_nowait(None)
            except queue.Full:
                try:
                    frame_queue.get_nowait()
                    frame_queue.put_nowait(None)
                except (queue.Empty, queue.Full):
                    pass
                except BaseException as error:
                    record_cleanup_error(error)
            except BaseException as error:
                record_cleanup_error(error)
            if processor is not None:
                try:
                    processor.join(timeout=2)
                except BaseException as error:
                    record_cleanup_error(error)
            for path in (stop_path, status_path):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except BaseException as error:
                        record_cleanup_error(error)
        finally:
            with self._lifecycle_condition:
                stopped_status = None
                try:
                    if (
                        self._stopping
                        and self._owner_generation == stopping_generation
                        and self._process is None
                    ):
                        try:
                            self.service.mark_stopped()
                        except BaseException as error:
                            record_cleanup_error(error)
                    try:
                        stopped_status = {
                            "ok": True,
                            "running": False,
                            **self.status(),
                        }
                    except BaseException as error:
                        record_cleanup_error(error)
                finally:
                    self._stopping = False
                    self._lifecycle_condition.notify_all()
        if cleanup_error is not None:
            raise cleanup_error
        assert stopped_status is not None
        return stopped_status

    def status(self) -> dict:
        for _ in range(3):
            with self._lock:
                manager_status, marker = self._manager_status_locked()
            service_status = self.service.status()
            with self._lock:
                if marker == self._manager_generation_marker_locked():
                    return self._merge_status(manager_status, service_status)
        # A lifecycle transition kept winning the optimistic retry. The service
        # never acquires the manager lock, so this final manager->service order
        # is safe and guarantees a single-generation snapshot.
        with self._lock:
            manager_status, _ = self._manager_status_locked()
            service_status = self.service.status()
            return self._merge_status(manager_status, service_status)

    def _manager_generation_marker_locked(self) -> tuple:
        return (
            self._owner_generation,
            self._session_id,
            self._process,
            self._frame_queue,
            self._stopping,
        )

    def _manager_status_locked(self) -> tuple[dict, tuple]:
        running = self._process is not None and self._process.poll() is None
        session_attached = bool(self._session_id)
        return (
            {
                "running": running,
                "session_attached": session_attached,
                "owner_generation": self._owner_generation,
                "owner_identity": self._owner_identity if session_attached else "",
                "capture_queue": self._frame_queue.qsize(),
                "capture_queue_peak": self._capture_queue_peak,
                "capture_queue_limit": self._frame_queue.maxsize,
                "capture_dropped_frames": self._dropped_capture_frames,
                "capture_frames_received": self._capture_frames_received,
            },
            self._manager_generation_marker_locked(),
        )

    @staticmethod
    def _merge_status(manager_status: dict, service_status: dict) -> dict:
        service_counters = dict(service_status.get("counters") or {})
        service_queues = dict(service_status.get("queues") or {})
        result = {
            **service_status,
            "running": manager_status["running"],
            "session_attached": manager_status["session_attached"],
            "owner_generation": manager_status["owner_generation"],
            "owner_identity": manager_status["owner_identity"],
            "backend": "isolated-pyaudio-portaudio-stream",
            "native": True,
            "webview_permission_required": False,
            "capture_queue": manager_status["capture_queue"],
            "capture_queue_peak": manager_status["capture_queue_peak"],
            "capture_queue_limit": manager_status["capture_queue_limit"],
            "capture_dropped_frames": manager_status["capture_dropped_frames"],
        }
        result["counters"] = {
            **service_counters,
            "capture_frames_received": manager_status["capture_frames_received"],
        }
        result["queues"] = {
            "capture": {
                "current": manager_status["capture_queue"],
                "peak": manager_status["capture_queue_peak"],
                "limit": manager_status["capture_queue_limit"],
                "dropped_frames": manager_status["capture_dropped_frames"],
            },
            **service_queues,
        }
        return result

    def events_after(
        self,
        after_sequence: int,
        *,
        session_id: str | None = None,
        owner_generation: int | None = None,
        timeout: float = 0.0,
    ) -> list[dict]:
        with self._lock:
            if session_id is not None and self._session_id != str(session_id).strip():
                raise RuntimeError("microphone stream belongs to another conversation")
            expected_generation = (
                self._owner_generation
                if owner_generation is None
                else int(owner_generation)
            )
            if expected_generation != self._owner_generation:
                raise RuntimeError("microphone stream belongs to another owner generation")
        events = self.service.events_after(after_sequence, timeout=timeout)
        with self._lock:
            if expected_generation != self._owner_generation:
                raise RuntimeError("microphone stream belongs to another owner generation")
        return events


continuous_capture_manager = NativeContinuousCaptureManager()
