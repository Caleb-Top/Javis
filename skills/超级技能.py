"""技能: 超级技能 — Superpowers 14个专业工作流程"""
SKILL_NAME="超级技能";SKILL_ICON="⚡";SKILL_DESC="Superpowers: TDD·调试·审查·计划·并行执行 14个专业工作流程"

def register(reg):
    """注册 Superpowers 专用工具集 + 保留核心系统工具"""
    # ===== 核心系统工具 (始终可用) =====
    try:
        import tools.desktop as d, tools.system as s, tools.file_ops as f
        from tools.manifest import register_desktop, register_window, register_system, register_file
        register_desktop(reg, d); register_window(reg, d)
        register_system(reg, s); register_file(reg, f)
    except Exception as e:
        import logging; logging.getLogger("skills.超级技能").warning(f"核心工具注册跳过: {e}")

    # ===== Superpowers 桥接工具 =====
    try:
        from tools_lib.tool_superpowers import tools_for_registry as sp_tools
        for td in sp_tools():
            reg.register(td)
    except Exception as e:
        import logging; logging.getLogger("skills.超级技能").warning(f"Superpowers工具注册跳过: {e}")

    # ===== 代码执行引擎 =====
    try:
        from tools.code_exec import tools_for_registry as ce_tools
        for td in ce_tools():
            reg.register(td)
    except Exception as e:
        import logging; logging.getLogger("skills.超级技能").warning(f"代码引擎注册跳过: {e}")

    return reg.count
