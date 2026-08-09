import asyncio
import threading
import unittest

from fastapi import WebSocketDisconnect

from voice.streaming_ws import VoiceGatewayDiagnostics, serve_continuous_voice_stream


class DisconnectingSocket:
    def __init__(self, session_id: str, *, disconnect_after_ready: bool = False):
        self._messages = [
            {
                "type": "audio.stream.start",
                "payload": {
                    "session_id": session_id,
                    "noise_profile": "standard",
                },
            }
        ]
        self._disconnect_after_ready = disconnect_after_ready
        self._ready_sent = asyncio.Event()
        self.accepted = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if self._messages:
            return self._messages.pop(0)
        if self._disconnect_after_ready:
            await self._ready_sent.wait()
        raise WebSocketDisconnect()

    async def send_json(self, payload):
        self.sent.append(payload)
        if payload.get("type") == "audio.stream.ready":
            self._ready_sent.set()


class ReconnectTrackingManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._events = []
        self._next_sequence = 1
        self.active_session = None
        self.max_active_owners = 0
        self.started_sessions = []
        self.stopped_sessions = []
        self.owner_generation = 0

    def start(self, *, session_id, noise_profile, device_index=None):
        with self._lock:
            if self.active_session is not None:
                raise RuntimeError("microphone stream belongs to another conversation")
            self.active_session = session_id
            self.owner_generation += 1
            owner_generation = self.owner_generation
            self.max_active_owners = max(self.max_active_owners, 1)
            self.started_sessions.append(session_id)
            self._events.append(
                {
                    "type": "audio.stream.ready",
                    "session_id": session_id,
                    "owner_generation": owner_generation,
                    "sequence": self._next_sequence,
                }
            )
            self._next_sequence += 1
        return {
            "ok": True,
            "running": True,
            "owner_generation": owner_generation,
        }

    def attach(self, *, session_id):
        raise AssertionError("reconnect integration must acquire a fresh lease")

    def stop(self, *, session_id=None, owner_generation=None):
        with self._lock:
            if self.active_session != session_id:
                raise RuntimeError("microphone stream belongs to another conversation")
            if owner_generation != self.owner_generation:
                raise RuntimeError("microphone stream belongs to another owner generation")
            self.active_session = None
            self.stopped_sessions.append(session_id)
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
            if owner_generation != self.owner_generation:
                raise RuntimeError("microphone stream belongs to another owner generation")
            return [
                event.copy()
                for event in self._events
                if event["session_id"] == session_id
                and event["sequence"] > after_sequence
            ]


class VoiceReconnectIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_conversation_reconnect_records_bounded_recovery_diagnostics(self):
        manager = ReconnectTrackingManager()
        diagnostics = VoiceGatewayDiagnostics(recovery_sample_limit=2)

        for _ in range(4):
            socket = DisconnectingSocket(
                "private-reconnect-session",
                disconnect_after_ready=True,
            )
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
        self.assertEqual(snapshot["connections_total"], 4)
        self.assertEqual(snapshot["reconnect_attempts_total"], 3)
        self.assertEqual(snapshot["recoveries_total"], 3)
        self.assertEqual(snapshot["recovery_ms"]["samples"], 2)
        self.assertIsInstance(snapshot["recovery_ms"]["last"], int)
        self.assertIsInstance(snapshot["recovery_ms"]["p50"], int)
        self.assertIsInstance(snapshot["recovery_ms"]["p95"], int)
        self.assertEqual(snapshot["active_tasks"], 0)
        self.assertNotIn("private-reconnect-session", repr(snapshot))

    async def test_disconnect_releases_owner_before_next_conversation_connects(self):
        manager = ReconnectTrackingManager()
        diagnostics = VoiceGatewayDiagnostics()
        tasks_before = set(asyncio.all_tasks())

        first = DisconnectingSocket("conversation-first")
        await asyncio.wait_for(
            serve_continuous_voice_stream(
                first,
                manager,
                diagnostics=diagnostics,
            ),
            timeout=1,
        )

        self.assertIsNone(manager.active_session)
        self.assertEqual(manager.stopped_sessions, ["conversation-first"])

        second = DisconnectingSocket(
            "conversation-second",
            disconnect_after_ready=True,
        )
        await asyncio.wait_for(
            serve_continuous_voice_stream(
                second,
                manager,
                diagnostics=diagnostics,
            ),
            timeout=1,
        )
        await asyncio.sleep(0)

        self.assertTrue(second.accepted)
        self.assertIn("audio.stream.ready", [event["type"] for event in second.sent])
        self.assertEqual(
            manager.started_sessions,
            ["conversation-first", "conversation-second"],
        )
        self.assertEqual(
            manager.stopped_sessions,
            ["conversation-first", "conversation-second"],
        )
        self.assertIsNone(manager.active_session)
        self.assertEqual(manager.max_active_owners, 1)
        snapshot = diagnostics.snapshot()
        self.assertEqual(snapshot["reconnect_attempts_total"], 0)
        self.assertEqual(snapshot["recoveries_total"], 0)
        self.assertEqual(snapshot["active_tasks"], 0)

        leaked = [
            task
            for task in asyncio.all_tasks() - tasks_before
            if not task.done()
        ]
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
