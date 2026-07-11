"""Superpowers 桥梁 — 14 个顶级工作技能注入 JARVIS 大脑

每个 skill 读入后成为:
  ① 大脑中的知识 (供自主选择)
  ② tools_lib 中的工具 (供调用执行)
  ③ 可追踪的执行经验 (供学习成长)

架构:
  superpowers_invoke(skill_name, task_context) → 返回技能指导
    ↕ Brain 知识: 452+ 条技能事实
    ↕ 经验系统: 每次调用记录
    ↕ 语言引擎: 可扩展示例代码
"""
import os, json, logging, textwrap, time
from pathlib import Path
from core.tool_result import ToolResult
logger = logging.getLogger("superpowers")

# ── 静态数据 ──
_PLUGIN_DIR = Path(__file__).parent.parent / ".codex" / "plugins" / "superpowers"
_SKILLS_DIR = _PLUGIN_DIR / "skills"
_BRAIN = None

# 14 个技能的元数据 (用于自主选择)
SKILL_META = {
    "brainstorming": {
        "name": "brainstorming",
        "title": "创意构思与需求探索",
        "trigger": "任何创造性工作前 — 建功能、改行为、加组件",
        "gate": "必须先构思再编码, 不能跳过设计直接实现",
        "order": 1,
        "icon": "💡",
    },
    "writing-plans": {
        "name": "writing-plans",
        "title": "编写实施计划",
        "trigger": "有设计或需求文档后, 多步骤实施前",
        "gate": "每一步 2-5 分钟, 每步可独立测试",
        "order": 2,
        "icon": "📋",
    },
    "executing-plans": {
        "name": "executing-plans",
        "title": "按计划执行",
        "trigger": "有书面计划, 需逐个执行任务",
        "gate": "先批判性审查计划, 有疑虑先问, 执行完用 finishing 技能收尾",
        "order": 3,
        "icon": "⚙️",
    },
    "subagent-driven-development": {
        "name": "subagent-driven-development",
        "title": "子代理驱动开发 (本会话并行)",
        "trigger": "有实施计划, 任务独立, 在本会话中执行",
        "gate": "每任务派新子代理, 每任务后审查, 最后整体审查",
        "order": 3,
        "icon": "🤖",
    },
    "dispatching-parallel-agents": {
        "name": "dispatching-parallel-agents",
        "title": "并行调度独立子代理",
        "trigger": "2+ 任务完全独立、无共享状态、可并行",
        "gate": "每个子代理只有自己任务的上下文, 不继承会话状态",
        "order": 3,
        "icon": "🚀",
    },
    "test-driven-development": {
        "name": "test-driven-development",
        "title": "测试驱动开发 (TDD)",
        "trigger": "任何功能开发或 bug 修复, 在写实现代码之前",
        "gate": "先写失败测试 → 最小实现 → 验证通过 → 重构",
        "order": 2,
        "icon": "🔴",
    },
    "systematic-debugging": {
        "name": "systematic-debugging",
        "title": "系统性调试",
        "trigger": "任何 bug、测试失败、非预期行为, 在提修复方案之前",
        "gate": "必须先找到根因才能提修复, 症状修复等于失败",
        "order": 1,
        "icon": "🔍",
    },
    "requesting-code-review": {
        "name": "requesting-code-review",
        "title": "请求代码审查",
        "trigger": "完成任务后、合并大功能前、合入主分支前",
        "gate": "早审查、常审查, 用子代理做审查员",
        "order": 4,
        "icon": "👁️",
    },
    "receiving-code-review": {
        "name": "receiving-code-review",
        "title": "接收审查反馈",
        "trigger": "收到代码审查反馈后、实现建议前",
        "gate": "用技术评估而非情绪回应, 验证后再实现",
        "order": 4,
        "icon": "📝",
    },
    "verification-before-completion": {
        "name": "verification-before-completion",
        "title": "完成前验证",
        "trigger": "声称工作完成/修复/通过之前, 提交或创建 PR 之前",
        "gate": "必须有当次会话的验证输出证据才能声称完成",
        "order": 5,
        "icon": "✅",
    },
    "using-git-worktrees": {
        "name": "using-git-worktrees",
        "title": "隔离工作空间 (git worktree)",
        "trigger": "开始特性开发、需要和当前工作空间隔离、执行计划前",
        "gate": "先用平台原生工具, 没有才退到 git worktree",
        "order": 2,
        "icon": "🌳",
    },
    "finishing-a-development-branch": {
        "name": "finishing-a-development-branch",
        "title": "完成开发分支",
        "trigger": "实现完成、测试通过、需要决定如何整合工作",
        "gate": "先验证测试 → 检测环境 → 呈现选项 → 执行选择 → 清理",
        "order": 6,
        "icon": "🎯",
    },
    "writing-skills": {
        "name": "writing-skills",
        "title": "编写技能 (元技能)",
        "trigger": "创建新技能、编辑已有技能、部署前验证",
        "gate": "基于 TDD 的方法: 先写压力测试 → 看代理失败 → 写技能 → 验证",
        "order": 7,
        "icon": "📖",
    },
    "using-superpowers": {
        "name": "using-superpowers",
        "title": "使用 Superpowers 生态",
        "trigger": "任何对话开始时 — 确立如何找到和使用技能",
        "gate": "回复前必须先检查技能适用性, 有技能就必须用",
        "order": 0,
        "icon": "⚡",
    },
}

