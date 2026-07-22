"""
Cron 用户可配置调度器 — jobs.json + 执行历史 + 文件锁
P1-4: Configurable cron with file locking, history, and pattern matching
"""
import os, json, time, logging, fcntl, threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("cron")

JOBS_FILE = Path("data/cron/jobs.json")
HISTORY_FILE = Path("data/cron/history.json")
LOCK_DIR = Path("data/cron/locks")


@dataclass
class CronJob:
    job_id: str
    prompt: str
    schedule: str = "0 9 * * *"  # cron 表达式
    enabled: bool = True
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    failure_count: int = 0
    created_at: float = field(default_factory=time.time)
    timeout_seconds: int = 300
    notify_on_completion: bool = False

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id, "prompt": self.prompt,
            "schedule": self.schedule, "enabled": self.enabled,
            "last_run": self.last_run, "next_run": self.next_run,
            "run_count": self.run_count, "failure_count": self.failure_count,
            "created_at": self.created_at,
            "timeout_seconds": self.timeout_seconds,
            "notify_on_completion": self.notify_on_completion,
        }


class CronScheduler:
    """Cron 调度器 — 用户可配置 + 文件锁 + 执行历史"""

    def __init__(self, jobs_path: str = None, history_path: str = None):
        self._jobs_path = Path(jobs_path) if jobs_path else JOBS_FILE
        self._history_path = Path(history_path) if history_path else HISTORY_FILE
        self._jobs_path.parent.mkdir(parents=True, exist_ok=True)
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, CronJob] = {}
        self._lock = threading.Lock()
        self._running_jobs: dict[str, bool] = {}
        self._load()

    def _load(self):
        """加载 jobs.json"""
        if self._jobs_path.exists():
            try:
                with self._lock:
                    data = json.loads(self._jobs_path.read_text())
                    for j in data:
                        self._jobs[j["job_id"]] = CronJob(
                            job_id=j["job_id"],
                            prompt=j.get("prompt", ""),
                            schedule=j.get("schedule", "0 9 * * *"),
                            enabled=j.get("enabled", True),
                            last_run=j.get("last_run", 0.0),
                            next_run=j.get("next_run", 0.0),
                            run_count=j.get("run_count", 0),
                            failure_count=j.get("failure_count", 0),
                            created_at=j.get("created_at", time.time()),
                            timeout_seconds=j.get("timeout_seconds", 300),
                            notify_on_completion=j.get("notify_on_completion", False),
                        )
                logger.info(f"加载 {len(self._jobs)} 个 Cron 任务")
            except Exception as e:
                logger.error(f"加载 jobs.json 失败: {e}")

    def _save(self):
        """保存 jobs.json"""
        with self._lock:
            self._jobs_path.write_text(json.dumps(
                [j.to_dict() for j in self._jobs.values()],
                indent=2, ensure_ascii=False,
            ))

    def add(self, job_id: str, prompt: str, schedule: str = "0 9 * * *",
            timeout: int = 300, notify: bool = False) -> CronJob:
        """添加定时任务"""
        with self._lock:
            job = CronJob(
                job_id=job_id, prompt=prompt, schedule=schedule,
                timeout_seconds=timeout, notify_on_completion=notify,
            )
            job.next_run = self._calc_next_run(schedule)
            self._jobs[job_id] = job
            self._save()
        logger.info(f"Cron 任务已添加: {job_id} [{schedule}]")
        return job

    def get(self, job_id: str) -> Optional[CronJob]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[CronJob]:
        return sorted(self._jobs.values(), key=lambda j: j.job_id)

    def remove(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                self._save()
                logger.info(f"Cron 任务已删除: {job_id}")
                return True
        return False

    def toggle(self, job_id: str) -> Optional[bool]:
        """切换启用状态, 返回新状态"""
        job = self._jobs.get(job_id)
        if job:
            job.enabled = not job.enabled
            self._save()
            return job.enabled
        return None

    def update(self, job_id: str, **kwargs) -> bool:
        """更新任务配置"""
        job = self._jobs.get(job_id)
        if not job:
            return False
        for key, val in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, val)
        self._save()
        return True

    def acquire_lock(self, job_id: str) -> bool:
        """获取文件锁 — 防重复执行"""
        lock_file = LOCK_DIR / f"{job_id}.lock"
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._running_jobs[job_id] = True
            return True
        except (IOError, BlockingIOError):
            logger.debug(f"任务已在运行，跳过: {job_id}")
            return False

    def release_lock(self, job_id: str):
        """释放文件锁"""
        self._running_jobs.pop(job_id, None)
        lock_file = LOCK_DIR / f"{job_id}.lock"
        try:
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass

    def is_running(self, job_id: str) -> bool:
        return self._running_jobs.get(job_id, False)

    def record_run(self, job_id: str, duration: float, success: bool, output: str = ""):
        """记录执行历史"""
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if self._history_path.exists():
            try:
                history = json.loads(self._history_path.read_text())
            except Exception:
                history = []

        entry = {
            "job_id": job_id, "time": time.time(),
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": round(duration, 2),
            "success": success, "output": output[:500] if output else "",
        }
        history.append(entry)

        # 保留最近 500 条
        self._history_path.write_text(json.dumps(
            history[-500:], indent=2, ensure_ascii=False
        ))

        # 更新任务统计
        job = self._jobs.get(job_id)
        if job:
            job.last_run = time.time()
            job.next_run = self._calc_next_run(job.schedule)
            job.run_count += 1
            if not success:
                job.failure_count += 1
            self._save()

    def get_history(self, job_id: str = None, limit: int = 50) -> list[dict]:
        """获取执行历史"""
        if not self._history_path.exists():
            return []
        try:
            history = json.loads(self._history_path.read_text())
        except Exception:
            return []

        if job_id:
            history = [h for h in history if h["job_id"] == job_id]
        return history[-limit:]

    def get_stats(self) -> dict:
        """调度器统计"""
        total = len(self._jobs)
        enabled = sum(1 for j in self._jobs.values() if j.enabled)
        running = sum(1 for j in self._jobs.values() if self.is_running(j.job_id))
        total_runs = sum(j.run_count for j in self._jobs.values())
        total_failures = sum(j.failure_count for j in self._jobs.values())
        return {
            "total": total, "enabled": enabled, "running": running,
            "total_runs": total_runs, "total_failures": total_failures,
        }

    @staticmethod
    def _calc_next_run(cron_expr: str) -> float:
        """简单计算下次运行时间（返回 epoch）"""
        # 简化实现: 返回明天同一时间
        import datetime
        now = datetime.datetime.now()
        return (now + datetime.timedelta(days=1)).timestamp()


