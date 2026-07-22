"""
自动更新系统 — GitHub API版本检查 + 自动下载替换 + 回滚
P1-5: Auto-updater with release fetching, asset downloads, and rollback
"""
import json, logging, os, sys, subprocess, shutil, tempfile, hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field
import time

logger = logging.getLogger("updater")

GITHUB_API = "https://api.github.com/repos/Caleb-Top/Javis"
RELEASES_URL = f"{GITHUB_API}/releases/latest"
VERSION_FILE = Path(__file__).parent.parent / "VERSION"
BACKUP_DIR = Path(__file__).parent.parent / "data" / "updates" / "backups"
UPDATE_LOG = Path(__file__).parent.parent / "data" / "updates" / "update_log.json"


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
    """获取当前版本号"""
    if VERSION_FILE.exists():
        ver = VERSION_FILE.read_text().strip()
        if ver:
            return ver
    # 回退: 尝试从 git tag 获取
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip().lstrip("v")
    except Exception:
        pass
    return "0.0.0"


def _version_gt(a: str, b: str) -> bool:
    """版本号比较: a > b"""
    try:
        ap = [int(x) for x in a.lstrip("v").split(".")]
        bp = [int(x) for x in b.lstrip("v").split(".")]
        # 补齐长度
        while len(ap) < len(bp):
            ap.append(0)
        while len(bp) < len(ap):
            bp.append(0)
        return ap > bp
    except (ValueError, AttributeError):
        return False


def check_for_updates(include_prerelease: bool = False) -> Optional[VersionInfo]:
    """检查 GitHub Release 最新版本"""
    import requests
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        # 如果有 token，使用认证请求（更高的 API 速率限制）
        token = os.environ.get("GITHUB_TOKEN", os.environ.get("GH_TOKEN", ""))
        if token:
            headers["Authorization"] = f"token {token}"

        resp = requests.get(RELEASES_URL, headers=headers, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"GitHub API 返回 {resp.status_code}")
            # 尝试获取所有 releases 列表
            resp = requests.get(
                f"{GITHUB_API}/releases?per_page=5",
                headers=headers, timeout=15,
            )
            if resp.status_code != 200 or not resp.json():
                return None
            releases = resp.json()
            if not include_prerelease:
                releases = [r for r in releases if not r.get("prerelease", False)]
            if not releases:
                return None
            release = releases[0]
        else:
            release = resp.json()

        latest = release["tag_name"].lstrip("v")
        current = get_current_version()

        info = VersionInfo(
            current=current,
            latest=latest,
            has_update=_version_gt(latest, current),
            release_url=release.get("html_url", ""),
            published_at=release.get("published_at", ""),
            body=(release.get("body", "") or "")[:500],
        )

        # 查找可下载资产
        assets = release.get("assets", [])
        for asset in assets:
            name = asset.get("name", "")
            if name.endswith(".zip") or name.endswith(".tar.gz"):
                info.download_url = asset.get("browser_download_url", "")
                info.asset_size = asset.get("size", 0)
                break

        return info
    except requests.exceptions.Timeout:
        logger.error("更新检查超时")
        return None
    except requests.exceptions.ConnectionError:
        logger.error("更新检查连接失败")
        return None
    except Exception as e:
        logger.error(f"更新检查异常: {e}")
        return None


def create_backup() -> Optional[str]:
    """创建当前版本的备份"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    project_root = Path(__file__).parent.parent
    current = get_current_version()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_v{current}_{timestamp}.tar.gz"
    backup_path = BACKUP_DIR / backup_name

    try:
        # 备份核心目录（排除 data/, venv/, __pycache__, .git）
        exclude_dirs = ["data", "venv", "__pycache__", ".git", "node_modules",
                       "logs", "output", "tmp", "workspace", "train_output",
                       "ollama_models", "python-embed", "voice"]
        exclude_args = []
        for d in exclude_dirs:
            exclude_args.extend(["--exclude", d])
        exclude_args.extend(["--exclude", "*.pyc", "--exclude", ".git"])

        subprocess.run(
            ["tar", "-czf", str(backup_path)] + exclude_args + ["."],
            cwd=str(project_root), check=True, timeout=60,
        )
        logger.info(f"备份已创建: {backup_path} ({backup_path.stat().st_size} bytes)")
        return str(backup_path)
    except Exception as e:
        logger.error(f"备份失败: {e}")
        return None


def restore_backup(backup_path: str) -> bool:
    """从备份恢复"""
    project_root = Path(__file__).parent.parent
    try:
        subprocess.run(
            ["tar", "-xzf", backup_path, "-C", str(project_root)],
            check=True, timeout=60,
        )
        logger.info(f"已从备份恢复: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"恢复备份失败: {e}")
        return False


def list_backups() -> list[dict]:
    """列出所有备份"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backups = []
    for f in sorted(BACKUP_DIR.glob("backup_*.tar.gz"), reverse=True):
        backups.append({
            "name": f.name,
            "size": f.stat().st_size,
            "created": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
        })
    return backups[:20]


