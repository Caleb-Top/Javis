"""配置管理 API"""
import os, base64, yaml, logging
from pathlib import Path
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

_SECURITY_WARNED = False

def _warn_security(config: dict):
    global _SECURITY_WARNED
    if _SECURITY_WARNED: return
    if "model" not in config: return
    for provider in ["deepseek", "glm", "kimi", "qwen", "openai", "anthropic"]:
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
    try:
        cfg=yaml.safe_load(open(CONFIG_PATH,encoding="utf-8"))
        if cfg and "model" in cfg:
            _warn_security(cfg)
            for k in ["deepseek","glm","kimi","qwen","openai","anthropic"]:
                pc=cfg["model"].get(k)
                if pc and isinstance(pc,dict) and pc.get("api_key"): pc["api_key"]=_decode(pc["api_key"])
        return cfg or {}
    except Exception as e:
        logger.warning(f"config.yaml 读取异常: {e}"); return {}

def save_config(config):
    if "model" in config:
        local=config["model"].get("local",{})
        if isinstance(local,dict): local["api_key"]="ollama"
        for k in ["deepseek","glm","kimi","qwen","openai","anthropic"]:
            pc=config["model"].get(k)
            if pc and isinstance(pc,dict) and pc.get("api_key") and not pc["api_key"].startswith(ENCODED_PREFIX) and pc["api_key"]!="ollama":
                pc["api_key"]=_encode(pc["api_key"])
    yaml.dump(config,open(CONFIG_PATH,"w",encoding="utf-8"),allow_unicode=True,default_flow_style=False,sort_keys=False)

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
    cfg=load_config(); cloud=["deepseek","glm","kimi","qwen","openai","anthropic"]
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

# ═══════════════════════════════════════════════════════════════
# 权限级别 — 决定工具调用的审批方式
# ═══════════════════════════════════════════════════════════════

PERMISSION_LEVELS = {
    "full_access": {
        "level": 1,
        "label": "完全访问",
        "icon": "🔓",
        "desc": "全部操作自动批准，无阻拦",
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
    return cfg.get("agent", {}).get("permission_level", "quick_auth")

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
