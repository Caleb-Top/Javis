"""Audited command task execution for the Javis control layer."""

from __future__ import annotations

import re
import base64
import hashlib
import os
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.events import EventBus
from core.subsystem import SubsystemStatus


_LEVEL_RANK = {
    "safe": 0,
    "full_approval": 0,
    "low": 0,
    "medium": 1,
    "safe_guard": 1,
    "trusted": 1,
    "dangerous": 1,
    "quick_auth": 1,
    "root": 2,
    "critical": 2,
    "full_access": 2,
}

_DANGEROUS_PATTERNS = [
    r"(?:^|\s)remove-item(?:\s|$).*-recurse",
    r"\brm\b\s+(-[a-z]*r[a-z]*f|-rf|-fr)\b",
    r"\bdel\b.*\s/(s|q)\b",
    r"\brmdir\b.*\s/s\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\breg\b\s+delete\b",
    r"\bdiskpart\b",
    r"\bbcdedit\b",
    r"\btaskkill\b.*\s/f\b",
]

_TRUSTED_PATTERNS = [
    r"\bpip\b\s+install\b",
    r"\bpython\b.*\s-m\s+pip\s+install\b",
    r"\bnpm\b\s+install\b",
    r"\bpnpm\b\s+install\b",
    r"\byarn\b\s+add\b",
    r"\bwinget\b\s+install\b",
    r"\bchoco\b\s+install\b",
    r"\bgit\b\s+(commit|push|pull|fetch|merge|rebase|reset|clean)\b",
]

_SAFE_PREFIXES = (
    "dir",
    "echo",
    "type",
    "where",
    "whoami",
    "cd",
    "pwd",
    "git status",
    "git diff",
    "git log",
    "python --version",
    "py --version",
)

_ROLLBACK_SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", "tools"}
_ROLLBACK_STORE_DIRNAME = ".javis_rollback_points"
_ROLLBACK_MAX_FILES = 500
_ROLLBACK_MAX_FILE_BYTES = 1_000_000


@dataclass
class CommandTask:
    task_id: str
    command: str
    shell: str
    cwd: str
    timeout: int
    risk: str
    status: str = "created"
    stdout: str = ""
    stderr: str = ""
    output: str = ""
    exit_code: int | None = None
    block_reason: str = ""
    rollback_point: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "command": self.command,
            "shell": self.shell,
            "cwd": self.cwd,
            "timeout": self.timeout,
            "risk": self.risk,
            "status": self.status,
            "exit_code": self.exit_code,
            "block_reason": self.block_reason,
            "rollback_point": self._public_rollback_point(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
        }

    def _public_rollback_point(self) -> dict[str, Any]:
        if not self.rollback_point:
            return {}
        files = self.rollback_point.get("files", [])
        return {
            "id": self.rollback_point.get("id", ""),
            "kind": self.rollback_point.get("kind", ""),
            "task_id": self.rollback_point.get("task_id", self.task_id),
            "created_at": self.rollback_point.get("created_at"),
            "file_count": self.rollback_point.get("file_count", len(files)),
            "captured_text_files": len([item for item in files if "content" in item]),
            "captured_binary_files": len([item for item in files if "content_b64" in item]),
            "captured_external_files": len([item for item in files if "external_path" in item]),
            "skipped_files": len([
                item
                for item in files
                if "content" not in item and "content_b64" not in item and "external_path" not in item
            ]),
        }

    @property
    def duration_ms(self) -> float | None:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at) * 1000, 1)


