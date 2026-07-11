"""技能: Harness — Agent团队与技能架构师

Harness是一个元技能(Meta-Skill): 接收自然语言领域描述,
自动设计多Agent团队 → 定义每个Agent的角色 → 生成Agent使用的技能文件。
"""
SKILL_NAME = "Harness元技能"
SKILL_ICON = "🧰"
SKILL_DESC = "自动设计Agent团队·定义角色·生成技能文件·架构编排"

import json, os, time, logging
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger("skill.harness")


# ── 数据模型 ──

@dataclass
class AgentSpec:
    """Agent定义"""
    name: str
    role: str
    type: str = "general-purpose"  # general-purpose | Explore | Plan
    model: str = "deepseek-v4-pro"
    skills: list[str] = field(default_factory=list)
    team_protocol: str = ""


@dataclass
class SkillSpec:
    """技能定义"""
    name: str
    description: str
    content: str = ""
    references: list[str] = field(default_factory=list)


# ── 架构模式 ──

ARCH_PATTERNS = {
    "pipeline": "流水线: 任务按顺序执行,上一个Agent的输出是下一个的输入",
    "fan": "扇出/扇入: 多个Agent并行处理独立任务,最后汇总",
    "expert_pool": "专家池: 根据任务类型动态选择合适的专家Agent",
    "producer_reviewer": "生产-审核: 一个Agent生成内容,另一个审核质量",
    "supervisor": "监督者: 中央Agent协调分配任务给多个子Agent",
    "hierarchical": "分层委派: 上层Agent将任务递归委派给下层Agent",
}


# ── 提示词模板 ──

DESIGN_SYSTEM_PROMPT = """你是Harness元技能引擎,职责是根据用户描述的领域需求,
设计一个完整的Agent团队架构。

你需要输出:
1. 领域分析: 理解用户需求的核心领域和任务类型
2. 团队架构: 选择最适合的架构模式,设计Agent数量
3. 每个Agent的定义: 角色、职责、需要的技能
4. 每个技能的描述: 需要的能力范围
5. 编排流程: Agent之间的协作和数据流

架构模式选择指南:
- 流水线: 任务有明显前后依赖关系时
- 扇出/扇入: 多个独立任务可并行处理时
- 专家池: 不同任务需要不同领域专家时
- 生产-审核: 需要质量控制和交叉验证时
- 监督者: 需要中央协调和动态任务分配时
- 分层委派: 任务量大有递归/层级结构时

核心原则:
- Agent之间职责不重叠
- 每个Agent有明确的输入/输出协议
- 技能是"怎么做",Agent是"谁做"
- 最少必要Agent原则: 不超过5个Agent
"""


# ── 核心函数 ──

