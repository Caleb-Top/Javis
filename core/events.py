"""Structured in-process event bus for Javis subsystems."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


logger = logging.getLogger("jarvis.events")


class EventType(str, Enum):
    """Stable event names shared by the Javis runtime and its surfaces."""

    RUNTIME_CREATED = "runtime.created"
    RUNTIME_STATUS = "runtime.status"
    SUBSYSTEM_REGISTERED = "subsystem.registered"
    MODEL_STARTED = "model.started"
    MODEL_COMPLETED = "model.completed"
    MODEL_FAILED = "model.failed"
    THINKING_STARTED = "thinking.started"
    THINKING_DELTA = "thinking.delta"
    THINKING_COMPLETED = "thinking.completed"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    AGENT_RUN_CREATED = "agent_run.created"
    AGENT_RUN_UPDATED = "agent_run.updated"
    AGENT_RUN_COMPLETED = "agent_run.completed"
    AGENT_RUN_FAILED = "agent_run.failed"
    AGENT_RUN_CANCELLED = "agent_run.cancelled"
    AGENT_TASK_CREATED = "agent_task.created"
    AGENT_TASK_UPDATED = "agent_task.updated"
    AGENT_STEP_CREATED = "agent_step.created"
    AGENT_STEP_UPDATED = "agent_step.updated"
    AGENT_DELTA_APPENDED = "agent_delta.appended"
    AGENT_CHECKPOINT_CREATED = "agent_checkpoint.created"
    MEMORY_WRITTEN = "memory.written"
    MEMORY_RECALLED = "memory.recalled"


def _event_type_value(event_type: str | EventType) -> str:
    return event_type.value if isinstance(event_type, EventType) else str(event_type)


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    timestamp: float = field(default_factory=time.time)
    schema_version: int = 1
    correlation_id: str | None = None
    causation_id: str | None = None
    sequence: int = 0


class EventBus:
    """Small synchronous event bus with bounded history."""

    def __init__(self, max_history: int = 200):
        self._history: deque[Event] = deque(maxlen=max_history)
        self._handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)
        self._sequence = 0
        self._lock = threading.RLock()

    def subscribe(self, event_type: str | EventType, handler: Callable[[Event], None]) -> None:
        with self._lock:
            self._handlers[_event_type_value(event_type)].append(handler)

    def publish(
        self,
        event_type: str | EventType,
        payload: dict[str, Any] | None = None,
        source: str = "system",
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        schema_version: int = 1,
    ) -> Event:
        event_type_value = _event_type_value(event_type)
        with self._lock:
            self._sequence += 1
            event = Event(
                id=uuid.uuid4().hex,
                type=event_type_value,
                payload=dict(payload or {}),
                source=source,
                schema_version=schema_version,
                correlation_id=correlation_id,
                causation_id=causation_id,
                sequence=self._sequence,
            )
            self._history.append(event)
            typed_handlers = list(self._handlers.get(event_type_value, []))
            wildcard_handlers = list(self._handlers.get("*", []))
        for handler in typed_handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.warning("Event handler failed for %s: %s", event_type_value, exc)
        for handler in wildcard_handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.warning("Wildcard event handler failed for %s: %s", event_type_value, exc)
        return event

    def history(self, event_type: str | EventType | None = None) -> list[Event]:
        with self._lock:
            events = list(self._history)
        if event_type is None:
            return events
        event_type_value = _event_type_value(event_type)
        return [event for event in events if event.type == event_type_value]
