"""Read-only HTTP and conversation push surfaces for the life kernel."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Query


logger = logging.getLogger("jarvis.life.api")

_SECRET_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "password",
        "secret",
        "token",
    }
)


def _is_secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    compact = normalized.replace("_", "")
    return any(
        part in normalized or part.replace("_", "") in compact
        for part in _SECRET_KEY_PARTS
    )


def _redact_for_api(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _redact_for_api(item)
            for key, item in value.items()
            if isinstance(key, str) and not _is_secret_key(key)
        }
    if isinstance(value, (list, tuple)):
        return [_redact_for_api(item) for item in value]
    return value


def _wire_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise TypeError("life API surface must be a mapping")
    return {str(key): item for key, item in value.items()}


def _snapshot_revision(life: Any) -> int:
    wire = _wire_mapping(life.snapshot())
    revision = wire.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise TypeError("life snapshot revision must be an integer")
    return revision


def _inner_state_wire(
    life: Any,
    *,
    snapshot_revision: int | None = None,
) -> dict[str, Any] | None:
    for name in ("inner_state_snapshot", "inner_state"):
        if not hasattr(life, name):
            continue
        candidate = getattr(life, name)
        try:
            surface = candidate() if callable(candidate) else candidate
        except RuntimeError:
            return None
        if surface is None:
            return None
        wire = _redact_for_api(_wire_mapping(surface))
        if snapshot_revision is not None:
            if wire.get("source_life_snapshot_revision") != snapshot_revision:
                return None
        return wire
    return None


def create_life_router(life: Any) -> APIRouter:
    """Build the L0 read-only router around one runtime-owned LifeService."""

    router = APIRouter(prefix="/api/life", tags=["life"])

    @router.get("/identity")
    async def identity() -> dict[str, Any]:
        return {"ok": True, "identity": life.identity_summary().to_dict()}

    @router.get("/snapshot")
    async def snapshot() -> dict[str, Any]:
        return {"ok": True, "snapshot": life.snapshot().to_dict()}

    @router.get("/lineage")
    async def lineage() -> dict[str, Any]:
        return {"ok": True, "lineage": life.lineage_summary().to_dict()}

    @router.get("/inner-state")
    async def inner_state() -> dict[str, Any]:
        wire = _inner_state_wire(
            life,
            snapshot_revision=_snapshot_revision(life),
        )
        if wire is None:
            raise HTTPException(status_code=503, detail="life inner state unavailable")
        return {"ok": True, "inner_state": wire}

    @router.get("/events")
    async def events(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        recent = _redact_for_api(life.recent_events(limit=limit))
        return {
            "ok": True,
            "cursor": int(life.status().get("journal_cursor", 0)),
            "events": recent,
        }

    return router


class LifeSessionPublisher:
    """Move synchronous LifeService changes onto the owning asyncio loop."""

    def __init__(self, life: Any, conversation_hub: Any) -> None:
        self.life = life
        self.conversation_hub = conversation_hub
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe = None
        self._latest: tuple[Any, Any] | None = None
        self._scheduled = False
        self._active = False
        self._tasks: set[asyncio.Task] = set()

    def start(self, loop: asyncio.AbstractEventLoop) -> bool:
        if loop.is_closed() or not loop.is_running():
            raise RuntimeError("LifeSessionPublisher requires a running event loop")
        with self._lock:
            if self._active:
                return False
            self._loop = loop
            self._active = True
            self._unsubscribe = self.life.subscribe(self._on_change)
            return True

    async def stop(self) -> None:
        with self._lock:
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
            self._active = False
            self._latest = None
            tasks = tuple(self._tasks)
        if unsubscribe is not None:
            unsubscribe()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        with self._lock:
            self._tasks.clear()
            self._scheduled = False
            self._loop = None

    def _on_change(self, snapshot: Any, expression: Any) -> None:
        with self._lock:
            if not self._active or self._loop is None:
                return
            self._latest = (snapshot, expression)
            if self._scheduled:
                return
            self._scheduled = True
            loop = self._loop
        try:
            loop.call_soon_threadsafe(self._start_drain)
        except RuntimeError:
            with self._lock:
                self._scheduled = False

    def _start_drain(self) -> None:
        with self._lock:
            if not self._active or self._loop is None:
                self._scheduled = False
                return
            task = self._loop.create_task(self._drain())
            self._tasks.add(task)
        task.add_done_callback(self._drain_finished)

    def _drain_finished(self, task: asyncio.Task) -> None:
        with self._lock:
            self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("Life session publication failed: %s", task.exception())

    async def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._active:
                    self._scheduled = False
                    return
                latest = self._latest
                self._latest = None
                if latest is None:
                    self._scheduled = False
                    return
            snapshot, expression = latest
            snapshot_wire = snapshot.to_dict()
            expression_wire = expression.to_dict()
            inner_state_wire = _inner_state_wire(
                self.life,
                snapshot_revision=snapshot_wire.get("revision"),
            )
            for session_id in self.conversation_hub.subscribed_sessions():
                events = [
                    ("life.snapshot", snapshot_wire),
                    ("life.expression", expression_wire),
                ]
                if inner_state_wire is not None:
                    events.append(("life.inner_state.changed", inner_state_wire))
                for event_type, payload in events:
                    try:
                        await self.conversation_hub.publish_system_event(
                            session_id,
                            event_type,
                            payload,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Life event %s publication failed for %s: %s",
                            event_type,
                            session_id,
                            exc,
                        )


__all__ = ["LifeSessionPublisher", "create_life_router"]
