import io
import math
import struct
import time
import unittest
import wave

from voice.native_playback import NativePlaybackManager


def wav_tone(duration_ms: int = 160, rate: int = 16_000) -> bytes:
    samples = []
    for index in range(rate * duration_ms // 1000):
        samples.append(int(4000 * math.sin(2 * math.pi * 220 * index / rate)))
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


class FakePlayer:
    def __init__(self):
        self.played = []
        self.stops = 0

    def play(self, path):
        self.played.append(path)

    def stop(self):
        self.stops += 1


class FakeVoiceService:
    def __init__(self):
        self.frames = []

    def ingest_playback_pcm(self, pcm: bytes, source_rate: int):
        self.frames.append((pcm, source_rate))


class NativePlaybackTests(unittest.TestCase):
    def test_native_playback_feeds_twenty_ms_echo_reference_without_raw_persistence(self):
        player = FakePlayer()
        service = FakeVoiceService()
        manager = NativePlaybackManager(service=service, player=player)

        result = manager.play_wav(wav_tone())
        deadline = time.monotonic() + 1.0
        while manager.status()["active"] and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertTrue(result["ok"])
        self.assertEqual(len(player.played), 1)
        self.assertGreaterEqual(len(service.frames), 6)
        self.assertTrue(all(rate == 16_000 for _, rate in service.frames))
        self.assertFalse(manager.status()["raw_microphone_audio_persisted"])

    def test_barge_in_stop_is_idempotent_and_marks_playback_inactive(self):
        player = FakePlayer()
        manager = NativePlaybackManager(service=FakeVoiceService(), player=player)
        manager.play_wav(wav_tone(duration_ms=500))

        first = manager.stop()
        second = manager.stop()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertFalse(manager.status()["active"])
        self.assertGreaterEqual(player.stops, 2)

    def test_stop_invalidates_speech_synthesized_for_an_old_reservation(self):
        player = FakePlayer()
        manager = NativePlaybackManager(service=FakeVoiceService(), player=player)
        reservation = manager.reserve()
        manager.stop()

        result = manager.play_reserved_wav(wav_tone(), reservation=reservation)

        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(player.played, [])


if __name__ == "__main__":
    unittest.main()
