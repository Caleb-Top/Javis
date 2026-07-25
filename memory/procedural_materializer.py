"""Materialize approved procedural memory candidates into local workflows."""

from __future__ import annotations

import json
import re
import time
import hashlib
from pathlib import Path
from typing import Any


class ProceduralMemoryMaterializer:
    """Turns reviewed procedural candidates into reusable workflow JSON files."""

    def __init__(self, store: Any, output_dir: str | Path):
        self.store = store
        self.output_dir = Path(output_dir)

    def materialize(self, limit: int = 100) -> dict[str, int]:
        candidates = getattr(self.store, "memory_candidates", None)
        if not callable(candidates):
            return {"procedural": 0}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for candidate in candidates(kind="procedural", status="active", limit=limit):
            workflow = self._candidate_to_workflow(candidate)
            if not workflow:
                continue
            path = self.output_dir / f"{workflow['id']}.json"
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing.get("workflow_hash") == workflow.get("workflow_hash"):
                    continue
                workflow["version"] = int(existing.get("version", 1)) + 1
                workflow["supersedes"] = existing.get("id", workflow["id"])
                path = self.output_dir / f"{workflow['id']}_v{workflow['version']}.json"
            path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
            count += 1
        return {"procedural": count}

    def _candidate_to_workflow(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        content = str(candidate.get("content", ""))
        match = re.match(r"Successful tool path:\s*(.+?)\s*->\s*(.+)$", content)
        if not match:
            return None
        task = match.group(1).strip()
        tool = match.group(2).strip()
        if not task or not tool:
            return None
        evidence = self._load_evidence(candidate.get("evidence_ids", []))
        params = self._first_params(evidence)
        candidate_id = candidate["candidate_id"]
        workflow_id = f"wft_{self._slug(task)}_{self._slug(tool)}_{candidate_id[:8]}"
        workflow_hash = self._workflow_hash(task, tool, params)
        return {
            "id": workflow_id,
            "name": f"{task} via {tool}",
            "description": f"Materialized from approved procedural memory {candidate_id}",
            "type": "workflow",
            "version": 1,
            "workflow_hash": workflow_hash,
            "source": "procedural_memory",
            "source_candidate_id": candidate_id,
            "steps": [
                {
                    "tool": tool,
                    "params": params,
                    "expected": "success",
                    "recorded_at": time.time(),
                }
            ],
            "created_at": int(time.time()),
            "usage_count": 0,
            "evidence_ids": candidate.get("evidence_ids", []),
        }

    def _load_evidence(self, evidence_ids: list[str]) -> list[dict[str, Any]]:
        events = getattr(self.store, "recent_events", None)
        if not callable(events) or not evidence_ids:
            return []
        evidence_set = set(evidence_ids)
        return [event for event in events(limit=500) if event.get("event_id") in evidence_set]

    @staticmethod
    def _first_params(events: list[dict[str, Any]]) -> dict[str, Any]:
        for event in events:
            payload = event.get("payload", {})
            params = payload.get("params")
            if isinstance(params, dict):
                return params
        return {}

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower()).strip("_")
        return slug[:48] or "workflow"

    @staticmethod
    def _workflow_hash(task: str, tool: str, params: dict[str, Any]) -> str:
        payload = json.dumps(
            {"task": task, "tool": tool, "params": params},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
