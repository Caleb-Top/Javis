"""Structured local perception events.

This service is intentionally lightweight. Heavy OCR, VLM, camera, and audio
adapters can feed it later without making the runtime depend on those packages.
"""

from __future__ import annotations

import time
import uuid
import inspect
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from core.subsystem import SubsystemStatus


@dataclass(frozen=True)
class PerceptionEvent:
    id: str
    source: str
    modality: str
    summary: str
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PerceptionService:
    name = "perception"

    def __init__(self, max_events: int = 200):
        self._runtime: Any | None = None
        self._events: deque[PerceptionEvent] = deque(maxlen=max_events)
        self._adapters: dict[str, Any] = {}
        self._state = "stopped"

    def start(self, runtime: Any) -> None:
        self._runtime = runtime
        self._state = "running"
        runtime.event_bus.publish(
            "perception.started",
            {"max_events": self._events.maxlen},
            source=self.name,
        )

    def stop(self) -> None:
        self._state = "stopped"

    def status(self) -> SubsystemStatus:
        last = self._events[-1] if self._events else None
        adapter_status: dict[str, Any] = {}
        for name, adapter in sorted(self._adapters.items()):
            status = getattr(adapter, "status", None)
            if not callable(status):
                adapter_status[name] = {"state": "unknown", "detail": "no status method"}
                continue
            try:
                adapter_status[name] = status()
            except Exception as exc:
                adapter_status[name] = {"state": "degraded", "detail": str(exc)[:200]}
        return SubsystemStatus(
            state=self._state,
            detail="local structured perception event bridge",
            metrics={
                "events": len(self._events),
                "last_source": last.source if last else "",
                "last_modality": last.modality if last else "",
                "adapters": adapter_status,
            },
        )

    def history(self) -> list[PerceptionEvent]:
        return list(self._events)

    def register_adapter(self, adapter: Any) -> None:
        name = getattr(adapter, "name", adapter.__class__.__name__)
        self._adapters[name] = adapter

    def get_adapter(self, name: str) -> Any | None:
        return self._adapters.get(name)

    def analyze_image(
        self,
        image: Any,
        source: str = "image",
        adapter_names: list[str] | None = None,
        adapter_options: dict[str, dict[str, Any]] | None = None,
    ) -> list[PerceptionEvent]:
        names = adapter_names or sorted(self._adapters)
        options = adapter_options or {}
        events: list[PerceptionEvent] = []
        for name in names:
            adapter = self._adapters.get(name)
            if adapter is None:
                continue
            kwargs = options.get(name, {})
            analyze = getattr(adapter, "analyze", None)
            detect = getattr(adapter, "detect", None)
            if callable(analyze):
                event = self._call_adapter(analyze, image=image, perception=self, source=source, **kwargs)
            elif callable(detect):
                event = self._call_adapter(detect, image=image, perception=self, source=source, **kwargs)
            else:
                continue
            if isinstance(event, list):
                events.extend(event)
            elif event is not None:
                events.append(event)
        return events

    @staticmethod
    def _call_adapter(method: Any, **kwargs: Any) -> Any:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return method(**kwargs)
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            return method(**kwargs)
        accepted = {key: value for key, value in kwargs.items() if key in signature.parameters}
        return method(**accepted)

    def ingest(
        self,
        source: str,
        modality: str,
        summary: str,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> PerceptionEvent:
        event = PerceptionEvent(
            id=uuid.uuid4().hex,
            source=(source or "unknown").strip() or "unknown",
            modality=(modality or "unknown").strip() or "unknown",
            summary=(summary or "").strip(),
            confidence=max(0.0, min(1.0, float(confidence))),
            metadata=metadata or {},
        )
        self._events.append(event)
        if self._runtime is not None:
            self._runtime.event_bus.publish(
                "perception.event",
                event.to_dict(),
                source=self.name,
            )
        return event