class CommandTaskRunner:
    """Runs terminal commands as audited tasks with fail-closed risk checks."""

    name = "control"

    def __init__(
        self,
        root: str | Path,
        event_bus: EventBus | None = None,
        guard: Any | None = None,
        permission_level: str | None = None,
        max_history: int = 200,
    ):
        self.root = Path(root).resolve()
        self.event_bus = event_bus or EventBus()
        self.guard = guard
        self.permission_level = permission_level
        self._tasks: list[CommandTask] = []
        self._max_history = max(10, int(max_history))
        self._root_tokens: dict[str, dict[str, Any]] = {}
        self._rollback_points: dict[str, dict[str, Any]] = {}
        self._fuse_reason = ""
        self._active_processes: dict[str, subprocess.Popen[str]] = {}
        self._cancelled_tasks: set[str] = set()
        self._process_lock = threading.RLock()

    def start(self, runtime: Any) -> None:
        self.event_bus = runtime.event_bus
        self.guard = runtime.registry.guard

    def status(self) -> SubsystemStatus:
        blocked = len([task for task in self._tasks if task.status == "blocked"])
        failed = len([task for task in self._tasks if task.status == "failed"])
        root_sessions = len([item for item in self._root_tokens.values() if item["expires_at"] > time.time()])
        return SubsystemStatus(
            state="stopped" if self._fuse_reason else "running",
            detail=f"{len(self._tasks)} command tasks, {blocked} blocked, {failed} failed",
            metrics={
                "tasks": len(self._tasks),
                "blocked": blocked,
                "failed": failed,
                "root_sessions": root_sessions,
                "fuse_tripped": bool(self._fuse_reason),
            },
        )

    def issue_root_token(self, reason: str = "", ttl_sec: int = 300) -> dict[str, Any]:
        ttl_sec = max(30, min(int(ttl_sec or 300), 3600))
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_token(token)
        expires_at = time.time() + ttl_sec
        self._root_tokens[token_hash] = {
            "reason": str(reason or "local root session"),
            "expires_at": expires_at,
            "issued_at": time.time(),
        }
        self.event_bus.publish(
            "control.root_token.issued",
            {"reason": reason or "local root session", "ttl_sec": ttl_sec, "expires_at": expires_at},
            source="control",
        )
        return {"ok": True, "token": token, "expires_at": expires_at, "ttl_sec": ttl_sec}

    def trip_fuse(self, reason: str = "manual fuse tripped") -> dict[str, Any]:
        self._fuse_reason = str(reason or "manual fuse tripped")
        self.event_bus.publish("control.fuse.tripped", {"reason": self._fuse_reason}, source="control")
        return {"ok": True, "fuse_tripped": True, "reason": self._fuse_reason}

    def reset_fuse(self) -> dict[str, Any]:
        previous = self._fuse_reason
        self._fuse_reason = ""
        self.event_bus.publish("control.fuse.reset", {"previous_reason": previous}, source="control")
        return {"ok": True, "fuse_tripped": False}

    def execute(
        self,
        command: str,
        shell: str = "cmd",
        timeout: int = 15,
        cwd: str | Path | None = None,
        root_token: str = "",
    ) -> dict[str, Any]:
        command = str(command or "").strip()
        if not command:
            return {"ok": False, "output": "command is required", "exit_code": -1}

        shell = self._normalize_shell(shell)
        timeout = max(1, min(int(timeout or 15), 60))
        cwd_path = self._resolve_cwd(cwd)
        risk, block_reason = self._analyze(command, cwd_path)
        task = CommandTask(
            task_id=uuid.uuid4().hex,
            command=command,
            shell=shell,
            cwd=str(cwd_path),
            timeout=timeout,
            risk=risk,
        )
        self._remember(task)
        self._publish("command_task.created", task)

        if self._fuse_reason:
            task.status = "blocked"
            task.block_reason = f"manual fuse tripped: {self._fuse_reason}"
            task.exit_code = -1
            task.output = task.block_reason
            task.finished_at = time.time()
            self._publish("command_task.blocked", task)
            return self._result(task, ok=False)

        if risk == "root" and not self._valid_root_token(root_token):
            task.status = "blocked"
            task.block_reason = "root session token required for root command"
            task.exit_code = -1
            task.output = task.block_reason
            task.finished_at = time.time()
            self._publish("command_task.blocked", task)
            return self._result(task, ok=False)

        if block_reason:
            task.status = "blocked"
            task.block_reason = block_reason
            task.exit_code = -1
            task.output = block_reason
            task.finished_at = time.time()
            self._publish("command_task.blocked", task)
            return self._result(task, ok=False)

        try:
            if risk == "root":
                task.rollback_point = self._create_rollback_point(task)
                self.event_bus.publish(
                    "control.rollback_point.created",
                    {"task_id": task.task_id, "rollback_point": task._public_rollback_point()},
                    source="control",
                )
            completed = subprocess.run(
                self._argv(shell, command),
                cwd=str(cwd_path),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            task.stdout = (completed.stdout or "").strip()
            task.stderr = (completed.stderr or "").strip()
            task.exit_code = completed.returncode
            task.output = self._merge_output(task.stdout, task.stderr, task.exit_code)
            task.status = "completed" if completed.returncode == 0 else "failed"
            task.finished_at = time.time()
            self._audit_post(task)
            self._publish("command_task.completed" if completed.returncode == 0 else "command_task.failed", task)
            return self._result(task, ok=completed.returncode == 0)
        except subprocess.TimeoutExpired:
            task.status = "failed"
            task.exit_code = -1
            task.output = "command timed out"
            task.finished_at = time.time()
            self._audit_post(task)
            self._publish("command_task.failed", task)
            return self._result(task, ok=False)
        except Exception as exc:
            task.status = "failed"
            task.exit_code = -1
            task.output = str(exc)[:500]
            task.finished_at = time.time()
            self._audit_post(task)
            self._publish("command_task.failed", task)
            return self._result(task, ok=False)

    def recent_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), self._max_history))
        return [task.to_dict() | {"output": task.output[:1000]} for task in self._tasks[-limit:]]

    def start_command(
        self,
        command: str,
        shell: str = "cmd",
        timeout: int = 15,
        cwd: str | Path | None = None,
        root_token: str = "",
    ) -> dict[str, Any]:
        """Start an audited command and return immediately with a pollable task id."""
        command = str(command or "").strip()
        if not command:
            return {"ok": False, "error": "command is required"}
        shell = self._normalize_shell(shell)
        timeout = max(1, min(int(timeout or 15), 60))
        cwd_path = self._resolve_cwd(cwd)
        risk, block_reason = self._analyze(command, cwd_path)
        task = CommandTask(
            task_id=uuid.uuid4().hex,
            command=command,
            shell=shell,
            cwd=str(cwd_path),
            timeout=timeout,
            risk=risk,
        )
        self._remember(task)
        self._publish("command_task.created", task)

        if self._fuse_reason:
            return self._block_started_task(task, f"manual fuse tripped: {self._fuse_reason}")
        if risk == "root" and not self._valid_root_token(root_token):
            return self._block_started_task(task, "root session token required for root command")
        if block_reason:
            return self._block_started_task(task, block_reason)
        if risk == "root":
            task.rollback_point = self._create_rollback_point(task)
            self.event_bus.publish(
                "control.rollback_point.created",
                {"task_id": task.task_id, "rollback_point": task._public_rollback_point()},
                source="control",
            )

        try:
            process = subprocess.Popen(
                self._argv(shell, command),
                cwd=str(cwd_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            task.status = "failed"
            task.output = str(exc)[:500]
            task.exit_code = -1
            task.finished_at = time.time()
            self._publish("command_task.failed", task)
            return {"ok": False, "error": task.output, "task": task.to_dict()}

        task.status = "running"
        with self._process_lock:
            self._active_processes[task.task_id] = process
        self._publish("command_task.started", task)
        threading.Thread(target=self._wait_for_process, args=(task, process), daemon=True).start()
        return {"ok": True, "task": task.to_dict()}

    def get_task(self, task_id: str) -> dict[str, Any]:
        task = self._find_task(task_id)
        if task is None:
            return {"ok": False, "error": "command task not found"}
        return {"ok": True, "task": task.to_dict() | {"output": task.output[:5000]}}

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        task = self._find_task(task_id)
        if task is None:
            return {"ok": False, "error": "command task not found"}
        with self._process_lock:
            process = self._active_processes.get(task.task_id)
            if process is None or process.poll() is not None:
                return {"ok": False, "error": "command task is not running", "task": task.to_dict()}
            self._cancelled_tasks.add(task.task_id)
            task.status = "cancelling"
        self._publish("command_task.cancelling", task)
        self._terminate_process_tree(process)
        return {"ok": True, "task": task.to_dict()}

    def _wait_for_process(self, task: CommandTask, process: subprocess.Popen[str]) -> None:
        try:
            stdout, stderr = process.communicate(timeout=task.timeout)
            cancelled = task.task_id in self._cancelled_tasks
            task.stdout = (stdout or "").strip()
            task.stderr = (stderr or "").strip()
            task.exit_code = -1 if cancelled else int(process.returncode or 0)
            task.output = "command cancelled" if cancelled else self._merge_output(task.stdout, task.stderr, task.exit_code)
            task.status = "cancelled" if cancelled else ("completed" if task.exit_code == 0 else "failed")
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(process)
            task.status = "failed"
            task.exit_code = -1
            task.output = "command timed out"
        except Exception as exc:
            task.status = "failed"
            task.exit_code = -1
            task.output = str(exc)[:500]
        finally:
            task.finished_at = time.time()
            with self._process_lock:
                self._active_processes.pop(task.task_id, None)
                self._cancelled_tasks.discard(task.task_id)
            self._audit_post(task)
            self._publish(f"command_task.{task.status}", task)

    def _terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
            else:
                process.terminate()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _find_task(self, task_id: str) -> CommandTask | None:
        wanted = str(task_id or "")
        return next((task for task in reversed(self._tasks) if task.task_id == wanted), None)

    def _block_started_task(self, task: CommandTask, reason: str) -> dict[str, Any]:
        task.status = "blocked"
        task.block_reason = reason
        task.output = reason
        task.exit_code = -1
        task.finished_at = time.time()
        self._publish("command_task.blocked", task)
        return {"ok": False, "error": reason, "task": task.to_dict()}

    def restore_rollback_point(self, rollback_id: str) -> dict[str, Any]:
        rollback = self._rollback_points.get(str(rollback_id or ""))
        if not rollback:
            return {"ok": False, "error": "rollback point not found"}
        restored = 0
        skipped = 0
        for item in rollback.get("files", []):
            if item.get("is_dir"):
                skipped += 1
                continue
            path = (self.root / item.get("path", "")).resolve()
            if not self._is_inside_root(path):
                skipped += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if "content" in item:
                path.write_text(item["content"], encoding=item.get("encoding", "utf-8"))
                restored += 1
            elif "content_b64" in item:
                path.write_bytes(base64.b64decode(item["content_b64"].encode("ascii")))
                restored += 1
            elif "external_path" in item:
                source = Path(item["external_path"]).resolve()
                if source.exists() and source.is_file():
                    shutil.copy2(source, path)
                    restored += 1
                else:
                    skipped += 1
            else:
                skipped += 1
        self.event_bus.publish(
            "control.rollback_point.restored",
            {"rollback_id": rollback["id"], "restored": restored, "skipped": skipped},
            source="control",
        )
        return {"ok": True, "rollback_id": rollback["id"], "restored": restored, "skipped": skipped}

    def _valid_root_token(self, token: str) -> bool:
        token_hash = self._hash_token(token)
        item = self._root_tokens.get(token_hash)
        if not item:
            return False
        if item["expires_at"] <= time.time():
            self._root_tokens.pop(token_hash, None)
            return False
        return True

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    def _create_rollback_point(self, task: CommandTask) -> dict[str, Any]:
        rollback_id = uuid.uuid4().hex
        storage_dir = self.root / _ROLLBACK_STORE_DIRNAME / rollback_id
        files = []
        try:
            for child in self._rollback_items():
                try:
                    stat = child.stat()
                    relative_path = child.relative_to(self.root).as_posix()
                    entry = {
                        "path": relative_path,
                        "is_dir": child.is_dir(),
                        "size": stat.st_size if child.is_file() else 0,
                        "modified": stat.st_mtime,
                    }
                    if child.is_file() and stat.st_size <= _ROLLBACK_MAX_FILE_BYTES:
                        try:
                            entry["content"] = child.read_text(encoding="utf-8")
                            entry["encoding"] = "utf-8"
                        except UnicodeDecodeError:
                            entry["content_b64"] = base64.b64encode(child.read_bytes()).decode("ascii")
                            entry["encoding"] = "base64"
                    elif child.is_file():
                        external = storage_dir / relative_path
                        external.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(child, external)
                        entry["external_path"] = str(external.resolve())
                        entry["encoding"] = "external"
                    files.append(entry)
                except OSError:
                    continue
        except OSError:
            files = []
        rollback = {
            "id": rollback_id,
            "kind": "manifest",
            "task_id": task.task_id,
            "created_at": time.time(),
            "root": str(self.root),
            "storage_dir": str(storage_dir.resolve()),
            "file_count": len(files),
            "files": files,
        }
        self._rollback_points[rollback["id"]] = rollback
        return rollback

    def _rollback_items(self) -> list[Path]:
        items: list[Path] = []
        for child in sorted(self.root.rglob("*"), key=lambda item: item.relative_to(self.root).as_posix().lower()):
            relative_parts = set(child.relative_to(self.root).parts)
            if relative_parts.intersection(_ROLLBACK_SKIP_DIRS | {_ROLLBACK_STORE_DIRNAME}):
                continue
            items.append(child)
            if len(items) >= _ROLLBACK_MAX_FILES:
                break
        return items

    def _analyze(self, command: str, cwd: Path) -> tuple[str, str]:
        command_l = re.sub(r"\s+", " ", command.lower()).strip()
        risk = self._classify_risk(command_l)
        outside_reason = self._workspace_escape_reason(command, cwd)
        if outside_reason:
            risk = "root"
        required = _LEVEL_RANK[risk]
        current = self._current_level()
        if current < required:
            reason = outside_reason or f"command requires {risk} permission, current permission is {self._current_label()}"
            return risk, reason
        return risk, ""

    def _classify_risk(self, command_l: str) -> str:
        if any(re.search(pattern, command_l) for pattern in _DANGEROUS_PATTERNS):
            return "root"
        if any(re.search(pattern, command_l) for pattern in _TRUSTED_PATTERNS):
            return "trusted"
        if any(command_l == prefix or command_l.startswith(prefix + " ") for prefix in _SAFE_PREFIXES):
            return "safe"
        return "trusted"

    def _workspace_escape_reason(self, command: str, cwd: Path) -> str:
        if not self._is_inside_root(cwd):
            return f"cwd escapes workspace root: {cwd}"
        for raw in self._absolute_path_tokens(command):
            path = Path(raw).resolve()
            if not self._is_inside_root(path):
                return f"path escapes workspace root: {raw}"
        return ""

    def _absolute_path_tokens(self, command: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z]:[\\/][^\s'\";&|<>]+", command)
        return [token.rstrip("),]") for token in tokens]

    def _is_inside_root(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root)
            return True
        except ValueError:
            return False

    def _current_level(self) -> int:
        return _LEVEL_RANK.get(self._current_label(), 0)

    def _current_label(self) -> str:
        if self.permission_level:
            return self.permission_level
        if self.guard is not None and hasattr(self.guard, "permission_level"):
            value = int(getattr(self.guard, "permission_level"))
            if value >= 4:
                return "root"
            if value >= 1:
                return "trusted"
        return "safe"

    def _normalize_shell(self, shell: str) -> str:
        shell = str(shell or "cmd").lower()
        if shell in {"powershell", "pwsh"}:
            return "powershell"
        return "cmd"

    def _argv(self, shell: str, command: str) -> list[str]:
        if shell == "powershell":
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        return ["cmd", "/c", command]

    def _resolve_cwd(self, cwd: str | Path | None) -> Path:
        if cwd is None or str(cwd).strip() == "":
            return self.root
        path = Path(cwd)
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    def _merge_output(self, stdout: str, stderr: str, exit_code: int) -> str:
        if stdout and stderr:
            return (stdout + "\n[stderr]\n" + stderr)[:5000]
        if stderr:
            return stderr[:5000]
        if stdout:
            return stdout[:5000]
        return f"(exit code: {exit_code})"

    def _audit_post(self, task: CommandTask) -> None:
        if self.guard is None:
            return
        post_check = getattr(self.guard, "post_check", None)
        if callable(post_check):
            post_check(
                "system_execute",
                {"command": task.command, "task_id": task.task_id, "risk": task.risk},
                task.status == "completed",
                task.duration_ms or 0,
                task.output[:5000],
            )

    def _publish(self, event_type: str, task: CommandTask) -> None:
        self.event_bus.publish(event_type, task.to_dict(), source="control")

    def _remember(self, task: CommandTask) -> None:
        self._tasks.append(task)
        if len(self._tasks) > self._max_history:
            self._tasks = self._tasks[-self._max_history :]

    def _result(self, task: CommandTask, ok: bool) -> dict[str, Any]:
        return {
            "ok": ok,
            "output": task.output[:5000],
            "exit_code": task.exit_code if task.exit_code is not None else -1,
            "task": task.to_dict(),
        }
