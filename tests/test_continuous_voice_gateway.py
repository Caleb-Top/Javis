import asyncio
import queue
import threading
import unittest

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


class FailingSendSocket(FakeSocket):
    def __init__(self, messages):
        super().__init__(messages)
        self.send_attempted = asyncio.Event()
        self.send_attempts = 0

    async def receive_json(self):
        if self.messages:
            return self.messages.pop(0)
        await self.send_attempted.wait()
        from fastapi import WebSocketDisconnect

        raise WebSocketDisconnect()

    async def send_json(self, payload):
        self.send_attempts += 1
        self.send_attempted.set()
        raise RuntimeError('Cannot call "send" once a close message has been sent.')


class ClosingReceiveSocket(FakeSocket):
    async def receive_json(self):
        if self.messages:
            return self.messages.pop(0)
        raise RuntimeError(
            'Cannot call "receive" once a disconnect message has been received.'
        )


class UnexpectedReceiveSocket(FakeSocket):
    async def receive_json(self):
        if self.messages:
            return self.messages.pop(0)
        raise RuntimeError("receive parser exploded")


class UnexpectedErrorSendSocket(UnexpectedReceiveSocket):
    async def send_json(self, payload):
        raise RuntimeError("send serializer failed")


class BlockingReceiveSocket(FakeSocket):
    def __init__(self, messages):
        super().__init__(messages)
        self.ready_sent = asyncio.Event()
        self.release_receive = asyncio.Event()

    async def receive_json(self):
        if self.messages:
            return self.messages.pop(0)
        await self.release_receive.wait()
        from fastapi import WebSocketDisconnect

        raise WebSocketDisconnect()

    async def send_json(self, payload):
        await super().send_json(payload)
        if payload.get("type") == "audio.stream.ready":
            self.ready_sent.set()


class SlowCancellingReceiveSocket(BlockingReceiveSocket):
    def __init__(self, messages):
        super().__init__(messages)
        self.cancel_entered = asyncio.Event()
        self.allow_cancel_finish = asyncio.Event()

    async def receive_json(self):
        if self.messages:
            return self.messages.pop(0)
        try:
            await self.release_receive.wait()
        except asyncio.CancelledError:
            self.cancel_entered.set()
            await self.allow_cancel_finish.wait()
            raise


class FakeManager:
    def __init__(self):
        self.started = []
        self.attached = []
        self.stopped = 0
        self.stopped_sessions = []
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
        self.stopped_sessions.append(session_id)
        self.events.append({"type": "audio.stream.stopped", "sequence": 4})
        return {"ok": True, "running": False}

    def events_after(self, after_sequence, *, session_id=None, timeout=0.0):
        return [event for event in self.events if event["sequence"] > after_sequence]


class LeaseTrackingManager(FakeManager):
    def __init__(self):
        super().__init__()
        self.active_session = None

    def start(self, *, session_id, noise_profile, device_index=None):
        if self.active_session is not None:
            raise RuntimeError("microphone stream belongs to another conversation")
        self.active_session = session_id
        return super().start(
            session_id=session_id,
            noise_profile=noise_profile,
            device_index=device_index,
        )

    def stop(self, *, session_id=None):
        if self.active_session != session_id:
            raise RuntimeError("microphone stream belongs to another conversation")
        self.active_session = None
        return super().stop(session_id=session_id)


class BlockingStartManager(FakeManager):
    def __init__(self):
        super().__init__()
        self.start_acquired = threading.Event()
        self.allow_start_return = threading.Event()
        self.start_finished = threading.Event()

    def start(self, *, session_id, noise_profile, device_index=None):
        result = super().start(
            session_id=session_id,
            noise_profile=noise_profile,
            device_index=device_index,
        )
        self.start_acquired.set()
        self.allow_start_return.wait(timeout=2)
        self.start_finished.set()
        return result


