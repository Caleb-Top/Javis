"""
Hook 事件系统 — 10种事件 + register/trigger
"""
import logging
from enum import Enum
from typing import Callable, Any
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

HookCallback = Callable[[dict[str, Any]], dict[str, Any]]

class HookSystem:
    """事件钩子系统"""

    def __init__(self):
        self._hooks: dict[HookEvent, list[HookCallback]] = {
            e: [] for e in HookEvent
        }

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        self._hooks[event].append(callback)
        logger.debug(f"Hook 注册: {event.value} ({len(self._hooks[event])} handlers)")

    def trigger(self, event: HookEvent, data: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for cb in self._hooks[event]:
            try:
                result = cb(data)
                results.append(result)
            except Exception as e:
                logger.error(f"Hook 执行失败 [{event.value}]: {e}")
        return results

# 全局单例
_hook_system: HookSystem | None = None

def get_hook_system() -> HookSystem:
    global _hook_system
    if _hook_system is None:
        _hook_system = HookSystem()
    return _hook_system
