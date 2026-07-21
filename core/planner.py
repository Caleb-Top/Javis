"""规划器 — 将用户请求分解为结构化任务树 + 执行调度"""

import json, logging, time, hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("planner")


@dataclass
class TaskNode:
    id: str = ""
    goal: str = ""
    status: str = "pending"     # pending | running | done | failed | skipped
    depends_on: list[str] = field(default_factory=list)
    expected_output: str = ""
    result: str = ""
    error: str = ""
    tool_used: str = ""
    created_at: float = 0.0
    completed_at: float = 0.0


@dataclass
class TaskPlan:
    id: str = ""
    user_goal: str = ""
    tasks: list[TaskNode] = field(default_factory=list)
    status: str = "created"
    created_at: float = 0.0
    completed_at: float = 0.0
    summary: str = ""


class Planner:
    """规划器 — 分解任务 + 调度执行"""

    def __init__(self):
        self._current_plan: Optional[TaskPlan] = None

    def create_plan(self, user_input: str) -> TaskPlan:
        now = time.time()
        plan_id = hashlib.md5(f"{user_input}{now}".encode()).hexdigest()[:8]
        plan = TaskPlan(id=plan_id, user_goal=user_input, created_at=now)
        self._current_plan = plan
        logger.info(f"📋 创建计划: {user_input[:60]}...")
        return plan

    def add_task(self, goal: str, depends_on: list[str] = None,
                 expected_output: str = "") -> TaskNode:
        if not self._current_plan:
            self._current_plan = TaskPlan(
                id=hashlib.md5(str(time.time()).encode()).hexdigest()[:8],
                user_goal="", created_at=time.time())
        now = time.time()
        task = TaskNode(
            id=hashlib.md5(f"{goal}{now}".encode()).hexdigest()[:8],
            goal=goal, depends_on=depends_on or [],
            expected_output=expected_output, created_at=now)
        self._current_plan.tasks.append(task)
        logger.info(f"  ├─ 子任务: {goal[:50]}...")
        return task

    def update_task(self, task_id: str, status: str = None,
                    result: str = None, error: str = None, tool_used: str = None):
        if not self._current_plan:
            return
        for task in self._current_plan.tasks:
            if task.id == task_id:
                if status: task.status = status
                if result: task.result = result[:200]
                if error: task.error = error[:200]
                if tool_used: task.tool_used = tool_used
                if status in ("done", "failed"):
                    task.completed_at = time.time()
                break

    def get_plan_snapshot(self) -> str:
        """获取当前计划的可读快照"""
        if not self._current_plan or not self._current_plan.tasks:
            return ""
        lines = [f"📋 当前任务计划 ({self._current_plan.id}):"]
        for t in self._current_plan.tasks:
            icons = {"pending": "⬜", "running": "▶️", "done": "✅", "failed": "❌", "skipped": "⏭️"}
            icon = icons.get(t.status, "⬜")
            deps = f" [依赖: {', '.join(t.depends_on[:3])}]" if t.depends_on else ""
            lines.append(f"  {icon} {t.goal[:60]}{deps}")
            if t.status == "done" and t.result:
                lines.append(f"     📎 {t.result[:80]}")
            if t.status == "failed" and t.error:
                lines.append(f"     ⚠️ {t.error[:80]}")
        # 自动完成
        if self._current_plan.status != "completed" and all(
            t.status in ("done", "failed", "skipped") for t in self._current_plan.tasks):
            self._current_plan.status = "completed"
            self._current_plan.completed_at = time.time()
        return "\n".join(lines)

    def get_next_ready_task(self) -> Optional[TaskNode]:
        """获取下一个依赖已全部完成的任务"""
        if not self._current_plan or not self._current_plan.tasks:
            return None
        completed_ids = {t.id for t in self._current_plan.tasks if t.status == "done"}
        for t in self._current_plan.tasks:
            if t.status != "pending":
                continue
            if all(dep in completed_ids for dep in t.depends_on):
                t.status = "running"
                logger.info(f"  ▶️ 执行: {t.goal[:60]}...")
                return t
        return None

    def mark_done(self, goal_prefix: str = "", result: str = ""):
        """按目标前缀标记任务完成"""
        if not self._current_plan:
            return
        for t in self._current_plan.tasks:
            if t.status == "running" and (not goal_prefix or t.goal.startswith(goal_prefix)):
                t.status = "done"
                t.completed_at = time.time()
                if result: t.result = result[:200]
                logger.info(f"  ✅ 完成: {t.goal[:60]}...")
                return

    def mark_failed(self, goal_prefix: str = "", error: str = ""):
        if not self._current_plan:
            return
        for t in self._current_plan.tasks:
            if t.status == "running" and (not goal_prefix or t.goal.startswith(goal_prefix)):
                t.status = "failed"
                t.error = error[:200]
                t.completed_at = time.time()
                logger.info(f"  ❌ 失败: {t.goal[:60]}...")
                return

    def complete_plan(self, summary: str = ""):
        if self._current_plan:
            self._current_plan.status = "completed"
            self._current_plan.completed_at = time.time()
            self._current_plan.summary = summary[:300]
            done = sum(1 for t in self._current_plan.tasks if t.status == "done")
            total = len(self._current_plan.tasks)
            logger.info(f"📋 计划完成: {done}/{total} 任务成功")

    def current_plan(self) -> Optional[TaskPlan]:
        return self._current_plan
