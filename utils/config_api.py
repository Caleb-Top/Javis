"""配置管理 API"""
import copy, os, base64, json, logging
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

try:
    import yaml
except ModuleNotFoundError:  # Optional for import-only tools and UI tests.
    yaml = None
logger = logging.getLogger("config_api")
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
ENCODED_PREFIX = "b64:"

# 环境变量优先（比 config.yaml 更安全）
ENV_KEY_MAP = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "glm": "GLM_API_KEY",
    "kimi": "KIMI_API_KEY",
    "qwen": "QWEN_API_KEY",
}

CLOUD_PROVIDERS = ("deepseek", "glm", "kimi", "qwen", "openai", "anthropic")
PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "glm": "智谱 GLM",
    "kimi": "Kimi",
    "qwen": "通义千问",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
}
DEFAULT_REMOTE_BASE_URLS = {
    "deepseek": "https://api.deepseek.com/v1",
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "kimi": "https://api.moonshot.cn/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}

_SECURITY_WARNED = False

DEFAULT_CONFIG = {
    "model": {
        "provider": "local",
        "name": "deepseek-r1:8b",
        "effort": "balanced",
        "temperature": 0.7,
        "max_tokens": 8192,
        "max_steps": 20,
        "max_retries": 3,
        "local": {
            "name": "deepseek-r1:8b",
            "base_url": "http://127.0.0.1:11435/v1",
            "api_key": "ollama",
        },
    },
    "agent": {
        "permission_level": "full_access",
    },
    "paths": {},
}

PATH_SETTING_KEYS = (
    "model_dir",
    "workspace_dir",
    "output_dir",
    "backup_dir",
)


def _default_config() -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    bundled_model = os.environ.get("JAVIS_BUNDLED_MODEL", "").strip()
    bundled_url = os.environ.get("JAVIS_BUNDLED_OLLAMA_URL", "").strip().rstrip("/")
    if bundled_model:
        config["model"]["name"] = bundled_model
        config["model"]["local"]["name"] = bundled_model
    if bundled_url:
        config["model"]["local"]["base_url"] = bundled_url
    return config


def _merge_defaults(config: dict) -> dict:
    merged = _default_config()
    if not isinstance(config, dict):
        return merged
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    model = merged.setdefault("model", {})
    local = model.setdefault("local", {})
    local.setdefault("name", model.get("name", "deepseek-r1:8b"))
    local.setdefault("base_url", "http://127.0.0.1:11435/v1")
    local["api_key"] = local.get("api_key") or "ollama"
    merged.setdefault("agent", {}).setdefault("permission_level", "full_access")
    return merged

def _warn_security(config: dict):
    global _SECURITY_WARNED
    if _SECURITY_WARNED: return
    if "model" not in config: return
    for provider in CLOUD_PROVIDERS:
        pc = config["model"].get(provider, {})
        if isinstance(pc, dict) and pc.get("api_key", "").startswith(ENCODED_PREFIX):
            logger.warning(f"🔑 {provider} 的 API Key 以 base64 编码存储在 config.yaml 中。base64 不是加密，可以轻易解码。推荐改用环境变量 {provider.upper()}_API_KEY。")
            _SECURITY_WARNED = True; return

def _encode(s): return ENCODED_PREFIX + base64.b64encode(s.encode()).decode() if s else ""
def _decode(s):
    if not s: return ""
    if s.startswith(ENCODED_PREFIX):
        try: return base64.b64decode(s[len(ENCODED_PREFIX):]).decode()
        except: return s
    return s

def load_config():
    if not CONFIG_PATH.exists():
        return _default_config()
    if yaml is None:
        logger.warning("PyYAML is unavailable; using default configuration")
        return _default_config()
    try:
        with CONFIG_PATH.open(encoding="utf-8") as f:
            cfg=yaml.safe_load(f)
        cfg = _merge_defaults(cfg or {})
        if cfg and "model" in cfg:
            _warn_security(cfg)
            for k in CLOUD_PROVIDERS:
                pc=cfg["model"].get(k)
                if pc and isinstance(pc,dict) and pc.get("api_key"): pc["api_key"]=_decode(pc["api_key"])
        return cfg
    except Exception as e:
        logger.warning(f"config.yaml 读取异常: {e}"); return _default_config()

def save_config(config):
    if yaml is None:
        raise RuntimeError("PyYAML is required to save config.yaml")
    if "model" in config:
        local=config["model"].get("local",{})
        if isinstance(local,dict): local["api_key"]="ollama"
        for k in CLOUD_PROVIDERS:
            pc=config["model"].get(k)
            if pc and isinstance(pc,dict) and pc.get("api_key") and not pc["api_key"].startswith(ENCODED_PREFIX) and pc["api_key"]!="ollama":
                pc["api_key"]=_encode(pc["api_key"])
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

def _get_env_api_key(provider: str) -> str:
    env_var = ENV_KEY_MAP.get(provider)
    if env_var:
        return os.environ.get(env_var, "")
    return ""

def _get_api_key(provider):
    env_key = _get_env_api_key(provider)
    if env_key:
        return env_key
    cfg=load_config(); pc=cfg.get("model",{}).get(provider,{})
    key=pc.get("api_key","") if isinstance(pc,dict) else ""
    return _decode(key) if key.startswith(ENCODED_PREFIX) else key

AVAILABLE_MODELS = {
    "deepseek": [
        ("deepseek-v4-pro", "V4 Pro", "旗舰推理与智能体"),
        ("deepseek-v4-flash", "V4 Flash", "高速通用与思考模式"),
    ],
    "glm": [
        ("glm-5.2", "GLM-5.2", "最新旗舰，1M 上下文"),
        ("glm-5.1", "GLM-5.1", "长程 Coding"),
        ("glm-5", "GLM-5", "智能体与编程"),
        ("glm-5-turbo", "GLM-5 Turbo", "复杂长任务加速"),
        ("glm-4.7", "GLM-4.7", "通用、推理与智能体"),
        ("glm-4.7-flashx", "GLM-4.7 FlashX", "低延迟高速"),
        ("glm-4.7-flash", "GLM-4.7 Flash", "免费轻量"),
        ("glm-4.6", "GLM-4.6", "高级编码与工具调用"),
        ("glm-4.5-air", "GLM-4.5 Air", "高性价比"),
        ("glm-4.5-airx", "GLM-4.5 AirX", "高性价比极速"),
        ("glm-4-long", "GLM-4 Long", "1M 长文本"),
        ("glm-4-flashx-250414", "GLM-4 FlashX", "高速高并发"),
        ("glm-4-flash-250414", "GLM-4 Flash", "免费长上下文"),
    ],
    "kimi": [
        ("kimi-k2.7-code", "Kimi K2.7 Code", "代码与智能体"),
        ("kimi-k2.5", "Kimi K2.5", "多模态旗舰"),
        ("kimi-k2-thinking", "Kimi K2 Thinking", "深度推理"),
        ("kimi-k2-thinking-turbo", "Kimi K2 Thinking Turbo", "高速深度推理"),
        ("kimi-k2-turbo-preview", "Kimi K2 Turbo", "高速预览"),
        ("kimi-k2-0905-preview", "Kimi K2 0905", "K2 预览快照"),
        ("moonshot-v1-auto", "Moonshot V1 Auto", "自动上下文"),
        ("moonshot-v1-8k", "Moonshot V1 8K", "短上下文"),
        ("moonshot-v1-32k", "Moonshot V1 32K", "长文本"),
        ("moonshot-v1-128k", "Moonshot V1 128K", "超长文本"),
    ],
    "qwen": [
        ("qwen3.7-max", "Qwen3.7 Max", "最新旗舰"),
        ("qwen3.7-plus", "Qwen3.7 Plus", "推荐均衡"),
        ("qwen3.6-flash", "Qwen3.6 Flash", "极速低成本"),
        ("qwen3.6-plus", "Qwen3.6 Plus", "通用与智能体"),
        ("qwen3.5-plus", "Qwen3.5 Plus", "通用多模态"),
        ("qwen3-max", "Qwen3 Max", "旗舰系列"),
        ("qwen3-coder-next", "Qwen3 Coder Next", "代码智能体"),
        ("qwen3-coder-plus", "Qwen3 Coder Plus", "代码与工程"),
        ("qwen-max", "Qwen Max", "经典旗舰别名"),
        ("qwen-plus", "Qwen Plus", "经典均衡别名"),
        ("qwen-turbo", "Qwen Turbo", "经典高速别名"),
        ("qwen-long", "Qwen Long", "经典长文本别名"),
    ],
    "openai": [
        ("gpt-5.2", "GPT-5.2", "最新通用与智能体"),
        ("gpt-5.2-pro", "GPT-5.2 Pro", "高精度复杂任务"),
        ("gpt-5.1", "GPT-5.1", "编程与智能体"),
        ("gpt-5-pro", "GPT-5 Pro", "高算力推理"),
        ("gpt-5", "GPT-5", "通用推理"),
        ("gpt-5-mini", "GPT-5 Mini", "快速高性价比"),
        ("gpt-5-nano", "GPT-5 Nano", "最低延迟"),
        ("gpt-4.1", "GPT-4.1", "非推理旗舰"),
        ("gpt-4.1-mini", "GPT-4.1 Mini", "轻量通用"),
        ("gpt-4.1-nano", "GPT-4.1 Nano", "极速轻量"),
        ("o3-pro", "o3 Pro", "高算力复杂推理"),
        ("o3", "o3", "复杂推理"),
        ("o4-mini", "o4-mini", "快速推理"),
        ("gpt-4o", "GPT-4o", "经典多模态"),
        ("gpt-4o-mini", "GPT-4o Mini", "经典轻量多模态"),
    ],
    "anthropic": [
        ("claude-fable-5", "Claude Fable 5", "长程智能体旗舰"),
        ("claude-opus-5", "Claude Opus 5", "复杂编码与企业任务"),
        ("claude-sonnet-5", "Claude Sonnet 5", "速度与能力均衡"),
        ("claude-haiku-4-5", "Claude Haiku 4.5", "最快低成本"),
        ("claude-opus-4-8", "Claude Opus 4.8", "上一代复杂智能体"),
        ("claude-opus-4-7", "Claude Opus 4.7", "上一代旗舰"),
        ("claude-opus-4-6", "Claude Opus 4.6", "复杂编码"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6", "上一代均衡"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5", "稳定均衡"),
    ],
    "local": [],
}

def set_provider(provider):
    cfg=load_config(); mdl=cfg.setdefault("model",{}); mdl["provider"]=provider
    if provider=="local":
        lc=mdl.setdefault("local",{}); lc["name"]=lc.get("name") or mdl.get("name","deepseek-r1:8b"); lc["api_key"]="ollama"; mdl["name"]=lc["name"]
    else:
        models=AVAILABLE_MODELS.get(provider,[])
        if models:
            mdl["name"]=models[0][0]; pc=mdl.setdefault(provider,{})
            if isinstance(pc,dict): pc["name"]=models[0][0]
    save_config(cfg); logger.info(f"切换: {provider}"); return mdl

def set_api_key(provider, api_key):
    cfg=load_config(); cloud=CLOUD_PROVIDERS
    cfg.setdefault("model", {})
    if provider in cloud:
        cfg["model"].setdefault(provider,{}); cfg["model"][provider]["api_key"]=api_key
        models=AVAILABLE_MODELS.get(provider,[])
        if models and not cfg["model"][provider].get("name"): cfg["model"][provider]["name"]=models[0][0]
        save_config(cfg)
    return cfg["model"]

def set_model_name(provider, model_name):
    cfg=load_config(); mdl=cfg.get("model",{}); mdl["name"]=model_name
    if provider in mdl and isinstance(mdl[provider],dict): mdl[provider]["name"]=model_name
    else: mdl[provider]=model_name if not isinstance(mdl.get(provider),dict) else {**mdl[provider],"name":model_name}
    save_config(cfg); return mdl


def _validate_model_endpoint(value: str, label: str) -> str:
    endpoint = str(value or "").strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label}必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.username or parsed.password:
        raise ValueError(f"{label}不能包含账号或密码")
    if len(endpoint) > 2048:
        raise ValueError(f"{label}过长")
    return endpoint


