"""搜索三件套 — grep/glob/file_edit，返回 ToolResult"""

import os
import re
import fnmatch
import logging
from typing import Optional

from core.tool_result import ToolResult

logger = logging.getLogger("search_tools")

# ---------------------------------------------------------------------------
# P0-2-1  grep — 内容搜索（仿 ripgrep）
# ---------------------------------------------------------------------------

def grep(
    pattern: str,
    path: str = ".",
    glob: str = "",
    output_mode: str = "content",
    head_limit: int = 50,
    case_insensitive: bool = False,
    context: int = 0,
    multiline: bool = False,
) -> ToolResult:
    """内容搜索 — 正则匹配文件内容

    Args:
        pattern: 正则表达式
        path: 搜索目录
        glob: 文件名过滤（如 "*.py"）
        output_mode: content(显示匹配行), files_with_matches(仅文件路径), count(匹配计数)
        head_limit: 最大输出行数
        case_insensitive: 忽略大小写
        context: 上下文行数（仅 content 模式生效）
        multiline: 多行匹配模式
    """
    if not os.path.isdir(path):
        return ToolResult.failure(f"搜索目录不存在: {path}")

    flags = re.MULTILINE | re.DOTALL if multiline else 0
    if case_insensitive:
        flags |= re.IGNORECASE

    try:
        compiled = re.compile(pattern, flags)
    except re.error as e:
        return ToolResult.failure(f"正则表达式无效: {e}")

    results = []
    file_count = 0
    total_matches = 0
    limit_reached = False

    for root, dirs, files in os.walk(path):
        # 跳过隐藏目录和常见的无关目录
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
            "node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build"
        )]

        for fname in files:
            if limit_reached:
                break

            if glob and not fnmatch.fnmatch(fname, glob):
                continue

            full = os.path.join(root, fname)
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (PermissionError, OSError):
                continue

            matches = list(compiled.finditer(content))
            if not matches:
                continue

            file_count += 1
            total_matches += len(matches)

            if output_mode == "files_with_matches":
                results.append(full)
                if len(results) >= head_limit:
                    limit_reached = True
                    break
                continue

            if output_mode == "count":
                results.append(f"{full}: {len(matches)}")
                if len(results) >= head_limit:
                    limit_reached = True
                    break
                continue

            # output_mode == "content"
            if multiline:
                # 多行模式：只显示匹配的完整块
                for m in matches:
                    if len(results) >= head_limit:
                        limit_reached = True
                        break
                    snippet = m.group(0)
                    if len(snippet) > 500:
                        snippet = snippet[:500] + "...(已截断)"
                    line_no = content[:m.start()].count("\n") + 1
                    results.append(f"{full}:{line_no}: {snippet}")
                continue

            # 单行模式
            lines = content.split("\n")
            matched_lines = set()
            for m in matches:
                line_no = content[:m.start()].count("\n")
                matched_lines.add(line_no)

            for line_no in sorted(matched_lines):
                if len(results) >= head_limit:
                    limit_reached = True
                    break
                start = max(0, line_no - context)
                end = min(len(lines), line_no + context + 1)
                for ln in range(start, end):
                    marker = ">" if ln == line_no else " "
                    results.append(f"{full}:{ln+1}:{marker} {lines[ln]}")
                if context > 0 and line_no != sorted(matched_lines)[-1]:
                    results.append("---")

        if limit_reached:
            break

    if not results:
        return ToolResult.success(f"未找到匹配 '{pattern}' 的内容")

    suffix = f"\n\n... 已达到输出上限 ({head_limit} 条)" if limit_reached else ""
    prefix = f"搜索结果: 模式 '{pattern}' | {file_count} 个文件 | {total_matches} 处匹配\n\n"
    return ToolResult.success(prefix + "\n".join(results) + suffix)


# ---------------------------------------------------------------------------
# P0-2-2  glob_search — 文件名匹配搜索
# ---------------------------------------------------------------------------

def glob_search(
    pattern: str,
    path: str = ".",
    max_results: int = 100,
) -> ToolResult:
    """文件名搜索 — 支持通配符（**/*.py, src/*.ts 等）

    Args:
        pattern: glob 模式
        path: 搜索根目录
        max_results: 最大结果数
    """
    if not os.path.isdir(path):
        return ToolResult.failure(f"搜索目录不存在: {path}")

    results = []

    # 判断是否包含 ** 递归模式
    recursive = "**" in pattern

    if recursive:
        # 递归搜索
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in (
                "node_modules", "__pycache__", ".git", "venv", ".venv", "dist", "build"
            )]
            for fname in files:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, path)
                rel_normalized = rel.replace("\\", "/")
                if fnmatch.fnmatch(rel_normalized, pattern):
                    results.append(rel)
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break
    else:
        # 仅搜索当前层级
        try:
            entries = os.listdir(path)
        except PermissionError:
            return ToolResult.failure(f"无法访问目录: {path}")

        for entry in entries:
            if fnmatch.fnmatch(entry, pattern):
                full = os.path.join(path, entry)
                if os.path.isfile(full):
                    results.append(entry)
                if len(results) >= max_results:
                    break

    if not results:
        return ToolResult.success(f"未找到匹配 '{pattern}' 的文件")

    suffix = f"\n\n... 已达到输出上限 ({max_results} 条)" if len(results) >= max_results else ""
    return ToolResult.success(
        f"Glob 结果: 模式 '{pattern}' | {len(results)} 个文件\n\n" + "\n".join(results) + suffix
    )


# ---------------------------------------------------------------------------
# P0-2-3  file_edit — 精确字符串替换编辑
# ---------------------------------------------------------------------------

def file_edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> ToolResult:
    """精确字符串替换编辑

    在文件中查找 old_string 并替换为 new_string。
    要求 old_string 在文件中唯一匹配（除非 replace_all=True）。

    Args:
        file_path: 要编辑的文件路径
        old_string: 要替换的原字符串
        new_string: 替换后的新字符串
        replace_all: 是否替换所有匹配（默认 False，仅替换唯一匹配）
    """
    if not os.path.exists(file_path):
        return ToolResult.failure(f"文件不存在: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (PermissionError, OSError) as e:
        return ToolResult.failure(f"无法读取文件: {e}")

    if old_string == new_string:
        return ToolResult.failure("old_string 和 new_string 相同，无需编辑")

    count = content.count(old_string)

    if count == 0:
        return ToolResult.failure(
            f"未找到匹配的字符串。old_string 在文件中不存在。\n"
            f"提示: 确保字符串完全匹配（包括缩进和空白字符）"
        )

    if count > 1 and not replace_all:
        # 显示上下文帮助定位
        lines = content.split("\n")
        occurrences = []
        for i, line in enumerate(lines):
            if old_string in line:
                context_start = max(0, i - 2)
                context_end = min(len(lines), i + 3)
                ctx = "\n".join(
                    f"  {j+1}: {lines[j]}" for j in range(context_start, context_end)
                )
                occurrences.append(f"--- 第 {i+1} 行 ---\n{ctx}")
                if len(occurrences) >= 5:
                    break

        return ToolResult.failure(
            f"old_string 在文件中出现了 {count} 次，不是唯一的。\n"
            f"请提供更多上下文使匹配唯一，或设置 replace_all=True。\n\n"
            + "\n".join(occurrences)
        )

    new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except (PermissionError, OSError) as e:
        return ToolResult.failure(f"无法写入文件: {e}")

    replaced = count if replace_all else 1
    return ToolResult.success(f"文件已编辑: {file_path}\n替换了 {replaced} 处")
