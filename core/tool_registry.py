"""Tool registry for registering and executing Javis tools."""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

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


class ToolRegistry:
    def __init__(self, permission_level: str = "full_access", guard: ToolGuard | None = None):
        self._tools: dict[str, ToolDef] = {}
        self._categories: dict[str, list[str]] = {}
        self.guard = guard or ToolGuard(PERMISSION_ALIASES.get(permission_level, permission_level))

    def register(self, tool: ToolDef):
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
    ) -> ToolResult:
        tool = self._tools.get(name)
        if not tool:
            return ToolResult.failure(f"Unknown tool: {name}")

        params = params or {}
        safe_params = sanitize_params(params)
        block_reason = self.guard.pre_check(
            name,
            safe_params,
            tool.parameters,
            confirmed=confirmed,
        )
        if block_reason:
            return ToolResult.failure(block_reason)

        started = time.perf_counter()
        try:
            result = tool.handler(**params)
            if asyncio.iscoroutine(result):
                result = await result
            if not isinstance(result, ToolResult):
                result = ToolResult.success(result)

            duration_ms = (time.perf_counter() - started) * 1000
            output = result.data if result.success else result.error
            post_reason = self.guard.post_check(name, safe_params, result.success, duration_ms, str(output))
            if post_reason:
                return ToolResult.failure(post_reason)
            return result
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            self.guard.post_check(name, safe_params, False, duration_ms, str(exc))
            logger.error(f"Tool execution failed [{name}]: {exc}")
            return ToolResult.failure(f"{name} execution failed: {exc}")

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
