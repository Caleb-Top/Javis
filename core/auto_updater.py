"""
自动更新系统 — GitHub API版本检查 + 自动下载替换
"""
import json, logging, os, sys, subprocess
from pathlib import Path

logger = logging.getLogger("updater")

UPGRADE_URL = "https://api.github.com/repos/Caleb-Top/Javis/releases/latest"
CURRENT_VERSION_FILE = Path(__file__).parent.parent / "VERSION"

def get_current_version() -> str:
    if CURRENT_VERSION_FILE.exists():
        return CURRENT_VERSION_FILE.read_text().strip()
    return "0.0.0"

def check_for_updates() -> dict | None:
    import requests
    try:
        r = requests.get(UPGRADE_URL, timeout=10)
        if r.status_code != 200:
            return None
        release = r.json()
        latest = release["tag_name"].lstrip("v")
        current = get_current_version()
        if _version_gt(latest, current):
            return {
                "has_update": True,
                "current": current,
                "latest": latest,
                "download_url": release.get("assets", [{}])[0].get("browser_download_url", ""),
                "body": release.get("body", "")[:200]
            }
        return {"has_update": False, "current": current, "latest": latest}
    except Exception as e:
        logger.error(f"更新检查失败: {e}")
        return None

def _version_gt(a: str, b: str) -> bool:
    try:
        ap = [int(x) for x in a.split(".")]
        bp = [int(x) for x in b.split(".")]
        return ap > bp
    except:
        return False


def register_in_manifest(reg):
    """Register auto updater tools in manifest"""
    from core.tool_registry import ToolDef
    import asyncio

    async def check_update(args):
        result = check_for_updates()
        if result is None:
            return {"success": False, "error": "无法检查更新"}
        return {"success": True, **result}

    async def update_version(args):
        result = check_for_updates()
        if result is None or not result.get("has_update"):
            return {"success": False, "error": "没有可用更新"}
        try:
            # Git pull approach for auto update
            import subprocess
            project_root = Path(__file__).parent.parent
            r = subprocess.run(
                ["git", "pull", "origin", "main"],
                cwd=str(project_root), capture_output=True, text=True, timeout=30
            )
            return {
                "success": r.returncode == 0,
                "output": (r.stdout + r.stderr)[:500],
                "current": get_current_version(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def current_version(args):
        return {"success": True, "version": get_current_version()}

    reg.register_many([
        ToolDef("check_update", "Check GitHub releases for new versions",
                {"type":"object","properties":{},"required":[]}, check_update, "update"),
        ToolDef("update_version", "Pull latest release from GitHub",
                {"type":"object","properties":{},"required":[]}, update_version, "update"),
        ToolDef("current_version", "Show current Javis version",
                {"type":"object","properties":{},"required":[]}, current_version, "update"),
    ])
