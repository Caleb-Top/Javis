"""Deterministic, content-free terminal projection decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Mapping


_OUTCOMES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_FINAL_STATES = frozenset({"excluded", "not_selected", "projected"})
_MAX_RETRY_SECONDS = 30.0


class TerminalProjectionError(RuntimeError):
    """Fail-closed source evidence error with a stable diagnostic code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class TerminalProjectionCandidate:
    """Content-free pointer to evidence eligible for Task 7 extraction."""

    source_store_id: str
    terminal_row_id: int
    session_id: str
    request_id: str
    source_terminal_event_id: str
    source_terminal_sequence: int
    source_sequence_domain: str
    source_digest: str
    actor_subject_id: str
    participant_subject_ids: tuple[str, ...]
    audience_ceiling: str
    acl_epoch: int


@dataclass(frozen=True, slots=True)
class TerminalProjectionDecision:
    receipt_values: Mapping[str, Any]
    advance_cursor: bool
    write_receipt: bool
    candidate: TerminalProjectionCandidate | None = None

    @property
    def projection_state(self) -> str:
        return str(self.receipt_values["projection_state"])

    @property
    def reason_code(self) -> str | None:
        value = self.receipt_values.get("reason_code")
        return None if value is None else str(value)


class TerminalProjector:
    """Classify authoritative terminal evidence without creating memory objects."""

    def __init__(
        self,
        *,
        retry_base_seconds: float = 0.25,
        retry_max_seconds: float = _MAX_RETRY_SECONDS,
    ) -> None:
        if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("retry bounds are invalid")
        self._retry_base = float(retry_base_seconds)
        self._retry_max = float(retry_max_seconds)

    def decide(
        self,
        *,
        source_store_id: str,
        terminal: Mapping[str, Any],
        evidence: Mapping[str, Any] | None,
        existing_receipt: Mapping[str, Any] | None = None,
        now_utc: datetime | None = None,
    ) -> TerminalProjectionDecision:
        source_id = _identifier(source_store_id, "source_store_id")
        identity = _terminal_identity(terminal)
        now = _normalize_now(now_utc)
        existing = _validate_existing(existing_receipt, source_id, identity)
        base = {
            "receipt_id": terminal_receipt_id(
                source_id, identity["session_id"], identity["request_id"]
            ),
            "source_store_id": source_id,
            "terminal_row_id": identity["terminal_row_id"],
            "session_id": identity["session_id"],
            "request_id": identity["request_id"],
            "source_terminal_event_id": identity["event_id"],
            "outcome": identity["outcome"],
        }

        if existing is not None and str(existing["projection_state"]) in _FINAL_STATES:
            values = _receipt_values_from_existing(existing, base)
            return TerminalProjectionDecision(
                MappingProxyType(values), advance_cursor=True, write_receipt=True
            )

        if identity["outcome"] != "completed":
            values = dict(
                base,
                projection_state="excluded",
                reason_code=f"terminal_{identity['outcome']}",
                attempt=0,
                next_retry_at_utc=None,
            )
            return TerminalProjectionDecision(
                MappingProxyType(values), advance_cursor=True, write_receipt=True
            )

        if evidence is None:
            return self._pending(base, existing, now, "evidence_pending")
        _validate_evidence_identity(source_id, identity, evidence)
        if bool(evidence.get("redacted")) or evidence.get("redaction_receipt") is not None:
            values = dict(
                base,
                projection_state="excluded",
                reason_code="deletion_suppressed",
                attempt=0,
                next_retry_at_utc=None,
            )
            return TerminalProjectionDecision(
                MappingProxyType(values), advance_cursor=True, write_receipt=True
            )
        if bool(evidence.get("terminal_conflict")):
            return self._pending(base, existing, now, "terminal_conflict")
        if not bool(evidence.get("evidence_ready")):
            return self._pending(base, existing, now, "evidence_pending")

        access = evidence.get("access_projection")
        if not isinstance(access, Mapping):
            return self._excluded(base, "access_context_missing")
        actor_kind = str(access.get("actor_kind") or "")
        if actor_kind not in {"primary_user", "known_person", "guest", "javis"}:
            raise TerminalProjectionError("access_projection_invalid")
        if actor_kind == "guest":
            return self._excluded(base, "guest_session")
        if access.get("identity_assurance") == "guest":
            raise TerminalProjectionError("access_projection_invalid")
        actor_subject_id = _optional_identifier(access.get("actor_subject_id"))
        participants = _participants(access.get("participant_subject_ids"))
        if actor_subject_id is None or not participants or actor_subject_id not in participants:
            return self._excluded(base, "participant_binding_missing")
        acl_epoch = access.get("acl_epoch")
        if type(acl_epoch) is not int or acl_epoch < 0:
            raise TerminalProjectionError("access_projection_invalid")
        audience_ceiling = str(access.get("audience_ceiling") or "")
        if audience_ceiling not in {
            "owner_private",
            "participants",
            "explicit_shared",
        }:
            raise TerminalProjectionError("access_projection_invalid")

        candidate = TerminalProjectionCandidate(
            source_store_id=source_id,
            terminal_row_id=identity["terminal_row_id"],
            session_id=identity["session_id"],
            request_id=identity["request_id"],
            source_terminal_event_id=identity["event_id"],
            source_terminal_sequence=identity["sequence"],
            source_sequence_domain=identity["sequence_domain"],
            source_digest=_evidence_digest(evidence),
            actor_subject_id=actor_subject_id,
            participant_subject_ids=participants,
            audience_ceiling=audience_ceiling,
            acl_epoch=acl_epoch,
        )
        decision = self._pending(base, existing, now, "eligible_candidate")
        return TerminalProjectionDecision(
            decision.receipt_values,
            advance_cursor=False,
            write_receipt=decision.write_receipt,
            candidate=candidate,
        )

    def _pending(
        self,
        base: Mapping[str, Any],
        existing: Mapping[str, Any] | None,
        now: datetime,
        reason_code: str,
    ) -> TerminalProjectionDecision:
        if existing is not None:
            retry_at = _parse_utc(existing.get("next_retry_at_utc"))
            if retry_at is not None and retry_at > now:
                values = _receipt_values_from_existing(existing, base)
                return TerminalProjectionDecision(
                    MappingProxyType(values),
                    advance_cursor=False,
                    write_receipt=False,
                )
        previous_attempt = int(existing.get("attempt", 0)) if existing is not None else 0
        attempt = min(previous_attempt + 1, 32)
        delay = min(self._retry_base * (2 ** min(attempt - 1, 16)), self._retry_max)
        values = dict(
            base,
            projection_state="pending",
            reason_code=reason_code,
            attempt=attempt,
            next_retry_at_utc=_format_utc(now + timedelta(seconds=delay)),
        )
        return TerminalProjectionDecision(
            MappingProxyType(values), advance_cursor=False, write_receipt=True
        )

    @staticmethod
    def _excluded(
        base: Mapping[str, Any], reason_code: str
    ) -> TerminalProjectionDecision:
        values = dict(
            base,
            projection_state="excluded",
            reason_code=reason_code,
            attempt=0,
            next_retry_at_utc=None,
        )
        return TerminalProjectionDecision(
            MappingProxyType(values), advance_cursor=True, write_receipt=True
        )


