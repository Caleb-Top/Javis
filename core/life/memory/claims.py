"""Deterministic claim policy; proposals never carry final authority fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import (
    AccessContext, ActorKind, Audience, ClaimEpistemicClass, ClaimSensitivity,
    ClaimStatus, ClaimValueType, UserModelClaim,
)


@dataclass(frozen=True)
class PredicateRule:
    value_type: ClaimValueType
    choices: frozenset[str] = frozenset()
    sensitivity: ClaimSensitivity = ClaimSensitivity.ORDINARY
    requires_confirmation: bool = False


def _choice(*values: str) -> PredicateRule:
    return PredicateRule(ClaimValueType.STRING, frozenset(values))


PREDICATE_REGISTRY = MappingProxyType({
    "communication.verbosity": _choice("brief", "balanced", "detailed"),
    "communication.directness": _choice("direct", "neutral", "gentle"),
    "communication.formality": _choice("casual", "neutral", "formal"),
    "communication.language": PredicateRule(ClaimValueType.STRING),
    "workflow.preferred_confirmation_style": _choice("concise", "detailed"),
    "workflow.focus_window": PredicateRule(
        ClaimValueType.TIME_WINDOW, sensitivity=ClaimSensitivity.PERSONAL
    ),
    "boundary.do_not_mention": PredicateRule(
        ClaimValueType.STRING_SET, sensitivity=ClaimSensitivity.SENSITIVE,
        requires_confirmation=True,
    ),
    "boundary.do_not_store": PredicateRule(
        ClaimValueType.STRING_SET, sensitivity=ClaimSensitivity.SENSITIVE,
        requires_confirmation=True,
    ),
    "boundary.ask_before_topic": PredicateRule(
        ClaimValueType.STRING_SET, sensitivity=ClaimSensitivity.SENSITIVE,
        requires_confirmation=True,
    ),
    "preference.response_format": _choice("prose", "bullets", "table"),
    "preference.units": _choice("metric", "imperial"),
})

_TOPICS = frozenset({"health", "finances", "relationships", "family", "work", "identity"})
_TIME_WINDOW = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]-(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?\Z")


def validate_claim_value(predicate: str, value_type: ClaimValueType | str, value: Any) -> Any:
    rule = PREDICATE_REGISTRY.get(predicate)
    if rule is None:
        raise ValueError("claim_predicate_not_supported")
    if ClaimValueType(value_type) is not rule.value_type:
        raise ValueError("claim_value_type_mismatch")
    if rule.value_type is ClaimValueType.STRING_SET:
        if (
            not isinstance(value, (list, tuple)) or not 1 <= len(value) <= len(_TOPICS)
            or any(type(item) is not str or item not in _TOPICS for item in value)
            or len(set(value)) != len(value)
        ):
            raise ValueError("claim_topic_not_supported")
        return tuple(sorted(value))
    if type(value) is not str or not value or len(value) > 64:
        raise ValueError("claim_value_invalid")
    if rule.choices and value not in rule.choices:
        raise ValueError("claim_value_not_supported")
    if predicate == "communication.language" and not _LANGUAGE.fullmatch(value):
        raise ValueError("claim_language_invalid")
    if rule.value_type is ClaimValueType.TIME_WINDOW:
        if not _TIME_WINDOW.fullmatch(value) or value[:5] == value[6:]:
            raise ValueError("claim_time_window_invalid")
    return value


@dataclass(frozen=True)
class ClaimProposal:
    predicate: str
    value_type: ClaimValueType
    value: str | tuple[str, ...]
    source_evidence_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ClaimProposal":
        if not isinstance(payload, Mapping) or set(payload) != {
            "predicate", "value_type", "value", "source_evidence_ids"
        }:
            raise ValueError("claim_proposal_fields_invalid")
        predicate = payload["predicate"]
        if type(predicate) is not str:
            raise ValueError("claim_predicate_not_supported")
        value_type = ClaimValueType(payload["value_type"])
        value = validate_claim_value(predicate, value_type, payload["value"])
        sources = payload["source_evidence_ids"]
        if (
            not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= 64
            or any(type(item) is not str or not item or len(item) > 256
                   or any(c in item for c in "\r\n\x00") for item in sources)
            or len(set(sources)) != len(sources)
        ):
            raise ValueError("claim_sources_invalid")
        return cls(predicate, value_type, value, tuple(sources))


@dataclass(frozen=True)
class DecideClaim:
    access_context: AccessContext
    claim_id: str
    expected_revision: int
    decision: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.access_context, AccessContext):
            raise ValueError("claim_access_context_required")
        for value in (self.claim_id, self.idempotency_key):
            if type(value) is not str or not value or len(value) > 256 or any(
                c in value for c in "\r\n\x00"
            ):
                raise ValueError("claim_identifier_invalid")
        if type(self.expected_revision) is not int or self.expected_revision < 1:
            raise ValueError("claim_revision_invalid")
        if self.decision not in {"confirm", "reject"}:
            raise ValueError("claim_decision_invalid")


@dataclass(frozen=True)
class CorrectClaim:
    access_context: AccessContext
    claim_id: str
    expected_revision: int
    proposal: ClaimProposal
    idempotency_key: str

    def __post_init__(self) -> None:
        DecideClaim(self.access_context, self.claim_id, self.expected_revision,
                    "confirm", self.idempotency_key)
        if not isinstance(self.proposal, ClaimProposal):
            raise ValueError("claim_proposal_required")
        normalized = ClaimProposal.from_dict({
            "predicate": self.proposal.predicate, "value_type": self.proposal.value_type,
            "value": self.proposal.value, "source_evidence_ids": self.proposal.source_evidence_ids,
        })
        object.__setattr__(self, "proposal", normalized)


def claim_prompt_eligible(claim: UserModelClaim, context: AccessContext, *, at_utc: str) -> bool:
    """Additional semantic gate after ACL filtering, including cached candidates."""
    if (
        context.actor_kind not in {ActorKind.PRIMARY_USER, ActorKind.KNOWN_PERSON}
        or context.guest_present or context.audience_ceiling is Audience.GUEST
        or claim.owner_subject_id != context.actor_subject_id
        or claim.subject_id != context.actor_subject_id
        or claim.audience is not Audience.OWNER_PRIVATE
        or claim.contradiction_claim_ids
    ):
        return False
    try:
        validate_claim_value(claim.predicate, claim.value_type, claim.value)
        now = datetime.fromisoformat(at_utc.replace("Z", "+00:00"))
        if now.tzinfo is None:
            return False
        if claim.expires_at_utc is not None:
            expires = datetime.fromisoformat(claim.expires_at_utc.replace("Z", "+00:00"))
            if expires <= now.astimezone(timezone.utc):
                return False
    except (ValueError, TypeError):
        return False
    if claim.status is ClaimStatus.CONFIRMED:
        return bool(
            claim.epistemic_class is ClaimEpistemicClass.CONFIRMED
            and claim.confirmation_event_id and claim.confirmed_at_utc
        )
    rule = PREDICATE_REGISTRY[claim.predicate]
    return bool(
        claim.status is ClaimStatus.ACTIVE
        and claim.epistemic_class is ClaimEpistemicClass.EXPLICIT_STATEMENT
        and not rule.requires_confirmation
        and claim.sensitivity in {ClaimSensitivity.ORDINARY, ClaimSensitivity.PERSONAL}
    )
