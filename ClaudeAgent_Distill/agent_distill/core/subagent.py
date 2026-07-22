"""
子代理系统 — 并行启动专用子代理处理独立任务

支持的代理类型:
- general-purpose    → 通用搜索/多步骤
- Explore            → 只读批量扫文件
- Plan               → 软件架构设计
- claude-code-guide  → Claude Code/API 问答
"""
from __future__ import annotations
import logging, uuid, time
from dataclasses import dataclass, field
from typing import Optional, Callable, Literal
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("agent.subagent")


# ═══════════════════════════════════════════════
# 类型定义
# ═══════════════════════════════════════════════

SubAgentType = Literal[
    "general-purpose",
    "Explore",
    "Plan",
    "claude-code-guide",
    "statusline-setup",
]

@dataclass
class SubAgentConfig:
    """子代理配置"""
    agent_type: SubAgentType = "general-purpose"
    description: str = ""                    # 3-5 词简短描述
    prompt: str = ""                         # 完整任务指令
    model: Optional[str] = None              # 模型覆写
    isolation: Optional[str] = None          # "worktree" | "remote" | None
    timeout: float = 300.0                   # 超时秒数

@dataclass
class SubAgentResult:
    """子代理执行结果"""
    agent_type: str
    description: str
    content: str
    success: bool = True
    error: Optional[str] = None
    elapsed: float = 0.0


# ═══════════════════════════════════════════════
# 子代理执行器
# ═══════════════════════════════════════════════

class SubAgentRunner:
    """
    子代理执行器。

    设计:
    - ThreadPoolExecutor 并行执行多个子代理
    - 每个子代理拥有独立的工具集 (取决于 agent_type)
    - 完成后结果汇总
    """

    def __init__(self, max_workers: int = 4):
        self.max_workers = max_workers
        self._executors: dict[str, "SubAgentExecutor"] = {}

    # ═══════════════════════════════════════════════
    # 单代理启动
    # ═══════════════════════════════════════════════

    def run(self, config: SubAgentConfig) -> SubAgentResult:
        """同步执行一个子代理"""
        executor = self._get_executor(config.agent_type)
        return executor.execute(config)

    def run_parallel(self, configs: list[SubAgentConfig]) -> list[SubAgentResult]:
        """
        并行执行多个子代理。

        使用场景:
        - "搜索整个项目找出所有 SQL 注入风险" + "同时分析性能瓶颈"
        - 两个任务互不依赖, 同时进行
        """
        results = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(configs))) as pool:
            futures = {
                pool.submit(self.run, cfg): cfg for cfg in configs
            }
            for future in as_completed(futures):
                cfg = futures[future]
                try:
                    result = future.result(timeout=cfg.timeout)
                    results.append(result)
                except Exception as e:
                    results.append(SubAgentResult(
                        agent_type=cfg.agent_type,
                        description=cfg.description,
                        content="",
                        success=False,
                        error=str(e),
                    ))
        return results

    def _get_executor(self, agent_type: str) -> "SubAgentExecutor":
        """获取或创建特定类型的执行器"""
        if agent_type not in self._executors:
            self._executors[agent_type] = SubAgentExecutor(agent_type)
        return self._executors[agent_type]


# ═══════════════════════════════════════════════
# 具体执行器
# ═══════════════════════════════════════════════

class SubAgentExecutor:
    """
    单个子代理的执行逻辑。

    在实际实现中，这会启动一个新的 Agent 实例，给它
    一个独立的工具集 (取决于 agent_type) 和对话上下文。

    不同类型的子代理有不同的工具权限:
    - Explore:      只读 (Read, Grep, Glob) — 不能写入
    - Plan:         读写 + 设计能力
    - general-purpose: 全部工具
    """

    # 各类型的工具限制
    TOOL_WHITELISTS = {
        "Explore": ["read_file", "grep", "glob"],
        "Plan": ["read_file", "grep", "glob", "write_file", "edit_file"],
        "general-purpose": None,  # None = 全部工具
        "claude-code-guide": None,
        "statusline-setup": ["read_file", "edit_file"],
    }

    def __init__(self, agent_type: str):
        self.agent_type = agent_type
        self.whitelist = self.TOOL_WHITELISTS.get(agent_type)

    def execute(self, config: SubAgentConfig) -> SubAgentResult:
        """
        执行一个子代理。

        步骤:
        1. 创建新的 Agent 实例
        2. 根据 agent_type 过滤工具
        3. 注入子代理专用的系统提示
        4. 运行推理循环
        5. 返回结果
        """
        start = time.time()
        logger.info(f"子代理启动: [{self.agent_type}] {config.description}")

        try:
            # 在实际实现中，这里会:
            # 1. 创建 Agent(config) 实例
            # 2. agent.tools.restrict(self.whitelist)  # 限制工具
            # 3. 注入对应的子代理系统提示
            # 4. result = agent.run(config.prompt)

            # 简化模拟 — 实际会调用 LLM
            result_content = f"[子代理 {self.agent_type}] 任务: {config.description}"

            elapsed = time.time() - start
            logger.info(f"子代理完成: [{self.agent_type}] {elapsed:.1f}s")

            return SubAgentResult(
                agent_type=self.agent_type,
                description=config.description,
                content=result_content,
                success=True,
                elapsed=elapsed,
            )

        except Exception as e:
            logger.error(f"子代理异常: {e}")
            return SubAgentResult(
                agent_type=self.agent_type,
                description=config.description,
                content="",
                success=False,
                error=str(e),
                elapsed=time.time() - start,
            )


# ═══════════════════════════════════════════════
# 各类型子代理的专用系统提示
# ═══════════════════════════════════════════════

EXPLORE_AGENT_PROMPT = """你是 Explore Agent — 只读搜索专家。

你的任务:
- 批量搜索大量文件/目录
- 找到代码位置、模式、定义
- 返回发现结果和结论

你不能:
- 写入任何文件
- 修改任何文件

输出格式:
- 给出清晰的结论，不需要附带完整的文件内容
- 如果有多个匹配，给出列表和简要说明
"""

PLAN_AGENT_PROMPT = """你是 Plan Agent — 软件架构设计专家。

你的任务:
- 分析需求并设计实施计划
- 确定关键文件和依赖关系
- 考虑架构权衡

输出:
- 分步骤的实施计划
- 每个步骤的说明和风险
- 文件清单
"""

GENERAL_PURPOSE_PROMPT = """你是通用子代理 — 可处理各种复杂多步骤任务。

你拥有所有工具的完整权限。
独立完成分配的任务并返回最终结果。
"""
