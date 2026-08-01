"""Ordered execution middleware shared by Javis runtime operations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, Iterable


class MiddlewareRejected(PermissionError):
    """Raised when middleware intentionally blocks an operation."""


@dataclass
class MiddlewareContext:
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    causation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        raise MiddlewareRejected(reason or "operation rejected")


class Middleware:
    """No-op base class for synchronous runtime middleware."""

    def before(self, context: MiddlewareContext) -> None:
        return None

    def after(self, context: MiddlewareContext, result: Any) -> Any:
        return result

    def on_error(self, context: MiddlewareContext, error: BaseException) -> None:
        return None


class MiddlewarePipeline:
    """Runs middleware as a fail-closed, nested execution pipeline."""

    def __init__(self, middleware: Iterable[Middleware] | None = None):
        self._middleware = list(middleware or ())
        self._lock = RLock()

    @property
    def middleware(self) -> tuple[Middleware, ...]:
        with self._lock:
            return tuple(self._middleware)

    def add(self, middleware: Middleware) -> None:
        with self._lock:
            self._middleware.append(middleware)

    def remove(self, middleware: Middleware) -> bool:
        with self._lock:
            try:
                self._middleware.remove(middleware)
            except ValueError:
                return False
            return True

    def execute(
        self,
        context: MiddlewareContext,
        handler: Callable[[MiddlewareContext], Any],
    ) -> Any:
        chain = self.middleware
        entered: list[Middleware] = []
        try:
            for middleware in chain:
                middleware.before(context)
                entered.append(middleware)
            result = handler(context)
            for middleware in reversed(entered):
                result = middleware.after(context, result)
            return result
        except BaseException as error:
            for middleware in reversed(entered):
                try:
                    middleware.on_error(context, error)
                except Exception:
                    continue
            raise