def terminal_receipt_id(source_store_id: str, session_id: str, request_id: str) -> str:
    digest = hashlib.sha256(
        f"{source_store_id}\0{session_id}\0{request_id}".encode("utf-8")
    ).hexdigest()
    return f"terminal-{digest}"


def _terminal_identity(terminal: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(terminal, Mapping):
        raise TerminalProjectionError("terminal_invalid")
    row_id = terminal.get("terminal_row_id", terminal.get("row_id"))
    sequence = terminal.get("sequence")
    if type(row_id) is not int or row_id < 1:
        raise TerminalProjectionError("terminal_cursor_invalid")
    if type(sequence) is not int or sequence < 1:
        raise TerminalProjectionError("terminal_sequence_invalid")
    outcome = str(terminal.get("outcome") or "")
    if outcome not in _OUTCOMES:
        raise TerminalProjectionError("terminal_outcome_invalid")
    session_id = _identifier(terminal.get("session_id"), "session_id")
    sequence_domain = _identifier(terminal.get("sequence_domain"), "sequence_domain")
    expected_domain = f"conversation_store:{session_id}"
    if sequence_domain != expected_domain:
        raise TerminalProjectionError("sequence_domain_mismatch")
    return {
        "terminal_row_id": row_id,
        "event_id": _identifier(terminal.get("event_id"), "event_id"),
        "session_id": session_id,
        "request_id": _identifier(terminal.get("request_id"), "request_id"),
        "sequence": sequence,
        "sequence_domain": sequence_domain,
        "outcome": outcome,
    }


def _validate_evidence_identity(
    source_store_id: str,
    terminal: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> None:
    if str(evidence.get("source_store_id") or "") != source_store_id:
        raise TerminalProjectionError("source_store_mismatch")
    if (
        evidence.get("session_id") != terminal["session_id"]
        or evidence.get("request_id") != terminal["request_id"]
        or evidence.get("sequence_domain") != terminal["sequence_domain"]
    ):
        raise TerminalProjectionError("request_evidence_mismatch")
    terminals = evidence.get("terminal_events")
    if not isinstance(terminals, (list, tuple)) or len(terminals) != 1:
        if not bool(evidence.get("terminal_conflict")):
            raise TerminalProjectionError("terminal_evidence_missing")
        return
    canonical = terminals[0]
    if not isinstance(canonical, Mapping):
        raise TerminalProjectionError("terminal_evidence_invalid")
    if (
        canonical.get("event_id") != terminal["event_id"]
        or canonical.get("sequence") != terminal["sequence"]
        or canonical.get("type") != f"request.{terminal['outcome']}"
    ):
        raise TerminalProjectionError("terminal_evidence_mismatch")


def _validate_existing(
    existing: Mapping[str, Any] | None,
    source_store_id: str,
    terminal: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if existing is None:
        return None
    if not isinstance(existing, Mapping):
        raise TerminalProjectionError("receipt_invalid")
    expected = (
        source_store_id,
        terminal["session_id"],
        terminal["request_id"],
        terminal["event_id"],
        terminal["terminal_row_id"],
        terminal["outcome"],
    )
    actual = (
        existing.get("source_store_id"),
        existing.get("session_id"),
        existing.get("request_id"),
        existing.get("source_terminal_event_id"),
        existing.get("terminal_row_id"),
        existing.get("outcome"),
    )
    if actual != expected:
        raise TerminalProjectionError("receipt_identity_conflict")
    return existing


def _receipt_values_from_existing(
    existing: Mapping[str, Any], base: Mapping[str, Any]
) -> dict[str, Any]:
    return dict(
        base,
        receipt_id=str(existing.get("receipt_id") or base["receipt_id"]),
        projection_state=str(existing["projection_state"]),
        reason_code=existing.get("reason_code"),
        episode_id=existing.get("episode_id"),
        attempt=int(existing.get("attempt", 0)),
        next_retry_at_utc=existing.get("next_retry_at_utc"),
    )


def _evidence_digest(evidence: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TerminalProjectionError("evidence_not_canonical") from exc
    return hashlib.sha256(encoded).hexdigest()


def _participants(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 16:
        return ()
    participants = tuple(item for item in (_optional_identifier(raw) for raw in value) if item)
    if len(participants) != len(value) or len(set(participants)) != len(participants):
        return ()
    return participants


def _identifier(value: Any, field_name: str) -> str:
    normalized = _optional_identifier(value)
    if normalized is None:
        raise TerminalProjectionError(f"{field_name}_invalid")
    return normalized


def _optional_identifier(value: Any) -> str | None:
    if type(value) is not str:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256 or any(char in normalized for char in "\r\n\0"):
        return None
    return normalized


def _normalize_now(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    return now.astimezone(timezone.utc)


def _parse_utc(value: Any) -> datetime | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "TerminalProjectionCandidate",
    "TerminalProjectionDecision",
    "TerminalProjectionError",
    "TerminalProjector",
    "terminal_receipt_id",
]
