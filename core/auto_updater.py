"""
P1-5: 自动更新模块 — Javis Auto Updater
通过 GitHub API 检测新版本, 自动拉取更新, 支持静默更新和手动触发
"""
import os
import json
import time
import hashlib
import shutil
import subprocess
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime


@dataclass
class UpdateInfo:
    """更新信息"""
    version: str
    tag_name: str = ""
    description: str = ""
    download_url: str = ""
    published_at: str = ""
    size_bytes: int = 0
    sha256: str = ""
    is_critical: bool = False


class AutoUpdater:
    """Javis 自动更新器 — GitHub API 版本检查 + 增量更新"""

    def __init__(self, repo: str = "Caleb-Top/Javis", branch: str = "main",
                 javis_path: str = ""):
        self.repo = repo
        self.branch = branch
        self.javis_path = Path(javis_path) if javis_path else Path(__file__).parent.parent
        self.current_version = self._read_version()
        self._last_check: float = 0
        self._check_interval: int = 3600  # 1小时
        self._update_history: List[Dict] = []
        self._load_history()

    @property
    def github_token(self) -> str:
        return os.getenv("GITHUB_TOKEN", "")

    # ---------- 版本管理 ----------

    def _read_version(self) -> str:
        """从 VERSION 文件或 git describe 读取当前版本"""
        version_file = self.javis_path / "VERSION"
        if version_file.exists():
            return version_file.read_text().strip()

        try:
            result = subprocess.run(
                ["git", "describe", "--tags", "--always", "--dirty"],
                cwd=self.javis_path, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

        return "0.0.0-dev"

    def _load_history(self):
        """加载更新历史"""
        history_file = self.javis_path / "data" / "update_history.json"
        try:
            if history_file.exists():
                self._update_history = json.loads(history_file.read_text())
        except Exception:
            self._update_history = []

    def _save_history(self):
        """保存更新历史"""
        history_file = self.javis_path / "data" / "update_history.json"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        history_file.write_text(json.dumps(self._update_history, indent=2))

    # ---------- 版本检查 ----------

    def check_for_updates(self) -> Optional[UpdateInfo]:
        """通过 GitHub API 检查新版本"""
        if time.time() - self._last_check < self._check_interval:
            return None

        self._last_check = time.time()

        try:
            url = f"https://api.github.com/repos/{self.repo}/releases/latest"
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "Javis-AutoUpdater/1.0"
            })
            if self.github_token:
                req.add_header("Authorization", f"token {self.github_token}")

            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            latest_version = data.get("tag_name", "").lstrip("v")
            if not latest_version:
                return None

            if self._compare_versions(latest_version, self.current_version) <= 0:
                return None  # 已经是最新

            return UpdateInfo(
                version=latest_version,
                tag_name=data.get("tag_name", ""),
                description=data.get("body", ""),
                download_url=data.get("zipball_url", ""),
                published_at=data.get("published_at", ""),
                size_bytes=data.get("size", 0),
            )
        except urllib.error.HTTPError as e:
            if e.code == 403:
                pass  # Rate limited, skip this check
            return None
        except Exception:
            return None

    @staticmethod
    def _compare_versions(v1: str, v2: str) -> int:
        """比较版本号: returns >0 if v1 newer, <0 if v1 older, 0 if equal"""
        try:
            parts1 = [int(x) for x in v1.replace("-dev", "").split(".") if x.isdigit()]
            parts2 = [int(x) for x in v2.replace("-dev", "").split(".") if x.isdigit()]
            for i in range(max(len(parts1), len(parts2))):
                a = parts1[i] if i < len(parts1) else 0
                b = parts2[i] if i < len(parts2) else 0
                if a != b:
                    return a - b
            return 0
        except Exception:
            return 0

    # ---------- Git 拉取更新 ----------

    def pull_latest(self) -> Tuple[bool, str]:
        """从 GitHub 拉取最新代码 (git pull)"""
        try:
            # 先 stash 本地改动
            subprocess.run(["git", "stash"], cwd=self.javis_path,
                          capture_output=True, timeout=10)

            # 拉取最新
            result = subprocess.run(
                ["git", "pull", "origin", self.branch],
                cwd=self.javis_path, capture_output=True, text=True, timeout=60
            )

            if result.returncode != 0:
                # 恢复 stash
                subprocess.run(["git", "stash", "pop"], cwd=self.javis_path,
                              capture_output=True, timeout=10)
                return False, result.stderr

            # 记录更新历史
            self._update_history.append({
                "version": self._read_version(),
                "timestamp": datetime.now().isoformat(),
                "branch": self.branch,
                "output": result.stdout[:500]
            })
            self.current_version = self._read_version()
            self._save_history()

            return True, result.stdout[:500]

        except subprocess.TimeoutExpired:
            return False, "Git pull timed out"
        except Exception as e:
            return False, str(e)

    # ---------- 文件完整性验证 ----------

    def verify_integrity(self) -> Dict[str, List[str]]:
        """验证关键文件的完整性"""
        critical_files = [
            "core/agent.py", "core/engine.py", "core/llm_client.py",
            "core/planner.py", "core/tool_registry.py",
            "tools/manifest.py", "tools/file_ops.py", "tools/system.py"
        ]

        missing = []
        unchanged = []
        modified = []

        for fpath in critical_files:
            full_path = self.javis_path / fpath
            if not full_path.exists():
                missing.append(fpath)
                continue

            content = full_path.read_bytes()
            file_hash = hashlib.sha256(content).hexdigest()

            # Compare against stored hash
            hash_file = self.javis_path / "data" / "file_hashes.json"
            stored_hashes = {}
            if hash_file.exists():
                stored_hashes = json.loads(hash_file.read_text())

            stored = stored_hashes.get(fpath, "")
            if stored and stored != file_hash:
                modified.append(fpath)
            else:
                unchanged.append(fpath)

        return {"missing": missing, "unchanged": unchanged, "modified": modified}

    # ---------- 回滚 ----------

    def rollback(self) -> Tuple[bool, str]:
        """回滚到上一个 git commit"""
        try:
            result = subprocess.run(
                ["git", "reset", "--hard", "HEAD~1"],
                cwd=self.javis_path, capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0, result.stdout[:500]
        except Exception as e:
            return False, str(e)

    # ---------- 状态查询 ----------

    def get_status(self) -> Dict:
        """获取更新器状态"""
        update = self.check_for_updates()
        return {
            "current_version": self.current_version,
            "repo": self.repo,
            "branch": self.branch,
            "update_available": update is not None,
            "latest_version": update.version if update else self.current_version,
            "last_check": datetime.fromtimestamp(self._last_check).isoformat() if self._last_check else None,
            "history_count": len(self._update_history),
            "integrity": self.verify_integrity(),
        }


# 全局单例
_updater: Optional[AutoUpdater] = None


def get_updater() -> AutoUpdater:
    global _updater
    if _updater is None:
        _updater = AutoUpdater()
    return _updater


def register_in_manifest(reg):
    """Register auto-updater tools"""
    from core.tool_registry import ToolDef
    updater = get_updater()

    async def check_updates(args):
        update = updater.check_for_updates()
        if update:
            return {"success": True, "update_available": True,
                    "current": updater.current_version,
                    "latest": update.version,
                    "description": update.description[:500],
                    "published": update.published_at}
        return {"success": True, "update_available": False,
                "current": updater.current_version}

    async def pull_update(args):
        ok, msg = updater.pull_latest()
        return {"success": ok, "message": msg, "version": updater.current_version}

    async def rollback_update(args):
        ok, msg = updater.rollback()
        return {"success": ok, "message": msg}

    async def updater_status(args):
        return {"success": True, **updater.get_status()}

    async def verify_integrity(args):
        result = updater.verify_integrity()
        return {"success": True, **result}

    reg.register_many([
        ToolDef("update_check", "Check for new versions on GitHub", {"type":"object","properties":{},"required":[]}, check_updates, "update"),
        ToolDef("update_pull", "Pull latest code from GitHub", {"type":"object","properties":{},"required":[]}, pull_update, "update"),
        ToolDef("update_rollback", "Rollback to previous version", {"type":"object","properties":{},"required":[]}, rollback_update, "update"),
        ToolDef("update_status", "Get auto-updater status", {"type":"object","properties":{},"required":[]}, updater_status, "update"),
        ToolDef("update_verify", "Verify file integrity", {"type":"object","properties":{},"required":[]}, verify_integrity, "update"),
    ])
