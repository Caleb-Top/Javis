"""反思器 — 任务执行后结构化评估与经验提炼 (26类错误分类器)"""

import logging, time, hashlib, re, traceback
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("reflector")


# ============================================================
# 26 类错误分类体系
# ============================================================
ERROR_CATEGORIES: dict[str, dict] = {
    # ── 参数与类型 (3类) ──
    "param_error": {
        "label": "参数错误",
        "keywords": ["参数", "类型错误", "required", "validation", "missing argument",
                     "invalid argument", "wrong type", "unexpected keyword",
                     "positional argument", "takes 0 positional"],
        "severity": "high",
        "fix_hint": "检查工具调用的参数格式和类型是否与 schema 一致",
        "retry_strategy": "修正参数后重试",
    },
    "syntax_error": {
        "label": "语法错误",
        "keywords": ["SyntaxError", "syntax error", "invalid syntax",
                     "EOL while scanning", "unexpected EOF", "indentation",
                     "IndentationError", "unterminated string",
                     "expected ':'", "cannot assign to"],
        "severity": "high",
        "fix_hint": "修正代码语法，检查缩进、括号匹配、引号配对",
        "retry_strategy": "修正语法后重新执行",
    },
    "type_error": {
        "label": "类型错误",
        "keywords": ["TypeError", "type error", "not iterable", "not callable",
                     "not subscriptable", "NoneType", "has no attribute",
                     "can't multiply", "unsupported operand"],
        "severity": "high",
        "fix_hint": "检查变量类型，确保操作符/函数适用于该类型",
        "retry_strategy": "添加类型转换或空值检查后重试",
    },

    # ── 文件与IO (3类) ──
    "file_error": {
        "label": "文件操作错误",
        "keywords": ["FileNotFoundError", "FileExistsError", "IsADirectoryError",
                     "NotADirectoryError", "file not found", "no such file",
                     "cannot open", "read-only", "disk full", "too many open files",
                     "directory not empty"],
        "severity": "medium",
        "fix_hint": "确认文件路径、权限和磁盘空间",
        "retry_strategy": "检查路径是否存在/创建目录后重试",
    },
    "encoding_error": {
        "label": "编码错误",
        "keywords": ["UnicodeDecodeError", "UnicodeEncodeError",
                     "encoding", "decode", "charmap", "codec can't",
                     "illegal multibyte", "invalid start byte",
                     "utf-8", "gbk", "latin", "ascii codec"],
        "severity": "medium",
        "fix_hint": "指定正确的编码格式 (utf-8/gbk/latin-1)，或使用 errors='ignore'",
        "retry_strategy": "换用编码或忽略编码错误后重试",
    },
    "parse_error": {
        "label": "解析/反序列化错误",
        "keywords": ["JSONDecodeError", "json decode", "parse error",
                     "xml.etree", "ParseError", "invalid format",
                     "malformed", "unexpected token", "deserialize",
                     "not valid JSON", "Expecting value",
                     "Extra data", "unterminated string"],
        "severity": "medium",
        "fix_hint": "检查数据格式，确认输入是有效的 JSON/XML/YAML",
        "retry_strategy": "截取有效部分或修复格式后重新解析",
    },

    # ── 权限与安全 (3类) ──
    "permission_error": {
        "label": "权限错误",
        "keywords": ["PermissionError", "权限", "拒绝", "denied", "access denied",
                     "not permitted", "operation not permitted",
                     "privilege", "sudo", "elevation", "requires elevation",
                     "administrator", "root required"],
        "severity": "critical",
        "fix_hint": "切换方式、提升权限或改用用户态操作",
        "retry_strategy": "提升权限或改用替代方案后重试",
    },
    "auth_error": {
        "label": "认证/授权失败",
        "keywords": ["401", "403", "unauthorized", "forbidden",
                     "authentication", "invalid token", "expired token",
                     "invalid credentials", "login failed", "not authenticated",
                     "API key", "invalid api key", "bearer", "token expired"],
        "severity": "critical",
        "fix_hint": "检查 API Key/Token 是否有效、是否过期",
        "retry_strategy": "刷新 Token 或重新认证后重试",
    },
    "sandbox_error": {
        "label": "沙箱/环境限制",
        "keywords": ["sandbox", "沙箱", "restricted", "not allowed",
                     "security policy", "blocked", "disabled",
                     "read-only filesystem", "seccomp", "capability",
                     "container", "cgroup", "jail"],
        "severity": "high",
        "fix_hint": "改用沙箱允许的操作方式或调整执行环境",
        "retry_strategy": "使用允许的 API/路径替代",
    },

    # ── 资源与依赖 (3类) ──
    "dependency_error": {
        "label": "依赖缺失/版本冲突",
        "keywords": ["ModuleNotFoundError", "ImportError", "module",
                     "import", "安装", "install", "not installed",
                     "No module named", "pip install", "missing module",
                     "version conflict", "requires", "incompatible"],
        "severity": "high",
        "fix_hint": "安装缺失的依赖或升级/降级版本",
        "retry_strategy": "安装依赖后重试",
    },
    "memory_error": {
        "label": "内存/OOM错误",
        "keywords": ["MemoryError", "out of memory", "OOM", "内存不足",
                     "allocation failed", "cannot allocate",
                     "CUDA out of memory", "heap", "stack overflow",
                     "recursion limit", "maximum recursion"],
        "severity": "critical",
        "fix_hint": "减少数据量、分批处理或用流式方式",
        "retry_strategy": "分批处理或减少数据量后重试",
    },
    "resource_error": {
        "label": "资源耗尽/不足",
        "keywords": ["resource exhausted", "quota exceeded", "limit reached",
                     "too many", "connection pool", "exhausted",
                     "资源不足", "bottleneck", "throttle", "capacity"],
        "severity": "high",
        "fix_hint": "释放资源、增加池大小或等待后重试",
        "retry_strategy": "等待资源释放或扩大池后重试",
    },

    # ── 网络与API (4类) ──
    "timeout_error": {
        "label": "超时",
        "keywords": ["timeout", "超时", "timed out", "timedout",
                     "TimeoutError", "ReadTimeout", "ConnectTimeout",
                     "request timed out", "deadline exceeded"],
        "severity": "medium",
        "fix_hint": "增加超时时间、分片执行或检查网络状况",
        "retry_strategy": "增加超时时间或分批后重试",
    },
    "network_error": {
        "label": "网络不可达",
        "keywords": ["ConnectionError", "ConnectionRefusedError",
                     "ConnectionResetError", "connection refused",
                     "connection reset", "network unreachable",
                     "host unreachable", "name resolution",
                     "DNS", "getaddrinfo", "no route to host",
                     "EOF occurred", "broken pipe"],
        "severity": "high",
        "fix_hint": "检查网络连接、DNS 解析、目标服务是否在线",
        "retry_strategy": "检查网络后重试（指数退避）",
    },
    "api_error": {
        "label": "API 错误响应",
        "keywords": ["API", "api error", "internal server error",
                     "bad gateway", "service unavailable",
                     "5xx", "500", "502", "503", "504",
                     "upstream", "backend", "server error"],
        "severity": "high",
        "fix_hint": "检查 API 状态、等待服务恢复或降级处理",
        "retry_strategy": "指数退避重试，最多 3 次",
    },
    "rate_limit_error": {
        "label": "频率限制/限流",
        "keywords": ["429", "rate limit", "too many requests",
                     "frequency", "throttle", "quota", "限流",
                     "请求过于频繁", "try again later", "retry-after",
                     "rate exceeded", "requests per"],
        "severity": "medium",
        "fix_hint": "降低请求频率、增加间隔或使用批量接口",
        "retry_strategy": "等待 retry-after 时间后重试",
    },

    # ── 工具与执行 (3类) ──
    "tool_error": {
        "label": "工具执行失败",
        "keywords": ["tool execution failed", "command failed",
                     "subprocess", "exit code", "nonzero exit",
                     "returned non-zero", "execution error",
                     "tool error", "command not found",
                     "executable not found", "which: no"],
        "severity": "medium",
        "fix_hint": "检查工具/命令是否存在、参数是否正确",
        "retry_strategy": "修正命令或安装工具后重试",
    },
    "llm_error": {
        "label": "LLM 调用失败",
        "keywords": ["llm error", "model error", "generation failed",
                     "token limit", "context length", "max tokens",
                     "content filtered", "safety filter",
                     "model not available", "overloaded",
                     "hallucination", "refusal"],
        "severity": "high",
        "fix_hint": "减少上下文长度、换模型或检查安全过滤",
        "retry_strategy": "截断上下文或换模型后重试",
    },
    "retry_error": {
        "label": "重试耗尽",
        "keywords": ["retry exhausted", "max retries", "all retries failed",
                     "retry limit", "too many retries", "放弃重试",
                     "still failing after", "attempts failed"],
        "severity": "medium",
        "fix_hint": "检查根本原因，手动干预或更换策略",
        "retry_strategy": "分析根因后更换策略，不盲目重试",
    },

    # ── 逻辑与状态 (4类) ──
    "state_error": {
        "label": "状态不一致",
        "keywords": ["state", "stale", "conflict", "race condition",
                     "inconsistent", "corrupted", "unexpected state",
                     "already exists", "duplicate", "modified by another",
                     "concurrent modification", "version mismatch"],
        "severity": "high",
        "fix_hint": "刷新状态、加锁或使用乐观并发控制",
        "retry_strategy": "刷新状态后重试",
    },
    "schema_error": {
        "label": "模式/格式不匹配",
        "keywords": ["schema", "validation error", "does not match",
                     "unexpected field", "missing field",
                     "required property", "additional properties",
                     "pattern", "format", "不符合", "格式错误",
                     "KeyError", "键错误"],
        "severity": "medium",
        "fix_hint": "检查数据结构与预期 schema 是否一致",
        "retry_strategy": "调整数据结构以匹配 schema 后重试",
    },
    "loop_error": {
        "label": "循环/死锁",
        "keywords": ["循环", "重复", "loop", "stuck", "infinite",
                     "deadlock", "livelock", "circular", "recursive",
                     "already tried", "重复操作", "same error"],
        "severity": "high",
        "fix_hint": "添加循环检测、上限计数或改用不同策略",
        "retry_strategy": "打破循环模式，使用全新方式重试",
    },
    "config_error": {
        "label": "配置错误",
        "keywords": ["config", "configuration", "misconfigured",
                     "invalid config", "missing config",
                     "environment variable", "env var", "not set",
                     ".yaml", ".json", "settings", "配置",
                     "未配置", "未设置"],
        "severity": "high",
        "fix_hint": "检查配置文件和环境变量是否设置正确",
        "retry_strategy": "修正配置后重试",
    },

    # ── 内容与数据 (2类) ──
    "content_error": {
        "label": "内容/数据异常",
        "keywords": ["empty response", "null", "None", "空结果",
                     "unexpected content", "garbage", "corrupted data",
                     "truncated", "incomplete", "missing data",
                     "empty", "空白", "无内容"],
        "severity": "medium",
        "fix_hint": "检查数据源是否正常，添加空值防护",
        "retry_strategy": "换参数或数据源重试",
    },
    "not_found_error": {
        "label": "资源不存在",
        "keywords": ["不存在", "找不到", "not found", "no such", "404",
                     "does not exist", "cannot find", "无法找到",
                     "未找到", "missing", "disappeared"],
        "severity": "medium",
        "fix_hint": "先用搜索确认目标存在，或创建后再操作",
        "retry_strategy": "确认资源存在或创建后重试",
    },

    # ── 未知（兜底） ──
    "unknown_error": {
        "label": "未分类错误",
        "keywords": [],
        "severity": "low",
        "fix_hint": "分析错误详情后归类",
        "retry_strategy": "记录详细信息后手动分析",
    },
}

