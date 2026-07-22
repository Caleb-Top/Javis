"""
核心数据类型 — Agent 系统中所有基础数据结构的定义
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Literal
from enum import Enum
import os


# ═══════════════════════════════════════════════
# 消息类型
# ═══════════════════════════════════════════════

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """对话消息"""
    role: Role
    content: str
    name: Optional[str] = None          # 工具名 (仅 tool 消息)
    tool_calls: list[ToolCall] = field(default_factory=list)  # 助手发出的工具调用
    tool_call_id: Optional[str] = None  # 工具调用 ID (仅 tool 消息)

    def to_api_dict(self) -> dict:
        """转为 API 兼容的消息格式"""
        d: dict = {"role": self.role.value}
        if self.content:
            d["content"] = self.content
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = [tc.to_api_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_api_dict(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.arguments,
            },
        }


# ═══════════════════════════════════════════════
# 工具定义
# ═══════════════════════════════════════════════

@dataclass
class ToolDef:
    """工具定义"""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable           # 实际执行函数
    category: str = "general"

    def to_api_dict(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    """工具执行结果"""
    content: str
    is_error: bool = False
    structured: Optional[dict] = None


# ═══════════════════════════════════════════════
# Skill 定义
# ═══════════════════════════════════════════════

@dataclass
class SkillDef:
    """技能定义"""
    name: str
    description: str                      # 触发条件描述
    instructions: str                     # SKILL.md 的完整内容
    scripts_dir: Optional[str] = None     # 可执行脚本目录
    references: dict[str, str] = field(default_factory=dict)  # 参考文档
    installed: bool = True


# ═══════════════════════════════════════════════
# Memory 条目
# ═══════════════════════════════════════════════

class MemoryType(str, Enum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass
class MemoryEntry:
    """一条记忆"""
    name: str                              # 文件名 (不含 .md)
    description: str                       # 一行描述
    type: MemoryType
    content: str                           # 正文
    file_path: str                         # 磁盘路径

    @property
    def frontmatter(self) -> str:
        return f"---\nname: {self.name}\ndescription: {self.description}\ntype: {self.type.value}\n---\n\n{self.content}"


# ═══════════════════════════════════════════════
# Task 条目
# ═══════════════════════════════════════════════

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DELETED = "deleted"


@dataclass
class Task:
    """任务"""
    id: str
    subject: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    owner: Optional[str] = None
    active_form: Optional[str] = None     # 进行中时的显示文本
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return len(self.blocked_by) > 0

    @property
    def is_ready(self) -> bool:
        return self.status == TaskStatus.PENDING and not self.is_blocked


# ═══════════════════════════════════════════════
# Agent 配置
# ═══════════════════════════════════════════════

@dataclass
class AgentConfig:
    """Agent 全局配置"""
    model: str = "claude-fable-5"
    max_tools_per_turn: int = 10
    max_rounds: int = 20                  # 最大推理轮数
    temperature: float = 0.7
    max_tokens: int = 4096
    workspace_dir: str = field(default_factory=lambda: os.path.expanduser("~/workspace"))
    memory_dir: str = ""                  # 内存目录, 设空则使用默认
    skills_dirs: list[str] = field(default_factory=list)  # Skill 搜索路径

    def __post_init__(self):
        if not self.memory_dir:
            self.memory_dir = os.path.join(self.workspace_dir, "memory")


# ═══════════════════════════════════════════════
# 路径映射
# ═══════════════════════════════════════════════

@dataclass
class PathMapper:
    """
    路径映射器 — Windows 路径 ↔ VM 路径 的双向转换。

    Windows 实际路径           → VM 工具路径
    D:\project                → /sessions/sess/mnt/project/
    """
    session_id: str = "default"
    mounts: dict[str, str] = field(default_factory=dict)  # win_path → mount_name

    def win_to_vm(self, win_path: str) -> str:
        """Windows 路径 → VM 路径"""
        for win_root, mount_name in self.mounts.items():
            if win_path.startswith(win_root):
                rel = os.path.relpath(win_path, win_root)
                return f"/sessions/{self.session_id}/mnt/{mount_name}/{rel}"
        return win_path

    def vm_to_win(self, vm_path: str) -> str:
        """VM 路径 → Windows 路径"""
        import os as _os
        prefix = f"/sessions/{self.session_id}/mnt/"
        if vm_path.startswith(prefix):
            rest = vm_path[len(prefix):]
            # rest 格式: "mount_name/rel/path" 或 "mount_name"
            parts = rest.split("/", 1)
            mount_name = parts[0]
            rel = parts[1] if len(parts) > 1 else ""
            for win_root, mn in self.mounts.items():
                if mn == mount_name:
                    if rel:
                        return _os.path.join(win_root, rel)
                    return win_root
        return vm_path
