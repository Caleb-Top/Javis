"""Anthropic 官方插件库桥梁 - 43 插件融合 JARVIS 大脑"""

import os, json, logging
from pathlib import Path
from core.tool_result import ToolResult
logger = logging.getLogger("anthropic_plugins")

_PLUGINS_DIR = Path(__file__).parent.parent / "tools" / "anthropic-plugins"
_BRAIN = None

PLUGIN_META = {
        "agent-sdk-dev": {"name":"agent-sdk-dev","type":"internal","desc":"Agent SDK Development Plugin - creating and verifying Claude Agent SDK applications","downloaded":True},
        "asana": {"name":"asana","type":"external","desc":"Asana plugin - no README found","downloaded":False},
        "clangd-lsp": {"name":"clangd-lsp","type":"internal","desc":"C/C++ language server (clangd) for Claude Code - code intelligence, diagnostics, formatting","downloaded":True},
        "claude-code-setup": {"name":"claude-code-setup","type":"internal","desc":"Claude Code Setup Plugin - analyze codebases and recommend tailored automations","downloaded":True},
        "claude-md-management": {"name":"claude-md-management","type":"internal","desc":"CLAUDE.md Management Plugin - tools to maintain and improve CLAUDE.md files","downloaded":True},
        "code-modernization": {"name":"code-modernization","type":"internal","desc":"Code Modernization Plugin - modernize legacy codebases with architecture maps and test harnesses","downloaded":True},
        "commit-commands": {"name":"commit-commands","type":"internal","desc":"Commit Commands Plugin - streamline git workflow for committing, pushing, and PRs","downloaded":True},
        "context7": {"name":"context7","type":"external","desc":"Context7 plugin - no README found","downloaded":False},
        "csharp-lsp": {"name":"csharp-lsp","type":"internal","desc":"C# language server for Claude Code - code intelligence and diagnostics","downloaded":True},
        "cwc-makers": {"name":"cwc-makers","type":"internal","desc":"Code-with-Claude Makers Cardputer kit onboarding","downloaded":True},
        "discord": {"name":"discord","type":"external","desc":"Discord bot MCP server - connect a Discord bot to Claude Code","downloaded":True},
        "explanatory-output-style": {"name":"explanatory-output-style","type":"internal","desc":"Explanatory Output Style Plugin - recreates deprecated Explanatory output style","downloaded":True},
        "fakechat": {"name":"fakechat","type":"external","desc":"Fake chat UI for testing channel contracts without an external service","downloaded":True},
        "feature-dev": {"name":"feature-dev","type":"internal","desc":"Feature Development Plugin - structured workflow with specialized agents","downloaded":True},
        "firebase": {"name":"firebase","type":"external","desc":"Firebase plugin - no README found","downloaded":False},
        "github": {"name":"github","type":"external","desc":"GitHub plugin - no README found","downloaded":False},
        "gitlab": {"name":"gitlab","type":"external","desc":"GitLab plugin - no README found","downloaded":False},
        "gopls-lsp": {"name":"gopls-lsp","type":"internal","desc":"Go language server (gopls) for Claude Code - code intelligence, refactoring, analysis","downloaded":True},
        "greptile": {"name":"greptile","type":"external","desc":"Greptile AI code review agent - connect to Greptile account from terminal","downloaded":True},
        "hookify": {"name":"hookify","type":"internal","desc":"Hookify Plugin - create custom hooks to prevent unwanted behaviors","downloaded":True},
        "imessage": {"name":"imessage","type":"external","desc":"iMessage integration - read/write iMessages via Claude Code (macOS only)","downloaded":True},
        "jdtls-lsp": {"name":"jdtls-lsp","type":"internal","desc":"Java language server (Eclipse JDT.LS) for Claude Code - code intelligence and refactoring","downloaded":True},
        "kotlin-lsp": {"name":"kotlin-lsp","type":"internal","desc":"Kotlin language server for Claude Code - code intelligence, refactoring, analysis","downloaded":True},
        "laravel-boost": {"name":"laravel-boost","type":"external","desc":"Laravel Boost plugin - no README found","downloaded":False},
        "learning-output-style": {"name":"learning-output-style","type":"internal","desc":"Learning Style Plugin - combines Learning output style with explanatory functionality","downloaded":True},
        "linear": {"name":"linear","type":"external","desc":"Linear plugin - no README found","downloaded":False},
        "lua-lsp": {"name":"lua-lsp","type":"internal","desc":"Lua language server for Claude Code - code intelligence and diagnostics","downloaded":True},
        "math-olympiad": {"name":"math-olympiad","type":"internal","desc":"Competition math solver with adversarial verification","downloaded":True},
        "mcp-server-dev": {"name":"mcp-server-dev","type":"internal","desc":"MCP server development - skills for designing and building MCP servers","downloaded":True},
        "mcp-tunnels": {"name":"mcp-tunnels","type":"internal","desc":"Connect Claude to MCP servers in private networks via Anthropic MCP tunnels","downloaded":True},
        "php-lsp": {"name":"php-lsp","type":"internal","desc":"PHP language server (Intelephense) for Claude Code - code intelligence and diagnostics","downloaded":True},
        "playwright": {"name":"playwright","type":"external","desc":"Playwright plugin - no README found","downloaded":False},
        "pr-review-toolkit": {"name":"pr-review-toolkit","type":"internal","desc":"PR Review Toolkit - specialized agents for thorough pull request review","downloaded":True},
        "pyright-lsp": {"name":"pyright-lsp","type":"internal","desc":"Python language server (Pyright) for Claude Code - static type checking and code intelligence","downloaded":True},
        "ralph-loop": {"name":"ralph-loop","type":"internal","desc":"Ralph Loop Plugin - iterative, self-referential AI development loops","downloaded":True},
        "ruby-lsp": {"name":"ruby-lsp","type":"internal","desc":"Ruby language server for Claude Code - code intelligence and analysis","downloaded":True},
        "rust-analyzer-lsp": {"name":"rust-analyzer-lsp","type":"internal","desc":"Rust language server for Claude Code - code intelligence and analysis","downloaded":True},
        "security-guidance": {"name":"security-guidance","type":"internal","desc":"Security review for Claude-generated code - pattern warnings and review guidance","downloaded":True},
        "serena": {"name":"serena","type":"external","desc":"Serena plugin - stub only (404: Not Found)","downloaded":True},
        "swift-lsp": {"name":"swift-lsp","type":"internal","desc":"Swift language server (SourceKit-LSP) for Claude Code - code intelligence for Swift","downloaded":True},
        "telegram": {"name":"telegram","type":"external","desc":"Telegram bot MCP server - connect a Telegram bot to Claude Code","downloaded":True},
        "terraform": {"name":"terraform","type":"external","desc":"Terraform plugin - stub only (404: Not Found)","downloaded":True},
        "typescript-lsp": {"name":"typescript-lsp","type":"internal","desc":"TypeScript/JavaScript language server for Claude Code - code intelligence features","downloaded":True}
}

