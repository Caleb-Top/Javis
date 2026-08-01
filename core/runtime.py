"""Runtime assembly for the Javis kernel.

This module is intentionally side-effect light: importing it must not boot the
assistant. Call ``create_runtime`` to assemble the runtime explicitly.
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.agent import Agent
from core.engine import InferenceEngine
from core.events import EventBus
from core.llm_client import LLMClient
from core.middleware import MiddlewarePipeline
from core.skill_catalog import SkillCatalog
from core.subsystem import SubsystemStatus
from core.tool_catalog import ToolCatalog
from core.tool_registry import ToolRegistry
from knowledge.brain import Brain
from knowledge.learner import Learner


logger = logging.getLogger("jarvis.runtime")


@dataclass
class JarvisRuntime:
    """Owns the core objects shared by API, WebSocket, and future subsystems."""

    root: Path
    startup_side_effects: bool
    brain: Brain
    learner: Learner
    registry: ToolRegistry
    llm: LLMClient
    engine: InferenceEngine
    agent: Agent
    event_bus: EventBus
    middleware: MiddlewarePipeline
    tool_catalog: ToolCatalog
    skill_catalog: SkillCatalog
    subsystems: dict[str, Any] = field(default_factory=dict)
    event_store: Any | None = None
    skill_list: list[dict[str, Any]] = field(default_factory=list)
    current_skill: str = "全功能"

    def register_always_on_tools(self) -> None:
        from tools.manifest import register_agent_tools, register_task_tools, register_web_tools

        register_agent_tools(self.registry)
        register_task_tools(self.registry)
        register_web_tools(self.registry)

    def discover_skills(self) -> list[dict[str, Any]]:
        import skills as skills_pkg

        discovered: list[dict[str, Any]] = []
        for module_info in pkgutil.iter_modules(skills_pkg.__path__):
            module = importlib.import_module(f"skills.{module_info.name}")
            discovered.append({
                "id": module_info.name,
                "name": getattr(module, "SKILL_NAME", module_info.name),
                "icon": getattr(module, "SKILL_ICON", "🔧"),
                "desc": getattr(module, "SKILL_DESC", ""),
            })
        self.skill_list = discovered
        return discovered

    def load_skill(self, skill_id: str) -> int:
        self.registry.clear()
        try:
            module = importlib.import_module(f"skills.{skill_id}")
            module.register(self.registry)
            self.register_always_on_tools()
            self.current_skill = skill_id
            self.agent.tools = self.registry
            return self.registry.count
        except Exception as exc:
            logger.error("技能%s:%s", skill_id, exc)
            self.register_always_on_tools()
            return 0

    def sync_permission(self, permission_level: str) -> None:
        self.registry.set_permission_level(permission_level)
        if hasattr(self.agent, "_permission_level"):
            self.agent._permission_level = permission_level
        self.event_bus.publish(
            "permission.changed",
            {"permission": permission_level},
            source="runtime",
        )

    def register_event_store(self, store: Any) -> None:
        self.event_store = store
        attach = getattr(store, "attach", None)
        if callable(attach):
            attach(self.event_bus)
        self.event_bus.publish(
            "event_store.registered",
            {"path": str(getattr(store, "path", ""))},
            source="runtime",
        )

    def register_subsystem(self, subsystem: Any) -> None:
        name = getattr(subsystem, "name", subsystem.__class__.__name__)
        self.subsystems[name] = subsystem
        start = getattr(subsystem, "start", None)
        if callable(start):
            start(self)
        self.event_bus.publish(
            "subsystem.registered",
            {"name": name},
            source="runtime",
        )

    def get_runtime_status(self) -> dict[str, Any]:
        events = self.event_bus.history()
        subsystem_status: dict[str, Any] = {}
        for name, subsystem in sorted(self.subsystems.items()):
            status = getattr(subsystem, "status", None)
            if not callable(status):
                subsystem_status[name] = SubsystemStatus(state="unknown", detail="no status method").to_dict()
                continue
            try:
                value = status()
                subsystem_status[name] = value.to_dict() if hasattr(value, "to_dict") else value
            except Exception as exc:
                subsystem_status[name] = SubsystemStatus(state="degraded", detail=str(exc)[:200]).to_dict()
        return {
            "ok": True,
            "root": str(self.root),
            "startup_side_effects": self.startup_side_effects,
            "model": self.llm.model,
            "tools": self.registry.count,
            "skill": self.current_skill,
            "skill_count": len(self.skill_list),
            "subsystems": sorted(self.subsystems),
            "subsystem_status": subsystem_status,
            "event_count": len(events),
            "recent_events": [event.type for event in events[-20:]],
            "event_store": self._event_store_status(),
        }

    def _event_store_status(self) -> dict[str, Any]:
        if self.event_store is None:
            return {"state": "disabled", "events": 0}
        status = getattr(self.event_store, "status", None)
        if callable(status):
            return status()
        return {"state": "unknown", "events": 0}

    def close(self) -> None:
        """Release owned subsystem and persistence resources."""
        for subsystem in reversed(list(self.subsystems.values())):
            stop = getattr(subsystem, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:
                    logger.debug("Subsystem stop skipped: %s", exc)
        closed: set[int] = set()
        for resource in (self.event_store, self.skill_catalog):
            if resource is None or id(resource) in closed:
                continue
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.debug("Runtime resource close skipped: %s", exc)
            closed.add(id(resource))


def _get_permission_level() -> str:
    try:
        from utils.config_api import get_permission_level

        return get_permission_level()
    except Exception:
        return "full_access"


def _run_tool_setup() -> None:
    try:
        from tools.setup import setup

        for name, status in setup().items():
            logger.info("工具初始化 %s: %s", name, status)
    except Exception as exc:
        logger.warning("工具路径初始化跳过: %s", exc)


def _inject_startup_knowledge(brain: Brain) -> None:
    from knowledge.human_knowledge import inject_to_brain as inject_human
    from knowledge.papers_db import ingest_to_brain

    try:
        brain.compress()
    except Exception as exc:
        logger.debug("大脑压缩跳过:%s", exc)
    try:
        ingest_to_brain(brain)
        logger.info("论文知识已注入大脑")
    except Exception as exc:
        logger.warning("论文注入跳过:%s", exc)
    try:
        count = inject_human(brain)
        logger.info("人类文明知识已注入:%s条", count)
    except Exception as exc:
        logger.warning("人类知识注入跳过:%s", exc)


def _connect_code_exec(registry: ToolRegistry, brain: Brain) -> None:
    try:
        import tools.code_exec as code_exec

        code_exec.REGISTRY = registry
        code_exec.set_brain(brain)
        logger.info("🔗 执行引擎已连接大脑和注册中心")
    except Exception as exc:
        logger.warning("执行引擎初始化跳过: %s", exc)


def _inject_runtime_facts(brain: Brain) -> None:
    try:
        brain.learn_fact(
            "用户风格: 自然口语，先结论后数据，不读原始数据行，短句优先",
            category="user_style.base",
            source="user_feedback",
            priority=5,
        )
        brain.learn_fact(
            "规则: 工具原始数据不能复读，用自己的话重新组织",
            category="user_style.rule.no_repeat_data",
            source="user_feedback",
            priority=5,
        )
        logger.info("用户风格初始化完成")
    except Exception as exc:
        logger.warning("用户风格初始化跳过: %s", exc)

    injectors = [
        ("tools_lib.tool_superpowers", "Superpowers 技能已注入大脑"),
        ("tools_lib.tool_plugin_creator", "Plugin Creator 技能已注入大脑"),
        ("tools_lib.tool_anthropic_plugins", "Anthropic 插件库已注入大脑"),
        ("tools_lib.tool_catch2", "Catch2 C++ 测试已注入大脑"),
    ]
    for module_name, message in injectors:
        try:
            inject_to_brain = getattr(importlib.import_module(module_name), "inject_to_brain")
            inject_to_brain(brain)
            logger.info(message)
        except Exception as exc:
            logger.warning("%s 注入跳过: %s", module_name, exc)


def _register_extension_tools(registry: ToolRegistry, root: Path) -> None:
    registrations = [
        ("tools.provider_loader", "Provider loader registered"),
        ("core.subagent", "Subagent system registered"),
        ("core.hook_system", "Hook system registered"),
        ("tools.cron_scheduler", "Cron scheduler registered"),
        ("core.auto_updater", "Auto updater registered"),
        ("tools.sandbox", "Sandbox system registered"),
        ("gateway.gateway_manager", "Gateway manager registered"),
    ]
    for module_name, message in registrations:
        try:
            register = getattr(importlib.import_module(module_name), "register_in_manifest")
            register(registry)
            logger.info(message)
        except Exception as exc:
            logger.warning("%s: %s", module_name, exc)

    try:
        from core.skill_creator import get_skill_creator, register_in_manifest as register_skill_creator

        skill_creator = get_skill_creator(str(root))
        logger.info("Skill creator initialized: %s skills", len(skill_creator.list_skills()))
        register_skill_creator(registry)
        logger.info("Skill creator tools registered")
        from core.skill_manager import get_skill_manager, register_in_manifest as register_skill_manager

        skill_manager = get_skill_manager()
        register_skill_manager(registry)
        logger.info("SkillManager registered: %s skills loaded", len(skill_manager.list_all()))
    except Exception as exc:
        logger.warning("Skill creator: %s", exc)


def _start_background_services(brain: Brain) -> None:
    try:
        from memory.controller import get_controller

        get_controller(brain).start_cycles()
        logger.info("记忆控制器已启动 (循环: 语义5m/压缩10m/摘要30m)")
    except Exception as exc:
        logger.warning("记忆控制器启动跳过: %s", exc)

    try:
        import threading
        from core.tray import _start_escape_hook

        hook_thread = threading.Thread(target=_start_escape_hook, daemon=True)
        hook_thread.start()
        logger.info("Escape 中断钩子已启动")
    except Exception as exc:
        logger.warning("Escape 钩子未启动: %s", exc)


def create_runtime(root: str | Path, startup_side_effects: bool = True) -> JarvisRuntime:
    root = Path(root).resolve()
    if startup_side_effects:
        _run_tool_setup()

    previous_auto_flush = os.environ.get("JAVIS_DISABLE_BRAIN_AUTO_FLUSH")
    if not startup_side_effects:
        os.environ["JAVIS_DISABLE_BRAIN_AUTO_FLUSH"] = "1"
    try:
        brain = Brain()
    finally:
        if not startup_side_effects:
            if previous_auto_flush is None:
                os.environ.pop("JAVIS_DISABLE_BRAIN_AUTO_FLUSH", None)
            else:
                os.environ["JAVIS_DISABLE_BRAIN_AUTO_FLUSH"] = previous_auto_flush
    learner = Learner(brain=brain)
    if startup_side_effects:
        _inject_startup_knowledge(brain)
    else:
        logger.info("启动副作用已关闭: 跳过知识注入和后台服务")

    event_bus = EventBus()
    middleware = MiddlewarePipeline()
    registry = ToolRegistry(
        permission_level=_get_permission_level(),
        event_bus=event_bus,
        middleware=middleware,
    )
    tool_catalog = ToolCatalog(registry)
    skill_catalog = SkillCatalog(root / "data" / "skills" / "catalog.sqlite3", event_bus=event_bus)
    llm = LLMClient(str(root / "config.yaml"))
    engine = InferenceEngine(llm)
    agent = Agent(llm, registry, brain=brain, learner=learner, engine=engine)
    agent.set_confirm_handler()

    runtime = JarvisRuntime(
        root=root,
        startup_side_effects=startup_side_effects,
        brain=brain,
        learner=learner,
        registry=registry,
        llm=llm,
        engine=engine,
        agent=agent,
        event_bus=event_bus,
        middleware=middleware,
        tool_catalog=tool_catalog,
        skill_catalog=skill_catalog,
    )
    runtime.event_bus.publish("runtime.created", {"root": str(root)}, source="runtime")
    runtime.register_always_on_tools()

    if startup_side_effects:
        _connect_code_exec(registry, brain)
        _inject_runtime_facts(brain)
        _register_extension_tools(registry, root)
        _start_background_services(brain)
        runtime.discover_skills()
        runtime.load_skill("全功能")

    return runtime
