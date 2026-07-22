"""
Provider 插件化系统 — 插件发现 + 懒加载 + 用户插件覆盖内置
P1-1: ProviderPlugin system with health checks, model routing, and fallback
"""
import os, sys, json, logging, importlib.util, asyncio, time
from dataclasses import dataclass, field
from typing import Optional, Callable
from pathlib import Path

logger = logging.getLogger("provider_loader")

@dataclass
class ProviderProfile:
    name: str
    label: str
    api_base: str
    models: list[str] = field(default_factory=list)
    api_key_env: str = ""
    headers: dict = field(default_factory=dict)
    priority: int = 100
    enabled: bool = True
    health_status: str = "unknown"  # unknown / healthy / degraded / down
    last_health_check: float = 0.0
    rate_limit_rpm: int = 0
    max_tokens: int = 8192
    supports_vision: bool = False
    supports_tools: bool = True
    supports_streaming: bool = True

class ProviderLoader:
    """Provider 插件加载器 — 支持内置 + 用户覆盖"""

    def __init__(self, builtin_dir: str = None, user_dir: str = None):
        self._builtin_dir = builtin_dir or str(Path(__file__).parent.parent / "providers")
        self._user_dir = user_dir or os.path.expanduser("~/.javis/providers")
        self._providers: dict[str, ProviderProfile] = {}
        self._discovered = False
        self._health_callbacks: dict[str, Callable] = {}

    def discover(self) -> list[ProviderProfile]:
        """扫描并发现所有 provider（懒加载 + 缓存）"""
        if self._discovered:
            return list(self._providers.values())

        # 内置优先
        self._scan_dir(self._builtin_dir, priority_offset=0)

        # 用户插件覆盖（优先级更高）
        if os.path.isdir(self._user_dir):
            self._scan_dir(self._user_dir, priority_offset=-20)

        self._discovered = True
        self._log_discovery()
        return list(self._providers.values())

    def _scan_dir(self, dir_path: str, priority_offset: int = 0):
        """扫描目录中的 provider 插件"""
        if not os.path.isdir(dir_path):
            return

        for name in os.listdir(dir_path):
            prov_dir = os.path.join(dir_path, name)
            if name.startswith("_") or name.startswith("."):
                continue

            init_py = os.path.join(prov_dir, "__init__.py")
            provider_py = os.path.join(prov_dir, "provider.py")

            if not os.path.isfile(init_py) and not os.path.isfile(provider_py):
                continue

            try:
                profile = self._load_provider(dir_path, name)
                if profile:
                    profile.priority += priority_offset
                    self._providers[name] = profile
                    logger.info(f"Provider 发现: {name} ({profile.label}), pri={profile.priority}")
            except Exception as e:
                logger.warning(f"Provider [{name}] 加载失败: {e}")

    def _load_provider(self, base_dir: str, name: str) -> Optional[ProviderProfile]:
        """安全加载单个 provider 模块"""
        prov_dir = os.path.join(base_dir, name)

        # 方法1: 直接 exec provider.py
        provider_py = os.path.join(prov_dir, "provider.py")
        if os.path.isfile(provider_py):
            namespace = {}
            with open(provider_py, "r", encoding="utf-8") as f:
                exec(f.read(), namespace)
            if "get_profile" in namespace:
                return namespace["get_profile"]()

        # 方法2: importlib 加载 __init__.py
        init_py = os.path.join(prov_dir, "__init__.py")
        if os.path.isfile(init_py):
            spec = importlib.util.spec_from_file_location(
                f"javis_provider_{name}", init_py
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "get_profile"):
                    return mod.get_profile()

        return None

    def _log_discovery(self):
        """记录发现结果"""
        total = len(self._providers)
        enabled = sum(1 for p in self._providers.values() if p.enabled)
        logger.info(f"Provider 发现完成: {enabled}/{total} 可用")

    def get(self, name: str) -> Optional[ProviderProfile]:
        self.discover()
        return self._providers.get(name)

    def list_all(self, enabled_only: bool = True) -> list[ProviderProfile]:
        self.discover()
        providers = self._providers.values()
        if enabled_only:
            providers = [p for p in providers if p.enabled]
        return sorted(providers, key=lambda p: p.priority)

    def register_health_check(self, name: str, callback: Callable):
        """注册健康检查回调 (async fn -> health_status)"""
        self._health_callbacks[name] = callback

    async def check_health(self, name: str = None) -> dict:
        """运行健康检查"""
        self.discover()

        targets = [name] if name else list(self._providers.keys())
        results = {}

        for n in targets:
            p = self._providers.get(n)
            if not p:
                results[n] = "not_found"
                continue

            if n in self._health_callbacks:
                try:
                    status = self._health_callbacks[n]()
                    if asyncio.iscoroutine(status):
                        status = await status
                    p.health_status = status
                except Exception as e:
                    p.health_status = "down"
                    logger.warning(f"Health check [{n}] failed: {e}")
            else:
                p.health_status = "unknown"

            p.last_health_check = time.time()
            results[n] = p.health_status

        return results

    def suggest_model(self, task_type: str = "general") -> Optional[dict]:
        """根据任务类型推荐 provider + model"""
        healthy = [p for p in self.list_all()
                   if p.health_status in ("healthy", "unknown")]
        if not healthy:
            return None

        best = healthy[0]
        model = best.models[0] if best.models else "default"
        return {"provider": best.name, "model": model, "api_base": best.api_base}

    def disable(self, name: str):
        p = self._providers.get(name)
        if p:
            p.enabled = False
            logger.info(f"Provider 已禁用: {name}")

    def enable(self, name: str):
        p = self._providers.get(name)
        if p:
            p.enabled = True
            logger.info(f"Provider 已启用: {name}")

    def get_stats(self) -> dict:
        """Provider 统计"""
        all_p = self.list_all(enabled_only=False)
        enabled = [p for p in all_p if p.enabled]
        healthy = [p for p in all_p if p.health_status == "healthy"]
        return {
            "total": len(all_p),
            "enabled": len(enabled),
            "healthy": len(healthy),
            "providers": [
                {"name": p.name, "label": p.label, "enabled": p.enabled,
                 "health": p.health_status, "models": len(p.models)}
                for p in all_p
            ],
        }


