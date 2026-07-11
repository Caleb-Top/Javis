"""tools_lib 加载器 — 扫描工具目录并注册到 Agent"""

import importlib.util, logging, json
from pathlib import Path
from core.tool_result import ToolResult
from core.tool_registry import ToolDef

logger = logging.getLogger("tools_lib.loader")
_TOOLS_LIB_DIR = Path(__file__).parent


def _wrap_handler(fn):
    """将工具函数包装为返回 ToolResult 的同步函数"""
    def wrapped(**kwargs):
        try:
            result = fn(**kwargs)
            if isinstance(result, ToolResult):
                return result
            if isinstance(result, str):
                # 尝试解析 JSON
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict):
                        success = parsed.get("success", True)
                        msg = parsed.get("message", str(result))
                        out = parsed.get("output", "")
                        if success:
                            return ToolResult.success(f"{msg} 输出: {out}" if out else msg)
                        else:
                            return ToolResult.failure(msg)
                except (json.JSONDecodeError, TypeError):
                    pass
                return ToolResult.success(result)
            return ToolResult.success(str(result))
        except Exception as e:
            return ToolResult.failure(str(e))
    return wrapped


def register(registry) -> int:
    """扫描 tools_lib/ 下所有工具文件并注册到 registry"""
    import tools_lib.tool_creator as tc
    tc._REGISTRY = registry  # 给 tool_creator 提供注册能力

    count = 0
    for f in sorted(_TOOLS_LIB_DIR.glob("tool_*.py")):
        mod_name = f.stem
        try:
            spec = importlib.util.spec_from_file_location(mod_name, f)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            tool_name = getattr(mod, "TOOL_NAME", mod_name.replace("tool_", "").replace("_", "-"))
            tool_desc = getattr(mod, "TOOL_DESC", "")
            tool_category = getattr(mod, "TOOL_CATEGORY", "custom")
            tool_params = getattr(mod, "TOOL_PARAMS", {"type": "object", "properties": {}, "required": []})
            handler_fn = getattr(mod, "handler", None)
            if handler_fn is None:
                logger.warning(f"工具 {mod_name} 缺少 handler, 跳过")
                continue

            registry.register(ToolDef(
                name=tool_name,
                description=tool_desc,
                parameters=tool_params,
                handler=_wrap_handler(handler_fn),
                category=tool_category,
            ))
            count += 1
            logger.info(f"✅ 注册工具: {tool_name} [{tool_category}]")
        except Exception as e:
            logger.error(f"❌ 加载 {mod_name} 失败: {e}")

    # ★ 注册自进化引擎工具 (code_exec 的语言处理器管理) ★
    try:
        from tools.code_exec import tools_for_registry
        for td in tools_for_registry():
            registry.register(td)
            count += 1
            logger.info(f"✅ 注册引擎工具: {td.name} [{td.category}]")
    except Exception as e:
        logger.warning(f"引擎工具注册失败: {e}")

    # ★ 注册 Superpowers 技能桥梁工具 ★
    try:
        from tools_lib.tool_superpowers import tools_for_registry as sp_tools
        for td in sp_tools():
            registry.register(td)
            count += 1
            logger.info(f"✅ 注册超级工具: {td.name} [{td.category}]")
    except Exception as e:
        logger.warning(f"Superpowers 注册失败: {e}")

    # ★ 注册 Plugin Creator 工具 ★
    try:
        from tools_lib.tool_plugin_creator import tools_for_registry as pc_tools
        for td in pc_tools():
            registry.register(td)
            count += 1
            logger.info(f"✅ 注册插件工具: {td.name} [{td.category}]")
    except Exception as e:
        logger.warning(f"PluginCreator 注册失败: {e}")

    try:
        from tools_lib.tool_anthropic_plugins import tools_for_registry as ap_tools
        for td in ap_tools():
            registry.register(td)
            count += 1
            logger.info(f"Anthropic: {td.name} [{td.category}]")
    except Exception as e:
        logger.warning(f"AnthropicPlugins 注册失败: {e}")

    logger.info(f"工具库加载完成: 共 {count} 个工具")
    return count
