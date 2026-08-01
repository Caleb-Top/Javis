"""Durable execution graph for Javis agent runs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from core.events import EventBus, EventType


SCHEMA_VERSION = 1
FINAL_RUN_STATES = {"completed", "failed", "cancelled"}
FINAL_TASK_STATES = {"completed", "failed", "cancelled"}
FINAL_STEP_STATES = {"completed", "failed", "cancelled", "blocked"}


class AgentRunStateError(RuntimeError):
    """Raised when an execution graph transition is unsafe or invalid."""


class AgentRunStore:
    def __init__(self, path: str | Path, event_bus: EventBus | None = None):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.event_bus = event_bus
        self._lock = threading.RLock()
        self._closed = False
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS run_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    parent_run_id TEXT,
                    metadata_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(parent_run_id) REFERENCES runs(id)
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    UNIQUE(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS steps (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    UNIQUE(task_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS deltas (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(step_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(step_id) REFERENCES steps(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    step_id TEXT,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE,
                    FOREIGN KEY(step_id) REFERENCES steps(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_deltas_step ON deltas(step_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id, status);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints(run_id, sequence);
                """
            )
            self._connection.execute(
                "INSERT OR REPLACE INTO run_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def create_run(
        self,
        objective: str,
        *,
        metadata: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        objective = str(objective or "").strip()
        if not objective:
            raise ValueError("run objective cannot be empty")
        if parent_run_id is not None:
            self._require_run(parent_run_id)
        run_id = run_id or uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO runs(id, objective, status, parent_run_id, metadata_json, error, "
                "created_at, updated_at, completed_at) VALUES (?, ?, 'running', ?, ?, '', ?, ?, NULL)",
                (run_id, objective, parent_run_id, _json(metadata or {}), now, now),
            )
        run = self.get_run(run_id)
        self._publish(EventType.AGENT_RUN_CREATED, run_id, {"run_id": run_id, "objective": objective})
        return run

    def add_task(
        self,
        run_id: str,
        title: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self._require_run(run_id)
        if run["status"] != "running":
            raise AgentRunStateError(f"cannot add task to run in state {run['status']}")
        now = time.time()
        task_id = uuid.uuid4().hex
        with self._lock, self._connection:
            sequence = self._next_sequence("tasks", "run_id", run_id)
            self._connection.execute(
                "INSERT INTO tasks(id, run_id, sequence, title, status, metadata_json, error, "
                "created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, 'pending', ?, '', ?, ?, NULL)",
                (task_id, run_id, sequence, str(title), _json(metadata or {}), now, now),
            )
        task = self._get_task(task_id)
        self._publish(
            EventType.AGENT_TASK_CREATED,
            run_id,
            {"run_id": run_id, "task_id": task_id, "sequence": sequence, "title": str(title)},
        )
        return task

    def start_step(
        self,
        run_id: str,
        task_id: str,
        name: str,
        *,
        kind: str = "action",
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self._require_run(run_id)
        task = self._get_task(task_id)
        if task is None or task["run_id"] != run_id:
            raise KeyError(task_id)
        if run["status"] != "running":
            raise AgentRunStateError(f"cannot start step for run in state {run['status']}")
        if task["status"] not in {"pending", "running"}:
            raise AgentRunStateError(f"cannot start step for task in state {task['status']}")
        now = time.time()
        step_id = uuid.uuid4().hex
        with self._lock, self._connection:
            sequence = self._next_sequence("steps", "task_id", task_id)
            self._connection.execute(
                "UPDATE tasks SET status='running', updated_at=? WHERE id=?",
                (now, task_id),
            )
            self._connection.execute(
                "INSERT INTO steps(id, run_id, task_id, sequence, name, kind, status, input_json, "
                "output_json, error, created_at, updated_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', ?, '{}', '', ?, ?, NULL)",
                (step_id, run_id, task_id, sequence, str(name), str(kind), _json(input_data or {}), now, now),
            )
        step = self._get_step(step_id)
        self._publish(
            EventType.AGENT_STEP_CREATED,
            run_id,
            {"run_id": run_id, "task_id": task_id, "step_id": step_id, "sequence": sequence},
        )
        return step

    def append_delta(
        self,
        run_id: str,
        step_id: str,
        kind: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self._require_run(run_id)
        step = self._get_step(step_id)
        if step is None or step["run_id"] != run_id:
            raise KeyError(step_id)
        if run["status"] not in {"running", "waiting_approval"}:
            raise AgentRunStateError(f"cannot append delta to run in state {run['status']}")
        if step["status"] not in {"running", "waiting_approval"}:
            raise AgentRunStateError(f"cannot append delta to step in state {step['status']}")
        delta_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connection:
            sequence = self._next_sequence("deltas", "step_id", step_id)
            self._connection.execute(
                "INSERT INTO deltas(id, run_id, step_id, sequence, kind, content, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (delta_id, run_id, step_id, sequence, str(kind), str(content), _json(metadata or {}), now),
            )
        delta = self._get_delta(delta_id)
        self._publish(
            EventType.AGENT_DELTA_APPENDED,
            run_id,
            {"run_id": run_id, "step_id": step_id, "delta_id": delta_id, "sequence": sequence, "kind": str(kind)},
        )
        return delta

    def create_checkpoint(self, run_id: str, state: dict[str, Any]) -> dict[str, Any]:
        run = self._require_run(run_id)
        if run["status"] in FINAL_RUN_STATES:
            raise AgentRunStateError(f"cannot checkpoint run in state {run['status']}")
        checkpoint_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connection:
            sequence = self._next_sequence("checkpoints", "run_id", run_id)
            self._connection.execute(
                "INSERT INTO checkpoints(id, run_id, sequence, state_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (checkpoint_id, run_id, sequence, _json(state), now),
            )
        checkpoint = self._get_checkpoint(checkpoint_id)
        self._publish(
            EventType.AGENT_CHECKPOINT_CREATED,
            run_id,
            {"run_id": run_id, "checkpoint_id": checkpoint_id, "sequence": sequence},
        )
        return checkpoint

    def finish_step(
        self,
        step_id: str,
        *,
        status: str,
        output_data: dict[str, Any] | None = None,
        error: str = "",
    ) -> dict[str, Any]:
        if status not in {"completed", "failed", "cancelled"}:
            raise AgentRunStateError(f"invalid final step status: {status}")
        step = self._get_step(step_id)
        if step is None:
            raise KeyError(step_id)
        if step["status"] not in {"running", "waiting_approval"}:
            raise AgentRunStateError(f"cannot finish step in state {step['status']}")
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE steps SET status=?, output_json=?, error=?, updated_at=?, completed_at=? WHERE id=?",
                (status, _json(output_data or {}), str(error), now, now, step_id),
            )
        updated = self._get_step(step_id)
        self._publish(
            EventType.AGENT_STEP_UPDATED,
            step["run_id"],
            {"run_id": step["run_id"], "step_id": step_id, "status": status},
        )
        return updated

    def finish_task(self, task_id: str, *, status: str, error: str = "") -> dict[str, Any]:
        if status not in {"completed", "failed", "cancelled"}:
            raise AgentRunStateError(f"invalid final task status: {status}")
        task = self._get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task["status"] not in {"pending", "running"}:
            raise AgentRunStateError(f"cannot finish task in state {task['status']}")
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE tasks SET status=?, error=?, updated_at=?, completed_at=? WHERE id=?",
                (status, str(error), now, now, task_id),
            )
        updated = self._get_task(task_id)
        self._publish(
            EventType.AGENT_TASK_UPDATED,
            task["run_id"],
            {"run_id": task["run_id"], "task_id": task_id, "status": status},
        )
        return updated

    def finish_run(self, run_id: str, *, status: str, error: str = "") -> dict[str, Any]:
        if status not in {"completed", "failed"}:
            raise AgentRunStateError(f"invalid final run status: {status}")
        run = self._require_run(run_id)
        if run["status"] in FINAL_RUN_STATES:
            raise AgentRunStateError(f"cannot finish run in state {run['status']}")
        if self._pending_approvals(run_id):
            raise AgentRunStateError("cannot finish run with pending approvals")
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE runs SET status=?, error=?, updated_at=?, completed_at=? WHERE id=?",
                (status, str(error), now, now, run_id),
            )
        event_type = EventType.AGENT_RUN_COMPLETED if status == "completed" else EventType.AGENT_RUN_FAILED
        self._publish(event_type, run_id, {"run_id": run_id, "status": status, "error": str(error)})
        return self.get_run(run_id)

    def request_approval(
        self,
        run_id: str,
        step_id: str | None,
        *,
        action: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self._require_run(run_id)
        if run["status"] != "running":
            raise AgentRunStateError(f"cannot request approval for run in state {run['status']}")
        if step_id is not None:
            step = self._get_step(step_id)
            if step is None or step["run_id"] != run_id:
                raise KeyError(step_id)
            if step["status"] != "running":
                raise AgentRunStateError(f"cannot request approval for step in state {step['status']}")
        approval_id = uuid.uuid4().hex
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO approvals(id, run_id, step_id, action, status, request_json, response_json, "
                "created_at, resolved_at) VALUES (?, ?, ?, ?, 'pending', ?, '{}', ?, NULL)",
                (approval_id, run_id, step_id, str(action), _json(request or {}), now),
            )
            self._connection.execute(
                "UPDATE runs SET status='waiting_approval', updated_at=? WHERE id=?",
                (now, run_id),
            )
            if step_id is not None:
                self._connection.execute(
                    "UPDATE steps SET status='waiting_approval', updated_at=? WHERE id=?",
                    (now, step_id),
                )
        approval = self.get_approval(approval_id)
        self._publish(
            EventType.APPROVAL_REQUESTED,
            run_id,
            {"run_id": run_id, "step_id": step_id, "approval_id": approval_id, "action": str(action)},
        )
        return approval

    def resolve_approval(
        self,
        approval_id: str,
        *,
        approved: bool,
        response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        approval = self.get_approval(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        if approval["status"] != "pending":
            raise AgentRunStateError(f"approval is already {approval['status']}")
        status = "approved" if approved else "denied"
        run_status = "running" if approved else "blocked"
        step_status = "running" if approved else "blocked"
        now = time.time()
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE approvals SET status=?, response_json=?, resolved_at=? WHERE id=?",
                (status, _json(response or {}), now, approval_id),
            )
            self._connection.execute(
                "UPDATE runs SET status=?, updated_at=? WHERE id=?",
                (run_status, now, approval["run_id"]),
            )
            if approval["step_id"] is not None:
                self._connection.execute(
                    "UPDATE steps SET status=?, updated_at=? WHERE id=?",
                    (step_status, now, approval["step_id"]),
                )
        resolved = self.get_approval(approval_id)
        self._publish(
            EventType.APPROVAL_RESOLVED,
            approval["run_id"],
            {"run_id": approval["run_id"], "approval_id": approval_id, "status": status},
        )
        return resolved

    def resume_run(self, run_id: str) -> dict[str, Any]:
        run = self._require_run(run_id)
        if run["status"] in FINAL_RUN_STATES:
            raise AgentRunStateError(f"cannot resume run in state {run['status']}")
        pending = self._pending_approvals(run_id)
        now = time.time()
        with self._lock, self._connection:
            if pending:
                response = _json({"reason": "auto-denied on resume"})
                for approval in pending:
                    self._connection.execute(
                        "UPDATE approvals SET status='denied', response_json=?, resolved_at=? WHERE id=?",
                        (response, now, approval["id"]),
                    )
                    if approval["step_id"]:
                        self._connection.execute(
                            "UPDATE steps SET status='blocked', updated_at=? WHERE id=?",
                            (now, approval["step_id"]),
                        )
                next_status = "blocked"
            else:
                next_status = "running"
            self._connection.execute(
                "UPDATE runs SET status=?, updated_at=? WHERE id=?",
                (next_status, now, run_id),
            )
        checkpoint = self._latest_checkpoint(run_id)
        self._publish(
            EventType.AGENT_RUN_UPDATED,
            run_id,
            {"run_id": run_id, "status": next_status, "auto_denied_approvals": len(pending)},
        )
        return {"run": self.get_run(run_id), "checkpoint": checkpoint}

    def cancel_run(self, run_id: str, *, reason: str = "") -> dict[str, Any]:
        run = self._require_run(run_id)
        if run["status"] in FINAL_RUN_STATES:
            raise AgentRunStateError(f"cannot cancel run in state {run['status']}")
        now = time.time()
        response = _json({"reason": str(reason or "run cancelled")})
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE runs SET status='cancelled', error=?, updated_at=?, completed_at=? WHERE id=?",
                (str(reason), now, now, run_id),
            )
            self._connection.execute(
                "UPDATE tasks SET status='cancelled', error=?, updated_at=?, completed_at=? "
                "WHERE run_id=? AND status NOT IN ('completed','failed','cancelled')",
                (str(reason), now, now, run_id),
            )
            self._connection.execute(
                "UPDATE steps SET status='cancelled', error=?, updated_at=?, completed_at=? "
                "WHERE run_id=? AND status NOT IN ('completed','failed','cancelled')",
                (str(reason), now, now, run_id),
            )
            self._connection.execute(
                "UPDATE approvals SET status='cancelled', response_json=?, resolved_at=? "
                "WHERE run_id=? AND status='pending'",
                (response, now, run_id),
            )
        self._publish(
            EventType.AGENT_RUN_CANCELLED,
            run_id,
            {"run_id": run_id, "status": "cancelled", "reason": str(reason)},
        )
        return self.get_run(run_id)

    def get_run(self, run_id: str, *, include_graph: bool = False) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        run = _run_dict(row)
        if not include_graph:
            return run
        with self._lock:
            task_rows = self._connection.execute(
                "SELECT * FROM tasks WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()
            approval_rows = self._connection.execute(
                "SELECT * FROM approvals WHERE run_id=? ORDER BY created_at, id", (run_id,)
            ).fetchall()
            checkpoint_rows = self._connection.execute(
                "SELECT * FROM checkpoints WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()
            tasks = []
            for task_row in task_rows:
                task = _task_dict(task_row)
                step_rows = self._connection.execute(
                    "SELECT * FROM steps WHERE task_id=? ORDER BY sequence", (task["id"],)
                ).fetchall()
                steps = []
                for step_row in step_rows:
                    step = _step_dict(step_row)
                    delta_rows = self._connection.execute(
                        "SELECT * FROM deltas WHERE step_id=? ORDER BY sequence", (step["id"],)
                    ).fetchall()
                    step["deltas"] = [_delta_dict(delta_row) for delta_row in delta_rows]
                    steps.append(step)
                task["steps"] = steps
                tasks.append(task)
        run["tasks"] = tasks
        run["approvals"] = [_approval_dict(item) for item in approval_rows]
        run["checkpoints"] = [_checkpoint_dict(item) for item in checkpoint_rows]
        return run

    def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        return _approval_dict(row) if row is not None else None

    def list_runs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            if status:
                rows = self._connection.execute(
                    "SELECT * FROM runs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                    (status, max(0, limit)),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (max(0, limit),)
                ).fetchall()
        return [_run_dict(row) for row in rows]

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def _get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return _task_dict(row) if row is not None else None

    def _get_step(self, step_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM steps WHERE id=?", (step_id,)).fetchone()
        return _step_dict(row) if row is not None else None

    def _get_delta(self, delta_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM deltas WHERE id=?", (delta_id,)).fetchone()
        return _delta_dict(row) if row is not None else None

    def _get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        return _checkpoint_dict(row) if row is not None else None

    def _latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM checkpoints WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return _checkpoint_dict(row) if row is not None else None

    def _pending_approvals(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM approvals WHERE run_id=? AND status='pending' ORDER BY created_at, id",
                (run_id,),
            ).fetchall()
        return [_approval_dict(row) for row in rows]

    def _next_sequence(self, table: str, owner_column: str, owner_id: str) -> int:
        allowed = {
            ("tasks", "run_id"),
            ("steps", "task_id"),
            ("deltas", "step_id"),
            ("checkpoints", "run_id"),
        }
        if (table, owner_column) not in allowed:
            raise ValueError("invalid sequence owner")
        row = self._connection.execute(
            f"SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM {table} WHERE {owner_column}=?",
            (owner_id,),
        ).fetchone()
        return int(row["next_sequence"])

    def _publish(self, event_type: EventType, run_id: str, payload: dict[str, Any]) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(
                event_type,
                payload,
                source="agent_runs",
                correlation_id=run_id,
            )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "objective": row["objective"],
        "status": row["status"],
        "parent_run_id": row["parent_run_id"],
        "metadata": json.loads(row["metadata_json"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "sequence": row["sequence"],
        "title": row["title"],
        "status": row["status"],
        "metadata": json.loads(row["metadata_json"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _step_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "task_id": row["task_id"],
        "sequence": row["sequence"],
        "name": row["name"],
        "kind": row["kind"],
        "status": row["status"],
        "input": json.loads(row["input_json"]),
        "output": json.loads(row["output_json"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def _delta_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "step_id": row["step_id"],
        "sequence": row["sequence"],
        "kind": row["kind"],
        "content": row["content"],
        "metadata": json.loads(row["metadata_json"]),
        "created_at": row["created_at"],
    }


def _approval_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "step_id": row["step_id"],
        "action": row["action"],
        "status": row["status"],
        "request": json.loads(row["request_json"]),
        "response": json.loads(row["response_json"]),
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }


def _checkpoint_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "sequence": row["sequence"],
        "state": json.loads(row["state_json"]),
        "created_at": row["created_at"],
    }
