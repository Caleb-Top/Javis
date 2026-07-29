"""Structured in-process event bus for Javis subsystems."""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable


logger = logging.getLogger("jarvis.events")


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    timestamp: float = field(default_factory=time.time)


class EventBus:
    """Small synchronous event bus with bounded history."""

    def __init__(self, max_history: int = 200):
        self._history: deque[Event] = deque(maxlen=max_history)
        self._handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        self._handlers[event_type].append(handler)

    def publish(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        source: str = "system",
    ) -> Event:
        event = Event(
            id=uuid.uuid4().hex,
            type=event_type,
            payload=payload or {},
            source=source,
        )
        self._history.append(event)
        for handler in list(self._handlers.get(event_type, [])):
            try:
                handler(event)
            except Exception as exc:
                logger.warning("Event handler failed for %s: %s", event_type, exc)
        for handler in list(self._handlers.get("*", [])):
            try:
                handler(event)
            except Exception as exc:
                logger.warning("Wildcard event handler failed for %s: %s", event_type, exc)
        return event

    def history(self, event_type: str | None = None) -> list[Event]:
        events = list(self._history)
        if event_type is None:
            return events
        return [event for event in events if event.type == event_type]
