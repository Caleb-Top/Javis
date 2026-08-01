import math
import random
import struct
import time
import unittest
from unittest.mock import patch

from voice import stt
from voice.continuous_capture import ContinuousVoiceService, resample_pcm16_mono
from voice.streaming_pipeline import StreamingVoicePipeline, VoicePipelineConfig


RATE = 16_000
FRAME_MS = 20
SAMPLES = RATE * FRAME_MS // 1000


def pcm_frame(*, tone: float = 0.0, noise: float = 0.0, phase: int = 0) -> bytes:
    rng = random.Random(1000 + phase)
    values = []
    for index in range(SAMPLES):
        sample = 0.0
        if tone:
            sample += tone * math.sin(2 * math.pi * 220 * (phase * SAMPLES + index) / RATE)
        if noise:
            sample += rng.uniform(-noise, noise)
        values.append(max(-32768, min(32767, int(sample))))
    return struct.pack(f"<{len(values)}h", *values)


def impulse_frame(amplitude: int = 18_000) -> bytes:
    values = [0] * SAMPLES
    values[SAMPLES // 2] = amplitude
    return struct.pack(f"<{len(values)}h", *values)


def rms(frame: bytes) -> float:
    values = struct.unpack(f"<{len(frame) // 2}h", frame)
    return math.sqrt(sum(value * value for value in values) / max(1, len(values)))


def mix_frames(left: bytes, right: bytes) -> bytes:
    left_values = struct.unpack(f"<{len(left) // 2}h", left)
    right_values = struct.unpack(f"<{len(right) // 2}h", right)
    mixed = [
        max(-32768, min(32767, a + b))
        for a, b in zip(left_values, right_values)
    ]
    return struct.pack(f"<{len(mixed)}h", *mixed)


class FakeTranscriber:
    def __init__(self):
        self.calls = 0

    def __call__(self, pcm: bytes, sample_rate: int, final: bool) -> str:
        self.calls += 1
        seconds = len(pcm) / 2 / sample_rate
        return f"turn {self.calls} {seconds:.2f}"


class StreamingVoicePipelineTests(unittest.TestCase):
    def test_default_endpoint_allows_natural_short_pauses(self):
        self.assertEqual(VoicePipelineConfig().endpoint_silence_ms, 900)
        self.assertEqual(VoicePipelineConfig().partial_interval_ms, 800)

    def make_pipeline(self, **overrides):
        settings = dict(
            sample_rate=RATE,
            frame_ms=FRAME_MS,
            pre_roll_ms=120,
            onset_frames=2,
            endpoint_silence_ms=120,
            partial_interval_ms=80,
            max_utterance_ms=1200,
        )
        settings.update(overrides)
        config = VoicePipelineConfig(**settings)
        return StreamingVoicePipeline(config=config, transcribe=FakeTranscriber())

    def test_pre_roll_partial_final_and_second_turn_are_ordered(self):
        pipeline = self.make_pipeline()
        events = []
        for index in range(5):
            events += pipeline.push_frame(pcm_frame(noise=80, phase=index))
        for index in range(10):
            events += pipeline.push_frame(pcm_frame(tone=6500, noise=400, phase=10 + index))
        for index in range(8):
            events += pipeline.push_frame(pcm_frame(noise=40, phase=30 + index))

        first_types = [event["type"] for event in events]
        self.assertIn("speech.start", first_types)
        self.assertIn("transcript.partial", first_types)
        self.assertIn("transcript.final", first_types)
        self.assertLess(first_types.index("speech.start"), first_types.index("transcript.partial"))
        self.assertLess(first_types.index("transcript.partial"), first_types.index("transcript.final"))
        first_final = next(event for event in events if event["type"] == "transcript.final")
        self.assertGreaterEqual(first_final["audio_ms"], 200)

        second = []
        for index in range(8):
            second += pipeline.push_frame(pcm_frame(tone=7000, noise=300, phase=50 + index))
        for index in range(8):
            second += pipeline.push_frame(pcm_frame(noise=30, phase=70 + index))
        second_final = next(event for event in second if event["type"] == "transcript.final")

        self.assertEqual(first_final["turn"], 1)
        self.assertEqual(second_final["turn"], 2)
        self.assertTrue(pipeline.listening)

    def test_stationary_noise_and_keyboard_impulses_do_not_finalize_speech(self):
        pipeline = self.make_pipeline()
        events = []
        for index in range(80):
            frame = impulse_frame() if index in {13, 39, 61} else pcm_frame(noise=550, phase=index)
            events += pipeline.push_frame(frame)

        self.assertNotIn("transcript.final", [event["type"] for event in events])

    def test_standard_noise_reduction_lowers_noise_without_erasing_speech(self):
        pipeline = self.make_pipeline(noise_profile="standard")
        for index in range(30):
            pipeline.process_frame(pcm_frame(noise=700, phase=index), speech_hint=False)

        noisy = pcm_frame(noise=700, phase=90)
        speech = pcm_frame(tone=6000, noise=700, phase=91)
        filtered_noise = pipeline.process_frame(noisy, speech_hint=False)
        filtered_speech = pipeline.process_frame(speech, speech_hint=True)

        self.assertLess(rms(filtered_noise), rms(noisy) * 0.75)
        self.assertGreater(rms(filtered_speech), rms(speech) * 0.55)

    def test_echo_reference_is_suppressed_before_vad(self):
        pipeline = self.make_pipeline()
        echo = pcm_frame(tone=5000, phase=1)
        pipeline.push_playback_frame(echo)

        processed = pipeline.process_frame(echo, speech_hint=False)

        self.assertLess(rms(processed), rms(echo) * 0.45)

    def test_playback_echo_does_not_trigger_speech_but_user_barge_in_does(self):
        pipeline = self.make_pipeline(onset_frames=2)
        echo_events = []
        for index in range(4):
            echo = pcm_frame(tone=5000, phase=index)
            pipeline.push_playback_frame(echo)
            echo_events += pipeline.push_frame(echo)

        self.assertNotIn("speech.start", [event["type"] for event in echo_events])

        barge_events = []
        for index in range(3):
            echo = pcm_frame(tone=4200, phase=20 + index)
            user = pcm_frame(tone=6500, phase=80 + index)
            pipeline.push_playback_frame(echo)
            barge_events += pipeline.push_frame(mix_frames(echo, user))

        self.assertIn("speech.start", [event["type"] for event in barge_events])

    def test_utterance_buffer_is_bounded_and_reports_overrun(self):
        pipeline = self.make_pipeline(max_utterance_ms=200)
        events = []
        for index in range(40):
            events += pipeline.push_frame(pcm_frame(tone=7000, phase=index))

        self.assertLessEqual(pipeline.buffered_frames, pipeline.max_utterance_frames)
        self.assertIn("audio.overrun", [event["type"] for event in events])
        self.assertGreater(pipeline.metrics()["dropped_frames"], 0)

    def test_diagnostics_are_truthful_and_raw_audio_is_not_persisted(self):
        pipeline = self.make_pipeline(noise_profile="strong")
        diagnostics = pipeline.diagnostics()

        self.assertEqual(diagnostics["noise_profile"], "strong")
        self.assertFalse(diagnostics["raw_audio_persisted"])
        self.assertEqual(diagnostics["vad_backend"], "adaptive-energy")
        self.assertEqual(diagnostics["audio_processing_mode"], "reduced")
        self.assertEqual(diagnostics["aec_backend"], "adaptive-reference")
        self.assertEqual(diagnostics["noise_backend"], "adaptive-wiener")

    def test_pcm_transcription_uses_wav_and_a_fast_partial_decode(self):
        calls = []

        class Segment:
            text = " hello "

        class FakeModel:
            def transcribe(self, path, **kwargs):
                with open(path, "rb") as stream:
                    header = stream.read(4)
                calls.append((path, header, kwargs))
                return [Segment()], object()

        original = stt._model
        stt._model = FakeModel()
        try:
            text = stt.transcribe_pcm(
                pcm_frame(tone=5000),
                sample_rate=RATE,
                language="en",
                final=False,
            )
        finally:
            stt._model = original

        self.assertEqual(text, "hello")
        self.assertTrue(calls[0][0].endswith(".wav"))
        self.assertEqual(calls[0][1], b"RIFF")
        self.assertEqual(calls[0][2]["beam_size"], 1)
        self.assertFalse(calls[0][2]["vad_filter"])


class ContinuousVoiceServiceTests(unittest.TestCase):
    def test_default_stt_adapter_uses_the_real_pcm_keyword_contract(self):
        with patch("voice.continuous_capture.transcribe_pcm", return_value="ok") as helper:
            service = ContinuousVoiceService()

            result = service._transcribe(b"\0\0" * 320, RATE, False)

        self.assertEqual(result, "ok")
        helper.assert_called_once_with(
            b"\0\0" * 320,
            sample_rate=RATE,
            final=False,
            beam_size=1,
        )

    def test_default_stt_adapter_uses_fast_final_beam(self):
        with patch("voice.continuous_capture.transcribe_pcm", return_value="ok") as helper:
            service = ContinuousVoiceService()

            service._transcribe(b"\0\0" * 320, RATE, True)

        helper.assert_called_once_with(
            b"\0\0" * 320,
            sample_rate=RATE,
            final=True,
            beam_size=2,
        )

    def test_resamples_native_48khz_frames_to_16khz_without_length_drift(self):
        source = struct.pack("<960h", *([1000, -1000, 500] * 320))

        result = resample_pcm16_mono(source, 48_000, 16_000)

        self.assertEqual(len(result), 320 * 2)

    def test_event_ring_is_ordered_bounded_and_contains_no_raw_audio(self):
        service = ContinuousVoiceService(
            transcribe=lambda pcm, rate, final: "hello" if final else "hel",
            event_limit=12,
            pipeline_config=VoicePipelineConfig(
                sample_rate=RATE,
                frame_ms=FRAME_MS,
                pre_roll_ms=120,
                onset_frames=2,
                endpoint_silence_ms=120,
                partial_interval_ms=80,
            ),
        )
        service.configure(noise_profile="standard")
        service.mark_ready({"rate": RATE, "trackLabel": "test microphone"})
        for index in range(12):
            service.ingest_pcm(pcm_frame(tone=6500, phase=index), RATE)
        for index in range(8):
            service.ingest_pcm(pcm_frame(noise=20, phase=30 + index), RATE)

        deadline = time.monotonic() + 1.0
        events = []
        while time.monotonic() < deadline:
            events = service.events_after(0)
            if any(event["type"] == "transcript.final" for event in events):
                break
            time.sleep(0.01)
        sequences = [event["sequence"] for event in events]

        self.assertEqual(sequences, sorted(sequences))
        self.assertLessEqual(len(events), 12)
        self.assertIn("transcript.final", [event["type"] for event in events])
        self.assertNotIn("pcm", repr(events).lower())
        self.assertNotIn("audio_base64", repr(events).lower())
        self.assertFalse(service.status()["raw_audio_persisted"])

    def test_playback_pcm_is_forwarded_as_echo_reference(self):
        service = ContinuousVoiceService(
            transcribe=lambda pcm, rate, final: "",
            pipeline_config=VoicePipelineConfig(sample_rate=RATE, frame_ms=FRAME_MS),
        )
        service.configure()
        playback = pcm_frame(tone=5000)

        service.ingest_playback_pcm(playback, RATE)
        processed = service._pipeline.process_frame(playback, speech_hint=False)

        self.assertLess(rms(processed), rms(playback) * 0.45)

    def test_slow_partial_transcription_does_not_block_audio_ingest(self):
        def slow_transcribe(pcm, rate, final):
            time.sleep(0.18)
            return "final text" if final else "partial text"

        service = ContinuousVoiceService(
            transcribe=slow_transcribe,
            pipeline_config=VoicePipelineConfig(
                sample_rate=RATE,
                frame_ms=FRAME_MS,
                pre_roll_ms=40,
                onset_frames=1,
                endpoint_silence_ms=60,
                partial_interval_ms=40,
            ),
        )
        service.configure()

        started = time.monotonic()
        for index in range(8):
            service.ingest_pcm(pcm_frame(tone=6500, phase=index), RATE)
        elapsed = time.monotonic() - started
        for index in range(5):
            service.ingest_pcm(pcm_frame(noise=10, phase=20 + index), RATE)

        self.assertLess(elapsed, 0.12)
        deadline = time.monotonic() + 1.5
        events = []
        while time.monotonic() < deadline:
            events = service.events_after(0)
            if any(event["type"] == "transcript.final" for event in events):
                break
            time.sleep(0.02)
        self.assertIn("transcript.final", [event["type"] for event in events])
        self.assertLessEqual(service.status()["transcription_queue"], 3)

    def test_worker_contract_is_48khz_twenty_millisecond_pcm(self):
        source = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "voice"
            / "native_capture_worker.py"
        ).read_text(encoding="utf-8")

        self.assertIn('sys.argv[1] == "stream"', source)
        self.assertIn("STREAM_RATE = 48_000", source)
        self.assertIn("frames_per_buffer=candidate // 50", source)
        self.assertIn("stream.read(rate // 50", source)
        self.assertIn('"type": "frame"', source)


if __name__ == "__main__":
    unittest.main()