# 技能优先级顺序 (用于自主路由)
SKILL_ORDER = ["using-superpowers", "brainstorming", "systematic-debugging",
    "writing-plans", "test-driven-development", "using-git-worktrees",
    "subagent-driven-development", "dispatching-parallel-agents", "executing-plans",
    "requesting-code-review", "receiving-code-review",
    "verification-before-completion", "finishing-a-development-branch", "writing-skills"]


def set_brain(brain):
    """注入大脑实例"""
    global _BRAIN
    _BRAIN = brain
    logger.info("🧠 Superpowers 已连接大脑")


# ── 核心: 调用技能 ──

def superpowers_invoke(skill_name: str, task_context: str = "") -> str:
    """调用指定 Superpowers 技能, 返回完整指导文本"""
    sm_path = _SKILLS_DIR / skill_name / "SKILL.md"
    if not sm_path.exists():
        available = ", ".join(SKILL_META.keys())
        return f"[Superpowers] 未知技能 '{skill_name}'。可用: {available}"

    content = sm_path.read_text(encoding="utf-8")

    # 记录经验
    if _BRAIN:
        try:
            _BRAIN.learn_fact(
                f"Superpowers 技能调用: {skill_name} — {task_context[:80]}",
                category=f"superpowers.{skill_name}", source="superpowers", priority=2)
        except: pass

    return (
        f"╔══ Superpowers 技能 [{skill_name}] ══╗\n\n"
        f"📋 说明: {SKILL_META.get(skill_name, {}).get('title', '')}\n"
        f"🚦 触发条件: {SKILL_META.get(skill_name, {}).get('trigger', '')}\n\n"
        f"{content}\n\n"
        f"╚══ 请严格遵循以上技能指导 ══╝"
    )


def superpowers_detect(task_text: str) -> list:
    """根据任务文本自动推荐适用的技能 (按优先级排序)"""
    task_lower = task_text.lower()
    matches = []

    # 关键词匹配规则
    rules = {
        "systematic-debugging": ["bug", "error", "fail", "crash", "not working", "unexpected",
                                 "issue", "problem", "broken", "exception", "debug",
                                 "bug", "崩溃", "错误", "失败", "问题", "异常", "修", "排查"],
        "brainstorming": ["i want", "design", "create", "build", "new feature", "add ",
                          "suggest", "idea", "think", "propose", "plan to",
                          "设计", "创建", "新建", "新增", "构思", "创意", "功能", "需求",
                          "我想", "我要", "能不能", "建议"],
        "test-driven-development": ["tdd", "test first", "write test", "testing",
                                    "测试优先", "先写测试"],
        "writing-plans": ["plan", "roadmap", "step by step", "implement",
                          "计划", "步骤", "实施", "方案", "规划", "路线图"],
        "dispatching-parallel-agents": ["parallel", "simultaneous", "concurrent", "many tasks",
                                        "并行", "同时", "并发", "多个任务"],
        "verification-before-completion": ["verify", "confirm", "check", "validate", "test it",
                                           "验证", "确认", "检查", "测试一下", "验证一下"],
        "requesting-code-review": ["review", "code review", "check my code",
                                   "审查", "review", "代码审查", "检查代码"],
        "finishing-a-development-branch": ["merge", "pr", "pull request", "finish branch",
                                           "合并", "PR", "提pr", "合入"],
        "using-git-worktrees": ["worktree", "isolate", "separate branch", "feature branch",
                                "工作树", "隔离", "新分支"],
        "receiving-code-review": ["feedback", "review comment", "cr comment",
                                  "反馈", "review意见", "审查意见"],
        "subagent-driven-development": ["implement these", "execute plan", "tasks",
                                        "执行计划", "实施任务", "执行任务"],
        "executing-plans": ["execute plan", "run tasks",
                            "执行计划", "跑任务"],
        "writing-skills": ["create skill", "write skill", "new skill",
                           "创建技能", "写技能", "新技能"],
    }

    for skill_name, keywords in rules.items():
        for kw in keywords:
            if kw in task_lower:
                matches.append(skill_name)
                break

    # 去重并按优先级排序
    seen = set()
    ordered = []
    for s in SKILL_ORDER:
        if s in matches and s not in seen:
            ordered.append(s)
            seen.add(s)

    return ordered


def superpowers_skills_list() -> str:
    """列出所有可用技能"""
    lines = ["╔══ Superpowers 技能库 (14个) ══╗", ""]
    for name in SKILL_ORDER:
        meta = SKILL_META.get(name, {})
        icon = meta.get("icon", "🔧")
        title = meta.get("title", name)
        trigger = meta.get("trigger", "")
        lines.append(f"  {icon} [{name}] {title}")
        lines.append(f"     触发: {trigger}")
        lines.append("")
    lines.append("╚═════════════════════════════╝")
    return "\n".join(lines)


# ── 注入大脑 ──

