"""
子代理系统 — AgentDefinition + 工具白名单 + 独立上下文
"""
import asyncio, logging
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("subagent")

@dataclass
class AgentDefinition:
    description: str
    prompt: str
    tools: Optional[list[str]] = None
    model: Optional[str] = None
    max_turns: int = 10

@dataclass
class SubAgentResult:
    agent_id: str
    description: str
    content: str
    success: bool = True
    error: Optional[str] = None
    turns: int = 0

class SubAgentRunner:
    """子代理执行器"""

    def __init__(self, llm_client, tool_registry):
        self.llm = llm_client
        self.tools = tool_registry
        self._results: dict[str, SubAgentResult] = {}

    async def run(self, config: AgentDefinition) -> SubAgentResult:
        import uuid
        agent_id = f"sub-{uuid.uuid4().hex[:8]}"
        logger.info(f"子代理启动: [{agent_id}] {config.description}")

        # 构建工具白名单
        filtered_tools = None
        if config.tools:
            filtered_tools = [t for t in self.tools.list_all()
                            if t.name in config.tools]

        try:
            # 独立上下文执行
            messages = [{"role": "system", "content": config.prompt}]
            content = ""
            for turn in range(config.max_turns):
                resp = await self.llm.chat(
                    messages=messages,
                    tools=filtered_tools or self.tools.get_schemas(),
                    system=config.prompt
                )
                if resp.content:
                    content = resp.content
                    break
            result = SubAgentResult(
                agent_id=agent_id, description=config.description,
                content=content, success=True, turns=turn+1
            )
        except Exception as e:
            result = SubAgentResult(
                agent_id=agent_id, description=config.description,
                content="", success=False, error=str(e)
            )

        self._results[agent_id] = result
        logger.info(f"子代理完成: [{agent_id}] {result.turns} turns")
        return result

    async def run_parallel(self, configs: list[AgentDefinition]) -> list[SubAgentResult]:
        tasks = [self.run(c) for c in configs]
        return await asyncio.gather(*tasks)
