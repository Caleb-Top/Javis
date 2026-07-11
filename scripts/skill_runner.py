"""技能子进程运行器 — 每个技能编译为 .exe 独立运行

协议 (JSON over stdin/stdout):
  输入: {"action": "register"} → 输出技能信息
  输入: {"action": "execute", "tool": "name", "params": {...}} → 执行并输出结果

用法: skill_全功能.exe           # 注册模式
      skill_全功能.exe execute 工具名 '{"参数":"值"}'   # 执行模式
"""

import sys, json, importlib, os

# 添加项目根目录到路径
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

SKILL_MODULE = ""  # 被 build.py 替换


def _get_skill_mod():
    """动态加载技能模块"""
    return importlib.import_module(f"skills.{SKILL_MODULE}")


def cmd_register():
    """输出技能注册信息 (ToolDef schemas)"""
    from core.tool_registry import ToolRegistry
    mod = _get_skill_mod()
    registry = ToolRegistry()
    count = mod.register(registry)

    tools = []
    for name, td in registry._tools.items():
        tools.append({
            "name": td.name,
            "description": td.description,
            "parameters": td.parameters,
            "category": td.category,
        })

    output = {
        "status": "ok",
        "skill": {
            "id": SKILL_MODULE,
            "name": getattr(mod, "SKILL_NAME", SKILL_MODULE),
            "icon": getattr(mod, "SKILL_ICON", "🔧"),
            "desc": getattr(mod, "SKILL_DESC", ""),
        },
        "count": count,
        "tools": tools,
    }
    print(json.dumps(output, ensure_ascii=False))


def cmd_execute(tool_name: str, params: dict):
    """执行单个工具并输出结果"""
    from core.tool_registry import ToolRegistry
    from core.tool_result import ToolResult
    import asyncio

    mod = _get_skill_mod()
    registry = ToolRegistry()
    mod.register(registry)

    td = registry.get(tool_name)
    if not td:
        result = ToolResult.failure(f"未知工具: {tool_name}")
    else:
        try:
            r = td.handler(**params)
            if asyncio.iscoroutine(r):
                r = asyncio.run(r)
            if not isinstance(r, ToolResult):
                r = ToolResult.success(str(r))
            result = r
        except Exception as e:
            result = ToolResult.failure(str(e))

    output = {
        "status": "ok",
        "result": {
            "success": result.success,
            "data": (result.data or "")[:2000],
            "error": result.error[:500] if result.error else "",
            "image": result.image[:100] if result.image else "",
        }
    }
    print(json.dumps(output, ensure_ascii=False))


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "execute" and len(sys.argv) >= 4:
        tool_name = sys.argv[2]
        try:
            params = json.loads(sys.argv[3])
        except json.JSONDecodeError:
            params = {}
        cmd_execute(tool_name, params)
    elif len(sys.argv) >= 2 and sys.argv[1] == "register":
        cmd_register()
    else:
        # 无参数默认注册模式
        cmd_register()


if __name__ == "__main__":
    main()
