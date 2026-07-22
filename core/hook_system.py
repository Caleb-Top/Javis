"""
Hook 事件系统 — 10种事件 + register/trigger + agent.py 集成
P1-3: Complete hook infrastructure with all 10 event types
"""
import logging, yaml, os, time, asyncio
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("hooks")


class HookEvent(str, Enum):
    """10 种标准 Hook 事件"""
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
    """单个 Hook 触发结果"""
    allowed: bool = True
    message: str = ""
    data: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


HookCallback = Callable[[dict[str, Any]], HookResult]


class HookSystem:
    """事件钩子系统 — 支持同步/异步回调 + YAML 配置"""

    def __init__(self, config_path: str = None):
        self._hooks: dict[HookEvent, list[tuple[str, HookCallback]]] = {
            e: [] for e in HookEvent
        }
        self._config_path = config_path or str(
            Path(__file__).parent.parent / "HOOK.yaml"
        )
        self._stats: dict[HookEvent, dict] = {
            e: {"count": 0, "last_trigger": 0, "blocks": 0}
            for e in HookEvent
        }

    def register(self, event: HookEvent, callback: HookCallback,
                 name: str = "") -> None:
        """注册钩子回调"""
        self._hooks[event].append((name or f"hook_{len(self._hooks[event])}", callback))
        logger.debug(f"Hook 注册: {event.value} [{name}] ({len(self._hooks[event])} handlers)")

    def unregister(self, event: HookEvent, name: str) -> bool:
        """按名称取消注册"""
        for i, (n, _) in enumerate(self._hooks[event]):
            if n == name:
                self._hooks[event].pop(i)
                return True
        return False

    def trigger(self, event: HookEvent, data: dict[str, Any]) -> HookResult:
        """同步触发钩子链 — 返回第一个拒绝的结果"""
        self._stats[event]["count"] += 1
        self._stats[event]["last_trigger"] = time.time()

        for name, cb in self._hooks[event]:
            try:
                result = cb(data)
                if isinstance(result, dict) and "allowed" in result:
                    result = HookResult(**result)
                if not isinstance(result, HookResult):
                    continue
                if not result.allowed:
                    self._stats[event]["blocks"] += 1
                    logger.info(f"Hook 拒绝 [{event.value}] {name}: {result.message}")
                    return result
            except Exception as e:
                logger.error(f"Hook 执行失败 [{event.value}] {name}: {e}")
        return HookResult(allowed=True)

    async def trigger_async(self, event: HookEvent,
                            data: dict[str, Any]) -> HookResult:
        """异步触发钩子链 — 支持 async 回调"""
        self._stats[event]["count"] += 1
        self._stats[event]["last_trigger"] = time.time()

        for name, cb in self._hooks[event]:
            try:
                result = cb(data)
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, dict) and "allowed" in result:
                    result = HookResult(**result)
                if not isinstance(result, HookResult):
                    continue
                if not result.allowed:
                    self._stats[event]["blocks"] += 1
                    logger.info(f"Hook 拒绝 [{event.value}] {name}: {result.message}")
                    return result
            except Exception as e:
                logger.error(f"Hook 执行失败 [{event.value}] {name}: {e}")
        return HookResult(allowed=True)

    def load_from_yaml(self, config_path: str = None) -> int:
        """从 HOOK.yaml 加载钩子配置"""
        path = config_path or self._config_path
        if not os.path.exists(path):
            logger.debug(f"HOOK.yaml 未找到: {path}")
            return 0

        loaded = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            hooks_section = config.get("hooks", {})
            scripts_dir = hooks_section.get("scripts_dir", "gateway/hooks/")
            auto_load = hooks_section.get("auto_load", False)

            if not auto_load:
                return 0

            # 加载 hook 脚本目录
            full_scripts_dir = Path(__file__).parent.parent / scripts_dir
            if full_scripts_dir.is_dir():
                for hook_file in sorted(full_scripts_dir.glob("*.py")):
                    try:
                        import importlib.util
                        spec = importlib.util.spec_from_file_location(
                            f"javis_hook_{hook_file.stem}", str(hook_file)
                        )
                        if spec and spec.loader:
                            mod = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(mod)
                            if hasattr(mod, "HOOKS"):
                                for hook_entry in mod.HOOKS:
                                    event = HookEvent(hook_entry["event"])
                                    cb = hook_entry["callback"]
                                    self.register(event, cb, hook_file.stem)
                                    loaded += 1
                    except Exception as e:
                        logger.warning(f"Hook 脚本加载失败 [{hook_file.name}]: {e}")

            logger.info(f"HOOK.yaml 加载完成: {loaded} handlers")
        except Exception as e:
            logger.error(f"HOOK.yaml 加载失败: {e}")

        return loaded

    def get_handlers_count(self, event: HookEvent) -> int:
        return len(self._hooks.get(event, []))

    def list_events(self) -> list[dict]:
        """列出所有事件及其处理程序数"""
        return [
            {
                "event": e.value,
                "handlers": len(self._hooks[e]),
                "triggers": self._stats[e]["count"],
                "blocks": self._stats[e]["blocks"],
                "last_trigger": self._stats[e]["last_trigger"],
            }
            for e in HookEvent
        ]

    def get_stats(self) -> dict:
        total_triggers = sum(s["count"] for s in self._stats.values())
        total_blocks = sum(s["blocks"] for s in self._stats.values())
        return {
            "total_events": len(HookEvent),
            "total_handlers": sum(len(v) for v in self._hooks.values()),
            "total_triggers": total_triggers,
            "total_blocks": total_blocks,
            "events": self.list_events(),
        }


