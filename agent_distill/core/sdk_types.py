"""
SDK 级 Agent 实现 — 基于官方 Claude Agent SDK 源码的完整映射

新增内容:
- Transport 协议 + SubprocessCLITransport
- 控制协议 (Query 类)
- Hook 系统 (10 种事件)
- 子代理 (AgentDefinition)
- SessionStore 协议
- 权限模式
- MCP 集成
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Literal, Protocol, AsyncIterator, TypedDict
from abc import ABC, abstractmethod
from collections.abc import Awaitable, AsyncIterable
import json, logging, os, uuid
from enum import Enum
from pathlib import Path

logger = logging.getLogger("agent.sdk")

# ═══════════════════════════════════════════════
# 权限模式
# ═══════════════════════════════════════════════

PermissionMode = Literal[
    "default",          # CLI 提示确认
    "acceptEdits",      # 自动接受编辑
    "plan",             # 只规划不执行
    "bypassPermissions",# 全部批准
    "dontAsk",          # 拒绝未预批准的
    "auto",             # 模型分类器自动判断
]

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


# ═══════════════════════════════════════════════
# Thinking 配置
# ═══════════════════════════════════════════════

class ThinkingConfigAdaptive(TypedDict):
    type: Literal["adaptive"]

class ThinkingConfigEnabled(TypedDict):
    type: Literal["enabled"]
    budget_tokens: int

class ThinkingConfigDisabled(TypedDict):
    type: Literal["disabled"]

ThinkingConfig = ThinkingConfigAdaptive | ThinkingConfigEnabled | ThinkingConfigDisabled


# ═══════════════════════════════════════════════
# Transport 协议
# ═══════════════════════════════════════════════

class Transport(ABC):
    """传输层抽象 — SDK 与 CLI 之间的通信通道"""

    @abstractmethod
    async def connect(self) -> None:
        """建立连接 (启动子进程或连接远程)"""

    @abstractmethod
    async def close(self) -> None:
        """关闭连接 + 清理资源"""

    @abstractmethod
    async def write(self, data: str) -> None:
        """写入数据 (JSON 行)"""

    @abstractmethod
    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        """读取解析后的消息 (NDJSON → dict)"""

    @abstractmethod
    async def end_input(self) -> None:
        """关闭输入流 (EOF)"""

    @abstractmethod
    def is_ready(self) -> bool:
        """检查传输是否就绪"""


# ═══════════════════════════════════════════════
# 消息类型
# ═══════════════════════════════════════════════

@dataclass
class TextBlock:
    text: str
    type: str = "text"

@dataclass
class ThinkingBlock:
    thinking: str
    signature: str
    type: str = "thinking"

@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"

@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: list[dict]
    is_error: bool = False
    type: str = "tool_result"

ContentBlock = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


@dataclass
class UserMessage:
    content: str | list[ContentBlock]
    uuid: Optional[str] = None
    session_id: str = "default"

@dataclass
class AssistantMessage:
    content: list[ContentBlock]
    model: str = ""
    session_id: str = "default"

@dataclass
class ResultMessage:
    subtype: str                          # success / error_max_turns / ...
    duration_ms: int = 0
    total_cost_usd: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    is_error: bool = False
    session_id: str = "default"

@dataclass
class SystemMessage:
    subtype: str
    session_id: str = "default"

@dataclass
class TaskStartedMessage(SystemMessage):
    task_id: str = ""
    agent_id: str = ""
    description: str = ""

@dataclass
class TaskProgressMessage(SystemMessage):
    task_id: str = ""
    message: str = ""

@dataclass
class TaskUpdatedMessage(SystemMessage):
    task_id: str = ""
    status: str = ""

Message = UserMessage | AssistantMessage | ResultMessage | SystemMessage | \
          TaskStartedMessage | TaskProgressMessage | TaskUpdatedMessage


# ═══════════════════════════════════════════════
# Hook 系统
# ═══════════════════════════════════════════════

class HookEvent(str, Enum):
    """10 种 Hook 事件"""
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    SUBAGENT_STOP = "SubagentStop"
    SUBAGENT_START = "SubagentStart"
    PRE_COMPACT = "PreCompact"
    PERMISSION_REQUEST = "PermissionRequest"
    NOTIFICATION = "Notification"

@dataclass
class HookJSONOutput:
    """Hook 回调的返回值"""
    continue_: bool = True
    suppress_output: bool = False
    stop_reason: Optional[str] = None
    decision: Optional[Literal["allow", "deny", "ask"]] = None
    system_message: Optional[str] = None
    reason: Optional[str] = None
    async_: bool = False
    additional_context: Optional[str] = None

    def to_cli_dict(self) -> dict:
        """转为 CLI 期望的字段名 (async_ → async, continue_ → continue)"""
        d = {}
        for k, v in self.__dict__.items():
            if v is None:
                continue
            if k == "async_":
                d["async"] = v
            elif k == "continue_":
                d["continue"] = v
            else:
                d[k] = v
        return d

@dataclass
class HookMatcher:
    """Hook 匹配器 — 绑定事件和回调"""
    matcher: str                        # 工具名正则 (如 "Edit|Write")
    hooks: list[Callable]               # 回调函数列表
    timeout: Optional[float] = None     # 超时秒数

    def to_cli_dict(self) -> dict:
        d: dict = {"matcher": self.matcher, "hooks": self.hooks}
        if self.timeout is not None:
            d["timeout"] = self.timeout
        return d


# ═══════════════════════════════════════════════
# 子代理定义
# ═══════════════════════════════════════════════

@dataclass
class AgentDefinition:
    """子代理定义"""
    description: str                    # 父代理何时委托给我
    prompt: str                         # 子代理系统提示
    tools: Optional[list[str]] = None   # 工具白名单 (None=全部)
    model: Optional[str] = None         # 模型覆写

    def to_cli_dict(self) -> dict:
        d = {
            "description": self.description,
            "prompt": self.prompt,
        }
        if self.tools is not None:
            d["tools"] = self.tools
        if self.model is not None:
            d["model"] = self.model
        return d


# ═══════════════════════════════════════════════
# 权限回调
# ═══════════════════════════════════════════════

@dataclass
class PermissionResultAllow:
    updated_input: Optional[dict] = None
    updated_permissions: Optional[list] = None

@dataclass
class PermissionResultDeny:
    message: str = ""
    interrupt: bool = False

PermissionResult = PermissionResultAllow | PermissionResultDeny

@dataclass
class ToolPermissionContext:
    tool_use_id: Optional[str] = None
    agent_id: Optional[str] = None
    blocked_path: Optional[str] = None
    decision_reason: Optional[str] = None
    title: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    suggestions: list = field(default_factory=list)

CanUseTool = Callable[
    [str, dict[str, Any], ToolPermissionContext],
    Awaitable[PermissionResult],
]


# ═══════════════════════════════════════════════
# ClaudeAgentOptions
# ═══════════════════════════════════════════════

@dataclass
class SandboxNetworkConfig:
    allowed_domains: list[str] = field(default_factory=list)
    denied_domains: list[str] = field(default_factory=list)

@dataclass
class SandboxSettings:
    enabled: bool = False
    network: Optional[SandboxNetworkConfig] = None
    ignore_violations: bool = False

@dataclass
class ClaudeAgentOptions:
    """Agent SDK 完整配置"""
    # 核心
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    fallback_model: Optional[str] = None

    # 权限
    permission_mode: Optional[PermissionMode] = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    can_use_tool: Optional[CanUseTool] = None

    # Session
    resume: Optional[str] = None
    session_id: Optional[str] = None
    continue_conversation: bool = False
    fork_session: bool = False

    # 行为
    max_turns: Optional[int] = None
    max_budget_usd: Optional[float] = None
    thinking: Optional[ThinkingConfig] = None
    effort: Optional[EffortLevel] = None
    output_format: Optional[dict] = None

    # 环境
    cwd: Optional[str] = None
    add_dirs: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    settings: Optional[str] = None
    sandbox: Optional[SandboxSettings] = None
    betas: list[str] = field(default_factory=list)

    # Skills
    skills: Optional[list[str] | Literal["all"]] = None
    setting_sources: Optional[list[str]] = None

    # MCP
    mcp_servers: Optional[dict[str, Any]] = None
    strict_mcp_config: bool = False

    # Session Store
    session_store: Optional[Any] = None
    session_store_flush: Optional[Literal["immediate", "batched"]] = None

    # Subagents
    agents: Optional[dict[str, AgentDefinition]] = None

    # Hooks
    hooks: Optional[dict[HookEvent, list[HookMatcher]]] = None

    # 其他
    include_partial_messages: bool = False
    include_hook_events: bool = False
    enable_file_checkpointing: bool = False
    max_buffer_size: Optional[int] = None
    cli_path: Optional[str] = None
    stderr: Optional[Callable[[str], None]] = None
    extra_args: dict[str, str | None] = field(default_factory=dict)


# ═══════════════════════════════════════════════
# SessionStore 协议
# ═══════════════════════════════════════════════

@dataclass
class SessionKey:
    session_id: str
    profile: Optional[str] = None
    project: Optional[str] = None


class SessionStore(Protocol):
    """Session 持久化存储协议"""

    async def append(self, key: SessionKey, entries: list[dict]) -> None: ...
    async def fork(self, key: SessionKey) -> SessionKey: ...
    async def rename(self, key: SessionKey, new_name: str) -> None: ...
    async def delete(self, key: SessionKey) -> None: ...
    async def load(self, key: SessionKey) -> Optional[list[dict]]: ...
    async def list_sessions(self) -> list[dict]: ...


# ═══════════════════════════════════════════════
# 控制协议 — Query 类
# ═══════════════════════════════════════════════

class Query:
    """
    双向控制协议处理器。

    管理 CLI 子进程和 SDK 之间的双向通信：
    - 发送用户消息
    - 处理 can_use_tool 请求
    - 处理 hook_callback 请求
    - 处理 MCP 消息
    - 流式消息分发给消费者
    """

    def __init__(
        self,
        transport: Transport,
        can_use_tool: Optional[CanUseTool] = None,
        hooks: Optional[dict[str, list[dict]]] = None,
        sdk_mcp_servers: Optional[dict[str, Any]] = None,
        agents: Optional[dict[str, dict]] = None,
        skills: Optional[list[str] | Literal["all"]] = None,
    ):
        self.transport = transport
        self.can_use_tool = can_use_tool
        self.hooks = hooks or {}
        self.sdk_mcp_servers = sdk_mcp_servers or {}
        self._agents = agents
        self._skills = skills

        # 控制协议状态
        self.pending_control_responses: dict[str, asyncio.Event] = {}
        self.pending_control_results: dict[str, Any] = {}
        self.hook_callbacks: dict[str, Callable] = {}
        self.next_callback_id = 0
        self._request_counter = 0

        # 消息流
        self._initialized = False
        self._closed = False

    async def initialize(self) -> dict[str, Any]:
        """初始化控制协议 — 发送 hooks/agents/skills 配置到 CLI"""
        hooks_config = {}
        if self.hooks:
            for event, matchers in self.hooks.items():
                if matchers:
                    hooks_config[event] = []
                    for matcher in matchers:
                        callback_ids = []
                        for callback in matcher.get("hooks", []):
                            callback_id = f"hook_{self.next_callback_id}"
                            self.next_callback_id += 1
                            self.hook_callbacks[callback_id] = callback
                            callback_ids.append(callback_id)
                        hooks_config[event].append({
                            "matcher": matcher.get("matcher"),
                            "hookCallbackIds": callback_ids,
                        })

        request = {
            "subtype": "initialize",
            "hooks": hooks_config if hooks_config else None,
        }
        if self._agents:
            request["agents"] = self._agents
        if isinstance(self._skills, list):
            request["skills"] = self._skills

        return await self._send_control_request(request, timeout=60.0)

    async def _send_control_request(self, request: dict, timeout: float = 60.0) -> dict:
        """发送控制请求 → 等待响应"""
        request_id = f"req_{self._request_counter}_{os.urandom(4).hex()}"
        self._request_counter += 1

        # 在实际实现中，这里会用 anyio.Event 等待
        # 这里展示接口
        control_request = {
            "type": "control_request",
            "request_id": request_id,
            "request": request,
        }
        await self.transport.write(json.dumps(control_request) + "\n")
        return {}  # 简化：实际等待 CLI 响应

    async def interrupt(self) -> None:
        await self._send_control_request({"subtype": "interrupt"})

    async def set_permission_mode(self, mode: PermissionMode) -> None:
        await self._send_control_request({"subtype": "set_permission_mode", "mode": mode})

    async def set_model(self, model: str | None) -> None:
        await self._send_control_request({"subtype": "set_model", "model": model})

    async def close(self) -> None:
        self._closed = True
        await self.transport.close()


# ═══════════════════════════════════════════════
# 快速导入
# ═══════════════════════════════════════════════

import asyncio
from typing import TypedDict

__all__ = [
    "Transport", "Query",
    "ClaudeAgentOptions", "PermissionMode", "EffortLevel",
    "AgentDefinition", "HookMatcher", "HookEvent", "HookJSONOutput",
    "CanUseTool", "PermissionResult", "PermissionResultAllow", "PermissionResultDeny",
    "ToolPermissionContext",
    "Message", "UserMessage", "AssistantMessage", "ResultMessage", "SystemMessage",
    "TaskStartedMessage", "TaskProgressMessage", "TaskUpdatedMessage",
    "ContentBlock", "TextBlock", "ThinkingBlock", "ToolUseBlock", "ToolResultBlock",
    "ThinkingConfig", "SessionKey", "SessionStore", "SandboxSettings",
    "query", "ClaudeSDKClient",
]


# ═══════════════════════════════════════════════
# 高层 API
# ═══════════════════════════════════════════════

async def query(
    prompt: str | AsyncIterable[dict],
    options: Optional[ClaudeAgentOptions] = None,
    transport: Optional[Transport] = None,
) -> AsyncIterator[Message]:
    """一次性查询"""
    if options is None:
        options = ClaudeAgentOptions()
    # 简化实现 — 实际会启动子进程 + 控制协议
    yield AssistantMessage(content=[TextBlock(text=f"Echo: {prompt}")])
    yield ResultMessage(subtype="success")


class ClaudeSDKClient:
    """交互式会话客户端"""

    def __init__(self, options: Optional[ClaudeAgentOptions] = None):
        self.options = options or ClaudeAgentOptions()
        self._transport: Optional[Transport] = None
        self._query: Optional[Query] = None

    async def connect(self, prompt: Optional[str] = None) -> None:
        self._query = Query(
            transport=self._transport,  # type: ignore
            hooks=self._convert_hooks(),
            agents=self._convert_agents(),
        )
        await self._query.initialize()

    async def query(self, prompt: str) -> None: ...

    async def receive_response(self) -> AsyncIterator[Message]:
        yield ResultMessage(subtype="success")

    async def disconnect(self) -> None:
        if self._query:
            await self._query.close()

    async def __aenter__(self): return self
    async def __aexit__(self, *args): await self.disconnect()

    def _convert_hooks(self) -> dict[str, list[dict]]:
        if not self.options.hooks:
            return {}
        result = {}
        for event, matchers in self.options.hooks.items():
            result[event.value] = [m.to_cli_dict() for m in matchers]
        return result

    def _convert_agents(self) -> Optional[dict[str, dict]]:
        if not self.options.agents:
            return None
        return {name: agent.to_cli_dict() for name, agent in self.options.agents.items()}
