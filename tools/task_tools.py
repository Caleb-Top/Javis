"""任务管理工具集 (P0-3) — TaskCreate / TaskGet / TaskList / TaskUpdate / TaskStop

基于内存的任务跟踪系统，支持结构化多步骤任务管理。
支持依赖关系（blocks/blockedBy）、状态流转（pending→in_progress→completed）。
"""

import json
import time
import uuid
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from core.tool_result import ToolResult

logger = logging.getLogger("task_tools")

# ── 全局任务存储（单 session 内存） ──
_tasks: dict[str, "TaskItem"] = {}
_task_counter: int = 0

VALID_STATUSES = {"pending", "in_progress", "completed", "deleted"}


@dataclass
class TaskItem:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"
    owner: str = ""
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    active_form: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def _task_to_dict(t: TaskItem) -> dict:
    return {
        "id": t.id,
        "subject": t.subject,
        "description": t.description,
        "status": t.status,
        "owner": t.owner,
        "blocks": t.blocks,
        "blockedBy": t.blocked_by,
        "activeForm": t.active_form or t.subject,
        "metadata": t.metadata,
        "createdAt": t.created_at and time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t.created_at)) or "",
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t.updated_at)),
    }


def _validate_dependencies(task_ids: list[str]) -> list[str]:
    """验证依赖的 task_id 是否存在（排除 deleted 状态）"""
    invalid = []
    for tid in task_ids:
        if tid not in _tasks or _tasks[tid].status == "deleted":
            invalid.append(tid)
    return invalid


