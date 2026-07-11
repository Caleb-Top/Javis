"""工具创建器 — 保存/列出自定义工具"""

import textwrap, importlib.util, logging
from pathlib import Path
from core.tool_result import ToolResult
from core.tool_registry import ToolDef

logger = logging.getLogger("tools_lib.tool_creator")
_TOOLS_LIB_DIR = Path(__file__).parent
_REGISTRY = None  # 由 loader 设置


def save_tool(name: str, description: str, handler_code: str, category: str = "custom") -> str:
    """保存新工具到 tools_lib/ 并注册到当前会话"""
    safe = name.replace(" ", "_").replace("-", "_")
    path = _TOOLS_LIB_DIR / f"tool_{safe}.py"

    content = f'"""Javis自创: {name}"""\n'
    content += f'TOOL_NAME="{name}"\n'
    content += f'TOOL_DESC="{description}"\n'
    content += f'TOOL_CATEGORY="{category}"\n'
    content += f'TOOL_PARAMS={{"type":"object","properties":{{}},"required":[]}}\n\n'
    content += f'def handler(**kwargs):\n'
    content += textwrap.indent(textwrap.dedent(handler_code.strip()), "    ") + "\n"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        # 验证语法
        compile(content, path.name, "exec")

        # 立即注册到内存
        try:
            spec = importlib.util.spec_from_file_location(f"tool_{safe}", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if _REGISTRY:
                    from tools_lib.loader import _wrap_handler
                    handler_fn = getattr(mod, "handler", None)
                    if handler_fn:
                        _REGISTRY.register(ToolDef(
                            name=name,
                            description=description,
                            parameters={"type": "object", "properties": {}, "required": []},
                            handler=_wrap_handler(handler_fn),
                            category=category,
                        ))
        except Exception as e:
            logger.debug(f"内存注册失败: {e}")

        return f"✅ 工具 '{name}' 已保存到 tools_lib/tool_{safe}.py"
    except SyntaxError as e:
        return f"❌ 语法错误: {e}"
    except Exception as e:
        return f"❌ 保存失败: {e}"


def list_custom_tools() -> str:
    """列出 tools_lib/ 中所有自定义工具"""
    files = sorted(_TOOLS_LIB_DIR.glob("tool_*.py"))
    if not files:
        return "📭 没有自定义工具"
    lines = [f"📦 工具库 ({len(files)} 个工具):"]
    for f in files:
        size = f.stat().st_size
        name = f.stem.replace("tool_", "").replace("_", "-")
        lines.append(f"  🛠 {name} ({size}B)")
    return "\n".join(lines)