class BlockingStopManager(FakeManager):
    def __init__(self):
        super().__init__()
        self.stop_calls = []
        self.stop_entered = threading.Event()
        self.duplicate_stop = threading.Event()
        self.allow_stop_return = threading.Event()
        self.stop_finished = threading.Event()

    def stop(self, *, session_id=None):
        self.stop_calls.append(session_id)
        if len(self.stop_calls) > 1:
            self.duplicate_stop.set()
            return {"ok": True, "running": False}
        self.stop_entered.set()
        self.allow_stop_return.wait(timeout=2)
        result = super().stop(session_id=session_id)
        self.stop_finished.set()
        return result


class StopThenEventsManager(BlockingStopManager):
    def events_after(self, after_sequence, *, session_id=None, timeout=0.0):
        if not self.stop_entered.wait(timeout=1):
            raise RuntimeError("stop did not begin before event polling")
        return super().events_after(
            after_sequence,
            session_id=session_id,
            timeout=timeout,
        )


class FailingEventsManager(FakeManager):
    def events_after(self, after_sequence, *, session_id=None, timeout=0.0):
        raise RuntimeError("event pump failed")


class ContinuousVoiceGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_receive_failure_does_not_send_audio_error(self):
        socket = ClosingReceiveSocket([])
        manager = FakeManager()

        await asyncio.wait_for(
            serve_continuous_voice_stream(socket, manager),
            timeout=1,
        )

        self.assertEqual(socket.sent, [])
        self.assertEqual(manager.started, [])

    async def test_disconnect_releases_stream_lease(self):
        socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = FakeManager()

        await asyncio.wait_for(
            serve_continuous_voice_stream(socket, manager),
            timeout=1,
        )

        self.assertEqual(manager.stopped, 1)
        self.assertEqual(manager.stopped_sessions, ["session-1"])

    async def test_blocking_explicit_stop_is_single_flight_when_sender_fails(self):
        socket = FailingSendSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                },
                {
                    "type": "audio.stream.stop",
                    "payload": {"session_id": "session-1"},
                },
            ]
        )
        manager = StopThenEventsManager()
        gateway_task = asyncio.create_task(
            serve_continuous_voice_stream(socket, manager)
        )

        try:
            entered = await asyncio.to_thread(manager.stop_entered.wait, 1)
            self.assertTrue(entered)
            await asyncio.wait_for(socket.send_attempted.wait(), timeout=1)
            duplicate = await asyncio.to_thread(manager.duplicate_stop.wait, 0.2)

            self.assertFalse(duplicate)
            self.assertFalse(gateway_task.done())
        finally:
            manager.allow_stop_return.set()
            await asyncio.gather(gateway_task, return_exceptions=True)
            finished = await asyncio.to_thread(manager.stop_finished.wait, 1)

        self.assertTrue(finished)
        self.assertEqual(manager.stop_calls, ["session-1"])

    async def test_repeated_cancellation_during_child_cleanup_waits_for_release(self):
        socket = SlowCancellingReceiveSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = BlockingStopManager()
        gateway_task = asyncio.create_task(
            serve_continuous_voice_stream(socket, manager)
        )

        try:
            await asyncio.wait_for(socket.ready_sent.wait(), timeout=1)
            gateway_task.cancel()
            await asyncio.wait_for(socket.cancel_entered.wait(), timeout=1)
            gateway_task.cancel()
            await asyncio.sleep(0.05)

            self.assertFalse(gateway_task.done())
            entered = await asyncio.to_thread(manager.stop_entered.wait, 0.2)
            self.assertTrue(entered)
            self.assertEqual(manager.stop_calls, ["session-1"])
        finally:
            socket.allow_cancel_finish.set()
            manager.allow_stop_return.set()
            results = await asyncio.gather(gateway_task, return_exceptions=True)
            finished = (
                await asyncio.to_thread(manager.stop_finished.wait, 1)
                if manager.stop_entered.is_set()
                else False
            )

        self.assertTrue(finished)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertEqual(manager.stop_calls, ["session-1"])

    async def test_cancellation_during_start_releases_stream_lease(self):
        socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = BlockingStartManager()
        gateway_task = asyncio.create_task(
            serve_continuous_voice_stream(socket, manager)
        )

        acquired = await asyncio.to_thread(manager.start_acquired.wait, 1)
        self.assertTrue(acquired)
        gateway_task.cancel()
        await asyncio.sleep(0)
        manager.allow_start_return.set()

        with self.assertRaises(asyncio.CancelledError):
            await gateway_task
        finished = await asyncio.to_thread(manager.start_finished.wait, 1)

        self.assertTrue(finished)
        self.assertEqual(manager.stopped, 1)

    async def test_repeated_cancellation_during_start_releases_stream_lease(self):
        socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = BlockingStartManager()
        gateway_task = asyncio.create_task(
            serve_continuous_voice_stream(socket, manager)
        )

        try:
            acquired = await asyncio.to_thread(manager.start_acquired.wait, 1)
            self.assertTrue(acquired)
            gateway_task.cancel()
            await asyncio.sleep(0)
            gateway_task.cancel()
            await asyncio.sleep(0.05)

            self.assertFalse(gateway_task.done())
        finally:
            manager.allow_start_return.set()
            results = await asyncio.gather(gateway_task, return_exceptions=True)
            finished = await asyncio.to_thread(manager.start_finished.wait, 1)

        self.assertTrue(finished)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertEqual(manager.stopped_sessions, ["session-1"])

    async def test_send_failure_releases_stream_lease(self):
        socket = FailingSendSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = FakeManager()

        await asyncio.wait_for(
            serve_continuous_voice_stream(socket, manager),
            timeout=1,
        )

        self.assertEqual(socket.send_attempts, 1)
        self.assertEqual(manager.stopped, 1)

    async def test_closing_receive_does_not_send_audio_error(self):
        socket = ClosingReceiveSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = FakeManager()
        manager.events = []

        await asyncio.wait_for(
            serve_continuous_voice_stream(socket, manager),
            timeout=1,
        )

        self.assertEqual(socket.sent, [])
        self.assertEqual(manager.stopped, 1)

    async def test_unexpected_receive_runtime_error_is_reported(self):
        socket = UnexpectedReceiveSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = FakeManager()
        manager.events = []

        await asyncio.wait_for(
            serve_continuous_voice_stream(socket, manager),
            timeout=1,
        )

        self.assertEqual(
            socket.sent,
            [{"type": "audio.error", "message": "receive parser exploded"}],
        )
        self.assertEqual(manager.stopped_sessions, ["session-1"])

    async def test_unexpected_sender_error_is_visible_after_cleanup(self):
        socket = BlockingReceiveSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = FailingEventsManager()

        with self.assertRaisesRegex(RuntimeError, "event pump failed"):
            await asyncio.wait_for(
                serve_continuous_voice_stream(socket, manager),
                timeout=1,
            )

        self.assertEqual(manager.stopped_sessions, ["session-1"])

    async def test_unexpected_error_send_failure_is_visible_after_cleanup(self):
        socket = UnexpectedErrorSendSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = FakeManager()
        manager.events = []

        with self.assertRaisesRegex(RuntimeError, "send serializer failed"):
            await asyncio.wait_for(
                serve_continuous_voice_stream(socket, manager),
                timeout=1,
            )

        self.assertEqual(manager.stopped_sessions, ["session-1"])

    async def test_explicit_stop_releases_stream_lease_once(self):
        socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                },
                {
                    "type": "audio.stream.stop",
                    "payload": {"session_id": "session-1"},
                },
            ]
        )
        manager = FakeManager()

        await asyncio.wait_for(
            serve_continuous_voice_stream(socket, manager),
            timeout=1,
        )

        self.assertEqual(manager.stopped, 1)

    async def test_second_session_starts_after_first_socket_disconnects(self):
        manager = LeaseTrackingManager()
        first_socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-1",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        second_socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "session-2",
                        "noise_profile": "standard",
                    },
                }
            ]
        )

        await asyncio.wait_for(
            serve_continuous_voice_stream(first_socket, manager),
            timeout=1,
        )
        await asyncio.wait_for(
            serve_continuous_voice_stream(second_socket, manager),
            timeout=1,
        )

        self.assertEqual(
            manager.started,
            [
                ("session-1", "standard", None),
                ("session-2", "standard", None),
            ],
        )
        self.assertEqual(manager.stopped, 2)

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
