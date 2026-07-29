"""配置管理 API"""
import copy, os, base64, logging
from pathlib import Path
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
        "name": "qwen2.5:7b",
        "effort": "balanced",
        "temperature": 0.7,
        "max_tokens": 8192,
        "max_steps": 20,
        "max_retries": 3,
        "local": {
            "name": "qwen2.5:7b",
            "base_url": "http://localhost:11434/v1",
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
    return copy.deepcopy(DEFAULT_CONFIG)


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
    local.setdefault("name", model.get("name", "qwen2.5:7b"))
    local.setdefault("base_url", "http://localhost:11434/v1")
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

AVAILABLE_MODELS={
    "deepseek": [("deepseek-v4-pro","V4 Pro","最新旗舰"),("deepseek-v4-flash","V4 Flash","极速"),("deepseek-chat","V3旧版","即将退役"),("deepseek-reasoner","R1旧版","即将退役")],
    "glm": [("glm-4-flash","Flash","轻量"),("glm-4-plus","Plus","均衡"),("glm-4","GLM-4","最强")],
    "kimi": [("moonshot-v1-8k","8K","标准"),("moonshot-v1-32k","32K","长文本"),("moonshot-v1-128k","128K","超长")],
    "qwen": [("qwen-plus","Plus","推荐"),("qwen-turbo","Turbo","极速"),("qwen-max","Max","最强"),("qwen-long","Long","长文本")],
    "openai": [("gpt-4o-mini","4o mini","轻量"),("gpt-4o","4o","最强"),("o3-mini","o3 mini","推理")],
    "anthropic": [("claude-haiku-3-5","Haiku","极速"),("claude-sonnet-4-20250514","Sonnet 4","均衡"),("claude-opus-4-20250514","Opus 4","最强")],
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
    return {
        "applied": True,
        "source": "local" if active_provider == "local" else "remote",
        "active_provider": active_provider,
        "active_model": (
            str(local.get("name") or model.get("name") or "qwen2.5:7b")
            if active_provider == "local"
            else remote_model
        ),
        "local": {
            "model": str(local.get("name") or "qwen2.5:7b"),
            "base_url": str(local.get("base_url") or "http://127.0.0.1:11434/v1"),
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
                    local.get("base_url", "http://127.0.0.1:11434/v1"),
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
    result = {}
    for key in PATH_SETTING_KEYS:
        raw = str(configured.get(key, "") or "").strip()
        result[key] = str(Path(raw).expanduser().resolve()) if raw else defaults[key]
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
