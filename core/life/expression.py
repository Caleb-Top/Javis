"""Stable LifeSnapshot to ExpressionIntent v1 projection."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    ExpressionBaseState,
    ExpressionIntent,
    GazeTarget,
    LifeSnapshot,
    VoiceActivity,
)


BASE_STATE_BY_ACTIVITY = {
    "quiet": ExpressionBaseState.IDLE,
    "attention": ExpressionBaseState.ATTENTION,
    "listening": ExpressionBaseState.LISTENING,
    "thinking": ExpressionBaseState.THINKING,
    "speaking": ExpressionBaseState.SPEAKING,
    "executing": ExpressionBaseState.EXECUTING,
    "blocked": ExpressionBaseState.BLOCKED,
    "error": ExpressionBaseState.ERROR,
    "offline": ExpressionBaseState.OFFLINE,
}

_DEFAULT_GAZE = {
    ExpressionBaseState.IDLE: GazeTarget.NONE,
    ExpressionBaseState.ATTENTION: GazeTarget.USER,
    ExpressionBaseState.LISTENING: GazeTarget.USER,
    ExpressionBaseState.THINKING: GazeTarget.CONTENT,
    ExpressionBaseState.SPEAKING: GazeTarget.USER,
    ExpressionBaseState.EXECUTING: GazeTarget.TASK,
    ExpressionBaseState.BLOCKED: GazeTarget.TASK,
    ExpressionBaseState.ERROR: GazeTarget.NONE,
    ExpressionBaseState.OFFLINE: GazeTarget.NONE,
}
_DEFAULT_VOICE = {
    ExpressionBaseState.IDLE: VoiceActivity.SILENT,
    ExpressionBaseState.ATTENTION: VoiceActivity.SILENT,
    ExpressionBaseState.LISTENING: VoiceActivity.LISTENING,
    ExpressionBaseState.THINKING: VoiceActivity.SILENT,
    ExpressionBaseState.SPEAKING: VoiceActivity.SPEAKING,
    ExpressionBaseState.EXECUTING: VoiceActivity.SILENT,
    ExpressionBaseState.BLOCKED: VoiceActivity.SILENT,
    ExpressionBaseState.ERROR: VoiceActivity.SILENT,
    ExpressionBaseState.OFFLINE: VoiceActivity.SILENT,
}
_DEFAULT_INTENSITY = {
    ExpressionBaseState.IDLE: 0.15,
    ExpressionBaseState.ATTENTION: 0.45,
    ExpressionBaseState.LISTENING: 0.55,
    ExpressionBaseState.THINKING: 0.50,
    ExpressionBaseState.SPEAKING: 0.65,
    ExpressionBaseState.EXECUTING: 0.55,
    ExpressionBaseState.BLOCKED: 0.60,
    ExpressionBaseState.ERROR: 0.75,
    ExpressionBaseState.OFFLINE: 0.20,
}
_TRANSITION_MS = {
    ExpressionBaseState.IDLE: 300,
    ExpressionBaseState.ATTENTION: 180,
    ExpressionBaseState.LISTENING: 100,
    ExpressionBaseState.THINKING: 220,
    ExpressionBaseState.SPEAKING: 80,
    ExpressionBaseState.EXECUTING: 160,
    ExpressionBaseState.BLOCKED: 100,
    ExpressionBaseState.ERROR: 0,
    ExpressionBaseState.OFFLINE: 0,
}
_EXPIRY_SECONDS = {
    ExpressionBaseState.IDLE: 15.0,
    ExpressionBaseState.OFFLINE: 10.0,
}
_INTERRUPTING_STATES = frozenset(
    {
        ExpressionBaseState.LISTENING,
        ExpressionBaseState.ERROR,
        ExpressionBaseState.OFFLINE,
    }
)


class StaleSnapshotError(ValueError):
    """Raised when a projector receives an older snapshot revision."""


def _epoch(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("now must be a finite epoch number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("now must be a finite epoch number")
    return numeric


def _timestamp(value: float) -> str:
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError("now is outside the supported epoch range") from exc


def _enum_override(value: Any, enum_type: type, field_name: str):
    if value is None:
        return None
    if isinstance(value, enum_type):
        return value
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a valid wire string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} has an unsupported value: {value!r}") from exc


def _boolean_override(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _text_override(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value or len(value) > 128:
        raise ValueError(f"{field_name} must be a bounded non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


class ExpressionProjector:
    """Stateful monotonic projector; it never writes back to life state."""

    def __init__(self) -> None:
        self._revision = 0
        self._last_snapshot_revision = -1
        self._last_generated_epoch = float("-inf")

    def project(
        self,
        snapshot: LifeSnapshot,
        now: float,
        *,
        intensity: float | None = None,
        base_state: ExpressionBaseState | str | None = None,
        gaze_target: GazeTarget | str | None = None,
        voice_activity: VoiceActivity | str | None = None,
        interrupt: bool | None = None,
        explanation_code: str | None = None,
    ) -> ExpressionIntent:
        if not isinstance(snapshot, LifeSnapshot):
            raise ValueError("snapshot must be a LifeSnapshot")
        if snapshot.revision < self._last_snapshot_revision:
            raise StaleSnapshotError(
                "stale snapshot revision cannot replace a newer expression"
            )
        resolved_base_state = _enum_override(
            base_state,
            ExpressionBaseState,
            "base_state",
        )
        base_state = resolved_base_state or BASE_STATE_BY_ACTIVITY.get(snapshot.activity)
        if base_state is None:
            raise ValueError(f"unsupported expression activity: {snapshot.activity!r}")
        generated_epoch = max(_epoch(now), self._last_generated_epoch)
        if intensity is None:
            bounded_intensity = _DEFAULT_INTENSITY[base_state]
        else:
            if isinstance(intensity, bool) or not isinstance(intensity, (int, float)):
                raise ValueError("intensity must be a finite number")
            numeric_intensity = float(intensity)
            if not math.isfinite(numeric_intensity):
                raise ValueError("intensity must be a finite number")
            bounded_intensity = min(1.0, max(0.0, numeric_intensity))
        resolved_gaze = _enum_override(gaze_target, GazeTarget, "gaze_target")
        resolved_voice = _enum_override(
            voice_activity,
            VoiceActivity,
            "voice_activity",
        )
        resolved_interrupt = _boolean_override(interrupt, "interrupt")
        resolved_explanation_code = _text_override(
            explanation_code,
            "explanation_code",
        )
        self._revision += 1
        self._last_snapshot_revision = snapshot.revision
        self._last_generated_epoch = generated_epoch
        expiry = generated_epoch + _EXPIRY_SECONDS.get(base_state, 5.0)
        return ExpressionIntent(
            schema_version=1,
            revision=self._revision,
            base_state=base_state,
            intensity=bounded_intensity,
            gaze_target=resolved_gaze or _DEFAULT_GAZE[base_state],
            voice_activity=resolved_voice or _DEFAULT_VOICE[base_state],
            transition_ms=_TRANSITION_MS[base_state],
            interrupt=(
                resolved_interrupt
                if resolved_interrupt is not None
                else base_state in _INTERRUPTING_STATES
            ),
            source_snapshot_revision=snapshot.revision,
            generated_at=_timestamp(generated_epoch),
            expires_at=_timestamp(expiry),
            explanation_code=resolved_explanation_code or base_state.value,
        )


__all__ = [
    "BASE_STATE_BY_ACTIVITY",
    "ExpressionProjector",
    "StaleSnapshotError",
]
