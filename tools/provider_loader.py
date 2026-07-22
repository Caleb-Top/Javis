"""
P1-1: Provider 插件化加载器 — Javis Provider Plugin Manager
支持7个Provider的动态加载、自动降级、健康检查
"""
import os
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    """Provider 配置"""
    name: str
    api_base: str
    api_key_env: str
    models: List[str] = field(default_factory=list)
    priority: int = 10
    enabled: bool = True
    max_retries: int = 3
    timeout: int = 60


class ProviderPlugin:
    """Provider 插件基类"""

    def __init__(self, config: ProviderConfig):
        self.config = config
        self._healthy: bool = True
        self._fail_count: int = 0

    @property
    def api_key(self) -> Optional[str]:
        return os.getenv(self.config.api_key_env)

    @property
    def is_healthy(self) -> bool:
        return self._healthy

    async def health_check(self) -> bool:
        raise NotImplementedError

    async def chat(self, messages: List[Dict], model: str = "", **kwargs) -> Dict:
        raise NotImplementedError

    def mark_failure(self):
        self._fail_count += 1
        if self._fail_count >= self.config.max_retries:
            self._healthy = False

    def mark_success(self):
        self._fail_count = 0
        self._healthy = True


class DeepSeekProvider(ProviderPlugin):
    """DeepSeek Provider"""
    async def health_check(self) -> bool:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with session.get(f"{self.config.api_base}/models", headers=headers, timeout=10) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def chat(self, messages, model="deepseek-chat", **kwargs):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"model": model, "messages": messages, **kwargs}
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            async with session.post(f"{self.config.api_base}/chat/completions", json=payload, headers=headers) as resp:
                return await resp.json()


class OpenAIProvider(ProviderPlugin):
    """OpenAI Provider"""
    async def health_check(self) -> bool:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.api_key}"}
                async with session.get(f"{self.config.api_base}/models", headers=headers, timeout=10) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def chat(self, messages, model="gpt-4o", **kwargs):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"model": model, "messages": messages, **kwargs}
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            async with session.post(f"{self.config.api_base}/chat/completions", json=payload, headers=headers) as resp:
                return await resp.json()


class OllamaProvider(ProviderPlugin):
    """Ollama 本地 Provider"""
    async def health_check(self) -> bool:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.config.api_base}/api/tags", timeout=5) as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def chat(self, messages, model="llama3", **kwargs):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {"model": model, "messages": messages, "stream": False, **kwargs}
            async with session.post(f"{self.config.api_base}/api/chat", json=payload) as resp:
                return await resp.json()


PROVIDER_CLASSES = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "anthropic": ProviderPlugin,
    "ollama": OllamaProvider,
    "glm": ProviderPlugin,
    "kimi": ProviderPlugin,
    "qwen": ProviderPlugin,
}


class ProviderLoader:
    """Provider 插件化加载器 — 加载、健康检查、自动降级"""

    def __init__(self, config_path: str = ""):
        self._providers: Dict[str, ProviderPlugin] = {}
        self._priority_order: List[str] = []
        if config_path:
            self.load_config(config_path)
        else:
            self._load_defaults()

    def _load_defaults(self):
        defaults = [
            ProviderConfig(name="deepseek", api_base="https://api.deepseek.com/v1", api_key_env="DEEPSEEK_API_KEY",
                          models=["deepseek-chat", "deepseek-reasoner"], priority=1),
            ProviderConfig(name="openai", api_base="https://api.openai.com/v1", api_key_env="OPENAI_API_KEY",
                          models=["gpt-4o", "gpt-4o-mini"], priority=2),
            ProviderConfig(name="anthropic", api_base="https://api.anthropic.com/v1", api_key_env="ANTHROPIC_API_KEY",
                          models=["claude-sonnet-4-20250514"], priority=3),
            ProviderConfig(name="qwen", api_base="https://dashscope.aliyuncs.com/compatible-mode/v1", api_key_env="DASHSCOPE_API_KEY",
                          models=["qwen-max", "qwen-plus"], priority=4),
            ProviderConfig(name="glm", api_base="https://open.bigmodel.cn/api/paas/v4", api_key_env="GLM_API_KEY",
                          models=["glm-4-plus"], priority=5),
            ProviderConfig(name="kimi", api_base="https://api.moonshot.cn/v1", api_key_env="MOONSHOT_API_KEY",
                          models=["moonshot-v1-8k"], priority=6),
            ProviderConfig(name="ollama", api_base="http://localhost:11434", api_key_env="",
                          models=["llama3", "qwen2.5"], priority=7),
        ]
        for cfg in defaults:
            self.register_provider(cfg)

    def load_config(self, config_path: str):
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for item in data.get("providers", []):
            cfg = ProviderConfig(**item)
            self.register_provider(cfg)

    def register_provider(self, config: ProviderConfig):
        cls = PROVIDER_CLASSES.get(config.name, ProviderPlugin)
        instance = cls(config)
        self._providers[config.name] = instance
        if config.enabled:
            self._priority_order.append(config.name)
        self._priority_order.sort(key=lambda n: self._providers[n].config.priority)

    def get_active(self, model: str = "") -> Optional[ProviderPlugin]:
        for name in self._priority_order:
            p = self._providers[name]
            if p.is_healthy:
                if not model or model in p.config.models:
                    return p
        return None

    async def fallback_check(self):
        for name in self._priority_order:
            p = self._providers[name]
            try:
                healthy = await p.health_check()
                if healthy:
                    p.mark_success()
                else:
                    p.mark_failure()
            except Exception:
                p.mark_failure()

    def list_providers(self) -> List[Dict[str, Any]]:
        return [{"name": p.config.name, "healthy": p.is_healthy, "models": p.config.models,
                 "priority": p.config.priority, "enabled": p.config.enabled} for p in self._providers.values()]

    async def chat(self, messages: List[Dict], model: str = "", **kwargs):
        errors = []
        for name in self._priority_order:
            p = self._providers[name]
            if not p.is_healthy:
                continue
            if model and model not in p.config.models:
                continue
            try:
                actual_model = model or p.config.models[0]
                result = await p.chat(messages, actual_model, **kwargs)
                p.mark_success()
                return {"provider": name, "result": result}
            except Exception as e:
                p.mark_failure()
                errors.append({"provider": name, "error": str(e)})
                continue
        raise RuntimeError(f"All providers unavailable: {errors}")


_loader: Optional[ProviderLoader] = None

def get_provider_loader() -> ProviderLoader:
    global _loader
    if _loader is None:
        _loader = ProviderLoader()
    return _loader


def register_in_manifest(reg):
    """Register provider tools in the tool registry"""
    from core.tool_registry import ToolDef
    loader = get_provider_loader()

    async def list_providers(args):
        return {"success": True, "providers": loader.list_providers()}

    async def switch_provider(args):
        model = args.get("model", "")
        provider = loader.get_active(model)
        if provider:
            return {"success": True, "active": provider.config.name, "model": model}
        return {"success": False, "error": "No available provider"}

    async def health_check(args):
        await loader.fallback_check()
        return {"success": True, "providers": loader.list_providers()}

    reg.register_many([
        ToolDef("provider_list", "List all AI providers", {"type":"object","properties":{},"required":[]}, list_providers, "provider"),
        ToolDef("provider_switch", "Switch active provider/model", {"type":"object","properties":{"model":{"type":"string","default":""}},"required":[]}, switch_provider, "provider"),
        ToolDef("provider_health", "Health check all providers", {"type":"object","properties":{},"required":[]}, health_check, "provider"),
    ])
