"""工作区自我管理工具 — Javis 的自我意识和自我组织能力"""

from core.tool_result import ToolResult
from core.workspace_manager import WorkspaceManager

_manager = WorkspaceManager()


def create_workspace_file(path: str, content: str, purpose: str = "",
                          category: str = "thought") -> ToolResult:
    """在工作区创建文件并自动注册"""
    return _manager.create_file(path, content, purpose, category)


def create_temp_file(content: str, purpose: str = "") -> ToolResult:
    """创建临时文件（自动命名，任务结束后会提醒清理）"""
    return _manager.create_temp(content, purpose)


def list_workspace() -> ToolResult:
    """列出工作区中所有由AI创建的文件"""
    return _manager.list_workspace()


def cleanup_temp(confirmed: bool = False) -> ToolResult:
    """清理临时文件。需要用户确认后执行"""
    return _manager.cleanup_temp(confirmed=confirmed)


def organize_workspace() -> ToolResult:
    """自动整理工作区文件到正确的子目录"""
    return _manager.organize()


def reflect_on_workspace() -> ToolResult:
    """分析工作区状态，给出整理建议"""
    return _manager.reflect()


def delete_file_handler(path: str) -> ToolResult:
    """删除文件或目录（高风险操作，含沙箱保护）"""
    return _manager.delete_file(path)