def inject_to_brain(brain=None):
    """将所有技能知识注入大脑"""
    global _BRAIN
    if brain:
        _BRAIN = brain
    if not _BRAIN:
        logger.warning("大脑未连接, 跳过注入")
        return 0

    count = 0
    for name in SKILL_ORDER:
        meta = SKILL_META.get(name, {})
        try:
            _BRAIN.learn_fact(
                f"Superpowers 技能 [{meta.get('icon','')}] {meta['title']} — "
                f"触发条件: {meta['trigger']}. 铁律: {meta['gate']}. "
                f"用法: 调用 superpowers_invoke('{name}') 获取完整指导",
                category=f"superpowers.{name}",
                source="superpowers",
                priority=3,
            )
            count += 1
        except Exception as e:
            logger.debug(f"注入 {name} 失败: {e}")

    # 注入路由知识
    _BRAIN.learn_fact(
        "Superpowers 任务路由规则: "
        "1) 遇到 bug/error→systematic-debugging; "
        "2) 任何创造性工作前→brainstorming; "
        "3) 有需求后多步骤工作前→writing-plans; "
        "4) 写实现前→TDD; "
        "5) 多个独立模块可并行→dispatching-parallel-agents; "
        "6) 声称完成前→verification-before-completion; "
        "7) 合并前→requesting-code-review; "
        "8) 收到审查→receiving-code-review; "
        "9) 完成分支→finishing-a-development-branch; "
        "10) 不确定用哪个→superpowers_skills_list 查看全部",
        category="superpowers.routing",
        source="superpowers",
        priority=5,
    )
    count += 1

    logger.info(f"🧠 Superpowers 注入大脑: {count} 条")
    return count


# ── 工具注册 ──

def tools_for_registry():
    """注册为 JARVIS 工具"""
    from core.tool_registry import ToolDef

    # 先注入大脑
    inject_to_brain()

    def wrap_skill(skill_name):
        def handler(**kw):
            context = kw.get("context", kw.get("task", kw.get("description", "")))
            result = superpowers_invoke(skill_name, context)
            return ToolResult.success(result)
        return handler

    tools = []

    # 主入口
    tools.append(ToolDef(
        "superpowers",
        "调用 Superpowers 工作技能。技能包括: brainstorming(创意构思), systematic-debugging(系统调试), "
        "test-driven-development(TDD), writing-plans(编写计划), subagent-driven-development(子代理开发), "
        "dispatching-parallel-agents(并行执行), requesting-code-review(请求审查), receiving-code-review(接收审查), "
        "verification-before-completion(完成验证), finishing-a-development-branch(完成分支), "
        "using-git-worktrees(工作树隔离), executing-plans(执行计划), writing-skills(编写技能), using-superpowers(总纲)。"
        "参数 skill_name 指定技能名, context 描述当前任务",
        {"type": "object", "properties": {
            "skill_name": {"type": "string", "enum": list(SKILL_META.keys()),
                           "description": "技能名称"},
            "context": {"type": "string", "description": "当前任务描述, 帮助技能聚焦"},
        }, "required": ["skill_name"]},
        lambda **kw: ToolResult.success(
            superpowers_invoke(kw.get("skill_name", ""), kw.get("context", ""))),
        "superpowers",
    ))

    # 推荐路由
    tools.append(ToolDef(
        "superpowers_detect",
        "分析任务文本, 自动推荐适合的 Superpowers 技能 (按优先级排序)",
        {"type": "object", "properties": {
            "task": {"type": "string", "description": "任务描述文本"},
        }, "required": ["task"]},
        lambda **kw: ToolResult.success(
            str(superpowers_detect(kw.get("task", "")))),
        "superpowers",
    ))

    # 列出全部技能
    tools.append(ToolDef(
        "superpowers_skills_list",
        "列出所有可用的 Superpowers 技能及其触发条件",
        {"type": "object", "properties": {}, "required": []},
        lambda **kw: ToolResult.success(superpowers_skills_list()),
        "superpowers",
    ))

    return tools


# ── 工具处理函数 (给 tools_lib/loader.py 用) ──

TOOL_NAME = "superpowers"
TOOL_DESC = "Superpowers 工作技能引擎 — 14个专业工作流程的动态调用与自主路由"
TOOL_CATEGORY = "superpowers"
TOOL_PARAMS = {"type": "object", "properties": {}, "required": []}

def handler(**kwargs):
    """入口: 自动检测或手动调用"""
    action = kwargs.get("action", "detect")
    skill_name = kwargs.get("skill_name", "")
    task_context = kwargs.get("context", kwargs.get("task", ""))

    if action == "list":
        return {"success": True, "output": superpowers_skills_list()}
    elif action == "detect" and task_context:
        skills = superpowers_detect(task_context)
        return {"success": True, "output": f"推荐技能: {skills}"}
    elif action == "invoke" and skill_name:
        result = superpowers_invoke(skill_name, task_context)
        return {"success": True, "output": result}
    else:
        return {"success": True,
                "output": f"Superpowers 桥梁已就绪。可用: superpowers_invoke, superpowers_detect, superpowers_skills_list"}
