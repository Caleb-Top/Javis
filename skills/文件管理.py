"""技能: 文件管理"""
SKILL_NAME="文件管理";SKILL_ICON="📁";SKILL_DESC="读写·浏览"
def register(reg):
    import tools.desktop as d,tools.system as s,tools.file_ops as f
    from tools.manifest import register_file,register_system
    register_file(reg,f);register_system(reg,s)
    # 加几个桌面工具辅助
    from core.tool_registry import ToolDef
    reg.register(ToolDef("screenshot","截屏",{"type":"object","properties":{},"required":[]},d.screenshot,"desktop"))
    reg.register(ToolDef("mouse_click","鼠标点击",{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"button":{"type":"string","enum":["left","right"]}},"required":["x","y"]},d.mouse_click,"desktop"))
    return reg.count