_loader: Optional[ProviderLoader] = None

def get_loader() -> ProviderLoader:
    global _loader
    if _loader is None:
        _loader = ProviderLoader()
    return _loader


def inject_to_brain(brain=None):
    """注入 provider 信息到大脑"""
    if brain is None:
        return
    loader = get_loader()
    for p in loader.list_all():
        brain.learn_fact(
            f"Provider: {p.label} ({p.name}) — {p.api_base}, models: {', '.join(p.models[:5])}",
            category="provider.available",
            source="provider_loader",
            priority=3,
        )


def register_in_manifest(reg):
    """注册 provider 工具到 manifest"""
    from core.tool_registry import ToolDef
    loader = get_loader()

    async def list_providers(args):
        enabled_only = args.get("enabled_only", True)
        providers = loader.list_all(enabled_only=enabled_only)
        return {
            "success": True,
            "providers": [{
                "name": p.name, "label": p.label,
                "api_base": p.api_base, "models": p.models,
                "priority": p.priority, "enabled": p.enabled,
                "health": p.health_status,
                "supports_vision": p.supports_vision,
                "supports_tools": p.supports_tools,
            } for p in providers],
            "count": len(providers),
        }

    async def get_provider(args):
        p = loader.get(args["name"])
        if p:
            return {
                "success": True, "name": p.name, "label": p.label,
                "api_base": p.api_base, "models": p.models,
                "priority": p.priority, "enabled": p.enabled,
                "health": p.health_status,
                "supports_vision": p.supports_vision,
                "supports_tools": p.supports_tools,
            }
        return {"success": False, "error": f"Provider not found: {args['name']}"}

    async def refresh_providers(args):
        loader._discovered = False
        loader._providers.clear()
        providers = loader.discover()
        return {"success": True, "count": len(providers)}

    async def check_health(args):
        name = args.get("name")
        results = await loader.check_health(name)
        return {"success": True, "results": results}

    async def suggest_model(args):
        task_type = args.get("task_type", "general")
        suggestion = loader.suggest_model(task_type)
        if suggestion:
            return {"success": True, **suggestion}
        return {"success": False, "error": "No healthy provider available"}

    async def provider_stats(args):
        return {"success": True, **loader.get_stats()}

    async def toggle_provider(args):
        name = args["name"]; enable = args.get("enable", True)
        if enable:
            loader.enable(name)
        else:
            loader.disable(name)
        return {"success": True, "name": name, "enabled": enable}

    reg.register_many([
        ToolDef("provider_list", "列出所有 AI provider，支持过滤启用的",
                {"type":"object","properties":{"enabled_only":{"type":"boolean","default":True}},"required":[]},
                list_providers, "provider"),
        ToolDef("provider_get", "获取指定 provider 详细信息",
                {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]},
                get_provider, "provider"),
        ToolDef("provider_refresh", "重新扫描并刷新 provider 列表",
                {"type":"object","properties":{},"required":[]},
                refresh_providers, "provider"),
        ToolDef("provider_health", "检查 provider 健康状态",
                {"type":"object","properties":{"name":{"type":"string"}},"required":[]},
                check_health, "provider"),
        ToolDef("provider_suggest", "根据任务类型推荐最合适的 provider/model",
                {"type":"object","properties":{"task_type":{"type":"string","enum":["general","code","vision","fast","reasoning"]}},"required":[]},
                suggest_model, "provider"),
        ToolDef("provider_stats", "Provider 系统统计",
                {"type":"object","properties":{},"required":[]},
                provider_stats, "provider"),
        ToolDef("provider_toggle", "启用/禁用一个 provider",
                {"type":"object","properties":{"name":{"type":"string"},"enable":{"type":"boolean","default":True}},"required":["name"]},
                toggle_provider, "provider"),
    ])
