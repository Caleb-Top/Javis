"""反思器 — 任务执行后结构化评估与经验提炼"""

import logging, time, hashlib
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("reflector")


# 错误类型分类
ERROR_CATEGORIES = {
    "param_error": ["参数", "类型错误", "required", "validation", "missing"],
    "permission_error": ["权限", "拒绝", "denied", "access", "沙箱", "sandbox"],
    "not_found_error": ["不存在", "找不到", "not found", "no such", "404"],
    "dependency_error": ["模块", "module", "import", "安装", "install", "not installed"],
    "timeout_error": ["超时", "timeout", "timed out", "超时"],
    "loop_error": ["循环", "重复", "loop", "stuck"],
    "api_error": ["API", "connection", "连接", "网络", "network"],
}

# 领域层次结构
DOMAIN_HIERARCHY = {
    "desktop": {
        "label": "桌面控制",
        "children": ["mouse", "keyboard", "window", "screenshot"]
    },
    "file": {
        "label": "文件管理",
        "children": ["read", "write", "delete", "list"]
    },
    "system": {
        "label": "系统管理",
        "children": ["execute", "info", "app", "process"]
    },
    "web": {
        "label": "网络",
        "children": ["search", "download", "api"]
    },
    "code": {
        "label": "代码",
        "children": ["python", "shell", "debug"]
    },
}


def classify_error(error_text: str) -> str:
    """对错误文本进行分类"""
    if not error_text:
        return "unknown"
    err_lower = error_text.lower()
    for category, keywords in ERROR_CATEGORIES.items():
        for kw in keywords:
            if kw in err_lower:
                return category
    return "unknown"


def map_tool_to_domain(tool_name: str) -> str:
    """将工具名映射到领域层次"""
    if not tool_name:
        return "general"
    t = tool_name.lower()
    if t in ("screenshot", "mouse_click", "mouse_move", "mouse_drag",
             "mouse_scroll", "mouse_double_click", "keyboard_type",
             "keyboard_press", "set_volume", "wait"):
        return "desktop"
    if t in ("focus_window", "list_windows", "get_foreground_window",
             "read_ui_window", "get_window_state", "click_element"):
        return "desktop.window"
    if t in ("file_read", "file_write", "file_list", "file_delete"):
        return "file"
    if t in ("system_info", "system_execute", "open_file", "open_app",
             "find_app", "launch_app"):
        return "system"
    if t in ("run_code",):
        return "code"
    if t in ("camera_snapshot", "camera_list"):
        return "system"
    if t in ("create_workspace_file", "create_temp_file", "list_workspace",
             "cleanup_temp", "organize_workspace", "reflect_on_workspace"):
        return "file"
    return "general"


@dataclass
class ReflectionResult:
    """一次反思的结果"""
    id: str = ""
    goal_achieved: bool = False     # 目标是否达成
    success_count: int = 0          # 成功步骤数
    failure_count: int = 0          # 失败步骤数
    failures: list[dict] = field(default_factory=list)  # 失败详情
    root_causes: list[str] = field(default_factory=list)  # 根因
    reusable_lesson: str = ""       # 可复用的经验教训
    priority: int = 1               # 优先级 1-5
    domain: str = "general"         # 所属领域
    created_at: float = 0.0
    style_lessons: list[str] = field(default_factory=list)  # 风格层面教训


