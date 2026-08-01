"""Tool registry for registering and executing Javis tools."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from core.events import EventBus, EventType
from core.middleware import MiddlewareContext, MiddlewarePipeline, MiddlewareRejected
from core.tool_guardrails import ToolGuard, sanitize_params
from core.tool_result import ToolResult

logger = logging.getLogger("tools")


PERMISSION_ALIASES = {
    "full_access": "critical",
    "quick_auth": "dangerous",
    "safe_guard": "medium",
    "full_approval": "safe",
}


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    handler: Callable
    category: str = "general"
    tags: tuple[str, ...] = ()
    source: str = "builtin"
    risk: str | None = None
    permission: str | None = None
    timeout_seconds: float | None = None


class ToolRegistry:
    def __init__(
        self,
        permission_level: str = "full_access",
        guard: ToolGuard | None = None,
        event_bus: EventBus | None = None,
        middleware: MiddlewarePipeline | None = None,
    ):
        self._tools: dict[str, ToolDef] = {}
        self._categories: dict[str, list[str]] = {}
        self._execution_stats: dict[str, dict[str, Any]] = {}
        self.guard = guard or ToolGuard(PERMISSION_ALIASES.get(permission_level, permission_level))
        self.event_bus = event_bus
        self.middleware = middleware or MiddlewarePipeline()

    def register(self, tool: ToolDef):
        previous = self._tools.get(tool.name)
        if previous is not None and previous.category != tool.category:
            previous_category = self._categories.get(previous.category, [])
            if tool.name in previous_category:
                previous_category.remove(tool.name)
        self._tools[tool.name] = tool
        category = self._categories.setdefault(tool.category, [])
        if tool.name not in category:
            category.append(tool.name)
        logger.info(f"Tool registered: {tool.name} [{tool.category}]")

    def register_many(self, tools: list[ToolDef]):
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    async def execute(
        self,
        name: str,
        params: dict | None,
        *,
        confirmed: bool = False,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult.failure(f"Unknown tool: {name}")

        params = params or {}
        safe_params = sanitize_params(params)
        correlation_id = correlation_id or uuid.uuid4().hex
        started_event = self._publish(
            EventType.TOOL_STARTED,
            {
                "tool": name,
                "category": tool.category,
                "confirmed": confirmed,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        started = time.perf_counter()

        async def invoke(_context: MiddlewareContext) -> ToolResult:
            block_reason = self.guard.pre_check(
                name,
                safe_params,
                tool.parameters,
                confirmed=confirmed,
            )
            if block_reason:
                return ToolResult.failure(block_reason)

            handler_started = time.perf_counter()
            try:
                result = tool.handler(**params)
                if asyncio.iscoroutine(result):
                    if tool.timeout_seconds:
                        result = await asyncio.wait_for(result, timeout=tool.timeout_seconds)
                    else:
                        result = await result
                if not isinstance(result, ToolResult):
                    result = ToolResult.success(result)

                duration_ms = (time.perf_counter() - handler_started) * 1000
                output = result.data if result.success else result.error
                post_reason = self.guard.post_check(name, safe_params, result.success, duration_ms, str(output))
                if post_reason:
                    return ToolResult.failure(post_reason)
                return result
            except Exception as exc:
                duration_ms = (time.perf_counter() - handler_started) * 1000
                self.guard.post_check(name, safe_params, False, duration_ms, str(exc))
                logger.error(f"Tool execution failed [{name}]: {exc}")
                return ToolResult.failure(f"{name} execution failed: {exc}")

        context = MiddlewareContext(
            operation="tool.execute",
            payload={"tool": name, "params": safe_params, "confirmed": confirmed},
            source="tools",
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata={
                "category": tool.category,
                "source": tool.source,
                "risk": tool.risk,
            },
        )
        try:
            result = await self.middleware.execute_async(context, invoke)
            if not isinstance(result, ToolResult):
                result = ToolResult.success(result)
        except MiddlewareRejected as exc:
            result = ToolResult.failure(str(exc))
        except Exception as exc:
            logger.error("Tool middleware failed [%s]: %s", name, exc)
            result = ToolResult.failure(f"{name} execution failed: {exc}")

        duration_ms = (time.perf_counter() - started) * 1000
        self._record_execution(name, result, duration_ms)
        event_type = EventType.TOOL_COMPLETED if result.success else EventType.TOOL_FAILED
        self._publish(
            event_type,
            {
                "tool": name,
                "category": tool.category,
                "success": result.success,
                "duration_ms": round(duration_ms, 3),
                "error": result.error[:500] if not result.success else "",
            },
            correlation_id=correlation_id,
            causation_id=started_event.id if started_event else causation_id,
        )
        return result

    def _publish(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        *,
        correlation_id: str,
        causation_id: str | None,
    ):
        if self.event_bus is None:
            return None
        return self.event_bus.publish(
            event_type,
            payload,
            source="tools",
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def _record_execution(self, name: str, result: ToolResult, duration_ms: float) -> None:
        stats = self._execution_stats.setdefault(name, {
            "calls": 0,
            "successes": 0,
            "failures": 0,
            "total_duration_ms": 0.0,
            "last_duration_ms": 0.0,
            "last_error": "",
        })
        stats["calls"] += 1
        stats["successes" if result.success else "failures"] += 1
        stats["total_duration_ms"] += duration_ms
        stats["last_duration_ms"] = duration_ms
        stats["last_error"] = "" if result.success else result.error[:500]

    def execution_stats(self, name: str) -> dict[str, Any]:
        stats = dict(self._execution_stats.get(name, {}))
        calls = int(stats.get("calls", 0))
        successes = int(stats.get("successes", 0))
        failures = int(stats.get("failures", 0))
        total_duration_ms = float(stats.get("total_duration_ms", 0.0))
        return {
            "calls": calls,
            "successes": successes,
            "failures": failures,
            "success_rate": round(successes / calls, 4) if calls else None,
            "average_duration_ms": round(total_duration_ms / calls, 3) if calls else None,
            "last_duration_ms": round(float(stats.get("last_duration_ms", 0.0)), 3) if calls else None,
            "last_error": str(stats.get("last_error", "")),
        }

    def get_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def get_light_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description.split(".")[0][:60],
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for tool in self._tools.values()
        ]

    @property
    def count(self) -> int:
        return len(self._tools)

    def list_all(self) -> list[str]:
        return list(self._tools.keys())

    def list_by_category(self, category: str) -> list[str]:
        return self._categories.get(category, [])

    def set_permission_level(self, permission_level: str):
        normalized = PERMISSION_ALIASES.get(permission_level, permission_level)
        self.guard.permission_level = ToolGuard(normalized).permission_level

    def clear(self):
        self._tools.clear()
        self._categories.clear()
        self._execution_stats.clear()
