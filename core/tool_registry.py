"""工具注册中心 — 管理所有工具的注册和调度"""

import asyncio
import logging
from typing import Any, Callable
from dataclasses import dataclass, field
from core.tool_result import ToolResult

logger = logging.getLogger("tools")


@dataclass
class ToolDef:
    """工具定义"""
    name: str
    description: str
    parameters: dict            # JSON Schema
    handler: Callable           # async function(params) -> ToolResult
    category: str = "general"


class ToolRegistry:
    """工具注册中心 — 动态管理所有可用工具"""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._categories: dict[str, list[str]] = {}

    def register(self, tool: ToolDef):
        """注册一个工具"""
        self._tools[tool.name] = tool
        self._categories.setdefault(tool.category, []).append(tool.name)
        logger.info(f"工具已注册: {tool.name} [{tool.category}]")

    def register_many(self, tools: list[ToolDef]):
        """批量注册"""
        for t in tools:
            self.register(t)

    def get(self, name: str) -> ToolDef | None:
        """获取工具定义"""
        return self._tools.get(name)

    async def execute(self, name: str, params: dict) -> ToolResult:
        """执行工具"""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult.failure(f"未知工具: {name}")

        try:
            result = tool.handler(**params)
            if asyncio.iscoroutine(result):
                result = await result
            if not isinstance(result, ToolResult):
                result = ToolResult.success(result)
            return result
        except Exception as e:
            logger.error(f"工具执行失败 [{name}]: {e}")
            return ToolResult.failure(f"{name} 执行失败: {e}")

    def get_schemas(self) -> list[dict]:
        """生成 LLM function calling 用的完整工具 schema"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                }
            }
            for t in self._tools.values()
        ]

    def get_light_schemas(self) -> list[dict]:
        """轻量级工具 schema（仅name+一句话描述，无parameters）"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description.split(".")[0][:60],
                    "parameters": {"type": "object", "properties": {}},
                }
            }
            for t in self._tools.values()
        ]

    @property
    def count(self) -> int:
        return len(self._tools)

    def list_all(self) -> list[str]:
        return list(self._tools.keys())

    def list_by_category(self, category: str) -> list[str]:
        return self._categories.get(category, [])

    def clear(self):
        """清空所有已注册工具 (用于技能切换)"""
        self._tools.clear()
        self._categories.clear()
