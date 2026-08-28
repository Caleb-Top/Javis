"""Legacy memory tool names kept fail-closed during L2 request-scoped cutover."""

from __future__ import annotations


_UNAVAILABLE = (
    "memory_unavailable: long-term recall requires the server-owned request access context"
)


async def memory_recall(query: str) -> str:
    """Never fall back to the legacy global Brain or episode index."""

    del query
    return _UNAVAILABLE


async def memory_recent() -> str:
    """Recent long-term memory is available only through request-scoped recall."""

    return _UNAVAILABLE


__all__ = ["memory_recall", "memory_recent"]
