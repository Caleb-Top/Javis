"""Deterministic, privacy-thin turn receipt projection."""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from core.life.contracts import (
    PrivacyClass,
    RetentionClass,
    canonical_content_hash,
)
from core.life.l1.contracts import (
    ApprovalOutcome,
    InputProvenance,
    LifeObservation,
    ObservationKind,
    ObservationOutcome,
    ReceiptCompleteness,
    ResponsePath,
    TurnExperienceReceipt,
    TurnOutcome,
)


ReceiptKey = tuple[str, str]
_MAX_RECENT = 32
_MAX_SEEN_EVENT_IDS = 128
_MACHINE_CODE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_TERMINALS = {
    ObservationKind.REQUEST_COMPLETED: (
        TurnOutcome.COMPLETED,
        ObservationOutcome.COMPLETED,
    ),
    ObservationKind.REQUEST_FAILED: (
        TurnOutcome.FAILED,
        ObservationOutcome.FAILED,
    ),
    ObservationKind.REQUEST_CANCELLED: (
        TurnOutcome.CANCELLED,
        ObservationOutcome.CANCELLED,
    ),
}
_NON_ACTIVITY_KINDS = frozenset(
    {ObservationKind.REQUEST_STARTED, *_TERMINALS}
)


@dataclass
class _TurnState:
    receipt_id: str
    session_id: str
    request_id: str
    input_provenance: InputProvenance
    source_boot_id: str
    first_event_id: str
    first_event_sequence: int
    first_event_sequence_domain: str
    started_at_utc: str
    ended_at_utc: str
    model_route: str | None
    response_path: ResponsePath
    activity_kinds: list[str] = field(default_factory=list)
    tool_count: int = 0
    approval_outcome: ApprovalOutcome = ApprovalOutcome.NONE
    interruption_count: int = 0
    goal_verified: bool = False
    last_sequences: dict[str, int] = field(default_factory=dict)
    seen_event_ids: deque[str] = field(
        default_factory=lambda: deque(maxlen=_MAX_SEEN_EVENT_IDS)
    )


