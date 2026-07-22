"""
Provider 插件化系统 — 插件发现 + 懒加载 + 用户插件覆盖内置
"""
import os, sys, logging
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

logger = logging.getLogger("provider_loader")

@dataclass
class ProviderProfile:
    name: str
    label: str
    api_base: str
    models: list[str] = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    priority: int = 100
    enabled: bool = True

class ProviderLoader:
    def __init__(self, builtin_dir: str = None, user_dir: str = None):
        self._builtin_dir = builtin_dir or str(Path(__file__).parent / "providers")
        self._user_dir = user_dir or os.path.expanduser("~/.javis/providers")
        self._providers: dict[str, ProviderProfile] = {}
        self._discovered = False

    def discover(self) -> list[ProviderProfile]:
        if self._discovered:
            return list(self._providers.values())
        self._scan_dir(self._builtin_dir, priority_offset=0)
        if os.path.isdir(self._user_dir):
            self._scan_dir(self._user_dir, priority_offset=-10)
        self._discovered = True
        logger.info(f"Provider 发现完成: {len(self._providers)} 个")
        return list(self._providers.values())

    def _scan_dir(self, dir_path: str, priority_offset: int = 0):
        if not os.path.isdir(dir_path):
            return
        for name in os.listdir(dir_path):
            prov_dir = os.path.join(dir_path, name)
            init = os.path.join(prov_dir, "__init__.py")
            if not os.path.isfile(init):
                continue
            try:
                spec = __import__(f"providers.{name}", fromlist=["get_profile"])
                if hasattr(spec, "get_profile"):
                    profile = spec.get_profile()
                    profile.priority += priority_offset
                    self._providers[name] = profile
            except Exception as e:
                logger.warning(f"加载 Provider 失败 [{name}]: {e}")

    def get(self, name: str) -> Optional[ProviderProfile]:
        self.discover()
        return self._providers.get(name)

    def list_all(self) -> list[ProviderProfile]:
        self.discover()
        return sorted(self._providers.values(), key=lambda p: p.priority)

_loader: Optional[ProviderLoader] = None

def get_loader() -> ProviderLoader:
    global _loader
    if _loader is None:
        _loader = ProviderLoader()
    return _loader

def inject_to_brain(brain=None):
    if brain is None:
        return
    loader = get_loader()
    for p in loader.list_all():
        brain.learn_fact(
            f"Provider: {p.label} ({p.name}) — {p.api_base}",
            category="provider.available",
            source="provider_loader",
            priority=3
        )

def register_in_manifest(reg):
    """Register provider tools in manifest"""
    from core.tool_registry import ToolDef
    loader = get_loader()

    async def list_providers(args):
        providers = loader.list_all()
        return {
            "success": True,
            "providers": [{
                "name": p.name, "label": p.label,
                "api_base": p.api_base, "models": p.models,
                "priority": p.priority, "enabled": p.enabled,
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
            }
        return {"success": False, "error": f"Provider not found: {args['name']}"}

    async def refresh_providers(args):
        loader._discovered = False
        loader._providers.clear()
        providers = loader.discover()
        return {"success": True, "count": len(providers)}

    reg.register_many([
        ToolDef("provider_list", "List all available AI providers", {"type":"object","properties":{},"required":[]}, list_providers, "provider"),
        ToolDef("provider_get", "Get details of a specific provider", {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}, get_provider, "provider"),
        ToolDef("provider_refresh", "Re-scan and refresh provider list", {"type":"object","properties":{},"required":[]}, refresh_providers, "provider"),
    ])
