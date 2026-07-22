"""
P1-3: Hooks 系统 — 10种事件钩子, 类似 Claude Agent SDK Hooks
支持 PreToolUse / PostToolUse / PostToolUseFailure / UserPromptSubmit
      Stop / SubagentStop / SubagentStart / PreCompact
      PermissionRequest / Notification
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Awaitable
from enum import Enum


class HookEvent(Enum):
    """10种 Hook 事件类型"""
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
    """Hook 执行结果"""
    allowed: bool = True
    modified: Optional[Any] = None
    message: str = ""
    data: Dict = field(default_factory=dict)


HookCallback = Callable[[str, Dict[str, Any]], Awaitable[HookResult]]


class HookManager:
    """事件钩子系统 — 注册、触发、链式执行"""

    def __init__(self):
        self._hooks: Dict[HookEvent, List[HookCallback]] = {
            event: [] for event in HookEvent
        }
        self._global_hooks: List[HookCallback] = []
        self._stats: Dict[HookEvent, Dict] = {
            event: {"fired": 0, "blocked": 0, "total_ms": 0}
            for event in HookEvent
        }

    def on(self, event: HookEvent, callback: HookCallback):
        """注册事件处理器"""
        self._hooks[event].append(callback)

    def on_all(self, callback: HookCallback):
        """注册全局处理器 (监听所有事件)"""
        self._global_hooks.append(callback)

    def off(self, event: HookEvent, callback: HookCallback):
        """移除事件处理器"""
        self._hooks[event] = [h for h in self._hooks[event] if h != callback]

    async def trigger(self, event: HookEvent, context: Dict[str, Any] = None) -> HookResult:
        """触发事件, 按顺序执行所有处理器, 任一返回 allowed=False 即阻断"""
        context = context or {}
        context["event"] = event.value
        context["timestamp"] = time.time()

        final_result = HookResult(allowed=True)

        # 先执行全局 hooks
        for hook in self._global_hooks:
            try:
                result = await hook(event.value, context)
                if not result.allowed:
                    final_result = result
                    self._stats[event]["blocked"] += 1
                    return final_result
                if result.modified is not None:
                    final_result.modified = result.modified
                    context["modified"] = result.modified
            except Exception as e:
                pass  # Hook errors don't crash the pipeline

        # 再执行事件专用 hooks
        for hook in self._hooks[event]:
            t0 = time.time()
            try:
                result = await hook(event.value, context)
                self._stats[event]["total_ms"] += (time.time() - t0) * 1000
                if not result.allowed:
                    final_result = result
                    self._stats[event]["blocked"] += 1
                    return final_result
                if result.modified is not None:
                    final_result.modified = result.modified
                    context["modified"] = result.modified
            except Exception as e:
                self._stats[event]["total_ms"] += (time.time() - t0) * 1000

        self._stats[event]["fired"] += 1
        return final_result

    def get_stats(self) -> Dict:
        """返回所有事件的统计信息"""
        return {event.value: stats.copy() for event, stats in self._stats.items()}


# ---------- 内置 Hook 处理器 ----------

async def tool_permission_hook(event: str, ctx: Dict) -> HookResult:
    """PreToolUse: 检查工具权限"""
    tool_name = ctx.get("tool_name", "")
    # 黑名单
    blocked = {"os.system", "subprocess.call", "eval", "exec"}
    if tool_name in blocked:
        return HookResult(allowed=False, message=f"Tool '{tool_name}' is blocked")
    return HookResult(allowed=True)


async def tool_logger_hook(event: str, ctx: Dict) -> HookResult:
    """PostToolUse: 记录工具调用日志"""
    tool_name = ctx.get("tool_name", "unknown")
    duration = ctx.get("duration_ms", 0)
    
    # 记录到日志
    import logging
    logger = logging.getLogger("javis.hooks")
    logger.info(f"[{event}] tool={tool_name} duration={duration}ms result={ctx.get('success', '?')}")
    return HookResult(allowed=True)


async def tool_failure_hook(event: str, ctx: Dict) -> HookResult:
    """PostToolUseFailure: 工具失败时自动重试建议"""
    tool_name = ctx.get("tool_name", "")
    error = ctx.get("error", "")
    
    # 自动重试建议规则
    retry_hints = {
        "timeout": "Consider increasing timeout or splitting the operation",
        "connection": "Check network connectivity, retry with backoff",
        "permission": "Check tool permissions and try with elevated access",
    }
    
    hint = ""
    for key, value in retry_hints.items():
        if key in str(error).lower():
            hint = value
            break
    
    return HookResult(allowed=True, message=hint, data={"retry_suggested": bool(hint)})


async def permission_request_hook(event: str, ctx: Dict) -> HookResult:
    """PermissionRequest: 权限请求处理"""
    required_level = ctx.get("required_level", "user")
    current_level = ctx.get("current_level", "user")
    
    levels = {"guest": 0, "user": 1, "admin": 2, "superadmin": 3}
    if levels.get(current_level, 0) >= levels.get(required_level, 0):
        return HookResult(allowed=True)
    
    return HookResult(allowed=False,
                      message=f"Permission denied: need {required_level}, have {current_level}")


async def subagent_guard_hook(event: str, ctx: Dict) -> HookResult:
    """SubagentStart: 检查子代理是否在白名单"""
    if event == HookEvent.SUBAGENT_START.value:
        agent_name = ctx.get("agent_name", "")
        allowed_agents = ctx.get("allowed_agents", [])
        if allowed_agents and agent_name not in allowed_agents:
            return HookResult(allowed=False,
                            message=f"Subagent '{agent_name}' not in allowed list")
    return HookResult(allowed=True)


# ---------- 工厂函数 ----------

def create_default_hook_manager() -> HookManager:
    """创建带默认Hooks的HookManager"""
    mgr = HookManager()
    
    # 注册默认 hooks
    mgr.on(HookEvent.PRE_TOOL_USE, tool_permission_hook)
    mgr.on(HookEvent.POST_TOOL_USE, tool_logger_hook)
    mgr.on(HookEvent.POST_TOOL_USE_FAILURE, tool_failure_hook)
    mgr.on(HookEvent.PERMISSION_REQUEST, permission_request_hook)
    mgr.on(HookEvent.SUBAGENT_START, subagent_guard_hook)
    
    return mgr


# 全局单例
_default_hook_manager: Optional[HookManager] = None


def get_hook_manager() -> HookManager:
    global _default_hook_manager
    if _default_hook_manager is None:
        _default_hook_manager = create_default_hook_manager()
    return _default_hook_manager


def register_in_manifest(reg):
    """Register hook tools"""
    from core.tool_registry import ToolDef
    mgr = get_hook_manager()

    async def list_hooks(args):
        stats = mgr.get_stats()
        return {"success": True, "events": list(HookEvent.__members__.keys()), "stats": stats}

    async def trigger_hook(args):
        event_name = args.get("event", "")
        context = args.get("context", {})
        try:
            event = HookEvent(event_name)
            result = await mgr.trigger(event, context)
            return {"success": True, "allowed": result.allowed, "message": result.message}
        except (ValueError, KeyError):
            return {"success": False, "error": f"Unknown event: {event_name}"}

    async def hook_stats(args):
        return {"success": True, "stats": mgr.get_stats()}

    reg.register_many([
        ToolDef("hook_list", "List all 10 hook event types", {"type":"object","properties":{},"required":[]}, list_hooks, "hook"),
        ToolDef("hook_trigger", "Trigger a hook event", {"type":"object","properties":{"event":{"type":"string"},"context":{"type":"object"}},"required":["event"]}, trigger_hook, "hook"),
        ToolDef("hook_stats", "Get hook event statistics", {"type":"object","properties":{},"required":[]}, hook_stats, "hook"),
    ])
