"""Deterministic, raw-CoT-free projection for L5 thinking checkpoints."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .contracts import (
    CheckpointAssumptionV1,
    DecisionSummaryV1,
    ExecutionBudgetV1,
    NextSafeStepV1,
    ThinkingCheckpointV1,
    ThinkingStage,
    canonical_content_hash,
)


# Keys are compared after NFKC, case folding, and removal of every separator.
FORBIDDEN_CHECKPOINT_KEY_TOKENS = frozenset(
    {
        "chainofthought",
        "chatmessages",
        "cot",
        "developerprompt",
        "hiddenprompt",
        "hiddenreasoning",
        "hiddenstate",
        "internalprompt",
        "internalreasoning",
        "internalthoughts",
        "logits",
        "message",
        "messages",
        "modelmessages",
        "modelprompt",
        "modelreasoning",
        "prompt",
        "rawcot",
        "rawmodeloutput",
        "rawprompt",
        "rawreasoning",
        "reasoning",
        "reasoningcontent",
        "scratchpad",
        "systemprompt",
        "thinkingtrace",
        "thoughtprocess",
        "tokenlikelihood",
        "tokenlikelihoods",
        "tokenprob",
        "tokenprobability",
        "tokenprobabilities",
        "tokenprobs",
    }
)

_CANDIDATE_FIELDS = frozenset(
    {
        "stage",
        "plan_id",
        "plan_revision",
        "completed_step_ids",
        "action_receipt_ids",
        "evidence_refs",
        "decision_summaries",
        "assumptions",
        "open_questions",
        "next_safe_step",
        "blocker_codes",
    }
)


class CheckpointProjectionError(ValueError):
    """Fail-closed rejection whose message never contains candidate content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _normalized_key_token(key: str) -> str:
    normalized = unicodedata.normalize("NFKC", key).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _scan_forbidden_keys(
    value: object,
    *,
    ancestors: frozenset[int] = frozenset(),
) -> None:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in ancestors:
            raise CheckpointProjectionError("checkpoint.cyclic_value")
        descendants = ancestors | {identity}
        for key, item in value.items():
            if type(key) is not str:
                raise CheckpointProjectionError("checkpoint.non_string_key")
            if _normalized_key_token(key) in FORBIDDEN_CHECKPOINT_KEY_TOKENS:
                raise CheckpointProjectionError("checkpoint.forbidden_field")
            _scan_forbidden_keys(item, ancestors=descendants)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in ancestors:
            raise CheckpointProjectionError("checkpoint.cyclic_value")
        descendants = ancestors | {identity}
        for item in value:
            _scan_forbidden_keys(item, ancestors=descendants)


