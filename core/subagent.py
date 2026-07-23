"""
子代理系统 — AgentDefinition + 工具白名单 + 独立上下文
P1-2: Sub-agent system with context isolation, tool sandboxing, and parallel orchestration
"""
import asyncio, logging, time, uuid
from dataclasses import dataclass, field
from typing import Callable, Optional, AsyncGenerator
from enum import Enum

logger = logging.getLogger("subagent")

class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"

@dataclass
class AgentDefinition:
    description: str
    prompt: str
    tools: Optional[list[str]] = None  # 白名单工具名; None = 全部可用
    model: Optional[str] = None
    max_turns: int = 10
    temperature: float = 0.7
    system_prompt: str = ""

@dataclass
class SubAgentResult:
    agent_id: str
    description: str
    content: str = ""
    success: bool = True
    error: Optional[str] = None
    turns: int = 0
    elapsed_ms: float = 0
    tool_calls: int = 0
    state: AgentState = AgentState.IDLE

class SubAgentRunner:
    """子代理执行器 — 独立上下文 + 受限工具集 + 结果聚合"""

    def __init__(self, llm_client=None, tool_registry=None):
        self.llm = llm_client
        self.tools = tool_registry
        self._results: dict[str, SubAgentResult] = {}
        self._cancel_tokens: dict[str, asyncio.Event] = {}

    def _build_filtered_tools(self, whitelist: Optional[list[str]]) -> list:
        """构建工具白名单 — 安全过滤"""
        if not self.tools:
            return []
        all_tools = self.tools.list_all() if hasattr(self.tools, 'list_all') else []
        if whitelist is None:
            return all_tools
        # list_all() 返回字符串列表（工具名），直接做成员检查
        return [t for t in all_tools if t in whitelist]

    async def run(self, config: AgentDefinition) -> SubAgentResult:
        """执行单个子代理"""
        agent_id = f"sub-{uuid.uuid4().hex[:8]}"
        cancel_event = asyncio.Event()
        self._cancel_tokens[agent_id] = cancel_event
        t0 = time.time()

        logger.info(f"子代理启动: [{agent_id}] {config.description}")

        result = SubAgentResult(
            agent_id=agent_id,
            description=config.description,
            state=AgentState.RUNNING,
        )

        try:
            # 构建工具白名单
            filtered_tools = self._build_filtered_tools(config.tools)

            # 独立上下文: 每次运行都是全新的消息列表
            system_msg = config.system_prompt or config.prompt
            messages = [{"role": "system", "content": system_msg}]

            content = ""
            tool_calls_count = 0

            for turn in range(config.max_turns):
                # 支持取消
                if cancel_event.is_set():
                    result.state = AgentState.CANCELLED
                    result.content = content or "[Cancelled]"
                    logger.info(f"子代理取消: [{agent_id}] at turn {turn}")
                    break

                resp = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=filtered_tools if filtered_tools else None,
                    system=system_msg,
                )

                if resp.tool_calls:
                    # 工具调用轮次
                    tool_calls_count += len(resp.tool_calls)
                    for tc in resp.tool_calls:
                        if self.tools:
                            # tc 是 dict: {"id": ..., "name": ..., "params": {...}}
                            tool_result = await self.tools.execute(tc["name"], tc["params"])
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": str(tool_result),
                            })
                else:
                    # 最终回复
                    content = resp.text or ""
                    messages.append({"role": "assistant", "content": content})
                    result.state = AgentState.DONE
                    break
            else:
                # 最大轮次用尽
                result.state = AgentState.DONE
                if not content:
                    content = "[Max turns reached]"

            result.content = content
            result.success = result.state == AgentState.DONE
            result.turns = (turn + 1) if (result.state != AgentState.CANCELLED) else turn
            result.tool_calls = tool_calls_count
            result.elapsed_ms = (time.time() - t0) * 1000

        except Exception as e:
            result.success = False
            result.error = str(e)
            result.state = AgentState.ERROR
            logger.error(f"子代理异常 [{agent_id}]: {e}")

        finally:
            self._cancel_tokens.pop(agent_id, None)

        self._results[agent_id] = result
        logger.info(
            f"子代理完成: [{agent_id}] state={result.state.value} "
            f"turns={result.turns} tools={result.tool_calls} "
            f"elapsed={result.elapsed_ms:.0f}ms"
        )
        return result

    async def run_stream(self, config: AgentDefinition) -> AsyncGenerator:
        """流式执行 — 逐步返回结果"""
        agent_id = f"sub-{uuid.uuid4().hex[:8]}"
        cancel_event = asyncio.Event()
        self._cancel_tokens[agent_id] = cancel_event
        t0 = time.time()

        yield {"type": "start", "agent_id": agent_id, "description": config.description}

        try:
            filtered_tools = self._build_filtered_tools(config.tools)
            system_msg = config.system_prompt or config.prompt
            messages = [{"role": "system", "content": system_msg}]

            for turn in range(config.max_turns):
                if cancel_event.is_set():
                    yield {"type": "cancelled", "agent_id": agent_id, "turn": turn}
                    break

                resp = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=filtered_tools if filtered_tools else None,
                    system=system_msg,
                )

                if resp.tool_calls:
                    for tc in resp.tool_calls:
                        yield {"type": "tool_call", "agent_id": agent_id,
                               "tool": tc["name"], "args": tc["params"]}
                        if self.tools:
                            tool_result = await self.tools.execute(tc["name"], tc["params"])
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": str(tool_result),
                            })
                else:
                    content = resp.text or ""
                    messages.append({"role": "assistant", "content": content})
                    yield {"type": "done", "agent_id": agent_id, "content": content,
                           "turns": turn + 1, "elapsed_ms": (time.time() - t0) * 1000}
                    return

            yield {"type": "done", "agent_id": agent_id,
                   "content": "[Max turns]", "turns": config.max_turns}

        except Exception as e:
            yield {"type": "error", "agent_id": agent_id, "error": str(e)}

        finally:
            self._cancel_tokens.pop(agent_id, None)

    def cancel(self, agent_id: str) -> bool:
        """取消运行中的子代理"""
        token = self._cancel_tokens.get(agent_id)
        if token:
            token.set()
            logger.info(f"子代理取消请求: {agent_id}")
            return True
        return False

    async def run_parallel(self, configs: list[AgentDefinition]) -> list[SubAgentResult]:
        """并行执行多个子代理"""
        tasks = [self.run(c) for c in configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            r if isinstance(r, SubAgentResult) else
            SubAgentResult(agent_id="error", description="",
                          success=False, error=str(r), state=AgentState.ERROR)
            for r in results
        ]

    def get_result(self, agent_id: str) -> Optional[SubAgentResult]:
        return self._results.get(agent_id)

    def list_results(self) -> list[SubAgentResult]:
        return list(self._results.values())

    def get_active_count(self) -> int:
        return len(self._cancel_tokens)


