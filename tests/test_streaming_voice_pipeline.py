import math
import random
import struct
import threading
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

    def test_direct_empty_final_transcript_emits_one_terminal_event(self):
        pipeline = self.make_pipeline(
            onset_frames=1,
            endpoint_silence_ms=60,
            partial_interval_ms=40,
        )
        pipeline.transcribe = lambda pcm, rate, final: ""
        events = []
        for index in range(8):
            events += pipeline.push_frame(
                pcm_frame(tone=6500, noise=300, phase=index)
            )
        for index in range(5):
            events += pipeline.push_frame(pcm_frame(noise=10, phase=20 + index))

        empty_events = [event for event in events if event["type"] == "transcript.empty"]

        self.assertEqual(len(empty_events), 1)
        self.assertEqual(
            set(empty_events[0]),
            {"type", "turn", "audio_ms", "input_rms", "input_peak"},
        )
        self.assertEqual(empty_events[0]["turn"], 1)
        self.assertGreater(empty_events[0]["audio_ms"], 0)
        self.assertGreater(empty_events[0]["input_rms"], 0.0)
        self.assertGreater(empty_events[0]["input_peak"], 0.0)
        self.assertNotIn("transcript.final", [event["type"] for event in events])

    def test_direct_pipeline_without_transcriber_still_emits_empty_terminal(self):
        pipeline = self.make_pipeline(
            onset_frames=1,
            endpoint_silence_ms=60,
            partial_interval_ms=40,
        )
        pipeline.transcribe = None
        events = []
        for index in range(8):
            events += pipeline.push_frame(
                pcm_frame(tone=6500, noise=300, phase=index)
            )
        for index in range(5):
            events += pipeline.push_frame(pcm_frame(noise=10, phase=20 + index))

        terminals = [
            event
            for event in events
            if event["type"] in {"transcript.final", "transcript.empty"}
        ]

        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["type"], "transcript.empty")
        self.assertEqual(terminals[0]["turn"], 1)

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

    def test_empty_final_transcript_emits_one_privacy_safe_terminal_event(self):
        service = ContinuousVoiceService(
            transcribe=lambda pcm, rate, final: "",
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
        for index in range(8):
            service.ingest_pcm(pcm_frame(tone=6500, noise=300, phase=index), RATE)
        for index in range(5):
            service.ingest_pcm(pcm_frame(noise=10, phase=20 + index), RATE)

        deadline = time.monotonic() + 1.0
        events = []
        while time.monotonic() < deadline:
            events = service.events_after(0)
            if (
                any(event["type"] == "transcript.empty" for event in events)
                and service.status()["transcription_queue"] == 0
            ):
                break
            time.sleep(0.01)
        time.sleep(0.05)
        events = service.events_after(0)

        empty_events = [event for event in events if event["type"] == "transcript.empty"]
        self.assertEqual(len(empty_events), 1)
        empty = empty_events[0]
        self.assertEqual(
            set(empty),
            {
                "type",
                "turn",
                "audio_ms",
                "input_rms",
                "input_peak",
                "sequence",
                "timestamp",
            },
        )
        self.assertEqual(empty["turn"], 1)
        self.assertGreater(empty["audio_ms"], 0)
        for field in ("input_rms", "input_peak"):
            self.assertIsInstance(empty[field], (int, float))
            self.assertNotIsInstance(empty[field], bool)
            self.assertGreaterEqual(empty[field], 0.0)
            self.assertLessEqual(empty[field], 1.0)
        self.assertGreater(empty["input_rms"], 0.0)
        self.assertGreater(empty["input_peak"], 0.0)
        self.assertLessEqual(empty["input_rms"], empty["input_peak"])

        def assert_no_audio_material(value):
            self.assertNotIsInstance(value, (bytes, bytearray, memoryview))
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized = str(key).lower()
                    self.assertNotIn(
                        normalized,
                        {
                            "pcm",
                            "audio",
                            "audio_base64",
                            "raw",
                            "samples",
                            "waveform",
                            "payload",
                        },
                    )
                    self.assertNotIn("base64", normalized)
                    assert_no_audio_material(nested)
            elif isinstance(value, (list, tuple, set)):
                for nested in value:
                    assert_no_audio_material(nested)

        assert_no_audio_material(empty)
        turn_terminals = [
            event
            for event in events
            if event["type"] in {"transcript.final", "transcript.empty"}
            and event.get("turn") == 1
        ]
        self.assertEqual(len(turn_terminals), 1)
        self.assertEqual(turn_terminals[0]["type"], "transcript.empty")

    def test_duplicate_final_tasks_publish_one_terminal_event(self):
        service = ContinuousVoiceService(transcribe=lambda pcm, rate, final: "")
        service.configure()
        final_pcm = pcm_frame(tone=6500, noise=300)
        metadata = {
            "turn": 1,
            "audio_ms": FRAME_MS,
            "input_rms": 0.1,
            "input_peak": 0.2,
        }

        with service._transcription_condition:
            service._enqueue_transcription(final_pcm, RATE, True, metadata)
            service._enqueue_transcription(final_pcm, RATE, True, metadata)

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            events = service.events_after(0)
            terminals = [
                event
                for event in events
                if event["type"] in {"transcript.final", "transcript.empty"}
                and event.get("turn") == 1
            ]
            if terminals and service.status()["transcription_queue"] == 0:
                break
            time.sleep(0.01)
        time.sleep(0.05)
        terminals = [
            event
            for event in service.events_after(0)
            if event["type"] in {"transcript.final", "transcript.empty"}
            and event.get("turn") == 1
        ]

        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0]["type"], "transcript.empty")

    def test_terminal_turn_tracking_compresses_large_sequential_history(self):
        service = ContinuousVoiceService(transcribe=lambda pcm, rate, final: "")
        service.configure()

        with service._transcription_condition:
            for turn in range(1, 4097):
                service._record_terminal_turn(turn)
            tracking = list(service._terminal_turn_ranges)

        self.assertEqual(len(tracking), 1)
        self.assertEqual(tracking, [(1, 4096)])

    def test_terminal_turn_tracking_merges_out_of_order_and_detects_duplicates(self):
        service = ContinuousVoiceService(transcribe=lambda pcm, rate, final: "")
        service.configure()

        with service._transcription_condition:
            self.assertTrue(service._record_terminal_turn(1))
            self.assertTrue(service._record_terminal_turn(3))
            self.assertTrue(service._record_terminal_turn(2))
            self.assertFalse(service._record_terminal_turn(2))
            tracking = list(service._terminal_turn_ranges)
            membership = [service._is_terminal_turn(turn) for turn in range(1, 5)]

        self.assertEqual(tracking, [(1, 3)])
        self.assertEqual(membership, [True, True, True, False])

    def test_full_transcription_queue_preserves_one_terminal_per_final_turn(self):
        service = ContinuousVoiceService(transcribe=lambda pcm, rate, final: "")
        service.configure()
        final_pcm = pcm_frame(tone=6500, noise=300)

        with service._transcription_condition:
            for turn in range(1, 5):
                service._enqueue_transcription(
                    final_pcm,
                    RATE,
                    True,
                    {
                        "turn": turn,
                        "audio_ms": FRAME_MS,
                        "input_rms": 0.1,
                        "input_peak": 0.2,
                    },
                )
                self.assertLessEqual(
                    len(service._transcription_queue),
                    service._transcription_limit,
                )

        deadline = time.monotonic() + 1.0
        terminals = []
        while time.monotonic() < deadline:
            events = service.events_after(0)
            terminals = [
                event
                for event in events
                if event["type"] in {"transcript.final", "transcript.empty"}
                and event.get("turn") in {1, 2, 3, 4}
            ]
            if len(terminals) >= 4:
                break
            time.sleep(0.01)

        self.assertEqual(sorted(event["turn"] for event in terminals), [1, 2, 3, 4])
        for turn in range(1, 5):
            self.assertEqual(sum(event["turn"] == turn for event in terminals), 1)
        self.assertLessEqual(
            service.status()["transcription_queue"],
            service.status()["transcription_queue_limit"],
        )
        self.assertEqual(service.status()["transcription_drops"], 1)

    def test_full_queue_evicts_partial_before_any_final_turn(self):
        service = ContinuousVoiceService(transcribe=lambda pcm, rate, final: "")
        service.configure()
        pcm = pcm_frame(tone=6500, noise=300)

        with service._transcription_condition:
            for turn, final in ((1, True), (2, False), (3, True), (4, True)):
                service._enqueue_transcription(
                    pcm,
                    RATE,
                    final,
                    {
                        "turn": turn,
                        "audio_ms": FRAME_MS,
                        "input_rms": 0.1,
                        "input_peak": 0.2,
                    },
                )
            queued = [
                (task["turn"], task["final"])
                for task in service._transcription_queue
            ]
            events_before_worker = service.events_after(0)

        self.assertEqual(queued, [(1, True), (3, True), (4, True)])
        self.assertEqual(events_before_worker, [])
        self.assertEqual(service.status()["transcription_drops"], 1)

    def test_later_overflow_terminal_does_not_suppress_earlier_inflight_final(self):
        first_final_started = threading.Event()
        allow_first_final = threading.Event()

        def transcribe(pcm, rate, final):
            if final and not first_final_started.is_set():
                first_final_started.set()
                allow_first_final.wait(1.0)
            return ""

        service = ContinuousVoiceService(transcribe=transcribe)
        service.configure()
        pcm = pcm_frame(tone=6500, noise=300)

        service._enqueue_transcription(
            pcm,
            RATE,
            True,
            {
                "turn": 1,
                "audio_ms": FRAME_MS,
                "input_rms": 0.1,
                "input_peak": 0.2,
            },
        )
        try:
            self.assertTrue(first_final_started.wait(1.0))
            with service._transcription_condition:
                for turn in range(2, 6):
                    service._enqueue_transcription(
                        pcm,
                        RATE,
                        True,
                        {
                            "turn": turn,
                            "audio_ms": FRAME_MS,
                            "input_rms": 0.1,
                            "input_peak": 0.2,
                        },
                    )
                    self.assertLessEqual(
                        len(service._transcription_queue),
                        service._transcription_limit,
                    )
        finally:
            allow_first_final.set()

        deadline = time.monotonic() + 1.0
        terminals = []
        while time.monotonic() < deadline:
            terminals = [
                event
                for event in service.events_after(0)
                if event["type"] in {"transcript.final", "transcript.empty"}
                and event.get("turn") in {1, 2, 3, 4, 5}
            ]
            if len(terminals) >= 5:
                break
            time.sleep(0.01)

        self.assertEqual(
            sorted(event["turn"] for event in terminals),
            [1, 2, 3, 4, 5],
        )
        for turn in range(1, 6):
            self.assertEqual(sum(event["turn"] == turn for event in terminals), 1)

    def test_reconfigure_cannot_cross_inflight_terminal_publication(self):
        service = ContinuousVoiceService(transcribe=lambda pcm, rate, final: "")
        service.configure()
        publish_entered = threading.Event()
        allow_publish = threading.Event()
        configure_started = threading.Event()
        pipeline_construction_started = threading.Event()
        configure_done = threading.Event()
        configure_errors = []
        original_publish = service._publish

        def blocking_publish(event):
            if event["type"] in {"transcript.final", "transcript.empty"}:
                publish_entered.set()
                allow_publish.wait(1.0)
            return original_publish(event)

        def build_pipeline(*args, **kwargs):
            pipeline_construction_started.set()
            return StreamingVoicePipeline(*args, **kwargs)

        def reconfigure():
            configure_started.set()
            try:
                service.configure()
            except Exception as error:
                configure_errors.append(error)
            finally:
                configure_done.set()

        service._publish = blocking_publish
        service._enqueue_transcription(
            pcm_frame(tone=6500, noise=300),
            RATE,
            True,
            {
                "turn": 1,
                "audio_ms": FRAME_MS,
                "input_rms": 0.1,
                "input_peak": 0.2,
            },
        )
        self.assertTrue(publish_entered.wait(1.0))

        with patch(
            "voice.continuous_capture.StreamingVoicePipeline",
            side_effect=build_pipeline,
        ):
            configure_thread = threading.Thread(target=reconfigure, daemon=True)
            configure_thread.start()
            self.assertTrue(configure_started.wait(1.0))
            crossed_before_publish = pipeline_construction_started.wait(0.25)
            allow_publish.set()
            configure_thread.join(1.0)

        service._publish = original_publish
        self.assertFalse(crossed_before_publish)
        self.assertFalse(configure_thread.is_alive())
        self.assertTrue(configure_done.is_set())
        self.assertEqual(configure_errors, [])
        terminals = [
            event
            for event in service.events_after(0)
            if event["type"] in {"transcript.final", "transcript.empty"}
            and event.get("turn") == 1
        ]
        self.assertEqual(len(terminals), 1)

    def test_reconfigure_waits_for_inflight_pipeline_before_advancing_generation(self):
        service = ContinuousVoiceService(transcribe=lambda pcm, rate, final: "")
        service.configure()
        generation_before = service._transcription_generation
        configure_started = threading.Event()
        configure_done = threading.Event()
        configure_errors = []

        def reconfigure():
            configure_started.set()
            try:
                service.configure()
            except Exception as error:
                configure_errors.append(error)
            finally:
                configure_done.set()

        with service._pipeline_lock:
            configure_thread = threading.Thread(target=reconfigure, daemon=True)
            configure_thread.start()
            self.assertTrue(configure_started.wait(1.0))
            deadline = time.monotonic() + 0.25
            while (
                service._transcription_generation == generation_before
                and time.monotonic() < deadline
            ):
                time.sleep(0.005)
            generation_advanced_while_pipeline_busy = (
                service._transcription_generation != generation_before
            )

        configure_thread.join(1.0)
        self.assertFalse(generation_advanced_while_pipeline_busy)
        self.assertFalse(configure_thread.is_alive())
        self.assertTrue(configure_done.is_set())
        self.assertEqual(configure_errors, [])
        self.assertEqual(service._transcription_generation, generation_before + 1)

    def test_stale_generation_transcriber_exception_is_not_published(self):
        first_transcription_started = threading.Event()
        allow_stale_exception = threading.Event()
        calls = 0

        def transcribe(pcm, rate, final):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_transcription_started.set()
                allow_stale_exception.wait(1.0)
                raise RuntimeError("stale transcription exploded")
            return ""

        service = ContinuousVoiceService(transcribe=transcribe)
        service.configure()
        metadata = {
            "turn": 1,
            "audio_ms": FRAME_MS,
            "input_rms": 0.1,
            "input_peak": 0.2,
        }
        service._enqueue_transcription(
            pcm_frame(tone=6500, noise=300),
            RATE,
            True,
            metadata,
        )
        try:
            self.assertTrue(first_transcription_started.wait(1.0))
            service.configure()
        finally:
            allow_stale_exception.set()

        fresh_metadata = dict(metadata)
        fresh_metadata["turn"] = 2
        service._enqueue_transcription(
            pcm_frame(tone=6500, noise=300, phase=1),
            RATE,
            True,
            fresh_metadata,
        )
        deadline = time.monotonic() + 1.0
        events = []
        while time.monotonic() < deadline:
            events = service.events_after(0)
            if any(event["type"] == "transcript.empty" for event in events):
                break
            time.sleep(0.01)

        self.assertIn("transcript.empty", [event["type"] for event in events])
        self.assertEqual(
            [event for event in events if event["type"] == "audio.error"],
            [],
        )
        with service._transcription_condition:
            self.assertFalse(service._is_terminal_turn(1))
            self.assertTrue(service._is_terminal_turn(2))
            self.assertEqual(service._terminal_turn_ranges, [(2, 2)])

    def test_current_generation_final_exception_is_one_recorded_terminal_error(self):
        calls = 0

        def transcribe(pcm, rate, final):
            nonlocal calls
            calls += 1
            raise RuntimeError("current transcription exploded")

        service = ContinuousVoiceService(transcribe=transcribe)
        service.configure()
        final_pcm = pcm_frame(tone=6500, noise=300)
        metadata = {
            "turn": 1,
            "audio_ms": FRAME_MS,
            "input_rms": 0.1,
            "input_peak": 0.2,
        }
        service._enqueue_transcription(final_pcm, RATE, True, metadata)

        deadline = time.monotonic() + 1.0
        events = []
        while time.monotonic() < deadline:
            events = service.events_after(0)
            if any(event["type"] == "audio.error" for event in events):
                break
            time.sleep(0.01)
        service._enqueue_transcription(final_pcm, RATE, True, metadata)
        time.sleep(0.05)
        events = service.events_after(0)
        errors = [event for event in events if event["type"] == "audio.error"]
        with service._transcription_condition:
            terminal_recorded = service._is_terminal_turn(1)
            ranges = list(service._terminal_turn_ranges)

        self.assertEqual(len(errors), 1)
        self.assertIn("current transcription exploded", errors[0]["message"])
        self.assertEqual(calls, 1)
        self.assertTrue(terminal_recorded)
        self.assertEqual(ranges, [(1, 1)])

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
