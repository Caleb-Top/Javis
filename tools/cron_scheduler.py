"""
P1-4: Cron 定时任务调度器 — Javis Cron Scheduler
用户可配置的定时任务系统, 支持 cron 表达式和一次性计划
"""
import json
import time
import asyncio
import os
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from croniter import croniter


@dataclass
class ScheduledTask:
    """单个定时任务"""
    name: str
    description: str = ""
    cron_expression: str = ""       # "0 9 * * *" 每天9点
    command: str = ""                # shell 命令 或 Python 函数名
    enabled: bool = True
    last_run: Optional[float] = None
    next_run: Optional[float] = None
    run_count: int = 0
    max_runs: int = 0                # 0 = 无限
    timeout: int = 300               # 默认5分钟超时
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "description": self.description,
            "cron": self.cron_expression, "enabled": self.enabled,
            "last_run": datetime.fromtimestamp(self.last_run).isoformat() if self.last_run else None,
            "next_run": datetime.fromtimestamp(self.next_run).isoformat() if self.next_run else None,
            "run_count": self.run_count, "max_runs": self.max_runs
        }

    def compute_next(self, base_time: float = None) -> Optional[float]:
        if not self.cron_expression:
            return None
        base = datetime.fromtimestamp(base_time or time.time())
        cron = croniter(self.cron_expression, base)
        return cron.get_next(float)


class CronScheduler:
    """定时任务调度器"""

    def __init__(self, storage_path: str = ""):
        self._tasks: Dict[str, ScheduledTask] = {}
        self._running: bool = False
        self._runner_task: Optional[asyncio.Task] = None
        self._command_handlers: Dict[str, Callable] = {}
        self._task_handlers: Dict[str, Callable] = {}  # SchedulerTask table
        self._run_count: int = 0
        self._storage_path = storage_path or os.path.join(
            os.path.dirname(__file__), "..", "data", "cron_tasks.json"
        )
        self._load()

    def _load(self):
        """从磁盘加载任务"""
        try:
            if os.path.exists(self._storage_path):
                with open(self._storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for item in data:
                    task = ScheduledTask(**item)
                    task.next_run = task.compute_next()
                    self._tasks[task.name] = task
        except Exception:
            pass

    def _save(self):
        """保存任务到磁盘"""
        os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
        tasks_data = [{
            "name": t.name, "description": t.description,
            "cron_expression": t.cron_expression, "command": t.command,
            "enabled": t.enabled, "max_runs": t.max_runs,
            "timeout": t.timeout, "run_count": t.run_count,
            "metadata": t.metadata
        } for t in self._tasks.values()]
        with open(self._storage_path, 'w', encoding='utf-8') as f:
            json.dump(tasks_data, f, indent=2, ensure_ascii=False)

    def add_task(self, task: ScheduledTask):
        task.next_run = task.compute_next()
        self._tasks[task.name] = task
        self._save()

    def remove_task(self, name: str) -> bool:
        if name in self._tasks:
            del self._tasks[name]
            self._save()
            return True
        return False

    def enable_task(self, name: str, enabled: bool = True):
        if name in self._tasks:
            self._tasks[name].enabled = enabled
            if enabled:
                self._tasks[name].next_run = self._tasks[name].compute_next()
            self._save()

    def list_tasks(self) -> List[Dict]:
        return [t.to_dict() for t in self._tasks.values()]

    def register_handler(self, command: str, handler: Callable):
        """注册命令处理函数"""
        self._command_handlers[command] = handler

    def register_task_handler(self, task_id: str, handler: Callable):
        """Register a task by SchedulerTask name"""
        self._task_handlers[task_id] = handler

    async def start(self):
        """启动调度循环"""
        self._running = True
        self._runner_task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """停止调度循环"""
        self._running = False
        if self._runner_task:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self):
        """主调度循环 — 每分钟检查一次"""
        while self._running:
            now = time.time()
            for task in list(self._tasks.values()):
                if not task.enabled:
                    continue
                if task.next_run and now >= task.next_run:
                    if task.max_runs > 0 and task.run_count >= task.max_runs:
                        task.enabled = False
                        continue
                    asyncio.create_task(self._execute_task(task))
                    task.run_count += 1
                    task.last_run = now
                    task.next_run = task.compute_next(now)
            self._run_count += 1
            self._save()
            await asyncio.sleep(60)

    async def _execute_task(self, task: ScheduledTask):
        """执行单个任务"""
        try:
            handler = self._command_handlers.get(task.command)
            if handler:
                result = await asyncio.wait_for(
                    handler(task.metadata), timeout=task.timeout
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    task.command, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=task.timeout
                )
                result = {
                    "returncode": proc.returncode,
                    "stdout": stdout.decode()[:1000] if stdout else "",
                    "stderr": stderr.decode()[:1000] if stderr else "",
                }
        except asyncio.TimeoutError:
            result = {"error": f"Task '{task.name}' timed out after {task.timeout}s"}
        except Exception as e:
            result = {"error": str(e)}

        # Log the execution
        import logging
        logger = logging.getLogger("javis.cron")
        logger.info(f"Task '{task.name}' completed: {str(result)[:200]}")

    def run_now(self, name: str) -> bool:
        """立即执行一次指定任务"""
        task = self._tasks.get(name)
        if not task:
            return False
        asyncio.create_task(self._execute_task(task))
        task.run_count += 1
        task.last_run = time.time()
        return True