# 全局单例
_runner: Optional[SubAgentRunner] = None

def get_runner(llm_client=None, tool_registry=None) -> SubAgentRunner:
    global _runner
    if _runner is None or (llm_client and _runner.llm is None):
        _runner = SubAgentRunner(llm_client, tool_registry)
    return _runner


def register_in_manifest(reg):
    """注册子代理工具到 manifest"""
    from core.tool_registry import ToolDef
    runner = get_runner()

    async def subagent_run(args):
        config = AgentDefinition(
            description=args.get("description", "Sub-agent task"),
            prompt=args["prompt"],
            tools=args.get("tools"),
            model=args.get("model"),
            max_turns=args.get("max_turns", 10),
            temperature=args.get("temperature", 0.7),
            system_prompt=args.get("system_prompt", ""),
        )
        result = await runner.run(config)
        return {
            "success": result.success,
            "agent_id": result.agent_id,
            "content": result.content[:2000],
            "error": result.error,
            "turns": result.turns,
            "elapsed_ms": result.elapsed_ms,
            "tool_calls": result.tool_calls,
            "state": result.state.value,
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
                system_prompt=item.get("system_prompt", ""),
            ))
        results = await runner.run_parallel(configs)
        return {
            "success": all(r.success for r in results),
            "results": [{
                "agent_id": r.agent_id, "description": r.description,
                "content": r.content[:500], "success": r.success,
                "error": r.error, "turns": r.turns,
                "elapsed_ms": r.elapsed_ms, "state": r.state.value,
            } for r in results],
        }

    async def subagent_status(args):
        active = runner.get_active_count()
        results = runner.list_results()
        return {
            "success": True,
            "active": active,
            "completed": len(results),
            "recent": [
                {"agent_id": r.agent_id, "description": r.description,
                 "state": r.state.value, "turns": r.turns}
                for r in results[-10:]
            ],
        }

    async def subagent_cancel(args):
        agent_id = args["agent_id"]
        ok = runner.cancel(agent_id)
        return {"success": ok, "agent_id": agent_id}

    reg.register_many([
        ToolDef("subagent_run", "运行隔离上下文的子代理",
                {"type":"object","properties":{
                    "description":{"type":"string"},"prompt":{"type":"string"},
                    "tools":{"type":"array","items":{"type":"string"}},
                    "model":{"type":"string"},"max_turns":{"type":"integer","default":10},
                    "temperature":{"type":"number","default":0.7},
                    "system_prompt":{"type":"string"}
                },"required":["prompt"]}, subagent_run, "subagent"),
        ToolDef("subagent_parallel", "并行运行多个子代理",
                {"type":"object","properties":{
                    "tasks":{"type":"array","items":{"type":"object","properties":{
                        "description":{"type":"string"},"prompt":{"type":"string"},
                        "tools":{"type":"array"},"max_turns":{"type":"integer"}
                    }}}
                },"required":["tasks"]}, subagent_parallel, "subagent"),
        ToolDef("subagent_status", "查看子代理系统状态",
                {"type":"object","properties":{},"required":[]},
                subagent_status, "subagent"),
        ToolDef("subagent_cancel", "取消运行中的子代理",
                {"type":"object","properties":{"agent_id":{"type":"string"}},"required":["agent_id"]},
                subagent_cancel, "subagent"),
    ])
