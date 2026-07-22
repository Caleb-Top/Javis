"""
完整启动流程 — 从零到就绪的每一个步骤

这是 Claude Agent 启动的真实顺序。
每个步骤对应蒸馏文档的第十部分。
"""
from __future__ import annotations
import os, sys, logging, time
from typing import Optional

from agent_distill.core.types import (
    AgentConfig, MemoryEntry, SkillDef, Task, TaskStatus,
    MemoryType, PathMapper,
)
from agent_distill.core.llm_client import LLMClient
from agent_distill.core.tool_registry import ToolRegistry, build_builtin_tools
from agent_distill.core.system_prompt import SystemPromptBuilder
from agent_distill.core.agent import Agent, AgentResult
from agent_distill.core.memory_manager import MemoryManager
from agent_distill.core.skill_manager import SkillManager, register_builtin_skills

logger = logging.getLogger("agent.bootstrap")


class AgentBootstrap:
    """
    Agent 启动器 — 编排完整的启动流程。

    启动时序:
    1. Linux VM 启动 (后台, 10-30s)
    2. Memory 系统初始化
    3. Skills 系统初始化
    4. 工具注册
    5. 路径映射
    6. 系统提示组装
    7. Agent 就绪
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.start_time = time.time()
        self._steps_completed: list[str] = []

    def boot(self) -> Agent:
        """
        完整启动流程。

        Returns:
            就绪的 Agent 实例
        """
        logger.info("═══ Agent 启动中 ═══")

        # Step 1: 路径映射
        self._boot_path_mapper()
        self._steps_completed.append("path_mapper")

        # Step 2: Memory 系统
        self._boot_memory()
        self._steps_completed.append("memory")

        # Step 3: Skills 系统
        self._boot_skills()
        self._steps_completed.append("skills")

        # Step 4: 创建 Agent + 工具
        agent = self._boot_agent()
        self._steps_completed.append("agent")

        # Step 5: 组装系统提示
        self._boot_system_prompt(agent)
        self._steps_completed.append("system_prompt")

        elapsed = time.time() - self.start_time
        logger.info(f"═══ Agent 就绪 ({elapsed:.1f}s) — "
                     f"步骤: {' → '.join(self._steps_completed)} ═══")
        return agent

    def _boot_path_mapper(self):
        """
        Step 1: 建立路径映射。

        探测用户挂载的文件夹, 建立 Windows ↔ VM 映射表。
        逻辑:
        - 如果有 D:\Javis → mount_name="Javis"
        - 如果有 D:\Claude测试 → mount_name="Claude测试"
        - 内部生成 session_id
        """
        logger.info("  [1/5] 建立路径映射...")
        # 实际实现中, 从环境变量或配置文件读取挂载信息
        self.path_mapper = PathMapper(
            session_id="default",
            mounts={
                self.config.workspace_dir: os.path.basename(self.config.workspace_dir),
            },
        )

    def _boot_memory(self):
        """
        Step 2: 初始化 Memory 系统。

        1. 读取 MEMORY.md 索引
        2. 解析每个 .md 文件的 frontmatter
        3. 按类型排序: user > feedback > project > reference
        4. 取前 10 条准备注入

        索引格式: - [name](name.md) — description
        文件格式: ---\nname: xxx\ndescription: xxx\ntype: xxx\n---\ncontent
        """
        logger.info("  [2/5] 加载 Memory...")
        mgr = MemoryManager(self.config.memory_dir)
        self.memory_entries = mgr.load_for_prompt(max_entries=10)

        if self.memory_entries:
            logger.info(f"    已加载 {len(self.memory_entries)} 条记忆:")
            for m in self.memory_entries[:5]:
                logger.info(f"      [{m.type.value}] {m.name}: {m.description[:60]}")

    def _boot_skills(self):
        """
        Step 3: 初始化 Skills 系统。

        1. 扫 描 skills_dirs 中的所有 SKILL.md
        2. 注册内置 Skills (docx, xlsx, pptx, pdf 等)
        3. 构建 available_skills 列表 (name + description)

        每个 Skill 的 description 是触发匹配的关键。
        """
        logger.info("  [3/5] 加载 Skills...")
        self.skill_mgr = SkillManager(self.config.skills_dirs)

        # 用户安装的 Skills (从磁盘扫描)
        self.user_skills = self.skill_mgr.list_all()

        # 内置 Skills (硬编码)
        self.builtin_skills = register_builtin_skills()

        all_skills = list(self.user_skills) + list(self.builtin_skills)
        logger.info(f"    共 {len(all_skills)} 个 Skills: "
                     f"{', '.join(s.name for s in all_skills[:8])}{'...' if len(all_skills) > 8 else ''}")

    def _boot_agent(self):
        """
        Step 4: 创建 Agent 并注册工具。

        Agent.__init__() 流程:
        1. LLMClient(config) → 探测可用的 API provider
        2. ToolRegistry() → 注册内置工具
        3. 注册可选工具 (根据环境)
        """
        logger.info("  [4/5] 创建 Agent + 工具...")
        agent = Agent(self.config)

        # 注入路径映射
        agent.path_mapper = self.path_mapper

        # 加载 Skills 到 Agent
        for skill in self.user_skills:
            agent.load_skill(skill)
        for skill in self.builtin_skills:
            agent.load_skill(skill)

        # 加载 Memory
        agent.set_memories(self.memory_entries)

        # 注册 Memory 写入钩子
        memory_mgr = MemoryManager(self.config.memory_dir)
        def memory_after_tool(tool_call, result):
            if memory_mgr.should_write():
                # 在实际实现中, 这里会分析工具结果并决定是否写入
                logger.debug(f"Memory write check triggered by [{tool_call.name}]")

        agent.add_hook("after_tool", memory_after_tool)

        logger.info(f"    工具数: {agent.tools.tool_count}"
                     f"  | Skills: {len(self.user_skills) + len(self.builtin_skills)}"
                     f"  | Memory: {len(self.memory_entries)}")

        return agent

    def _boot_system_prompt(self, agent: Agent):
        """
        Step 5: 组装系统提示 (验证可构建)。

        系统提示 = BASE_BEHAVIOR + env + memory_context + skills_list + output_rules
        不存储在 Agent 中 — 每次 run() 时动态重建 (因为 memory 可能变化)。
        """
        logger.info("  [5/5] 验证系统提示...")
        builder = SystemPromptBuilder(username="User")
        builder.inject_skills(
            list(self.user_skills) + list(self.builtin_skills)
        )
        builder.inject_memories(self.memory_entries)
        prompt = builder.build()
        logger.info(f"    系统提示长度: {len(prompt)} 字符, {len(prompt.splitlines())} 行")


def boot_agent(workspace_dir: str, model: str = "auto") -> Agent:
    """快速启动 (兼容旧 API)"""
    config = AgentConfig(
        workspace_dir=workspace_dir,
        model=model,
        skills_dirs=[
            os.path.join(workspace_dir, "skills"),
            os.path.expanduser("~/.claude/skills"),
        ],
    )
    bootstrap = AgentBootstrap(config)
    return bootstrap.boot()
