"""Tool Guardrails (P0-7) — 工具调用安全护栏

三层防护:
  Layer 1: 调用前校验 — 参数 schema 验证、危险操作拦截、频率限制
  Layer 2: 执行中监控 — 超时控制、资源限制
  Layer 3: 调用后审计 — 结果验证、异常检测、审计日志

集成点:
  - ToolRegistry.execute() 中注入 pre/post hooks
  - Agent._execute_tool() 中注入安全校验
"""

import time, re, logging
from typing import Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger("guardrails")

# ============================================================
# 权限级别
# ============================================================
PERMISSION_LEVELS = {
    "safe": 0, "low": 1, "medium": 2, "dangerous": 3, "critical": 4,
}

# ── 工具风险分级 ──
TOOL_RISK_LEVELS: dict[str, int] = {
    # Safe (只读)
    "screenshot": 0, "camera_snapshot": 0, "camera_list": 0,
    "file_read": 0, "file_list": 0, "system_info": 0,
    "get_window_state": 0, "list_windows": 0, "find_app": 0,
    "brain_status": 0, "memory_status": 0, "github_search": 0,
    "web_search": 0, "web_fetch": 0,
    # Low
    "mouse_move": 1, "mouse_click": 1, "keyboard_type": 1,
    "keyboard_press": 1, "set_volume": 1, "focus_window": 1,
    "open_app": 1, "screenshot_save": 1,
    # Medium
    "file_write": 2, "mouse_drag": 2, "mouse_scroll": 2,
    "download_file": 2, "copy_to_clipboard": 2,
    # Dangerous
    "file_delete": 3, "run_code": 3, "mouse_double_click": 3,
    # Critical
    "execute_command": 4, "system_shutdown": 4, "system_restart": 4,
    "registry_edit": 4, "install_package": 4, "uninstall_app": 4,
}

# ── 速率限制配置 ──
RATE_LIMITS: dict[str, dict] = {
    "web_search": {"window_sec": 60, "max_calls": 10},
    "web_fetch": {"window_sec": 60, "max_calls": 20},
    "screenshot": {"window_sec": 10, "max_calls": 5},
    "run_code": {"window_sec": 60, "max_calls": 30},
    "execute_command": {"window_sec": 60, "max_calls": 10},
}
DEFAULT_RATE_LIMIT = {"window_sec": 60, "max_calls": 60}
DEFAULT_TIMEOUT_MS = 30_000
MAX_OUTPUT_SIZE = 100_000

# ============================================================
# @dataclass
# ============================================================
@dataclass
class AuditEntry:
    tool_name: str
    params: dict
    risk_level: int
    risk_label: str
    result_ok: bool
    duration_ms: float
    blocked: bool = False
    block_reason: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "tool": self.tool_name,
            "params": str(self.params)[:200],
            "risk": self.risk_label,
            "ok": self.result_ok,
            "duration_ms": round(self.duration_ms, 1),
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
        }

# ============================================================
# ParamValidator — JSON Schema 参数校验
# ============================================================
class ParamValidator:
    @staticmethod
    def validate(tool_name: str, params: dict, schema: dict) -> Optional[str]:
        if not isinstance(params, dict):
            return f"[{tool_name}] 参数必须是 dict，收到 {type(params).__name__}"
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in params or params[key] is None:
                return f"[{tool_name}] 缺少必填参数: {key}"
        for key, value in params.items():
            if key not in properties:
                continue
            prop = properties[key]
            expected_type = prop.get("type")
            err = ParamValidator._check_type(key, value, expected_type, prop)
            if err:
                return f"[{tool_name}] {err}"
        return None

    @staticmethod
    def _check_type(key: str, value: Any, expected_type: str, prop: dict) -> Optional[str]:
        type_map = {"string": str, "integer": int, "number": (int, float),
                     "boolean": bool, "array": list, "object": dict}
        if expected_type in type_map and not isinstance(value, type_map[expected_type]):
            return f"参数 '{key}' 应为 {expected_type}，收到 {type(value).__name__}"
        if expected_type == "string":
            if "minLength" in prop and len(str(value)) < prop["minLength"]:
                return f"参数 '{key}' 长度不足 (min: {prop['minLength']})"
            if "maxLength" in prop and len(str(value)) > prop["maxLength"]:
                return f"参数 '{key}' 长度超出 (max: {prop['maxLength']})"
        if expected_type in ("integer", "number"):
            if "minimum" in prop and value < prop["minimum"]:
                return f"参数 '{key}' 小于最小值 {prop['minimum']}"
            if "maximum" in prop and value > prop["maximum"]:
                return f"参数 '{key}' 大于最大值 {prop['maximum']}"
        if "enum" in prop and value not in prop["enum"]:
            return f"参数 '{key}' 不在允许值范围内: {prop['enum']}"
        return None

# ============================================================
# RateLimiter — 滑动窗口限流
# ============================================================
class RateLimiter:
    def __init__(self):
        self._windows: dict[str, list[float]] = {}

    def check(self, tool_name: str) -> tuple[bool, str]:
        limit = RATE_LIMITS.get(tool_name, DEFAULT_RATE_LIMIT)
        now = time.time()
        window_sec = limit["window_sec"]
        max_calls = limit["max_calls"]
        if tool_name not in self._windows:
            self._windows[tool_name] = []
        cutoff = now - window_sec
        self._windows[tool_name] = [t for t in self._windows[tool_name] if t > cutoff]
        if len(self._windows[tool_name]) >= max_calls:
            return False, f"[{tool_name}] 频率限制: {max_calls}次/{window_sec}s"
        self._windows[tool_name].append(now)
        return True, ""

