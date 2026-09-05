"""Runtime assembly for the Javis kernel.

This module is intentionally side-effect light: importing it must not boot the
assistant. Call ``create_runtime`` to assemble the runtime explicitly.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import platform
import pkgutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.agent_runs import AgentRunStore
from core.agent import Agent
from core.conversation_hub import ConversationHub
from core.conversation_store import ConversationStore
from core.engine import InferenceEngine
from core.events import EventBus
from core.llm_client import LLMClient
from core.life.paths import resolve_data_root
from core.life.l8.layout import ContinuityLayout
from core.life.l7.supervisor import L7Supervisor
from core.life.memory.access import AccessContextFactory, MemoryServiceAccessView
from core.life.memory.service import MemoryService
from core.life.service import LifeService
from core.middleware import MiddlewarePipeline
from core.skill_catalog import SkillCatalog, SkillGovernanceError
from core.subsystem import SubsystemStatus
from core.tool_catalog import ToolCatalog
from core.tool_registry import ToolRegistry
from knowledge.brain import Brain


logger = logging.getLogger("jarvis.runtime")


@dataclass
class JarvisRuntime:
    """Owns the core objects shared by API, WebSocket, and future subsystems."""

    root: Path
    data_root: Path
    continuity_layout: ContinuityLayout
    startup_side_effects: bool
    brain: Brain
    learner: Any | None
    registry: ToolRegistry
    llm: LLMClient
    engine: InferenceEngine
    agent: Agent
    event_bus: EventBus
    middleware: MiddlewarePipeline
    tool_catalog: ToolCatalog
    skill_catalog: SkillCatalog
    agent_runs: AgentRunStore
    conversation_store: ConversationStore
    conversation_hub: ConversationHub
    life: LifeService
    memory_service: MemoryService
    l7: L7Supervisor
    memory_access_factory: AccessContextFactory | None = None
    subsystems: dict[str, Any] = field(default_factory=dict)
    event_store: Any | None = None
    skill_list: list[dict[str, Any]] = field(default_factory=list)
    current_skill: str = "全功能"

    def register_always_on_tools(self) -> None:
        from tools.manifest import register_agent_tools, register_task_tools, register_web_tools

        register_agent_tools(self.registry)
        register_task_tools(self.registry)
        register_web_tools(self.registry)
        self._register_catalog_tools()

    def _register_catalog_tools(self) -> None:
        from core.tool_registry import ToolDef
        from core.tool_result import ToolResult

        def search_tools(
            query: str,
            category: str = "",
            max_risk: str = "",
            limit: int = 12,
        ) -> ToolResult:
            try:
                if category:
                    tools = self.tool_catalog.list_tools(
                        categories=(category,),
                        max_risk=max_risk or None,
                    )[:max(1, min(int(limit), 50))]
                else:
                    tools = self.tool_catalog.search(
                        query,
                        max_risk=max_risk or None,
                        limit=max(1, min(int(limit), 50)),
                    )
                return ToolResult.success(json.dumps({"tools": tools}, ensure_ascii=False))
            except (KeyError, TypeError, ValueError) as exc:
                return ToolResult.failure(str(exc))

        def inspect_tool(name: str) -> ToolResult:
            tool = self.tool_catalog.inspect(name)
            if tool is None:
                return ToolResult.failure(f"Unknown tool: {name}")
            return ToolResult.success(json.dumps(tool, ensure_ascii=False))

        self.registry.register_many([
            ToolDef(
                "tool_search",
                "Search the internal Javis tool catalog by task, category, tags, and risk",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "category": {"type": "string", "default": ""},
                        "max_risk": {
                            "type": "string",
                            "enum": ["", "safe", "low", "medium", "dangerous", "critical"],
                            "default": "",
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 12},
                    },
                    "required": ["query"],
                },
                search_tools,
                "catalog",
                tags=("discover", "tools", "internal"),
                source="javis.kernel",
                risk="safe",
                timeout_seconds=2,
            ),
            ToolDef(
                "tool_inspect",
                "Inspect one internal Javis tool and return its full parameter schema",
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                inspect_tool,
                "catalog",
                tags=("inspect", "schema", "internal"),
                source="javis.kernel",
                risk="safe",
                timeout_seconds=2,
            ),
        ])

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
        existing = self.subsystems.get(name)
        if existing is not None and existing is not subsystem:
            raise RuntimeError(f"subsystem {name!r} is already registered")
        self.subsystems[name] = subsystem
        start = getattr(subsystem, "start", None)
        try:
            if callable(start):
                start(self)
        except BaseException:
            if existing is None and self.subsystems.get(name) is subsystem:
                self.subsystems.pop(name, None)
            raise
        self.event_bus.publish(
            "subsystem.registered",
            {"name": name},
            source="runtime",
        )

    def get_runtime_status(self) -> dict[str, Any]:
        events = self.event_bus.history()
        skill_stats = self.skill_catalog.stats()
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
            "tool_count": self.registry.count,
            "skill": self.current_skill,
            "skill_count": skill_stats["total"],
            "operational_skill_count": len(self.skill_list),
            "subsystems": sorted(self.subsystems),
            "subsystem_status": subsystem_status,
            "event_count": len(events),
            "recent_events": [event.type for event in events[-20:]],
            "event_store": self._event_store_status(),
            "catalogs": {
                "tools": {"count": self.registry.count},
                "skills": skill_stats,
            },
            "agent_runs": self.agent_runs.stats(),
            "conversations": {
                **self.conversation_store.stats(),
                **self.conversation_hub.stats(),
            },
            "memory": self.memory_service.status(),
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
        try:
            self.memory_service.shutdown(timeout=5.0, drain=True)
        except Exception as exc:
            logger.debug("Memory service shutdown skipped: %s", exc)
        closed: set[int] = set()
        for resource in (
            self.event_store,
            self.conversation_store,
            self.agent_runs,
            self.skill_catalog,
        ):
            if resource is None or id(resource) in closed:
                continue
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.debug("Runtime resource close skipped: %s", exc)
            closed.add(id(resource))

    async def aclose(self) -> None:
        await self.conversation_hub.shutdown()
        self.close()


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


def _connect_code_exec(registry: ToolRegistry, brain: Brain) -> None:
    try:
        import tools.code_exec as code_exec

        code_exec.REGISTRY = registry
        code_exec.set_brain(brain)
        logger.info("🔗 执行引擎已连接大脑和注册中心")
    except Exception as exc:
        logger.warning("执行引擎初始化跳过: %s", exc)


def _register_extension_tools(registry: ToolRegistry, root: Path) -> None:
    registrations = [
        ("tools.provider_loader", "Provider loader registered"),
        ("core.subagent", "Subagent system registered"),
        ("core.hook_system", "Hook system registered"),
        ("tools.cron_scheduler", "Cron scheduler registered"),
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

    # Source updates and skill mutation are governed by native release continuity
    # and SkillForge. Legacy modules remain importable but are not model-facing.


def _start_background_services() -> None:
    try:
        import threading
        from core.tray import _start_escape_hook

        hook_thread = threading.Thread(target=_start_escape_hook, daemon=True)
        hook_thread.start()
        logger.info("Escape 中断钩子已启动")
    except Exception as exc:
        logger.warning("Escape 钩子未启动: %s", exc)


def create_runtime(
    root: str | Path,
    startup_side_effects: bool = True,
    *,
    data_root: str | Path | None = None,
) -> JarvisRuntime:
    root = Path(root).resolve()
    if (
        data_root is None
        and not os.environ.get("JAVIS_DATA_ROOT", "").strip()
        and not startup_side_effects
    ):
        root_hash = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]
        temp_root = Path(tempfile.gettempdir()).resolve()
        drive_root = Path(root.anchor or temp_root.anchor)
        data_root = drive_root / ".javis-test-data" / "runtime" / root_hash
    resolved_data_root = resolve_data_root(root, explicit=data_root)
    continuity_layout = ContinuityLayout.from_roots(
        resolved_data_root,
        source_root=root,
    ).ensure_directories()
    if startup_side_effects:
        _run_tool_setup()

    previous_auto_flush = os.environ.get("JAVIS_DISABLE_BRAIN_AUTO_FLUSH")
    if not startup_side_effects:
        os.environ["JAVIS_DISABLE_BRAIN_AUTO_FLUSH"] = "1"
    try:
        brain = Brain(data_root=resolved_data_root, read_only=True)
    finally:
        if not startup_side_effects:
            if previous_auto_flush is None:
                os.environ.pop("JAVIS_DISABLE_BRAIN_AUTO_FLUSH", None)
            else:
                os.environ["JAVIS_DISABLE_BRAIN_AUTO_FLUSH"] = previous_auto_flush
    # The legacy Learner is archive-only. Durable learning is owned by
    # ConversationStore projection and MemoryService.
    learner = None
    if not startup_side_effects:
        logger.info("启动副作用已关闭: 跳过知识注入和后台服务")

    event_bus = EventBus()
    middleware = MiddlewarePipeline()
    registry = ToolRegistry(
        permission_level=_get_permission_level(),
        event_bus=event_bus,
        middleware=middleware,
    )
    tool_catalog = ToolCatalog(registry)
    skill_catalog = SkillCatalog(
        resolved_data_root / "skills" / "catalog.sqlite3",
        event_bus=event_bus,
    )
    agent_runs = AgentRunStore(
        resolved_data_root / "agent_runs" / "runs.sqlite3",
        event_bus=event_bus,
    )
    conversation_store = ConversationStore(
        resolved_data_root / "conversations" / "conversations.sqlite3"
    )
    environment_fingerprint = "|".join(
        (
            os.name,
            sys.platform,
            os.environ.get("PROCESSOR_ARCHITECTURE", "").casefold()
            or platform.machine().casefold()
            or "unknown-machine",
            str(root),
        )
    )
    life = LifeService(
        resolved_data_root,
        environment_fingerprint=environment_fingerprint,
    )
    memory_service = MemoryService(
        resolved_data_root,
        conversation_store=conversation_store,
    )
    l7 = L7Supervisor()
    llm = LLMClient(str(root / "config.yaml"))
    engine = InferenceEngine(llm)
    agent = Agent(
        llm,
        registry,
        brain=brain,
        learner=learner,
        engine=engine,
        tool_catalog=tool_catalog,
    )
    agent.set_confirm_handler()
    memory_service.register_prompt_invalidator(agent.invalidate_memory_context)
    conversation_hub = ConversationHub(
        conversation_store,
        agent_runs,
        resolve_confirmation=agent.resolve_confirm,
        event_bus=event_bus,
        terminal_wakeup=memory_service.wake_reconcile,
    )

    runtime = JarvisRuntime(
        root=root,
        data_root=resolved_data_root,
        continuity_layout=continuity_layout,
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
        agent_runs=agent_runs,
        conversation_store=conversation_store,
        conversation_hub=conversation_hub,
        life=life,
        memory_service=memory_service,
        l7=l7,
    )
    runtime.register_always_on_tools()
    _discover_external_skill_imports(runtime)

    if startup_side_effects:
        _connect_code_exec(registry, brain)
        _register_extension_tools(registry, root)
        _start_background_services()
        runtime.discover_skills()
        runtime.load_skill("全功能")

    life.attach_l7_supervisor(l7)
    runtime.register_subsystem(life)
    runtime.register_subsystem(l7)
    life_boot_id = str(life.status().get("boot_id") or "runtime-boot-unavailable")
    memory_service.configure_runtime_identity(
        life_boot_id,
        life.identity_summary().identity_id,
    )
    memory_service.start()
    runtime.memory_access_factory = AccessContextFactory(
        MemoryServiceAccessView(memory_service),
        runtime_boot_id=life_boot_id,
    )
    runtime.event_bus.publish("runtime.created", {"root": str(root)}, source="runtime")
    return runtime


def _discover_external_skill_imports(runtime: JarvisRuntime) -> None:
    external_root = runtime.root / "skills" / "external"
    if not external_root.is_dir():
        return
    for manifest_path in sorted(
        external_root.rglob("PROVENANCE.json"),
        key=lambda value: str(value).casefold(),
    ):
        try:
            runtime.skill_catalog.discover_manifest(manifest_path)
        except (SkillGovernanceError, OSError, json.JSONDecodeError) as exc:
            logger.warning("External skill import rejected (%s): %s", manifest_path, exc)