def _validate_model_name(value: str, label: str) -> str:
    model_name = str(value or "").strip()
    if not model_name:
        raise ValueError(f"{label}不能为空")
    if len(model_name) > 240:
        raise ValueError(f"{label}过长")
    return model_name


def _provider_models(provider: str) -> list[dict]:
    return [
        {"id": name, "label": label, "description": description}
        for name, label, description in AVAILABLE_MODELS.get(provider, [])
    ]


def discover_remote_provider_models(
    provider: str,
    base_url: str = "",
    api_key: str = "",
) -> dict:
    """Merge the built-in compatible catalog with the provider's account catalog."""
    normalized = str(provider or "").strip().lower()
    if normalized not in CLOUD_PROVIDERS:
        return {"connected": False, "models": [], "message": "未知 API 提供商"}

    built_in = _provider_models(normalized)
    key = str(api_key or "").strip() or _get_api_key(normalized)
    if not key:
        return {
            "connected": False,
            "models": built_in,
            "discovered_count": 0,
            "message": f"已列出 {len(built_in)} 个内置兼容模型；配置 API Key 后可同步账户模型",
        }

    configured_base = str(base_url or "").strip()
    if not configured_base:
        cfg = load_config()
        profile = cfg.get("model", {}).get(normalized, {})
        if isinstance(profile, dict):
            configured_base = str(profile.get("base_url") or "")
    endpoint = _validate_model_endpoint(
        configured_base or DEFAULT_REMOTE_BASE_URLS[normalized],
        "API 地址",
    ) + "/models"
    headers = {"Accept": "application/json"}
    if normalized == "anthropic":
        headers.update({
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        })
    else:
        headers["Authorization"] = f"Bearer {key}"

    try:
        with request.urlopen(request.Request(endpoint, headers=headers), timeout=15) as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        discovered = sorted({
            str(row.get("id") or "").strip()
            for row in rows
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        })
        merged = list(built_in)
        known = {item["id"] for item in merged}
        for model_id in discovered:
            if model_id not in known:
                merged.append({
                    "id": model_id,
                    "label": model_id,
                    "description": "账户 API 返回",
                })
        return {
            "connected": True,
            "models": merged,
            "discovered_count": len(discovered),
            "message": f"已同步账户模型 {len(discovered)} 个；合并后共 {len(merged)} 个",
        }
    except HTTPError as error:
        message = "API Key 无效或无权读取模型" if error.code in {401, 403} else f"供应商返回 HTTP {error.code}"
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        reason = getattr(error, "reason", error)
        message = f"无法同步供应商模型：{str(reason)[:120]}"
    return {
        "connected": False,
        "models": built_in,
        "discovered_count": 0,
        "message": f"{message}；仍显示 {len(built_in)} 个内置兼容模型",
    }


