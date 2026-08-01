import asyncio
import importlib
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from core.agent_run_recorder import AgentRunRecorder
from core.agent_runs import AgentRunStore
from core.runtime import create_runtime
from core.skill_catalog import SkillCatalog


class RuntimeAgentFusionTests(unittest.TestCase):
    def test_websocket_agent_loop_records_stream_without_rewriting_payloads(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")

        self.assertIn("AgentRunRecorder(", source)
        self.assertIn("runtime.agent_runs,", source)
        self.assertIn("recorder.record(msg)", source)
        self.assertIn("recorder.resolve_confirmation", source)
        self.assertIn("await ws.send_json(r)", source)

    def test_runtime_registers_catalog_discovery_tools_and_agent_selects_progressively(self):
        with tempfile.TemporaryDirectory() as root_text:
            runtime = create_runtime(Path(root_text), startup_side_effects=False)
            runtime.register_always_on_tools()

            schemas = runtime.agent._select_tool_schemas("search the web for release notes")
            names = [schema["function"]["name"] for schema in schemas]
            all_names = runtime.registry.list_all()
            status = runtime.get_runtime_status()
            runtime.close()

        self.assertIn("tool_search", names)
        self.assertIn("tool_inspect", names)
        self.assertIn("web_search", names)
        self.assertLess(len(names), len(all_names))
        self.assertEqual(status["catalogs"]["tools"]["count"], len(all_names))
        self.assertEqual(status["catalogs"]["skills"]["total"], 0)
        self.assertEqual(status["agent_runs"]["active"], 0)

    def test_recorder_maps_existing_agent_messages_without_changing_them(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = AgentRunStore(Path(root_text) / "runs.sqlite3")
            recorder = AgentRunRecorder(store, "Inspect system", session_id="session-1")
            messages = [
                {"type": "thinking", "content": "planning"},
                {"type": "tool_start", "tool": "system_info", "params": {}},
                {
                    "type": "confirm_required",
                    "tool": "system_info",
                    "reason": "test approval",
                    "params": {},
                },
            ]
            for message in messages:
                self.assertIs(recorder.record(message), message)
            recorder.resolve_confirmation(True, response={"source": "unit"})
            result_message = {
                "type": "tool_result",
                "tool": "system_info",
                "success": True,
                "data": "ok",
            }
            recorder.record(result_message)
            recorder.record({"type": "text_delta", "text": "System is healthy"})
            recorder.record({"type": "done"})
            graph = store.get_run(recorder.run_id, include_graph=True)
            store.close()

        self.assertEqual(graph["status"], "completed")
        self.assertEqual(graph["metadata"]["session_id"], "session-1")
        self.assertEqual(graph["approvals"][0]["status"], "approved")
        steps = graph["tasks"][0]["steps"]
        self.assertEqual([step["kind"] for step in steps], ["conversation", "tool"])
        self.assertEqual(steps[1]["status"], "completed")

    def test_catalog_and_run_api_contracts_return_structured_results(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        tools = asyncio.run(main.api_tool_catalog(q="web search", limit=10))
        inspected = asyncio.run(main.api_tool_catalog_inspect("tool_search"))
        missing = asyncio.run(main.api_tool_catalog_inspect("missing-tool"))
        created = asyncio.run(main.api_agent_run_create({"objective": "API contract test"}))
        fetched = asyncio.run(main.api_agent_run_get(created["run"]["id"]))
        cancelled = asyncio.run(main.api_agent_run_cancel(created["run"]["id"], {"reason": "done"}))

        self.assertTrue(tools["ok"])
        self.assertNotIn("parameters", tools["tools"][0])
        self.assertTrue(inspected["ok"])
        self.assertIn("parameters", inspected["tool"])
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"]["code"], "tool_not_found")
        self.assertEqual(fetched["run"]["objective"], "API contract test")
        self.assertEqual(cancelled["run"]["status"], "cancelled")

    def test_skill_catalog_api_exposes_governance_without_execution(self):
        os.environ["JAVIS_TEST_MODE"] = "1"
        main = importlib.import_module("main")

        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            skill_dir = root / "sample"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: api-skill\n"
                "description: API skill catalog contract\n"
                "license: MIT\n"
                "version: 1.0.0\n"
                "tags: [api, test]\n"
                "---\n"
                "Use guarded tools.\n",
                encoding="utf-8",
            )
            catalog = SkillCatalog(root / "catalog.sqlite3", event_bus=main.runtime.event_bus)
            catalog.discover(root, source="unit")
            with patch.object(main.runtime, "skill_catalog", catalog):
                listed = asyncio.run(main.api_skill_catalog(q="api", status="", source="", limit=10))
                evaluated = asyncio.run(main.api_skill_catalog_evaluate({
                    "name": "api-skill",
                    "status": "passed",
                    "score": 0.9,
                }))
                staged = asyncio.run(main.api_skill_catalog_status({
                    "name": "api-skill",
                    "status": "staged",
                }))
            catalog.close()

        self.assertEqual(listed["skills"][0]["name"], "api-skill")
        self.assertEqual(evaluated["skill"]["evaluation_status"], "passed")
        self.assertEqual(staged["skill"]["status"], "staged")


if __name__ == "__main__":
    unittest.main()