class Reflector:
    """任务执行后反思，提炼可复用经验"""

    def __init__(self, brain=None):
        self.brain = brain
        self._last_reflection: Optional[ReflectionResult] = None

    def reflect(self, user_input: str, action_history: list[dict],
                planner_output: str = "") -> ReflectionResult:
        """对一次任务执行进行结构化反思"""
        now = time.time()
        successes = [a for a in action_history if a.get("result") == "success"]
        failures = [a for a in action_history if a.get("result") == "failure"]

        # 分析失败原因
        failure_details = []
        root_causes = set()
        domains_affected = set()

        for f in failures:
            error_text = f.get("error", "")
            cat = classify_error(error_text)
            domain = map_tool_to_domain(f.get("tool", ""))
            domains_affected.add(domain)
            failure_details.append({
                "tool": f.get("tool", ""),
                "error": error_text[:100],
                "category": cat,
                "domain": domain,
            })
            root_causes.add(cat)

        # 确定优先级
        priority = self._calc_priority(failures, domains_affected)

        # 提炼可复用教训 — 基于失败模式提取规则
        lesson = self._extract_lesson(failures, user_input)

        # 判断目标是否达成（有成功步骤且无致命失败）
        goal_achieved = len(successes) > 0 and len(failures) == 0

        # Style assessment (brain-native)
        style_lessons = []
        if self.brain and user_input:
            try:
                from knowledge.brain import Brain
                ud = Brain.extract_style(user_input)
                if ud.get("复读数据", 0) > 0:
                    style_lessons.append("user msg contains raw data format - assistant likely repeated tool output before")
            except Exception:
                pass

        result = ReflectionResult(
            id=hashlib.md5(f"ref{user_input}{now}".encode()).hexdigest()[:12],
            goal_achieved=goal_achieved,
            success_count=len(successes),
            failure_count=len(failures),
            failures=failure_details,
            root_causes=list(root_causes),
            reusable_lesson=lesson,
            priority=priority,
            domain=", ".join(sorted(domains_affected)) if domains_affected else "general",
            created_at=now,
            style_lessons=style_lessons,
        )
        self._last_reflection = result
        logger.info(f"🔍 反思完成: {'✅成功' if goal_achieved else '❌部分失败'} "
                    f"({len(successes)}成功/{len(failures)}失败) "
                    f"优先级:{priority}")

        # 将高优先级经验写入 brain
        if self.brain and lesson and priority >= 3:
            self.brain.learn_fact(
                f"[经验规则] {lesson}",
                category=f"experience.{'.'.join(sorted(domains_affected)) if domains_affected else 'general'}",
                source="self_reflection"
            )
            logger.info(f"📝 经验入库: [{priority}★] {lesson[:60]}...")

        return result

    def _calc_priority(self, failures: list, domains: set) -> int:
        """计算经验优先级 1-5"""
        if not failures:
            return 1
        priority = 2
        # 影响多个领域 → 更优先
        if len(domains) >= 2:
            priority += 1
        # 致命错误（权限/参数）→ 更优先
        categories = [f.get("category", "") for f in failures]
        if "permission_error" in categories or "param_error" in categories:
            priority += 1
        if "api_error" in categories or "dependency_error" in categories:
            priority += 1
        # 频繁失败 → 更优先
        if len(failures) >= 3:
            priority += 1
        return min(5, priority)

    def _extract_lesson(self, failures: list, user_input: str) -> str:
        """从失败中提炼可复用的规则"""
        if not failures:
            return ""

        # 归类失败，按类型聚合
        by_type = {}
        for f in failures:
            cat = f.get("category", "unknown")
            if cat not in by_type:
                by_type[cat] = []
            by_type[cat].append(f)

        # 为最常见的失败类型生成规则
        lessons = []
        # 参数错误
        if "param_error" in by_type:
            tools = ", ".join(set(f["tool"] for f in by_type["param_error"]))
            lessons.append(f"使用{tools}前需检查参数格式")
        # 没有找到
        if "not_found_error" in by_type:
            tools = ", ".join(set(f["tool"] for f in by_type["not_found_error"]))
            lessons.append(f"使用{tools}前先用搜索确认目标存在")
        # 依赖缺失
        if "dependency_error" in by_type:
            tools = ", ".join(set(f["tool"] for f in by_type["dependency_error"]))
            lessons.append(f"使用{tools}前先确认依赖已安装")
        # 权限错误
        if "permission_error" in by_type:
            lessons.append("涉及系统文件的操作需先切换方式或提升权限")
        # 超时
        if "timeout_error" in by_type:
            lessons.append("长时间操作需分段执行或使用异步方式")

        if lessons:
            return " ; ".join(lessons[:3])
        # 兜底
        tool_names = ", ".join(set(f["tool"] for f in failures[:3]))
        return f"在'{user_input[:30]}'场景中, {tool_names}易失败"

    def get_summary(self) -> str:
        """获取上次反思的摘要文本"""
        if not self._last_reflection:
            return "暂无反思记录"
        r = self._last_reflection
        lines = [
            f"🔍 反思 [{r.priority}★]",
            f"目标达成: {'✅' if r.goal_achieved else '❌'}",
            f"成功率: {r.success_count}/{r.success_count + r.failure_count}",
        ]
        if r.root_causes:
            lines.append(f"根因: {', '.join(r.root_causes)}")
        if r.reusable_lesson:
            lines.append(f"教训: {r.reusable_lesson}")
        return "\n".join(lines)
