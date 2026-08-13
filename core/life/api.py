"""Read-only HTTP and conversation push surfaces for the life kernel."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Query


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
            for session_id in self.conversation_hub.subscribed_sessions():
                for event_type, payload in (
                    ("life.snapshot", snapshot_wire),
                    ("life.expression", expression_wire),
                ):
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
