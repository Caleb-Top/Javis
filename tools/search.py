"""搜索三件套 — grep / glob / file_edit

基于 subprocess 调用 ripgrep (rg) 和 Python glob 实现文件搜索和编辑。
所有函数返回 ToolResult。
"""

import os
import re
import subprocess
import fnmatch
import logging
from typing import Optional

from core.tool_result import ToolResult

logger = logging.getLogger("search_tools")

# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------

_TIMEOUT = 30  # 本地搜索操作超时

RG_PATH = None  # 缓存的 rg 路径

def _find_rg() -> Optional[str]:
    """查找 ripgrep 可执行文件"""
    global RG_PATH
    if RG_PATH is not None:
        return RG_PATH
    # 优先使用 tools 目录下自带的
    candidates = []
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(tools_dir, "rg", "rg.exe"))
    candidates.append(os.path.join(tools_dir, "rg", "rg"))
    # 系统 PATH
    for name in ("rg", "rg.exe"):
        r = subprocess.run(["where", name] if os.name == "nt" else ["which", name],
                          capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            candidates.append(r.stdout.strip().split("\n")[0])
    # 验证
    for c in candidates:
        if os.path.isfile(c):
            RG_PATH = c
            return c
    return None

def _run_rg(args: list, cwd: str, timeout: int = _TIMEOUT) -> ToolResult:
    """统一执行 rg 命令"""
    rg = _find_rg()
    if not rg:
        return ToolResult.failure("未找到 rg (ripgrep) 命令。请确认 rg 已安装或在 tools/rg/ 目录中。")
    try:
        r = subprocess.run(
            [rg] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
        )
        if r.returncode == 0:
            output = r.stdout.strip()
            if not output:
                return ToolResult.success("(无匹配结果)")
            return ToolResult.success(output)
        if r.returncode == 1:
            # rg returns 1 for "no matches"
            return ToolResult.success("(无匹配结果)")
        return ToolResult.failure(r.stderr.strip() or f"rg 返回码: {r.returncode}")
    except subprocess.TimeoutExpired:
        return ToolResult.failure("搜索超时，请缩小搜索范围")
    except Exception as e:
        return ToolResult.failure(f"rg 执行异常: {e}")


# ===================================================================
# P0-2-1  grep
# ===================================================================

def grep(
    pattern: str,
    path: str = ".",
    glob: str = "",
    output_mode: str = "content",
    max_count: int = 200,
    context: int = 0,
    ignore_case: bool = False,
    multiline: bool = False,
    include_hidden: bool = False,
) -> ToolResult:
    """在文件中搜索正则表达式模式（基于 ripgrep）。

    Args:
        pattern: 正则表达式模式
        path: 搜索目录或文件路径
        glob: 文件名过滤 glob (如 "*.py" "*.{ts,tsx}")
        output_mode: "content" 显示匹配行, "files_with_matches" 仅文件路径, "count" 匹配计数
        max_count: 最大输出行数 (默认200)
        context: 匹配前后的上下文行数
        ignore_case: 忽略大小写
        multiline: 多行模式 (. 匹配换行符, 模式可跨行)
        include_hidden: 是否搜索隐藏文件/目录
    """
    cwd = os.path.abspath(path) if os.path.isdir(path) else os.path.dirname(os.path.abspath(path)) or "."

    args = []

    # 输出模式
    if output_mode == "files_with_matches":
        args.append("-l")
    elif output_mode == "count":
        args.append("-c")
    else:
        args.append("--heading")
        if context > 0:
            args.extend(["-C", str(context)])

    # 选项
    if ignore_case:
        args.append("-i")
    if not include_hidden:
        args.extend(["--glob", "!.*"])
    else:
        args.append("--hidden")

    if multiline:
        args.extend(["--multiline", "--multiline-dotall"])

    # max count
    args.extend(["-m", str(max_count)])

    # glob 过滤
    if glob:
        args.extend(["-g", glob])

    # pattern 和 path
    args.append(pattern)
    if os.path.isfile(path):
        args.append(path)
    elif path != ".":
        args.append(path)

    r = _run_rg(args, cwd)

    if r.success and len(r.data) > 8000:
        lines = r.data.split("\n")
        return ToolResult.success(
            f"[输出较长，已截断至前 {max_count} 行]\n\n" + "\n".join(lines[:max_count])
        )
    return r


# ===================================================================
# P0-2-2  glob
# ===================================================================

def glob_find(
    pattern: str,
    path: str = ".",
    max_results: int = 100,
    include_hidden: bool = False,
) -> ToolResult:
    """按 glob 模式查找文件（使用 Python 原生 fnmatch + os.walk）。

    Args:
        pattern: glob 模式 (如 "**/*.py", "src/**/*.tsx")
        path: 起始搜索目录
        max_results: 最大返回结果数
        include_hidden: 是否包含隐藏文件和目录
    """
    cwd = os.path.abspath(path)
    if not os.path.isdir(cwd):
        return ToolResult.failure(f"路径不存在或不是目录: {path}")

    results = []
    scanned = 0

    try:
        recursive = "**" in pattern

        if recursive:
            parts = pattern.split("**")
            root_dir = cwd
            if parts[0].rstrip("/"):
                root_dir = os.path.join(cwd, parts[0].rstrip("/"))
            file_pattern = parts[-1].lstrip("/") or "*"

            for dirpath, dirnames, filenames in os.walk(root_dir):
                if not include_hidden:
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    filenames = [f for f in filenames if not f.startswith(".")]
                for fn in filenames:
                    if fnmatch.fnmatch(fn, file_pattern):
                        rel = os.path.relpath(os.path.join(dirpath, fn), cwd)
                        results.append(rel)
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
                scanned += 1
                if scanned > 10000:
                    break
        else:
            pattern_dir = os.path.dirname(pattern)
            pattern_name = os.path.basename(pattern)
            search_dir = os.path.join(cwd, pattern_dir) if pattern_dir else cwd

            if not os.path.isdir(search_dir):
                return ToolResult.failure(f"目录不存在: {pattern_dir}")

            try:
                entries = os.listdir(search_dir)
                if not include_hidden:
                    entries = [e for e in entries if not e.startswith(".")]
                for entry in entries:
                    full = os.path.join(search_dir, entry)
                    if os.path.isfile(full) and fnmatch.fnmatch(entry, pattern_name):
                        rel = os.path.relpath(full, cwd)
                        results.append(rel)
                        if len(results) >= max_results:
                            break
            except PermissionError:
                return ToolResult.failure(f"无权限访问目录: {pattern_dir}")

    except PermissionError:
        return ToolResult.failure(f"无权限访问: {path}")
    except Exception as e:
        return ToolResult.failure(f"glob 执行异常: {e}")

    if not results:
        return ToolResult.success(f"(无匹配文件) pattern={pattern}")

    # 按修改时间排序
    results_with_mtime = []
    for f in results:
        full = os.path.join(cwd, f)
        try:
            mtime = os.path.getmtime(full)
            results_with_mtime.append((f, mtime))
        except OSError:
            results_with_mtime.append((f, 0))
    results_with_mtime.sort(key=lambda x: x[1], reverse=True)

    lines = [f"找到 {len(results)} 个匹配文件:"]
    for f, _ in results_with_mtime[:max_results]:
        lines.append(f"  {f}")
    if len(results) > max_results:
        lines.append(f"  ... 还有 {len(results) - max_results} 个结果")

    return ToolResult.success("\n".join(lines))


# ===================================================================
# P0-2-3  file_edit
# ===================================================================

def file_edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> ToolResult:
    """精确字符串替换编辑文件。

    与 Claude Code 的 Edit 工具行为一致:
    - old_string 必须在文件中恰好出现一次 (replace_all=False)
    - 制表符、缩进等必须完全匹配

    Args:
        file_path: 要编辑的文件绝对路径
        old_string: 要替换的字符串（必须完全匹配，包括缩进）
        new_string: 替换后的字符串
        replace_all: True 时替换所有出现的位置
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return ToolResult.failure(f"文件不存在: {file_path}")
    except PermissionError:
        return ToolResult.failure(f"无权限读取文件: {file_path}")
    except Exception as e:
        return ToolResult.failure(f"读取文件失败: {e}")

    count = content.count(old_string)
    if count == 0:
        lines_preview = []
        for i, line in enumerate(old_string.split("\n")[:3], 1):
            lines_preview.append(f"  L{i}: {repr(line)}")
        return ToolResult.failure(
            f"未找到要替换的字符串。文件: {file_path}\n"
            f"查找内容的前几行:\n" + "\n".join(lines_preview)
        )

    if not replace_all and count > 1:
        return ToolResult.failure(
            f"old_string 在文件中出现了 {count} 次。"
            f"请提供更精确的上下文使匹配唯一，或设置 replace_all=True 替换所有出现。"
        )

    new_content = content.replace(old_string, new_string)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except PermissionError:
        return ToolResult.failure(f"无权限写入文件: {file_path}")
    except Exception as e:
        return ToolResult.failure(f"写入文件失败: {e}")

    replaced_count = count if replace_all else 1
    return ToolResult.success(
        f"文件已编辑: {file_path}\n"
        f"替换了 {replaced_count} 处匹配。"
    )
