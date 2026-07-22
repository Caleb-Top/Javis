"""
系统提示构建器 — 组装注入给 LLM 的完整系统提示
"""
from __future__ import annotations
from typing import Optional
import os

from .types import SkillDef, MemoryEntry


class SystemPromptBuilder:
    """
    系统提示构建器。

    负责将所有注入层拼接成一条完整的系统提示:
    1. application_details  → Agent 自身介绍
    2. claude_behavior      → 行为规范
    3. skills_instructions  → Skills 使用说明
    4. available_skills     → 已安装 Skill 列表
    5. available_tools      → 工具说明 (动态注入到 tools 参数, 不放在 prompt 里)
    6. env                  → 日期/用户名
    7. auto_memory          → 记忆系统提示
    8. producing_outputs    → 输出格式规范

    完整系统提示 = base_section + skills_section + memory_section + output_section
    """

    # ═══════════════════════════════════════════════
    # 基础行为规范 (精简版 — 完整版见 distill doc)
    # ═══════════════════════════════════════════════

    BASE_BEHAVIOR = """你是 Claude Agent，运行在桌面应用的轻量 Linux VM 沙箱中。

## 核心行为规则

**语调与格式:**
- 默认用自然段落。
- 只在用户要求时才使用列表/项目符号。
- 保持温暖友善。
- 认错但要保持自尊,聚焦解决问题。

**能力:**
- 你可以读写文件、执行 Shell 命令、搜索代码、调用外部 API。
- 你可以启动子代理并行处理独立任务。
- 你可以安装 Skills 来扩展自己的专业能力。
- 工作区文件夹是持久化的, 文件会保留到下次会话。

**文件路径:**
- `计算机://` 链接格式给用户看到的是 Windows 路径。
- 代码工具 (Read/Write) 用 Windows 路径。
- Shell 工具 (Bash) 用 VM 路径 (`/sessions/.../mnt/...`)。

**何时使用工具:**
- 创建文件/文档 → 总是实际创建文件, 不要只显示内容
- 涉及用户文件 → 先检查具体内容
- 复杂多步任务 → 先建 Task 列表
- 不确定需求 → 先 AskUserQuestion 澄清
- 需要搜索 → Grep/Glob 本地优先

**何时不使用工具:**
- 纯知识问题
- 用户上传的文件内容已在对话中
- 只需要解释概念
"""

    OUTPUT_RULES = """## 产出物规范

- 短内容 (<100 行) → 直接 Write 到工作区
- 长内容 (>100 行) → 先建骨架, 再分段 Edit
- 最终产出必须在工作区, 用 computer:// 链接分享
- 只提供链接, 不需要长篇解释文件内容 (用户能自己看)
"""

    def __init__(self, username: str = "User", date_str: str = ""):
        self.username = username
        self.date_str = date_str
        self._skills: list[SkillDef] = []
        self._memories: list[MemoryEntry] = []

    # ═══════════════════════════════════════════════
    # 注入 Skills
    # ═══════════════════════════════════════════════

    def inject_skills(self, skills: list[SkillDef]) -> None:
        """注入可用 Skills 列表"""
        self._skills = skills

    def get_skills_prompt(self) -> str:
        """生成 Skills 部分"""
        if not self._skills:
            return ""

        lines = ["## 可用 Skills\n"]
        lines.append("以下 Skills 提供了专业领域知识。触发时会自动加载其完整指令。\n")
        for s in self._skills:
            lines.append(f"- **{s.name}**: {s.description}")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════

    def inject_memories(self, memories: list[MemoryEntry]) -> None:
        """注入记忆条目"""
        self._memories = memories

    def get_memories_prompt(self) -> str:
        """生成 Memory 部分"""
        if not self._memories:
            return ""

        relevant = [m for m in self._memories if m.type.value in ("user", "feedback", "project")]

        if not relevant:
            return ""

        lines = ["## 已知上下文 (来自记忆系统)\n"]
        for m in relevant[:10]:  # 限制数量, 避免 token 爆炸
            summary = m.description or m.content[:100]
            lines.append(f"- [{m.type.value}] {summary}")
        return "\n".join(lines)

    # ═══════════════════════════════════════════════
    # 组装完整提示
    # ═══════════════════════════════════════════════

    def build(self) -> str:
        """组装最终的完整系统提示"""
        import datetime

        date_str = self.date_str or datetime.datetime.now().strftime("%Y-%m-%d")

        sections = [
            self.BASE_BEHAVIOR,
            f"## 环境\n当前日期: {date_str}\n用户: {self.username}",
        ]

        # Memory
        mem_section = self.get_memories_prompt()
        if mem_section:
            sections.append(mem_section)

        # Skills
        skills_section = self.get_skills_prompt()
        if skills_section:
            sections.append(skills_section)

        sections.append(self.OUTPUT_RULES)

        return "\n\n".join(sections)