def _route_from_model(model: dict, route_name: str) -> dict:
    """Return a normalized Live/Code route while preserving v3 legacy config."""
    routes = model.get("routes", {}) if isinstance(model.get("routes"), dict) else {}
    saved = routes.get(route_name, {}) if isinstance(routes.get(route_name), dict) else {}
    legacy_provider = str(model.get("provider", "local") or "local")
    source = str(saved.get("source") or ("local" if legacy_provider == "local" else "remote"))
    if source not in {"local", "remote"}:
        source = "local"

    global_local = model.get("local", {}) if isinstance(model.get("local"), dict) else {}
    saved_local = saved.get("local", {}) if isinstance(saved.get("local"), dict) else {}
    remote_provider = str(
        (saved.get("remote", {}) or {}).get("provider")
        if isinstance(saved.get("remote"), dict)
        else ""
    ) or str(model.get("remote_provider") or (legacy_provider if legacy_provider in CLOUD_PROVIDERS else "deepseek"))
    if remote_provider not in CLOUD_PROVIDERS:
        remote_provider = "deepseek"
    global_remote = model.get(remote_provider, {})
    if not isinstance(global_remote, dict):
        global_remote = {}
    saved_remote = saved.get("remote", {}) if isinstance(saved.get("remote"), dict) else {}
    available = AVAILABLE_MODELS.get(remote_provider, [])
    default_remote_model = available[0][0] if available else ""
    local_base_url = _effective_local_base_url(
        saved_local.get("base_url")
        or global_local.get("base_url")
        or "http://127.0.0.1:11435/v1"
    )
    return {
        "source": source,
        "local": {
            "model": str(saved_local.get("model") or saved_local.get("name") or global_local.get("name") or "qwen2.5:7b"),
            "base_url": local_base_url,
        },
        "remote": {
            "provider": remote_provider,
            "model": str(saved_remote.get("model") or saved_remote.get("name") or global_remote.get("name") or default_remote_model),
            "base_url": str(saved_remote.get("base_url") or global_remote.get("base_url") or DEFAULT_REMOTE_BASE_URLS[remote_provider]),
        },
    }


