"""Runtime subsystem wrapper for self-evolution candidate discovery."""

from __future__ import annotations

from typing import Any

from core.subsystem import SubsystemStatus
from evolution.engine import EvolutionCandidateEngine


class EvolutionService:
    name = "evolution"

    def __init__(self, min_successes: int = 3):
        self.min_successes = min_successes
        self.runtime: Any | None = None

    def start(self, runtime: Any) -> None:
        self.runtime = runtime

    def review(self, limit: int = 500) -> dict[str, int]:
        store = getattr(self.runtime, "event_store", None) if self.runtime else None
        if store is None:
            return {"workflow": 0}
        return EvolutionCandidateEngine(store, min_successes=self.min_successes).review(limit=limit)

    def status(self) -> SubsystemStatus:
        store = getattr(self.runtime, "event_store", None) if self.runtime else None
        if store is None:
            return SubsystemStatus(state="degraded", detail="event store unavailable")
        status = getattr(store, "status", None)
        metrics = status() if callable(status) else {}
        return SubsystemStatus(
            state="running",
            detail="candidate discovery ready",
            metrics={
                "min_successes": self.min_successes,
                "evolution_candidates": metrics.get("evolution_candidates", 0),
            },
        )
