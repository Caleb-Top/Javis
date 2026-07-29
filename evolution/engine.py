"""Candidate discovery for JARVIS self-evolution.

The engine only proposes reviewable changes. It never writes executable code or
activates a capability by itself.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any


class EvolutionCandidateEngine:
    """Finds repeated high-value paths that may deserve local automation."""

    def __init__(self, store: Any, min_successes: int = 3):
        self.store = store
        self.min_successes = max(2, int(min_successes))

    def review(self, limit: int = 500) -> dict[str, int]:
        recent_events = getattr(self.store, "recent_events", None)
        upsert = getattr(self.store, "upsert_evolution_candidate", None)
        if not callable(recent_events) or not callable(upsert):
            return {"workflow": 0}

        events = recent_events(limit=limit)
        return {"workflow": self._discover_workflow_candidates(events)}

    def _discover_workflow_candidates(self, events: list[dict[str, Any]]) -> int:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            if event["type"] != "tool.completed":
                continue
            payload = event.get("payload", {})
            if payload.get("success") is not True:
                continue
            task = str(payload.get("task", "general")).strip() or "general"
            tool = str(payload.get("tool", "")).strip()
            if not tool:
                continue
            groups[(task, tool)].append(event)

        created = 0
        for (task, tool), grouped_events in groups.items():
            if len(grouped_events) < self.min_successes:
                continue
            evidence_ids = [event["event_id"] for event in grouped_events]
            latencies = [
                float(event.get("payload", {}).get("latency_ms"))
                for event in grouped_events
                if self._is_number(event.get("payload", {}).get("latency_ms"))
            ]
            avg_latency_ms = round(mean(latencies), 2) if latencies else None
            confidence = min(0.95, 0.55 + len(grouped_events) * 0.08)
            title = f"Local workflow candidate: {task} via {tool}"
            description = (
                f"The task '{task}' repeatedly succeeds through '{tool}'. "
                "Consider turning this path into a local script, workflow, or cached routine."
            )
            proposal = {
                "task": task,
                "tool": tool,
                "trigger": "repeated_success",
                "success_count": len(grouped_events),
                "avg_latency_ms": avg_latency_ms,
                "next_step": "sandbox_generate_and_test",
            }
            if self.store.upsert_evolution_candidate(
                kind="workflow",
                title=title,
                description=description,
                evidence_ids=evidence_ids,
                confidence=confidence,
                risk="trusted",
                proposal=proposal,
            ):
                created += 1
        return created

    @staticmethod
    def _is_number(value: Any) -> bool:
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
