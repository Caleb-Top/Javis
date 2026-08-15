"""Pure exact-invocation matching and deterministic presence decisions."""

from __future__ import annotations

import math
import threading
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Final, Literal

from .contracts import InputModality, InputProvenance, InputVerification


PRESENCE_RESPONSE_TEXT: Final = "我在"
DETERMINISTIC_LOCAL_LANE: Final = "deterministic_local"
EXCLUSIVE_LANE: Final = "exclusive"
DEDUPE_WINDOW_MS: Final = 1500.0

_EXACT_ALIASES: Final = frozenset({"javis", "jarvis", "贾维斯"})
_CALL_PUNCTUATION: Final = frozenset(
    ",.!?:;'\"，。！？：；、…~～“”‘’「」『』"
)
_ASCII_LOWER: Final = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)


def normalize_invocation(text: str) -> str:
    """Normalize only the boundary forms governed by the L1 design."""

    if not isinstance(text, str):
        raise TypeError("invocation text must be a string")
    normalized = unicodedata.normalize("NFKC", text).translate(_ASCII_LOWER)
    previous = None
    while normalized != previous:
        previous = normalized
        normalized = normalized.strip()
        normalized = normalized.strip("".join(_CALL_PUNCTUATION))
    return normalized


def is_exact_invocation(text: str) -> bool:
    """Return true only when the whole normalized input is a governed alias."""

    return normalize_invocation(text) in _EXACT_ALIASES


@dataclass(frozen=True)
class PresenceDecision:
    """One local routing decision; it contains no generated model content."""

    exact_invocation: bool
    duplicate: bool
    response_text: str | None
    execution_lane: Literal["exclusive", "deterministic_local"]

    @property
    def matched(self) -> bool:
        return self.exact_invocation

    @property
    def should_respond(self) -> bool:
        return self.exact_invocation and not self.duplicate

    @property
    def response(self) -> str | None:
        return self.response_text

    @property
    def lane(self) -> Literal["exclusive", "deterministic_local"]:
        return self.execution_lane


_Identity = tuple[str, ...]


class PresenceResponder:
    """Classify exact calls and suppress only repeats of the same provenance.

    The caller supplies monotonic time. This component performs no clock, model,
    network, persistence, or playback I/O.
    """

    def __init__(self, *, capacity: int = 4096) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an integer")
        if not 1 <= capacity <= 65536:
            raise ValueError("capacity must be in [1, 65536]")
        self._capacity = capacity
        self._seen: OrderedDict[_Identity, float] = OrderedDict()
        self._lock = threading.RLock()

    def decide(
        self,
        text: str,
        *,
        session_id: str,
        request_id: str,
        now_monotonic_ms: int | float,
        input_provenance: InputProvenance | None = None,
        idempotency_key: str = "",
    ) -> PresenceDecision:
        """Return the normal lane, a local response, or a provenance duplicate."""

        session = _identifier(session_id, "session_id", required=True)
        request = _identifier(request_id, "request_id", required=False)
        idempotency = _identifier(
            idempotency_key, "idempotency_key", required=False
        )
        now = _monotonic_milliseconds(now_monotonic_ms)
        provenance = input_provenance or InputProvenance.unknown()
        if not isinstance(provenance, InputProvenance):
            raise TypeError("input_provenance must be an InputProvenance")

        if not is_exact_invocation(text):
            return PresenceDecision(False, False, None, EXCLUSIVE_LANE)

        identities = _dedupe_identities(
            session,
            request,
            idempotency,
            provenance,
        )
        with self._lock:
            self._purge(now)
            duplicate = any(
                identity in self._seen
                and max(0.0, now - self._seen[identity]) <= DEDUPE_WINDOW_MS
                for identity in identities
            )
            for identity in identities:
                self._seen[identity] = now
                self._seen.move_to_end(identity)
            while len(self._seen) > self._capacity:
                self._seen.popitem(last=False)

        return PresenceDecision(
            exact_invocation=True,
            duplicate=duplicate,
            response_text=None if duplicate else PRESENCE_RESPONSE_TEXT,
            execution_lane=DETERMINISTIC_LOCAL_LANE,
        )

    def _purge(self, now: float) -> None:
        for identity, seen_at in tuple(self._seen.items()):
            if now >= seen_at and now - seen_at > DEDUPE_WINDOW_MS:
                del self._seen[identity]


def _dedupe_identities(
    session_id: str,
    request_id: str,
    idempotency_key: str,
    provenance: InputProvenance,
) -> tuple[_Identity, ...]:
    modality = provenance.modality.value
    prefix = (session_id, modality)
    identities: list[_Identity] = []

    if (
        provenance.modality is InputModality.VOICE
        and provenance.verification is InputVerification.SERVER_VERIFIED
    ):
        if provenance.source_session_id != session_id:
            raise ValueError("verified voice provenance session does not match request session")
        identities.append(
            (
                "voice",
                *prefix,
                provenance.runtime_boot_id or "",
                provenance.source_session_id or "",
                str(provenance.owner_generation),
                str(provenance.voice_sequence),
                str(provenance.voice_turn),
            )
        )

    if request_id:
        identities.append(("request", *prefix, request_id))
    if idempotency_key:
        identities.append(("idempotency", *prefix, idempotency_key))
    return tuple(identities)


def _identifier(value: str, field: str, *, required: bool) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) > 256 or any(
        unicodedata.category(character) == "Cc" for character in normalized
    ):
        raise ValueError(f"{field} must be a bounded identifier")
    return normalized


def _monotonic_milliseconds(value: int | float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("now_monotonic_ms must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError("now_monotonic_ms must be finite and non-negative")
    return normalized


__all__ = [
    "DEDUPE_WINDOW_MS",
    "DETERMINISTIC_LOCAL_LANE",
    "EXCLUSIVE_LANE",
    "PRESENCE_RESPONSE_TEXT",
    "PresenceDecision",
    "PresenceResponder",
    "is_exact_invocation",
    "normalize_invocation",
]
