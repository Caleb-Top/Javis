"""Loopback-only fault injection for conversation watchdog verification."""

from __future__ import annotations

import asyncio
import ipaddress
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator


NO_ACK_TRIGGER = "/__javis_stall__/no-ack"
AFTER_ACK_TRIGGER = "/__javis_stall__/after-ack"
AFTER_DELTA_TRIGGER = "/__javis_stall__/after-delta"


class StallMode(str, Enum):
    NO_ACK = "no-ack"
    AFTER_ACK = "after-ack"
    AFTER_DELTA = "after-delta"


_TRIGGERS = {
    NO_ACK_TRIGGER: StallMode.NO_ACK,
    AFTER_ACK_TRIGGER: StallMode.AFTER_ACK,
    AFTER_DELTA_TRIGGER: StallMode.AFTER_DELTA,
}


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_loopback_client(ws: Any) -> bool:
    client = getattr(ws, "client", None)
    host = str(getattr(client, "host", "") or "").strip()
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


@dataclass(frozen=True)
class ConversationStallHarness:
    """Selects deterministic stalls only when explicitly enabled for local tests."""

    enabled: bool = False

    @classmethod
    def from_environment(cls) -> "ConversationStallHarness":
        return cls(
            enabled=_env_flag("JAVIS_TEST_MODE")
            and _env_flag("JAVIS_STALL_HARNESS")
        )

    def mode_for(self, ws: Any, text: str) -> StallMode | None:
        if not self.enabled or not _is_loopback_client(ws):
            return None
        return _TRIGGERS.get(str(text or "").strip())

    async def run(
        self,
        mode: StallMode,
        token: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if mode is StallMode.NO_ACK:
            raise ValueError("no-ack stalls must be intercepted before submission")
        if mode is StallMode.AFTER_DELTA:
            yield {
                "type": "text_delta",
                "text": "[stall harness] first response",
            }
        await token.race(asyncio.Event().wait())
