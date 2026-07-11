"""技能: 全功能"""
SKILL_NAME="全功能";SKILL_ICON="⚡";SKILL_DESC="所有工具完整可用"
def register(reg):
    import tools.desktop as d,tools.system as s,tools.file_ops as f,tools.camera as c,tools.code_exec as ce,tools.workspace as w
    from tools.manifest import register_desktop,register_window,register_system,register_file,register_camera,register_code_exec,register_workspace
    register_desktop(reg,d);register_window(reg,d);register_system(reg,s)
    register_file(reg,f);register_camera(reg,c);register_code_exec(reg,ce)
    register_workspace(reg,w)
    # 自动加载 tools_lib/ 中的自定义工具（如文件格式转换工具）
    try:
        from tools_lib.loader import register as register_tools_lib
        register_tools_lib(reg)
    except Exception as e:
        import logging
        logging.getLogger("skills.全功能").debug(f"tools_lib 加载跳过: {e}")
    return reg.count
