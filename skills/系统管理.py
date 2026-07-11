"""技能: 系统管理"""
SKILL_NAME="系统管理";SKILL_ICON="⚙️";SKILL_DESC="系统·命令·应用"
def register(reg):
    import tools.system as s
    from tools.manifest import register_system
    register_system(reg,s);return reg.count
