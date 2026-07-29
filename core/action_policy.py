"""Single action policy for Javis UI approval and execution guardrails."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActionDecision:
    action: str
    reason: str = ""


DELETE_TOOLS = {
    "file_delete",
    "delete_file",
    "workspace_delete",
    "uninstall_app",
}
WRITE_TOOLS = {
    "file_write",
    "create_workspace_file",
    "workspace_save",
}
DESTRUCTIVE_COMMAND = re.compile(
    r"\b(remove-item|del|erase|rmdir|rd|rm|unlink|shutil\.rmtree|os\.remove|os\.unlink)\b",
    re.IGNORECASE,
)
MUTATING_COMMAND = re.compile(
    r"\b(set-content|add-content|out-file|move-item|copy-item|python|powershell|cmd)\b|[>]|\bwrite_(text|bytes)\b",
    re.IGNORECASE,
)
PATH_KEYS = {
    "path",
    "file",
    "filename",
    "target",
    "destination",
    "cwd",
    "directory",
    "root",
}


def _normal(path: str | Path) -> str:
    text = os.path.expandvars(os.path.expanduser(str(path or ""))).replace("/", "\\")
    return os.path.normcase(os.path.abspath(text)).rstrip("\\")


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "\\")


def _paths(params: dict[str, Any]) -> list[str]:
    found: list[str] = []

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key).casefold())
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, key)
        elif isinstance(value, (str, Path)) and key in PATH_KEYS:
            text = str(value).strip()
            if text:
                found.append(_normal(text))

    visit(params)
    return found


def _protected_windows_roots() -> tuple[str, ...]:
    windows = _normal(os.environ.get("SystemRoot", r"C:\Windows"))
    return (
        windows,
        _normal(r"C:\System Volume Information"),
        _normal(r"C:\Recovery"),
        _normal(r"C:\EFI"),
    )


def _is_bottom_system_path(path: str) -> bool:
    if any(_inside(path, root) for root in _protected_windows_roots()):
        return True
    name = path.casefold()
    return name in {
        _normal(r"C:\bootmgr"),
        _normal(r"C:\pagefile.sys"),
        _normal(r"C:\hiberfil.sys"),
        _normal(r"C:\swapfile.sys"),
    }


def evaluate_action(
    tool_name: str,
    params: dict[str, Any] | None,
    *,
    app_root: str | Path | None = None,
) -> ActionDecision:
    tool = str(tool_name or "").strip()
    values = params if isinstance(params, dict) else {}
    paths = _paths(values)
    root = _normal(app_root or Path(__file__).resolve().parent.parent)

    command = str(
        values.get("command")
        or values.get("code")
        or values.get("script")
        or ""
    )
    normalized_command = command.replace("/", "\\").casefold()
    protected_mentions = [
        protected for protected in _protected_windows_roots()
        if protected.casefold() in normalized_command
    ]

    deleting = tool in DELETE_TOOLS or (
        tool in {"execute_command", "system_execute", "run_code"}
        and bool(DESTRUCTIVE_COMMAND.search(command))
    )
    if deleting and (
        any(_is_bottom_system_path(path) for path in paths)
        or protected_mentions
    ):
        return ActionDecision("deny", "禁止删除 Windows 底层系统文件")

    if deleting:
        return ActionDecision("confirm", "删除文件或应用前需要本地确认")

    writes_self = tool in WRITE_TOOLS and any(_inside(path, root) for path in paths)
    command_touches_self = (
        tool in {"execute_command", "system_execute", "run_code"}
        and root.casefold() in normalized_command
        and bool(MUTATING_COMMAND.search(command))
    )
    if writes_self or command_touches_self:
        return ActionDecision("confirm", "修改 Javis 自身代码或应用文件前需要本地确认")

    return ActionDecision("allow")


__all__ = ["ActionDecision", "evaluate_action"]