def _array(value: object, reason_code: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise CheckpointProjectionError(reason_code)
    return tuple(value)


def _contract_item(
    value: object,
    contract_type: type[Any],
    reason_code: str,
) -> Any:
    if isinstance(value, contract_type):
        return value
    if isinstance(value, Mapping):
        return contract_type.from_dict(value)
    raise CheckpointProjectionError(reason_code)


@dataclass(frozen=True, slots=True)
class ThinkingCheckpointCandidate:
    """A bounded structured candidate with no raw model transcript fields."""

    stage: ThinkingStage | str
    plan_id: str | None = None
    plan_revision: int | None = None
    completed_step_ids: tuple[str, ...] = ()
    action_receipt_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    decision_summaries: tuple[DecisionSummaryV1, ...] = ()
    assumptions: tuple[CheckpointAssumptionV1, ...] = ()
    open_questions: tuple[str, ...] = ()
    next_safe_step: NextSafeStepV1 | None = None
    blocker_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        raw_values = {
            field_name: getattr(self, field_name) for field_name in _CANDIDATE_FIELDS
        }
        _scan_forbidden_keys(raw_values)

        try:
            stage = (
                self.stage
                if isinstance(self.stage, ThinkingStage)
                else ThinkingStage(self.stage)
            )
        except (TypeError, ValueError) as exc:
            raise CheckpointProjectionError("checkpoint.invalid_stage") from exc
        object.__setattr__(self, "stage", stage)

        for field_name in (
            "completed_step_ids",
            "action_receipt_ids",
            "evidence_refs",
            "open_questions",
            "blocker_codes",
        ):
            object.__setattr__(
                self,
                field_name,
                _array(getattr(self, field_name), f"checkpoint.invalid_{field_name}"),
            )

        decisions = _array(
            self.decision_summaries, "checkpoint.invalid_decision_summaries"
        )
        object.__setattr__(
            self,
            "decision_summaries",
            tuple(
                _contract_item(
                    item,
                    DecisionSummaryV1,
                    "checkpoint.invalid_decision_summary",
                )
                for item in decisions
            ),
        )

        assumptions = _array(self.assumptions, "checkpoint.invalid_assumptions")
        object.__setattr__(
            self,
            "assumptions",
            tuple(
                _contract_item(
                    item,
                    CheckpointAssumptionV1,
                    "checkpoint.invalid_assumption",
                )
                for item in assumptions
            ),
        )

        if self.next_safe_step is not None:
            object.__setattr__(
                self,
                "next_safe_step",
                _contract_item(
                    self.next_safe_step,
                    NextSafeStepV1,
                    "checkpoint.invalid_next_safe_step",
                ),
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ThinkingCheckpointCandidate":
        if not isinstance(data, Mapping):
            raise CheckpointProjectionError("checkpoint.candidate_not_object")
        _scan_forbidden_keys(data)
        if any(type(key) is not str for key in data):
            raise CheckpointProjectionError("checkpoint.non_string_key")
        if set(data) - _CANDIDATE_FIELDS:
            raise CheckpointProjectionError("checkpoint.unexpected_field")
        if "stage" not in data:
            raise CheckpointProjectionError("checkpoint.missing_stage")
        return cls(
            stage=data["stage"],
            plan_id=data.get("plan_id"),
            plan_revision=data.get("plan_revision"),
            completed_step_ids=data.get("completed_step_ids", ()),
            action_receipt_ids=data.get("action_receipt_ids", ()),
            evidence_refs=data.get("evidence_refs", ()),
            decision_summaries=data.get("decision_summaries", ()),
            assumptions=data.get("assumptions", ()),
            open_questions=data.get("open_questions", ()),
            next_safe_step=data.get("next_safe_step"),
            blocker_codes=data.get("blocker_codes", ()),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "plan_id": self.plan_id,
            "plan_revision": self.plan_revision,
            "completed_step_ids": list(self.completed_step_ids),
            "action_receipt_ids": list(self.action_receipt_ids),
            "evidence_refs": list(self.evidence_refs),
            "decision_summaries": [item.to_dict() for item in self.decision_summaries],
            "assumptions": [item.to_dict() for item in self.assumptions],
            "open_questions": list(self.open_questions),
            "next_safe_step": (
                None if self.next_safe_step is None else self.next_safe_step.to_dict()
            ),
            "blocker_codes": list(self.blocker_codes),
        }


class ThinkingCheckpointProjector:
    """Project one governed candidate without clocks, models, or storage access."""

    def project(
        self,
        candidate: ThinkingCheckpointCandidate | Mapping[str, object],
        *,
        checkpoint_id: str,
        intention_id: str,
        commitment_id: str | None,
        budget_snapshot: ExecutionBudgetV1 | Mapping[str, object],
        created_at_utc: str,
    ) -> ThinkingCheckpointV1:
        if isinstance(candidate, Mapping):
            projected_candidate = ThinkingCheckpointCandidate.from_dict(candidate)
        elif isinstance(candidate, ThinkingCheckpointCandidate):
            projected_candidate = candidate
            _scan_forbidden_keys(projected_candidate.to_dict())
        else:
            raise CheckpointProjectionError("checkpoint.candidate_not_object")

        if isinstance(budget_snapshot, Mapping):
            _scan_forbidden_keys(budget_snapshot)
            projected_budget = ExecutionBudgetV1.from_dict(budget_snapshot)
        elif isinstance(budget_snapshot, ExecutionBudgetV1):
            projected_budget = budget_snapshot
        else:
            raise CheckpointProjectionError("checkpoint.invalid_budget_snapshot")

        candidate_wire = projected_candidate.to_dict()
        wire: dict[str, object] = {
            "schema_version": 1,
            "checkpoint_id": checkpoint_id,
            "intention_id": intention_id,
            "commitment_id": commitment_id,
            "plan_id": candidate_wire["plan_id"],
            "plan_revision": candidate_wire["plan_revision"],
            "stage": candidate_wire["stage"],
            "completed_step_ids": candidate_wire["completed_step_ids"],
            "action_receipt_ids": candidate_wire["action_receipt_ids"],
            "evidence_refs": candidate_wire["evidence_refs"],
            "decision_summaries": candidate_wire["decision_summaries"],
            "assumptions": candidate_wire["assumptions"],
            "open_questions": candidate_wire["open_questions"],
            "next_safe_step": candidate_wire["next_safe_step"],
            "blocker_codes": candidate_wire["blocker_codes"],
            "budget_snapshot": projected_budget.to_dict(),
            "created_at_utc": created_at_utc,
        }
        _scan_forbidden_keys(wire)
        wire["content_hash"] = canonical_content_hash(wire)
        return ThinkingCheckpointV1.from_dict(wire)


def project_thinking_checkpoint(
    candidate: ThinkingCheckpointCandidate | Mapping[str, object],
    *,
    checkpoint_id: str,
    intention_id: str,
    commitment_id: str | None,
    budget_snapshot: ExecutionBudgetV1 | Mapping[str, object],
    created_at_utc: str,
) -> ThinkingCheckpointV1:
    """Functional entry point for callers that do not retain a projector."""

    return ThinkingCheckpointProjector().project(
        candidate,
        checkpoint_id=checkpoint_id,
        intention_id=intention_id,
        commitment_id=commitment_id,
        budget_snapshot=budget_snapshot,
        created_at_utc=created_at_utc,
    )


__all__ = [
    "CheckpointProjectionError",
    "FORBIDDEN_CHECKPOINT_KEY_TOKENS",
    "ThinkingCheckpointCandidate",
    "ThinkingCheckpointProjector",
    "project_thinking_checkpoint",
]
