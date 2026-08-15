"""Short-lived, single-use provenance for continuous voice transcripts."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.life.l1.contracts import (
    InputModality,
    InputProvenance,
    InputVerification,
)


_PROOF_PATTERN = re.compile(r"[0-9a-f]{64}")
_REFERENCE_FIELDS = frozenset(
    {
        "runtime_boot_id",
        "session_id",
        "owner_generation",
        "voice_sequence",
        "voice_turn",
        "nonce",
        "proof",
    }
)


class VoiceTurnRegistryError(ValueError):
    """Raised when a voice provenance reference cannot be trusted."""


@dataclass
class _Entry:
    transcript: str
    nonce: str
    proof: str
    expires_at: float
    state: str = "available"
    reserved_session_id: str = ""
    reserved_request_id: str = ""


class VoiceTurnRegistry:
    """Binds a backend microphone transcript to one accepted request."""

    def __init__(
        self,
        runtime_boot_id: str,
        *,
        ttl_seconds: float = 30.0,
        capacity: int = 512,
        clock: Callable[[], float] = time.monotonic,
        secret: bytes | None = None,
    ) -> None:
        self.runtime_boot_id = _identifier(runtime_boot_id, "runtime_boot_id")
        self.ttl_seconds = max(0.25, min(float(ttl_seconds), 300.0))
        self.capacity = max(1, min(int(capacity), 4096))
        self._clock = clock
        self._secret = bytes(secret) if secret is not None else secrets.token_bytes(32)
        if len(self._secret) < 32:
            raise ValueError("voice turn registry secret must contain at least 32 bytes")
        self._entries: OrderedDict[tuple[str, str, int, int, int], _Entry] = OrderedDict()
        self._committed: OrderedDict[tuple[str, str, int, int, int], None] = OrderedDict()
        self._lock = threading.RLock()

    def register(
        self,
        *,
        session_id: str,
        owner_generation: int,
        voice_sequence: int,
        voice_turn: int,
        transcript: str,
    ) -> dict[str, Any]:
        session = _identifier(session_id, "session_id")
        generation = _non_negative_int(owner_generation, "owner_generation")
        sequence = _non_negative_int(voice_sequence, "voice_sequence")
        turn = _non_negative_int(voice_turn, "voice_turn")
        normalized_transcript = _normalize_transcript(transcript)
        key = (self.runtime_boot_id, session, generation, sequence, turn)
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            if key in self._committed:
                raise VoiceTurnRegistryError("voice turn was already committed")
            existing = self._entries.get(key)
            if existing is not None:
                if not _constant_text_equal(existing.transcript, normalized_transcript):
                    raise VoiceTurnRegistryError("voice transcript does not match its turn")
                self._entries.move_to_end(key)
                return self._reference(key, existing)
            self._make_room()
            nonce = secrets.token_urlsafe(24)
            proof = self._proof(key, nonce, normalized_transcript)
            entry = _Entry(
                transcript=normalized_transcript,
                nonce=nonce,
                proof=proof,
                expires_at=now + self.ttl_seconds,
            )
            self._entries[key] = entry
            return self._reference(key, entry)

    def reserve(
        self,
        reference: Mapping[str, Any],
        *,
        transcript: str,
        session_id: str,
        request_id: str,
    ) -> InputProvenance:
        normalized, key = self._normalize_reference(reference)
        session = _identifier(session_id, "session_id")
        request = _identifier(request_id, "request_id")
        if normalized["runtime_boot_id"] != self.runtime_boot_id:
            raise VoiceTurnRegistryError("voice provenance belongs to another runtime boot")
        if normalized["session_id"] != session:
            raise VoiceTurnRegistryError("voice provenance belongs to another session")
        normalized_transcript = _normalize_transcript(transcript)
        now = self._clock()
        with self._lock:
            self._cleanup(now)
            if key in self._committed:
                raise VoiceTurnRegistryError("voice turn was already committed")
            entry = self._entries.get(key)
            if entry is None:
                raise VoiceTurnRegistryError("voice provenance is missing or expired")
            if not _constant_text_equal(entry.transcript, normalized_transcript):
                raise VoiceTurnRegistryError("voice transcript does not match its turn")
            expected = self._proof(key, normalized["nonce"], normalized_transcript)
            if not hmac.compare_digest(normalized["proof"], expected):
                raise VoiceTurnRegistryError("voice provenance proof is invalid")
            if not hmac.compare_digest(entry.proof, normalized["proof"]):
                raise VoiceTurnRegistryError("voice provenance reference was altered")
            if entry.state == "reserved":
                if (
                    entry.reserved_session_id != session
                    or entry.reserved_request_id != request
                ):
                    raise VoiceTurnRegistryError("voice turn is reserved by another request")
            else:
                entry.state = "reserved"
                entry.reserved_session_id = session
                entry.reserved_request_id = request
            self._entries.move_to_end(key)
            return self._provenance(key)

    def commit(
        self,
        reference: Mapping[str, Any],
        *,
        session_id: str,
        request_id: str,
    ) -> InputProvenance:
        _, key = self._normalize_reference(reference)
        session = _identifier(session_id, "session_id")
        request = _identifier(request_id, "request_id")
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                if key in self._committed:
                    raise VoiceTurnRegistryError("voice turn was already committed")
                raise VoiceTurnRegistryError("voice provenance is missing or expired")
            if (
                entry.state != "reserved"
                or entry.reserved_session_id != session
                or entry.reserved_request_id != request
            ):
                raise VoiceTurnRegistryError("voice reservation does not match request")
            provenance = self._provenance(key)
            del self._entries[key]
            self._committed[key] = None
            self._committed.move_to_end(key)
            while len(self._committed) > self.capacity:
                self._committed.popitem(last=False)
            return provenance

    def rollback(
        self,
        reference: Mapping[str, Any],
        *,
        session_id: str,
        request_id: str,
    ) -> bool:
        _, key = self._normalize_reference(reference)
        session = _identifier(session_id, "session_id")
        request = _identifier(request_id, "request_id")
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            if entry.state != "reserved":
                return False
            if (
                entry.reserved_session_id != session
                or entry.reserved_request_id != request
            ):
                raise VoiceTurnRegistryError("voice reservation does not match request")
            entry.state = "available"
            entry.reserved_session_id = ""
            entry.reserved_request_id = ""
            return True

    def stats(self) -> dict[str, int]:
        with self._lock:
            self._cleanup(self._clock())
            return {
                "entries": len(self._entries),
                "available": sum(entry.state == "available" for entry in self._entries.values()),
                "reserved": sum(entry.state == "reserved" for entry in self._entries.values()),
                "committed_tombstones": len(self._committed),
                "capacity": self.capacity,
            }

    def _normalize_reference(
        self, reference: Mapping[str, Any]
    ) -> tuple[dict[str, Any], tuple[str, str, int, int, int]]:
        if not isinstance(reference, Mapping) or set(reference) != _REFERENCE_FIELDS:
            raise VoiceTurnRegistryError("invalid voice provenance reference")
        normalized = {
            "runtime_boot_id": _identifier(reference["runtime_boot_id"], "runtime_boot_id"),
            "session_id": _identifier(reference["session_id"], "session_id"),
            "owner_generation": _non_negative_int(reference["owner_generation"], "owner_generation"),
            "voice_sequence": _non_negative_int(reference["voice_sequence"], "voice_sequence"),
            "voice_turn": _non_negative_int(reference["voice_turn"], "voice_turn"),
            "nonce": _identifier(reference["nonce"], "nonce"),
            "proof": str(reference["proof"] or "").strip().lower(),
        }
        if len(normalized["nonce"]) > 128 or _PROOF_PATTERN.fullmatch(normalized["proof"]) is None:
            raise VoiceTurnRegistryError("invalid voice provenance cryptographic fields")
        key = (
            normalized["runtime_boot_id"],
            normalized["session_id"],
            normalized["owner_generation"],
            normalized["voice_sequence"],
            normalized["voice_turn"],
        )
        return normalized, key

    def _proof(
        self,
        key: tuple[str, str, int, int, int],
        nonce: str,
        transcript: str,
    ) -> str:
        material = json.dumps(
            [*key, nonce, transcript],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(self._secret, material, hashlib.sha256).hexdigest()

    @staticmethod
    def _reference(
        key: tuple[str, str, int, int, int], entry: _Entry
    ) -> dict[str, Any]:
        return {
            "runtime_boot_id": key[0],
            "session_id": key[1],
            "owner_generation": key[2],
            "voice_sequence": key[3],
            "voice_turn": key[4],
            "nonce": entry.nonce,
            "proof": entry.proof,
        }

    @staticmethod
    def _provenance(key: tuple[str, str, int, int, int]) -> InputProvenance:
        return InputProvenance(
            InputModality.VOICE,
            InputVerification.SERVER_VERIFIED,
            key[0],
            key[1],
            key[2],
            key[3],
            key[4],
        )

    def _cleanup(self, now: float) -> None:
        for key in tuple(self._entries):
            entry = self._entries[key]
            if entry.state == "available" and entry.expires_at <= now:
                del self._entries[key]

    def _make_room(self) -> None:
        while len(self._entries) >= self.capacity:
            removable = next(
                (key for key, entry in self._entries.items() if entry.state == "available"),
                None,
            )
            if removable is None:
                raise VoiceTurnRegistryError("voice turn registry capacity is reserved")
            del self._entries[removable]


def _identifier(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256 or any(c in normalized for c in "\r\n\x00"):
        raise VoiceTurnRegistryError(f"invalid {field}")
    return normalized


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise VoiceTurnRegistryError(f"invalid {field}")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise VoiceTurnRegistryError(f"invalid {field}") from exc
    if normalized < 0 or normalized > (1 << 63) - 1:
        raise VoiceTurnRegistryError(f"invalid {field}")
    return normalized


def _normalize_transcript(value: Any) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", str(value or "")).split())
    if not normalized or len(normalized) > 100_000:
        raise VoiceTurnRegistryError("invalid voice transcript")
    return normalized


def _constant_text_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


__all__ = ["VoiceTurnRegistry", "VoiceTurnRegistryError"]
