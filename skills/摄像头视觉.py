"""技能: 摄像头视觉"""
SKILL_NAME="摄像头视觉";SKILL_ICON="📷";SKILL_DESC="拍照·检测"
def register(reg):
    import tools.desktop as d,tools.system as s
    from tools.manifest import register_camera,register_system
    import tools.camera as c
    register_camera(reg,c);register_system(reg,s)
    from core.tool_registry import ToolDef
    reg.register(ToolDef("screenshot","截屏",{"type":"object","properties":{},"required":[]},d.screenshot,"desktop"))
    return reg.count
