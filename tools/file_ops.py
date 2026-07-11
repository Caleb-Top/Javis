"""文件操作工具"""

import os
import logging
from core.tool_result import ToolResult
from utils.error_messages import friendly_error, translate_file_error

logger = logging.getLogger("tools.file_ops")


def file_read(path: str, offset: int = 0, limit: int = 100) -> ToolResult:
    """读取文件"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            total = len(lines)
            selected = lines[offset:offset + limit]
            content = "".join(selected)
            return ToolResult.success(
                f"文件 {path} (共 {total} 行, 显示 {offset+1}-{offset+len(selected)} 行):\n{content}"
            )
    except FileNotFoundError:
        return ToolResult.failure(translate_file_error(path, FileNotFoundError(path)))
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def file_write(path: str, content: str) -> ToolResult:
    """写入文件"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult.success(f"文件已写入: {path} ({len(content)} 字符)")
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def file_list(directory: str = ".") -> ToolResult:
    """列出目录"""
    try:
        # 智能路径映射
        path_map = {
            "桌面": os.path.expanduser("~/Desktop"),
            "desktop": os.path.expanduser("~/Desktop"),
            "下载": os.path.expanduser("~/Downloads"),
            "文档": os.path.expanduser("~/Documents"),
            "d盘": "D:/", "d:/": "D:/", "d:\\": "D:/",
            "c盘": "C:/", "c:/": "C:/", "c:\\": "C:/",
            "home": os.path.expanduser("~"),
        }
        directory = path_map.get(directory.lower(), directory)
        if not directory or directory == ".":
            directory = os.getcwd()
        items = os.listdir(directory)
        result = [f"目录 {directory} (共 {len(items)} 项):"]
        for item in sorted(items):
            full = os.path.join(directory, item)
            prefix = "📁" if os.path.isdir(full) else "📄"
            size = os.path.getsize(full) if os.path.isfile(full) else 0
            result.append(f"  {prefix} {item} ({_format_size(size)})")
        return ToolResult.success("\n".join(result[:50]))
    except Exception as e:
        return ToolResult.failure(friendly_error(e))


def _format_size(size: int) -> str:
    if size < 1024: return f"{size}B"
    if size < 1024 ** 2: return f"{size / 1024:.1f}KB"
    return f"{size / 1024 ** 2:.1f}MB"
