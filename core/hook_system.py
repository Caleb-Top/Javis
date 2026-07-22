"""
Hook 事件系统 — 10种事件 + register/trigger + 集成到 agent.py
"""
import logging
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("hooks")


class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    SUBAGENT_STOP = "SubagentStop"
    SUBAGENT_START = "SubagentStart"
    PRE_COMPACT = "PreCompact"
    PERMISSION_REQUEST = "PermissionRequest"
    NOTIFICATION = "Notification"


@dataclass
class HookResult:
    """单个 Hook 触发的结果"""
    allowed: bool = True
    message: str = ""
    data: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


HookCallback = Callable[[dict[str, Any]], HookResult]


class HookSystem:
    """事件钩子系统 — 支持同步/异步回调"""

    def __init__(self):
        self._hooks: dict[HookEvent, list[HookCallback]] = {
            e: [] for e in HookEvent
        }

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        """注册一个钩子回调"""
        self._hooks[event].append(callback)
        logger.debug(f"Hook 注册: {event.value} ({len(self._hooks[event])} handlers)")

    def trigger(self, event: HookEvent, data: dict[str, Any]) -> HookResult:
        """同步触发钩子链 — 返回第一个拒绝的结果，否则允许"""
        for cb in self._hooks[event]:
            try:
                result = cb(data)
                if not result.allowed:
                    return result
            except Exception as e:
                logger.error(f"Hook 执行失败 [{event.value}]: {e}")
        return HookResult(allowed=True)

    async def trigger_async(self, event: HookEvent, data: dict[str, Any]) -> HookResult:
        """异步触发钩子链 — 支持 async 回调"""
        import asyncio
        for cb in self._hooks[event]:
            try:
                result = cb(data)
                if asyncio.iscoroutine(result):
                    result = await result
                if not isinstance(result, HookResult):
                    result = HookResult(allowed=True)
                if not result.allowed:
                    return result
            except Exception as e:
                logger.error(f"Hook 执行失败 [{event.value}]: {e}")
        return HookResult(allowed=True)

    def get_handlers_count(self, event: HookEvent) -> int:
        return len(self._hooks.get(event, []))

    def list_events(self) -> list[dict]:
        """列出所有事件及其处理程序数"""
        return [{"event": e.value, "handlers": len(self._hooks[e])} for e in HookEvent]


class HookManager:
    """Hook 管理器 — 与 HookSystem 兼容的异步接口，用于 agent.py 集成"""

    def __init__(self):
        self._system = get_hook_system()

    async def trigger(self, event: HookEvent, data: dict[str, Any]) -> HookResult:
        return await self._system.trigger_async(event, data)

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        self._system.register(event, callback)


# 全局单例
_hook_system: Optional[HookSystem] = None
_hook_manager: Optional[HookManager] = None


def get_hook_system() -> HookSystem:
    global _hook_system
    if _hook_system is None:
        _hook_system = HookSystem()
    return _hook_system


def get_hook_manager() -> HookManager:
    """agent.py 使用的别名"""
    global _hook_manager
    if _hook_manager is None:
        _hook_manager = HookManager()
    return _hook_manager


def register_in_manifest(reg):
    """Register hook tools in manifest"""
    from core.tool_registry import ToolDef
    hooks = get_hook_system()

    async def hook_list(args):
        events = hooks.list_events()
        return {"success": True, "events": events, "total_events": len(events)}

    async def hook_status(args):
        return {
            "success": True,
            "events": hooks.list_events(),
            "total_handlers": sum(len(cbs) for cbs in hooks._hooks.values()),
        }

    async def hook_trigger(args):
        event_name = args.get("event", "")
        try:
            event = HookEvent(event_name)
        except ValueError:
            return {"success": False, "error": f"Unknown event: {event_name}"}
        result = hooks.trigger(event, args.get("data", {}))
        return {
            "success": True, "allowed": result.allowed,
            "message": result.message, "data": result.data,
        }

    reg.register_many([
        ToolDef("hook_list", "List all hook events and their handler counts",
                {"type":"object","properties":{},"required":[]}, hook_list, "hooks"),
        ToolDef("hook_status", "Get hook system status",
                {"type":"object","properties":{},"required":[]}, hook_status, "hooks"),
        ToolDef("hook_trigger", "Manually trigger a hook event",
                {"type":"object","properties":{"event":{"type":"string"},"data":{"type":"object","default":{}}},"required":["event"]}, hook_trigger, "hooks"),
    ])
