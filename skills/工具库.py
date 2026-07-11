"""技能: 工具库 — 独立工具管理"""
SKILL_NAME="工具库";SKILL_ICON="🧩";SKILL_DESC="独立工具·自创·动态加载"
def register(reg):
    from tools_lib.loader import register as r
    from tools_lib.tool_creator import save_tool,list_custom_tools
    from core.tool_registry import ToolDef
    base_count = r(reg)
    # 注册工具创建器
    reg.register(ToolDef("save_tool","持久化保存新工具到tools_lib/,重启不丢失",{"type":"object","properties":{"name":{"type":"string"},"description":{"type":"string"},"handler_code":{"type":"string"},"category":{"type":"string","default":"general"}},"required":["name","description","handler_code"]},save_tool,"system"))
    reg.register(ToolDef("list_custom_tools","列出所有自创建工具",{"type":"object","properties":{},"required":[]},list_custom_tools,"system"))
    return base_count + 2
