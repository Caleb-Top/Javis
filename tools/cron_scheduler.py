"""
Cron 用户可配置调度器 — jobs.json + 执行历史 + 文件锁
"""
import os, json, time, logging
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger("cron")

JOBS_FILE = Path("data/cron/jobs.json")
HISTORY_FILE = Path("data/cron/history.json")
LOCK_FILE = Path("data/cron/.lock")

@dataclass
class CronJob:
    job_id: str
    prompt: str
    schedule: str = "0 9 * * *"
    enabled: bool = True
    last_run: float = 0.0
    run_count: int = 0

class CronScheduler:
    def __init__(self):
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, CronJob] = {}
        self._load()

    def _load(self):
        if JOBS_FILE.exists():
            data = json.loads(JOBS_FILE.read_text())
            for j in data:
                self._jobs[j["job_id"]] = CronJob(**j)

    def _save(self):
        JOBS_FILE.write_text(json.dumps(
            [vars(j) for j in self._jobs.values()], indent=2, default=str
        ))

    def add(self, job_id: str, prompt: str, schedule: str = "0 9 * * *") -> CronJob:
        job = CronJob(job_id=job_id, prompt=prompt, schedule=schedule)
        self._jobs[job_id] = job
        self._save()
        return job

    def list_jobs(self) -> list[CronJob]:
        return list(self._jobs.values())

    def remove(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._save()
            return True
        return False

    def record_run(self, job_id: str, duration: float, success: bool):
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        history = []
        if HISTORY_FILE.exists():
            history = json.loads(HISTORY_FILE.read_text())
        history.append({
            "job_id": job_id, "time": time.time(),
            "duration": duration, "success": success
        })
        HISTORY_FILE.write_text(json.dumps(history[-500:], indent=2))

_scheduler: CronScheduler | None = None

def get_scheduler() -> CronScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler()
    return _scheduler


def register_in_manifest(reg):
    """Register cron scheduler tools in manifest"""
    from core.tool_registry import ToolDef
    sched = get_scheduler()

    async def cron_list(args):
        jobs = sched.list_jobs()
        return {
            "success": True,
            "jobs": [{
                "job_id": j.job_id, "prompt": j.prompt,
                "schedule": j.schedule, "enabled": j.enabled,
                "last_run": j.last_run, "run_count": j.run_count,
            } for j in jobs],
            "count": len(jobs),
        }

    async def cron_add(args):
        job = sched.add(
            job_id=args["job_id"],
            prompt=args["prompt"],
            schedule=args.get("schedule", "0 9 * * *"),
        )
        return {"success": True, "job_id": job.job_id, "schedule": job.schedule}

    async def cron_remove(args):
        ok = sched.remove(args["job_id"])
        return {"success": ok}

    async def cron_history(args):
        history = []
        if HISTORY_FILE.exists():
            history = json.loads(HISTORY_FILE.read_text())
        return {"success": True, "history": history[-50:], "count": len(history)}

    reg.register_many([
        ToolDef("cron_list", "List all scheduled cron jobs",
                {"type":"object","properties":{},"required":[]}, cron_list, "cron"),
        ToolDef("cron_add", "Add a new cron job",
                {"type":"object","properties":{"job_id":{"type":"string"},"prompt":{"type":"string"},"schedule":{"type":"string","default":"0 9 * * *"}},"required":["job_id","prompt"]}, cron_add, "cron"),
        ToolDef("cron_remove", "Remove a cron job",
                {"type":"object","properties":{"job_id":{"type":"string"}},"required":["job_id"]}, cron_remove, "cron"),
        ToolDef("cron_history", "View cron execution history",
                {"type":"object","properties":{},"required":[]}, cron_history, "cron"),
    ])