class HookManager:
    """Hook 管理器 — agent.py 使用的兼容接口"""

    def __init__(self):
        self._system = get_hook_system()

    async def trigger(self, event: HookEvent, data: dict[str, Any]) -> HookResult:
        return await self._system.trigger_async(event, data)

    def register(self, event: HookEvent, callback: HookCallback,
                 name: str = "") -> None:
        self._system.register(event, callback, name)

    def load_config(self, path: str = None) -> int:
        return self._system.load_from_yaml(path)


# 全局单例
_hook_system: Optional[HookSystem] = None
_hook_manager: Optional[HookManager] = None


def get_hook_system() -> HookSystem:
    global _hook_system
    if _hook_system is None:
        _hook_system = HookSystem()
    return _hook_system


def get_hook_manager() -> HookManager:
    """agent.py 使用的兼容接口"""
    global _hook_manager
    if _hook_manager is None:
        _hook_manager = HookManager()
    return _hook_manager


def register_in_manifest(reg):
    """注册 Hook 工具到 manifest"""
    from core.tool_registry import ToolDef
    hooks = get_hook_system()

    async def hook_list(args):
        events = hooks.list_events()
        return {"success": True, "events": events, "total_events": len(events)}

    async def hook_status(args):
        stats = hooks.get_stats()
        return {"success": True, **stats}

    async def hook_trigger(args):
        event_name = args.get("event", "")
        try:
            event = HookEvent(event_name)
        except ValueError:
            return {"success": False, "error": f"Unknown event: {event_name}. "
                       f"Valid: {[e.value for e in HookEvent]}"}
        result = hooks.trigger(event, args.get("data", {}))
        return {
            "success": True, "allowed": result.allowed,
            "message": result.message, "data": result.data,
        }

    async def hook_load_config(args):
        config_path = args.get("config_path")
        count = hooks.load_from_yaml(config_path)
        return {"success": True, "handlers_loaded": count}

    async def hook_register(args):
        event_name = args.get("event", "")
        try:
            event = HookEvent(event_name)
        except ValueError:
            return {"success": False, "error": f"Unknown event: {event_name}"}
        return {
            "success": True,
            "message": "Use Python API: get_hook_system().register(event, callback)",
        }

    reg.register_many([
        ToolDef("hook_list", "列出所有 Hook 事件及处理程序数",
                {"type":"object","properties":{},"required":[]},
                hook_list, "hooks"),
        ToolDef("hook_status", "获取 Hook 系统状态和统计",
                {"type":"object","properties":{},"required":[]},
                hook_status, "hooks"),
        ToolDef("hook_trigger", "手动触发一个 Hook 事件",
                {"type":"object","properties":{
                    "event":{"type":"string","description":"事件名: PreToolUse/PostToolUse/...等10种"},
                    "data":{"type":"object","default":{}}
                },"required":["event"]},
                hook_trigger, "hooks"),
        ToolDef("hook_load_config", "从 HOOK.yaml 重新加载配置",
                {"type":"object","properties":{"config_path":{"type":"string"}},"required":[]},
                hook_load_config, "hooks"),
        ToolDef("hook_register_programmatic", "程序化注册钩子（需用 Python API）",
                {"type":"object","properties":{"event":{"type":"string"}},"required":["event"]},
                hook_register, "hooks"),
    ])
