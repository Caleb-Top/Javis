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

# Global singleton
_runner: Optional[SubAgentRunner] = None

def get_runner(llm_client=None, tool_registry=None) -> SubAgentRunner:
    global _runner
    if _runner is None:
        _runner = SubAgentRunner(llm_client, tool_registry)
    return _runner

def register_in_manifest(reg):
    """Register subagent tools in manifest"""
    from core.tool_registry import ToolDef
    runner = get_runner()

    async def subagent_run(args):
        config = AgentDefinition(
            description=args.get("description", "Sub-agent task"),
            prompt=args["prompt"],
            tools=args.get("tools"),
            model=args.get("model"),
            max_turns=args.get("max_turns", 10),
        )
        result = await runner.run(config)
        return {
            "success": result.success,
            "agent_id": result.agent_id,
            "content": result.content[:2000],
            "error": result.error,
            "turns": result.turns,
        }

    async def subagent_parallel(args):
        configs = []
        for item in args.get("tasks", []):
            configs.append(AgentDefinition(
                description=item.get("description", ""),
                prompt=item["prompt"],
                tools=item.get("tools"),
                model=item.get("model"),
                max_turns=item.get("max_turns", 10),
            ))
        results = await runner.run_parallel(configs)
        return {
            "success": all(r.success for r in results),
            "results": [{
                "agent_id": r.agent_id, "description": r.description,
                "content": r.content[:500], "success": r.success,
                "error": r.error, "turns": r.turns,
            } for r in results],
        }

    reg.register_many([
        ToolDef("subagent_run", "Run a sub-agent with isolated context",
                {"type":"object","properties":{"description":{"type":"string"},"prompt":{"type":"string"},"tools":{"type":"array","items":{"type":"string"}},"model":{"type":"string"},"max_turns":{"type":"integer","default":10}},"required":["prompt"]}, subagent_run, "subagent"),
        ToolDef("subagent_parallel", "Run multiple sub-agents in parallel",
                {"type":"object","properties":{"tasks":{"type":"array","items":{"type":"object","properties":{"description":{"type":"string"},"prompt":{"type":"string"},"tools":{"type":"array"},"max_turns":{"type":"integer"}}}}},"required":["tasks"]}, subagent_parallel, "subagent"),
    ])
