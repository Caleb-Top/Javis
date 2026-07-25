"""Consolidate raw session events into reviewable memory candidates."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class EventMemoryConsolidator:
    """Creates candidate memories without directly mutating long-term facts."""

    def __init__(self, store: Any, min_tool_successes: int = 3):
        self.store = store
        self.min_tool_successes = min_tool_successes

    def consolidate(self, limit: int = 500) -> dict[str, int]:
        events = self.store.recent_events(limit=limit)
        result = {"semantic": 0, "procedural": 0}
        result["semantic"] += self._consolidate_semantic(events)
        result["procedural"] += self._consolidate_procedural(events)
        return result

    def _consolidate_semantic(self, events: list[dict[str, Any]]) -> int:
        created = 0
        for event in events:
            if event["type"] != "user.preference":
                continue
            payload = event.get("payload", {})
            key = str(payload.get("key", "")).strip()
            value = str(payload.get("value", "")).strip()
            if not key or not value:
                continue
            content = f"User preference: {key} = {value}"
            if self.store.upsert_memory_candidate(
                kind="semantic",
                content=content,
                evidence_ids=[event["event_id"]],
                confidence=0.75,
            ):
                created += 1
        return created

    def _consolidate_procedural(self, events: list[dict[str, Any]]) -> int:
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
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
            groups[(task, tool)].append(event["event_id"])

        created = 0
        for (task, tool), evidence_ids in groups.items():
            if len(evidence_ids) < self.min_tool_successes:
                continue
            content = f"Successful tool path: {task} -> {tool}"
            confidence = min(0.95, 0.5 + len(evidence_ids) * 0.1)
            if self.store.upsert_memory_candidate(
                kind="procedural",
                content=content,
                evidence_ids=evidence_ids,
                confidence=confidence,
            ):
                created += 1
        return created
