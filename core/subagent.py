"""
P1-2: 子代理系统 — Javis Subagent System
支持AgentDefinition注册、白名单控制、分层权限隔离
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Set
from enum import Enum


class SubagentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class AgentDefinition:
    """子代理定义 — 类似 Claude Agent SDK 的 AgentDefinition"""
    name: str
    description: str
    system_prompt: str
    allowed_tools: List[str] = field(default_factory=list)  # 白名单
    denied_tools: List[str] = field(default_factory=list)    # 黑名单
    model: str = ""
    max_turns: int = 10
    max_timeout: int = 120  # 秒
    parent_agent: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "description": self.description,
            "allowed_tools": self.allowed_tools, "model": self.model,
            "max_turns": self.max_turns, "max_timeout": self.max_timeout
        }


@dataclass
class SubagentResult:
    """子代理执行结果"""
    agent_name: str
    status: SubagentStatus
    output: str = ""
    tool_calls: List[Dict] = field(default_factory=list)
    turns_used: int = 0
    error: Optional[str] = None
    usage: Dict = field(default_factory=dict)


class SubagentManager:
    """子代理管理器 — 注册、调度、生命周期管理"""

    def __init__(self, tool_registry, llm_client=None):
        self._agents: Dict[str, AgentDefinition] = {}
        self._running: Dict[str, SubagentResult] = {}
        self._tool_registry = tool_registry
        self._llm_client = llm_client
        self._lock = asyncio.Lock()
        self._register_builtins()

    def _register_builtins(self):
        """注册内置子代理"""
        builtins = [
            AgentDefinition(
                name="code-reviewer",
                description="代码审查专家, 负责审查代码质量、安全性和最佳实践",
                system_prompt="你是资深代码审查专家。审查代码的质量、安全性、性能和可维护性。列出具体问题和改进建议。",
                allowed_tools=["file_read", "file_list", "search_code"],
                max_turns=5, max_timeout=60
            ),
            AgentDefinition(
                name="researcher",
                description="信息搜索研究员, 负责收集和分析信息",
                system_prompt="你是信息搜索研究员。高效收集、分析和总结信息。使用搜索结果给出有深度的分析。",
                allowed_tools=["web_search", "web_fetch", "file_write"],
                max_turns=8, max_timeout=90
            ),
            AgentDefinition(
                name="shell-executor",
                description="命令执行器, 执行系统命令并返回结果",
                system_prompt="你是系统命令执行专家。安全地执行系统命令, 必须在执行前确认命令安全性。",
                allowed_tools=["system_execute", "file_read", "file_list"],
                max_turns=3, max_timeout=30
            ),
            AgentDefinition(
                name="file-organizer",
                description="文件组织助手, 整理和分类文件",
                system_prompt="你是文件管理专家。高效组织目录结构, 重命名文件, 清理冗余。",
                allowed_tools=["file_read", "file_write", "file_list"],
                max_turns=5, max_timeout=60
            ),
        ]
        for agent in builtins:
            self.register(agent)

    def register(self, definition: AgentDefinition) -> str:
        """注册一个子代理"""
        self._agents[definition.name] = definition
        return definition.name

    def unregister(self, name: str):
        """注销子代理"""
        self._agents.pop(name, None)

    def list_agents(self) -> List[Dict]:
        """列出所有已注册的子代理"""
        return [a.to_dict() for a in self._agents.values()]

    def get_agent(self, name: str) -> Optional[AgentDefinition]:
        return self._agents.get(name)

    async def spawn(self, agent_name: str, task: str,
                    context: Dict = None, callback: Callable = None) -> SubagentResult:
        """启动一个子代理任务"""
        agent = self._agents.get(agent_name)
        if not agent:
            return SubagentResult(agent_name=agent_name, status=SubagentStatus.FAILED,
                                  error=f"Unknown agent: {agent_name}")

        task_id = f"{agent_name}-{uuid.uuid4().hex[:8]}"
        result = SubagentResult(agent_name=agent_name, status=SubagentStatus.RUNNING)

        async with self._lock:
            self._running[task_id] = result

        try:
            messages = [{"role": "system", "content": agent.system_prompt},
                        {"role": "user", "content": task}]

            if context:
                messages.insert(1, {"role": "system",
                    "content": f"Additional context:\n{context}"})

            full_output = []
            tool_calls = []

            for turn in range(agent.max_turns):
                try:
                    response = await asyncio.wait_for(
                        self._llm_client.chat(messages=messages, model=agent.model),
                        timeout=min(30, agent.max_timeout // agent.max_turns)
                    )

                    choice = response.get("choices", [{}])[0]
                    msg = choice.get("message", {})

                    if "tool_calls" in msg:
                        for tc in msg["tool_calls"]:
                            tool_name = tc["function"]["name"]
                            if tool_name in agent.denied_tools:
                                tool_result = f"Error: Tool '{tool_name}' denied by agent policy"
                            elif not agent.allowed_tools or tool_name in agent.allowed_tools:
                                try:
                                    tool_fn = self._tool_registry.get(tool_name)
                                    tool_args = tc["function"]["arguments"]
                                    tool_result = await tool_fn(tool_args)
                                except Exception as e:
                                    tool_result = f"Tool error: {e}"
                            else:
                                tool_result = f"Error: Tool '{tool_name}' not in allowed list"

                            tool_calls.append({"tool": tool_name, "args": tc["function"]["arguments"],
                                              "result": str(tool_result)[:500]})
                            messages.append({"role": "assistant", "content": None,
                                           "tool_calls": [tc]})
                            messages.append({"role": "tool", "content": str(tool_result),
                                           "tool_call_id": tc["id"]})
                    else:
                        text = msg.get("content", "")
                        full_output.append(text)
                        result.turns_used = turn + 1
                        break

                except asyncio.TimeoutError:
                    result.status = SubagentStatus.TIMEOUT
                    result.error = f"Turn {turn+1} timed out"
                    break

            result.output = "\n".join(full_output)
            result.tool_calls = tool_calls
            if result.status == SubagentStatus.RUNNING:
                result.status = SubagentStatus.COMPLETED

            if callback:
                await callback(result)

        except Exception as e:
            result.status = SubagentStatus.FAILED
            result.error = str(e)

        finally:
            async with self._lock:
                self._running.pop(task_id, None)

        return result

    async def spawn_parallel(self, tasks: List[Dict]) -> List[SubagentResult]:
        """并行启动多个子代理"""
        coros = [self.spawn(t["agent"], t["task"], t.get("context"))
                 for t in tasks]
        return await asyncio.gather(*coros, return_exceptions=True)

    def get_running(self) -> List[str]:
        """返回所有正在运行的子代理ID"""
        return list(self._running.keys())

    def cancel(self, agent_name: str):
        """取消指定子代理的所有运行实例"""
        to_remove = [tid for tid in self._running if tid.startswith(agent_name)]
        for tid in to_remove:
            result = self._running[tid]
            result.status = SubagentStatus.FAILED
            result.error = "Cancelled by user"
            self._running.pop(tid, None)


# 全局管理器
_manager: Optional[SubagentManager] = None


def get_subagent_manager(tool_registry=None, llm_client=None) -> SubagentManager:
    global _manager
    if _manager is None:
        _manager = SubagentManager(tool_registry, llm_client)
    return _manager


def register_in_manifest(reg):
    """Register subagent tools"""
    from core.tool_registry import ToolDef
    mgr = get_subagent_manager()

    async def list_subagents(args):
        agents = mgr.list_agents()
        running = mgr.get_running()
        return {"success": True, "agents": agents, "running": running}

    async def spawn_subagent(args):
        result = await mgr.spawn(args["agent"], args["task"], args.get("context"))
        return {"success": result.status in [SubagentStatus.COMPLETED, SubagentStatus.RUNNING],
                "agent": result.agent_name, "status": result.status.value,
                "output": result.output, "turns_used": result.turns_used,
                "error": result.error}

    async def spawn_parallel(args):
        results = await mgr.spawn_parallel(args["tasks"])
        return {"success": True, "results": [{"agent": r.agent_name, "status": r.status.value,
                "output": r.output[:500]} for r in results]}

    async def cancel_subagent(args):
        mgr.cancel(args["agent"])
        return {"success": True, "cancelled": args["agent"]}

    reg.register_many([
        ToolDef("subagent_list", "List available sub-agents", {"type":"object","properties":{},"required":[]}, list_subagents, "subagent"),
        ToolDef("subagent_spawn", "Spawn a sub-agent to handle a task", {"type":"object","properties":{"agent":{"type":"string"},"task":{"type":"string"},"context":{"type":"object"}},"required":["agent","task"]}, spawn_subagent, "subagent"),
        ToolDef("subagent_parallel", "Spawn multiple sub-agents in parallel",
                {"type":"object","properties":{"tasks":{"type":"array","items":{"type":"object","properties":{"agent":{"type":"string"},"task":{"type":"string"}}}}},"required":["tasks"]}, spawn_parallel, "subagent"),
        ToolDef("subagent_cancel", "Cancel a running sub-agent", {"type":"object","properties":{"agent":{"type":"string"}},"required":["agent"]}, cancel_subagent, "subagent"),
    ])