# ============================================================
# ContentFilter — 敏感信息检测
# ============================================================
class ContentFilter:
    SENSITIVE_PATTERNS = [
        (r'(?:api[_-]?key|token|secret|password)\s*[:=]\s*[\w\-\.]{16,}', "API Key"),
        (r'(?:gh[pousr]_[a-zA-Z0-9]{36,})', "GitHub Token"),
        (r'(?:sk-[a-zA-Z0-9]{20,})', "OpenAI Key"),
        (r'\b1[3-9]\d{9}\b', "手机号"),
        (r'eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]+', "JWT Token"),
    ]

    @classmethod
    def scan(cls, text: str) -> list[str]:
        return [f"检测到疑似{label}泄露" for pattern, label in cls.SENSITIVE_PATTERNS
                if re.search(pattern, text)]

    @classmethod
    def sanitize(cls, text: str) -> str:
        for pattern, _ in cls.SENSITIVE_PATTERNS:
            text = re.sub(pattern, '[已脱敏]', text)
        return text

# ============================================================
# ToolGuard — 主护栏类
# ============================================================
class ToolGuard:
    def __init__(self, permission_level: str = "safe"):
        self.permission_level = PERMISSION_LEVELS.get(permission_level, 0)
        self.validator = ParamValidator()
        self.rate_limiter = RateLimiter()
        self.audit_log: list[AuditEntry] = []
        self._blocked_count: int = 0
        self._total_count: int = 0

    def pre_check(self, tool_name: str, params: dict, schema: dict = None) -> Optional[str]:
        """调用前检查, 返回 None=通过, 否则=拦截原因"""
        self._total_count += 1
        if schema:
            err = self.validator.validate(tool_name, params, schema)
            if err:
                self._blocked_count += 1
                self._log(tool_name, params, 0, 0, False, True, err)
                return err
        risk = TOOL_RISK_LEVELS.get(tool_name, 2)
        if risk > self.permission_level:
            msg = f"[{tool_name}] 风险级别({risk}) 超过当前权限({self._perm_label()})，已拦截"
            self._blocked_count += 1
            self._log(tool_name, params, risk, 0, False, True, msg)
            return msg
        allowed, reason = self.rate_limiter.check(tool_name)
        if not allowed:
            self._blocked_count += 1
            self._log(tool_name, params, risk, 0, False, True, reason)
            return reason
        return None

    def post_check(self, tool_name: str, params: dict, result_ok: bool,
                   duration_ms: float, output: str = "") -> Optional[str]:
        risk = TOOL_RISK_LEVELS.get(tool_name, 2)
        if output:
            leaks = ContentFilter.scan(output)
            if leaks:
                logger.warning(f"[{tool_name}] 敏感检测: {leaks}")
                return f"[{tool_name}] 输出包含敏感信息: {'; '.join(leaks)}"
        self._log(tool_name, params, risk, duration_ms, result_ok)
        return None

    def _log(self, tool_name, params, risk, duration_ms, ok, blocked=False, reason=""):
        rl = [k for k, v in PERMISSION_LEVELS.items() if v == risk]
        self.audit_log.append(AuditEntry(
            tool_name=tool_name, params=params, risk_level=risk,
            risk_label=rl[0] if rl else str(risk), result_ok=ok,
            duration_ms=duration_ms, blocked=blocked, block_reason=reason,
        ))
        if len(self.audit_log) > 1000:
            self.audit_log = self.audit_log[-500:]

    def _perm_label(self) -> str:
        return [k for k, v in PERMISSION_LEVELS.items() if v == self.permission_level][0]

    @property
    def stats(self) -> dict:
        return {"total": self._total_count, "blocked": self._blocked_count,
                "passed": self._total_count - self._blocked_count,
                "audit_entries": len(self.audit_log)}

    def recent_audit(self, n: int = 20) -> list[dict]:
        return [e.to_dict() for e in self.audit_log[-n:]]

    def reset(self):
        self._blocked_count = 0
        self._total_count = 0

# ============================================================
# 敏感参数脱敏
# ============================================================
_SENSITIVE_PARAM_KEYS = {
    "api_key", "token", "password", "secret", "credential",
    "access_token", "refresh_token", "private_key", "auth",
    "authorization", "cookie", "session_id", "jwt",
}

def sanitize_params(params: dict) -> dict:
    safe = {}
    for k, v in params.items():
        if k.lower() in _SENSITIVE_PARAM_KEYS:
            safe[k] = "[已隐藏]"
        elif isinstance(v, dict):
            safe[k] = sanitize_params(v)
        else:
            safe[k] = v
    return safe

__all__ = [
    "ToolGuard", "ParamValidator", "RateLimiter",
    "ContentFilter", "AuditEntry", "sanitize_params",
    "TOOL_RISK_LEVELS", "PERMISSION_LEVELS",
    "RATE_LIMITS", "DEFAULT_TIMEOUT_MS", "MAX_OUTPUT_SIZE",
]