# 全局单例
_scheduler: Optional[CronScheduler] = None

def get_scheduler() -> CronScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler()
    return _scheduler


def register_in_manifest(reg):
    """注册 Cron 调度器工具到 manifest"""
    from core.tool_registry import ToolDef
    sched = get_scheduler()

    async def cron_list(args):
        jobs = sched.list_jobs()
        return {
            "success": True,
            "stats": sched.get_stats(),
            "jobs": [j.to_dict() for j in jobs],
        }

    async def cron_add(args):
        job = sched.add(
            job_id=args["job_id"],
            prompt=args["prompt"],
            schedule=args.get("schedule", "0 9 * * *"),
            timeout=args.get("timeout_seconds", 300),
            notify=args.get("notify_on_completion", False),
        )
        return {"success": True, "job": job.to_dict()}

    async def cron_remove(args):
        ok = sched.remove(args["job_id"])
        return {"success": ok, "job_id": args["job_id"]}

    async def cron_toggle(args):
        new_state = sched.toggle(args["job_id"])
        if new_state is None:
            return {"success": False, "error": f"Job not found: {args['job_id']}"}
        return {"success": True, "job_id": args["job_id"], "enabled": new_state}

    async def cron_history(args):
        job_id = args.get("job_id")
        limit = args.get("limit", 50)
        history = sched.get_history(job_id, limit)
        return {"success": True, "history": history, "count": len(history)}

    async def cron_stats(args):
        return {"success": True, **sched.get_stats()}

    reg.register_many([
        ToolDef("cron_list", "列出所有定时任务及统计",
                {"type":"object","properties":{},"required":[]},
                cron_list, "cron"),
        ToolDef("cron_add", "添加新的定时任务",
                {"type":"object","properties":{
                    "job_id":{"type":"string"},"prompt":{"type":"string"},
                    "schedule":{"type":"string","default":"0 9 * * *"},
                    "timeout_seconds":{"type":"integer","default":300},
                    "notify_on_completion":{"type":"boolean","default":False}
                },"required":["job_id","prompt"]},
                cron_add, "cron"),
        ToolDef("cron_remove", "删除定时任务",
                {"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"]},
                cron_remove, "cron"),
        ToolDef("cron_toggle", "启用/禁用定时任务",
                {"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"]},
                cron_toggle, "cron"),
        ToolDef("cron_history", "查看执行历史",
                {"type":"object","properties":{
                    "job_id":{"type":"string"},"limit":{"type":"integer","default":50}
                },"required":[]},
                cron_history, "cron"),
        ToolDef("cron_stats", "查看调度器统计",
                {"type":"object","properties":{},"required":[]},
                cron_stats, "cron"),
    ])
