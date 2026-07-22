"""
工具注册表 — 注册、查找、执行工具
"""
from __future__ import annotations
import json, logging, time, traceback
from typing import Any, Optional, Callable
from dataclasses import dataclass, field

from .types import ToolDef, ToolResult

logger = logging.getLogger("agent.tools")


class ToolRegistry:
    """
    工具注册表 — 所有工具的中央仓库。

    职责:
    1. 注册工具 (提供 name + description + JSON Schema + handler)
    2. 批量查找 (给 LLM 发送可用工具列表)
    3. 执行工具 (按 tool_call 调度到对应 handler)

    使用:
        reg = ToolRegistry()
        reg.register(ToolDef("read_file", "读取文件", {...}, handler, "file"))
        result = reg.execute("read_file", {"path": "/foo.txt"})
    """

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._categories: dict[str, list[str]] = {}
        self._exec_count: dict[str, int] = {}  # 执行计数

    # ═══════════════════════════════════════════════
    # 注册
    # ═══════════════════════════════════════════════

    def register(self, tool: ToolDef) -> None:
        """注册一个工具"""
        self._tools[tool.name] = tool
        self._categories.setdefault(tool.category, []).append(tool.name)

    def register_many(self, tools: list[ToolDef]) -> None:
        """批量注册"""
        for t in tools:
            self.register(t)

    def unregister(self, name: str) -> None:
        """移除工具"""
        if name in self._tools:
            cat = self._tools[name].category
            self._tools.pop(name)
            if cat in self._categories:
                self._categories[cat] = [n for n in self._categories[cat] if n != name]

    # ═══════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def list_all(self) -> list[ToolDef]:
        return list(self._tools.values())

    def list_by_category(self, category: str) -> list[ToolDef]:
        names = self._categories.get(category, [])
        return [self._tools[n] for n in names if n in self._tools]

    def get_api_definitions(self) -> list[dict]:
        """生成发送给 LLM 的工具定义列表"""
        return [t.to_api_dict() for t in self._tools.values()]

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    # ═══════════════════════════════════════════════
    # 执行
    # ═══════════════════════════════════════════════

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        执行一个工具调用。

        流程:
        1. 查找工具定义
        2. 验证必要参数
        3. 调用 handler
        4. 包装结果 (处理异常)
        5. 记录执行统计
        """
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(
                content=json.dumps({"error": f"未找到工具: {name}"}),
                is_error=True,
            )

        # 验证参数
        validation_error = self._validate_args(tool, arguments)
        if validation_error:
            return ToolResult(
                content=json.dumps({"error": validation_error}),
                is_error=True,
            )

        # 执行
        start = time.time()
        try:
            raw_result = tool.handler(**arguments)

            # 包装为 ToolResult (如果 handler 直接返回字符串)
            if isinstance(raw_result, ToolResult):
                result = raw_result
            elif isinstance(raw_result, dict):
                result = ToolResult(
                    content=raw_result.get("content", json.dumps(raw_result)),
                    structured=raw_result.get("structured"),
                    is_error=raw_result.get("is_error", False),
                )
            else:
                result = ToolResult(content=str(raw_result))

        except Exception as e:
            logger.error(f"工具执行异常 [{name}]: {e}\n{traceback.format_exc()}")
            result = ToolResult(
                content=json.dumps({
                    "error": str(e),
                    "traceback": traceback.format_exc()[-500:],  # 截断
                }),
                is_error=True,
            )

        elapsed_ms = (time.time() - start) * 1000
        self._exec_count[name] = self._exec_count.get(name, 0) + 1
        logger.info(f"工具 [{name}] 完成 ({elapsed_ms:.0f}ms, #{self._exec_count[name]})")

        return result

    def _validate_args(self, tool: ToolDef, args: dict) -> Optional[str]:
        """基础参数验证 (简化版)"""
        schema = tool.parameters
        if "required" not in schema:
            return None

        for required_param in schema["required"]:
            if required_param not in args:
                return f"缺少必要参数: {required_param} (工具: {tool.name})"
        return None

    # ═══════════════════════════════════════════════
    # 统计
    # ═══════════════════════════════════════════════

    def stats(self) -> dict:
        """工具使用统计"""
        return {
            "total_tools": len(self._tools),
            "categories": list(self._categories.keys()),
            "exec_counts": dict(self._exec_count),
        }


# ═══════════════════════════════════════════════
# 内置工具实现
# ═══════════════════════════════════════════════

def build_builtin_tools(workspace_dir: str, path_mapper) -> list[ToolDef]:
    """
    构建内置工具集。

    对应系统提示中的核心工具:
    - Read    → 读取文件/图片/PDF
    - Write   → 创建文件
    - Edit    → 精确字符串替换
    - Bash    → 执行 Shell 命令
    - Grep    → 内容搜索 (ripgrep)
    - Glob    → 文件名模式匹配
    """
    return [
        # ── Read ──
        ToolDef(
            name="read_file",
            description="读取文件内容。支持文本文件、图片 (PNG/JPG)、PDF。用 pages 参数指定 PDF 页码范围。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "绝对路径"},
                    "offset": {"type": "integer", "description": "起始行号"},
                    "limit": {"type": "integer", "description": "行数限制"},
                    "pages": {"type": "string", "description": "PDF 页码范围"},
                },
                "required": ["file_path"],
            },
            handler=_read_file,
            category="file",
        ),
        # ── Write ──
        ToolDef(
            name="write_file",
            description="创建或覆盖文件",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "绝对路径"},
                    "content": {"type": "string", "description": "文件内容"},
                },
                "required": ["file_path", "content"],
            },
            handler=_write_file,
            category="file",
        ),
        # ── Edit ──
        ToolDef(
            name="edit_file",
            description="精确字符串替换。old_string 必须精确匹配文件中唯一的一段文本。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string", "description": "要替换的原文"},
                    "new_string": {"type": "string", "description": "替换后的文字"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
            handler=_edit_file,
            category="file",
        ),
        # ── Bash ──
        ToolDef(
            name="bash",
            description="执行 Shell 命令 (隔离 Linux 环境)。适合批量操作。",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_ms": {"type": "integer", "default": 30000},
                },
                "required": ["command"],
            },
            handler=_bash,
            category="system",
        ),
        # ── Grep ──
        ToolDef(
            name="grep",
            description="基于 ripgrep 的内容搜索。支持正则。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "搜索目录"},
                    "glob": {"type": "string", "description": "文件过滤 (如 *.py)"},
                    "-i": {"type": "boolean", "description": "忽略大小写"},
                },
                "required": ["pattern"],
            },
            handler=_grep,
            category="search",
        ),
        # ── Glob ──
        ToolDef(
            name="glob",
            description="文件名模式匹配。支持 ** 递归。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式"},
                    "path": {"type": "string", "description": "搜索根目录"},
                },
                "required": ["pattern"],
            },
            handler=_glob,
            category="search",
        ),
    ]


# ═══════════════════════════════════════════════
# 内置工具 Handler 实现
# ═══════════════════════════════════════════════

def _read_file(file_path: str, offset: int = 0, limit: int = 2000,
               pages: Optional[str] = None) -> ToolResult:
    """实现 Read 工具"""
    try:
        if not os.path.exists(file_path):
            return ToolResult(content=f"错误: 文件不存在: {file_path}", is_error=True)

        # PDF 处理
        if file_path.endswith(".pdf") and pages:
            return _read_pdf(file_path, pages)

        # 图片处理
        if file_path.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return _read_image(file_path)

        # 文本文件
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            if offset > 0:
                lines = f.readlines()
                selected = lines[offset : offset + limit]
                content = "".join(selected)
            else:
                content = f.read()

        return ToolResult(content=content)
    except Exception as e:
        return ToolResult(content=f"读取失败: {e}", is_error=True)


def _read_pdf(file_path: str, pages: str) -> ToolResult:
    """读取 PDF 页面"""
    try:
        import subprocess
        # 简化版 PDF 阅读 — 用 pdftotext
        result = subprocess.run(
            ["pdftotext", "-f", pages.split("-")[0],
             "-l", pages.split("-")[-1] if "-" in pages else pages.split("-")[0],
             file_path, "-"],
            capture_output=True, text=True, timeout=30
        )
        return ToolResult(content=result.stdout or result.stderr)
    except Exception as e:
        return ToolResult(content=f"PDF 读取失败: {e}", is_error=True)


def _read_image(file_path: str) -> ToolResult:
    """读取图片 (返回描述性信息)"""
    try:
        from PIL import Image
        img = Image.open(file_path)
        info = {
            "format": img.format,
            "size": list(img.size),
            "mode": img.mode,
            "file_path": file_path,
        }
        return ToolResult(content=json.dumps(info, ensure_ascii=False))
    except Exception as e:
        return ToolResult(content=f"图片读取失败: {e}", is_error=True)


def _write_file(file_path: str, content: str) -> ToolResult:
    """实现 Write 工具"""
    try:
        dirname = os.path.dirname(file_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        size = len(content)
        lines = content.count("\n") + 1
        return ToolResult(
            content=json.dumps({"ok": True, "path": file_path, "size": size, "lines": lines})
        )
    except Exception as e:
        return ToolResult(content=f"写入失败: {e}", is_error=True)


def _edit_file(file_path: str, old_string: str, new_string: str,
               replace_all: bool = False) -> ToolResult:
    """实现 Edit 工具 (精确字符串替换)"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            original = f.read()

        if old_string not in original:
            return ToolResult(
                content=f"错误: old_string 在文件中不匹配 (或多次出现, 需设置 replace_all=True)",
                is_error=True,
            )

        count = original.count(old_string)
        if count > 1 and not replace_all:
            return ToolResult(
                content=f"错误: old_string 出现 {count} 次。如要全部替换请设 replace_all=True",
                is_error=True,
            )

        edited = original.replace(old_string, new_string)

        if edited == original:
            return ToolResult(content="无更改: new_string 与 old_string 相同", is_error=True)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(edited)

        return ToolResult(
            content=json.dumps({"ok": True, "replacements": count if replace_all else 1})
        )
    except Exception as e:
        return ToolResult(content=f"编辑失败: {e}", is_error=True)