class TurnExperienceProjector:
    """Reduce governed observations into bounded, content-free turn receipts.

    Every accepted mutation returns the latest projection. Callers can enqueue
    that immutable value on the L0 asynchronous persistence path. Replays and
    late events return ``None``, so synchronous event handlers never need to
    perform idempotency writes themselves.
    """

    def __init__(self, *, max_recent: int = _MAX_RECENT) -> None:
        if type(max_recent) is not int or not 1 <= max_recent <= _MAX_RECENT:
            raise ValueError("max_recent must be an integer in [1, 32]")
        self._max_recent = max_recent
        self._active: dict[ReceiptKey, _TurnState] = {}
        self._recent: OrderedDict[ReceiptKey, TurnExperienceReceipt] = OrderedDict()

    def apply(
        self,
        observation: LifeObservation,
        *,
        model_route: str | None = None,
        response_path: ResponsePath | str | None = None,
    ) -> TurnExperienceReceipt | None:
        """Apply one observation and return a changed projection, if any."""

        if not isinstance(observation, LifeObservation):
            raise TypeError("observation must be a LifeObservation")
        if observation.session_id is None or observation.request_id is None:
            return None

        key = (observation.session_id, observation.request_id)
        state = self._active.get(key)
        known = self._recent.get(key)
        if state is None:
            if known is not None or observation.kind is not ObservationKind.REQUEST_STARTED:
                return None
            state = self._start_state(observation, model_route, response_path)
            receipt = self._build_receipt(state)
            self._active[key] = state
            self._remember(receipt)
            return receipt

        if observation.source_boot_id != state.source_boot_id:
            raise ValueError("observation boot does not match the active turn")
        if observation.kind is ObservationKind.REQUEST_STARTED:
            return None
        if self._is_replay_or_late(state, observation):
            return None

        terminal = _TERMINALS.get(observation.kind)
        if terminal is not None and observation.outcome is not terminal[1]:
            raise ValueError(
                f"{observation.kind.value} requires {terminal[1].value} outcome"
            )
        normalized_model_route = (
            _model_route(model_route) if model_route is not None else None
        )
        normalized_response_path = (
            _response_path(response_path) if response_path is not None else None
        )

        if normalized_model_route is not None:
            if (
                state.model_route is not None
                and state.model_route != normalized_model_route
            ):
                raise ValueError("model_route cannot change within a turn")
            state.model_route = normalized_model_route
        if normalized_response_path is not None:
            state.response_path = _merge_response_path(
                state.response_path,
                normalized_response_path,
            )

        self._record_observation(state, observation)
        if terminal is None:
            receipt = self._build_receipt(state)
        else:
            outcome, _ = terminal
            receipt = self._build_receipt(
                state,
                outcome=outcome,
                completeness=ReceiptCompleteness.COMPLETE,
                terminal=observation,
            )
            self._active.pop(key, None)

        self._remember(receipt)
        return receipt

    def receipt_for(
        self,
        session_id: str,
        request_id: str,
    ) -> TurnExperienceReceipt | None:
        """Return a retained projection without changing recency ordering."""

        return self._recent.get((session_id, request_id))

    def recent_receipts(self) -> tuple[TurnExperienceReceipt, ...]:
        """Return retained projections from oldest to newest."""

        return tuple(self._recent.values())

    def latest_receipt(self) -> TurnExperienceReceipt | None:
        if not self._recent:
            return None
        return next(reversed(self._recent.values()))

    @property
    def pending_count(self) -> int:
        return len(self._active)

    def recover_after_restart(
        self,
        persisted_receipts: Iterable[TurnExperienceReceipt],
        *,
        recovered_at_utc: str,
        recovery_event_id: str | None = None,
    ) -> tuple[TurnExperienceReceipt, ...]:
        """Mark persisted incomplete turns interrupted without forging terminals.

        Terminal and previously recovered receipts are retained unchanged. Exact
        duplicate inputs are accepted, while conflicting rows for one key are
        rejected so recovery does not guess which history is authoritative.
        """

        unique: OrderedDict[ReceiptKey, TurnExperienceReceipt] = OrderedDict()
        for receipt in persisted_receipts:
            if not isinstance(receipt, TurnExperienceReceipt):
                raise TypeError("persisted receipt must be a TurnExperienceReceipt")
            key = (receipt.session_id, receipt.request_id)
            prior = unique.get(key)
            if prior is not None:
                if prior != receipt:
                    raise ValueError("conflicting persisted receipts for one turn")
                continue
            unique[key] = receipt

        staged: list[tuple[ReceiptKey, TurnExperienceReceipt, bool]] = []
        for key, receipt in unique.items():
            retained = self._recent.get(key)
            if retained is not None:
                if retained == receipt or (
                    retained.completeness
                    is ReceiptCompleteness.INTERRUPTED_BY_RESTART
                    and receipt.completeness is ReceiptCompleteness.INCOMPLETE
                ):
                    continue
                raise ValueError("conflicting retained receipt for one turn")

            if receipt.completeness is not ReceiptCompleteness.INCOMPLETE:
                staged.append((key, receipt, False))
                continue
            if receipt.outcome is not TurnOutcome.UNKNOWN or any(
                value is not None
                for value in (
                    receipt.terminal_event_id,
                    receipt.terminal_event_sequence,
                    receipt.terminal_event_sequence_domain,
                )
            ):
                raise ValueError("incomplete restart receipt cannot contain a terminal")
            if _timestamp(recovered_at_utc) < _timestamp(receipt.ended_at_utc):
                raise ValueError("recovered_at_utc cannot precede the persisted receipt")

            recovered_receipt = _recover_receipt(
                receipt,
                recovered_at_utc=recovered_at_utc,
                recovery_event_id=recovery_event_id,
            )
            staged.append((key, recovered_receipt, True))

        recovered: list[TurnExperienceReceipt] = []
        for key, receipt, was_recovered in staged:
            self._active.pop(key, None)
            self._remember(receipt)
            if was_recovered:
                recovered.append(receipt)

        return tuple(recovered)

    def _start_state(
        self,
        observation: LifeObservation,
        model_route: str | None,
        response_path: ResponsePath | str | None,
    ) -> _TurnState:
        assert observation.session_id is not None
        assert observation.request_id is not None
        state = _TurnState(
            receipt_id=_receipt_id(observation.session_id, observation.request_id),
            session_id=observation.session_id,
            request_id=observation.request_id,
            input_provenance=observation.input_provenance,
            source_boot_id=observation.source_boot_id,
            first_event_id=observation.source_event_id,
            first_event_sequence=observation.sequence,
            first_event_sequence_domain=observation.sequence_domain,
            started_at_utc=observation.occurred_at_utc,
            ended_at_utc=observation.occurred_at_utc,
            model_route=_model_route(model_route) if model_route is not None else None,
            response_path=_response_path(response_path or ResponsePath.MODEL),
            last_sequences={observation.sequence_domain: observation.sequence},
        )
        state.seen_event_ids.append(observation.source_event_id)
        return state

    @staticmethod
    def _is_replay_or_late(
        state: _TurnState,
        observation: LifeObservation,
    ) -> bool:
        if observation.source_event_id in state.seen_event_ids:
            return True
        prior_sequence = state.last_sequences.get(observation.sequence_domain)
        if prior_sequence is not None and observation.sequence <= prior_sequence:
            return True
        if _timestamp(observation.occurred_at_utc) < _timestamp(state.ended_at_utc):
            return True
        return False

    @staticmethod
    def _record_observation(
        state: _TurnState,
        observation: LifeObservation,
    ) -> None:
        state.last_sequences[observation.sequence_domain] = observation.sequence
        state.seen_event_ids.append(observation.source_event_id)
        state.ended_at_utc = observation.occurred_at_utc

        if observation.kind not in _NON_ACTIVITY_KINDS:
            code = observation.kind.value
            if code not in state.activity_kinds and len(state.activity_kinds) < 32:
                state.activity_kinds.append(code)
        if observation.kind is ObservationKind.TOOL_STARTED:
            state.tool_count += 1
            if state.response_path is not ResponsePath.TOOL:
                state.response_path = _merge_response_path(
                    state.response_path,
                    ResponsePath.TOOL,
                )
        elif observation.kind is ObservationKind.APPROVAL_REQUIRED:
            state.approval_outcome = ApprovalOutcome.UNKNOWN
        elif observation.kind is ObservationKind.APPROVAL_RESOLVED:
            if observation.outcome is ObservationOutcome.APPROVED:
                state.approval_outcome = ApprovalOutcome.APPROVED
            elif observation.outcome is ObservationOutcome.DENIED:
                state.approval_outcome = ApprovalOutcome.DENIED
            else:
                state.approval_outcome = ApprovalOutcome.UNKNOWN
        elif observation.kind is ObservationKind.INTERACTION_INTERRUPTED:
            state.interruption_count += 1
        elif (
            observation.kind is ObservationKind.GOAL_VERIFIED
            and observation.outcome is ObservationOutcome.VERIFIED
        ):
            state.goal_verified = True

    @staticmethod
    def _build_receipt(
        state: _TurnState,
        *,
        outcome: TurnOutcome = TurnOutcome.UNKNOWN,
        completeness: ReceiptCompleteness = ReceiptCompleteness.INCOMPLETE,
        terminal: LifeObservation | None = None,
    ) -> TurnExperienceReceipt:
        payload = {
            "schema_version": 1,
            "receipt_id": state.receipt_id,
            "session_id": state.session_id,
            "request_id": state.request_id,
            "input_provenance": state.input_provenance.to_dict(),
            "source_boot_id": state.source_boot_id,
            "first_event_id": state.first_event_id,
            "first_event_sequence": state.first_event_sequence,
            "first_event_sequence_domain": state.first_event_sequence_domain,
            "started_at_utc": state.started_at_utc,
            "ended_at_utc": state.ended_at_utc,
            "outcome": outcome.value,
            "terminal_event_id": terminal.source_event_id if terminal else None,
            "terminal_event_sequence": terminal.sequence if terminal else None,
            "terminal_event_sequence_domain": (
                terminal.sequence_domain if terminal else None
            ),
            "recovery_event_id": None,
            "recovered_at_utc": None,
            "activity_kinds": list(state.activity_kinds),
            "tool_count": state.tool_count,
            "approval_outcome": state.approval_outcome.value,
            "interruption_count": state.interruption_count,
            "goal_verified": state.goal_verified,
            "model_route": state.model_route,
            "response_path": state.response_path.value,
            "completeness": completeness.value,
            "privacy_class": PrivacyClass.LOCAL_INTERNAL.value,
            "retention_class": RetentionClass.OPERATIONAL.value,
        }
        payload["content_hash"] = canonical_content_hash(payload)
        return TurnExperienceReceipt.from_dict(payload)

    def _remember(self, receipt: TurnExperienceReceipt) -> None:
        key = (receipt.session_id, receipt.request_id)
        self._recent[key] = receipt
        self._recent.move_to_end(key)
        while len(self._recent) > self._max_recent:
            self._recent.popitem(last=False)