def resolve_model_route(config: dict, route_name: str = "live") -> dict:
    """Resolve a surface route from an already loaded configuration."""
    normalized = "code" if str(route_name).strip().lower() == "code" else "live"
    model = config.get("model", {}) if isinstance(config, dict) else {}
    if not isinstance(model, dict):
        model = {}
    if normalized == "code" and bool(model.get("share_live_code", True)):
        normalized = "live"
    return _route_from_model(model, normalized)


def get_model_route(route_name: str = "live") -> dict:
    """Resolve the effective route used by one conversation surface."""
    return resolve_model_route(load_config(), route_name)


def _effective_local_base_url(value: object) -> str:
    """Migrate only the retired Javis default while preserving custom Ollama URLs."""
    configured = str(value or "").strip().rstrip("/")
    bundled = os.environ.get("JAVIS_BUNDLED_OLLAMA_URL", "").strip().rstrip("/")
    legacy_defaults = {
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
    }
    if bundled and configured in legacy_defaults:
        return bundled
    return configured or bundled or "http://127.0.0.1:11435/v1"


def get_model_connection_settings() -> dict:
    cfg = load_config()
    model = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    active_provider = str(model.get("provider", "local") or "local")
    if active_provider not in {"local", *CLOUD_PROVIDERS}:
        active_provider = "local"

    local = model.get("local", {}) if isinstance(model.get("local"), dict) else {}
    remote_provider = active_provider if active_provider in CLOUD_PROVIDERS else str(
        model.get("remote_provider", "deepseek") or "deepseek"
    )
    if remote_provider not in CLOUD_PROVIDERS:
        remote_provider = "deepseek"
    remote = model.get(remote_provider, {})
    if not isinstance(remote, dict):
        remote = {}
    remote_models = AVAILABLE_MODELS.get(remote_provider, [])
    remote_model = str(remote.get("name") or "")
    if not remote_model and remote_models:
        remote_model = remote_models[0][0]

    env_key = _get_env_api_key(remote_provider)
    configured_key = _get_api_key(remote_provider)
    remote_profiles = {}
    for provider in CLOUD_PROVIDERS:
        profile = model.get(provider, {})
        if not isinstance(profile, dict):
            profile = {}
        available = AVAILABLE_MODELS.get(provider, [])
        profile_model = str(profile.get("name") or "")
        if not profile_model and available:
            profile_model = available[0][0]
        provider_env_key = _get_env_api_key(provider)
        provider_key = _get_api_key(provider)
        remote_profiles[provider] = {
            "model": profile_model,
            "base_url": str(
                profile.get("base_url")
                or DEFAULT_REMOTE_BASE_URLS[provider]
            ),
            "has_key": bool(provider_key),
            "key_source": (
                "environment"
                if provider_env_key
                else ("local" if provider_key else "none")
            ),
            "env_var": ENV_KEY_MAP[provider],
        }
    share_live_code = bool(model.get("share_live_code", True))
    routes = {
        "live": _route_from_model(model, "live"),
        "code": _route_from_model(model, "code"),
    }
    if share_live_code:
        routes["code"] = copy.deepcopy(routes["live"])
    return {
        "applied": True,
        "share_live_code": share_live_code,
        "routes": routes,
        "source": "local" if active_provider == "local" else "remote",
        "active_provider": active_provider,
        "active_model": (
            str(local.get("name") or model.get("name") or "qwen2.5:7b")
            if active_provider == "local"
            else remote_model
        ),
        "local": {
            "model": str(local.get("name") or "qwen2.5:7b"),
            "base_url": _effective_local_base_url(local.get("base_url")),
        },
        "remote": {
            "provider": remote_provider,
            "model": remote_model,
            "base_url": str(
                remote.get("base_url")
                or DEFAULT_REMOTE_BASE_URLS[remote_provider]
            ),
            "has_key": bool(configured_key),
            "key_source": "environment" if env_key else ("local" if configured_key else "none"),
            "env_var": ENV_KEY_MAP[remote_provider],
        },
        "remote_profiles": remote_profiles,
        "providers": [
            {
                "id": provider,
                "label": PROVIDER_LABELS[provider],
                "models": _provider_models(provider),
                "default_base_url": DEFAULT_REMOTE_BASE_URLS[provider],
            }
            for provider in CLOUD_PROVIDERS
        ],
    }


