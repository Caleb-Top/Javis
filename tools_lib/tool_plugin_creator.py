"""Plugin Creator 桥梁 — Codex 插件脚手架技能融入 JARVIS

技能来源: Codex .system/plugin-creator
核心能力: 创建/验证/更新 Codex 插件, 管理 marketplace 条目
"""
import os, sys, json, logging, subprocess, textwrap
from pathlib import Path
from core.tool_result import ToolResult
logger = logging.getLogger("plugin_creator")

_PLUGIN_DIR = Path(__file__).parent.parent / "tools" / "plugin-creator"
_SCRIPTS_DIR = _PLUGIN_DIR / "scripts"
_BRAIN = None

SKILL_META = {
    "plugin-creator": {
        "name": "plugin-creator",
        "title": "Codex 插件创建器",
        "trigger": "需要创建/更新/验证 Codex 插件, 管理 marketplace 条目时",
        "gate": "必须运行 validate_plugin.py 验证后才能交付",
        "order": 0,
        "icon": "🧩",
    }
}


def set_brain(brain):
    global _BRAIN
    _BRAIN = brain


# ── 核心: 插件脚手架 ──

def plugin_scaffold(plugin_name: str, output_dir: str = "",
                    with_skills=False, with_hooks=False, with_scripts=False,
                    with_assets=False, with_mcp=False, with_apps=False,
                    with_marketplace=False, marketplace_path: str = "",
                    marketplace_name: str = "") -> str:
    """运行 create_basic_plugin.py 脚手架"""
    script = _SCRIPTS_DIR / "create_basic_plugin.py"
    if not script.exists():
        return f"[PluginCreator] 脚本不存在: {script}"

    cmd = [sys.executable, str(script), plugin_name]
    if output_dir:
        cmd += ["--path", output_dir]
    if with_skills: cmd.append("--with-skills")
    if with_hooks: cmd.append("--with-hooks")
    if with_scripts: cmd.append("--with-scripts")
    if with_assets: cmd.append("--with-assets")
    if with_mcp: cmd.append("--with-mcp")
    if with_apps: cmd.append("--with-apps")
    if with_marketplace: cmd.append("--with-marketplace")
    if marketplace_path: cmd += ["--marketplace-path", marketplace_path]
    if marketplace_name: cmd += ["--marketplace-name", marketplace_name]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = r.stdout.strip() or r.stderr.strip()
        return f"[PluginCreator] exit={r.returncode}\n{out[:2000]}"
    except Exception as e:
        return f"[PluginCreator] 错误: {e}"


def plugin_validate(plugin_path: str) -> str:
    """验证生成的插件结构"""
    script = _SCRIPTS_DIR / "validate_plugin.py"
    if not script.exists():
        return "[PluginCreator] validate_plugin.py 不存在"

    try:
        r = subprocess.run([sys.executable, str(script), plugin_path],
                          capture_output=True, text=True, timeout=30)
        return (r.stdout.strip() or r.stderr.strip())[:2000]
    except Exception as e:
        return f"[PluginCreator] 验证错误: {e}"


def plugin_update_cachebuster(plugin_path: str, cachebuster: str = "") -> str:
    """更新插件的 cachebuster 用于开发迭代"""
    script = _SCRIPTS_DIR / "update_plugin_cachebuster.py"
    if not script.exists():
        return "[PluginCreator] update_plugin_cachebuster.py 不存在"

    cmd = [sys.executable, str(script), plugin_path]
    if cachebuster:
        cmd += ["--cachebuster", cachebuster]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return (r.stdout.strip() or r.stderr.strip())[:2000]
    except Exception as e:
        return f"[PluginCreator] 更新错误: {e}"