def _bash(command: str, timeout_ms: int = 30000) -> ToolResult:
    """实现 Bash 工具"""
    try:
        import subprocess
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout_ms / 1000,
        )
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        return ToolResult(content=output, is_error=result.returncode != 0)
    except subprocess.TimeoutExpired:
        return ToolResult(content=f"命令超时 ({timeout_ms}ms)", is_error=True)
    except Exception as e:
        return ToolResult(content=f"执行失败: {e}", is_error=True)


def _grep(pattern: str, path: str = ".", glob: Optional[str] = None,
          ignore_case: bool = False, **kwargs) -> ToolResult:
    """实现 Grep 工具 (简化为 Python 实现)"""
    import fnmatch, re
    try:
        flags = re.IGNORECASE if kwargs.get("-i") or ignore_case else 0
        regex = re.compile(pattern, flags)

        results = []
        for root, dirs, files in os.walk(path):
            # 跳过隐藏目录
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if glob and not fnmatch.fnmatch(fname, glob):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(f"{fpath}:{i}: {line.rstrip()}")
                                if len(results) >= 100:  # 限制结果
                                    break
                except Exception:
                    pass
                if len(results) >= 100:
                    break

        content = "\n".join(results) if results else "无匹配结果"
        if len(results) >= 100:
            content += f"\n... (结果被截断, 共 >= {len(results)} 行)"
        return ToolResult(content=content)
    except Exception as e:
        return ToolResult(content=f"搜索失败: {e}", is_error=True)


def _glob(pattern: str, path: str = ".") -> ToolResult:
    """实现 Glob 工具"""
    import glob as _glob_mod
    try:
        matches = _glob_mod.glob(os.path.join(path, pattern), recursive=True)
        content = "\n".join(sorted(matches[:200]))
        if len(matches) > 200:
            content += f"\n... (结果被截断, 共 {len(matches)} 个文件)"
        return ToolResult(content=content or "无匹配文件")
    except Exception as e:
        return ToolResult(content=f"Glob 失败: {e}", is_error=True)


import os