# 快速索引：关键词 -> 类别
_KEYWORD_INDEX: dict[str, str] = {}
for cat_name, cat_info in ERROR_CATEGORIES.items():
    for kw in cat_info["keywords"]:
        k = kw.lower()
        _KEYWORD_INDEX[k] = cat_name

# 类别按 severity 排序（用于优先级计算）
_SEVERITY_ORDER = {"critical": 5, "high": 4, "medium": 3, "low": 2}
ERROR_CATEGORY_COUNT = len(ERROR_CATEGORIES)  # 26


# ============================================================
# 领域层次结构（扩展）
# ============================================================
DOMAIN_HIERARCHY = {
    "desktop": {
        "label": "桌面控制",
        "children": ["mouse", "keyboard", "window", "screenshot"]
    },
    "file": {
        "label": "文件管理",
        "children": ["read", "write", "delete", "list", "archive", "watch"]
    },
    "system": {
        "label": "系统管理",
        "children": ["execute", "info", "app", "process", "package", "service"]
    },
    "web": {
        "label": "网络",
        "children": ["search", "download", "api", "fetch", "browse"]
    },
    "code": {
        "label": "代码",
        "children": ["python", "shell", "debug", "analysis", "generate"]
    },
    "memory": {
        "label": "记忆/知识",
        "children": ["store", "recall", "search", "index", "clean"]
    },
    "ai": {
        "label": "AI/LLM",
        "children": ["completion", "embedding", "vision", "voice"]
    },
}


