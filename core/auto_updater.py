"""Read-only release discovery and legacy update compatibility boundary."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("updater")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GITHUB_API = "https://api.github.com/repos/Caleb-Top/Javis"
RELEASES_URL = f"{GITHUB_API}/releases/latest"
RELEASE_MANIFEST = PROJECT_ROOT / "app" / "release.manifest.json"
BACKUP_DIR = PROJECT_ROOT / "data" / "updates" / "backups"
UPDATE_LOG = PROJECT_ROOT / "data" / "updates" / "update_log.json"


@dataclass
class VersionInfo:
    current: str
    latest: str = ""
    has_update: bool = False
    release_url: str = ""
    published_at: str = ""
    body: str = ""
    download_url: str = ""
    asset_size: int = 0


def get_current_version() -> str:
    """Read the installed version from the native release contract."""
    try:
        payload = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return "0.0.0"
    version = payload.get("version")
    return version.strip().lstrip("v") if isinstance(version, str) and version.strip() else "0.0.0"


def _version_gt(a: str, b: str) -> bool:
    try:
        left = [int(value) for value in a.lstrip("v").split(".")]
        right = [int(value) for value in b.lstrip("v").split(".")]
    except (AttributeError, ValueError):
        return False
    width = max(len(left), len(right))
    return left + [0] * (width - len(left)) > right + [0] * (width - len(right))


def check_for_updates(include_prerelease: bool = False) -> Optional[VersionInfo]:
    """Discover release metadata without changing the installed application."""
    import requests

    headers = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN", os.environ.get("GH_TOKEN", ""))
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        response = requests.get(RELEASES_URL, headers=headers, timeout=15)
        if response.status_code == 200:
            release = response.json()
        else:
            response = requests.get(
                f"{GITHUB_API}/releases?per_page=5",
                headers=headers,
                timeout=15,
            )
            if response.status_code != 200:
                return None
            releases = response.json()
            if not include_prerelease:
                releases = [item for item in releases if not item.get("prerelease", False)]
            if not releases:
                return None
            release = releases[0]

        latest = str(release.get("tag_name", "")).lstrip("v")
        info = VersionInfo(
            current=get_current_version(),
            latest=latest,
            release_url=str(release.get("html_url", "")),
            published_at=str(release.get("published_at", "")),
            body=str(release.get("body", "") or "")[:500],
        )
        info.has_update = _version_gt(info.latest, info.current)
        for asset in release.get("assets", []):
            name = str(asset.get("name", ""))
            if name.endswith((".zip", ".tar.gz")):
                info.download_url = str(asset.get("browser_download_url", ""))
                info.asset_size = int(asset.get("size", 0) or 0)
                break
        return info
    except requests.exceptions.RequestException as exc:
        logger.warning("Release discovery failed: %s", exc)
        return None
    except (TypeError, ValueError, KeyError) as exc:
        logger.warning("Invalid release metadata: %s", exc)
        return None


def _native_continuity_required(action: str) -> dict:
    return {
        "success": False,
        "code": "native_continuity_required",
        "error": "native_continuity_required",
        "action": action,
    }


def create_backup() -> dict:
    """Reject legacy source-tree backups; the native installer owns continuity."""
    return _native_continuity_required("create_backup")


def restore_backup(backup_path: str) -> dict:
    """Reject in-place source restoration regardless of archive contents."""
    return _native_continuity_required("restore_backup")


def pull_latest() -> dict:
    """Reject legacy in-place source updates."""
    return _native_continuity_required("pull_latest")


def list_backups() -> list[dict]:
    """List historical legacy artifacts without creating directories."""
    if not BACKUP_DIR.is_dir():
        return []
    return [
        {
            "name": path.name,
            "size": path.stat().st_size,
            "created": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        }
        for path in sorted(BACKUP_DIR.glob("backup_*.zip"), reverse=True)[:20]
        if path.is_file()
    ]


def get_update_history() -> list[dict]:
    if not UPDATE_LOG.is_file():
        return []
    try:
        payload = json.loads(UPDATE_LOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    return payload[-20:] if isinstance(payload, list) else []


def register_in_manifest(reg) -> None:
    """Register release inspection only; mutation belongs to the native shell."""
    from core.tool_registry import ToolDef

    async def check_update(include_prerelease: bool = False):
        info = check_for_updates(include_prerelease=include_prerelease)
        if info is None:
            return {"success": False, "error": "release_discovery_unavailable"}
        return {
            "success": True,
            "has_update": info.has_update,
            "current": info.current,
            "latest": info.latest,
            "release_url": info.release_url,
            "published_at": info.published_at,
            "changelog": info.body[:300],
            "download_url": info.download_url,
        }

    async def current_version():
        return {"success": True, "version": get_current_version()}

    async def update_history():
        history = get_update_history()
        return {"success": True, "history": history, "count": len(history)}

    reg.register_many([
        ToolDef(
            "check_update",
            "Check the published Javis release metadata",
            {
                "type": "object",
                "properties": {"include_prerelease": {"type": "boolean", "default": False}},
                "required": [],
            },
            check_update,
            "update",
        ),
        ToolDef(
            "current_version",
            "Read the installed release version",
            {"type": "object", "properties": {}, "required": []},
            current_version,
            "update",
        ),
        ToolDef(
            "update_history",
            "Read historical native update receipts",
            {"type": "object", "properties": {}, "required": []},
            update_history,
            "update",
        ),
    ])
