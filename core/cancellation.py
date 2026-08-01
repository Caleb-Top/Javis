"""Cooperative cancellation primitives for interruptible Javis requests."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, Awaitable


class RequestCancelled(Exception):
    """Raised at a safe request cancellation boundary."""

    def __init__(self, reason: str = "cancelled"):
        self.reason = str(reason or "cancelled")
        super().__init__(self.reason)


class CancellationToken:
    """Signals cancellation without force-killing an active system operation."""

    def __init__(self):
        self._event = asyncio.Event()
        self.reason = ""

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "cancelled") -> None:
        if self._event.is_set():
            return
        self.reason = str(reason or "cancelled")
        self._event.set()

    async def wait(self) -> str:
        await self._event.wait()
        return self.reason

    async def checkpoint(self) -> None:
        if self._event.is_set():
            raise RequestCancelled(self.reason)

    async def race(self, awaitable: Awaitable[Any]) -> Any:
        operation = asyncio.ensure_future(awaitable)
        cancellation = asyncio.create_task(self._event.wait())
        try:
            done, _ = await asyncio.wait(
                {operation, cancellation},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation in done:
                operation.cancel()
                with suppress(asyncio.CancelledError):
                    await operation
                raise RequestCancelled(self.reason)
            cancellation.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation
            return await operation
        finally:
            if not cancellation.done():
                cancellation.cancel()