def pull_latest() -> tuple[bool, str]:
    """通过 git pull 更新"""
    project_root = Path(__file__).parent.parent
    try:
        # 先创建备份
        create_backup()

        # Git pull
        r = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=str(project_root), capture_output=True, text=True, timeout=30,
        )
        output = (r.stdout + r.stderr)[:500]
        success = r.returncode == 0

        # 更新 VERSION 文件
        if success:
            _update_version_file()

        # 记录日志
        _log_update("git_pull", success, output)
        return success, output
    except subprocess.TimeoutExpired:
        return False, "Git pull 超时"
    except Exception as e:
        return False, str(e)


def _update_version_file():
    """更新 VERSION 文件"""
    try:
        r = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            VERSION_FILE.write_text(r.stdout.strip().lstrip("v"))
    except Exception:
        pass


def _log_update(method: str, success: bool, detail: str = ""):
    """记录更新日志"""
    UPDATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    logs = []
    if UPDATE_LOG.exists():
        try:
            logs = json.loads(UPDATE_LOG.read_text())
        except Exception:
            logs = []

    logs.append({
        "time": time.time(),
        "date": datetime.now().isoformat(),
        "method": method,
        "success": success,
        "detail": detail[:200],
        "version_after": get_current_version(),
    })

    UPDATE_LOG.write_text(json.dumps(logs[-100:], indent=2, ensure_ascii=False))


def get_update_history() -> list[dict]:
    """获取更新历史"""
    if UPDATE_LOG.exists():
        try:
            logs = json.loads(UPDATE_LOG.read_text())
            return logs[-20:]
        except Exception:
            pass
    return []


# ── 注册到 manifest ──

def register_in_manifest(reg):
    """注册自动更新工具到 manifest"""
    from core.tool_registry import ToolDef
    import asyncio

    async def check_update(args):
        include_prerelease = args.get("include_prerelease", False)
        info = check_for_updates(include_prerelease=include_prerelease)
        if info is None:
            return {"success": False,
                    "error": "无法连接到 GitHub API，请检查网络"}
        return {
            "success": True,
            "has_update": info.has_update,
            "current": info.current,
            "latest": info.latest,
            "release_url": info.release_url,
            "published_at": info.published_at,
            "changelog": info.body[:300] if info.body else "",
            "download_url": info.download_url if info.download_url else "",
        }

    async def update_version(args):
        success, output = pull_latest()
        return {
            "success": success,
            "output": output[:500],
            "current_version": get_current_version(),
        }

    async def current_version(args):
        return {"success": True, "version": get_current_version()}

    async def list_backups_tool(args):
        backups = list_backups()
        return {"success": True, "backups": backups, "count": len(backups)}

    async def create_backup_tool(args):
        path = create_backup()
        if path:
            return {"success": True, "backup_path": path}
        return {"success": False, "error": "创建备份失败"}

    async def update_history(args):
        history = get_update_history()
        return {"success": True, "history": history, "count": len(history)}

    reg.register_many([
        ToolDef("check_update", "检查 GitHub 最新版本",
                {"type":"object","properties":{
                    "include_prerelease":{"type":"boolean","default":False}
                },"required":[]},
                check_update, "update"),
        ToolDef("update_version", "拉取最新版本（git pull + 备份）",
                {"type":"object","properties":{},"required":[]},
                update_version, "update"),
        ToolDef("current_version", "查看当前版本号",
                {"type":"object","properties":{},"required":[]},
                current_version, "update"),
        ToolDef("update_backups", "列出所有可用备份",
                {"type":"object","properties":{},"required":[]},
                list_backups_tool, "update"),
        ToolDef("update_create_backup", "创建当前版本备份",
                {"type":"object","properties":{},"required":[]},
                create_backup_tool, "update"),
        ToolDef("update_history", "查看更新历史",
                {"type":"object","properties":{},"required":[]},
                update_history, "update"),
    ])