def classify_error(error_text: str) -> tuple[str, str, str]:
    """对错误文本进行分类，返回 (类别, 严重性, 修复提示)

    使用多层匹配:
    1. 精确关键词匹配
    2. 正则模式匹配
    3. 堆栈跟踪解析
    """
    if not error_text:
        return ("unknown_error", "low", ERROR_CATEGORIES["unknown_error"]["fix_hint"])

    err_lower = error_text.lower()

    # ── 第1层：精确关键词匹配 ──
    matches: dict[str, int] = {}  # category -> score
    for kw, cat_name in _KEYWORD_INDEX.items():
        if kw in err_lower:
            matches[cat_name] = matches.get(cat_name, 0) + 1

    if matches:
        # 得分最高的类别
        best = max(matches, key=lambda c: (matches[c], _SEVERITY_ORDER.get(
            ERROR_CATEGORIES[c]["severity"], 0)))
        info = ERROR_CATEGORIES[best]
        return (best, info["severity"], info["fix_hint"])

    # ── 第2层：正则模式匹配 ──
    regex_rules = [
        (r"ModuleNotFoundError|ImportError|No module named", "dependency_error"),
        (r"PermissionError|permission denied|not permitted", "permission_error"),
        (r"FileNotFoundError|no such file|ENOENT", "file_error"),
        (r"TypeError|AttributeError", "type_error"),
        (r"SyntaxError|IndentationError", "syntax_error"),
        (r"ConnectionError|ConnectionRefusedError|ConnectionResetError", "network_error"),
        (r"TimeoutError|timed\s*out|deadline exceeded", "timeout_error"),
        (r"MemoryError|out of memory|OOM", "memory_error"),
        (r"JSONDecodeError|json\.decode|ParseError", "parse_error"),
        (r"UnicodeDecodeError|UnicodeEncodeError|codec can't", "encoding_error"),
        (r"KeyError|键错误", "schema_error"),
        (r"4\d{2}\b", "api_error"),
        (r"5\d{2}\b", "api_error"),
        (r"rate.?limit|too many requests|429", "rate_limit_error"),
        (r"subprocess|exit code|nonzero exit", "tool_error"),
        (r"token|context length|model|hallucination|refusal|safety filter", "llm_error"),
        (r"auth|unauthorized|forbidden|login|invalid token", "auth_error"),
        (r"config|configuration|environment variable|\.yaml|\.env", "config_error"),
        (r"sandbox|restricted|security policy|blocked|disabled", "sandbox_error"),
        (r"retry|max retries|all retries|attempts failed", "retry_error"),
        (r"state|stale|conflict|duplicate|concurrent|corrupted", "state_error"),
        (r"loop|stuck|infinite|deadlock|already tried|circular", "loop_error"),
        (r"quota|exhausted|too many|limit reached|throttle", "resource_error"),
        (r"empty|null|None|空|missing data|truncated", "content_error"),
        (r"not found|不存在|找不到|does not exist|404", "not_found_error"),
    ]

    for pattern, cat_name in regex_rules:
        if re.search(pattern, err_lower):
            info = ERROR_CATEGORIES[cat_name]
            return (cat_name, info["severity"], info["fix_hint"])

    # ── 第3层：堆栈跟踪解析 ──
    if "Traceback" in error_text or "traceback" in err_lower:
        # 尝试从堆栈中提取异常类型
        exc_match = re.search(r'(\w+Error|\w+Exception)', error_text)
        if exc_match:
            exc_name = exc_match.group(1).lower()
            for cat_name, info in ERROR_CATEGORIES.items():
                for kw in info["keywords"]:
                    if kw.lower() in exc_name:
                        return (cat_name, info["severity"], info["fix_hint"])

    return ("unknown_error", "low", ERROR_CATEGORIES["unknown_error"]["fix_hint"])


