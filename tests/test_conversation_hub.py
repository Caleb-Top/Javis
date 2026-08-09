import asyncio
import tempfile
import unittest
from pathlib import Path

from core.agent_run_recorder import AgentRunRecorder
from core.agent_runs import AgentRunStore
from core.cancellation import CancellationToken, RequestCancelled
from core.conversation_hub import ConversationHub, ConversationRequest
from core.conversation_store import ConversationStore


class ConversationHubTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = ConversationStore(root / "conversations.sqlite3")
        self.run_store = AgentRunStore(root / "runs.sqlite3")
        self.confirmations = []
        self.approval_gate = asyncio.Event()

        def resolve_confirmation(confirmed: bool) -> None:
            self.confirmations.append(bool(confirmed))
            self.approval_gate.set()

        self.hub = ConversationHub(
            self.store,
            self.run_store,
            resolve_confirmation=resolve_confirmation,
        )

    async def asyncTearDown(self):
        await self.hub.shutdown()
        self.run_store.close()
        self.temp.cleanup()

    async def test_replacement_cancels_old_request_and_rejects_late_delta(self):
        release = asyncio.Event()

        async def runner(request, token):
            yield {"type": "thinking", "content": "understanding"}
            if request.request_id == "request-1":
                await release.wait()
                await token.checkpoint()
            yield {"type": "text_delta", "text": request.text}
            yield {"type": "done"}

        await self.hub.submit(
            ConversationRequest("session-1", "request-1", "old", "live", "message-1"),
            runner,
        )
        await self.hub.submit(
            ConversationRequest("session-1", "request-2", "new", "code", "message-2"),
            runner,
        )
        release.set()
        first_terminal = await self.hub.wait_for_terminal("request-1")
        second_terminal = await self.hub.wait_for_terminal("request-2")
        events = self.store.events_after("session-1")
        history = self.store.history("session-1", include_interrupted=True)

        self.assertEqual(first_terminal["type"], "request.cancelled")
        self.assertEqual(second_terminal["type"], "request.completed")
        self.assertTrue(
            any(
                event["type"] == "request.cancellation_pending"
                and event["request_id"] == "request-1"
                for event in events
            )
        )
        cancellation_sequence = next(
            event["sequence"]
            for event in events
            if event["type"] == "request.cancellation_pending"
            and event["request_id"] == "request-1"
        )
        replacement_sequence = next(
            event["sequence"]
            for event in events
            if event["type"] == "request.accepted"
            and event["request_id"] == "request-2"
        )
        self.assertLess(cancellation_sequence, replacement_sequence)
        self.assertFalse(
            any(
                event["type"] == "response.delta"
                and event["request_id"] == "request-1"
                for event in events
            )
        )
        self.assertEqual(
            [item["content"] for item in history],
            ["old", "new", "new"],
        )

    async def test_subscribers_receive_ordered_events_and_can_replay_after_cursor(self):
        async def runner(request, token):
            await token.checkpoint()
            yield {"type": "activity", "activity": "planning", "detail": "two steps"}
            yield {"type": "text_delta", "text": "answer"}
            yield {"type": "done"}

        live = await self.hub.attach("session-1")
        code = await self.hub.attach("session-1")
        await self.hub.submit(
            ConversationRequest("session-1", "request-1", "question", "live", "message-1"),
            runner,
        )
        await self.hub.wait_for_terminal("request-1")

        live_events = [await live.get() for _ in range(4)]
        code_events = [await code.get() for _ in range(4)]
        replay = await self.hub.attach("session-1", after_sequence=2)

        self.assertEqual(
            [event["type"] for event in live_events],
            [
                "request.accepted",
                "activity.planning",
                "response.delta",
                "request.completed",
            ],
        )
        self.assertEqual(live_events, code_events)
        self.assertEqual(
            [event["sequence"] for event in replay.replay],
            [3, 4],
        )
        await live.close()
        await code.close()
        await replay.close()

    async def test_duplicate_idempotency_key_runs_only_once(self):
        calls = []

        async def runner(request, token):
            calls.append(request.request_id)
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done"}

        first = await self.hub.submit(
            ConversationRequest("session-1", "request-1", "hello", "live", "message-1"),
            runner,
        )
        duplicate = await self.hub.submit(
            ConversationRequest("session-1", "request-2", "hello", "code", "message-1"),
            runner,
        )
        await self.hub.wait_for_terminal("request-1")

        self.assertTrue(first["accepted"])
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["request_id"], "request-1")
        self.assertEqual(calls, ["request-1"])

    async def test_approval_must_match_session_request_and_approval_id(self):
        async def runner(request, token):
            yield {
                "type": "tool_start",
                "tool": "write_file",
                "params": {"path": "note.txt"},
            }
            yield {
                "type": "confirm_required",
                "tool": "write_file",
                "reason": "write note",
                "params": {"path": "note.txt"},
            }
            await self.approval_gate.wait()
            await token.checkpoint()
            yield {"type": "tool_result", "tool": "write_file", "success": True, "data": "ok"}
            yield {"type": "done"}

        subscription = await self.hub.attach("session-1")
        await self.hub.submit(
            ConversationRequest("session-1", "request-1", "save note", "code", "message-1"),
            runner,
        )
        approval_event = None
        while approval_event is None:
            event = await asyncio.wait_for(subscription.get(), timeout=2)
            if event["type"] == "approval.required":
                approval_event = event
        approval_id = approval_event["payload"]["approval_id"]

        wrong_session = await self.hub.confirm(
            "session-2", "request-1", approval_id, True
        )
        wrong_request = await self.hub.confirm(
            "session-1", "request-2", approval_id, True
        )
        wrong_approval = await self.hub.confirm(
            "session-1", "request-1", "missing", True
        )
        accepted = await self.hub.confirm(
            "session-1", "request-1", approval_id, True
        )
        terminal = await self.hub.wait_for_terminal("request-1")

        self.assertFalse(wrong_session)
        self.assertFalse(wrong_request)
        self.assertFalse(wrong_approval)
        self.assertTrue(accepted)
        self.assertEqual(self.confirmations, [True])
        self.assertEqual(terminal["type"], "request.completed")
        await subscription.close()

    async def test_explicit_cancel_marks_durable_run_graph_cancelled(self):
        release = asyncio.Event()

        async def runner(request, token):
            yield {"type": "tool_start", "tool": "wait", "params": {}}
            await release.wait()
            await token.checkpoint()

        await self.hub.submit(
            ConversationRequest("session-1", "request-1", "wait", "live", "message-1"),
            runner,
        )
        cancelled = await self.hub.cancel("session-1", "request-1", reason="user stop")
        release.set()
        terminal = await self.hub.wait_for_terminal("request-1")
        run = self.run_store.list_runs(limit=1)[0]
        graph = self.run_store.get_run(run["id"], include_graph=True)

        self.assertTrue(cancelled)
        self.assertEqual(terminal["type"], "request.cancelled")
        self.assertEqual(graph["status"], "cancelled")
        self.assertTrue(all(task["status"] == "cancelled" for task in graph["tasks"]))
        self.assertTrue(
            all(
                step["status"] == "cancelled"
                for task in graph["tasks"]
                for step in task["steps"]
            )
        )

    async def test_cancellation_token_interrupts_cancel_aware_await(self):
        token = CancellationToken()
        operation = asyncio.create_task(token.race(asyncio.sleep(30)))
        await asyncio.sleep(0)
        token.cancel("replacement")

        with self.assertRaises(RequestCancelled) as captured:
            await asyncio.wait_for(operation, timeout=1)

        self.assertTrue(token.cancelled)
        self.assertEqual(captured.exception.reason, "replacement")

    async def test_cancelled_request_records_in_flight_tool_completion_before_stopping(self):
        release = asyncio.Event()
        tool_started = asyncio.Event()

        async def runner(request, token):
            yield {"type": "tool_start", "tool": "system_info", "params": {}}
            tool_started.set()
            await release.wait()
            yield {
                "type": "tool_result",
                "tool": "system_info",
                "success": True,
                "data": "finished safely",
            }
            await token.checkpoint()

        await self.hub.submit(
            ConversationRequest("session-1", "request-1", "inspect", "live", "message-1"),
            runner,
        )
        await asyncio.wait_for(tool_started.wait(), timeout=2)
        await self.hub.cancel("session-1", "request-1", reason="voice barge-in")
        release.set()
        terminal = await self.hub.wait_for_terminal("request-1")
        events = self.store.events_after("session-1")

        completed = next(
            event for event in events if event["type"] == "activity.tool_completed"
        )
        cancelled = next(event for event in events if event["type"] == "request.cancelled")
        self.assertTrue(completed["payload"]["success"])
        self.assertLess(completed["sequence"], cancelled["sequence"])

    async def test_shutdown_does_not_cancel_requests_that_are_already_terminal(self):
        async def runner(request, token):
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "done"}

        await self.hub.submit(
            ConversationRequest("session-1", "request-1", "hello", "live", "message-1"),
            runner,
        )
        await self.hub.wait_for_terminal("request-1")
        before = self.store.events_after("session-1")

        await self.hub.shutdown()
        await asyncio.sleep(0)
        after = self.store.events_after("session-1")

        self.assertEqual(after, before)

    async def test_unsuccessful_done_event_becomes_request_failed(self):
        async def runner(request, token):
            yield {
                "type": "tool_result",
                "tool": "save_note",
                "success": False,
                "data": "save failed",
            }
            yield {"type": "done", "success": False, "detail": "fallback exhausted"}

        await self.hub.submit(
            ConversationRequest(
                "session-1",
                "request-failed",
                "save",
                "live",
                "key-failed",
            ),
            runner,
        )
        terminal = await self.hub.wait_for_terminal("request-failed")
        runs = self.run_store.list_runs(limit=5)

        self.assertEqual(terminal["type"], "request.failed")
        self.assertEqual(terminal["payload"]["error"], "fallback exhausted")
        self.assertEqual(runs[0]["status"], "failed")

    async def test_actionable_model_failure_fields_survive_the_hub_terminal_event(self):
        async def runner(request, token):
            yield {
                "type": "error",
                "code": "model_setup_required",
                "message": "select a model",
                "recovery_action": "open_model_settings",
                "route": "live",
                "reason": "selected_local_model_not_installed",
                "base_url": "http://secret@example.invalid/v1",
            }

        await self.hub.submit(
            ConversationRequest(
                "session-1",
                "request-model-setup",
                "hello",
                "live",
                "key-model-setup",
            ),
            runner,
        )
        terminal = await self.hub.wait_for_terminal("request-model-setup")

        self.assertEqual(terminal["type"], "request.failed")
        self.assertEqual(
            terminal["payload"],
            {
                "error": "select a model",
                "code": "model_setup_required",
                "recovery_action": "open_model_settings",
                "route": "live",
                "reason": "selected_local_model_not_installed",
            },
        )

    def test_recorder_cancel_is_terminal_and_cannot_be_overwritten_by_done(self):
        recorder = AgentRunRecorder(
            self.run_store,
            "cancel recorder",
            session_id="session-1",
        )
        recorder.record({"type": "thinking", "content": "working"})
        recorder.record({"type": "tool_start", "tool": "write_file", "params": {}})
        recorder.record(
            {
                "type": "confirm_required",
                "tool": "write_file",
                "reason": "confirm",
                "params": {},
            }
        )
        recorder.cancel("replacement")
        recorder.record({"type": "done"})
        graph = self.run_store.get_run(recorder.run_id, include_graph=True)

        self.assertEqual(graph["status"], "cancelled")
        self.assertEqual(graph["approvals"][0]["status"], "cancelled")
        self.assertTrue(all(task["status"] == "cancelled" for task in graph["tasks"]))


if __name__ == "__main__":
    unittest.main()
