"""Read-only quarantine for the retired sleep-learning implementation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_MAX_META_BYTES = 65_536


class LegacySleepQuarantinedError(RuntimeError):
    """Raised when a retired sleep/training mutation is requested."""


@dataclass(frozen=True, slots=True)
class LegacySleepMigrationHint:
    meta_present: bool
    meta_valid: bool
    last_sleep_at_utc: str | None
    marker_ignored: bool
    formal_evidence_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]


class LegacySleepLearningQuarantine:
    """Expose bounded diagnostics without forwarding any legacy mutation."""

    def __init__(
        self,
        *,
        source_root: str | Path,
        data_root: str | Path,
        legacy: Any | None = None,
    ) -> None:
        self.source_root = Path(source_root).expanduser().resolve()
        self.data_root = Path(data_root).expanduser().resolve()
        self._legacy = legacy

    def status(self) -> dict[str, bool | str]:
        legacy = self._legacy
        return {
            "state": "quarantined",
            "monitor_running": bool(getattr(legacy, "_running", False)),
            "is_sleeping": bool(getattr(legacy, "is_sleeping", False)),
            "legacy_state_available": legacy is not None,
        }

    def migration_hint(self) -> LegacySleepMigrationHint:
        meta_path = self.source_root / "brain_data" / "sleep_meta.json"
        marker_path = self.source_root / "brain_data" / "episodes" / ".consolidated"
        meta_present = meta_path.is_file() and not meta_path.is_symlink()
        marker_ignored = marker_path.exists() or marker_path.is_symlink()
        meta_valid = False
        last_sleep_at_utc: str | None = None

        if meta_present:
            try:
                if meta_path.stat().st_size > _MAX_META_BYTES:
                    raise ValueError("legacy sleep metadata exceeds the read limit")
                value = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("legacy sleep metadata must be an object")
                timestamp = value.get("last_sleep_time")
                if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                    raise ValueError("legacy sleep timestamp is invalid")
                timestamp = float(timestamp)
                if not math.isfinite(timestamp) or timestamp < 0:
                    raise ValueError("legacy sleep timestamp is invalid")
                last_sleep_at_utc = (
                    datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")
                )
                meta_valid = True
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError, OverflowError):
                meta_valid = False
                last_sleep_at_utc = None

        reasons: list[str] = []
        if meta_present:
            reasons.append(
                "legacy_sleep_meta_unverified"
                if meta_valid
                else "legacy_sleep_meta_invalid"
            )
        if marker_ignored:
            reasons.append("legacy_marker_ignored")
        return LegacySleepMigrationHint(
            meta_present=meta_present,
            meta_valid=meta_valid,
            last_sleep_at_utc=last_sleep_at_utc,
            marker_ignored=marker_ignored,
            formal_evidence_ids=(),
            reason_codes=tuple(reasons),
        )

    def start(self, *_args: Any, **_kwargs: Any) -> None:
        self._deny()

    def enter_sleep(self, *_args: Any, **_kwargs: Any) -> None:
        self._deny()

    def train(self, *_args: Any, **_kwargs: Any) -> None:
        self._deny()

    def should_sleep(self, *_args: Any, **_kwargs: Any) -> None:
        self._deny()

    @staticmethod
    def _deny() -> None:
        raise LegacySleepQuarantinedError(
            "legacy SleepLearning is read-only and cannot start sleep or training"
        )


__all__ = [
    "LegacySleepLearningQuarantine",
    "LegacySleepMigrationHint",
    "LegacySleepQuarantinedError",
]
