"""
Agent 主入口 — 一站式启动和配置

使用示例:
    from agent_distill import create_agent, AgentConfig

    config = AgentConfig(workspace_dir="D:/projects/foo")
    agent = create_agent(config)
    result = agent.run("读取 README.md 并帮你分析")
    print(result.content)
"""
from __future__ import annotations
import logging

from .core.types import (
    AgentConfig, Message, ToolCall, ToolDef, ToolResult,
    Role, SkillDef, MemoryEntry, Task, TaskStatus, MemoryType,
    PathMapper,
)
from .core.llm_client import LLMClient, LLMResponse
from .core.tool_registry import ToolRegistry
from .core.system_prompt import SystemPromptBuilder
from .core.agent import Agent, AgentResult
from .core.memory_manager import MemoryManager
from .core.skill_manager import SkillManager, register_builtin_skills
from .core.subagent import SubAgentRunner, SubAgentConfig, SubAgentResult
from .core.artifacts import ArtifactManager, ArtifactDef, build_artifact_html


def create_agent(config: AgentConfig) -> Agent:
    """
    创建并配置一个完整的 Agent 实例。

    包含:
    - LLM 客户端
    - 工具注册表 (含内置工具)
    - Skills 管理器 (含内置 Skills)
    - Memory 系统
    - 路径映射器
    """
    agent = Agent(config)

    # 路径映射
    agent.path_mapper = PathMapper(
        session_id="default",
        mounts={
            config.workspace_dir: os.path.basename(config.workspace_dir),
        },
    )

    # Skills
    skill_mgr = SkillManager(config.skills_dirs)
    builtin_skills = register_builtin_skills()
    for skill in builtin_skills:
        agent.load_skill(skill)

    # 也可以从磁盘加载用户安装的 Skills
    for skill in skill_mgr.list_all():
        agent.load_skill(skill)

    # Memory
    memory_mgr = MemoryManager(config.memory_dir)
    agent.set_memories(memory_mgr.load_for_prompt(max_entries=10))

    # 注册 Memory 写入钩子
    def memory_hook(tool_call: ToolCall, result: ToolResult):
        if memory_mgr.should_write():
            logger.info("Memory: 触发写入检查")

    agent.add_hook("after_tool", memory_hook)

    return agent


def create_minimal_agent(workspace_dir: str = ".", model: str = "qwen2.5:7b") -> Agent:
    """创建最小配置的 Agent (快速测试用)"""
    return create_agent(AgentConfig(
        workspace_dir=workspace_dir,
        model=model,
        max_rounds=5,
    ))


# ═══════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════

def main():
    """命令行交互模式"""
    import sys

    print(f"Claude Agent v{__import__('agent_distill').__version__}")
    print("输入 /quit 退出, /clear 清空历史\n")

    workspace = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    config = AgentConfig(workspace_dir=workspace)
    agent = create_agent(config)

    while True:
        try:
            user_input = input("\nYou> ").strip()
            if not user_input:
                continue
            if user_input == "/quit":
                break
            if user_input == "/clear":
                agent.clear_history()
                print("[历史已清空]")
                continue
            if user_input == "/stats":
                stats = agent.stats()
                print(f"轮数: {stats['total_rounds']}, 工具调用: {stats['total_tool_calls']}, "
                      f"Skills: {stats['skills_loaded']}, Memory: {stats['memories_loaded']}")
                continue

            # 检查 Skill 匹配
            skill = agent.match_skill(user_input)
            extra_prompt = ""
            if skill:
                print(f"[触发 Skill: {skill.name}]")
                extra_prompt = skill.instructions

            result = agent.run(user_input, system_extra=extra_prompt)
            print(f"\nAgent> {result.content}")
            if result.reasoning:
                print(f"\n[推理过程]\n{result.reasoning[:200]}...")

        except KeyboardInterrupt:
            print("\n再见！")
            break
        except Exception as e:
            print(f"错误: {e}")
            logging.exception("Agent 运行异常")


import os
import logging
logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