def plugin_read_marketplace_name(marketplace_path: str = "") -> str:
    """读取 marketplace 的名称"""
    script = _SCRIPTS_DIR / "read_marketplace_name.py"
    if not script.exists():
        return "[PluginCreator] read_marketplace_name.py 不存在"

    cmd = [sys.executable, str(script)]
    if marketplace_path:
        cmd += [marketplace_path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (r.stdout.strip() or r.stderr.strip())[:500]
    except Exception as e:
        return f"[PluginCreator] 读取错误: {e}"


def normalize_plugin_name(name: str) -> str:
    """规范化插件名 (与 create_basic_plugin.py 逻辑一致)"""
    import re
    n = name.strip().lower()
    n = re.sub(r'[^a-z0-9]+', '-', n)
    n = n.strip('-')
    n = re.sub(r'-{2,}', '-', n)
    return n


# ── 注入大脑 ──

def inject_to_brain(brain=None):
    global _BRAIN
    if brain: _BRAIN = brain
    if not _BRAIN: return 0

    count = 0
    try:
        _BRAIN.learn_fact(
            "Plugin Creator 技能: 创建/验证/更新 Codex 插件。"
            "用法: plugin_scaffold(name) 脚手架, plugin_validate(path) 验证, "
            "plugin_update_cachebuster(path) 迭代更新。"
            "插件必须包含 .codex-plugin/plugin.json, 名称用 kebab-case。"
            "默认创建到 ~/plugins/<name>/, marketplace 默认为个人。",
            category="plugin_creator.main", source="plugin_creator", priority=3)
        count += 1

        _BRAIN.learn_fact(
            "Plugin Creator 验证规则: 始终运行 validate_plugin.py 后再交付;"
            "manifest 中不能有 [TODO:] 占位符;"
            "外文件夹名与 plugin.json name 必须一致;"
            "marketplace 条目必须有 policy.installation, policy.authentication, category。",
            category="plugin_creator.rules", source="plugin_creator", priority=2)
        count += 1

        _BRAIN.learn_fact(
            "Plugin Creator 开发迭代流程: 修改插件后运行 "
            "scripts/update_plugin_cachebuster.py <plugin-path> 更新缓存标记, "
            "然后重装插件。不要手动编辑 marketplace.json。",
            category="plugin_creator.workflow", source="plugin_creator", priority=2)
        count += 1

        logger.info(f"🧠 Plugin Creator 注入大脑: {count} 条")
    except Exception as e:
        logger.debug(f"注入失败: {e}")
    return count


# ── 工具注册 ──

def tools_for_registry():
    from core.tool_registry import ToolDef
    inject_to_brain()

    return [
        ToolDef("plugin_creator", "创建 Codex 插件脚手架。支持 skills/hooks/scripts/assets/mcp/apps 等可选组件, "
                "以及 marketplace 条目生成。参数: plugin_name(必填, 自动转 kebab-case), "
                "output_dir(输出目录), with_skills/with_hooks/with_scripts/with_assets/with_mcp/with_apps(可选组件), "
                "with_marketplace(生成 marketplace 条目), marketplace_path/marketplace_name",
                {"type": "object", "properties": {
                    "plugin_name": {"type": "string", "description": "插件名 (自动转 kebab-case)"},
                    "output_dir": {"type": "string", "description": "输出目录 (默认 ~/plugins/)"},
                    "with_skills": {"type": "boolean"},
                    "with_hooks": {"type": "boolean"},
                    "with_scripts": {"type": "boolean"},
                    "with_assets": {"type": "boolean"},
                    "with_mcp": {"type": "boolean"},
                    "with_apps": {"type": "boolean"},
                    "with_marketplace": {"type": "boolean"},
                    "marketplace_path": {"type": "string"},
                    "marketplace_name": {"type": "string"},
                }, "required": ["plugin_name"]},
                lambda **kw: ToolResult.success(
                    plugin_scaffold(**{k: v for k, v in kw.items() if v is not None})),
                "plugin_creator"),

        ToolDef("plugin_validate", "验证已生成的 Codex 插件结构是否正确",
                {"type": "object", "properties": {
                    "plugin_path": {"type": "string", "description": "插件目录路径"}},
                 "required": ["plugin_path"]},
                lambda **kw: ToolResult.success(plugin_validate(kw.get("plugin_path", ""))),
                "plugin_creator"),

        ToolDef("plugin_update_cachebuster", "更新本地开发中插件的 cachebuster 以触发 Codex 重载",
                {"type": "object", "properties": {
                    "plugin_path": {"type": "string"},
                    "cachebuster": {"type": "string", "description": "可选自定义标记"}},
                 "required": ["plugin_path"]},
                lambda **kw: ToolResult.success(plugin_update_cachebuster(kw.get("plugin_path", ""), kw.get("cachebuster", ""))),
                "plugin_creator"),

        ToolDef("plugin_read_marketplace_name", "读取 marketplace.json 中的名称",
                {"type": "object", "properties": {
                    "marketplace_path": {"type": "string", "description": "marketplace.json 路径(可选)"}},
                 "required": []},
                lambda **kw: ToolResult.success(plugin_read_marketplace_name(kw.get("marketplace_path", ""))),
                "plugin_creator"),

        ToolDef("plugin_normalize_name", "将插件名转为标准 kebab-case 格式",
                {"type": "object", "properties": {
                    "name": {"type": "string", "description": "原始名称"}},
                 "required": ["name"]},
                lambda **kw: ToolResult.success(normalize_plugin_name(kw.get("name", ""))),
                "plugin_creator"),
    ]


# ── tools_lib/loader.py 兼容 ──
TOOL_NAME = "plugin_creator"
TOOL_DESC = "Codex 插件脚手架 — 创建/验证/更新 Codex 插件及其 marketplace 条目"
TOOL_CATEGORY = "plugin_creator"
TOOL_PARAMS = {"type": "object", "properties": {}, "required": []}

def handler(**kwargs):
    action = kwargs.get("action", "info")
    if action == "info":
        return {"success": True, "output":
            "Plugin Creator 桥梁就绪。可用工具: plugin_creator, plugin_validate, "
            "plugin_update_cachebuster, plugin_read_marketplace_name, plugin_normalize_name"}
    if action == "scaffold":
        return {"success": True, "output": plugin_scaffold(kwargs.get("plugin_name", ""))}
    if action == "validate" and kwargs.get("plugin_path"):
        return {"success": True, "output": plugin_validate(kwargs["plugin_path"])}
    return {"success": True, "output": "用法: action=scaffold|validate|info"}
