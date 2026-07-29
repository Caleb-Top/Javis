"""Compare the running JARVIS system against the app architecture blueprint."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BlueprintSystemCheck:
    id: str
    name: str
    state: str
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)

    def score(self) -> float:
        weights = {"missing": 0.0, "partial": 0.55, "running": 1.0}
        return weights.get(self.state, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "evidence": self.evidence,
            "gaps": self.gaps,
            "next_actions": self.next_actions,
            "score": self.score(),
        }


class BlueprintAuditor:
    """Produces an app-readable gap report for the five-system JARVIS blueprint."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    def coverage(self) -> dict[str, Any]:
        checks = [
            self._kernel(),
            self._control(),
            self._perception(),
            self._memory(),
            self._evolution(),
        ]
        score = round(sum(check.score() for check in checks) / len(checks), 2)
        next_actions = []
        for check in checks:
            if check.state != "running":
                next_actions.extend(check.next_actions[:1])
        return {
            "ok": True,
            "blueprint": "JARVIS five-system app architecture",
            "score": score,
            "systems": [check.to_dict() for check in checks],
            "next_actions": next_actions,
        }

    def _kernel(self) -> BlueprintSystemCheck:
        evidence = []
        gaps = []
        next_actions = []
        if getattr(self.runtime, "event_bus", None) is not None:
            evidence.append("EventBus is mounted")
        else:
            gaps.append("EventBus missing")
        registry = getattr(self.runtime, "registry", None)
        if registry is not None and getattr(registry, "count", 0) > 0:
            evidence.append(f"Tool registry has {registry.count} tools")
        else:
            gaps.append("Tool registry has no always-on tools")
        if getattr(self.runtime, "agent", None) is not None:
            evidence.append("Agent is owned by JarvisRuntime")
        else:
            gaps.append("Agent is not runtime-owned")
        state = "running" if not gaps else "partial"
        if gaps:
            next_actions.append("Route all app entrypoints through JarvisRuntime")
        return BlueprintSystemCheck("kernel", "Jarvis Core", state, evidence, gaps, next_actions)

    def _control(self) -> BlueprintSystemCheck:
        registry = getattr(self.runtime, "registry", None)
        control = self._subsystem("control")
        evidence = []
        gaps = []
        next_actions = []
        guard = getattr(registry, "guard", None)
        if guard is not None and hasattr(guard, "permission_level"):
            evidence.append(f"Permission rank is {guard.permission_level}")
        else:
            gaps.append("Permission level is not visible from registry guard")
        if hasattr(self.runtime, "sync_permission"):
            evidence.append("Permission changes route through runtime events")
        else:
            gaps.append("Permission changes are not runtime-routed")
        if control is not None and hasattr(control, "recent_tasks"):
            evidence.append("Audited command task runner is mounted")
            status = self._status_dict(control)
            tasks = status.get("metrics", {}).get("tasks", 0)
            evidence.append(f"Command task history has {tasks} tasks")
            if all(hasattr(control, name) for name in ["issue_root_token", "trip_fuse", "reset_fuse"]):
                evidence.append("Root session token and manual fuse controls are available")
            if hasattr(control, "restore_rollback_point"):
                evidence.append("Rollback restore is available for captured recursive text/binary snapshots")
            try:
                from control.command_tasks import _ROLLBACK_STORE_DIRNAME  # noqa: F401

                evidence.append("Durable large-file rollback storage is available")
            except Exception:
                gaps.append("Durable large-file rollback storage is not implemented")
                next_actions.append("Add durable large-file rollback storage")
        else:
            gaps.append("Audited command task runner is not mounted")
            next_actions.append("Replace raw terminal execution with audited command tasks")
        state = "running" if evidence and not gaps else ("partial" if evidence else "missing")
        return BlueprintSystemCheck("control", "Design Control", state, evidence, gaps, next_actions)

    def _perception(self) -> BlueprintSystemCheck:
        service = self._subsystem("perception")
        evidence = []
        gaps = []
        next_actions = []
        if service is not None:
            evidence.append("Perception subsystem is mounted")
            status = self._status_dict(service)
            adapters = status.get("metrics", {}).get("adapters", [])
            if adapters:
                evidence.append(f"Local adapters: {', '.join(adapters)}")
        else:
            gaps.append("Perception subsystem is not mounted")
        if "perception.event" in self._recent_event_types():
            evidence.append("Structured perception events are flowing")
        else:
            evidence.append("No perception event observed yet in this runtime")
        try:
            from perception.video import VideoStreamAnalyzer  # noqa: F401

            evidence.append("Long-video change detection and segmented summary are available")
        except Exception:
            gaps.append("Long-video change detection and segmented summary are not implemented")
        try:
            from perception.adapters.vlm import LocalVlmAdapter  # noqa: F401

            evidence.append("Local VLM adapter gate is available for MiniCPM/Qwen-style backends")
        except Exception:
            gaps.append("Local VLM adapter gate is not implemented")
            next_actions.append("Add MiniCPM/Qwen local VLM adapter gate")
        if service is not None:
            adapter_status = status.get("metrics", {}).get("adapters", {})
            vlm_status = adapter_status.get("vlm", {}) if isinstance(adapter_status, dict) else {}
            if vlm_status.get("state") == "available":
                evidence.append("Local VLM backend is configured")
            elif vlm_status.get("state") == "not_configured":
                gaps.append("Local VLM backend is not configured yet")
                next_actions.append("Connect MiniCPM/Qwen/Ollama VLM describer to the local adapter")
        state = "running" if evidence and not gaps else ("partial" if evidence else "missing")
        return BlueprintSystemCheck("perception", "Multimodal Perception", state, evidence, gaps, next_actions)

    def _memory(self) -> BlueprintSystemCheck:
        store = getattr(self.runtime, "event_store", None)
        evidence = []
        gaps = []
        next_actions = []
        if store is not None:
            evidence.append("SQLite event store is mounted")
            status = getattr(store, "status", lambda: {})()
            evidence.append(f"Event store has {status.get('events', 0)} events")
            if status.get("candidates", 0) or hasattr(store, "memory_candidates"):
                evidence.append("Memory candidate review table is available")
            if hasattr(store, "recall"):
                evidence.append("FTS-backed explainable recall is available")
        else:
            gaps.append("Session event store is missing")
        try:
            from memory.activation import ActiveMemoryApplier  # noqa: F401

            evidence.append("Active semantic candidates can sync into Brain")
        except Exception:
            gaps.append("Active semantic candidates are not yet synced into long-term semantic memory")
        try:
            from memory.procedural_materializer import ProceduralMemoryMaterializer  # noqa: F401

            evidence.append("Active procedural candidates can materialize into workflow templates")
            if hasattr(ProceduralMemoryMaterializer, "_workflow_hash"):
                evidence.append("Materialized workflows have dedupe hashes and versions")
        except Exception:
            gaps.append("Procedural candidates are not yet materialized as reusable workflows")
        state = "running" if evidence and not gaps else ("partial" if evidence else "missing")
        return BlueprintSystemCheck("memory", "Long-Term Memory", state, evidence, gaps, next_actions)

    def _evolution(self) -> BlueprintSystemCheck:
        service = self._subsystem("evolution")
        evidence = []
        gaps = []
        next_actions = []
        if service is not None:
            evidence.append("Evolution subsystem is mounted")
        else:
            gaps.append("Evolution subsystem is not mounted")
        store = getattr(self.runtime, "event_store", None)
        if store is not None and hasattr(store, "evolution_candidates"):
            evidence.append("Evolution candidate pool is available")
        else:
            gaps.append("Evolution candidate pool is missing")
        if store is not None and hasattr(store, "validate_evolution_candidate"):
            evidence.append("Sandbox/static validation gate blocks staged activation")
        else:
            gaps.append("Sandbox generation/test gate is not implemented")
        if store is not None and hasattr(store, "record_evolution_performance"):
            import inspect

            params = inspect.signature(store.record_evolution_performance).parameters
            evidence.append("Active candidates record performance and rollback on regression")
            if {"quality_score", "max_latency_ms", "min_quality_score"}.issubset(params):
                evidence.append("Regression rollback uses failure, latency, and quality baselines")
            else:
                gaps.append("Regression rollback uses a simple failure-rate window")
                next_actions.append("Add richer quality/latency baselines for evolution rollback")
        else:
            gaps.append("Regression tracking and automatic rollback are not implemented")
            next_actions.append("Add regression tracking and automatic rollback after active candidates")
        state = "running" if evidence and not gaps else ("partial" if evidence else "missing")
        return BlueprintSystemCheck("evolution", "Self-Evolution Engine", state, evidence, gaps, next_actions)

    def _subsystem(self, name: str) -> Any | None:
        return getattr(self.runtime, "subsystems", {}).get(name)

    def _status_dict(self, subsystem: Any) -> dict[str, Any]:
        status = getattr(subsystem, "status", None)
        if not callable(status):
            return {}
        value = status()
        return value.to_dict() if hasattr(value, "to_dict") else value

    def _recent_event_types(self) -> list[str]:
        event_bus = getattr(self.runtime, "event_bus", None)
        if event_bus is None:
            return []
        return [event.type for event in event_bus.history()]
