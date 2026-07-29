"""Bounded phase supervisor for the Javis desktop app build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PHASES = (
    "phase-2",
    "phase-3",
    "phase-4",
    "verification",
    "phase-5",
    "release-verification",
    "phase-6",
    "build-readiness-verification",
)
FINAL_PHASES = {"verification", "release-verification", "build-readiness-verification"}
SUCCESS_STATUSES = {"checkpoint", "completed"}
FAILURE_STATUSES = {"failed", "error"}
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)\b(token|password|api[_-]?key|secret)\s*=\s*[^\s,;]+"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redact(value: str) -> str:
    text = str(value or "")[:4000]
    text = _SECRET_PATTERNS[0].sub("[REDACTED]", text)
    text = _SECRET_PATTERNS[1].sub(r"\1[REDACTED]", text)
    return _SECRET_PATTERNS[2].sub(r"\1=[REDACTED]", text)


def _default_log_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Javis" / "logs" / "supervisor"


class BuildSupervisor:
    def __init__(
        self,
        state_path: Path | None = None,
        log_path: Path | None = None,
        max_same_failures: int = 3,
    ) -> None:
        root = _default_log_root()
        self.state_path = Path(state_path or root / "app-phase-build-state.json")
        self.log_path = Path(log_path or root / "app-phase-build.jsonl")
        self.max_same_failures = max(1, int(max_same_failures))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    def _initial_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "run_status": "active",
            "current_phase": None,
            "completed_phases": [],
            "failure_signature": "",
            "failure_count": 0,
            "last_step": "",
            "updated_at": _utc_now(),
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._initial_state()
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            return self._initial_state() | loaded
        except (OSError, ValueError, TypeError):
            state = self._initial_state()
            state["run_status"] = "recovered"
            return state

    def _phase_allowed(self, phase: str) -> bool:
        if phase not in PHASES:
            return False
        index = PHASES.index(phase)
        return index == 0 or PHASES[index - 1] in self._state["completed_phases"]

    def _write_state(self) -> None:
        payload = json.dumps(self._state, ensure_ascii=False, indent=2)
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.state_path)

    def _append_event(self, event: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def record(
        self,
        phase: str,
        step: str,
        status: str,
        detail: str = "",
        signature: str = "",
    ) -> dict[str, Any]:
        phase = str(phase or "").strip().lower()
        step = str(step or "").strip()[:160]
        status = str(status or "").strip().lower()
        detail = _redact(detail)
        if not self._phase_allowed(phase):
            result = {"ok": False, "reason": "phase-order", "phase": phase, "blocked": False}
            self._append_event({"timestamp": _utc_now(), **result, "step": step, "status": status})
            return result

        if self._state["run_status"] == "blocked" and status not in {"reset", "status"}:
            return {"ok": False, "reason": "fuse-blocked", "phase": phase, "blocked": True}

        self._state["current_phase"] = phase
        self._state["last_step"] = step
        self._state["updated_at"] = _utc_now()

        if status in FAILURE_STATUSES:
            fingerprint_source = signature.strip() or f"{phase}:{step}:{detail}"
            fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:16]
            if fingerprint == self._state["failure_signature"]:
                self._state["failure_count"] += 1
            else:
                self._state["failure_signature"] = fingerprint
                self._state["failure_count"] = 1
            if self._state["failure_count"] >= self.max_same_failures:
                self._state["run_status"] = "blocked"
        elif status in SUCCESS_STATUSES or status in {"started", "reset"}:
            self._state["failure_signature"] = ""
            self._state["failure_count"] = 0
            self._state["run_status"] = "active"

        if status == "completed" and phase not in self._state["completed_phases"]:
            self._state["completed_phases"].append(phase)
        if status == "completed" and phase in FINAL_PHASES:
            self._state["run_status"] = "completed"

        self._write_state()
        event = {
            "timestamp": self._state["updated_at"],
            "phase": phase,
            "step": step,
            "status": status,
            "detail": detail,
            "failure_count": self._state["failure_count"],
            "blocked": self._state["run_status"] == "blocked",
        }
        self._append_event(event)
        return {"ok": True, **event}

    def status(self) -> dict[str, Any]:
        return dict(self._state)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supervise staged Javis App implementation and release verification")
    parser.add_argument("action", choices=("start", "checkpoint", "fail", "complete", "status", "reset"))
    parser.add_argument("--phase", choices=PHASES)
    parser.add_argument("--step", default="")
    parser.add_argument("--detail", default="")
    parser.add_argument("--signature", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    supervisor = BuildSupervisor()
    if args.action == "status":
        print(json.dumps(supervisor.status(), ensure_ascii=False, indent=2))
        return 0
    if not args.phase:
        print("--phase is required", file=sys.stderr)
        return 2
    status = {
        "start": "started",
        "checkpoint": "checkpoint",
        "fail": "failed",
        "complete": "completed",
        "reset": "reset",
    }[args.action]
    result = supervisor.record(args.phase, args.step or args.action, status, args.detail, args.signature)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
