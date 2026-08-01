import unittest
import queue

from voice.streaming_ws import serve_continuous_voice_stream
from voice.continuous_capture import NativeContinuousCaptureManager


class FakeSocket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if not self.messages:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect()
        return self.messages.pop(0)

    async def send_json(self, payload):
        self.sent.append(payload)


class FakeManager:
    def __init__(self):
        self.started = []
        self.attached = []
        self.stopped = 0
        self.events = [
            {"type": "audio.stream.ready", "sequence": 1},
            {"type": "speech.start", "sequence": 2},
            {"type": "transcript.final", "text": "hello", "turn": 1, "sequence": 3},
        ]

    def start(self, *, session_id, noise_profile, device_index=None):
        self.started.append((session_id, noise_profile, device_index))
        return {"ok": True, "running": True}

    def attach(self, *, session_id):
        self.attached.append(session_id)
        return {"ok": True, "running": True}

    def stop(self, *, session_id=None):
        self.stopped += 1
        self.events.append({"type": "audio.stream.stopped", "sequence": 4})
        return {"ok": True, "running": False}

    def events_after(self, after_sequence, *, session_id=None, timeout=0.0):
        return [event for event in self.events if event["sequence"] > after_sequence]


class ContinuousVoiceGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_streams_ordered_public_events_and_stop_is_explicit(self):
        socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                        "after_sequence": 0,
                    },
                },
                {
                    "type": "audio.stream.stop",
                    "payload": {"session_id": "session-1"},
                },
            ]
        )
        manager = FakeManager()

        await serve_continuous_voice_stream(socket, manager)

        self.assertTrue(socket.accepted)
        self.assertEqual(manager.started, [("session-1", "standard", None)])
        self.assertEqual(manager.stopped, 1)
        self.assertEqual(
            [message["type"] for message in socket.sent],
            [
                "audio.stream.ready",
                "speech.start",
                "transcript.final",
                "audio.stream.stopped",
            ],
        )
        self.assertNotIn("pcm", repr(socket.sent).lower())

    async def test_invalid_noise_profile_fails_closed(self):
        socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "maximum-magic",
                    },
                }
            ]
        )
        manager = FakeManager()

        await serve_continuous_voice_stream(socket, manager)

        self.assertEqual(manager.started, [])
        self.assertEqual(socket.sent[0]["type"], "audio.error")


class CaptureQueueTests(unittest.TestCase):
    def test_capture_reader_queue_is_bounded_and_reports_dropped_frames(self):
        class Service:
            def __init__(self):
                self.overruns = []

            def mark_overrun(self, dropped):
                self.overruns.append(dropped)

        service = Service()
        manager = NativeContinuousCaptureManager(service=service, frame_queue_size=2)

        manager._enqueue_frame(b"a", 48_000)
        manager._enqueue_frame(b"b", 48_000)
        manager._enqueue_frame(b"c", 48_000)

        self.assertLessEqual(manager._frame_queue.qsize(), 2)
        self.assertEqual(service.overruns, [1])

    def test_running_microphone_stream_rejects_a_different_session(self):
        class Process:
            @staticmethod
            def poll():
                return None

        manager = NativeContinuousCaptureManager()
        manager._process = Process()
        manager._session_id = "owner-session"

        with self.assertRaisesRegex(RuntimeError, "another conversation"):
            manager.attach(session_id="other-session")


if __name__ == "__main__":
    unittest.main()