PLUGIN_BY_CATEGORY = {
    "lsp": ["clangd-lsp", "csharp-lsp", "gopls-lsp", "jdtls-lsp", "kotlin-lsp", "lua-lsp", "php-lsp", "pyright-lsp", "ruby-lsp", "rust-analyzer-lsp", "swift-lsp", "typescript-lsp"],
    "workflow": ["agent-sdk-dev", "claude-code-setup", "claude-md-management", "code-modernization", "commit-commands", "cwc-makers", "explanatory-output-style", "feature-dev", "hookify", "learning-output-style", "math-olympiad", "mcp-server-dev", "mcp-tunnels", "pr-review-toolkit", "ralph-loop", "security-guidance"],
    "external": ["asana", "context7", "discord", "fakechat", "firebase", "github", "gitlab", "greptile", "imessage", "laravel-boost", "linear", "playwright", "serena", "telegram", "terraform"],
}

def set_brain(brain):
    global _BRAIN
    _BRAIN = brain

def get_plugin_guide(name):
    for td in ["internal", "external"]:
        for fn in ["SKILL.md", "README.md"]:
            p = _PLUGINS_DIR / td / name / fn
            if p.exists() and p.stat().st_size > 50:
                content = p.read_text(encoding="utf-8")
                return "[Anthropic Plugin] " + name + "\n\n" + content[:5000]
    meta = PLUGIN_META.get(name, {})
    return ("[Anthropic Plugin] " + name + " (" + meta.get("type","?") + ")\n"
            "用途: " + meta.get("desc","未知") + "\n"
            "本地内容: 未下载\n"
            "原始来源: https://github.com/anthropics/claude-plugins-official")