# ═══════════════════════════════════════════════════════════════
# TaskCreate
# ═══════════════════════════════════════════════════════════════
def task_create(
    subject: str,
    description: str = "",
    active_form: str = "",
    status: str = "pending",
    blocks: list[str] = None,
    blocked_by: list[str] = None,
    owner: str = "",
    metadata: dict = None,
    **kwargs,
) -> ToolResult:
    """创建新任务"""
    global _task_counter

    if not subject or not subject.strip():
        return ToolResult.failure("subject 不能为空")

    if status not in VALID_STATUSES:
        return ToolResult.failure(f"无效状态: {status}，可选: {sorted(VALID_STATUSES)}")

    _task_counter += 1
    task_id = str(_task_counter)
    now = time.time()

    task = TaskItem(
        id=task_id,
        subject=subject.strip(),
        description=description or subject,
        status=status,
        owner=owner,
        blocks=blocks or [],
        blocked_by=blocked_by or [],
        active_form=active_form or subject,
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )

    # 验证依赖
    invalid_deps = _validate_dependencies(task.blocks) + _validate_dependencies(task.blocked_by)
    if invalid_deps:
        return ToolResult.failure(f"依赖的 task_id 不存在或已删除: {invalid_deps}")

    _tasks[task_id] = task
    logger.info(f"TaskCreate: [{task_id}] {subject} ({status})")
    return ToolResult.success(json.dumps({"taskId": task_id, "subject": subject, "status": status}, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# TaskGet
# ═══════════════════════════════════════════════════════════════
def task_get(task_id: str, **kwargs) -> ToolResult:
    """获取单个任务详情"""
    task = _tasks.get(task_id)
    if not task or task.status == "deleted":
        return ToolResult.failure(f"任务不存在或已删除: {task_id}")
    return ToolResult.success(json.dumps(_task_to_dict(task), ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════
# TaskList
# ═══════════════════════════════════════════════════════════════
def task_list(
    status: str = "",
    owner: str = "",
    **kwargs,
) -> ToolResult:
    """列出任务（支持按 status/owner 过滤）"""
    results = []
    for task in _tasks.values():
        if task.status == "deleted":
            continue
        if status and task.status != status:
            continue
        if owner and task.owner != owner:
            continue
        results.append({
            "id": task.id,
            "subject": task.subject,
            "status": task.status,
            "owner": task.owner,
            "blockedBy": task.blocked_by,
        })

    results.sort(key=lambda t: int(t["id"]))
    return ToolResult.success(json.dumps({
        "total": len(results),
        "tasks": results,
    }, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════
# TaskUpdate
# ═══════════════════════════════════════════════════════════════
def task_update(
    task_id: str,
    status: str = "",
    subject: str = "",
    description: str = "",
    active_form: str = "",
    owner: str = "",
    metadata: dict = None,
    add_blocks: list[str] = None,
    add_blocked_by: list[str] = None,
    remove_blocks: list[str] = None,
    remove_blocked_by: list[str] = None,
    **kwargs,
) -> ToolResult:
    """更新任务：状态、字段、依赖"""
    task = _tasks.get(task_id)
    if not task:
        return ToolResult.failure(f"任务不存在: {task_id}")
    if task.status == "deleted":
        return ToolResult.failure(f"任务已删除: {task_id}")

    changed = []

    if status and status != task.status:
        if status not in VALID_STATUSES:
            return ToolResult.failure(f"无效状态: {status}")
        old_status = task.status
        task.status = status
        changed.append(f"status: {old_status} → {status}")

    if subject:
        task.subject = subject.strip()
        if not task.active_form:
            task.active_form = subject
        changed.append("subject")

    if description:
        task.description = description
        changed.append("description")

    if active_form:
        task.active_form = active_form
        changed.append("active_form")

    if owner is not None:
        task.owner = owner
        changed.append(f"owner: {owner}")

    if metadata is not None:
        for k, v in metadata.items():
            if v is None:
                task.metadata.pop(k, None)
            else:
                task.metadata[k] = v
        changed.append("metadata")

    if add_blocks:
        invalid = _validate_dependencies(add_blocks)
        if invalid:
            return ToolResult.failure(f"blocks 中的任务不存在: {invalid}")
        for tid in add_blocks:
            if tid not in task.blocks:
                task.blocks.append(tid)
                _tasks[tid].blocked_by.append(task_id)
        changed.append(f"+blocks: {add_blocks}")

    if add_blocked_by:
        invalid = _validate_dependencies(add_blocked_by)
        if invalid:
            return ToolResult.failure(f"blockedBy 中的任务不存在: {invalid}")
        for tid in add_blocked_by:
            if tid not in task.blocked_by:
                task.blocked_by.append(tid)
                _tasks[tid].blocks.append(task_id)
        changed.append(f"+blockedBy: {add_blocked_by}")

    if remove_blocks:
        for tid in remove_blocks:
            if tid in task.blocks:
                task.blocks.remove(tid)
            if tid in _tasks:
                _tasks[tid].blocked_by = [b for b in _tasks[tid].blocked_by if b != task_id]
        changed.append(f"-blocks: {remove_blocks}")

    if remove_blocked_by:
        for tid in remove_blocked_by:
            if tid in task.blocked_by:
                task.blocked_by.remove(tid)
            if tid in _tasks:
                _tasks[tid].blocks = [b for b in _tasks[tid].blocks if b != task_id]
        changed.append(f"-blockedBy: {remove_blocked_by}")

    task.updated_at = time.time()
    logger.info(f"TaskUpdate: [{task_id}] {'; '.join(changed)}")
    return ToolResult.success(json.dumps({
        "taskId": task_id,
        "changes": changed,
        "task": _task_to_dict(task),
    }, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════
# TaskStop
# ═══════════════════════════════════════════════════════════════
def task_stop(task_id: str, **kwargs) -> ToolResult:
    """停止/取消任务 — 将任务标记为 deleted"""
    task = _tasks.get(task_id)
    if not task:
        return ToolResult.failure(f"任务不存在: {task_id}")

    task.status = "deleted"
    task.updated_at = time.time()

    # 清理依赖引用
    for other in _tasks.values():
        if task_id in other.blocks:
            other.blocks.remove(task_id)
        if task_id in other.blocked_by:
            other.blocked_by.remove(task_id)

    logger.info(f"TaskStop: [{task_id}] {task.subject} → deleted")
    return ToolResult.success(json.dumps({"taskId": task_id, "status": "deleted"}, ensure_ascii=False))
