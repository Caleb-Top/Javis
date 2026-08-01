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
from gateway.conversation_ws import ConversationWebSocketGateway


class FakeWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)
        self.accepted = False
        self.sent = []
        self.attached = asyncio.Event()
        self.terminal = asyncio.Event()

    async def accept(self):
        self.accepted = True

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

    def make_gateway(self, agent):
        self.hub = ConversationHub(self.store, self.run_store)
        runtime = SimpleNamespace(
            agent=agent,
            conversation_store=self.store,
            conversation_hub=self.hub,
            registry=SimpleNamespace(count=7),
            llm=SimpleNamespace(model="unit-model"),
        )
        return ConversationWebSocketGateway(runtime)

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


if __name__ == "__main__":
    unittest.main()