def classify_error_batch(errors: list[str]) -> dict[str, int]:
    """批量分类错误，返回类别计数"""
    counts: dict[str, int] = {}
    for e in errors:
        cat, _, _ = classify_error(e)
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def get_error_severity(category: str) -> str:
    """获取错误严重性"""
    return ERROR_CATEGORIES.get(category, {}).get("severity", "low")


def get_fix_hint(category: str) -> str:
    """获取修复提示"""
    return ERROR_CATEGORIES.get(category, {}).get("fix_hint", "未知错误，请记录详情")


def get_retry_strategy(category: str) -> str:
    """获取重试策略"""
    return ERROR_CATEGORIES.get(category, {}).get("retry_strategy", "记录后跳过")


def list_all_categories() -> list[dict]:
    """列出所有 26 个错误类别"""
    return [
        {"name": name, "label": info["label"], "severity": info["severity"]}
        for name, info in ERROR_CATEGORIES.items()
    ]


# ============================================================
# 工具→领域映射（增强）
# ============================================================
_TOOL_DOMAIN_MAP: dict[str, str] = {
    # desktop
    "screenshot": "desktop", "mouse_click": "desktop", "mouse_move": "desktop",
    "mouse_drag": "desktop", "mouse_scroll": "desktop", "mouse_double_click": "desktop",
    "keyboard_type": "desktop", "keyboard_press": "desktop",
    "set_volume": "desktop", "wait": "desktop",
    "focus_window": "desktop.window", "list_windows": "desktop.window",
    "get_foreground_window": "desktop.window", "read_ui_window": "desktop.window",
    "get_window_state": "desktop.window", "click_element": "desktop.window",
    # file
    "file_read": "file", "file_write": "file", "file_list": "file",
    "file_delete": "file", "create_workspace_file": "file",
    "create_temp_file": "file", "list_workspace": "file",
    "cleanup_temp": "file", "organize_workspace": "file",
    "reflect_on_workspace": "file",
    # system
    "system_info": "system", "system_execute": "system",
    "open_file": "system", "open_app": "system",
    "find_app": "system", "launch_app": "system",
    "camera_snapshot": "system", "camera_list": "system",
    "run_code": "code",
    # web
    "web_search": "web", "web_fetch": "web",
    "http_get": "web", "http_post": "web",
    # AI
    "llm_complete": "ai", "llm_chat": "ai",
    "embed": "ai", "vision_analyze": "ai",
    # memory
    "memory_store": "memory", "memory_recall": "memory",
    "memory_search": "memory", "memory_index": "memory",
}


