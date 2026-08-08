"""Bounded continuous-voice processing for Javis native audio frames."""

from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass
import importlib.util
import math
import sys
from typing import Callable


Transcriber = Callable[[bytes, int, bool], str]
TranscriptionSink = Callable[[bytes, int, bool, dict], None]


@dataclass(frozen=True)
class VoicePipelineConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    pre_roll_ms: int = 200
    onset_frames: int = 3
    endpoint_silence_ms: int = 900
    partial_interval_ms: int = 800
    max_utterance_ms: int = 15_000
    noise_profile: str = "standard"

    def __post_init__(self) -> None:
        if self.sample_rate < 8_000 or self.sample_rate > 48_000:
            raise ValueError("sample_rate must be between 8000 and 48000")
        if self.frame_ms not in {10, 20, 30}:
            raise ValueError("frame_ms must be 10, 20 or 30")
        if self.onset_frames < 1:
            raise ValueError("onset_frames must be positive")
        if self.noise_profile not in {"off", "standard", "strong"}:
            raise ValueError("noise_profile must be off, standard or strong")


def _decode_pcm(frame: bytes) -> list[int]:
    if len(frame) % 2:
        raise ValueError("PCM16 frames must contain an even byte count")
    samples = array("h")
    samples.frombytes(frame)
    if sys.byteorder != "little":
        samples.byteswap()
    return list(samples)


def _encode_pcm(samples: list[float] | list[int]) -> bytes:
    values = array(
        "h",
        (max(-32768, min(32767, int(round(value)))) for value in samples),
    )
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def _rms(samples: list[int] | list[float]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(float(value) * float(value) for value in samples) / len(samples))


def _normalized_pcm_levels(pcm: bytes) -> tuple[float, float]:
    samples = _decode_pcm(pcm)
    if not samples:
        return 0.0, 0.0
    full_scale = 32768.0
    input_rms = min(1.0, _rms(samples) / full_scale)
    input_peak = min(1.0, max(abs(value) for value in samples) / full_scale)
    return round(input_rms, 6), round(input_peak, 6)


def _zero_crossing_rate(samples: list[int]) -> float:
    if len(samples) < 2:
        return 0.0
    crossings = sum(
        1
        for left, right in zip(samples, samples[1:])
        if (left < 0 <= right) or (left >= 0 > right)
    )
    return crossings / (len(samples) - 1)


