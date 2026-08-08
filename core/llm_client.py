"""LLM 客户端"""
import os, json, logging, asyncio
from dataclasses import dataclass, field
from utils.config_api import resolve_model_route, load_config as lcfg
logger = logging.getLogger("llm")
DEFAULT_SYSTEM = "你是 Javis，桌面智能助手。中文回复。"

@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    reasoning_content: str = ""


class LLMClient:
    def __init__(self, config_path: str = "config.yaml"):
        self._config_path = config_path
        self.route_name = "live"
        self._init_client(self.route_name)

    @staticmethod
    def _has_usable_key(api_key: str) -> bool:
        if not api_key:
            return False
        lowered = api_key.strip().lower()
        return lowered not in {"your_api_key_here", "sk-xxx", "none", "null"}

    def _init_client(self, route_name: str = "live"):
        """读取配置并初始化 API 客户端（__init__ 和 reload 共用）"""
        self.config = lcfg()
        cfg = self.config.get("model", {})
        self.route_name = "code" if str(route_name).strip().lower() == "code" else "live"
        route = resolve_model_route(self.config, self.route_name)
        remote_route = route.get("remote", {})
        local_route = route.get("local", {})
        self.provider = "local" if route.get("source") == "local" else remote_route.get("provider", "deepseek")
        self.temperature = cfg.get("temperature", 0.7)
        self.top_p = cfg.get("top_p", 0.9)
        self.max_tokens = cfg.get("max_tokens", 4096)

        if self.provider == "local":
            lc = cfg.get("local", {})
            self.model = local_route.get("model") or lc.get("name") or cfg.get("name", "deepseek-r1:8b")
            self.base_url = local_route.get("base_url") or lc.get("base_url", "http://localhost:11434/v1")
            api_key = lc.get("api_key", "ollama")
        elif self.provider == "anthropic":
            ac = cfg.get("anthropic", {})
            self.model = remote_route.get("model") or ac.get("name", cfg.get("name"))
            api_key = os.getenv("ANTHROPIC_API_KEY") or ac.get("api_key", "")
            self.base_url = ""
        else:
            pc = cfg.get(self.provider, {})
            self.model = remote_route.get("model") or pc.get("name", cfg.get("name", ""))
            self.base_url = remote_route.get("base_url") or pc.get("base_url", "")
            # 优先级: 环境变量 > config.yaml (环境变量更安全)
            api_key = os.getenv(f"{self.provider.upper()}_API_KEY") or pc.get("api_key", "")

        if self.provider == "anthropic":
            if not self._has_usable_key(api_key):
                self._client = None
                self._api_ready = False
                logger.warning("Anthropic API key missing; LLM client started in not-ready state")
                return
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=api_key)
            self._api_ready = True
        else:
            if self.provider != "local" and not self._has_usable_key(api_key):
                self._client = None
                self._api_ready = False
                logger.warning(f"{self.provider} API key missing; LLM client started in not-ready state")
                return
            try:
                from openai import AsyncOpenAI
            except ModuleNotFoundError:
                self._client = None
                self._api_ready = False
                logger.warning("OpenAI-compatible client dependency is unavailable; LLM client started in not-ready state")
                return
            kwargs = {"api_key": api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
            self._api_ready = True

    @property
    def is_ready(self): return self._api_ready

    def reload(self, route_name: str | None = None):
        self._init_client(route_name or self.route_name)
        logger.info(f"配置已重载: {self.route_name} -> {self.provider}/{self.model}")

    def use_route(self, route_name: str):
        """Apply the effective Live or Code route before a serialized request."""
        normalized = "code" if str(route_name).strip().lower() == "code" else "live"
        self._init_client(normalized)
        logger.info("推理路由: %s -> %s/%s", normalized, self.provider, self.model)

    async def chat_with_tools(self, messages, tools, system=DEFAULT_SYSTEM):
        if not self.is_ready:
            raise RuntimeError(f"{self.provider} provider is not configured. Set an API key or switch to local mode.")
        await self._ensure_connected()
        if self.provider == "anthropic":
            return await self._chat_anthropic(messages, tools, system)
        try:
            return await self._chat_openai(messages, tools, system)
        except Exception as e:
            if "invalid tool" in str(e).lower() or "400" in str(e):
                return await self._chat_openai(messages, [], system)
            raise

    async def chat_brief(self, messages, system=DEFAULT_SYSTEM, max_tokens=256):
        """Low-latency conversation path without tool schemas or long reasoning."""
        if not self.is_ready:
            raise RuntimeError(f"{self.provider} provider is not configured.")
        await self._ensure_connected()
        if self.provider != "local":
            return await self._chat_openai(messages, [], system, max_tokens=max_tokens)

        import httpx

        native_base = self.base_url.rstrip("/")
        if native_base.endswith("/v1"):
            native_base = native_base[:-3]
        def clean_template_content(value):
            text = str(value or "")
            for token in ("<｜User｜>", "<｜Assistant｜>", "<｜end▁of▁sentence｜>"):
                text = text.replace(token, "")
            return text

        prompt_parts = [clean_template_content(system)]
        for message in messages[-12:]:
            role = str(message.get("role", "user"))
            content = clean_template_content(message.get("content", ""))
            if not content:
                continue
            if role == "user":
                prompt_parts.extend(("<｜User｜>", content, "<｜Assistant｜>"))
            elif role == "assistant":
                prompt_parts.extend((content, "<｜end▁of▁sentence｜>"))
        # DeepSeek-R1 on Ollama may ignore think=false. Closing the model's
        # reasoning segment in the native template starts the final answer directly.
        prompt_parts.append("</think>\n")
        payload = {
            "model": self.model,
            "prompt": "".join(prompt_parts),
            "raw": True,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "temperature": min(float(self.temperature), 0.35),
                "top_p": min(float(self.top_p), 0.9),
                "num_ctx": 2048,
                "num_predict": max(48, min(int(max_tokens), 128)),
                "stop": ["<｜end▁of▁sentence｜>"],
            },
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(f"{native_base}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
        text = str(data.get("response", "") or "").strip()
        if not text:
            raise RuntimeError("Local brief chat returned no final content")
        return LLMResponse(text=text)

    async def _chat_openai(self, messages, tools, system, max_tokens=None):
        processed = []
        for msg in messages:
            m = dict(msg)
            c = m.get("content", "")
            if isinstance(c, list):
                texts = [p for p in c if isinstance(p, dict) and p.get("type") == "text"]
                m["content"] = texts[0].get("text", "") if texts else ""
            elif isinstance(c, str) and c.strip().startswith("["):
                try:
                    parsed = json.loads(c)
                    if isinstance(parsed, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in parsed):
                        texts = [p.get("text", "") for p in parsed if isinstance(p, dict) and p.get("type") == "text"]
                        m["content"] = texts[0] if texts else ""
                except (json.JSONDecodeError, TypeError):
                    pass  # 普通文本内容包含 '[' 开头，不做处理
            if m.get("role") == "assistant":
                if m.get("content") is None:
                    m["content"] = None
            processed.append(m)

        full = [{"role": "system", "content": system}] + processed
        schemas = [{"type": "function", "function": t["function"]} for t in tools] if tools else None
        resp = await self._client.chat.completions.create(
            model=self.model, messages=full, temperature=self.temperature,
            top_p=self.top_p, max_tokens=max_tokens or self.max_tokens,
            tools=schemas, tool_choice="auto" if schemas else None)
        choice = resp.choices[0].message

        tcs = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                try: params = json.loads(tc.function.arguments)
                except: params = {}
                tcs.append({"id": tc.id, "name": tc.function.name, "params": params})

        reasoning = getattr(choice, 'reasoning_content', '') or ''
        text = ""
        if choice.content: text = choice.content
        elif not choice.tool_calls and reasoning: text = reasoning
        return LLMResponse(text=text, tool_calls=tcs, reasoning_content=reasoning)

    async def _chat_anthropic(self, messages, tools, system):
        ams = []
        for msg in messages:
            if msg["role"] == "assistant" and msg.get("content") is None: continue
            if msg["role"] == "tool":
                ams.append({"role": "user", "content": f"[工具结果] {msg['content']}"})
            elif msg["role"] == "user":
                ams.append({"role": "user", "content": msg["content"]})
            elif msg["role"] == "assistant":
                ams.append({"role": "assistant", "content": msg.get("content", "")})
        resp = await self._client.messages.create(
            model=self.model, system=system, messages=ams,
            max_tokens=self.max_tokens, temperature=self.temperature)
        return LLMResponse(text=resp.content[0].text if resp.content else "")

    def switch_provider(self, provider: str, model: str = "", base_url: str = ""):
        """运行时切换 provider (云 ↔ 本地) — 避免 engine 直接修改私有 _client"""
        if provider == "local" and model:
            self.provider = "local"
            self.model = model
            self.base_url = base_url or "http://localhost:11434/v1"
            try:
                from openai import AsyncOpenAI
            except ModuleNotFoundError:
                self._client = None
                self._api_ready = False
                logger.warning("OpenAI-compatible client dependency is unavailable; provider switch deferred")
                return
            self._client = AsyncOpenAI(api_key="ollama", base_url=self.base_url)
            self._api_ready = True
            logger.info(f"切换到本地: {model}")
        else:
            self.reload()

    def update_param(self, k, v):
        if hasattr(self, k): setattr(self, k, v)

    def get_params(self):
        return {"provider": self.provider, "model": self.model,
                "temperature": self.temperature, "top_p": self.top_p,
                "max_tokens": self.max_tokens}

    async def _ensure_connected(self, retries=10, delay=3):
        if self.provider != "local": return
        import httpx
        native_base = str(self.base_url or "http://localhost:11434/v1").rstrip("/")
        if native_base.endswith("/v1"):
            native_base = native_base[:-3]
        for i in range(retries):
            try:
                async with httpx.AsyncClient() as c:
                    r = await c.get(f"{native_base}/api/tags", timeout=3)
                    if r.status_code == 200: return
            except Exception:
                pass  # Ollama 尚未启动，继续等待
            await asyncio.sleep(delay)
        raise ConnectionError("Ollama 无法连接")