def map_tool_to_domain(tool_name: str) -> str:
    """将工具名映射到领域层次（带缓存查找）"""
    if not tool_name:
        return "general"
    t = tool_name.lower()
    if t in _TOOL_DOMAIN_MAP:
        return _TOOL_DOMAIN_MAP[t]
    return "general"


# ============================================================
# ReflectionResult 数据类
# ============================================================
@dataclass
class ErrorDetail:
    """单个错误的详细信息"""
    tool: str = ""
    error: str = ""
    category: str = "unknown_error"
    category_label: str = ""
    severity: str = "low"
    fix_hint: str = ""
    retry_strategy: str = ""
    domain: str = "general"
    timestamp: float = 0.0


@dataclass
class ReflectionResult:
    """一次反思的结果"""
    id: str = ""
    goal_achieved: bool = False
    success_count: int = 0
    failure_count: int = 0
    failures: list[dict] = field(default_factory=list)
    error_details: list[ErrorDetail] = field(default_factory=list)
    root_causes: list[str] = field(default_factory=list)
    reusable_lesson: str = ""
    priority: int = 1
    domain: str = "general"
    created_at: float = 0.0
    style_lessons: list[str] = field(default_factory=list)
    error_breakdown: dict[str, int] = field(default_factory=dict)  # 类别计数
    recovery_plan: str = ""  # 恢复建议