# 全局单例
_scheduler: Optional[CronScheduler] = None


def get_scheduler() -> CronScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler()
    return _scheduler


def register_in_manifest(reg):
    """Register cron tools in manifest"""
    from core.tool_registry import ToolDef
    sched = get_scheduler()

    async def list_cron_tasks(args):
        tasks = sched.list_tasks()
        return {"success": True, "tasks": tasks, "running": sched._running}

    async def add_cron_task(args):
        task = ScheduledTask(
            name=args["name"],
            description=args.get("description", ""),
            cron_expression=args["cron"],
            command=args.get("command", ""),
            max_runs=args.get("max_runs", 0),
            timeout=args.get("timeout", 300)
        )
        sched.add_task(task)
        return {"success": True, "task": task.to_dict()}

    async def remove_cron_task(args):
        ok = sched.remove_task(args["name"])
        return {"success": ok}

    async def enable_cron_task(args):
        sched.enable_task(args["name"], args.get("enabled", True))
        return {"success": True}

    async def run_cron_now(args):
        ok = sched.run_now(args["name"])
        return {"success": ok}

    async def scheduler_status(args):
        return {"success": True, "running": sched._running,
                "task_count": len(sched._tasks),
                "loop_iterations": sched._run_count}

    async def scheduler_start(args):
        if not sched._running:
            await sched.start()
        return {"success": True, "running": sched._running}

    async def scheduler_stop(args):
        await sched.stop()
        return {"success": True, "running": sched._running}

    reg.register_many([
        ToolDef("cron_list", "List all scheduled cron tasks", {"type":"object","properties":{},"required":[]}, list_cron_tasks, "cron"),
        ToolDef("cron_add", "Add a new cron scheduled task",
                {"type":"object","properties":{"name":{"type":"string"},"cron":{"type":"string"},"command":{"type":"string","default":""},"description":{"type":"string","default":""},"max_runs":{"type":"integer","default":0},"timeout":{"type":"integer","default":300}},"required":["name","cron"]}, add_cron_task, "cron"),
        ToolDef("cron_remove", "Remove a cron task", {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}, remove_cron_task, "cron"),
        ToolDef("cron_enable", "Enable/disable a cron task", {"type":"object","properties":{"name":{"type":"string"},"enabled":{"type":"boolean","default":true}},"required":["name"]}, enable_cron_task, "cron"),
        ToolDef("cron_run_now", "Run a cron task immediately", {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}, run_cron_now, "cron"),
        ToolDef("cron_status", "Get scheduler status", {"type":"object","properties":{},"required":[]}, scheduler_status, "cron"),
        ToolDef("cron_start", "Start the scheduler loop", {"type":"object","properties":{},"required":[]}, scheduler_start, "cron"),
        ToolDef("cron_stop", "Stop the scheduler loop", {"type":"object","properties":{},"required":[]}, scheduler_stop, "cron"),
    ])
