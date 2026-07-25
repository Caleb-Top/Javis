"""Apply approved memory candidates into long-term runtime memory."""

from __future__ import annotations

from typing import Any


class ActiveMemoryApplier:
    """Moves reviewed active candidates into Brain-facing memory.

    The applier intentionally ignores candidates that are still in review. Only
    explicit `active` candidates are eligible.
    """

    def __init__(self, runtime: Any):
        self.runtime = runtime

    def apply(self, limit: int = 100) -> dict[str, int]:
        store = getattr(self.runtime, "event_store", None)
        brain = getattr(self.runtime, "brain", None)
        candidates = getattr(store, "memory_candidates", None)
        if not callable(candidates) or brain is None:
            return {"semantic": 0, "procedural": 0}

        result = {"semantic": 0, "procedural": 0}
        for candidate in candidates(kind="semantic", status="active", limit=limit):
            content = str(candidate.get("content", "")).strip()
            if not content:
                continue
            brain.learn_fact(
                content,
                category="memory.semantic.approved",
                source=f"candidate:{candidate.get('candidate_id', '')}",
                priority=max(3, int(float(candidate.get("confidence", 0.5)) * 5)),
            )
            result["semantic"] += 1
            self._publish_applied(candidate, "semantic")

        return result

    def _publish_applied(self, candidate: dict[str, Any], kind: str) -> None:
        event_bus = getattr(self.runtime, "event_bus", None)
        publish = getattr(event_bus, "publish", None)
        if callable(publish):
            publish(
                "memory.candidate.applied",
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "kind": kind,
                    "content": candidate.get("content"),
                },
                source="memory",
            )