def set_model_connection_settings(values: dict) -> dict:
    if not isinstance(values, dict):
        return {"applied": False, "error": "模型配置必须是对象"}

    cfg = load_config()
    model = cfg.setdefault("model", {})
    current_provider = str(model.get("provider", "local") or "local")
    route_values = values.get("routes")
    if route_values is not None and not isinstance(route_values, dict):
        return {"applied": False, "error": "Live/Code 路由配置必须是对象"}
    model["share_live_code"] = bool(values.get("share_live_code", model.get("share_live_code", True)))
    source = str(
        values.get("source")
        or ("local" if current_provider == "local" else "remote")
    ).strip().lower()
    if source not in {"local", "remote"}:
        return {"applied": False, "error": "模型来源只能是 local 或 remote"}

    try:
        local_values = values.get("local", {})
        if local_values is not None and not isinstance(local_values, dict):
            raise ValueError("本地模型配置必须是对象")
        local = model.setdefault("local", {})
        if local_values:
            local["name"] = _validate_model_name(
                local_values.get("model", local.get("name", "qwen2.5:7b")),
                "本地模型名称",
            )
            local["base_url"] = _validate_model_endpoint(
                local_values.get(
                    "base_url",
                    local.get("base_url", "http://127.0.0.1:11435/v1"),
                ),
                "本地模型地址",
            )
            local["api_key"] = "ollama"

        remote_values = values.get("remote", {})
        if remote_values is not None and not isinstance(remote_values, dict):
            raise ValueError("远端 API 配置必须是对象")
        requested_remote = (
            remote_values.get("provider")
            if remote_values
            else model.get("remote_provider")
        )
        if not requested_remote and current_provider in CLOUD_PROVIDERS:
            requested_remote = current_provider
        remote_provider = str(requested_remote or "deepseek").strip().lower()
        if remote_provider not in CLOUD_PROVIDERS:
            raise ValueError(f"不支持的远端提供商：{remote_provider}")
        model["remote_provider"] = remote_provider

        remote = model.setdefault(remote_provider, {})
        if not isinstance(remote, dict):
            remote = {}
            model[remote_provider] = remote
        if remote_values:
            available = AVAILABLE_MODELS.get(remote_provider, [])
            default_model = available[0][0] if available else ""
            remote["name"] = _validate_model_name(
                remote_values.get("model", remote.get("name") or default_model),
                "远端模型名称",
            )
            remote["base_url"] = _validate_model_endpoint(
                remote_values.get(
                    "base_url",
                    remote.get("base_url")
                    or DEFAULT_REMOTE_BASE_URLS[remote_provider],
                ),
                "远端 API 地址",
            )
            submitted_key = remote_values.get("api_key")
            if submitted_key is not None and str(submitted_key).strip():
                api_key = str(submitted_key).strip()
                if len(api_key) > 4096:
                    raise ValueError("API Key 过长")
                remote["api_key"] = api_key

        if source == "local":
            local_name = _validate_model_name(
                local.get("name") or model.get("name") or "qwen2.5:7b",
                "本地模型名称",
            )
            model["provider"] = "local"
            model["name"] = local_name
        else:
            remote_name = _validate_model_name(
                remote.get("name")
                or (AVAILABLE_MODELS.get(remote_provider) or [["", "", ""]])[0][0],
                "远端模型名称",
            )
            model["provider"] = remote_provider
            model["name"] = remote_name

        if route_values is not None:
            routes = model.setdefault("routes", {})
            for route_name in ("live", "code"):
                submitted = route_values.get(route_name)
                if submitted is None:
                    continue
                if not isinstance(submitted, dict):
                    raise ValueError(f"{route_name} 路由配置必须是对象")
                route_source = str(submitted.get("source", "local")).strip().lower()
                if route_source not in {"local", "remote"}:
                    raise ValueError(f"{route_name} 模型来源只能是 local 或 remote")
                submitted_local = submitted.get("local", {})
                submitted_remote = submitted.get("remote", {})
                if not isinstance(submitted_local, dict) or not isinstance(submitted_remote, dict):
                    raise ValueError(f"{route_name} 的本地与远端配置必须是对象")
                route_provider = str(submitted_remote.get("provider") or remote_provider).strip().lower()
                if route_provider not in CLOUD_PROVIDERS:
                    raise ValueError(f"不支持的远端提供商：{route_provider}")
                route_profile = {
                    "source": route_source,
                    "local": {
                        "model": _validate_model_name(
                            submitted_local.get("model") or local.get("name") or "qwen2.5:7b",
                            f"{route_name} 本地模型名称",
                        ),
                        "base_url": _validate_model_endpoint(
                            submitted_local.get("base_url") or local.get("base_url") or "http://127.0.0.1:11435/v1",
                            f"{route_name} 本地模型地址",
                        ),
                    },
                    "remote": {
                        "provider": route_provider,
                        "model": _validate_model_name(
                            submitted_remote.get("model")
                            or (model.get(route_provider, {}) if isinstance(model.get(route_provider), dict) else {}).get("name")
                            or (AVAILABLE_MODELS.get(route_provider) or [["", "", ""]])[0][0],
                            f"{route_name} 远端模型名称",
                        ),
                        "base_url": _validate_model_endpoint(
                            submitted_remote.get("base_url")
                            or (model.get(route_provider, {}) if isinstance(model.get(route_provider), dict) else {}).get("base_url")
                            or DEFAULT_REMOTE_BASE_URLS[route_provider],
                            f"{route_name} 远端 API 地址",
                        ),
                    },
                }
                submitted_key = submitted_remote.get("api_key")
                if submitted_key is not None and str(submitted_key).strip():
                    key = str(submitted_key).strip()
                    if len(key) > 4096:
                        raise ValueError("API Key 过长")
                    provider_profile = model.setdefault(route_provider, {})
                    provider_profile["api_key"] = key
                routes[route_name] = route_profile

            if model["share_live_code"]:
                routes["code"] = copy.deepcopy(routes.get("live") or _route_from_model(model, "live"))

            live_route = routes.get("live") or _route_from_model(model, "live")
            if live_route["source"] == "local":
                model["provider"] = "local"
                model["name"] = live_route["local"]["model"]
            else:
                model["provider"] = live_route["remote"]["provider"]
                model["name"] = live_route["remote"]["model"]

        save_config(cfg)
    except (TypeError, ValueError) as error:
        return {"applied": False, "error": str(error)}

    return get_model_connection_settings()


