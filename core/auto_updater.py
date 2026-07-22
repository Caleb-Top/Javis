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
