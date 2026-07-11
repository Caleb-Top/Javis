"""技能: 桌面控制"""
SKILL_NAME="桌面控制";SKILL_ICON="🖥️";SKILL_DESC="截图·鼠标·键盘·窗口"
def register(reg):
    import tools.desktop as d,tools.system as s
    from tools.manifest import register_desktop,register_window,register_system
    register_desktop(reg,d);register_window(reg,d);register_system(reg,s)
    return reg.count
