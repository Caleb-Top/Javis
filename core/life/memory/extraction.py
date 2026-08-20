"""Deterministic, source-bounded episode and journal extraction."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from core.life.l1.wake import is_exact_invocation

from .contracts import (
    DerivationEdge,
    ExperienceEpisode,
    JournalEntry,
    MemoryItemStatus,
)
from .projection import TerminalProjectionCandidate


_EXPLICIT_REMEMBER = re.compile(
    r"(?:\bremember\b|\bplease\s+(?:save|record|note)\b|\bdon't\s+forget\b|"
    r"记住|请记下|请记录|别忘记|不要忘记)",
    re.IGNORECASE,
)
_NEGATED_REMEMBER = re.compile(
    r"(?:\bdo\s+not\s+remember\b|\bdon't\s+remember\b|\bdo\s+you\s+remember\b|"
    r"不要记住|别记住|你记得吗|还记得吗)",
    re.IGNORECASE,
)
_VERIFIED_EVENT_REASONS = {
    "decision.confirmed": "confirmed_decision",
    "boundary.confirmed": "confirmed_boundary",
    "goal.verified": "verified_goal",
    "milestone.verified": "verified_milestone",
}
_MODEL_TEXT_FIELDS = (
    "what_happened",
    "javis_attention",
    "intent_summary",
    "action_summary",
    "verified_result_summary",
    "meaning_for_user",
    "meaning_for_javis",
)
_MODEL_FIELDS = frozenset(
    (*_MODEL_TEXT_FIELDS, "source_message_ids", "source_event_ids", "epistemic_label", "privacy_class")
)


class EpisodeExtractionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class EpisodeSelectionReason(str, Enum):
    EXPLICIT_REMEMBER = "explicit_remember"
    CONFIRMED_DECISION = "confirmed_decision"
    CONFIRMED_BOUNDARY = "confirmed_boundary"
    VERIFIED_GOAL = "verified_goal"
    VERIFIED_MILESTONE = "verified_milestone"
    ORDINARY_TURN = "ordinary_turn"
    EXACT_INVOCATION = "exact_invocation"
    EVIDENCE_INELIGIBLE = "evidence_ineligible"


@dataclass(frozen=True, slots=True)
class EpisodeSelection:
    selected: bool
    reason: EpisodeSelectionReason
    selection_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EpisodeExtractionResult:
    selection: EpisodeSelection
    episode: ExperienceEpisode | None


@dataclass(frozen=True, slots=True)
class JournalProjection:
    entry: JournalEntry
    derivation_edges: tuple[DerivationEdge, ...]


class StructuredEpisodeAdapter(Protocol):
    def extract(
        self,
        candidate: TerminalProjectionCandidate,
        evidence: Mapping[str, Any],
        selection: EpisodeSelection,
    ) -> Mapping[str, Any]: ...


class DeterministicEpisodeSelector:
    """Select only explicit memory intent or separately verified lifecycle events."""

    def select(self, evidence: Mapping[str, Any]) -> EpisodeSelection:
        if (
            not isinstance(evidence, Mapping)
            or evidence.get("outcome") != "completed"
            or not bool(evidence.get("evidence_ready"))
            or bool(evidence.get("redacted"))
            or bool(evidence.get("terminal_conflict"))
        ):
            return EpisodeSelection(False, EpisodeSelectionReason.EVIDENCE_INELIGIBLE)

        user_text = _joined_messages(evidence, "user")
        accepted = evidence.get("accepted_event")
        accepted_payload = accepted.get("payload") if isinstance(accepted, Mapping) else {}
        if (
            isinstance(accepted_payload, Mapping)
            and accepted_payload.get("execution_lane") == "deterministic_local"
        ) or (user_text and is_exact_invocation(user_text)):
            return EpisodeSelection(False, EpisodeSelectionReason.EXACT_INVOCATION)

        if user_text and not _NEGATED_REMEMBER.search(user_text) and _EXPLICIT_REMEMBER.search(
            user_text
        ):
            return EpisodeSelection(True, EpisodeSelectionReason.EXPLICIT_REMEMBER)

        for event in _events(evidence):
            reason = _VERIFIED_EVENT_REASONS.get(str(event.get("type") or ""))
            if reason is None:
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping) or not (
                payload.get("verified") is True or payload.get("confirmed") is True
            ):
                continue
            return EpisodeSelection(
                True,
                EpisodeSelectionReason(reason),
                (_identifier(event.get("event_id"), "event_id"),),
            )
        return EpisodeSelection(False, EpisodeSelectionReason.ORDINARY_TURN)


class EpisodeExtractor:
    """Build an immutable episode using only authoritative source evidence."""

    def __init__(
        self,
        *,
        selector: DeterministicEpisodeSelector | None = None,
        adapter: StructuredEpisodeAdapter | None = None,
    ) -> None:
        self._selector = selector or DeterministicEpisodeSelector()
        self._adapter = adapter

    def extract(
        self,
        candidate: TerminalProjectionCandidate,
        evidence: Mapping[str, Any],
    ) -> EpisodeExtractionResult:
        if not isinstance(candidate, TerminalProjectionCandidate):
            raise TypeError("candidate must be a TerminalProjectionCandidate")
        selection = self._selector.select(evidence)
        if not selection.selected:
            return EpisodeExtractionResult(selection, None)
        _validate_candidate_evidence(candidate, evidence)

        messages = tuple(
            message
            for message in evidence.get("messages", ())
            if isinstance(message, Mapping)
            and message.get("status") == "complete"
            and message.get("role") in {"user", "assistant"}
            and bool(message.get("content"))
        )
        events = _events(evidence)
        message_ids = tuple(_identifier(item.get("message_id"), "message_id") for item in messages)
        event_ids = tuple(_identifier(item.get("event_id"), "event_id") for item in events)
        user_text = _joined_messages(evidence, "user")
        assistant_text = _joined_messages(evidence, "assistant")
        if not user_text or not assistant_text:
            raise EpisodeExtractionError("completed_message_evidence_missing")

        values = {
            "what_happened": _bounded(f"User: {user_text} | Javis: {assistant_text}", 2048),
            "javis_attention": _bounded(user_text, 1024),
            "intent_summary": _bounded(user_text, 1024),
            "action_summary": _bounded(assistant_text, 1024),
            "verified_result_summary": "request.completed",
            "meaning_for_user": "",
            "meaning_for_javis": "",
            "source_message_ids": message_ids,
            "source_event_ids": event_ids,
        }
        if self._adapter is not None:
            draft = self._adapter.extract(candidate, evidence, selection)
            values.update(_validate_model_draft(draft, messages, events))

        accepted_event = evidence.get("accepted_event")
        terminal_event = evidence.get("terminal_event")
        if not isinstance(accepted_event, Mapping) or not isinstance(terminal_event, Mapping):
            raise EpisodeExtractionError("terminal_evidence_missing")
        started = _event_timestamp(accepted_event)
        ended = _event_timestamp(terminal_event)
        episode = ExperienceEpisode.from_dict(
            {
                "schema_version": 1,
                "episode_id": _episode_id(candidate),
                "revision": 1,
                "owner_subject_id": candidate.actor_subject_id,
                "audience": "owner_private",
                "privacy_class": "user_private",
                "session_id": candidate.session_id,
                "request_id": candidate.request_id,
                "participant_subject_ids": list(candidate.participant_subject_ids),
                "started_at_utc": started,
                "ended_at_utc": ended,
                "outcome": "completed",
                "what_happened": values["what_happened"],
                "javis_attention": values["javis_attention"],
                "intent_summary": values["intent_summary"],
                "action_summary": values["action_summary"],
                "verified_result_summary": values["verified_result_summary"],
                "meaning_for_user": values["meaning_for_user"],
                "meaning_for_javis": values["meaning_for_javis"],
                "source_terminal_event_id": candidate.source_terminal_event_id,
                "source_terminal_sequence": candidate.source_terminal_sequence,
                "source_sequence_domain": candidate.source_sequence_domain,
                "source_message_ids": list(values["source_message_ids"]),
                "source_event_ids": list(values["source_event_ids"]),
                "source_digest": candidate.source_digest,
                "extractor_version": "deterministic.v1",
                "confidence": (
                    0.95
                    if selection.reason is EpisodeSelectionReason.EXPLICIT_REMEMBER
                    else 1.0
                ),
                "status": "active",
                "retention_class": "memory_candidate",
                "expires_at_utc": None,
                "created_at_utc": ended,
                "updated_at_utc": ended,
            }
        )
        return EpisodeExtractionResult(selection, episode)


class JournalBuilder:
    """Derive one continuity note only from persisted active episodes."""

    def build(self, episodes: Sequence[ExperienceEpisode]) -> JournalProjection | None:
        bounded = tuple(episodes)
        if not bounded:
            return None
        if len(bounded) > 64:
            raise EpisodeExtractionError("journal_source_limit_exceeded")
        if any(
            not isinstance(episode, ExperienceEpisode)
            or episode.status is not MemoryItemStatus.ACTIVE
            for episode in bounded
        ):
            raise EpisodeExtractionError("journal_requires_active_episodes")
        owners = {episode.owner_subject_id for episode in bounded}
        if len(owners) != 1:
            raise EpisodeExtractionError("journal_owner_mismatch")
        ordered = tuple(sorted(bounded, key=lambda item: (item.started_at_utc, item.episode_id)))
        digest = hashlib.sha256(
            json.dumps(
                [episode.source_digest for episode in ordered],
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        entry_id = f"journal-{digest}"
        created = max(episode.updated_at_utc for episode in ordered)
        title = f"Continuity {ordered[0].started_at_utc[:10]}"
        body = _bounded(" | ".join(episode.what_happened for episode in ordered), 4096)
        entry = JournalEntry.from_dict(
            {
                "schema_version": 1,
                "entry_id": entry_id,
                "revision": 1,
                "owner_subject_id": ordered[0].owner_subject_id,
                "audience": "owner_private",
                "privacy_class": "user_private",
                "range_started_at_utc": min(item.started_at_utc for item in ordered),
                "range_ended_at_utc": max(item.ended_at_utc for item in ordered),
                "title": title,
                "body": body,
                "source_episode_ids": [item.episode_id for item in ordered],
                "entry_kind": "continuity_note",
                "source_digest": digest,
                "status": "active",
                "retention_class": "continuity",
                "expires_at_utc": None,
                "created_at_utc": created,
                "updated_at_utc": created,
            }
        )
        edges = tuple(
            DerivationEdge.from_dict(
                {
                    "schema_version": 1,
                    "edge_id": _edge_id(episode.episode_id, entry_id),
                    "source_kind": "experience_episode",
                    "source_id": episode.episode_id,
                    "target_kind": "journal_entry",
                    "target_id": entry_id,
                    "relation": "derived_from",
                    "extractor": "journal",
                    "extractor_version": "deterministic.v1",
                    "source_digest": episode.source_digest,
                    "created_at_utc": created,
                    "active": True,
                }
            )
            for episode in ordered
        )
        return JournalProjection(entry, edges)


def _validate_candidate_evidence(
    candidate: TerminalProjectionCandidate, evidence: Mapping[str, Any]
) -> None:
    if (
        evidence.get("source_store_id") != candidate.source_store_id
        or evidence.get("session_id") != candidate.session_id
        or evidence.get("request_id") != candidate.request_id
        or evidence.get("sequence_domain") != candidate.source_sequence_domain
    ):
        raise EpisodeExtractionError("candidate_evidence_mismatch")
    terminal = evidence.get("terminal_event")
    if not isinstance(terminal, Mapping) or (
        terminal.get("event_id") != candidate.source_terminal_event_id
        or terminal.get("sequence") != candidate.source_terminal_sequence
        or terminal.get("type") != "request.completed"
    ):
        raise EpisodeExtractionError("candidate_terminal_mismatch")


def _validate_model_draft(
    draft: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(draft, Mapping) or set(draft) != _MODEL_FIELDS:
        raise EpisodeExtractionError("model_draft_schema_invalid")
    if draft.get("epistemic_label") != "evidence_derived":
        raise EpisodeExtractionError("model_epistemic_label_invalid")
    if draft.get("privacy_class") != "user_private":
        raise EpisodeExtractionError("model_privacy_invalid")
    source_text = "\n".join(str(item.get("content") or "") for item in messages)
    result: dict[str, Any] = {}
    for field_name in _MODEL_TEXT_FIELDS:
        value = draft.get(field_name)
        if type(value) is not str or len(value) > (2048 if field_name == "what_happened" else 1024):
            raise EpisodeExtractionError("model_text_invalid")
        if any(unicodedata.category(character) == "Cc" for character in value):
            raise EpisodeExtractionError("model_text_invalid")
        if value and value not in source_text:
            raise EpisodeExtractionError("model_new_fact_rejected")
        result[field_name] = value
    valid_message_ids = {str(item.get("message_id")) for item in messages}
    valid_event_ids = {str(item.get("event_id")) for item in events}
    message_ids = _source_ids(draft.get("source_message_ids"), valid_message_ids)
    event_ids = _source_ids(draft.get("source_event_ids"), valid_event_ids)
    required_message_ids = {
        str(item.get("message_id"))
        for item in messages
        if item.get("role") in {"user", "assistant"}
    }
    required_event_ids = {
        str(item.get("event_id"))
        for item in events
        if item.get("type") in {"request.accepted", "request.completed"}
    }
    if (
        not message_ids
        or not event_ids
        or not required_message_ids.issubset(message_ids)
        or not required_event_ids.issubset(event_ids)
    ):
        raise EpisodeExtractionError("model_source_refs_missing")
    result["source_message_ids"] = message_ids
    result["source_event_ids"] = event_ids
    return result


def _source_ids(value: Any, allowed: set[str]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 64:
        raise EpisodeExtractionError("model_source_refs_invalid")
    ids = tuple(_identifier(item, "source_id") for item in value)
    if len(set(ids)) != len(ids) or not set(ids).issubset(allowed):
        raise EpisodeExtractionError("model_source_refs_invalid")
    return ids


def _events(evidence: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = evidence.get("events", ())
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _joined_messages(evidence: Mapping[str, Any], role: str) -> str:
    raw = evidence.get("messages", ())
    if not isinstance(raw, (list, tuple)):
        return ""
    values = [
        str(item.get("content") or "").strip()
        for item in raw
        if isinstance(item, Mapping)
        and item.get("role") == role
        and item.get("status") == "complete"
        and bool(str(item.get("content") or "").strip())
    ]
    return " ".join(_clean_text(value) for value in values)


def _event_timestamp(event: Mapping[str, Any]) -> str:
    value = event.get("timestamp")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EpisodeExtractionError("event_timestamp_missing")
    timestamp = datetime.fromtimestamp(float(value), timezone.utc)
    return timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _episode_id(candidate: TerminalProjectionCandidate) -> str:
    digest = hashlib.sha256(
        f"{candidate.source_store_id}\0{candidate.session_id}\0{candidate.request_id}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"episode-{digest}"


def _edge_id(source_id: str, target_id: str) -> str:
    return "edge-" + hashlib.sha256(f"{source_id}\0{target_id}".encode("utf-8")).hexdigest()


def _identifier(value: Any, field_name: str) -> str:
    if type(value) is not str or not value or len(value) > 256:
        raise EpisodeExtractionError(f"{field_name}_invalid")
    return value


def _bounded(value: str, maximum: int) -> str:
    normalized = _clean_text(value).strip()
    if len(normalized) <= maximum:
        return normalized
    return normalized[:maximum].rstrip()


def _clean_text(value: str) -> str:
    return "".join(
        " " if unicodedata.category(character) == "Cc" else character
        for character in value
    )


__all__ = [
    "DeterministicEpisodeSelector",
    "EpisodeExtractionError",
    "EpisodeExtractionResult",
    "EpisodeExtractor",
    "EpisodeSelection",
    "EpisodeSelectionReason",
    "JournalBuilder",
    "JournalProjection",
    "StructuredEpisodeAdapter",
]
