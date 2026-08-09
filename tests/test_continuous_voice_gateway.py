import asyncio
import queue
import threading
import unittest
from unittest.mock import MagicMock, patch

from voice.streaming_ws import (
    GATEWAY_ERROR_CATEGORIES,
    VoiceGatewayDiagnostics,
    get_gateway_diagnostics,
    serve_continuous_voice_stream,
)
from voice.continuous_capture import (
    ContinuousVoiceService,
    NativeContinuousCaptureManager,
)


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
        self.cancel_finished = asyncio.Event()
        self.allow_cancel_finish = asyncio.Event()

    async def receive_json(self):
        if self.messages:
            return self.messages.pop(0)
        try:
            await self.release_receive.wait()
        except asyncio.CancelledError:
            self.cancel_entered.set()
            try:
                await self.allow_cancel_finish.wait()
            finally:
                self.cancel_finished.set()
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

    def stop(self, *, session_id=None, owner_generation=None):
        self.stopped += 1
        self.stopped_sessions.append(session_id)
        self.events.append({"type": "audio.stream.stopped", "sequence": 4})
        return {"ok": True, "running": False}

    def events_after(self, after_sequence, *, session_id=None, owner_generation=None, timeout=0.0):
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

    def stop(self, *, session_id=None, owner_generation=None):
        if self.active_session != session_id:
            raise RuntimeError("microphone stream belongs to another conversation")
        self.active_session = None
        return super().stop(session_id=session_id, owner_generation=owner_generation)


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

    def stop(self, *, session_id=None, owner_generation=None):
        self.stop_calls.append(session_id)
        if len(self.stop_calls) > 1:
            self.duplicate_stop.set()
            return {"ok": True, "running": False}
        self.stop_entered.set()
        self.allow_stop_return.wait(timeout=2)
        result = super().stop(session_id=session_id, owner_generation=owner_generation)
        self.stop_finished.set()
        return result


class StopThenEventsManager(BlockingStopManager):
    def events_after(self, after_sequence, *, session_id=None, owner_generation=None, timeout=0.0):
        if not self.stop_entered.wait(timeout=1):
            raise RuntimeError("stop did not begin before event polling")
        return super().events_after(
            after_sequence,
            session_id=session_id,
            owner_generation=owner_generation,
            timeout=timeout,
        )


class FailingEventsManager(FakeManager):
    def events_after(self, after_sequence, *, session_id=None, owner_generation=None, timeout=0.0):
        raise RuntimeError("event pump failed")


class GenerationReplayManager(FakeManager):
    def __init__(self):
        super().__init__()
        self.second_poll_entered = threading.Event()
        self.allow_current_ready = threading.Event()

    def start(self, *, session_id, noise_profile, device_index=None):
        super().start(
            session_id=session_id,
            noise_profile=noise_profile,
            device_index=device_index,
        )
        return {"ok": True, "running": True, "owner_generation": 2}

    def events_after(self, after_sequence, *, session_id=None, owner_generation=None, timeout=0.0):
        if after_sequence <= 0:
            return [
                {
                    "type": "audio.stream.ready",
                    "owner_generation": 1,
                    "sequence": 1,
                }
            ]
        if after_sequence == 1:
            self.second_poll_entered.set()
            self.allow_current_ready.wait(timeout=2)
            return [
                {
                    "type": "audio.stream.ready",
                    "owner_generation": 2,
                    "sequence": 2,
                }
            ]
        return []


class CurrentGenerationReadySocket(BlockingReceiveSocket):
    async def send_json(self, payload):
        self.sent.append(payload)
        if (
            payload.get("type") == "audio.stream.ready"
            and payload.get("owner_generation") == 2
        ):
            self.ready_sent.set()


class ExpectedGenerationReadySocket(BlockingReceiveSocket):
    def __init__(self, messages, expected_generation):
        super().__init__(messages)
        self.expected_generation = expected_generation

    async def send_json(self, payload):
        self.sent.append(payload)
        if (
            payload.get("type") == "audio.stream.ready"
            and payload.get("owner_generation") == self.expected_generation
        ):
            self.ready_sent.set()


class OverlappingGenerationManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._generation = 0
        self._sequence = 0
        self._events = []
        self.active_session = None
        self.active_generation = 0
        self.successful_stops = []
        self.rejected_stops = []

    def start(self, *, session_id, noise_profile, device_index=None):
        with self._lock:
            self._generation += 1
            self.active_session = session_id
            self.active_generation = self._generation
            self._sequence += 1
            self._events.append(
                {
                    "type": "audio.stream.ready",
                    "owner_generation": self._generation,
                    "sequence": self._sequence,
                }
            )
            return {
                "ok": True,
                "running": True,
                "owner_generation": self._generation,
            }

    def attach(self, *, session_id):
        return self.start(session_id=session_id, noise_profile="standard")

    def stop(self, *, session_id=None, owner_generation=None):
        with self._lock:
            if (
                self.active_session != session_id
                or owner_generation != self.active_generation
            ):
                self.rejected_stops.append(owner_generation)
                raise RuntimeError("microphone stream belongs to another owner generation")
            self.successful_stops.append(owner_generation)
            self.active_session = None
            self.active_generation = 0
            return {"ok": True, "running": False}

    def events_after(
        self,
        after_sequence,
        *,
        session_id=None,
        owner_generation=None,
        timeout=0.0,
    ):
        with self._lock:
            if owner_generation != self.active_generation:
                raise RuntimeError("microphone stream belongs to another owner generation")
            return [
                event.copy()
                for event in self._events
                if event["sequence"] > after_sequence
            ]


class ContinuousVoiceGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_intentional_close_does_not_turn_a_later_start_into_reconnect(self):
        diagnostics = VoiceGatewayDiagnostics()
        diagnostics.note_connection("private-explicit-stop", 1)
        diagnostics.note_ready("private-explicit-stop", 1)
        diagnostics.note_closed("private-explicit-stop", 1)
        diagnostics.note_connection("private-explicit-stop", 2)
        diagnostics.note_ready("private-explicit-stop", 2)

        snapshot = diagnostics.snapshot()

        self.assertEqual(snapshot["reconnect_attempts_total"], 0)
        self.assertEqual(snapshot["recoveries_total"], 0)
        self.assertNotIn("private-explicit-stop", repr(snapshot))

    async def test_overlapping_same_session_reconnect_cannot_be_stopped_by_old_handler(self):
        diagnostics = VoiceGatewayDiagnostics()
        manager = OverlappingGenerationManager()

        def socket_for(generation):
            return ExpectedGenerationReadySocket(
                [
                    {
                        "type": "audio.stream.start",
                        "payload": {
                            "session_id": "shared-session",
                            "noise_profile": "standard",
                        },
                    }
                ],
                generation,
            )

        first_socket = socket_for(1)
        first_task = asyncio.create_task(
            serve_continuous_voice_stream(
                first_socket,
                manager,
                diagnostics=diagnostics,
            )
        )
        await asyncio.wait_for(first_socket.ready_sent.wait(), timeout=1)

        second_socket = socket_for(2)
        second_task = asyncio.create_task(
            serve_continuous_voice_stream(
                second_socket,
                manager,
                diagnostics=diagnostics,
            )
        )
        try:
            await asyncio.wait_for(second_socket.ready_sent.wait(), timeout=1)
            first_socket.release_receive.set()
            await asyncio.wait_for(first_task, timeout=1)

            self.assertEqual(manager.active_generation, 2)
            self.assertEqual(manager.successful_stops, [])
            self.assertEqual(manager.rejected_stops, [1])

            second_socket.release_receive.set()
            await asyncio.wait_for(second_task, timeout=1)
        finally:
            first_socket.release_receive.set()
            second_socket.release_receive.set()
            for task in (first_task, second_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(first_task, second_task, return_exceptions=True)

        snapshot = diagnostics.snapshot()
        self.assertEqual(manager.successful_stops, [2])
        self.assertEqual(manager.active_generation, 0)
        self.assertEqual(snapshot["reconnect_attempts_total"], 1)
        self.assertEqual(snapshot["recoveries_total"], 1)
        self.assertEqual(snapshot["active_tasks"], 0)

    async def test_recovery_requires_ready_from_the_acquired_owner_generation(self):
        diagnostics = VoiceGatewayDiagnostics()
        diagnostics.note_disconnect("generation-session")
        socket = CurrentGenerationReadySocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "generation-session",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = GenerationReplayManager()
        task = asyncio.create_task(
            serve_continuous_voice_stream(
                socket,
                manager,
                diagnostics=diagnostics,
            )
        )

        try:
            entered = await asyncio.to_thread(manager.second_poll_entered.wait, 1)
            self.assertTrue(entered)
            self.assertEqual(diagnostics.snapshot()["recoveries_total"], 0)

            manager.allow_current_ready.set()
            await asyncio.wait_for(socket.ready_sent.wait(), timeout=1)
            socket.release_receive.set()
            await asyncio.wait_for(task, timeout=1)
        finally:
            manager.allow_current_ready.set()
            socket.release_receive.set()
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)

        snapshot = diagnostics.snapshot()
        self.assertEqual(snapshot["recoveries_total"], 1)
        self.assertEqual(snapshot["recovery_ms"]["samples"], 1)

    async def test_gateway_diagnostics_release_all_tasks_and_redact_session(self):
        diagnostics = VoiceGatewayDiagnostics()
        socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "private-conversation-id",
                        "noise_profile": "standard",
                    },
                }
            ]
        )
        manager = FakeManager()

        await asyncio.wait_for(
            serve_continuous_voice_stream(
                socket,
                manager,
                diagnostics=diagnostics,
            ),
            timeout=1,
        )
        await asyncio.sleep(0)

        snapshot = diagnostics.snapshot()
        self.assertEqual(snapshot["active_handlers"], 0)
        self.assertEqual(snapshot["active_child_tasks"], 0)
        self.assertEqual(snapshot["active_tasks"], 0)
        self.assertEqual(snapshot["connections_total"], 1)
        self.assertEqual(snapshot["errors"]["recoverable_total"], 1)
        self.assertEqual(snapshot["errors"]["by_category"]["socket_disconnect"], 1)
        self.assertEqual(
            set(snapshot["errors"]["by_category"]),
            set(GATEWAY_ERROR_CATEGORIES),
        )
        self.assertNotIn("private-conversation-id", repr(snapshot))
        self.assertEqual(set(get_gateway_diagnostics()), set(snapshot))

    async def test_gateway_diagnostics_count_release_task_until_stop_finishes(self):
        diagnostics = VoiceGatewayDiagnostics()
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
        manager = BlockingStopManager()
        gateway_task = asyncio.create_task(
            serve_continuous_voice_stream(
                socket,
                manager,
                diagnostics=diagnostics,
            )
        )

        try:
            await asyncio.wait_for(socket.ready_sent.wait(), timeout=1)
            socket.release_receive.set()
            entered = await asyncio.to_thread(manager.stop_entered.wait, 1)
            self.assertTrue(entered)

            active = diagnostics.snapshot()
            self.assertEqual(active["active_handlers"], 1)
            self.assertGreaterEqual(active["active_child_tasks"], 1)
            self.assertEqual(
                active["active_tasks"],
                active["active_handlers"] + active["active_child_tasks"],
            )
        finally:
            manager.allow_stop_return.set()
            await asyncio.gather(gateway_task, return_exceptions=True)
            await asyncio.sleep(0)

        stopped = diagnostics.snapshot()
        self.assertEqual(stopped["active_handlers"], 0)
        self.assertEqual(stopped["active_child_tasks"], 0)
        self.assertEqual(stopped["active_tasks"], 0)

    async def test_gateway_diagnostics_classify_invalid_request_without_error_text(self):
        diagnostics = VoiceGatewayDiagnostics()
        socket = FakeSocket(
            [
                {
                    "type": "audio.stream.start",
                    "payload": {
                        "session_id": "private-invalid-session",
                        "noise_profile": "not-a-profile",
                    },
                }
            ]
        )

        await serve_continuous_voice_stream(
            socket,
            FakeManager(),
            diagnostics=diagnostics,
        )
        await asyncio.sleep(0)

        snapshot = diagnostics.snapshot()
        self.assertEqual(snapshot["errors"]["nonrecoverable_total"], 1)
        self.assertEqual(snapshot["errors"]["by_category"]["invalid_request"], 1)
        self.assertNotIn("not-a-profile", repr(snapshot))
        self.assertNotIn("private-invalid-session", repr(snapshot))

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
        stop_finished_at_gateway_done = []
        gateway_task.add_done_callback(
            lambda _: stop_finished_at_gateway_done.append(
                manager.stop_finished.is_set()
            )
        )

        try:
            await asyncio.wait_for(socket.ready_sent.wait(), timeout=1)
            gateway_task.cancel()
            await asyncio.wait_for(socket.cancel_entered.wait(), timeout=1)
            gateway_task.cancel()
            await asyncio.sleep(0.05)

            self.assertFalse(gateway_task.done())
            socket.allow_cancel_finish.set()
            await asyncio.wait_for(socket.cancel_finished.wait(), timeout=1)
            await asyncio.sleep(0)

            self.assertFalse(gateway_task.done())
            entered = await asyncio.to_thread(manager.stop_entered.wait, 0.2)
            self.assertTrue(entered)
            self.assertEqual(manager.stop_calls, ["session-1"])
            self.assertFalse(manager.stop_finished.is_set())

            manager.allow_stop_return.set()
            results = await asyncio.gather(gateway_task, return_exceptions=True)
            finished = manager.stop_finished.is_set()
        finally:
            socket.allow_cancel_finish.set()
            manager.allow_stop_return.set()
            if not gateway_task.done():
                await asyncio.gather(gateway_task, return_exceptions=True)

        self.assertTrue(finished)
        self.assertEqual(stop_finished_at_gateway_done, [True])
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
    @staticmethod
    def _empty_service_status(*, frames=0, generation=0):
        return {
            "configured": True,
            "service_generation": generation,
            "counters": {
                "frames": frames,
                "turns": 0,
                "transcript_final": 0,
                "transcript_empty": 0,
                "audio_error": 0,
            },
            "queues": {
                "transcription": {
                    "current": 0,
                    "peak": 0,
                    "limit": 3,
                    "dropped": 0,
                }
            },
        }

    def test_successful_capture_starts_advance_owner_generation_without_exposing_id(self):
        service = MagicMock()
        service.status.return_value = {
            "configured": True,
            "counters": {
                "frames": 0,
                "turns": 0,
                "transcript_final": 0,
                "transcript_empty": 0,
                "audio_error": 0,
            },
            "queues": {
                "transcription": {
                    "current": 0,
                    "peak": 0,
                    "limit": 3,
                    "dropped": 0,
                }
            },
        }
        manager = NativeContinuousCaptureManager(service=service)
        stop_path = MagicMock()
        status_path = MagicMock()
        first_process = MagicMock()
        first_process.poll.return_value = None
        second_process = MagicMock()
        second_process.poll.return_value = None
        thread = MagicMock()

        with (
            patch("voice.continuous_capture.WORKER") as worker,
            patch(
                "voice.continuous_capture.subprocess.Popen",
                side_effect=[first_process, second_process],
            ),
            patch("voice.continuous_capture.threading.Thread", return_value=thread),
            patch.object(manager, "_paths", return_value=(stop_path, status_path)),
            patch.object(
                manager,
                "_read_status",
                return_value={"ok": True, "rate": 48_000},
            ),
        ):
            worker.is_file.return_value = True
            first = manager.start(session_id="private-owner-one")
            same = manager.start(session_id="private-owner-one")
            with self.assertRaisesRegex(RuntimeError, "another owner generation"):
                manager.stop(
                    session_id="private-owner-one",
                    owner_generation=first["owner_generation"],
                )
            manager.stop(
                session_id="private-owner-one",
                owner_generation=same["owner_generation"],
            )
            second = manager.start(session_id="private-owner-two")

        self.assertEqual(first["owner_generation"], 1)
        self.assertEqual(same["owner_generation"], 2)
        self.assertEqual(second["owner_generation"], 3)
        self.assertTrue(first["owner_identity"])
        self.assertNotEqual(first["owner_identity"], second["owner_identity"])
        self.assertNotIn("private-owner-one", repr(first))
        self.assertNotIn("private-owner-two", repr(second))

    def test_same_session_takeover_clears_old_ready_replay_and_keeps_sequence(self):
        service = ContinuousVoiceService(transcribe=lambda *_: "")
        manager = NativeContinuousCaptureManager(service=service)
        process = MagicMock()
        process.poll.return_value = None
        stop_path = MagicMock()
        status_path = MagicMock()
        thread = MagicMock()

        with (
            patch("voice.continuous_capture.WORKER") as worker,
            patch("voice.continuous_capture.subprocess.Popen", return_value=process),
            patch("voice.continuous_capture.threading.Thread", return_value=thread),
            patch.object(manager, "_paths", return_value=(stop_path, status_path)),
            patch.object(
                manager,
                "_read_status",
                return_value={"ok": True, "rate": 48_000},
            ),
        ):
            worker.is_file.return_value = True
            first = manager.start(session_id="same-private-owner")
            first_ready = service.events_after(0)
            second = manager.start(session_id="same-private-owner")
            current_events = service.events_after(0)
            manager.stop(
                session_id="same-private-owner",
                owner_generation=second["owner_generation"],
            )

        self.assertEqual(first["owner_generation"], 1)
        self.assertEqual(second["owner_generation"], 2)
        self.assertEqual(len(first_ready), 1)
        self.assertEqual(first_ready[0]["owner_generation"], 1)
        self.assertEqual(len(current_events), 1)
        self.assertEqual(current_events[0]["owner_generation"], 2)
        self.assertGreater(
            current_events[0]["sequence"],
            first_ready[0]["sequence"],
        )

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

    def test_processor_discards_a_frame_from_an_obsolete_owner_generation(self):
        class Service:
            def __init__(self):
                self.ingested = []

            def ingest_pcm(self, pcm, sample_rate):
                self.ingested.append((pcm, sample_rate))

            @staticmethod
            def mark_error(message):
                raise AssertionError(message)

        class Process:
            @staticmethod
            def poll():
                return None

        service = Service()
        manager = NativeContinuousCaptureManager(service=service)
        process = Process()
        manager._process = process
        manager._owner_generation = 2
        manager._frame_queue.put_nowait((b"old", 48_000, 1))
        manager._frame_queue.put_nowait((b"new", 48_000, 2))
        manager._frame_queue.put_nowait(None)

        manager._process_frames(process)

        self.assertEqual(service.ingested, [(b"new", 48_000)])

    def test_status_retries_instead_of_mixing_manager_and_service_generations(self):
        class SwitchingService:
            def __init__(self):
                self.manager = None
                self.calls = 0

            def status(self):
                self.calls += 1
                if self.calls == 1:
                    with self.manager._lock:
                        self.manager._owner_generation = 2
                        self.manager._session_id = "second-private-owner"
                        self.manager._owner_identity = self.manager._fingerprint_session(
                            self.manager._session_id
                        )
                    return CaptureQueueTests._empty_service_status(
                        frames=1,
                        generation=1,
                    )
                return CaptureQueueTests._empty_service_status(
                    frames=2,
                    generation=2,
                )

        service = SwitchingService()
        manager = NativeContinuousCaptureManager(service=service)
        service.manager = manager
        manager._owner_generation = 1
        manager._session_id = "first-private-owner"
        manager._owner_identity = manager._fingerprint_session(manager._session_id)

        status = manager.status()

        self.assertEqual(service.calls, 2)
        self.assertEqual(status["owner_generation"], 2)
        self.assertEqual(status["service_generation"], 2)
        self.assertEqual(status["counters"]["frames"], 2)
        self.assertNotIn("first-private-owner", repr(status))
        self.assertNotIn("second-private-owner", repr(status))

    def test_stop_cleanup_blocks_next_start_and_never_joins_new_threads(self):
        class RecordingService:
            def __init__(self):
                self.events = []
                self.generation = 1

            def configure(self, *, noise_profile):
                self.generation += 1
                self.events.append(("configure", self.generation))
                return self.status()

            def mark_ready(self, metadata):
                self.events.append(("ready", metadata["owner_generation"]))

            def mark_stopped(self):
                self.events.append(("stopped", self.generation))

            def status(self):
                return CaptureQueueTests._empty_service_status(
                    generation=self.generation
                )

        class BlockingProcess:
            def __init__(self):
                self.wait_entered = threading.Event()
                self.allow_wait = threading.Event()
                self.finished = False

            def poll(self):
                return 0 if self.finished else None

            def wait(self, timeout=None):
                self.wait_entered.set()
                self.allow_wait.wait(timeout=2)
                self.finished = True
                return 0

        service = RecordingService()
        manager = NativeContinuousCaptureManager(service=service)
        old_process = BlockingProcess()
        old_reader = MagicMock()
        old_processor = MagicMock()
        old_queue = manager._frame_queue
        manager._process = old_process
        manager._reader = old_reader
        manager._processor = old_processor
        manager._stop_path = MagicMock()
        manager._status_path = MagicMock()
        manager._session_id = "old-owner"
        manager._owner_generation = 1
        manager._owner_identity = manager._fingerprint_session("old-owner")

        new_process = MagicMock()
        new_process.poll.return_value = None
        new_processor = MagicMock()
        new_reader = MagicMock()
        popen = MagicMock(return_value=new_process)
        stop_result = {}
        start_result = {}
        stop_done = threading.Event()
        start_done = threading.Event()
        real_thread = threading.Thread

        def stop_capture():
            stop_result.update(
                manager.stop(session_id="old-owner", owner_generation=1)
            )
            stop_done.set()

        def start_capture():
            start_result.update(manager.start(session_id="new-owner"))
            start_done.set()

        with (
            patch("voice.continuous_capture.WORKER") as worker,
            patch("voice.continuous_capture.subprocess.Popen", popen),
            patch(
                "voice.continuous_capture.threading.Thread",
                side_effect=[new_processor, new_reader],
            ),
            patch.object(
                manager,
                "_paths",
                return_value=(MagicMock(), MagicMock()),
            ),
            patch.object(
                manager,
                "_read_status",
                return_value={"ok": True, "rate": 48_000},
            ),
        ):
            worker.is_file.return_value = True
            stop_thread = real_thread(target=stop_capture)
            stop_thread.start()
            self.assertTrue(old_process.wait_entered.wait(timeout=1))

            start_thread = real_thread(target=start_capture)
            start_thread.start()
            self.assertFalse(start_done.wait(timeout=0.05))
            popen.assert_not_called()
            self.assertEqual(service.events, [])

            old_process.allow_wait.set()
            self.assertTrue(stop_done.wait(timeout=1))
            self.assertTrue(start_done.wait(timeout=1))
            stop_thread.join(timeout=1)
            start_thread.join(timeout=1)

        self.assertFalse(stop_result["running"])
        self.assertTrue(start_result["running"])
        self.assertEqual(start_result["owner_generation"], 2)
        self.assertEqual(
            service.events,
            [("stopped", 1), ("configure", 2), ("ready", 2)],
        )
        old_reader.join.assert_called_once_with(timeout=1)
        old_processor.join.assert_called_once_with(timeout=2)
        new_reader.join.assert_not_called()
        new_processor.join.assert_not_called()
        self.assertIsNot(manager._frame_queue, old_queue)

    def test_capture_queue_does_not_report_a_drop_if_consumer_won_the_race(self):
        class Service:
            def __init__(self):
                self.overruns = []

            def mark_overrun(self, dropped):
                self.overruns.append(dropped)

        class ConsumerWonQueue:
            maxsize = 2

            def __init__(self):
                self.put_calls = 0

            def put_nowait(self, item):
                self.put_calls += 1
                if self.put_calls == 1:
                    raise queue.Full

            @staticmethod
            def get_nowait():
                raise queue.Empty

            @staticmethod
            def qsize():
                return 0

        service = Service()
        manager = NativeContinuousCaptureManager(service=service, frame_queue_size=2)
        manager._frame_queue = ConsumerWonQueue()

        manager._enqueue_frame(b"frame", 48_000)

        self.assertEqual(manager._dropped_capture_frames, 0)
        self.assertEqual(service.overruns, [])

    def test_capture_status_exposes_bounded_queue_and_redacted_owner(self):
        class Service:
            @staticmethod
            def mark_overrun(dropped):
                return None

            @staticmethod
            def status():
                return {
                    "configured": True,
                    "counters": {
                        "frames": 2,
                        "turns": 0,
                        "transcript_final": 0,
                        "transcript_empty": 0,
                        "audio_error": 0,
                    },
                    "queues": {
                        "transcription": {
                            "current": 0,
                            "peak": 0,
                            "limit": 3,
                            "dropped": 0,
                        }
                    },
                }

        manager = NativeContinuousCaptureManager(service=Service(), frame_queue_size=2)
        owner = "private-owner-session"
        self.assertEqual(
            manager._fingerprint_session(owner),
            manager._fingerprint_session(owner),
        )
        self.assertNotEqual(
            manager._fingerprint_session(owner),
            manager._fingerprint_session("different-owner-session"),
        )
        manager._session_id = owner
        manager._owner_generation = 4
        manager._owner_identity = manager._fingerprint_session(owner)
        manager._enqueue_frame(b"a", 48_000)
        manager._enqueue_frame(b"b", 48_000)
        manager._enqueue_frame(b"c", 48_000)

        status = manager.status()

        self.assertEqual(status["owner_generation"], 4)
        self.assertTrue(status["owner_identity"])
        self.assertNotEqual(status["owner_identity"], owner)
        self.assertNotIn(owner, repr(status))
        self.assertEqual(
            status["queues"]["capture"],
            {
                "current": 2,
                "peak": 2,
                "limit": 2,
                "dropped_frames": 1,
            },
        )
        self.assertEqual(status["capture_queue"], 2)
        self.assertEqual(status["capture_queue_peak"], 2)
        self.assertEqual(status["capture_dropped_frames"], 1)
        self.assertEqual(status["counters"]["frames"], 2)
        self.assertEqual(status["counters"]["capture_frames_received"], 3)

        stopped = manager.stop(session_id=owner)
        self.assertFalse(stopped["session_attached"])
        self.assertEqual(stopped["owner_generation"], 4)
        self.assertEqual(stopped["owner_identity"], "")

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
