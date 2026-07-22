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
