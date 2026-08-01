import asyncio
import tempfile
import unittest
from pathlib import Path

from core.events import EventBus, EventType
from core.middleware import Middleware, MiddlewareContext, MiddlewarePipeline
from core.tool_catalog import ToolCatalog, ToolPreset
from core.tool_registry import ToolDef, ToolRegistry
from core.tool_result import ToolResult


EMPTY_SCHEMA = {"type": "object", "properties": {}, "required": []}


def make_registry(
    event_bus: EventBus | None = None,
    middleware: MiddlewarePipeline | None = None,
) -> ToolRegistry:
    registry = ToolRegistry(
        permission_level="full_access",
        event_bus=event_bus,
        middleware=middleware,
    )
    registry.register_many([
        ToolDef(
            "screenshot",
            "Capture the current screen for visual understanding",
            EMPTY_SCHEMA,
            lambda: ToolResult.success("captured"),
            "vision",
            tags=("screen", "capture", "realtime"),
            source="javis",
            risk="safe",
            timeout_seconds=5,
        ),
        ToolDef(
            "file_read",
            "Read a text file from the workspace",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            lambda path: ToolResult.success(path),
            "file",
            tags=("workspace", "read"),
            source="javis",
            risk="safe",
        ),
        ToolDef(
            "system_execute",
            "Execute a system command",
            EMPTY_SCHEMA,
            lambda: ToolResult.failure("disabled in test"),
            "system",
            tags=("command", "write"),
            source="javis",
            risk="critical",
        ),
    ])
    return registry


class ToolCatalogTests(unittest.TestCase):
    def test_runtime_owns_one_catalog_event_bus_and_middleware_pipeline(self):
        from core.runtime import create_runtime

        with tempfile.TemporaryDirectory() as root:
            runtime = create_runtime(Path(root), startup_side_effects=False)
            self.assertIs(runtime.tool_catalog.registry, runtime.registry)
            self.assertIs(runtime.registry.event_bus, runtime.event_bus)
            self.assertIs(runtime.registry.middleware, runtime.middleware)
            runtime.close()

    def test_list_is_lightweight_and_inspect_returns_one_full_schema(self):
        catalog = ToolCatalog(make_registry())

        listed = catalog.list_tools()
        inspected = catalog.inspect("file_read")

        self.assertEqual([item["name"] for item in listed], ["file_read", "screenshot", "system_execute"])
        self.assertNotIn("parameters", listed[0])
        self.assertNotIn("handler", listed[0])
        self.assertEqual(inspected["name"], "file_read")
        self.assertEqual(inspected["parameters"]["required"], ["path"])
        self.assertEqual(inspected["source"], "javis")

    def test_search_ranks_name_tags_and_description_deterministically(self):
        catalog = ToolCatalog(make_registry())

        capture = catalog.search("screen capture")
        workspace = catalog.search("workspace text")

        self.assertEqual(capture[0]["name"], "screenshot")
        self.assertEqual(workspace[0]["name"], "file_read")

    def test_presets_filter_categories_tags_and_maximum_risk(self):
        catalog = ToolCatalog(make_registry())
        catalog.define_preset(
            ToolPreset(
                name="visual-safe",
                categories=("vision",),
                required_tags=("realtime",),
                max_risk="low",
            )
        )

        selected = catalog.list_tools(preset="visual-safe")
        safe = catalog.list_tools(max_risk="safe")

        self.assertEqual([item["name"] for item in selected], ["screenshot"])
        self.assertEqual([item["name"] for item in safe], ["file_read", "screenshot"])

    def test_schema_selection_does_not_dump_unrelated_tools(self):
        catalog = ToolCatalog(make_registry())

        schemas = catalog.schemas_for("read workspace file", limit=2)

        self.assertEqual([schema["function"]["name"] for schema in schemas], ["file_read"])
        self.assertEqual(schemas[0]["function"]["parameters"]["required"], ["path"])

    def test_execution_delegates_to_registry_emits_events_and_updates_health(self):
        bus = EventBus()
        registry = make_registry(event_bus=bus)
        catalog = ToolCatalog(registry)

        success = asyncio.run(catalog.execute("screenshot", {}, correlation_id="run-7"))
        failure = asyncio.run(catalog.execute("system_execute", {}, confirmed=True, correlation_id="run-8"))
        health = {item["name"]: item for item in catalog.health()}

        self.assertTrue(success.success)
        self.assertFalse(failure.success)
        self.assertEqual(health["screenshot"]["calls"], 1)
        self.assertEqual(health["screenshot"]["success_rate"], 1.0)
        self.assertEqual(health["system_execute"]["failures"], 1)
        started = bus.history(EventType.TOOL_STARTED)[0]
        completed = bus.history(EventType.TOOL_COMPLETED)[0]
        failed = bus.history(EventType.TOOL_FAILED)[0]
        self.assertEqual(started.correlation_id, "run-7")
        self.assertEqual(completed.causation_id, started.id)
        self.assertEqual(failed.correlation_id, "run-8")

    def test_middleware_rejection_fails_closed_before_handler(self):
        called = False

        class RejectSystem(Middleware):
            def before(self, context: MiddlewareContext) -> None:
                if context.payload["tool"] == "system_execute":
                    context.reject("system tools disabled")

        registry = ToolRegistry(
            permission_level="full_access",
            middleware=MiddlewarePipeline([RejectSystem()]),
        )

        def handler():
            nonlocal called
            called = True
            return "unexpected"

        registry.register(ToolDef("system_execute", "test", EMPTY_SCHEMA, handler, "system"))

        result = asyncio.run(registry.execute("system_execute", {}, confirmed=True))

        self.assertFalse(result.success)
        self.assertIn("system tools disabled", result.error)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
