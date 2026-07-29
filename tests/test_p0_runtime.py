import asyncio
import importlib
import json
import gc
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import patch
import re

from core.llm_client import LLMClient
from core.tool_registry import ToolDef, ToolRegistry
from core.tool_result import ToolResult


async def _collect_async(stream):
    items = []
    async for item in stream:
        items.append(item)
    return items


class P0RuntimeTests(unittest.TestCase):
    def test_cloud_provider_without_api_key_does_not_crash_at_startup(self):
        os.environ.pop("DEEPSEEK_API_KEY", None)

        import core.llm_client as llm_module

        original_loader = llm_module.lcfg
        llm_module.lcfg = lambda: {
            "model": {
                "provider": "deepseek",
                "name": "deepseek-chat",
                "deepseek": {
                    "name": "deepseek-chat",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "",
                },
            }
        }
        try:
            client = LLMClient()
        finally:
            llm_module.lcfg = original_loader

        self.assertFalse(client.is_ready)
        self.assertEqual(client.provider, "deepseek")

    def test_tool_registry_blocks_dangerous_tools_when_guard_is_safe(self):
        called = False

        def delete_handler(path: str):
            nonlocal called
            called = True
            return ToolResult.success("deleted")

        registry = ToolRegistry(permission_level="safe")
        registry.register(
            ToolDef(
                "file_delete",
                "delete a file",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                delete_handler,
                "file",
            )
        )

        result = asyncio.run(registry.execute("file_delete", {"path": "important.txt"}))

        self.assertFalse(result.success)
        self.assertFalse(called)

    def test_full_skill_keeps_always_on_core_tools_registered(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        os.environ.setdefault("DEEPSEEK_API_KEY", "dummy")
        main = importlib.import_module("main")

        tools = set(main.registry.list_all())

        self.assertIn("end_turn", tools)
        self.assertIn("task_create", tools)
        self.assertIn("web_search", tools)

    def test_main_import_test_mode_suppresses_startup_side_effects(self):
        env = {
            **os.environ,
            "DEEPSEEK_API_KEY": "dummy",
            "JAVIS_TEST_MODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import main; print('imported'); print('TOOLS=' + ','.join(sorted(main.registry.list_all())))",
            ],
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("imported", output)
        self.assertIn("end_turn", output)
        self.assertIn("task_create", output)
        self.assertIn("web_search", output)
        self.assertNotIn("工具初始化", output)
        self.assertNotIn("Provider loader registered", output)
        self.assertNotIn("Skill creator initialized", output)
        self.assertNotIn("记忆控制器已启动", output)
        self.assertNotIn("Escape 中断钩子已启动", output)
        self.assertNotIn("论文知识已注入大脑", output)
        self.assertNotIn("Superpowers 技能已注入大脑", output)

    def test_runtime_module_import_has_no_boot_side_effects(self):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, "-c", "import core.runtime; print('runtime-imported')"],
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout + result.stderr
        self.assertIn("runtime-imported", output)
        self.assertNotIn("大脑加载", output)
        self.assertNotIn("工具初始化", output)

    def test_create_runtime_without_startup_side_effects_does_not_register_brain_exit_flush(self):
        script = (
            "import json, os\n"
            "os.environ.pop('JAVIS_TEST_MODE', None)\n"
            "os.environ.pop('JAVIS_DISABLE_BRAIN_AUTO_FLUSH', None)\n"
            "import knowledge.brain as brain_module\n"
            "calls=[]\n"
            "brain_module.atexit.register=lambda fn: calls.append(getattr(fn, '__name__', 'unknown'))\n"
            "from core.runtime import create_runtime\n"
            "create_runtime('.', startup_side_effects=False)\n"
            "print('ATEXIT_JSON=' + json.dumps(calls))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        marker = "ATEXIT_JSON="
        line = next(line for line in result.stdout.splitlines() if line.startswith(marker))
        self.assertNotIn("_flush", json.loads(line[len(marker):]))

    def test_create_runtime_minimal_kernel_registers_core_tools(self):
        from core.runtime import create_runtime

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        tools = set(runtime.registry.list_all())

        self.assertIn("end_turn", tools)
        self.assertIn("task_create", tools)
        self.assertIn("web_search", tools)
        self.assertEqual(runtime.current_skill, "全功能")
        self.assertEqual(runtime.skill_list, [])

    def test_runtime_load_skill_preserves_always_on_tools(self):
        from core.runtime import create_runtime

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        count = runtime.load_skill("全功能")
        tools = set(runtime.registry.list_all())

        self.assertGreater(count, 0)
        self.assertIn("end_turn", tools)
        self.assertIn("task_create", tools)
        self.assertIn("web_search", tools)

    def test_event_bus_records_and_dispatches_structured_events(self):
        from core.events import EventBus

        seen = []
        bus = EventBus(max_history=3)
        bus.subscribe("memory.write", lambda event: seen.append(event))

        event = bus.publish("memory.write", {"key": "preference"}, source="test")

        self.assertEqual(event.type, "memory.write")
        self.assertEqual(event.payload["key"], "preference")
        self.assertEqual(event.source, "test")
        self.assertEqual(seen, [event])
        self.assertEqual(bus.history()[-1], event)

    def test_session_event_store_persists_event_bus_events_to_sqlite(self):
        from core.events import EventBus
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=3)
            store.attach(bus)

            event = bus.publish("perception.event", {"summary": "OCR read: Build"}, source="perception")
            rows = store.recent_events(limit=5)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], event.id)
        self.assertEqual(rows[0]["type"], "perception.event")
        self.assertEqual(rows[0]["source"], "perception")
        self.assertEqual(rows[0]["payload"]["summary"], "OCR read: Build")

    def test_runtime_registers_session_event_store_for_future_events(self):
        from core.runtime import create_runtime
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            runtime = create_runtime(Path.cwd(), startup_side_effects=False)
            store = SessionEventStore(Path(td) / "session.sqlite")
            runtime.register_event_store(store)
            runtime.event_bus.publish("memory.write", {"key": "preference"}, source="unit")

            rows = store.recent_events(limit=5)
            status = runtime.get_runtime_status()

        self.assertEqual(rows[-1]["type"], "memory.write")
        self.assertEqual(rows[-1]["payload"]["key"], "preference")
        self.assertEqual(status["event_store"]["state"], "running")
        self.assertGreaterEqual(status["event_store"]["events"], 1)

    def test_event_consolidator_creates_semantic_candidate_from_preference_event(self):
        from core.events import EventBus
        from memory.consolidation import EventMemoryConsolidator
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=3)
            store.attach(bus)
            bus.publish("user.preference", {"key": "theme", "value": "dark"}, source="unit")

            result = EventMemoryConsolidator(store).consolidate(limit=20)
            candidates = store.memory_candidates(kind="semantic")

        self.assertEqual(result["semantic"], 1)
        self.assertEqual(candidates[0]["kind"], "semantic")
        self.assertEqual(candidates[0]["status"], "candidate")
        self.assertEqual(candidates[0]["content"], "User preference: theme = dark")
        self.assertEqual(candidates[0]["evidence_count"], 1)

    def test_memory_candidate_status_transition_requires_known_status(self):
        from core.events import EventBus
        from memory.consolidation import EventMemoryConsolidator
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=3)
            store.attach(bus)
            bus.publish("user.preference", {"key": "theme", "value": "dark"}, source="unit")
            EventMemoryConsolidator(store).consolidate(limit=20)
            candidate_id = store.memory_candidates(kind="semantic")[0]["candidate_id"]

            updated = store.update_memory_candidate_status(candidate_id, "active")
            active = store.memory_candidates(kind="semantic", status="active")
            rejected = store.update_memory_candidate_status(candidate_id, "unknown")

        self.assertTrue(updated)
        self.assertEqual(active[0]["candidate_id"], candidate_id)
        self.assertFalse(rejected)

    def test_memory_recall_returns_explainable_fts_matches(self):
        from core.events import EventBus
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            bus = EventBus()
            store = SessionEventStore(Path(td) / "session.sqlite")
            store.attach(bus)
            event = bus.publish(
                "user.preference",
                {"key": "theme", "value": "holographic dark"},
                source="unit",
            )
            store.upsert_memory_candidate(
                kind="semantic",
                content="User preference: theme = holographic dark",
                evidence_ids=[event.id],
                confidence=0.8,
            )

            results = store.recall("theme holographic", limit=5)

        self.assertGreaterEqual(len(results), 2)
        self.assertEqual(results[0]["query"], "theme holographic")
        self.assertTrue(any(item["kind"] == "candidate" for item in results))
        self.assertTrue(any("matched" in item["why"].lower() for item in results))

    def test_core_memory_kernel_serves_identity_without_semantic_recall(self):
        from core.memory_kernel import CoreMemoryKernel

        with tempfile.TemporaryDirectory() as td:
            rules_dir = Path(td)
            (rules_dir / "core_identity.md").write_text(
                "# 核心身份规则\n- **主人**: Eric\n",
                encoding="utf-8",
            )
            kernel = CoreMemoryKernel(rules_dir)

            answer = kernel.answer_if_core_query("你好，你还记得我是谁吗")

        self.assertEqual(answer, "记得。你是 Eric。")

    def test_prompt_builder_always_injects_core_identity_memory(self):
        from core.prompt_builder import MemoryLayerBuilder

        prompt = MemoryLayerBuilder.build(brain=None)

        self.assertIn("当前用户叫 Eric", prompt)
        self.assertIn("不要说“还没建立正式身份关系”", prompt)

    def test_agent_identity_question_short_circuits_before_llm(self):
        from core.agent import Agent

        class FailingLLM:
            async def chat_with_tools(self, *args, **kwargs):
                raise AssertionError("identity question should not call llm")

        class EmptyTools:
            def get_schemas(self):
                return []

        agent = Agent(FailingLLM(), EmptyTools())
        events = asyncio.run(_collect_async(agent.chat("你好，你还记得我是谁吗")))

        text = "".join(e.get("text", "") for e in events if e.get("type") == "text_delta")
        self.assertIn("你是 Eric", text)
        self.assertEqual(events[-1]["type"], "done")

    def test_session_recall_summarizes_current_conversation_cards(self):
        from core.session_recall import build_session_recall_answer

        cards = [
            {"role": "user", "text": "自己写一个可视化进程，并重新跑一遍 EMNIST CNN 训练"},
            {"role": "assistant", "text": "训练跑完了，测试准确率很高。"},
            {"role": "user", "text": "现在自我检测"},
            {"role": "assistant", "text": "自检通过，风格规则还在。"},
            {"role": "user", "text": "你好，你还记得我是谁吗"},
            {"role": "assistant", "text": "记得。你是 Eric。"},
        ]

        answer = build_session_recall_answer(
            "我之前和你讨论的是这个吗？你看看这轮对话呢",
            conversation_cards=cards,
        )

        self.assertIn("不用重新训练", answer)
        self.assertIn("训练脚本", answer)
        self.assertIn("自检", answer)
        self.assertIn("Eric", answer)

    def test_agent_session_recall_short_circuits_before_llm(self):
        from core.agent import Agent

        class FailingLLM:
            async def chat_with_tools(self, *args, **kwargs):
                raise AssertionError("session recall should not call llm")

        class EmptyTools:
            def get_schemas(self):
                return []

        cards = [
            {"role": "user", "text": "重新跑 EMNIST CNN 训练，并显示进度条"},
            {"role": "assistant", "text": "已经跑完训练。"},
            {"role": "user", "text": "全局检测，感觉你退化了"},
            {"role": "assistant", "text": "自检完成。"},
        ]
        agent = Agent(FailingLLM(), EmptyTools())
        events = asyncio.run(_collect_async(agent.chat(
            "你看看这轮对话呢",
            session_id="unit",
            conversation_cards=cards,
        )))

        text = "".join(e.get("text", "") for e in events if e.get("type") == "text_delta")
        self.assertIn("训练脚本", text)
        self.assertIn("自检", text)
        self.assertEqual(events[-1]["type"], "done")

    def test_active_semantic_memory_candidate_can_sync_into_brain(self):
        from core.events import EventBus
        from core.runtime import create_runtime
        from memory.activation import ActiveMemoryApplier
        from memory.consolidation import EventMemoryConsolidator
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            runtime = create_runtime(Path.cwd(), startup_side_effects=False)
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=10)
            store.attach(bus)
            bus.publish("user.preference", {"key": "tone", "value": "brief"}, source="unit")
            EventMemoryConsolidator(store).consolidate(limit=20)
            candidate_id = store.memory_candidates(kind="semantic")[0]["candidate_id"]
            store.update_memory_candidate_status(candidate_id, "active")
            runtime.register_event_store(store)

            result = ActiveMemoryApplier(runtime).apply(limit=20)

        self.assertEqual(result["semantic"], 1)
        self.assertTrue(any(
            fact.content == "User preference: tone = brief"
            and fact.category == "memory.semantic.approved"
            for fact in runtime.brain._facts
        ))

    def test_event_consolidator_creates_procedural_candidate_from_repeated_tool_successes(self):
        from core.events import EventBus
        from memory.consolidation import EventMemoryConsolidator
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=10)
            store.attach(bus)
            for _ in range(3):
                bus.publish("tool.completed", {"tool": "web_search", "task": "research", "success": True}, source="tools")

            result = EventMemoryConsolidator(store, min_tool_successes=3).consolidate(limit=20)
            candidates = store.memory_candidates(kind="procedural")

        self.assertEqual(result["procedural"], 1)
        self.assertEqual(candidates[0]["kind"], "procedural")
        self.assertEqual(candidates[0]["content"], "Successful tool path: research -> web_search")
        self.assertEqual(candidates[0]["evidence_count"], 3)

    def test_active_procedural_candidate_materializes_local_workflow(self):
        from core.events import EventBus
        from memory.consolidation import EventMemoryConsolidator
        from memory.procedural_materializer import ProceduralMemoryMaterializer
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=10)
            store.attach(bus)
            for _ in range(3):
                bus.publish(
                    "tool.completed",
                    {
                        "tool": "web_search",
                        "task": "research",
                        "success": True,
                        "params": {"query": "local models"},
                    },
                    source="tools",
                )
            EventMemoryConsolidator(store, min_tool_successes=3).consolidate(limit=20)
            candidate_id = store.memory_candidates(kind="procedural")[0]["candidate_id"]
            store.update_memory_candidate_status(candidate_id, "active")

            result = ProceduralMemoryMaterializer(store, Path(td) / "procedural").materialize(limit=20)
            workflow_files = list((Path(td) / "procedural").glob("wft_*.json"))
            workflow = json.loads(workflow_files[0].read_text(encoding="utf-8"))

        self.assertEqual(result["procedural"], 1)
        self.assertEqual(workflow["type"], "workflow")
        self.assertEqual(workflow["source_candidate_id"], candidate_id)
        self.assertEqual(workflow["steps"][0]["tool"], "web_search")
        self.assertEqual(workflow["steps"][0]["params"]["query"], "local models")

    def test_active_procedural_candidate_materialization_versions_changed_workflow(self):
        from core.events import EventBus
        from memory.consolidation import EventMemoryConsolidator
        from memory.procedural_materializer import ProceduralMemoryMaterializer
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=10)
            store.attach(bus)
            for query in ["local models", "local models", "vision models"]:
                bus.publish(
                    "tool.completed",
                    {
                        "tool": "web_search",
                        "task": "research",
                        "success": True,
                        "params": {"query": query},
                    },
                    source="tools",
                )
            EventMemoryConsolidator(store, min_tool_successes=3).consolidate(limit=20)
            candidate_id = store.memory_candidates(kind="procedural")[0]["candidate_id"]
            store.update_memory_candidate_status(candidate_id, "active")
            output_dir = Path(td) / "procedural"
            materializer = ProceduralMemoryMaterializer(store, output_dir)

            first = materializer.materialize(limit=20)
            second = materializer.materialize(limit=20)
            files = sorted(output_dir.glob("wft_*.json"))
            workflow = json.loads(files[0].read_text(encoding="utf-8"))

        self.assertEqual(first["procedural"], 1)
        self.assertEqual(second["procedural"], 0)
        self.assertEqual(len(files), 1)
        self.assertEqual(workflow["version"], 1)
        self.assertIn("workflow_hash", workflow)

    def test_main_materializes_active_procedural_memory_api(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        task = f"research_{uuid.uuid4().hex[:8]}"
        for _ in range(3):
            main.runtime.event_bus.publish(
                "tool.completed",
                {
                    "tool": "web_search",
                    "task": task,
                    "success": True,
                    "params": {"query": "local vlm"},
                },
                source="tools",
            )
        asyncio.run(main.api_memory_consolidate({"limit": 100}))
        candidates = asyncio.run(main.api_memory_candidates(kind="procedural", limit=100))["candidates"]
        candidate_id = next(item["candidate_id"] for item in candidates if item["content"].endswith(f"{task} -> web_search"))
        asyncio.run(main.api_memory_candidate_status({"candidate_id": candidate_id, "status": "active"}))
        with tempfile.TemporaryDirectory() as td:
            result = asyncio.run(main.api_memory_materialize_procedural({
                "limit": 100,
                "output_dir": td,
            }))
            files = list(Path(td).glob("wft_*.json"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["result"]["procedural"], 1)
        self.assertEqual(len(files), 1)

    def test_evolution_engine_creates_reviewable_workflow_candidate_from_hot_tool_path(self):
        from core.events import EventBus
        from evolution.engine import EvolutionCandidateEngine
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=10)
            store.attach(bus)
            for _ in range(4):
                bus.publish(
                    "tool.completed",
                    {
                        "tool": "web_search",
                        "task": "daily_research",
                        "success": True,
                        "latency_ms": 900,
                    },
                    source="tools",
                )

            result = EvolutionCandidateEngine(store, min_successes=4).review(limit=20)
            candidates = store.evolution_candidates(kind="workflow")

        self.assertEqual(result["workflow"], 1)
        self.assertEqual(candidates[0]["status"], "candidate")
        self.assertEqual(candidates[0]["title"], "Local workflow candidate: daily_research via web_search")
        self.assertEqual(candidates[0]["evidence_count"], 4)
        self.assertEqual(candidates[0]["proposal"]["tool"], "web_search")

    def test_evolution_candidate_requires_staged_before_active(self):
        from core.events import EventBus
        from evolution.engine import EvolutionCandidateEngine
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            bus = EventBus(max_history=10)
            store.attach(bus)
            for _ in range(3):
                bus.publish("tool.completed", {"tool": "file_read", "task": "audit", "success": True}, source="tools")
            EvolutionCandidateEngine(store, min_successes=3).review(limit=20)
            candidate_id = store.evolution_candidates(kind="workflow")[0]["candidate_id"]

            direct_active = store.update_evolution_candidate_status(candidate_id, "active")
            direct_staged = store.update_evolution_candidate_status(candidate_id, "staged")
            validated = store.validate_evolution_candidate(candidate_id)
            staged = store.update_evolution_candidate_status(candidate_id, "staged")
            active = store.update_evolution_candidate_status(candidate_id, "active")

        self.assertFalse(direct_active)
        self.assertFalse(direct_staged)
        self.assertTrue(validated["ok"])
        self.assertTrue(staged)
        self.assertTrue(active)

    def test_evolution_validation_gate_rejects_dangerous_generated_code(self):
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            store.upsert_evolution_candidate(
                kind="workflow",
                title="Dangerous local workflow",
                description="candidate should not pass sandbox validation",
                evidence_ids=["evt-danger"],
                confidence=0.7,
                proposal={
                    "task": "cleanup",
                    "tool": "generated_script",
                    "language": "python",
                    "code": "import shutil\nshutil.rmtree('core')",
                },
            )
            candidate_id = store.evolution_candidates(kind="workflow")[0]["candidate_id"]

            validated = store.validate_evolution_candidate(candidate_id)
            staged = store.update_evolution_candidate_status(candidate_id, "staged")
            candidate = store.evolution_candidates(kind="workflow", status=None)[0]

        self.assertFalse(validated["ok"])
        self.assertFalse(staged)
        self.assertEqual(candidate["validation"]["status"], "failed")

    def test_active_evolution_candidate_rolls_back_when_performance_regresses(self):
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            store.upsert_evolution_candidate(
                kind="workflow",
                title="Regression watched workflow",
                description="candidate should roll back on repeated failures",
                evidence_ids=["evt-regression"],
                confidence=0.8,
                proposal={"task": "audit", "tool": "file_read"},
            )
            candidate_id = store.evolution_candidates(kind="workflow")[0]["candidate_id"]
            store.validate_evolution_candidate(candidate_id)
            store.update_evolution_candidate_status(candidate_id, "staged")
            store.update_evolution_candidate_status(candidate_id, "active")

            first = store.record_evolution_performance(candidate_id, success=False, latency_ms=50, window=3)
            second = store.record_evolution_performance(candidate_id, success=False, latency_ms=60, window=3)
            third = store.record_evolution_performance(candidate_id, success=False, latency_ms=70, window=3)
            candidate = store.evolution_candidates(kind="workflow", status=None)[0]

        self.assertFalse(first["rolled_back"])
        self.assertFalse(second["rolled_back"])
        self.assertTrue(third["rolled_back"])
        self.assertEqual(candidate["status"], "staged")

    def test_active_evolution_candidate_rolls_back_when_quality_or_latency_regresses(self):
        from memory.session_db import SessionEventStore

        with tempfile.TemporaryDirectory() as td:
            store = SessionEventStore(Path(td) / "session.sqlite")
            store.upsert_evolution_candidate(
                kind="workflow",
                title="Quality watched workflow",
                description="candidate should roll back on slow low quality runs",
                evidence_ids=["evt-quality"],
                confidence=0.8,
                proposal={"task": "audit", "tool": "file_read"},
            )
            candidate_id = store.evolution_candidates(kind="workflow")[0]["candidate_id"]
            store.validate_evolution_candidate(candidate_id)
            store.update_evolution_candidate_status(candidate_id, "staged")
            store.update_evolution_candidate_status(candidate_id, "active")

            store.record_evolution_performance(
                candidate_id,
                success=True,
                latency_ms=420,
                quality_score=0.4,
                window=3,
                max_latency_ms=300,
                min_quality_score=0.7,
            )
            store.record_evolution_performance(
                candidate_id,
                success=True,
                latency_ms=450,
                quality_score=0.45,
                window=3,
                max_latency_ms=300,
                min_quality_score=0.7,
            )
            result = store.record_evolution_performance(
                candidate_id,
                success=True,
                latency_ms=430,
                quality_score=0.5,
                window=3,
                max_latency_ms=300,
                min_quality_score=0.7,
            )
            candidate = store.evolution_candidates(kind="workflow", status=None)[0]

        self.assertTrue(result["rolled_back"])
        self.assertIn("latency", result["rollback_reasons"])
        self.assertIn("quality", result["rollback_reasons"])
        self.assertEqual(candidate["status"], "staged")

    def test_main_evolution_performance_api_rolls_back_active_candidate(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        title = f"api regression {uuid.uuid4().hex[:8]}"
        main.runtime.event_store.upsert_evolution_candidate(
            kind="workflow",
            title=title,
            description="api candidate should roll back on repeated failures",
            evidence_ids=["evt-api-regression"],
            confidence=0.8,
            proposal={"task": "api_audit", "tool": "file_read"},
        )
        candidate_id = next(
            item["candidate_id"]
            for item in main.runtime.event_store.evolution_candidates(kind="workflow", status=None)
            if item["title"] == title
        )
        asyncio.run(main.api_evolution_candidate_validate({"candidate_id": candidate_id}))
        asyncio.run(main.api_evolution_candidate_status({"candidate_id": candidate_id, "status": "staged"}))
        asyncio.run(main.api_evolution_candidate_status({"candidate_id": candidate_id, "status": "active"}))

        asyncio.run(main.api_evolution_candidate_performance({
            "candidate_id": candidate_id,
            "success": False,
            "window": 3,
        }))
        asyncio.run(main.api_evolution_candidate_performance({
            "candidate_id": candidate_id,
            "success": False,
            "window": 3,
        }))
        result = asyncio.run(main.api_evolution_candidate_performance({
            "candidate_id": candidate_id,
            "success": False,
            "window": 3,
        }))
        candidate = next(
            item
            for item in main.runtime.event_store.evolution_candidates(kind="workflow", status=None)
            if item["candidate_id"] == candidate_id
        )

        self.assertTrue(result["rolled_back"])
        self.assertEqual(candidate["status"], "staged")
        self.assertEqual(main.runtime.event_bus.history("evolution.candidate.performance")[-1].payload["rolled_back"], True)

    def test_blueprint_audit_reports_five_system_coverage_and_gaps(self):
        from blueprint.audit import BlueprintAuditor
        from control.command_tasks import CommandTaskRunner
        from core.runtime import create_runtime
        from evolution.service import EvolutionService
        from memory.session_db import SessionEventStore
        from perception.service import PerceptionService

        with tempfile.TemporaryDirectory() as td:
            runtime = create_runtime(Path.cwd(), startup_side_effects=False)
            runtime.register_event_store(SessionEventStore(Path(td) / "session.sqlite"))
            runtime.register_subsystem(PerceptionService())
            runtime.register_subsystem(EvolutionService())
            runtime.register_subsystem(CommandTaskRunner(Path.cwd()))

            report = BlueprintAuditor(runtime).coverage()

        system_ids = {item["id"] for item in report["systems"]}
        memory = next(item for item in report["systems"] if item["id"] == "memory")
        control = next(item for item in report["systems"] if item["id"] == "control")

        self.assertTrue(report["ok"])
        self.assertEqual(system_ids, {"kernel", "control", "perception", "memory", "evolution"})
        self.assertEqual(memory["state"], "running")
        self.assertIn("event store", " ".join(memory["evidence"]).lower())
        self.assertIn("command task", " ".join(control["evidence"]).lower())
        perception = next(item for item in report["systems"] if item["id"] == "perception")
        evolution = next(item for item in report["systems"] if item["id"] == "evolution")
        self.assertIn("local vlm", " ".join(perception["evidence"]).lower())
        self.assertNotIn("Local VLM adapter gates are not implemented", perception["gaps"])
        self.assertIn("rollback", " ".join(evolution["evidence"]).lower())
        self.assertNotIn("Regression tracking and automatic rollback are not implemented", evolution["gaps"])
        self.assertEqual(report["score"], 1.0)
        self.assertEqual(report["next_actions"], [])

    def test_runtime_registers_subsystems_and_emits_lifecycle_events(self):
        from core.runtime import create_runtime

        class DemoSubsystem:
            name = "demo"

            def __init__(self):
                self.started = False

            def start(self, runtime):
                self.started = runtime is not None

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        subsystem = DemoSubsystem()
        runtime.register_subsystem(subsystem)

        self.assertIs(runtime.subsystems["demo"], subsystem)
        self.assertTrue(subsystem.started)
        self.assertEqual(runtime.event_bus.history()[-1].type, "subsystem.registered")
        self.assertEqual(runtime.event_bus.history()[-1].payload["name"], "demo")

    def test_runtime_status_includes_standard_subsystem_health(self):
        from core.runtime import create_runtime
        from core.subsystem import SubsystemStatus

        class DemoSubsystem:
            name = "demo"

            def status(self):
                return SubsystemStatus(state="running", detail="ready", metrics={"events": 2})

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        runtime.register_subsystem(DemoSubsystem())
        status = runtime.get_runtime_status()

        self.assertEqual(status["subsystem_status"]["demo"]["state"], "running")
        self.assertEqual(status["subsystem_status"]["demo"]["detail"], "ready")
        self.assertEqual(status["subsystem_status"]["demo"]["metrics"]["events"], 2)

    def test_perception_service_publishes_structured_local_events(self):
        from core.runtime import create_runtime
        from perception.service import PerceptionService

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        service = PerceptionService()
        runtime.register_subsystem(service)

        event = service.ingest(
            source="screen",
            modality="vision",
            summary="Browser window with a search box",
            confidence=0.91,
            metadata={"ocr": ["Search"]},
        )
        published = runtime.event_bus.history("perception.event")[-1]

        self.assertEqual(event.source, "screen")
        self.assertEqual(event.modality, "vision")
        self.assertEqual(event.summary, "Browser window with a search box")
        self.assertEqual(published.payload["summary"], event.summary)
        self.assertEqual(published.payload["metadata"]["ocr"], ["Search"])
        self.assertEqual(service.status().metrics["events"], 1)

    def test_yolo_adapter_converts_detections_to_perception_event(self):
        from core.runtime import create_runtime
        from perception.adapters.yolo import YoloDetectionAdapter
        from perception.service import PerceptionService

        class FakeYolo:
            def detect(self, image, conf_threshold=0.25, iou_threshold=0.45):
                return [
                    {"label": "person", "confidence": 0.93, "x": 10, "y": 20, "w": 30, "h": 40},
                    {"label": "laptop", "confidence": 0.82, "x": 50, "y": 60, "w": 70, "h": 80},
                ]

            def status(self):
                return {"active_model": "yolov8n", "models": {"yolov8n": 12.3}, "training_samples": 0}

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        service = PerceptionService()
        runtime.register_subsystem(service)
        adapter = YoloDetectionAdapter(manager_factory=lambda: FakeYolo())
        service.register_adapter(adapter)

        event = adapter.detect(image=object(), perception=service, source="unit-test")

        self.assertIn("person x1", event.summary)
        self.assertIn("laptop x1", event.summary)
        self.assertEqual(event.metadata["detector"], "yolo")
        self.assertEqual(event.metadata["objects"][0]["label"], "person")
        self.assertEqual(runtime.event_bus.history("perception.event")[-1].payload["summary"], event.summary)

    def test_perception_status_reports_yolo_adapter_without_loading_model(self):
        from perception.adapters.yolo import YoloDetectionAdapter
        from perception.service import PerceptionService

        service = PerceptionService()
        service.register_adapter(YoloDetectionAdapter(manager_factory=lambda: None))

        status = service.status().to_dict()

        self.assertIn("yolo", status["metrics"]["adapters"])
        self.assertEqual(status["metrics"]["adapters"]["yolo"]["state"], "available")

    def test_perception_service_returns_registered_adapter(self):
        from perception.service import PerceptionService

        adapter = SimpleNamespace(name="demo")
        service = PerceptionService()
        service.register_adapter(adapter)

        self.assertIs(service.get_adapter("demo"), adapter)
        self.assertIsNone(service.get_adapter("missing"))

    def test_perception_service_analyzes_image_with_selected_adapters(self):
        from core.runtime import create_runtime
        from perception.service import PerceptionService

        class FakeOcrAdapter:
            name = "ocr"

            def analyze(self, image, perception, source="image"):
                return perception.ingest(
                    source=source,
                    modality="vision.ocr",
                    summary="OCR read: Launch",
                    confidence=0.88,
                    metadata={"adapter": "ocr", "text": "Launch"},
                )

        class FakeYoloAdapter:
            name = "yolo"

            def detect(self, image, perception, source="image"):
                return perception.ingest(
                    source=source,
                    modality="vision.object_detection",
                    summary="YOLO detected button x1",
                    confidence=0.76,
                    metadata={"adapter": "yolo", "objects": [{"label": "button"}]},
                )

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        service = PerceptionService()
        runtime.register_subsystem(service)
        service.register_adapter(FakeOcrAdapter())
        service.register_adapter(FakeYoloAdapter())

        events = service.analyze_image(
            image=object(),
            source="unit-image",
            adapter_names=["ocr", "yolo"],
        )

        self.assertEqual([event.modality for event in events], ["vision.ocr", "vision.object_detection"])
        self.assertEqual(events[0].metadata["text"], "Launch")
        self.assertEqual(events[1].metadata["objects"][0]["label"], "button")
        self.assertEqual(len(runtime.event_bus.history("perception.event")), 2)

    def test_video_stream_analyzer_skips_static_frames_and_emits_segment_summary(self):
        import numpy as np
        from perception.service import PerceptionService
        from perception.video import VideoStreamAnalyzer

        class FakeAdapter:
            name = "fake"

            def analyze(self, image, perception, source):
                return perception.ingest(
                    source=source,
                    modality="vision.frame",
                    summary=f"frame mean {int(image.mean())}",
                    confidence=0.9,
                    metadata={"adapter": "fake"},
                )

        service = PerceptionService()
        service.register_adapter(FakeAdapter())
        frames = [
            (0.0, np.zeros((4, 4, 3), dtype=np.uint8)),
            (0.5, np.zeros((4, 4, 3), dtype=np.uint8)),
            (1.0, np.full((4, 4, 3), 80, dtype=np.uint8)),
            (1.5, np.full((4, 4, 3), 82, dtype=np.uint8)),
        ]
        analyzer = VideoStreamAnalyzer(
            perception=service,
            change_threshold=0.1,
            segment_seconds=1.0,
        )

        result = analyzer.analyze_frames(frames, source="unit-video", adapters=["fake"])

        self.assertTrue(result["ok"])
        self.assertEqual(result["frames_seen"], 4)
        self.assertEqual(result["frames_analyzed"], 2)
        self.assertGreaterEqual(result["segments"], 1)
        self.assertTrue(any(event.modality == "vision.video_segment" for event in service.history()))

    def test_ocr_adapter_converts_reader_output_to_perception_event(self):
        from core.runtime import create_runtime
        from perception.adapters.ocr import OcrAdapter
        from perception.service import PerceptionService

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        service = PerceptionService()
        runtime.register_subsystem(service)
        adapter = OcrAdapter(reader=lambda image: [
            {"text": "Hello", "confidence": 0.9, "box": [1, 2, 3, 4]},
            {"text": "Jarvis", "confidence": 0.7, "box": [5, 6, 7, 8]},
            {"text": "noise", "confidence": 0.1, "box": [9, 10, 11, 12]},
        ])

        event = adapter.analyze(image=object(), perception=service, source="unit-ocr", min_confidence=0.5)

        self.assertEqual(event.modality, "vision.ocr")
        self.assertEqual(event.summary, "OCR read: Hello Jarvis")
        self.assertEqual(event.confidence, 0.8)
        self.assertEqual(event.metadata["text"], "Hello Jarvis")
        self.assertEqual(len(event.metadata["tokens"]), 2)

    def test_local_vlm_adapter_marks_low_confidence_for_upgrade(self):
        from perception.adapters.vlm import LocalVlmAdapter
        from perception.service import PerceptionService

        service = PerceptionService()
        adapter = LocalVlmAdapter(
            describer=lambda image, prompt: {
                "summary": "ambiguous dashboard",
                "confidence": 0.42,
                "model": "unit-vlm",
            },
            upgrade_threshold=0.6,
        )

        event = adapter.analyze(image=object(), perception=service, source="unit-vlm", prompt="describe")

        self.assertEqual(event.modality, "vision.vlm")
        self.assertEqual(event.summary, "ambiguous dashboard")
        self.assertTrue(event.metadata["needs_upgrade"])
        self.assertEqual(event.metadata["model"], "unit-vlm")

    def test_openai_compatible_vlm_describer_sends_image_payload_to_local_endpoint(self):
        from perception.adapters.vlm import OpenAICompatibleVlmDescriber

        calls = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({
                    "choices": [
                        {"message": {"content": "A dashboard with status panels."}}
                    ]
                }).encode("utf-8")

        def fake_urlopen(request, timeout):
            calls["url"] = request.full_url
            calls["timeout"] = timeout
            calls["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            describer = OpenAICompatibleVlmDescriber(
                base_url="http://localhost:11434/v1",
                model="unit-vlm",
                timeout=3,
            )
            result = describer.describe(b"fake-jpeg", "describe this")

        content = calls["payload"]["messages"][0]["content"]
        self.assertEqual(result["summary"], "A dashboard with status panels.")
        self.assertEqual(result["confidence"], 0.72)
        self.assertEqual(calls["url"], "http://localhost:11434/v1/chat/completions")
        self.assertEqual(calls["payload"]["model"], "unit-vlm")
        self.assertEqual(content[0]["text"], "describe this")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_main_mounts_vlm_adapter_gate_for_local_multimodal_path(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        adapter = main.perception_service.get_adapter("vlm")
        status = main.perception_service.status().to_dict()

        self.assertIsNotNone(adapter)
        self.assertIn("vlm", status["metrics"]["adapters"])

    def test_yolo_manager_discovers_extra_model_dirs_without_loading_model(self):
        from tools import yolo_manager

        with tempfile.TemporaryDirectory() as td:
            model_path = Path(td) / "yolov8n.onnx"
            model_path.write_bytes(b"fake")
            with patch.dict(os.environ, {"JAVIS_YOLO_DIRS": td}):
                manager = yolo_manager.YoloManager(autoload=False)

        status = manager.status()

        self.assertIn("yolov8n", status["models"])
        self.assertIsNone(status["active_model"])

    def test_yolo_adapter_status_reports_extra_model_dirs(self):
        from perception.adapters.yolo import YoloDetectionAdapter

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "unit_extra_yolo.onnx").write_bytes(b"fake")
            with patch.dict(os.environ, {"JAVIS_YOLO_DIRS": td}):
                status = YoloDetectionAdapter().status()

        self.assertIn("unit_extra_yolo", status["models"])
        self.assertGreaterEqual(status["model_count"], 1)

    def test_runtime_status_reports_kernel_events_and_subsystems(self):
        from core.runtime import create_runtime

        class DemoSubsystem:
            name = "demo"

        runtime = create_runtime(Path.cwd(), startup_side_effects=False)
        runtime.register_subsystem(DemoSubsystem())
        status = runtime.get_runtime_status()

        self.assertTrue(status["ok"])
        self.assertEqual(status["subsystems"], ["demo"])
        self.assertGreaterEqual(status["event_count"], 2)
        self.assertIn("runtime.created", status["recent_events"])
        self.assertIn("subsystem.registered", status["recent_events"])

    def test_main_exposes_runtime_status_api(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        status = asyncio.run(main.api_runtime_status())

        self.assertTrue(status["ok"])
        self.assertIn("event_count", status)
        self.assertIn("subsystems", status)

    def test_main_mounts_session_event_store_for_app_memory(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        main.runtime.event_bus.publish("unit.memory", {"value": 42}, source="unit")
        status = asyncio.run(main.api_runtime_status())
        rows = main.runtime.event_store.recent_events(event_type="unit.memory", limit=1)

        self.assertEqual(status["event_store"]["state"], "running")
        self.assertEqual(rows[-1]["payload"]["value"], 42)

    def test_main_memory_events_api_returns_persisted_timeline(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        main.runtime.event_bus.publish("unit.timeline", {"step": "first"}, source="unit")
        result = asyncio.run(main.api_memory_events(type="unit.timeline", limit=1))

        self.assertTrue(result["ok"])
        self.assertEqual(result["events"][-1]["type"], "unit.timeline")
        self.assertEqual(result["events"][-1]["payload"]["step"], "first")

    def test_main_memory_consolidation_api_returns_candidates(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        key = f"voice_{uuid.uuid4().hex[:8]}"
        content = f"User preference: {key} = quiet"
        main.runtime.event_bus.publish("user.preference", {"key": key, "value": "quiet"}, source="unit")
        consolidated = asyncio.run(main.api_memory_consolidate({"limit": 50}))
        candidates = asyncio.run(main.api_memory_candidates(kind="semantic", limit=10))

        self.assertTrue(consolidated["ok"])
        self.assertGreaterEqual(consolidated["result"]["semantic"], 1)
        self.assertTrue(candidates["ok"])
        self.assertIn(content, [item["content"] for item in candidates["candidates"]])

    def test_main_memory_candidate_status_api_activates_candidate(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        key = f"pace_{uuid.uuid4().hex[:8]}"
        content = f"User preference: {key} = fast"
        main.runtime.event_bus.publish("user.preference", {"key": key, "value": "fast"}, source="unit")
        asyncio.run(main.api_memory_consolidate({"limit": 50}))
        candidates = asyncio.run(main.api_memory_candidates(kind="semantic", limit=100))["candidates"]
        candidate_id = next(item["candidate_id"] for item in candidates if item["content"] == content)

        result = asyncio.run(main.api_memory_candidate_status({
            "candidate_id": candidate_id,
            "status": "active",
        }))
        active = asyncio.run(main.api_memory_candidates(kind="semantic", status="active", limit=20))

        self.assertTrue(result["ok"])
        self.assertIn(candidate_id, [item["candidate_id"] for item in active["candidates"]])

    def test_main_memory_apply_active_api_syncs_approved_semantic_candidate_to_brain(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        key = f"format_{uuid.uuid4().hex[:8]}"
        content = f"User preference: {key} = concise"
        main.runtime.event_bus.publish("user.preference", {"key": key, "value": "concise"}, source="unit")
        asyncio.run(main.api_memory_consolidate({"limit": 50}))
        candidates = asyncio.run(main.api_memory_candidates(kind="semantic", limit=100))["candidates"]
        candidate_id = next(item["candidate_id"] for item in candidates if item["content"] == content)
        asyncio.run(main.api_memory_candidate_status({"candidate_id": candidate_id, "status": "active"}))

        result = asyncio.run(main.api_memory_apply_active({"limit": 100}))

        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["result"]["semantic"], 1)
        self.assertTrue(any(fact.content == content for fact in main.runtime.brain._facts))

    def test_main_exposes_evolution_service_and_candidate_apis(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        task = f"daily_audit_{uuid.uuid4().hex[:8]}"
        for _ in range(3):
            main.runtime.event_bus.publish(
                "tool.completed",
                {"tool": "file_read", "task": task, "success": True, "latency_ms": 30},
                source="tools",
            )

        status = asyncio.run(main.api_runtime_status())
        reviewed = asyncio.run(main.api_evolution_review({"limit": 100}))
        candidates = asyncio.run(main.api_evolution_candidates(kind="workflow", limit=100))["candidates"]
        candidate_id = next(item["candidate_id"] for item in candidates if item["proposal"]["task"] == task)
        direct_active = asyncio.run(main.api_evolution_candidate_status({
            "candidate_id": candidate_id,
            "status": "active",
        }))
        validated = asyncio.run(main.api_evolution_candidate_validate({
            "candidate_id": candidate_id,
        }))
        staged = asyncio.run(main.api_evolution_candidate_status({
            "candidate_id": candidate_id,
            "status": "staged",
        }))

        self.assertIn("evolution", status["subsystems"])
        self.assertTrue(reviewed["ok"])
        self.assertGreaterEqual(reviewed["result"]["workflow"], 1)
        self.assertFalse(direct_active["ok"])
        self.assertTrue(validated["ok"])
        self.assertTrue(staged["ok"])

    def test_main_exposes_blueprint_coverage_api_for_app_dashboard(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        report = asyncio.run(main.api_blueprint_coverage())

        self.assertTrue(report["ok"])
        self.assertIn("score", report)
        self.assertEqual(
            {item["id"] for item in report["systems"]},
            {"kernel", "control", "perception", "memory", "evolution"},
        )
        self.assertIn("next_actions", report)

    def test_main_mounts_perception_subsystem_for_app_status(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        status = asyncio.run(main.api_runtime_status())

        self.assertIn("perception", status["subsystems"])
        self.assertEqual(status["subsystem_status"]["perception"]["state"], "running")
        self.assertIn("yolo", status["subsystem_status"]["perception"]["metrics"]["adapters"])

    def test_main_perception_ingest_api_publishes_event(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        result = asyncio.run(main.api_perception_ingest({
            "source": "screen",
            "modality": "vision",
            "summary": "Settings window is open",
            "confidence": 0.8,
            "metadata": {"ocr": ["Settings"]},
        }))
        event = main.runtime.event_bus.history("perception.event")[-1]

        self.assertTrue(result["ok"])
        self.assertEqual(result["event"]["summary"], "Settings window is open")
        self.assertEqual(event.payload["metadata"]["ocr"], ["Settings"])

    def test_main_yolo_detect_api_uses_perception_adapter(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        class FakeYoloAdapter:
            name = "yolo"

            def detect(self, image, perception, source="image", conf_threshold=0.25, iou_threshold=0.45):
                return perception.ingest(
                    source=source,
                    modality="vision.object_detection",
                    summary="YOLO detected keyboard x1",
                    confidence=0.77,
                    metadata={
                        "detector": "yolo",
                        "objects": [{"label": "keyboard", "confidence": 0.77}],
                        "thresholds": {"confidence": conf_threshold, "iou": iou_threshold},
                    },
                )

        original = main.perception_service.get_adapter("yolo")
        main.perception_service.register_adapter(FakeYoloAdapter())
        try:
            with patch.object(main, "_load_perception_image", return_value=object()):
                result = asyncio.run(main.api_perception_yolo_detect({
                    "image_path": "web/index.html",
                    "source": "unit-yolo",
                    "conf_threshold": 0.5,
                }))
        finally:
            main.perception_service.register_adapter(original)

        self.assertTrue(result["ok"])
        self.assertEqual(result["event"]["source"], "unit-yolo")
        self.assertEqual(result["event"]["metadata"]["detector"], "yolo")
        self.assertEqual(result["event"]["metadata"]["thresholds"]["confidence"], 0.5)

    def test_main_image_analyze_api_runs_local_visual_pipeline(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        class FakeOcrAdapter:
            name = "ocr"

            def analyze(self, image, perception, source="image"):
                return perception.ingest(
                    source=source,
                    modality="vision.ocr",
                    summary="OCR read: Deploy",
                    confidence=0.9,
                    metadata={"adapter": "ocr", "text": "Deploy"},
                )

        class FakeYoloAdapter:
            name = "yolo"

            def detect(self, image, perception, source="image"):
                return perception.ingest(
                    source=source,
                    modality="vision.object_detection",
                    summary="YOLO detected terminal x1",
                    confidence=0.8,
                    metadata={"adapter": "yolo", "objects": [{"label": "terminal"}]},
                )

        original_ocr = main.perception_service.get_adapter("ocr")
        original_yolo = main.perception_service.get_adapter("yolo")
        main.perception_service.register_adapter(FakeOcrAdapter())
        main.perception_service.register_adapter(FakeYoloAdapter())
        try:
            with patch.object(main, "_load_perception_image", return_value=object()):
                result = asyncio.run(main.api_perception_image_analyze({
                    "image_path": "web/index.html",
                    "source": "unit-pipeline",
                    "adapters": ["ocr", "yolo"],
                }))
        finally:
            if original_ocr is not None:
                main.perception_service.register_adapter(original_ocr)
            if original_yolo is not None:
                main.perception_service.register_adapter(original_yolo)

        self.assertTrue(result["ok"])
        self.assertEqual([event["modality"] for event in result["events"]], ["vision.ocr", "vision.object_detection"])
        self.assertEqual(result["events"][0]["metadata"]["text"], "Deploy")
        self.assertEqual(result["events"][1]["metadata"]["objects"][0]["label"], "terminal")

    def test_main_screen_analyze_api_captures_screen_into_visual_pipeline(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        class FakeOcrAdapter:
            name = "ocr"

            def analyze(self, image, perception, source="image"):
                return perception.ingest(
                    source=source,
                    modality="vision.ocr",
                    summary="OCR read: Dashboard",
                    confidence=0.91,
                    metadata={"adapter": "ocr", "text": "Dashboard"},
                )

        original_ocr = main.perception_service.get_adapter("ocr")
        main.perception_service.register_adapter(FakeOcrAdapter())
        try:
            with patch.object(main, "_capture_perception_screenshot", return_value=object()):
                result = asyncio.run(main.api_perception_screen_analyze({
                    "source": "unit-screen",
                    "adapters": ["ocr"],
                }))
        finally:
            if original_ocr is not None:
                main.perception_service.register_adapter(original_ocr)

        self.assertTrue(result["ok"])
        self.assertEqual(result["events"][0]["source"], "unit-screen")
        self.assertEqual(result["events"][0]["metadata"]["text"], "Dashboard")

    def test_main_camera_analyze_api_captures_camera_into_visual_pipeline(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        class FakeYoloAdapter:
            name = "yolo"

            def detect(self, image, perception, source="image"):
                return perception.ingest(
                    source=source,
                    modality="vision.object_detection",
                    summary="YOLO detected person x1",
                    confidence=0.86,
                    metadata={"adapter": "yolo", "objects": [{"label": "person"}]},
                )

        original_yolo = main.perception_service.get_adapter("yolo")
        main.perception_service.register_adapter(FakeYoloAdapter())
        try:
            with patch.object(main, "_capture_perception_camera", return_value=object()):
                result = asyncio.run(main.api_perception_camera_analyze({
                    "source": "unit-camera",
                    "adapters": ["yolo"],
                    "device_id": 0,
                }))
        finally:
            if original_yolo is not None:
                main.perception_service.register_adapter(original_yolo)

        self.assertTrue(result["ok"])
        self.assertEqual(result["events"][0]["source"], "unit-camera")
        self.assertEqual(result["events"][0]["metadata"]["objects"][0]["label"], "person")

    def test_permission_api_syncs_through_runtime_event_bus(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        before = len(main.runtime.event_bus.history())
        with patch.object(main, "set_permission_level", return_value={"applied": True}):
            result = asyncio.run(main.api_set_permission({"permission": "quick_auth"}))
        event = main.runtime.event_bus.history()[-1]

        self.assertTrue(result.get("applied"))
        self.assertGreater(len(main.runtime.event_bus.history()), before)
        self.assertEqual(event.type, "permission.changed")
        self.assertEqual(event.payload["permission"], "quick_auth")

    def test_command_task_runner_records_completed_command(self):
        from control.command_tasks import CommandTaskRunner
        from core.events import EventBus

        bus = EventBus()
        runner = CommandTaskRunner(root=Path.cwd(), event_bus=bus, permission_level="root")

        result = runner.execute(
            command="echo javis-command-ok",
            shell="cmd",
            timeout=10,
            cwd=Path.cwd(),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["task"]["status"], "completed")
        self.assertIn("javis-command-ok", result["output"])
        self.assertEqual(bus.history("command_task.created")[-1].payload["task_id"], result["task"]["task_id"])
        self.assertEqual(bus.history("command_task.completed")[-1].payload["task_id"], result["task"]["task_id"])

    def test_command_task_runner_blocks_destructive_command_fail_closed(self):
        from control.command_tasks import CommandTaskRunner
        from core.events import EventBus

        bus = EventBus()
        runner = CommandTaskRunner(root=Path.cwd(), event_bus=bus, permission_level="safe")

        with patch("subprocess.run") as run:
            result = runner.execute(
                command="Remove-Item -Recurse -Force core",
                shell="powershell",
                timeout=10,
                cwd=Path.cwd(),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["task"]["status"], "blocked")
        self.assertIn("root", result["task"]["block_reason"].lower())
        run.assert_not_called()
        self.assertEqual(bus.history("command_task.blocked")[-1].payload["task_id"], result["task"]["task_id"])

    def test_root_command_requires_session_token_and_creates_rollback_point(self):
        from control.command_tasks import CommandTaskRunner
        from core.events import EventBus

        bus = EventBus()
        runner = CommandTaskRunner(root=Path.cwd(), event_bus=bus, permission_level="root")

        with patch("subprocess.run") as run:
            blocked = runner.execute(
                command="Remove-Item -Recurse -Force temp_unit",
                shell="powershell",
                timeout=10,
                cwd=Path.cwd(),
            )
            token = runner.issue_root_token(reason="unit test", ttl_sec=60)["token"]
            run.return_value = SimpleNamespace(returncode=0, stdout="removed", stderr="")
            allowed = runner.execute(
                command="Remove-Item -Recurse -Force temp_unit",
                shell="powershell",
                timeout=10,
                cwd=Path.cwd(),
                root_token=token,
            )

        self.assertFalse(blocked["ok"])
        self.assertIn("root session token", blocked["task"]["block_reason"].lower())
        self.assertTrue(allowed["ok"], allowed)
        self.assertEqual(allowed["task"]["risk"], "root")
        self.assertTrue(allowed["task"]["rollback_point"]["id"])
        self.assertEqual(bus.history("control.root_token.issued")[-1].payload["reason"], "unit test")

    def test_root_command_rollback_restores_file_contents(self):
        from control.command_tasks import CommandTaskRunner
        from core.events import EventBus

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "unit.txt"
            target.write_text("before", encoding="utf-8")
            runner = CommandTaskRunner(root=root, event_bus=EventBus(), permission_level="root")
            token = runner.issue_root_token(reason="unit rollback", ttl_sec=60)["token"]

            def mutate_file(*args, **kwargs):
                target.write_text("after", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="mutated", stderr="")

            with patch("subprocess.run", side_effect=mutate_file):
                executed = runner.execute(
                    command="Remove-Item -Recurse -Force unit.txt",
                    shell="powershell",
                    timeout=10,
                    cwd=root,
                    root_token=token,
                )
            restored = runner.restore_rollback_point(executed["task"]["rollback_point"]["id"])
            restored_text = target.read_text(encoding="utf-8")

        self.assertTrue(executed["ok"], executed)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(restored_text, "before")

    def test_command_task_public_rollback_point_redacts_snapshot_contents(self):
        from control.command_tasks import CommandTaskRunner
        from core.events import EventBus

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as td:
            root = Path(td)
            target = root / "unit.txt"
            target.write_text("private snapshot", encoding="utf-8")
            runner = CommandTaskRunner(root=root, event_bus=EventBus(), permission_level="root")
            token = runner.issue_root_token(reason="redaction", ttl_sec=60)["token"]

            result = runner.execute(
                command="Remove-Item -Recurse -Force unit.txt",
                shell="powershell",
                timeout=10,
                cwd=root,
                root_token=token,
            )

        rollback_point = result["task"]["rollback_point"]
        self.assertTrue(result["ok"], result)
        self.assertIn("id", rollback_point)
        self.assertEqual(rollback_point["file_count"], 1)
        self.assertNotIn("content", json.dumps(rollback_point))

    def test_root_command_rollback_restores_nested_binary_file_contents(self):
        from control.command_tasks import CommandTaskRunner
        from core.events import EventBus

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "unit.bin"
            target.write_bytes(b"\x00\x01before")
            runner = CommandTaskRunner(root=root, event_bus=EventBus(), permission_level="root")
            token = runner.issue_root_token(reason="binary rollback", ttl_sec=60)["token"]

            def mutate_file(*args, **kwargs):
                target.write_bytes(b"\x00\x01after")
                return SimpleNamespace(returncode=0, stdout="mutated", stderr="")

            with patch("subprocess.run", side_effect=mutate_file):
                executed = runner.execute(
                    command="Remove-Item -Recurse -Force nested",
                    shell="powershell",
                    timeout=10,
                    cwd=root,
                    root_token=token,
                )
            restored = runner.restore_rollback_point(executed["task"]["rollback_point"]["id"])
            restored_bytes = target.read_bytes()

        self.assertTrue(executed["ok"], executed)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(restored_bytes, b"\x00\x01before")

    def test_root_command_rollback_restores_large_file_from_durable_snapshot(self):
        from control.command_tasks import CommandTaskRunner
        from core.events import EventBus

        before = (b"before-large-file" * 70000)
        after = b"after"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "large.bin"
            target.write_bytes(before)
            runner = CommandTaskRunner(root=root, event_bus=EventBus(), permission_level="root")
            token = runner.issue_root_token(reason="large rollback", ttl_sec=60)["token"]

            def mutate_file(*args, **kwargs):
                target.write_bytes(after)
                return SimpleNamespace(returncode=0, stdout="mutated", stderr="")

            with patch("subprocess.run", side_effect=mutate_file):
                executed = runner.execute(
                    command="Remove-Item -Recurse -Force large.bin",
                    shell="powershell",
                    timeout=10,
                    cwd=root,
                    root_token=token,
                )
            rollback_point = executed["task"]["rollback_point"]
            restored = runner.restore_rollback_point(rollback_point["id"])
            restored_bytes = target.read_bytes()

        self.assertTrue(executed["ok"], executed)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(restored_bytes, before)
        self.assertEqual(rollback_point["captured_external_files"], 1)

    def test_main_control_rollback_restore_api_restores_snapshot(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as td:
            root = Path(td)
            target = root / "api-unit.txt"
            target.write_text("before", encoding="utf-8")
            old_root = main.command_task_runner.root
            main.command_task_runner.root = root.resolve()
            try:
                token = main.command_task_runner.issue_root_token(reason="api rollback", ttl_sec=60)["token"]

                def mutate_file(*args, **kwargs):
                    target.write_text("after", encoding="utf-8")
                    return SimpleNamespace(returncode=0, stdout="mutated", stderr="")

                with patch("subprocess.run", side_effect=mutate_file):
                    executed = asyncio.run(main.api_terminal_exec({
                        "command": "Remove-Item -Recurse -Force api-unit.txt",
                        "shell": "powershell",
                        "cwd": str(root),
                        "root_token": token,
                    }))
                restored = asyncio.run(main.api_control_rollback_restore({
                    "rollback_id": executed["task"]["rollback_point"]["id"],
                }))
                restored_text = target.read_text(encoding="utf-8")
            finally:
                main.command_task_runner.root = old_root

        self.assertTrue(executed["ok"], executed)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(restored_text, "before")

    def test_manual_fuse_blocks_commands_until_reset(self):
        from control.command_tasks import CommandTaskRunner
        from core.events import EventBus

        bus = EventBus()
        runner = CommandTaskRunner(root=Path.cwd(), event_bus=bus, permission_level="root")
        runner.trip_fuse("unit stop")
        blocked = runner.execute("echo after-fuse", shell="cmd", cwd=Path.cwd())
        runner.reset_fuse()
        allowed = runner.execute("echo after-reset", shell="cmd", cwd=Path.cwd())

        self.assertFalse(blocked["ok"])
        self.assertIn("manual fuse", blocked["task"]["block_reason"].lower())
        self.assertTrue(allowed["ok"], allowed)
        self.assertEqual(bus.history("control.fuse.tripped")[-1].payload["reason"], "unit stop")

    def test_terminal_api_returns_audited_command_task(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        before = len(main.runtime.event_bus.history())
        result = asyncio.run(main.api_terminal_exec({
            "command": "echo api-command-ok",
            "shell": "cmd",
            "timeout": 10,
        }))

        self.assertTrue(result["ok"], result)
        self.assertIn("task", result)
        self.assertEqual(result["task"]["status"], "completed")
        self.assertIn("api-command-ok", result["output"])
        self.assertGreater(len(main.runtime.event_bus.history()), before)

    def test_brain_test_mode_does_not_start_auto_flush_thread(self):
        env = {**os.environ, "JAVIS_TEST_MODE": "1", "PYTHONIOENCODING": "utf-8"}
        script = (
            "import json, threading\n"
            "from knowledge.brain import Brain\n"
            "Brain()\n"
            "names=[t.name for t in threading.enumerate() if t is not threading.main_thread()]\n"
            "print('THREADS_JSON=' + json.dumps(names))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        marker = "THREADS_JSON="
        line = next(line for line in result.stdout.splitlines() if line.startswith(marker))
        self.assertEqual(json.loads(line[len(marker):]), [])

    def test_brain_test_mode_does_not_register_exit_flush(self):
        env = {**os.environ, "JAVIS_TEST_MODE": "1", "PYTHONIOENCODING": "utf-8"}
        script = (
            "import json\n"
            "import knowledge.brain as brain_module\n"
            "calls=[]\n"
            "brain_module.atexit.register=lambda fn: calls.append(getattr(fn, '__name__', 'unknown'))\n"
            "brain_module.Brain()\n"
            "print('ATEXIT_JSON=' + json.dumps(calls))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        marker = "ATEXIT_JSON="
        line = next(line for line in result.stdout.splitlines() if line.startswith(marker))
        self.assertEqual(json.loads(line[len(marker):]), [])

    def test_providers_package_imports_without_stale_symbols(self):
        providers = importlib.import_module("providers")

        self.assertTrue(hasattr(providers, "ProviderConfig"))
        self.assertTrue(hasattr(providers, "ProviderPlugin"))

    def test_missing_config_uses_local_defaults_without_warning(self):
        from utils import config_api

        with tempfile.TemporaryDirectory() as td:
            original_path = config_api.CONFIG_PATH
            config_api.CONFIG_PATH = Path(td) / "missing-config.yaml"
            try:
                with self.assertNoLogs("config_api", level="WARNING"):
                    cfg = config_api.load_config()
            finally:
                config_api.CONFIG_PATH = original_path

        self.assertEqual(cfg["model"]["provider"], "local")
        self.assertEqual(cfg["model"]["local"]["api_key"], "ollama")

    def test_set_api_key_initializes_missing_model_section(self):
        from utils import config_api

        with tempfile.TemporaryDirectory() as td:
            original_path = config_api.CONFIG_PATH
            config_path = Path(td) / "config.yaml"
            config_api.CONFIG_PATH = config_path
            security_warned = config_api._SECURITY_WARNED
            config_api._SECURITY_WARNED = True
            try:
                model = config_api.set_api_key("deepseek", "secret")
                saved = config_path.exists()
                loaded = config_api.load_config()
            finally:
                config_api.CONFIG_PATH = original_path
                config_api._SECURITY_WARNED = security_warned

        self.assertIn("deepseek", model)
        self.assertEqual(loaded["model"]["deepseek"]["api_key"], "secret")
        self.assertTrue(saved)

    def test_config_read_write_closes_file_handles(self):
        from utils import config_api

        with tempfile.TemporaryDirectory() as td:
            original_path = config_api.CONFIG_PATH
            config_api.CONFIG_PATH = Path(td) / "config.yaml"
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always", ResourceWarning)
                    config_api.set_provider("local")
                    config_api.load_config()
                    gc.collect()
            finally:
                config_api.CONFIG_PATH = original_path

        resource_warnings = [w for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(resource_warnings, [])

    def test_tool_setup_closes_github_token_file(self):
        from tools import setup as tool_setup

        original_tools_dir = tool_setup.TOOLS_DIR
        original_path = os.environ.get("PATH")
        original_token = os.environ.get("GH_TOKEN")
        with tempfile.TemporaryDirectory() as td:
            tools_dir = Path(td) / "tools"
            token_dir = tools_dir / "gh"
            token_dir.mkdir(parents=True)
            (token_dir / ".token").write_text("test-token-value", encoding="utf-8")
            tool_setup.TOOLS_DIR = str(tools_dir)
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always", ResourceWarning)
                    result = tool_setup.setup()
                    gc.collect()
            finally:
                tool_setup.TOOLS_DIR = original_tools_dir
                if original_path is None:
                    os.environ.pop("PATH", None)
                else:
                    os.environ["PATH"] = original_path
                if original_token is None:
                    os.environ.pop("GH_TOKEN", None)
                else:
                    os.environ["GH_TOKEN"] = original_token

        resource_warnings = [w for w in caught if issubclass(w.category, ResourceWarning)]
        self.assertEqual(result["gh_token"], "OK")
        self.assertEqual(resource_warnings, [])

    def test_workspace_paths_cannot_escape_project_root(self):
        main = importlib.import_module("main")

        inside = main._resolve_workspace_path("web/index.html")
        self.assertTrue(str(inside).startswith(str(main.ROOT.resolve())))

        with self.assertRaises(ValueError):
            main._resolve_workspace_path("../outside.txt")

    def test_ws_upload_helper_sanitizes_and_saves_inside_uploads(self):
        main = importlib.import_module("main")

        saved = main._save_uploaded_file_for_ws("../unsafe.txt", "hello")
        try:
            uploads_root = (main.ROOT / "uploads").resolve()
            self.assertEqual(saved.name, "unsafe.txt")
            self.assertTrue(str(saved).startswith(str(uploads_root)))
            self.assertEqual(saved.read_text(encoding="utf-8"), "hello")
        finally:
            if saved.exists():
                saved.unlink()

    def test_persisted_language_loader_strips_duplicate_entrypoints(self):
        from tools import code_exec

        persisted = "\n".join(
            [
                "# Auto-registered language: demo",
                "# compiler: persisted, version: auto",
                "",
                "def handler(code):",
                "    return code",
                "",
                'if __name__ == "__main__":',
                "    import sys",
                "    r = handler(sys.stdin.read())",
                "    print(r)",
                "",
                'if __name__ == "__main__":',
                "    import sys",
                "    r = handler(sys.stdin.read())",
                "    print(r)",
            ]
        )

        body = code_exec._extract_language_handler_source(persisted)
        rendered = code_exec._render_language_registration("demo", body, "persisted", "auto")

        self.assertNotIn('if __name__ == "__main__"', body)
        self.assertEqual(rendered.count('if __name__ == "__main__"'), 1)

    def test_catch2_tool_uses_project_paths_and_handles_missing_compiler(self):
        from tools_lib import tool_catch2

        self.assertEqual(tool_catch2.PROJECT_ROOT, Path.cwd().resolve())
        self.assertEqual(tool_catch2.CATCH_DIR, Path.cwd().resolve() / "tools" / "catch2")

        original_compiler = tool_catch2.MINGW_GXX
        original_path_lookup = tool_catch2.shutil.which
        tool_catch2.MINGW_GXX = Path.cwd() / "__missing__" / "g++.exe"
        tool_catch2.shutil.which = lambda _name: None
        try:
            result = tool_catch2.run_catch2_test("basic")
        finally:
            tool_catch2.MINGW_GXX = original_compiler
            tool_catch2.shutil.which = original_path_lookup

        self.assertIn("C++ compiler is not available", result)

    def test_tools_lib_loader_skips_internal_modules_without_warning(self):
        from core.tool_registry import ToolRegistry
        from tools_lib import loader

        registry = ToolRegistry(permission_level="safe")
        with self.assertNoLogs("tools_lib.loader", level="WARNING"):
            loader.register(registry)

        self.assertIn("plugin_creator", registry.list_all())

    def test_distill_bash_tool_does_not_use_shell_true(self):
        from agent_distill.core import tool_registry

        fake_completed = SimpleNamespace(stdout="ok", stderr="", returncode=0)
        with patch("subprocess.run", return_value=fake_completed) as run:
            result = tool_registry._bash("echo ok")

        self.assertFalse(result.is_error)
        self.assertIn("ok", result.content)
        self.assertNotEqual(run.call_args.kwargs.get("shell"), True)
        self.assertIsInstance(run.call_args.args[0], list)

    def test_lnk_probe_passes_path_outside_powershell_script(self):
        from tools import system

        suspicious_path = 'C:\\Temp\\bad"; Write-Output HACK; #.lnk'
        fake_completed = SimpleNamespace(stdout="STORE\n", stderr="", returncode=0)
        with patch("subprocess.run", return_value=fake_completed) as run:
            self.assertTrue(system._is_store_lnk(suspicious_path))

        args = run.call_args.args[0]
        self.assertIsInstance(args, list)
        self.assertNotIn(suspicious_path, args)
        self.assertEqual(run.call_args.kwargs["env"]["JAVIS_LNK_PATH"], suspicious_path)

    def test_tool_creator_rejects_unsafe_tool_names_without_writing_file(self):
        from tools_lib import tool_creator

        original_dir = tool_creator._TOOLS_LIB_DIR
        with tempfile.TemporaryDirectory() as td:
            tool_creator._TOOLS_LIB_DIR = Path(td)
            try:
                result = tool_creator.save_tool('bad"name', "desc", 'return "ok"')
                written = list(Path(td).rglob("*.py"))
            finally:
                tool_creator._TOOLS_LIB_DIR = original_dir

        self.assertIn("工具名", result)
        self.assertEqual(written, [])

    def test_file_write_refuses_protected_javis_system_paths(self):
        from tools import file_ops

        target = Path("core") / "__guard_test__.txt"
        try:
            result = file_ops.file_write(str(target), "should not be written")
        finally:
            if target.exists():
                target.unlink()

        self.assertFalse(result.success)
        self.assertFalse(target.exists())

    def test_file_edit_refuses_protected_javis_system_paths(self):
        from tools import search

        target = Path("core") / "__edit_guard_test__.txt"
        target.write_text("old", encoding="utf-8")
        try:
            result = search.file_edit(str(target), "old", "new")
            content = target.read_text(encoding="utf-8")
        finally:
            if target.exists():
                target.unlink()

        self.assertFalse(result.success)
        self.assertEqual(content, "old")

    def test_workspace_save_refuses_protected_javis_system_paths(self):
        main = importlib.import_module("main")

        target = Path("core") / "__api_guard_test__.txt"
        try:
            result = asyncio.run(main.api_workspace_save({
                "path": str(target),
                "content": "should not be written",
            }))
        finally:
            if target.exists():
                target.unlink()

        self.assertFalse(result["ok"])
        self.assertFalse(target.exists())

    def test_memory_rename_reports_index_read_failure(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        os.environ.setdefault("DEEPSEEK_API_KEY", "dummy")
        main = importlib.import_module("main")

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.read_text", side_effect=OSError("index unavailable")),
        ):
            result = asyncio.run(main.api_mem_rename("session-1", {"name": "Renamed"}))

        self.assertFalse(result["ok"])
        self.assertIn("index unavailable", result["error"])

    def test_skill_manager_exports_main_expected_api(self):
        from core import skill_manager

        original_dir = skill_manager.SKILLS_DIR
        original_manager = skill_manager._manager
        with tempfile.TemporaryDirectory() as td:
            skill_manager.SKILLS_DIR = Path(td)
            (Path(td) / "demo.py").write_text("def register(tools): pass\n", encoding="utf-8")
            skill_manager._manager = None
            try:
                manager = skill_manager.get_skill_manager()
                installed = manager.list_all()
            finally:
                skill_manager.SKILLS_DIR = original_dir
                skill_manager._manager = original_manager

        self.assertEqual(installed, [{"name": "demo", "status": "active"}])

    def test_skill_manager_registered_tools_accept_registry_kwargs(self):
        from core import skill_manager

        original_dir = skill_manager.SKILLS_DIR
        original_manager = skill_manager._manager
        with tempfile.TemporaryDirectory() as td:
            skill_manager.SKILLS_DIR = Path(td)
            (Path(td) / "demo.py").write_text("def register(tools): pass\n", encoding="utf-8")
            skill_manager._manager = None
            try:
                registry = ToolRegistry(permission_level="full_access")
                skill_manager.register_in_manifest(registry)
                result = asyncio.run(registry.execute("skill_list_installed", {}))
            finally:
                skill_manager.SKILLS_DIR = original_dir
                skill_manager._manager = original_manager

        self.assertTrue(result.success)
        self.assertIn("'success': True", result.data)
        self.assertIn("'name': 'demo'", result.data)
        self.assertIn("'status': 'active'", result.data)

    def test_skill_creator_registered_tools_accept_registry_kwargs(self):
        from core import skill_creator

        original_creator = skill_creator._creator
        with tempfile.TemporaryDirectory() as td:
            skill_creator._creator = skill_creator.SkillCreator(td)
            try:
                registry = ToolRegistry(permission_level="full_access")
                skill_creator.register_in_manifest(registry)
                result = asyncio.run(registry.execute("skill_list", {}))
            finally:
                skill_creator._creator = original_creator

        self.assertTrue(result.success)
        self.assertIn("'success': True", result.data)
        self.assertIn("'skills':", result.data)

    def test_skill_creator_rejects_unsafe_skill_names_before_writing(self):
        from core import skill_creator

        with tempfile.TemporaryDirectory() as td:
            outside = Path(td).parent / "bad.py"
            if outside.exists():
                outside.unlink()
            creator = skill_creator.SkillCreator(td)
            try:
                with self.assertRaises(ValueError):
                    creator.create("../bad", "description long enough", "prompt long enough for testing")

                self.assertFalse(outside.exists())
                self.assertEqual(list(Path(td).rglob("*.py")), [])
            finally:
                if outside.exists():
                    outside.unlink()

    def test_skill_creator_mutating_operations_reject_unsafe_names(self):
        from core import skill_creator

        with tempfile.TemporaryDirectory() as td:
            outside = Path(td).parent / "bad.py"
            outside.write_text("original", encoding="utf-8")
            creator = skill_creator.SkillCreator(td)
            try:
                self.assertFalse(creator.improve("../bad", new_prompt="changed"))
                self.assertIsNone(creator.export_skill("../bad", td))
                self.assertFalse(creator.delete_skill("../bad"))

                self.assertTrue(outside.exists())
                self.assertEqual(outside.read_text(encoding="utf-8"), "original")
            finally:
                if outside.exists():
                    outside.unlink()

    def test_skill_creator_review_understands_generated_prompt_format(self):
        from core import skill_creator

        long_prompt = "x" * 120
        with tempfile.TemporaryDirectory() as td:
            creator = skill_creator.SkillCreator(td)
            creator.create("demo_skill", "description long enough", long_prompt)
            review = creator.review("demo_skill")

        prompt_check = next(c for c in review["checks"] if c["check"] == "prompt_min_100")
        self.assertTrue(prompt_check["passed"])

    def test_registered_async_tool_handlers_do_not_use_legacy_args_signature(self):
        legacy_pattern = re.compile(r"async def [A-Za-z0-9_]+\(\s*args\s*\)")
        paths = [
            Path("gateway/gateway_manager.py"),
            Path("core/auto_updater.py"),
            Path("core/hook_system.py"),
            Path("tools/cron_scheduler.py"),
            Path("core/subagent.py"),
            Path("tools/sandbox.py"),
            Path("tools/provider_loader.py"),
            Path("core/skill_creator.py"),
            Path("core/skill_manager.py"),
        ]
        offenders = [
            str(path)
            for path in paths
            if legacy_pattern.search(path.read_text(encoding="utf-8"))
        ]

        self.assertEqual(offenders, [])

    def test_main_entrypoint_does_not_open_config_without_context_manager(self):
        content = Path("main.py").read_text(encoding="utf-8")

        self.assertNotIn("safe_load(open(", content)

    def test_main_uses_runtime_factory_instead_of_manual_kernel_bootstrap(self):
        content = Path("main.py").read_text(encoding="utf-8")

        self.assertIn("create_runtime(ROOT", content)
        self.assertNotIn("brain=Brain()", content)
        self.assertNotIn("registry=ToolRegistry", content)
        self.assertNotIn("agent=Agent(", content)

    def test_extension_registry_status_tools_execute_with_kwargs_contract(self):
        registry = ToolRegistry(permission_level="full_access")

        from tools import provider_loader, cron_scheduler, sandbox
        from core import hook_system, auto_updater, subagent
        from gateway import gateway_manager

        for module in [
            provider_loader,
            cron_scheduler,
            sandbox,
            hook_system,
            auto_updater,
            subagent,
            gateway_manager,
        ]:
            module.register_in_manifest(registry)

        for tool_name in [
            "provider_stats",
            "cron_stats",
            "sandbox_status",
            "hook_status",
            "current_version",
            "subagent_status",
            "gateway_status",
        ]:
            result = asyncio.run(registry.execute(tool_name, {}))
            self.assertTrue(result.success, f"{tool_name}: {result.error}")


if __name__ == "__main__":
    unittest.main()