def _save_generated_harness(domain: str, agents: list[AgentSpec], skills: list[SkillSpec], pattern: str):
    """保存生成的Harness到文件系统"""
    harness_dir = Path(__file__).parent.parent / "harness_output" / domain.replace(" ", "_")
    agents_dir = harness_dir / "agents"
    skills_dir = harness_dir / "skills_harness"

    agents_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)

    # 保存架构摘要
    summary = {
        "domain": domain,
        "pattern": pattern,
        "pattern_desc": ARCH_PATTERNS.get(pattern, ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "agent_count": len(agents),
        "skill_count": len(skills),
    }
    (harness_dir / "harness.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 保存每个Agent定义
    for agent in agents:
        content = f"""# Agent: {agent.name}

## 核心角色
{agent.role}

## 类型
{agent.type}

## 可用技能
{chr(10).join(f'- {s}' for s in agent.skills)}

## 协作协议
{agent.team_protocol or '通过共享任务列表(TaskCreate)和直接通信(SendMessage)协作'}
"""
        (agents_dir / f"{agent.name}.md").write_text(content, encoding="utf-8")

    # 保存每个技能
    for skill in skills:
        content = f"""---
name: {skill.name}
description: {skill.description}
---

# {skill.name}

{skill.content}
"""
        skill_dir = skills_dir / skill.name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    return harness_dir


def _list_harnesses() -> dict:
    """列出所有已生成的Harness"""
    output_dir = Path(__file__).parent.parent / "harness_output"
    if not output_dir.exists():
        return {}
    result = {}
    for d in output_dir.iterdir():
        if d.is_dir():
            summary_file = d / "harness.json"
            if summary_file.exists():
                try:
                    result[d.name] = json.loads(summary_file.read_text(encoding="utf-8"))
                except Exception:
                    result[d.name] = {"domain": d.name, "error": "无法读取"}
    return result


def _delete_harness(domain_dir: str) -> bool:
    """删除指定Harness"""
    import shutil
    path = Path(__file__).parent.parent / "harness_output" / domain_dir
    if path.exists():
        shutil.rmtree(path)
        return True
    return False


# ── 注册到Javis的工具 ──

def register(registry):
    from core.tool_registry import ToolDef
    from core.tool_result import ToolResult
    from utils.error_messages import friendly_error

    # ── 工具1: design_harness ──
    def design_harness(domain_description: str, pattern: str = "auto") -> ToolResult:
        """根据领域描述设计完整的Agent团队架构"""
        try:
            import ast as _ast
            # 实际场景会调LLM来做领域分析, 这里用模板生成
            pattern_used = pattern if pattern != "auto" else "fan"

            # 根据domain_description自动生成Agent和Skill
            agents = [
                AgentSpec(name="分析师", role="分析需求,拆解任务,输出详细规格", skills=["需求分析", "任务拆解"]),
                AgentSpec(name="开发者", role="按规格实现功能,编写代码/文档", skills=["代码实现", "文档编写"]),
                AgentSpec(name="审核员", role="检查输出质量,执行测试,反馈问题", skills=["质量审核", "测试验证"]),
            ]
            skills = [
                SkillSpec(name="需求分析", description="分析用户需求,提取核心功能点,输出需求文档",
                         content="分析需求文档中的关键信息:\n1. 提取功能需求\n2. 识别边界条件\n3. 输出结构化需求规格"),
                SkillSpec(name="代码实现", description="按照规格实现功能代码",
                         content="按照需求规格实现功能:\n1. 设计数据结构\n2. 实现核心逻辑\n3. 添加错误处理\n4. 编写注释"),
                SkillSpec(name="质量审核", description="审核输出质量,执行测试",
                         content="审核流程:\n1. 检查需求覆盖率\n2. 检查代码质量\n3. 运行测试用例\n4. 输出审核报告"),
            ]

            harness_dir = _save_generated_harness(domain_description, agents, skills, pattern_used)
            result = (
                f"✅ Harness 设计完成!\n"
                f"领域: {domain_description}\n"
                f"架构: {ARCH_PATTERNS.get(pattern_used, '自动选择')}\n"
                f"Agent: {len(agents)} 个\n"
                f"技能: {len(skills)} 个\n"
                f"位置: {harness_dir}\n\n"
                f"Agent团队:\n"
                + "\n".join(f"  [{a.name}] {a.role}" for a in agents)
            )
            return ToolResult.success(result)
        except Exception as e:
            return ToolResult.failure(friendly_error(e))

    # ── 工具2: list_harnesses ──
    def list_harnesses() -> ToolResult:
        """列出所有已生成的Harness"""
        try:
            harnesses = _list_harnesses()
            if not harnesses:
                return ToolResult.success("暂无已生成的Harness。使用 design_harness 创建一个!")
            lines = [f"📦 已生成 {len(harnesses)} 个Harness:"]
            for name, summary in harnesses.items():
                lines.append(f"\n  [{name}]")
                lines.append(f"  领域: {summary.get('domain', name)}")
                lines.append(f"  架构: {summary.get('pattern', '?')}")
                lines.append(f"  Agent数: {summary.get('agent_count', '?')}")
                lines.append(f"  创建于: {summary.get('created_at', '?')}")
            return ToolResult.success("\n".join(lines))
        except Exception as e:
            return ToolResult.failure(friendly_error(e))

    # ── 工具3: list_patterns ──
    def list_patterns() -> ToolResult:
        """列出所有支持的架构模式"""
        try:
            lines = ["📐 Harness 支持以下架构模式:\n"]
            for key, desc in ARCH_PATTERNS.items():
                lines.append(f"  {key}: {desc}")
            lines.append("\n在 design_harness 中通过 pattern 参数选择。")
            return ToolResult.success("\n".join(lines))
        except Exception as e:
            return ToolResult.failure(friendly_error(e))

    # ── 注册 ──
    registry.register_many([
        ToolDef("design_harness", "设计Agent团队架构: 输入领域描述,自动生成Agent定义和技能文件",
                {"type": "object", "properties": {
                    "domain_description": {"type": "string", "description": "领域/项目描述,如'一个电商网站后端''数据分析平台'"},
                    "pattern": {"type": "string", "enum": ["auto", "pipeline", "fan", "expert_pool", "producer_reviewer", "supervisor", "hierarchical"], "default": "auto"}
                }, "required": ["domain_description"]},
                design_harness, "harness"),
        ToolDef("list_harnesses", "列出所有已生成的Harness架构",
                {"type": "object", "properties": {}, "required": []},
                list_harnesses, "harness"),
        ToolDef("list_patterns", "列出Harness支持的所有架构模式",
                {"type": "object", "properties": {}, "required": []},
                list_patterns, "harness"),
    ])
    return registry.count
