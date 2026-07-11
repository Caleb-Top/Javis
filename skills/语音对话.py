"""技能: 语音对话"""
SKILL_NAME="语音对话";SKILL_ICON="🎤";SKILL_DESC="语音识别·合成"
def register(reg):
    import tools.desktop as d,tools.system as s
    from tools.manifest import register_system
    register_system(reg,s)
    from core.tool_registry import ToolDef
    reg.register(ToolDef("screenshot","截屏",{"type":"object","properties":{},"required":[]},d.screenshot,"desktop"))
    reg.register(ToolDef("mouse_click","点击",{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"},"button":{"type":"string","enum":["left","right"]}},"required":["x","y"]},d.mouse_click,"desktop"))
    return reg.count