def _default_path_settings() -> dict[str, str]:
    root = CONFIG_PATH.parent.resolve()
    model_dir = Path(
        os.environ.get("OLLAMA_MODELS")
        or (Path.home() / ".ollama" / "models")
    ).expanduser()
    return {
        "model_dir": str(model_dir.resolve()),
        "workspace_dir": str((root / "workspace").resolve()),
        "output_dir": str((root / "uploads").resolve()),
        "backup_dir": str((root / "backups").resolve()),
    }


def get_path_settings() -> dict[str, str]:
    defaults = _default_path_settings()
    configured = load_config().get("paths", {})
    if not isinstance(configured, dict):
        return defaults
    root = CONFIG_PATH.parent.resolve()
    result = {}
    for key in PATH_SETTING_KEYS:
        raw = str(configured.get(key, "") or "").strip()
        if not raw:
            result[key] = defaults[key]
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = root / path
        result[key] = str(path.resolve())
    return result


def set_path_settings(values: dict) -> dict:
    if not isinstance(values, dict):
        return {"applied": False, "error": "路径配置必须是对象"}
    unknown = sorted(set(values) - set(PATH_SETTING_KEYS))
    if unknown:
        return {"applied": False, "error": f"未知路径类型: {', '.join(unknown)}"}

    validated: dict[str, str] = {}
    for key, raw_value in values.items():
        raw = str(raw_value or "").strip()
        path = Path(raw).expanduser()
        if not path.is_absolute():
            return {"applied": False, "error": f"{key} 必须使用绝对路径"}
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            return {"applied": False, "error": f"{key} 目录不存在: {path}"}
        if not resolved.is_dir():
            return {"applied": False, "error": f"{key} 不是目录: {resolved}"}
        validated[key] = str(resolved)

    current_paths = get_path_settings()
    changed = sorted(
        key for key, value in validated.items()
        if current_paths.get(key) != value
    )
    if changed:
        cfg = load_config()
        cfg.setdefault("paths", {}).update(validated)
        save_config(cfg)
    paths = get_path_settings()
    logger.info("路径配置已更新: %s", ", ".join(changed) or "unchanged")
    return {
        "applied": True,
        "paths": paths,
        "changed": changed,
        "restart_required": ["model_dir"] if "model_dir" in changed else [],
    }

