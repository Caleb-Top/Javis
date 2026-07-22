"""
Agent 核心执行循环 — 整个系统的心脏

这是 Claude Agent 最核心的逻辑:
1. 接收用户消息
2. 构建系统提示 + 注入 Memory/Skills
3. LLM 推理 → 返回文本/工具调用
4. 执行工具 → 结果反馈给 LLM
5. 循环直到 LLM 只返回文本 (不再调用工具)
"""
from __future__ import annotations
import logging, uuid, time
from typing import Optional, Callable

from .types import (
    AgentConfig, Message, ToolCall, ToolDef, ToolResult,
    Role, SkillDef, MemoryEntry, Task, TaskStatus,
)
from .llm_client import LLMClient, LLMResponse
from .tool_registry import ToolRegistry, build_builtin_tools
from .system_prompt import SystemPromptBuilder

logger = logging.getLogger("agent.core")


class Agent:
    """
    Claude Agent 核心执行循环。

    ┌─────────────────────────────────────────────────────────┐
    │ 用户输入                                                  │
    └────────────┬────────────────────────────────────────────┘
                 ↓
    ┌─────────────────────────────────────────────────────────┐
    │ 1. 预处理: 注入 Memory, 匹配 Skill                        │
    └────────────┬────────────────────────────────────────────┘
                 ↓
    ┌─────────────────────────────────────────────────────────┐
    │ 2. 发送 [系统提示 + 历史 + 用户消息 + 工具列表] → LLM     │
    └────────────┬────────────────────────────────────────────┘
                 ↓
    ┌──────────────────────────────────────────────────────┐   │
    │ 3. LLM 返回:                                          │   │
    │    ├── 纯文本 → 结束 (返回给用户)                      │   │
    │    └── tool_calls → 继续                               │   │
    └────────────┬─────────────────────────────────────────┘   │
                 ↓                                              │
    ┌─────────────────────────────────────────────────────────┐
    │ 4. 执行工具 → 获取 ToolResult → 追加到对话历史            │
    └────────────┬────────────────────────────────────────────┘
                 ↓
    ┌─────────────────────────────────────────────────────────┐
    │ 5. 带着工具结果再次调用 LLM (回到步骤 2)                  │
    └─────────────────────────────────────────────────────────┘
                    ... 循环直到 ...
    ┌─────────────────────────────────────────────────────────┐
    │ 6. LLM 返回纯文本 → 标记完成 → 后处理 (Memory + 文件链接) │
    └─────────────────────────────────────────────────────────┘
    """

    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm = LLMClient(config)
        self.tools = ToolRegistry()
        self.path_mapper = None  # 由外部注入

        # 注册内置工具
        builtin = build_builtin_tools(config.workspace_dir, self.path_mapper)
        self.tools.register_many(builtin)

        # Skill 系统
        self._skills: dict[str, SkillDef] = {}

        # Memory 系统
        self._memories: list[MemoryEntry] = []

        # 对话历史
        self._history: list[Message] = []

        # Task 系统
        self._tasks: dict[str, Task] = {}

        # 钩子 (可扩展)
        self._hooks: dict[str, list[Callable]] = {
            "before_tool": [],     # 工具执行前
            "after_tool": [],      # 工具执行后
            "before_response": [], # 发送给用户前
            "on_error": [],        # 出错时
        }

        # 统计
        self._stats = {
            "total_rounds": 0,
            "total_tool_calls": 0,
            "total_tokens": 0,
        }

    # ═══════════════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════════════

    def run(self, user_input: str, system_extra: str = "") -> AgentResult:
        """
        执行一次完整的 Agent 推理循环。

        Args:
            user_input: 用户输入
            system_extra: 额外的系统提示 (如 Skill 加载后的指令)

        Returns:
            AgentResult 包含最终回复和元信息
        """
        start = time.time()

        # Step 1: 构建系统提示
        prompt_builder = SystemPromptBuilder(username="User")
        prompt_builder.inject_skills(list(self._skills.values()))
        prompt_builder.inject_memories(self._memories)
        system_prompt = prompt_builder.build()
        if system_extra:
            system_prompt += "\n\n## 当前技能指令\n" + system_extra

        # Step 2: 加入用户消息
        user_msg = Message(role=Role.USER, content=user_input)
        self._history.append(user_msg)

        # Step 3: 执行推理-工具循环
        final_response = None
        for round_idx in range(self.config.max_rounds):
            all_messages = self._history[:]  # 发送完整历史

            tools_api = self.tools.get_api_definitions()
            response = self.llm.chat(
                messages=all_messages,
                tools=tools_api,
                system_prompt=system_prompt,
            )

            self._stats["total_rounds"] += 1
            self._stats["total_tokens"] += response.usage.get("total_tokens",
                                    response.usage.get("input_tokens", 0) + response.usage.get("output_tokens", 0))

            # 情况 A: 纯文本 → 结束
            if not response.tool_calls and response.content:
                assistant_msg = Message(role=Role.ASSISTANT, content=response.content)
                self._history.append(assistant_msg)
                final_response = response
                break

            # 情况 B: 工具调用 → 执行
            if response.tool_calls:
                final_response = response  # 保留最后一轮 (可能同时有文本+工具调用)

                # 构建助手消息 (含工具调用)
                assistant_msg = Message(
                    role=Role.ASSISTANT,
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
                self._history.append(assistant_msg)

                # 执行每个工具调用
                for tc in response.tool_calls:
                    result = self._execute_tool(tc)
                    tool_msg = Message(
                        role=Role.TOOL,
                        content=result.content,
                        tool_call_id=tc.id,
                        name=tc.name,
                    )
                    self._history.append(tool_msg)

                # 继续下一轮 (LLM 看到工具结果后会决定下一步)
                continue

            # 情况 C: 无文本也无工具调用 → 异常, 停止
            logger.warning(f"LLM 返回空响应 (round {round_idx})")
            final_response = response
            break

        elapsed = time.time() - start
        logger.info(
            f"Agent 完成: {self._stats['total_rounds']} round(s), "
            f"{self._stats['total_tool_calls']} tool call(s), "
            f"{elapsed:.1f}s"
        )

        # 后处理: Memory 写入检查
        self._check_memory_write(user_input, final_response)

        return AgentResult(
            content=final_response.content if final_response else "",
            reasoning=final_response.reasoning if final_response else "",
            rounds=self._stats["total_rounds"],
            tool_calls_count=self._stats["total_tool_calls"],
            elapsed=elapsed,
        )

    # ═══════════════════════════════════════════════
    # 工具执行
    # ═══════════════════════════════════════════════

    def _execute_tool(self, tool_call: ToolCall) -> ToolResult:
        """执行一个工具调用 (含钩子)"""
        self._stats["total_tool_calls"] += 1

        # before_tool 钩子
        for hook in self._hooks["before_tool"]:
            try:
                hook(tool_call)
            except Exception:
                pass

        result = self.tools.execute(tool_call.name, tool_call.arguments)

        # after_tool 钩子
        for hook in self._hooks["after_tool"]:
            try:
                hook(tool_call, result)
            except Exception:
                pass

        return result

    # ═══════════════════════════════════════════════
    # Memory 写入检查
    # ═══════════════════════════════════════════════

    _last_memory_write: float = 0.0

    def _check_memory_write(self, user_input: str, response: Optional[LLMResponse]) -> None:
        """
        Memory 写入策略:
        - 如果距离上次写入超过 15 分钟, 建议写入
        - 实际写入由外部 MemoryManager 处理
        """
        import time as _time
        elapsed = _time.time() - self._last_memory_write
        if elapsed > 900:  # 15 分钟
            logger.debug("Memory: 超过 15 分钟未写入, 将触发检查")
            self._last_memory_write = _time.time()

    # ═══════════════════════════════════════════════
    # Skill 管理
    # ═══════════════════════════════════════════════

    def load_skill(self, skill: SkillDef) -> None:
        """加载一个 Skill — 注册到内存中, 下次推理时注入"""
        self._skills[skill.name] = skill
        logger.info(f"Skill 已加载: {skill.name}")

    def unload_skill(self, name: str) -> None:
        self._skills.pop(name, None)

    def get_skill(self, name: str) -> Optional[SkillDef]:
        return self._skills.get(name)

    def match_skill(self, user_input: str) -> Optional[SkillDef]:
        """
        关键词匹配 — 检查用户输入是否匹配某个 Skill。

        匹配规则:
        1. 检查 Skill 的 description 中的关键词
        2. 检查 Skill 的 name
        """
        text_lower = user_input.lower()
        for name, skill in self._skills.items():
            # 检查 name 是否直接出现在用户输入中
            if name.lower() in text_lower:
                return skill
            # 检查 description 关键词
            desc_lower = skill.description.lower()
            keywords = desc_lower.replace(",", " ").split()
            match_count = sum(1 for kw in keywords if kw in text_lower)
            if match_count >= 2:  # 至少匹配 2 个关键词
                return skill
        return None

    # ═══════════════════════════════════════════════
    # Memory 管理
    # ═══════════════════════════════════════════════

    def set_memories(self, memories: list[MemoryEntry]) -> None:
        self._memories = memories

    # ═══════════════════════════════════════════════
    # Task 管理
    # ═══════════════════════════════════════════════

    def create_task(self, subject: str, description: str = "") -> Task:
        task = Task(
            id=f"task-{uuid.uuid4().hex[:8]}",
            subject=subject,
            description=description,
        )
        self._tasks[task.id] = task
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def update_task(self, task_id: str, **kwargs) -> Optional[Task]:
        task = self._tasks.get(task_id)
        if not task:
            return None
        for k, v in kwargs.items():
            if hasattr(task, k):
                setattr(task, k, v)
        return task

    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[Task]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    def get_ready_tasks(self) -> list[Task]:
        """获取所有就绪 (可开始) 的任务 — pending 且不被任何未完成任务阻塞"""
        all_tasks = list(self._tasks.values())
        ready = []
        for t in all_tasks:
            if t.status != TaskStatus.PENDING:
                continue
            # 检查是否被未完成的任务阻塞
            blocked = False
            for dep_id in t.blocked_by:
                dep = self._tasks.get(dep_id)
                if dep and dep.status not in (TaskStatus.COMPLETED, TaskStatus.DELETED):
                    blocked = True
                    break
            if not blocked:
                ready.append(t)
        return ready

    # ═══════════════════════════════════════════════
    # 钩子系统
    # ═══════════════════════════════════════════════

    def add_hook(self, event: str, callback: Callable) -> None:
        """添加事件钩子"""
        if event in self._hooks:
            self._hooks[event].append(callback)

    def remove_hook(self, event: str, callback: Callable) -> None:
        if event in self._hooks:
            self._hooks[event] = [h for h in self._hooks[event] if h != callback]

    # ═══════════════════════════════════════════════
    # 对话管理
    # ═══════════════════════════════════════════════

    def clear_history(self) -> None:
        self._history = []

    def get_history(self) -> list[Message]:
        return self._history[:]

    # ═══════════════════════════════════════════════
    # 统计
    # ═══════════════════════════════════════════════

    def stats(self) -> dict:
        return {
            **self._stats,
            "skills_loaded": len(self._skills),
            "memories_loaded": len(self._memories),
            "tasks": len(self._tasks),
            "history_messages": len(self._history),
        }


# ═══════════════════════════════════════════════
# 结果类型
# ═══════════════════════════════════════════════

from dataclasses import dataclass, field

@dataclass
class AgentResult:
    """Agent 执行结果"""
    content: str
    reasoning: str = ""
    rounds: int = 0
    tool_calls_count: int = 0
    elapsed: float = 0.0