class StreamingVoicePipeline:
    """Processes PCM frames while preserving a bounded continuous session."""

    def __init__(
        self,
        *,
        config: VoicePipelineConfig | None = None,
        transcribe: Transcriber | None = None,
        vad_score: Callable[[bytes, int], float] | None = None,
        transcription_sink: TranscriptionSink | None = None,
    ) -> None:
        self.config = config or VoicePipelineConfig()
        self.transcribe = transcribe
        self.vad_score = vad_score
        self.transcription_sink = transcription_sink
        self.frame_samples = self.config.sample_rate * self.config.frame_ms // 1000
        self.pre_roll_frames = max(1, self.config.pre_roll_ms // self.config.frame_ms)
        self.endpoint_frames = max(
            1,
            self.config.endpoint_silence_ms // self.config.frame_ms,
        )
        self.partial_frames = max(
            1,
            self.config.partial_interval_ms // self.config.frame_ms,
        )
        self.max_utterance_frames = max(
            self.pre_roll_frames + 1,
            self.config.max_utterance_ms // self.config.frame_ms,
        )
        self.listening = True
        self._pre_roll: deque[bytes] = deque(maxlen=self.pre_roll_frames)
        self._playback: deque[bytes] = deque(maxlen=self.pre_roll_frames * 2)
        self._utterance: deque[bytes] = deque(maxlen=self.max_utterance_frames)
        self._noise_power = 200.0**2
        self._noise_rms = 200.0
        self._onset_count = 0
        self._silence_count = 0
        self._speech_frames = 0
        self._frames_since_partial = 0
        self._last_partial = ""
        self._turn = 0
        self._dropped_frames = 0
        self._overrun_reported = False

    @property
    def buffered_frames(self) -> int:
        return len(self._utterance)

    def push_playback_frame(self, frame: bytes) -> None:
        if frame:
            self._playback.append(bytes(frame))

    def _cancel_echo(self, frame: bytes) -> bytes:
        samples = [float(value) for value in _decode_pcm(frame)]
        if not samples:
            return b""
        if self._playback:
            reference = _decode_pcm(self._playback.popleft())
            if len(reference) == len(samples):
                denominator = sum(float(value) * float(value) for value in reference)
                if denominator > 1.0:
                    projection = sum(
                        sample * float(echo)
                        for sample, echo in zip(samples, reference)
                    ) / denominator
                    projection = max(0.0, min(1.2, projection))
                    samples = [
                        sample - projection * float(echo)
                        for sample, echo in zip(samples, reference)
                    ]
        return _encode_pcm(samples)

    def process_frame(
        self,
        frame: bytes,
        *,
        speech_hint: bool,
        cancel_echo: bool = True,
    ) -> bytes:
        if cancel_echo:
            frame = self._cancel_echo(frame)
        samples = [float(value) for value in _decode_pcm(frame)]
        if not samples:
            return b""

        power = max(1.0, _rms(samples) ** 2)
        if not speech_hint:
            self._noise_power = 0.94 * self._noise_power + 0.06 * power
            self._noise_rms = math.sqrt(self._noise_power)

        if self.config.noise_profile == "off":
            floor = 1.0
        elif self.config.noise_profile == "strong":
            floor = 0.18
        else:
            floor = 0.32
        gain = 1.0 if floor == 1.0 else max(floor, 1.0 - self._noise_power / power)
        if speech_hint:
            gain = max(gain, 0.62)
        samples = [sample * gain for sample in samples]

        if speech_hint:
            current_rms = _rms(samples)
            if current_rms > 1.0:
                agc = max(0.65, min(2.8, 3_500.0 / current_rms))
                samples = [sample * agc for sample in samples]
        return _encode_pcm(samples)

    def _is_speech(self, frame: bytes) -> bool:
        if self.vad_score is not None:
            return float(self.vad_score(frame, self.config.sample_rate)) >= 0.55
        samples = _decode_pcm(frame)
        level = _rms(samples)
        zcr = _zero_crossing_rate(samples)
        threshold = max(360.0, self._noise_rms * 2.25)
        return level >= threshold and 0.008 <= zcr <= 0.32

    def _append_utterance(self, frame: bytes, events: list[dict]) -> None:
        if len(self._utterance) >= self.max_utterance_frames:
            self._utterance.popleft()
            self._dropped_frames += 1
            if not self._overrun_reported:
                events.append(
                    {
                        "type": "audio.overrun",
                        "dropped_frames": self._dropped_frames,
                    }
                )
                self._overrun_reported = True
        self._utterance.append(frame)

    def _transcribe(self, *, final: bool) -> str:
        if self.transcribe is None or not self._utterance:
            return ""
        return str(
            self.transcribe(
                b"".join(self._utterance),
                self.config.sample_rate,
                final,
            )
            or ""
        ).strip()

    def _submit_transcription(self, *, final: bool, metadata: dict) -> str:
        if not self._utterance:
            return ""
        if self.transcription_sink is not None:
            self.transcription_sink(
                b"".join(self._utterance),
                self.config.sample_rate,
                final,
                metadata,
            )
            return ""
        return self._transcribe(final=final)

    def push_frame(self, frame: bytes) -> list[dict]:
        if not self.listening:
            return []
        if len(frame) != self.frame_samples * 2:
            raise ValueError(
                f"expected {self.frame_samples * 2} PCM bytes, received {len(frame)}"
            )

        raw_samples = _decode_pcm(frame)
        echo_cleaned = self._cancel_echo(frame)
        speech = self._is_speech(echo_cleaned)
        processed = self.process_frame(
            echo_cleaned,
            speech_hint=speech,
            cancel_echo=False,
        )
        level = min(1.0, _rms(raw_samples) / 8_000.0)
        events: list[dict] = [{"type": "audio.level", "level": round(level, 4)}]
        self._pre_roll.append(processed)

        if not self._utterance:
            if speech:
                self._onset_count += 1
            else:
                self._onset_count = 0
            if self._onset_count < self.config.onset_frames:
                return events
            for buffered in self._pre_roll:
                self._append_utterance(buffered, events)
            self._speech_frames = self._onset_count
            self._frames_since_partial = 0
            self._silence_count = 0
            self._last_partial = ""
            events.append({"type": "speech.start"})
            return events

        self._append_utterance(processed, events)
        self._frames_since_partial += 1
        if speech:
            self._speech_frames += 1
            self._silence_count = 0
        else:
            self._silence_count += 1

        if (
            self._frames_since_partial >= self.partial_frames
            and self._silence_count < self.endpoint_frames
        ):
            self._frames_since_partial = 0
            partial = self._submit_transcription(
                final=False,
                metadata={
                    "turn": self._turn + 1,
                    "audio_ms": len(self._utterance) * self.config.frame_ms,
                },
            )
            if partial and partial != self._last_partial:
                self._last_partial = partial
                events.append({"type": "transcript.partial", "text": partial})

        if self._silence_count < self.endpoint_frames:
            return events

        self._turn += 1
        audio_ms = len(self._utterance) * self.config.frame_ms
        input_rms, input_peak = _normalized_pcm_levels(b"".join(self._utterance))
        final_text = self._submit_transcription(
            final=True,
            metadata={
                "turn": self._turn,
                "audio_ms": audio_ms,
                "input_rms": input_rms,
                "input_peak": input_peak,
            },
        )
        if final_text:
            events.append(
                {
                    "type": "transcript.final",
                    "text": final_text,
                    "turn": self._turn,
                    "audio_ms": audio_ms,
                }
            )
        elif self.transcription_sink is None:
            events.append(
                {
                    "type": "transcript.empty",
                    "turn": self._turn,
                    "audio_ms": audio_ms,
                    "input_rms": input_rms,
                    "input_peak": input_peak,
                }
            )
        self._utterance.clear()
        self._onset_count = 0
        self._silence_count = 0
        self._speech_frames = 0
        self._frames_since_partial = 0
        self._last_partial = ""
        self._overrun_reported = False
        return events

    def metrics(self) -> dict:
        return {
            "turns": self._turn,
            "dropped_frames": self._dropped_frames,
            "buffered_frames": len(self._utterance),
            "noise_rms": round(self._noise_rms, 3),
        }

    def diagnostics(self) -> dict:
        webrtc_available = importlib.util.find_spec("webrtc_audio_processing") is not None
        rnnoise_available = importlib.util.find_spec("rnnoise") is not None
        silero_available = importlib.util.find_spec("silero_vad") is not None
        return {
            "audio_processing_mode": "reduced",
            "aec_backend": "adaptive-reference",
            "noise_backend": "adaptive-wiener",
            "noise_profile": self.config.noise_profile,
            "vad_backend": "external-neural" if self.vad_score is not None else "adaptive-energy",
            "optional_backends_available": {
                "webrtc_apm": webrtc_available,
                "rnnoise": rnnoise_available,
                "silero_vad": silero_available,
            },
            "raw_audio_persisted": False,
            "frame_ms": self.config.frame_ms,
            "sample_rate": self.config.sample_rate,
            **self.metrics(),
        }