# ═══════════════════════════════════════════════════════════════
# 权限级别 — 决定工具调用的审批方式
# ═══════════════════════════════════════════════════════════════

PERMISSION_LEVELS = {
      "full_access": {
        "level": 1,
        "label": "完全访问",
        "icon": "🔓",
          "desc": "感知与非破坏操作自动执行；自修改和删除需确认；系统核心文件禁止删除",
        "color": "#30d158",
    },
    "quick_auth": {
        "level": 2,
        "label": "快速授权",
        "icon": "⚡",
        "desc": "仅危险操作需确认（写入/删除/代码执行）",
        "color": "#ff9f0a",
    },
    "safe_guard": {
        "level": 3,
        "label": "安全审批",
        "icon": "🛡️",
        "desc": "所有修改操作需确认，读取自动通过",
        "color": "#0a84ff",
    },
    "full_approval": {
        "level": 4,
        "label": "完全审批",
        "icon": "🔒",
        "desc": "每一个工具调用都需要人工确认",
        "color": "#ff453a",
    },
}

def get_permission_level() -> str:
    cfg = load_config()
    return cfg.get("agent", {}).get("permission_level", "full_access")

def set_permission_level(level: str) -> dict:
    if level not in PERMISSION_LEVELS:
        return {"applied": False, "error": f"未知权限级别: {level}"}
    cfg = load_config()
    cfg.setdefault("agent", {})["permission_level"] = level
    save_config(cfg)
    lvl = PERMISSION_LEVELS[level]
    logger.info(f"权限级别切换: {level} ({lvl['label']})")
    return {
        "applied": True,
        "permission": level,
        "label": lvl["label"],
        "icon": lvl["icon"],
        "desc": lvl["desc"],
        "color": lvl["color"],
    }

