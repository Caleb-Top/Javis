import asyncio
import unittest

from core.agent import Agent
from core.cancellation import CancellationToken, RequestCancelled
from core.llm_client import LLMResponse
from core.tool_result import ToolResult


class EmptyTools:
    def get_schemas(self):
        return []

    def get(self, _name):
        return None


class InterruptibleAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_cards_replace_unrelated_agent_window(self):
        agent = Agent(object(), EmptyTools())
        agent.state.messages = [{"role": "user", "content": "other session"}]
        cards = [
            {"role": "user", "text": "my name is Eric"},
            {"role": "assistant", "content": "I will remember that"},
            {
                "role": "assistant",
                "text": "obsolete partial answer",
                "status": "interrupted",
            },
        ]

        messages = agent._conversation_history(cards)

        self.assertEqual(messages[0]["content"], "my name is Eric")
        self.assertNotIn("other session", [item["content"] for item in messages])
        self.assertNotIn("obsolete partial answer", [item["content"] for item in messages])

    async def test_cancel_during_llm_wait_emits_no_answer(self):
        started = asyncio.Event()

        class SlowLLM:
            async def chat_with_tools(self, *args, **kwargs):
                started.set()
                await asyncio.Event().wait()

        agent = Agent(SlowLLM(), EmptyTools())
        agent.max_retries = 0
        token = CancellationToken()
        events = []

        async def collect():
            async for event in agent.chat(
                "perform a long integration task",
                session_id="session-a",
                conversation_cards=[],
                cancellation=token,
            ):
                events.append(event)

        task = asyncio.create_task(collect())
        await asyncio.wait_for(started.wait(), timeout=2)
        token.cancel("replacement request")
        with self.assertRaises(RequestCancelled):
            await asyncio.wait_for(task, timeout=2)

        self.assertFalse(any(event.get("type") == "text_delta" for event in events))
        self.assertTrue(
            any(
                event.get("type") == "activity"
                and event.get("activity") == "understanding"
                for event in events
            )
        )

    async def test_cancel_during_tool_waits_for_safe_boundary_then_stops(self):
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()

        class ToolCallingLLM:
            def __init__(self):
                self.calls = 0

            async def chat_with_tools(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(
                        tool_calls=[
                            {"id": "one", "name": "system_info", "params": {"step": 1}},
                            {"id": "two", "name": "system_info", "params": {"step": 2}},
                        ]
                    )
                return LLMResponse(text="should not be reached")

        class SlowTools(EmptyTools):
            def __init__(self):
                self.calls = []

            async def execute(self, name, params, **kwargs):
                self.calls.append((name, params))
                tool_started.set()
                await release_tool.wait()
                return ToolResult.success("finished")

        tools = SlowTools()
        agent = Agent(ToolCallingLLM(), tools)
        token = CancellationToken()
        events = []

        async def collect():
            async for event in agent.chat(
                "inspect the system twice",
                session_id="session-a",
                conversation_cards=[],
                cancellation=token,
            ):
                events.append(event)

        task = asyncio.create_task(collect())
        await asyncio.wait_for(tool_started.wait(), timeout=2)
        token.cancel("voice barge-in")
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release_tool.set()
        with self.assertRaises(RequestCancelled):
            await asyncio.wait_for(task, timeout=2)

        self.assertEqual(len(tools.calls), 1)
        self.assertTrue(any(event.get("type") == "tool_result" for event in events))
        self.assertFalse(any(event.get("type") == "text_delta" for event in events))

    async def test_activity_events_do_not_expose_reasoning_content(self):
        class ReasoningLLM:
            async def chat_with_tools(self, *args, **kwargs):
                return LLMResponse(
                    text="public answer",
                    reasoning_content="private chain of thought",
                )

        agent = Agent(ReasoningLLM(), EmptyTools())
        events = [
            event
            async for event in agent.chat(
                "answer this uncommon architecture question",
                session_id="session-a",
                conversation_cards=[],
                cancellation=CancellationToken(),
            )
        ]

        self.assertTrue(any(event.get("type") == "activity" for event in events))
        self.assertFalse(any(event.get("type") == "thinking" for event in events))
        self.assertFalse(any("reasoning_content" in event for event in events))
        self.assertNotIn("private chain of thought", repr(events))

    async def test_unresolved_tool_failure_marks_done_as_unsuccessful(self):
        class FailingThenAnsweringLLM:
            def __init__(self):
                self.calls = 0

            async def chat_with_tools(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(
                        tool_calls=[
                            {"id": "one", "name": "save_note", "params": {"text": "x"}},
                        ]
                    )
                return LLMResponse(text="Saved successfully")

        class FailingTools(EmptyTools):
            async def execute(self, name, params, **kwargs):
                return ToolResult.failure("save failed")

        agent = Agent(FailingThenAnsweringLLM(), FailingTools())
        events = [
            event
            async for event in agent.chat(
                "save this note",
                session_id="session-a",
                conversation_cards=[],
                cancellation=CancellationToken(),
            )
        ]

        self.assertTrue(any(event.get("activity") == "fallback" for event in events))
        visible = "".join(
            str(event.get("text") or "")
            for event in events
            if event.get("type") == "text_delta"
        )
        self.assertNotIn("Saved successfully", visible)
        self.assertIn("\u4efb\u52a1\u672a\u5b8c\u6210", visible)
        self.assertIs(events[-1].get("success"), False)

    async def test_later_successful_tool_resolves_an_earlier_failure(self):
        class RecoveryLLM:
            def __init__(self):
                self.calls = 0

            async def chat_with_tools(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(
                        tool_calls=[
                            {"id": "one", "name": "primary_save", "params": {}},
                            {"id": "two", "name": "fallback_save", "params": {}},
                        ]
                    )
                return LLMResponse(text="Saved with the fallback")

        class RecoveryTools(EmptyTools):
            def __init__(self):
                self.calls = 0

            async def execute(self, name, params, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return ToolResult.failure("primary failed")
                return ToolResult.success("saved")

        agent = Agent(RecoveryLLM(), RecoveryTools())
        events = [
            event
            async for event in agent.chat(
                "save this note with a fallback",
                session_id="session-a",
                conversation_cards=[],
                cancellation=CancellationToken(),
            )
        ]

        visible = "".join(
            str(event.get("text") or "")
            for event in events
            if event.get("type") == "text_delta"
        )
        self.assertIn("Saved with the fallback", visible)
        self.assertIs(events[-1].get("success"), True)


if __name__ == "__main__":
    unittest.main()