def list_plugins(category=""):
    if category == "lsp": names = PLUGIN_BY_CATEGORY["lsp"]
    elif category == "workflow": names = PLUGIN_BY_CATEGORY["workflow"]
    elif category == "external": names = PLUGIN_BY_CATEGORY["external"]
    else: names = sorted(PLUGIN_META.keys())
    lines = ["Anthropic 插件库: " + str(len(PLUGIN_META)) + " 个"]
    for n in sorted(names):
        m = PLUGIN_META.get(n, {})
        flag = "downloaded" if m.get("downloaded") else "cloud"
        t = m.get("type", "?")[:3]
        lines.append("  [" + flag + "] [" + t + "] " + n + ": " + m.get("desc","")[:80])
    return "\n".join(lines)

def inject_to_brain(brain=None):
    global _BRAIN
    if brain: _BRAIN = brain
    if not _BRAIN: return 0
    count = 0
    for name, meta in PLUGIN_META.items():
        try:
            _BRAIN.learn_fact(
                "Anthropic plugin [" + name + "]: " + meta.get("desc","")[:120],
                category="anthropic_plugins." + meta.get("type","general") + "." + name,
                source="anthropic", priority=2)
            count += 1
        except: pass
    lsp_c = len(PLUGIN_BY_CATEGORY["lsp"])
    wf_c = len(PLUGIN_BY_CATEGORY["workflow"])
    ext_c = len(PLUGIN_BY_CATEGORY["external"])
    _BRAIN.learn_fact(
        "Anthropic plugin routing: " + str(lsp_c) + " LSP plugins for code intelligence, "
        + str(wf_c) + " workflow plugins for process, " + str(ext_c) + " external for services. "
        "Use anthropic_plugins_list(category) to browse. "
        "annotations: download for list of plugins, guide for details.",
        category="anthropic_plugins.routing", source="anthropic", priority=5)
    count += 1
    logger.info("Anthropic plugins injected: " + str(count) + " facts")
    return count

def tools_for_registry():
    from core.tool_registry import ToolDef
    inject_to_brain()
    return [
        ToolDef("anthropic_plugins_list",
                "List Anthropic official plugins, category=lsp/workflow/external",
                {"type":"object","properties":{"category":{"type":"string"}},"required":[]},
                lambda **kw: ToolResult.success(list_plugins(kw.get("category",""))), "anthropic"),
        ToolDef("anthropic_plugin_guide",
                "Get full guide for an Anthropic plugin",
                {"type":"object","properties":{"name":{"type":"string","description":"plugin name"}},"required":["name"]},
                lambda **kw: ToolResult.success(get_plugin_guide(kw.get("name",""))), "anthropic"),
    ]

TOOL_NAME = "anthropic_plugins"
TOOL_DESC = "Anthropic official plugin bridge"
TOOL_CATEGORY = "anthropic"
TOOL_PARAMS = {"type":"object","properties":{},"required":[]}

def handler(**kwargs):
    a = kwargs.get("action", "list")
    if a == "list": return {"success":True,"output":list_plugins(kwargs.get("category",""))}
    if a == "guide": return {"success":True,"output":get_plugin_guide(kwargs.get("name",""))}
    return {"success":True,"output":"Anthropic bridge ready. " + str(len(PLUGIN_META)) + " plugins"}