def _recover_receipt(
    receipt: TurnExperienceReceipt,
    *,
    recovered_at_utc: str,
    recovery_event_id: str | None,
) -> TurnExperienceReceipt:
    payload = receipt.to_dict()
    payload.update(
        {
            "ended_at_utc": recovered_at_utc,
            "outcome": TurnOutcome.INTERRUPTED.value,
            "terminal_event_id": None,
            "terminal_event_sequence": None,
            "terminal_event_sequence_domain": None,
            "recovery_event_id": recovery_event_id,
            "recovered_at_utc": recovered_at_utc if recovery_event_id else None,
            "completeness": ReceiptCompleteness.INTERRUPTED_BY_RESTART.value,
        }
    )
    payload.pop("content_hash", None)
    payload["content_hash"] = canonical_content_hash(payload)
    return TurnExperienceReceipt.from_dict(payload)


def _receipt_id(session_id: str, request_id: str) -> str:
    digest = hashlib.sha256(
        f"l1-turn-receipt-v1\0{session_id}\0{request_id}".encode("utf-8")
    ).hexdigest()
    return f"receipt-{digest[:32]}"


def _response_path(value: ResponsePath | str) -> ResponsePath:
    if isinstance(value, ResponsePath):
        return value
    try:
        return ResponsePath(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("response_path must be a known ResponsePath") from exc


def _model_route(value: str) -> str:
    if type(value) is not str or _MACHINE_CODE.fullmatch(value) is None:
        raise ValueError("model_route must be a bounded machine code")
    return value


def _merge_response_path(current: ResponsePath, incoming: ResponsePath) -> ResponsePath:
    if current is incoming or current is ResponsePath.MIXED:
        return current
    if incoming is ResponsePath.MIXED:
        return incoming
    return ResponsePath.MIXED


def _timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")


__all__ = ["TurnExperienceProjector"]
