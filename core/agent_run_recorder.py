"""Maps the existing Agent message stream into a durable AgentRunStore graph."""

from __future__ import annotations

from typing import Any

from core.agent_runs import AgentRunStateError, AgentRunStore, FINAL_RUN_STATES


class AgentRunRecorder:
    def __init__(
        self,
        store: AgentRunStore,
        objective: str,
        *,
        session_id: str = "",
        interaction_mode: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        run_metadata = {
            "session_id": session_id,
            "interaction_mode": interaction_mode,
            **(metadata or {}),
        }
        run = store.create_run(objective, metadata=run_metadata)
        task = store.add_task(run["id"], "Agent response")
        step = store.start_step(run["id"], task["id"], "conversation", kind="conversation")
        self.store = store
        self.run_id = run["id"]
        self.task_id = task["id"]
        self.conversation_step_id = step["id"]
        self._tool_steps: list[tuple[str, str]] = []
        self._pending_approval_id: str | None = None
        self._finalized = False

    @property
    def pending_approval_id(self) -> str | None:
        return self._pending_approval_id

    def record(self, message: dict[str, Any]) -> dict[str, Any]:
        if self._finalized:
            return message
        message_type = str(message.get("type", ""))
        if message_type in {"thinking", "text_delta"}:
            content = message.get("content") if message_type == "thinking" else message.get("text")
            self._append_conversation_delta(message_type, str(content or ""))
        elif message_type == "tool_start":
            tool_name = str(message.get("tool", "unknown"))
            step = self.store.start_step(
                self.run_id,
                self.task_id,
                tool_name,
                kind="tool",
                input_data={"params": message.get("params", {})},
            )
            self._tool_steps.append((tool_name, step["id"]))
        elif message_type == "confirm_required":
            tool_name = str(message.get("tool", "unknown"))
            step_id = self._latest_tool_step(tool_name)
            approval = self.store.request_approval(
                self.run_id,
                step_id,
                action=tool_name,
                request={
                    "reason": message.get("reason", ""),
                    "params": message.get("params", {}),
                },
            )
            self._pending_approval_id = approval["id"]
        elif message_type == "tool_result":
            self._record_tool_result(message)
        elif message_type == "error":
            self._append_conversation_delta("error", str(message.get("message", "")))
            self.finalize("failed", error=str(message.get("message", "")))
        elif message_type == "done":
            success = message.get("success") is not False
            self.finalize(
                "completed" if success else "failed",
                error="" if success else str(message.get("detail") or "request failed"),
            )
        return message

    def resolve_confirmation(
        self,
        confirmed: bool,
        *,
        response: dict[str, Any] | None = None,
    ) -> None:
        if self._pending_approval_id is None:
            return
        self.store.resolve_approval(
            self._pending_approval_id,
            approved=bool(confirmed),
            response=response or {},
        )
        self._pending_approval_id = None

    def finalize(self, status: str, *, error: str = "") -> None:
        if self._finalized:
            return
        if self._pending_approval_id is not None:
            self.resolve_confirmation(False, response={"reason": "stream ended before approval"})
            status = "failed"
        graph = self.store.get_run(self.run_id, include_graph=True)
        if graph is None:
            self._finalized = True
            return
        final_step_status = "completed" if status == "completed" else "failed"
        for task in graph.get("tasks", []):
            for step in task.get("steps", []):
                if step["status"] in {"running", "waiting_approval"}:
                    self.store.finish_step(
                        step["id"],
                        status=final_step_status,
                        error=error,
                    )
            if task["status"] in {"pending", "running"}:
                self.store.finish_task(
                    task["id"],
                    status=final_step_status,
                    error=error,
                )
        refreshed_run = self.store.get_run(self.run_id)
        if refreshed_run and refreshed_run["status"] not in FINAL_RUN_STATES:
            self.store.finish_run(
                self.run_id,
                status="completed" if status == "completed" else "failed",
                error=error,
            )
        self._finalized = True

    def cancel(self, reason: str = "stream cancelled") -> None:
        if self._finalized:
            return
        run = self.store.get_run(self.run_id)
        if run and run["status"] not in FINAL_RUN_STATES:
            self.store.cancel_run(self.run_id, reason=reason)
        self._finalized = True

    def _append_conversation_delta(self, kind: str, content: str) -> None:
        if not content:
            return
        try:
            self.store.append_delta(
                self.run_id,
                self.conversation_step_id,
                kind,
                content,
            )
        except AgentRunStateError:
            return

    def _latest_tool_step(self, tool_name: str) -> str | None:
        for name, step_id in reversed(self._tool_steps):
            if name == tool_name:
                return step_id
        return None

    def _record_tool_result(self, message: dict[str, Any]) -> None:
        tool_name = str(message.get("tool", "unknown"))
        step_id = self._latest_tool_step(tool_name)
        if step_id is None:
            return
        success = bool(message.get("success", False))
        data = str(message.get("data", ""))
        try:
            self.store.append_delta(self.run_id, step_id, "tool_result", data)
            self.store.finish_step(
                step_id,
                status="completed" if success else "failed",
                output_data={"data": data[:2000]},
                error="" if success else data[:500],
            )
        except AgentRunStateError:
            return
