import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import WebSocketDisconnect

from core.agent_runs import AgentRunStore
from core.conversation_hub import ConversationHub
from core.conversation_store import ConversationStore
from gateway.conversation_stall_harness import (
    AFTER_ACK_TRIGGER,
    AFTER_DELTA_TRIGGER,
    NO_ACK_TRIGGER,
    ConversationStallHarness,
)
from gateway.conversation_ws import ConversationWebSocketGateway
from voice.turn_registry import VoiceTurnRegistry


class FakeWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)
        self.accepted = False
        self.sent = []
        self.attached = asyncio.Event()
        self.terminal = asyncio.Event()
        self.closed = []
        self.scope = {}

    async def accept(self, subprotocol=None):
        self.accepted = True

    async def close(self, code, reason=""):
        self.closed.append((code, reason))
        self.terminal.set()

    async def receive_text(self):
        if self._messages:
            return json.dumps(self._messages.pop(0))
        await self.terminal.wait()
        raise WebSocketDisconnect()

    async def send_json(self, message):
        self.sent.append(message)
        if message.get("type") == "conversation.attached":
            self.attached.set()
        if message.get("type") in {"request.completed", "request.cancelled", "request.failed"}:
            self.terminal.set()
        if message.get("type") in {"done", "error"}:
            self.terminal.set()


class FakeAgent:
    async def chat(self, text, **kwargs):
        token = kwargs["cancellation"]
        yield {"type": "activity", "activity": "understanding", "detail": "Understanding"}
        await token.checkpoint()
        yield {"type": "text_delta", "text": f"reply:{text}"}
        yield {"type": "done"}


class SlowAgent:
    def __init__(self):
        self.started = asyncio.Event()

    async def chat(self, text, **kwargs):
        token = kwargs["cancellation"]
        yield {"type": "activity", "activity": "understanding", "detail": "Understanding"}
        self.started.set()
        await token.race(asyncio.sleep(30))
        yield {"type": "text_delta", "text": "late answer"}


class CountingAgent(FakeAgent):
    def __init__(self):
        self.calls = 0

    async def chat(self, text, **kwargs):
        self.calls += 1
        async for event in super().chat(text, **kwargs):
            yield event


class QueuedWebSocket:
    def __init__(self, *, host="127.0.0.1"):
        self.client = SimpleNamespace(host=host)
        self.accepted = False
        self.sent = []
        self._incoming = asyncio.Queue()
        self._sent_changed = asyncio.Condition()

    async def accept(self):
        self.accepted = True

    def push(self, message):
        consumed = asyncio.get_running_loop().create_future()
        self._incoming.put_nowait((message, consumed))
        return consumed

    def disconnect(self):
        return self.push(None)

    async def receive_text(self):
        message, consumed = await self._incoming.get()
        if not consumed.done():
            consumed.set_result(None)
        if message is None:
            raise WebSocketDisconnect()
        return json.dumps(message)

    async def send_json(self, message):
        async with self._sent_changed:
            self.sent.append(message)
            self._sent_changed.notify_all()

    # Release qualification can run while large archives are being unpacked;
    # test gateway behavior instead of failing on transient scheduler latency.
    async def wait_for_type(self, event_type, *, request_id="", timeout=5):
        async def wait():
            async with self._sent_changed:
                while True:
                    for message in self.sent:
                        if message.get("type") != event_type:
                            continue
                        if request_id and message.get("request_id") != request_id:
                            continue
                        return message
                    await self._sent_changed.wait()

        return await asyncio.wait_for(wait(), timeout=timeout)


class FailingSendWebSocket(QueuedWebSocket):
    def __init__(self, *, fail_on_type):
        super().__init__()
        self.fail_on_type = fail_on_type
        self.send_failed = asyncio.Event()

    async def send_json(self, message):
        if message.get("type") == self.fail_on_type and not self.send_failed.is_set():
            self.send_failed.set()
            raise RuntimeError("simulated websocket send failure")
        await super().send_json(message)


class ConversationGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = ConversationStore(root / "conversations.sqlite3")
        self.run_store = AgentRunStore(root / "runs.sqlite3")

    async def asyncTearDown(self):
        if hasattr(self, "hub"):
            await self.hub.shutdown()
        self.run_store.close()
        self.temp.cleanup()

    def make_gateway(
        self,
        agent,
        *,
        stall_harness=None,
        authorize=None,
        voice_turn_registry=None,
    ):
        self.hub = ConversationHub(self.store, self.run_store)
        runtime = SimpleNamespace(
            agent=agent,
            conversation_store=self.store,
            conversation_hub=self.hub,
            registry=SimpleNamespace(count=7),
            llm=SimpleNamespace(model="unit-model"),
        )
        return ConversationWebSocketGateway(
            runtime,
            stall_harness=stall_harness,
            authorize=authorize,
            voice_turn_registry=voice_turn_registry,
        )

    async def test_runtime_access_is_checked_before_websocket_accept(self):
        checks = []

        async def deny(socket, scope):
            checks.append((socket.accepted, scope))
            return False

        gateway = self.make_gateway(FakeAgent(), authorize=deny)
        socket = FakeWebSocket([])

        await gateway.serve(socket)

        self.assertEqual(checks, [(False, "conversation")])
        self.assertFalse(socket.accepted)
        self.assertEqual(self.store.stats()["events"], 0)

    async def test_runtime_access_expiry_closes_an_idle_authorized_socket(self):
        async def allow(socket, scope):
            socket.scope["javis.runtime_access"] = {
                "scope": scope,
                "deadline_monotonic": asyncio.get_running_loop().time() + 0.01,
            }
            return True

        gateway = self.make_gateway(FakeAgent(), authorize=allow)
        socket = FakeWebSocket([])

        await gateway.serve(socket)

        self.assertTrue(socket.accepted)
        self.assertEqual(socket.closed, [(4401, "capability_expired")])
        self.assertEqual(self.store.stats()["events"], 0)

    @staticmethod
    def canonical_message(request_id, text, *, session_id="stall-session"):
        return {
            "type": "conversation.message",
            "payload": {
                "session_id": session_id,
                "request_id": request_id,
                "idempotency_key": request_id,
                "text": text,
                "interaction_mode": "live",
                "protocol_version": 2,
            },
        }

    @staticmethod
    def canonical_cancel(request_id, *, session_id="stall-session"):
        return {
            "type": "conversation.cancel",
            "payload": {
                "session_id": session_id,
                "request_id": request_id,
                "reason": "watchdog timeout",
                "protocol_version": 2,
            },
        }

    @staticmethod
    def request_events(socket, request_id):
        return [
            message["type"]
            for message in socket.sent
            if message.get("request_id") == request_id
            and message.get("type", "").startswith(("request.", "response."))
        ]

    async def test_canonical_gateway_attaches_and_persists_authoritative_history(self):
        gateway = self.make_gateway(FakeAgent())
        ws = FakeWebSocket([
            {
                "type": "conversation.attach",
                "payload": {"session_id": "s1", "protocol_version": 2},
            },
            {
                "type": "conversation.message",
                "payload": {
                    "session_id": "s1",
                    "request_id": "r1",
                    "idempotency_key": "m1",
                    "text": "hello",
                    "interaction_mode": "code",
                    "protocol_version": 2,
                },
            },
        ])

        await asyncio.wait_for(gateway.serve(ws), timeout=3)

        event_types = [message.get("type") for message in ws.sent]
        sequences = [
            message["sequence"]
            for message in ws.sent
            if isinstance(message.get("sequence"), int)
        ]
        history = self.store.history("s1")
        self.assertTrue(ws.accepted)
        self.assertIn("conversation.attached", event_types)
        self.assertIn("activity.understanding", event_types)
        self.assertIn("response.delta", event_types)
        self.assertIn("request.completed", event_types)
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertEqual(history[-1]["content"], "reply:hello")

    async def test_verified_voice_reference_is_consumed_by_atomic_acceptance(self):
        registry = VoiceTurnRegistry("boot-1")
        reference = registry.register(
            session_id="s1",
            owner_generation=4,
            voice_sequence=9,
            voice_turn=2,
            transcript="hello from microphone",
        )
        gateway = self.make_gateway(
            FakeAgent(),
            voice_turn_registry=registry,
        )
        ws = FakeWebSocket([
            {
                "type": "conversation.message",
                "payload": {
                    "session_id": "s1",
                    "request_id": "voice-r1",
                    "idempotency_key": "voice-r1",
                    "text": "hello from microphone",
                    "interaction_mode": "live",
                    "protocol_version": 2,
                    "voice_provenance": reference,
                },
            }
        ])

        await asyncio.wait_for(gateway.serve(ws), timeout=3)

        accepted = next(
            event for event in self.store.events_after("s1")
            if event["type"] == "request.accepted"
        )
        provenance = accepted["payload"]["input_provenance"]
        self.assertEqual(provenance["modality"], "voice")
        self.assertEqual(provenance["verification"], "server_verified")
        self.assertEqual(provenance["voice_sequence"], 9)
        self.assertEqual(registry.stats()["entries"], 0)

    async def test_two_canonical_subscribers_receive_same_session_events(self):
        gateway = self.make_gateway(FakeAgent())
        observer = FakeWebSocket([{
            "type": "conversation.attach",
            "payload": {"session_id": "s1", "protocol_version": 2},
        }])
        observer_task = asyncio.create_task(gateway.serve(observer))
        await asyncio.wait_for(observer.attached.wait(), timeout=2)
        sender = FakeWebSocket([
            {
                "type": "conversation.attach",
                "payload": {"session_id": "s1", "protocol_version": 2},
            },
            {
                "type": "conversation.message",
                "payload": {
                    "session_id": "s1",
                    "request_id": "r1",
                    "text": "shared",
                    "protocol_version": 2,
                },
            },
        ])

        await asyncio.wait_for(asyncio.gather(gateway.serve(sender), observer_task), timeout=3)

        sender_events = [m["type"] for m in sender.sent if m["type"].startswith(("activity.", "response.", "request."))]
        observer_events = [m["type"] for m in observer.sent if m["type"].startswith(("activity.", "response.", "request."))]
        self.assertEqual(observer_events, sender_events)

    async def test_legacy_message_receives_legacy_projection(self):
        gateway = self.make_gateway(FakeAgent())
        ws = FakeWebSocket([{
            "type": "message",
            "payload": {"session_id": "s1", "request_id": "r1", "text": "hello"},
        }])

        await asyncio.wait_for(gateway.serve(ws), timeout=3)

        self.assertIn("thinking", [message.get("type") for message in ws.sent])
        self.assertIn("text_delta", [message.get("type") for message in ws.sent])
        self.assertIn("done", [message.get("type") for message in ws.sent])

    async def test_cancel_command_interrupts_running_request_without_late_text(self):
        agent = SlowAgent()
        gateway = self.make_gateway(agent)
        ws = FakeWebSocket([
            {
                "type": "conversation.attach",
                "payload": {"session_id": "s1", "protocol_version": 2},
            },
            {
                "type": "conversation.message",
                "payload": {
                    "session_id": "s1",
                    "request_id": "r1",
                    "text": "wait",
                    "protocol_version": 2,
                },
            },
            {
                "type": "conversation.cancel",
                "payload": {
                    "session_id": "s1",
                    "request_id": "r1",
                    "reason": "user interrupt",
                    "protocol_version": 2,
                },
            },
        ])

        await asyncio.wait_for(gateway.serve(ws), timeout=3)

        self.assertIn("request.cancelled", [message.get("type") for message in ws.sent])
        self.assertNotIn("response.delta", [message.get("type") for message in ws.sent])

    async def test_stall_no_ack_keeps_socket_open_until_cancel_without_calling_agent(self):
        agent = CountingAgent()
        gateway = self.make_gateway(agent, stall_harness=ConversationStallHarness(enabled=True))
        ws = QueuedWebSocket()
        task = asyncio.create_task(gateway.serve(ws))

        consumed = ws.push(self.canonical_message("stall-no-ack", NO_ACK_TRIGGER))
        await asyncio.wait_for(consumed, timeout=1)
        await asyncio.sleep(0)

        self.assertEqual(self.request_events(ws, "stall-no-ack"), [])
        self.assertEqual(self.hub.stats()["active_requests"], 0)
        self.assertEqual(agent.calls, 0)

        cancelled = ws.push(self.canonical_cancel("stall-no-ack"))
        await asyncio.wait_for(cancelled, timeout=1)
        await asyncio.sleep(0)
        self.assertFalse(any(message.get("type") == "protocol.error" for message in ws.sent))

        ws.disconnect()
        await asyncio.wait_for(task, timeout=1)

    async def test_stall_after_ack_waits_for_cancel_without_delta_or_agent_call(self):
        agent = CountingAgent()
        gateway = self.make_gateway(agent, stall_harness=ConversationStallHarness(enabled=True))
        ws = QueuedWebSocket()
        task = asyncio.create_task(gateway.serve(ws))

        ws.push(self.canonical_message("stall-after-ack", AFTER_ACK_TRIGGER))
        await ws.wait_for_type("request.accepted", request_id="stall-after-ack")

        self.assertEqual(self.request_events(ws, "stall-after-ack"), ["request.accepted"])
        self.assertEqual(self.hub.stats()["active_requests"], 1)
        self.assertEqual(agent.calls, 0)

        ws.push(self.canonical_cancel("stall-after-ack"))
        await ws.wait_for_type("request.cancelled", request_id="stall-after-ack")
        ws.disconnect()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(self.hub.stats()["active_requests"], 0)

    async def test_stall_after_delta_waits_for_cancel_without_terminal_or_agent_call(self):
        agent = CountingAgent()
        gateway = self.make_gateway(agent, stall_harness=ConversationStallHarness(enabled=True))
        ws = QueuedWebSocket()
        task = asyncio.create_task(gateway.serve(ws))

        ws.push(self.canonical_message("stall-after-delta", AFTER_DELTA_TRIGGER))
        await ws.wait_for_type("response.delta", request_id="stall-after-delta")

        self.assertEqual(
            self.request_events(ws, "stall-after-delta"),
            ["request.accepted", "response.delta"],
        )
        self.assertEqual(self.hub.stats()["active_requests"], 1)
        self.assertEqual(agent.calls, 0)

        ws.push(self.canonical_cancel("stall-after-delta"))
        await ws.wait_for_type("request.cancelled", request_id="stall-after-delta")
        ws.disconnect()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(self.hub.stats()["active_requests"], 0)

    async def test_stalled_request_is_cancelled_and_drained_when_socket_disconnects(self):
        agent = CountingAgent()
        gateway = self.make_gateway(agent, stall_harness=ConversationStallHarness(enabled=True))
        ws = QueuedWebSocket()
        task = asyncio.create_task(gateway.serve(ws))

        ws.push(self.canonical_message("stall-disconnect", AFTER_DELTA_TRIGGER))
        await ws.wait_for_type("response.delta", request_id="stall-disconnect")
        ws.disconnect()
        await asyncio.wait_for(task, timeout=1)

        terminal = await self.hub.wait_for_terminal("stall-disconnect", timeout=1)
        self.assertEqual(terminal["type"], "request.cancelled")
        self.assertEqual(self.hub.stats()["active_requests"], 0)
        self.assertEqual(self.hub.stats()["subscribers"], 0)
        self.assertEqual(agent.calls, 0)

    async def test_sender_failure_and_disconnect_still_release_subscription_and_stall(self):
        agent = CountingAgent()
        gateway = self.make_gateway(agent, stall_harness=ConversationStallHarness(enabled=True))
        ws = FailingSendWebSocket(fail_on_type="response.delta")
        task = asyncio.create_task(gateway.serve(ws))

        ws.push(self.canonical_message("stall-send-failure", AFTER_DELTA_TRIGGER))
        await asyncio.wait_for(ws.send_failed.wait(), timeout=1)
        ws.disconnect()

        with self.assertRaisesRegex(RuntimeError, "simulated websocket send failure"):
            await asyncio.wait_for(task, timeout=1)

        terminal = await self.hub.wait_for_terminal("stall-send-failure", timeout=1)
        self.assertEqual(terminal["type"], "request.cancelled")
        self.assertEqual(self.hub.stats()["active_requests"], 0)
        self.assertEqual(self.hub.stats()["subscribers"], 0)
        self.assertEqual(agent.calls, 0)

    async def test_remote_client_cannot_activate_stall_trigger(self):
        agent = CountingAgent()
        gateway = self.make_gateway(agent, stall_harness=ConversationStallHarness(enabled=True))
        ws = QueuedWebSocket(host="192.0.2.10")
        task = asyncio.create_task(gateway.serve(ws))

        ws.push(self.canonical_message("remote-trigger", AFTER_DELTA_TRIGGER))
        await ws.wait_for_type("request.completed", request_id="remote-trigger")
        ws.disconnect()
        await asyncio.wait_for(task, timeout=1)

        self.assertEqual(agent.calls, 1)
        self.assertIn("response.delta", self.request_events(ws, "remote-trigger"))


if __name__ == "__main__":
    unittest.main()