# ============================================================
# Reflector 类
# ============================================================
class Reflector:
    """任务执行后反思，提炼可复用经验（26类错误分类）"""

    def __init__(self, brain=None):
        self.brain = brain
        self._last_reflection: Optional[ReflectionResult] = None
        self._error_history: list[ErrorDetail] = []  # 累积错误历史

    def reflect(self, user_input: str, action_history: list[dict],
                planner_output: str = "") -> ReflectionResult:
        """对一次任务执行进行结构化反思"""
        now = time.time()
        successes = [a for a in action_history if a.get("result") == "success"]
        failures = [a for a in action_history if a.get("result") == "failure"]

        # 分析失败，使用 26 类分类器
        failure_details = []
        error_details: list[ErrorDetail] = []
        root_causes: list[str] = []
        domains_affected: set[str] = set()
        error_breakdown: dict[str, int] = {}

        for f in failures:
            error_text = f.get("error", "")
            cat, severity, fix_hint = classify_error(error_text)
            domain = map_tool_to_domain(f.get("tool", ""))
            domains_affected.add(domain)

            ed = ErrorDetail(
                tool=f.get("tool", ""),
                error=error_text[:200],
                category=cat,
                category_label=ERROR_CATEGORIES.get(cat, {}).get("label", cat),
                severity=severity,
                fix_hint=fix_hint,
                retry_strategy=get_retry_strategy(cat),
                domain=domain,
                timestamp=now,
            )
            error_details.append(ed)
            self._error_history.append(ed)

            failure_details.append({
                "tool": f.get("tool", ""),
                "error": error_text[:100],
                "category": cat,
                "category_label": ed.category_label,
                "severity": severity,
                "fix_hint": fix_hint,
                "domain": domain,
            })

            if cat not in root_causes:
                root_causes.append(cat)

            error_breakdown[cat] = error_breakdown.get(cat, 0) + 1

        # 限制历史大小
        if len(self._error_history) > 500:
            self._error_history = self._error_history[-200:]

        # 确定优先级
        priority = self._calc_priority(failures, domains_affected, error_breakdown)

        # 提炼可复用教训
        lesson = self._extract_lesson(failures, user_input)

        # 生成恢复计划
        recovery_plan = self._generate_recovery_plan(error_breakdown)

        # 判断目标是否达成
        goal_achieved = len(successes) > 0 and len(failures) == 0

        # Style assessment
        style_lessons = []
        if self.brain and user_input:
            try:
                from knowledge.brain import Brain
                ud = Brain.extract_style(user_input)
                if ud.get("复读数据", 0) > 0:
                    style_lessons.append(
                        "user msg contains raw data format - assistant likely repeated tool output before")
            except Exception:
                pass

        result = ReflectionResult(
            id=hashlib.md5(f"ref{user_input}{now}".encode()).hexdigest()[:12],
            goal_achieved=goal_achieved,
            success_count=len(successes),
            failure_count=len(failures),
            failures=failure_details,
            error_details=error_details,
            root_causes=root_causes,
            reusable_lesson=lesson,
            priority=priority,
            domain=", ".join(sorted(domains_affected)) if domains_affected else "general",
            created_at=now,
            style_lessons=style_lessons,
            error_breakdown=error_breakdown,
            recovery_plan=recovery_plan,
        )
        self._last_reflection = result

        # 日志
        cat_summary = ", ".join(f"{ERROR_CATEGORIES.get(c, {}).get('label', c)}({n})"
                                for c, n in sorted(error_breakdown.items(),
                                                   key=lambda x: -x[1])[:5])
        logger.info(
            f"🔍 反思完成: {'✅成功' if goal_achieved else '❌部分失败'} "
            f"({len(successes)}成功/{len(failures)}失败) "
            f"优先级:{priority} | 错误分布: {cat_summary}"
        )

        # 将高优先级经验写入 brain
        if self.brain and lesson and priority >= 3:
            self.brain.learn_fact(
                f"[经验规则] {lesson}",
                category=f"experience.{'.'.join(sorted(domains_affected)) if domains_affected else 'general'}",
                source="self_reflection"
            )
            logger.info(f"📝 经验入库: [{priority}★] {lesson[:60]}...")

        return result

    def _calc_priority(self, failures: list, domains: set,
                       error_breakdown: dict[str, int]) -> int:
        """计算经验优先级 1-5 (考虑 26 类严重性)"""
        if not failures:
            return 1

        priority = 2

        # 影响多个领域 → 更优先
        if len(domains) >= 2:
            priority += 1
        if len(domains) >= 3:
            priority += 1

        # 致命错误 → 提升优先级
        critical_errors = {"permission_error", "auth_error", "memory_error"}
        high_errors = {"param_error", "syntax_error", "type_error",
                       "dependency_error", "sandbox_error", "network_error",
                       "api_error", "state_error", "loop_error",
                       "resource_error", "llm_error", "config_error"}
        for ecat in error_breakdown:
            if ecat in critical_errors:
                priority += 2
            elif ecat in high_errors:
                priority += 1

        # 频繁失败 → 更优先
        if len(failures) >= 3:
            priority += 1
        if len(failures) >= 5:
            priority += 1

        return min(5, priority)

    def _extract_lesson(self, failures: list, user_input: str) -> str:
        """从失败中提炼可复用的规则（按 26 类处理）"""
        if not failures:
            return ""

        # 按类别聚合
        by_type: dict[str, list] = {}
        for f in failures:
            cat = f.get("category", "unknown_error")
            if cat not in by_type:
                by_type[cat] = []
            by_type[cat].append(f)

        lessons: list[str] = []

        # 按严重性排序处理
        category_order = sorted(
            by_type.keys(),
            key=lambda c: _SEVERITY_ORDER.get(
                ERROR_CATEGORIES.get(c, {}).get("severity", "low"), 0),
            reverse=True,
        )

        for cat in category_order:
            tools = ", ".join(set(f["tool"] for f in by_type[cat]))
            info = ERROR_CATEGORIES.get(cat, {})
            hint = info.get("fix_hint", "")
            label = info.get("label", cat)

            if hint:
                lessons.append(f"[{label}] 使用{tools}时: {hint}")
            else:
                lessons.append(f"[{label}] 使用{tools}时失败，需手动排查")

        if lessons:
            return " ; ".join(lessons[:4])
        tool_names = ", ".join(set(f["tool"] for f in failures[:3]))
        return f"在'{user_input[:30]}'场景中, {tool_names}易失败"

    def _generate_recovery_plan(self, error_breakdown: dict[str, int]) -> str:
        """根据错误分布生成恢复计划"""
        if not error_breakdown:
            return ""

        steps: list[str] = []
        # 按严重性和频率排序
        sorted_errors = sorted(
            error_breakdown.items(),
            key=lambda x: (
                _SEVERITY_ORDER.get(ERROR_CATEGORIES.get(x[0], {}).get("severity", "low"), 0),
                x[1],
            ),
            reverse=True,
        )

        for i, (cat, count) in enumerate(sorted_errors[:5]):
            strategy = get_retry_strategy(cat)
            label = ERROR_CATEGORIES.get(cat, {}).get("label", cat)
            steps.append(f"  {i+1}. [{label}] ×{count}: {strategy}")

        return "\n".join(steps)

    def get_error_stats(self) -> dict:
        """获取累积错误统计（从历史记录中）"""
        stats: dict[str, dict] = {}
        for ed in self._error_history:
            if ed.category not in stats:
                stats[ed.category] = {
                    "label": ed.category_label,
                    "severity": ed.severity,
                    "count": 0,
                    "tools": set(),
                    "domains": set(),
                }
            stats[ed.category]["count"] += 1
            stats[ed.category]["tools"].add(ed.tool)
            stats[ed.category]["domains"].add(ed.domain)

        # 转换 set 为 list
        for v in stats.values():
            v["tools"] = list(v["tools"])
            v["domains"] = list(v["domains"])

        return stats

    def get_summary(self) -> str:
        """获取上次反思的摘要文本"""
        if not self._last_reflection:
            return "暂无反思记录"
        r = self._last_reflection
        lines = [
            f"🔍 反思 [{r.priority}★] | 26类分类器",
            f"目标达成: {'✅' if r.goal_achieved else '❌'}",
            f"成功率: {r.success_count}/{r.success_count + r.failure_count}",
        ]
        if r.error_breakdown:
            cats = ", ".join(
                f"{ERROR_CATEGORIES.get(c, {}).get('label', c)}({n})"
                for c, n in sorted(r.error_breakdown.items(), key=lambda x: -x[1])[:3]
            )
            lines.append(f"错误分布: {cats}")
        if r.root_causes:
            lines.append(f"根因: {', '.join(r.root_causes)}")
        if r.reusable_lesson:
            lines.append(f"教训: {r.reusable_lesson}")
        if r.recovery_plan:
            lines.append(f"恢复计划:\n{r.recovery_plan}")
        return "\n".join(lines)

    def clear_history(self):
        """清空错误历史"""
        self._error_history.clear()
        logger.info("错误历史已清空")


# ============================================================
# 便捷导出
# ============================================================
__all__ = [
    "Reflector", "ReflectionResult", "ErrorDetail",
    "classify_error", "classify_error_batch",
    "get_error_severity", "get_fix_hint", "get_retry_strategy",
    "list_all_categories", "map_tool_to_domain",
    "ERROR_CATEGORIES", "ERROR_CATEGORY_COUNT",
    "DOMAIN_HIERARCHY",
]