# ═══════════════════════════════════════════════════════════════
# 推理深度 (Effort) — 独立于模型选择
# 修改: temperature + max_tokens, 不换模型
# ═══════════════════════════════════════════════════════════════

EFFORT_LEVELS = {
    "balanced": {
        "label": "均衡",
        "desc": "平衡速度与质量（默认）",
        "temperature": 0.7,
        "max_tokens": 8192,
        "max_steps": 20,
        "max_retries": 3,
    },
    "deep": {
        "label": "深度",
        "desc": "更强推理，允许更多思考步数",
        "temperature": 0.5,
        "max_tokens": 16384,
        "max_steps": 35,
        "max_retries": 5,
    },
    "max": {
        "label": "最大",
        "desc": "全力以赴，最多步数和重试",
        "temperature": 0.3,
        "max_tokens": 32768,
        "max_steps": 50,
        "max_retries": 8,
    },
}

def get_effort() -> str:
    cfg = load_config()
    return cfg.get("model", {}).get("effort", "balanced")

def set_effort(level: str) -> dict:
    if level not in EFFORT_LEVELS:
        return {"applied": False, "error": f"未知推理深度: {level}"}
    cfg = load_config()
    m = cfg.setdefault("model", {})
    lvl = EFFORT_LEVELS[level]
    m["effort"] = level
    m["temperature"] = lvl["temperature"]
    m["max_tokens"] = lvl["max_tokens"]
    m["max_steps"] = lvl["max_steps"]
    m["max_retries"] = lvl["max_retries"]
    save_config(cfg)
    logger.info(f"推理深度切换: {level} ({lvl['label']})")
    return {
        "applied": True,
        "effort": level,
        "label": lvl["label"],
        "temperature": lvl["temperature"],
        "max_tokens": lvl["max_tokens"],
        "max_steps": lvl["max_steps"],
        "max_retries": lvl["max_retries"],
    }

# 状态信息
# ═══════════════════════════════════════════════════════════════

def get_status():
    cfg=load_config(); m=cfg.get("model",{}); provider=m.get("provider","local")
    effort = m.get("effort", "balanced")
    if provider=="local": has_key=True; hint=None; model_name=m.get("name","qwen2.5:7b")
    else:
        raw=_get_api_key(provider); has_key=bool(raw)
        labels={"deepseek":"DeepSeek","glm":"智谱GLM","kimi":"Kimi","qwen":"通义千问","openai":"OpenAI","anthropic":"Claude"}
        hint=f"请设置{ENV_KEY_MAP.get(provider, provider.upper()+'_API_KEY')}环境变量或在config.yaml中配置" if not has_key else None
        pc=m.get(provider,{}); model_name=pc.get("name","") if isinstance(pc,dict) else m.get("name","")
    available=AVAILABLE_MODELS.get(provider,[])
    models=[(n,l,d,n==model_name) for n,l,d in available]
    effort_info = EFFORT_LEVELS.get(effort, EFFORT_LEVELS["balanced"])
    perm = get_permission_level()
    perm_info = PERMISSION_LEVELS.get(perm, PERMISSION_LEVELS["quick_auth"])
    return {
        "provider":provider,"model":model_name,"models":models,
        "has_key":has_key,"temperature":m.get("temperature",0.7),"hint":hint,
        "effort": effort, "effort_label": effort_info["label"],
        "effort_desc": effort_info["desc"], "max_tokens": m.get("max_tokens", 8192),
        "max_steps": m.get("max_steps", 20), "max_retries": m.get("max_retries", 3),
        "permission": perm, "permission_label": perm_info["label"],
        "permission_icon": perm_info["icon"], "permission_desc": perm_info["desc"],
        "permission_color": perm_info["color"],
    }
