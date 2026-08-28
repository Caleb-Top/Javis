"""Conservative, durable gating for proactive L5 suggestions.

Candidate generation is deliberately separate from presentation.  This module
only spends a presentation budget immediately before a caller renders or speaks
the suggestion; it never grants permission to execute an action.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (
    AudienceScope,
    QuietHoursV1,
    SuggestionBudgetState,
    SuggestionBudgetV1,
    SuggestionCategory,
    SuggestionOutcome,
    canonical_content_hash,
    canonical_json_bytes,
)
from .store import (
    ConsumeSuggestionCommand,
    IntentionStore,
    IntentionStoreConflictError,
    PersistSuggestionBudgetCommand,
)


DEFAULT_GLOBAL_MAX_PRESENTATIONS = 2
DEFAULT_CATEGORY_MAX_PRESENTATIONS = 1
DEFAULT_COOLDOWN = timedelta(hours=4)
DEFAULT_REJECTION_SUPPRESSION = timedelta(days=7)
DEFAULT_QUIET_START_LOCAL = "22:00"
DEFAULT_QUIET_END_LOCAL = "08:00"
_SAFETY_AUDIT_MAX_PRESENTATIONS = 1000
_MAX_IDEMPOTENCY_CACHE = 1024


class SuggestionBudgetError(RuntimeError):
    """Stable policy or idempotency rejection without suggestion content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class SuggestionBudgetPolicy:
    """User-visible defaults governing one owner's proactive suggestions."""

    timezone_id: str = "UTC"
    global_max_presentations: int = DEFAULT_GLOBAL_MAX_PRESENTATIONS
    category_max_presentations: int = DEFAULT_CATEGORY_MAX_PRESENTATIONS
    cooldown: timedelta = DEFAULT_COOLDOWN
    rejection_suppression: timedelta = DEFAULT_REJECTION_SUPPRESSION
    quiet_start_local: str = DEFAULT_QUIET_START_LOCAL
    quiet_end_local: str = DEFAULT_QUIET_END_LOCAL
    global_budget_revision: int = 1

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone_id)
        except (TypeError, ZoneInfoNotFoundError) as exc:
            raise ValueError("timezone_id must be an available IANA timezone") from exc
        if (
            type(self.global_max_presentations) is not int
            or not 0 <= self.global_max_presentations <= 1000
        ):
            raise ValueError("global_max_presentations must be an integer in [0, 1000]")
        if (
            type(self.category_max_presentations) is not int
            or not 0 <= self.category_max_presentations <= 1000
        ):
            raise ValueError("category_max_presentations must be an integer in [0, 1000]")
        if type(self.global_budget_revision) is not int or self.global_budget_revision < 1:
            raise ValueError("global_budget_revision must be a positive integer")
        if not isinstance(self.cooldown, timedelta) or self.cooldown < timedelta(0):
            raise ValueError("cooldown must be a non-negative timedelta")
        if (
            not isinstance(self.rejection_suppression, timedelta)
            or self.rejection_suppression <= timedelta(0)
        ):
            raise ValueError("rejection_suppression must be a positive timedelta")
        QuietHoursV1(
            timezone_id=self.timezone_id,
            starts_at_local=self.quiet_start_local,
            ends_at_local=self.quiet_end_local,
        )

    @property
    def quiet_hours(self) -> QuietHoursV1:
        return QuietHoursV1(
            timezone_id=self.timezone_id,
            starts_at_local=self.quiet_start_local,
            ends_at_local=self.quiet_end_local,
        )


@dataclass(frozen=True, slots=True)
class SuggestionPresentationDecision:
    """A presentation-only decision; it is never an action authorization."""

    allowed: bool
    reason_code: str
    budget: SuggestionBudgetV1
    visual_allowed: bool
    sound_allowed: bool
    consumed: bool
    count_bypassed: bool
    idempotent_replay: bool = False
    execution_authorized: bool = field(default=False, init=False)
    authorization: None = field(default=None, init=False)

    @property
    def audio_allowed(self) -> bool:
        return self.sound_allowed

    @property
    def grants_execution(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class _CachedDecision:
    fingerprint: str
    decision: SuggestionPresentationDecision


_LOCKS_GUARD = threading.Lock()
_DATABASE_LOCKS: dict[str, threading.RLock] = {}


def _database_lock(store: IntentionStore) -> threading.RLock:
    key = str(store.database_path.resolve()).casefold()
    with _LOCKS_GUARD:
        return _DATABASE_LOCKS.setdefault(key, threading.RLock())


def _require_aware(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be an aware datetime")
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    normalized = _require_aware(value, "timestamp")
    milliseconds = normalized.microsecond // 1000
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def _parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _parse_local_time(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def local_window_bounds(
    now: datetime,
    timezone_id: str,
    *,
    days: int = 1,
) -> tuple[datetime, datetime]:
    """Return a fixed local-calendar window, preserving 23/25 hour DST days."""

    current = _require_aware(now, "now")
    if type(days) is not int or days < 1:
        raise ValueError("days must be a positive integer")
    try:
        zone = ZoneInfo(timezone_id)
    except (TypeError, ZoneInfoNotFoundError) as exc:
        raise ValueError("timezone_id must be an available IANA timezone") from exc
    local_day = current.astimezone(zone).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=zone)
    end_local = datetime.combine(local_day + timedelta(days=days), time.min, tzinfo=zone)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def is_in_quiet_hours(now: datetime, quiet_hours: QuietHoursV1) -> bool:
    """Evaluate local quiet hours from an instant using the IANA zone database."""

    current = _require_aware(now, "now")
    if not isinstance(quiet_hours, QuietHoursV1):
        raise TypeError("quiet_hours must be QuietHoursV1")
    try:
        zone = ZoneInfo(quiet_hours.timezone_id)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("quiet_hours timezone is unavailable") from exc
    local_time = current.astimezone(zone).time().replace(tzinfo=None)
    starts = _parse_local_time(quiet_hours.starts_at_local)
    ends = _parse_local_time(quiet_hours.ends_at_local)
    if starts < ends:
        return starts <= local_time < ends
    return local_time >= starts or local_time < ends


def _sealed_budget(value: SuggestionBudgetV1 | Mapping[str, object], **changes: object) -> SuggestionBudgetV1:
    wire = value.to_dict() if isinstance(value, SuggestionBudgetV1) else dict(value)
    wire.update(changes)
    wire.pop("content_hash", None)
    wire["content_hash"] = canonical_content_hash(wire)
    return SuggestionBudgetV1.from_dict(wire)


def _category(value: SuggestionCategory | str) -> SuggestionCategory:
    if isinstance(value, SuggestionCategory):
        return value
    try:
        return SuggestionCategory(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("category must be a supported SuggestionCategory") from exc


def _outcome(value: SuggestionOutcome | str) -> SuggestionOutcome:
    if isinstance(value, SuggestionOutcome):
        return value
    try:
        return SuggestionOutcome(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("outcome must be a supported SuggestionOutcome") from exc


def _identifier(value: str, field_name: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 256:
        raise ValueError(f"{field_name} must contain 1..256 UTF-8 bytes")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


def _store_key(prefix: str, value: str) -> str:
    candidate = f"{prefix}.{value}"
    if len(candidate.encode("utf-8")) <= 256:
        return candidate
    return f"{prefix}.{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


class SuggestionBudgetGate:
    """Durable policy gate called immediately before suggestion presentation."""

    def __init__(
        self,
        store: IntentionStore,
        *,
        javis_identity_id: str,
        instance_id: str,
        runtime_boot_id: str,
        policy: SuggestionBudgetPolicy | None = None,
        timezone_id: str | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(store, IntentionStore):
            raise TypeError("store must be an IntentionStore")
        self._store = store
        self._javis_identity_id = _identifier(javis_identity_id, "javis_identity_id")
        self._instance_id = _identifier(instance_id, "instance_id")
        self._runtime_boot_id = _identifier(runtime_boot_id, "runtime_boot_id")
        if policy is not None and timezone_id is not None:
            raise ValueError("provide policy or timezone_id, not both")
        self._policy = policy or SuggestionBudgetPolicy(
            timezone_id=timezone_id or "UTC"
        )
        if not isinstance(self._policy, SuggestionBudgetPolicy):
            raise TypeError("policy must be SuggestionBudgetPolicy")
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = _database_lock(store)
        self._idempotency: OrderedDict[str, _CachedDecision] = OrderedDict()

    @property
    def policy(self) -> SuggestionBudgetPolicy:
        return self._policy

    @staticmethod
    def budget_id_for(owner_subject_id: str, category: SuggestionCategory | str) -> str:
        owner = _identifier(owner_subject_id, "owner_subject_id")
        normalized = _category(category)
        digest = hashlib.sha256(
            f"{owner}\x00{normalized.value}".encode("utf-8")
        ).hexdigest()[:32]
        return f"suggestion-budget-{digest}"

    def get_budget(
        self,
        owner_subject_id: str,
        category: SuggestionCategory | str,
    ) -> SuggestionBudgetV1:
        owner = _identifier(owner_subject_id, "owner_subject_id")
        normalized = _category(category)
        with self._lock:
            return self._ensure_budget(owner, normalized, self._clock())

    ensure_budget = get_budget

    def evaluate(
        self,
        owner_subject_id: str,
        category: SuggestionCategory | str,
        *,
        candidate_id: str,
        presented_intention_id: str,
        request_sound: bool = True,
        safety: bool | None = None,
        candidate_metadata: Mapping[str, object] | None = None,
        **untrusted_signals: object,
    ) -> SuggestionPresentationDecision:
        """Evaluate without spending; untrusted relationship/emotion signals are ignored."""

        owner, normalized, candidate, intention = self._validate_presentation(
            owner_subject_id,
            category,
            candidate_id,
            presented_intention_id,
            request_sound,
            safety,
            candidate_metadata,
            untrusted_signals,
        )
        with self._lock:
            now = self._clock()
            budget = self._ensure_budget(owner, normalized, now)
            return self._evaluate_budget(
                budget,
                now=now,
                candidate_id=candidate,
                presented_intention_id=intention,
                request_sound=request_sound,
            )

    def consume_before_present(
        self,
        owner_subject_id: str,
        category: SuggestionCategory | str,
        *,
        candidate_id: str,
        presented_intention_id: str,
        idempotency_key: str,
        request_sound: bool = True,
        safety: bool | None = None,
        candidate_metadata: Mapping[str, object] | None = None,
        **untrusted_signals: object,
    ) -> SuggestionPresentationDecision:
        """Atomically spend one visible presentation immediately before delivery."""

        owner, normalized, candidate, intention = self._validate_presentation(
            owner_subject_id,
            category,
            candidate_id,
            presented_intention_id,
            request_sound,
            safety,
            candidate_metadata,
            untrusted_signals,
        )
        key = _identifier(idempotency_key, "idempotency_key")
        fingerprint = hashlib.sha256(
            canonical_json_bytes(
                {
                    "owner_subject_id": owner,
                    "category": normalized.value,
                    "candidate_id": candidate,
                    "presented_intention_id": intention,
                    "request_sound": request_sound,
                }
            )
        ).hexdigest()

        with self._lock:
            cached = self._idempotency.get(key)
            if cached is not None:
                if cached.fingerprint != fingerprint:
                    raise SuggestionBudgetError("idempotency_conflict")
                self._idempotency.move_to_end(key)
                return self._as_replay(cached.decision)

            persisted = self._persisted_replay(
                _store_key("suggestion.consume", key),
                owner=owner,
                category=normalized,
                candidate_id=candidate,
                presented_intention_id=intention,
                request_sound=request_sound,
            )
            if persisted is not None:
                self._remember(key, fingerprint, persisted)
                return persisted

            now = self._clock()
            budget = self._ensure_budget(owner, normalized, now)
            decision = self._evaluate_budget(
                budget,
                now=now,
                candidate_id=candidate,
                presented_intention_id=intention,
                request_sound=request_sound,
            )
            if not decision.allowed:
                self._remember(key, fingerprint, decision)
                return decision

            used = budget.presentations_used + 1
            if used > budget.max_presentations:
                denied = self._deny(budget, "safety_audit_capacity_exhausted")
                self._remember(key, fingerprint, denied)
                return denied
            cooldown_until = now + self._policy.cooldown
            quiet = is_in_quiet_hours(now, budget.quiet_hours)
            next_state = self._state_for(
                budget,
                now=now,
                presentations_used=used,
                cooldown_until=cooldown_until,
                suppressed_until=_parse_utc(budget.suppressed_until_utc),
                quiet=quiet,
            )
            consumed = _sealed_budget(
                budget,
                revision=budget.revision + 1,
                updated_at_utc=_format_utc(now),
                state=next_state.value,
                presentations_used=used,
                cooldown_until_utc=_format_utc(cooldown_until),
                last_candidate_id=candidate,
                last_presented_intention_id=intention,
                last_outcome=SuggestionOutcome.NONE.value,
            )
            try:
                self._store.execute(
                    ConsumeSuggestionCommand(
                        consumed,
                        candidate,
                        intention,
                        _format_utc(now),
                    ),
                    budget.revision,
                    _store_key("suggestion.consume", key),
                )
            except IntentionStoreConflictError as exc:
                raise SuggestionBudgetError("atomic_consumption_conflict") from exc

            result = SuggestionPresentationDecision(
                allowed=True,
                reason_code=decision.reason_code,
                budget=consumed,
                visual_allowed=True,
                sound_allowed=decision.sound_allowed,
                consumed=True,
                count_bypassed=normalized is SuggestionCategory.SAFETY,
            )
            self._remember(key, fingerprint, result)
            return result

    consume_before_presentation = consume_before_present
    consume = consume_before_present

    def present(
        self,
        owner_subject_id: str,
        category: SuggestionCategory | str,
        *,
        candidate_id: str,
        presented_intention_id: str,
        idempotency_key: str,
        sender: Callable[[SuggestionPresentationDecision], object],
        request_sound: bool = True,
        safety: bool | None = None,
        candidate_metadata: Mapping[str, object] | None = None,
        **untrusted_signals: object,
    ) -> SuggestionPresentationDecision:
        """Spend first, then deliver; sender failures intentionally do not refund."""

        if not callable(sender):
            raise TypeError("sender must be callable")
        decision = self.consume_before_present(
            owner_subject_id,
            category,
            candidate_id=candidate_id,
            presented_intention_id=presented_intention_id,
            idempotency_key=idempotency_key,
            request_sound=request_sound,
            safety=safety,
            candidate_metadata=candidate_metadata,
            **untrusted_signals,
        )
        if decision.allowed:
            sender(decision)
        return decision

    def record_outcome(
        self,
        owner_subject_id: str,
        category: SuggestionCategory | str,
        outcome: SuggestionOutcome | str,
        *,
        idempotency_key: str,
        candidate_id: str | None = None,
    ) -> SuggestionBudgetV1:
        """Record user feedback; an explicit rejection suppresses the category for seven days."""

        owner = _identifier(owner_subject_id, "owner_subject_id")
        normalized = _category(category)
        normalized_outcome = _outcome(outcome)
        key = _identifier(idempotency_key, "idempotency_key")
        if normalized_outcome is SuggestionOutcome.NONE:
            raise ValueError("outcome must describe a user-visible result")
        if candidate_id is not None:
            candidate_id = _identifier(candidate_id, "candidate_id")

        with self._lock:
            now = self._clock()
            budget = self._ensure_budget(owner, normalized, now)
            if candidate_id is not None and budget.last_candidate_id != candidate_id:
                raise SuggestionBudgetError("candidate_mismatch")
            if budget.last_outcome is normalized_outcome:
                return budget
            suppressed = _parse_utc(budget.suppressed_until_utc)
            if normalized_outcome is SuggestionOutcome.REJECTED:
                rejected_until = now + self._policy.rejection_suppression
                suppressed = max(
                    (item for item in (suppressed, rejected_until) if item is not None),
                    default=rejected_until,
                )
            elif normalized_outcome is SuggestionOutcome.ACCEPTED:
                suppressed = None
            next_state = self._state_for(
                budget,
                now=now,
                suppressed_until=suppressed,
            )
            updated = _sealed_budget(
                budget,
                revision=budget.revision + 1,
                updated_at_utc=_format_utc(now),
                state=next_state.value,
                suppressed_until_utc=(
                    None if suppressed is None else _format_utc(suppressed)
                ),
                last_outcome=normalized_outcome.value,
            )
            self._persist_budget(
                updated,
                budget.revision,
                _store_key("suggestion.outcome", key),
            )
            return updated

    def reject(
        self,
        owner_subject_id: str,
        category: SuggestionCategory | str,
        *,
        idempotency_key: str,
        candidate_id: str | None = None,
    ) -> SuggestionBudgetV1:
        return self.record_outcome(
            owner_subject_id,
            category,
            SuggestionOutcome.REJECTED,
            idempotency_key=idempotency_key,
            candidate_id=candidate_id,
        )

    def pause(
        self,
        owner_subject_id: str,
        *,
        until: datetime,
        idempotency_key: str,
        category: SuggestionCategory | str | None = None,
    ) -> tuple[SuggestionBudgetV1, ...]:
        """Pause one category or all categories, including safety."""

        owner = _identifier(owner_subject_id, "owner_subject_id")
        key = _identifier(idempotency_key, "idempotency_key")
        pause_until = _require_aware(until, "until")
        with self._lock:
            now = self._clock()
            if pause_until <= now:
                raise ValueError("until must be in the future")
            categories = (
                tuple(SuggestionCategory)
                if category is None
                else (_category(category),)
            )
            updated: list[SuggestionBudgetV1] = []
            for item in categories:
                budget = self._ensure_budget(owner, item, now)
                existing = _parse_utc(budget.suppressed_until_utc)
                effective_until = max(
                    (value for value in (existing, pause_until) if value is not None),
                    default=pause_until,
                )
                paused = _sealed_budget(
                    budget,
                    revision=budget.revision + 1,
                    updated_at_utc=_format_utc(now),
                    state=SuggestionBudgetState.SUPPRESSED.value,
                    suppressed_until_utc=_format_utc(effective_until),
                )
                self._persist_budget(
                    paused,
                    budget.revision,
                    _store_key(f"suggestion.pause.{item.value}", key),
                )
                updated.append(paused)
            return tuple(updated)

    pause_user = pause

    def resume(
        self,
        owner_subject_id: str,
        *,
        idempotency_key: str,
        category: SuggestionCategory | str | None = None,
    ) -> tuple[SuggestionBudgetV1, ...]:
        """Explicitly clear a user pause or rejection suppression."""

        owner = _identifier(owner_subject_id, "owner_subject_id")
        key = _identifier(idempotency_key, "idempotency_key")
        with self._lock:
            now = self._clock()
            categories = (
                tuple(SuggestionCategory)
                if category is None
                else (_category(category),)
            )
            updated: list[SuggestionBudgetV1] = []
            for item in categories:
                budget = self._ensure_budget(owner, item, now)
                next_state = self._state_for(
                    budget,
                    now=now,
                    suppressed_until=None,
                )
                resumed = _sealed_budget(
                    budget,
                    revision=budget.revision + 1,
                    updated_at_utc=_format_utc(now),
                    state=next_state.value,
                    suppressed_until_utc=None,
                )
                self._persist_budget(
                    resumed,
                    budget.revision,
                    _store_key(f"suggestion.resume.{item.value}", key),
                )
                updated.append(resumed)
            return tuple(updated)

    resume_user = resume

    def _clock(self) -> datetime:
        return _require_aware(self._now(), "SuggestionBudgetGate clock")

    def _validate_presentation(
        self,
        owner_subject_id: str,
        category: SuggestionCategory | str,
        candidate_id: str,
        presented_intention_id: str,
        request_sound: bool,
        safety: bool | None,
        candidate_metadata: Mapping[str, object] | None,
        untrusted_signals: Mapping[str, object],
    ) -> tuple[str, SuggestionCategory, str, str]:
        owner = _identifier(owner_subject_id, "owner_subject_id")
        normalized = _category(category)
        candidate = _identifier(candidate_id, "candidate_id")
        intention = _identifier(presented_intention_id, "presented_intention_id")
        if type(request_sound) is not bool:
            raise ValueError("request_sound must be a boolean")
        if safety is not None and type(safety) is not bool:
            raise ValueError("safety must be a boolean or None")
        if candidate_metadata is not None and not isinstance(candidate_metadata, Mapping):
            raise ValueError("candidate_metadata must be a mapping or None")
        # Relationship, emotion, model urgency and arbitrary injected fields are
        # intentionally neither persisted nor consulted. Category is authoritative.
        _ = candidate_metadata, untrusted_signals, safety
        return owner, normalized, candidate, intention

    def _ensure_budget(
        self,
        owner: str,
        category: SuggestionCategory,
        now: datetime,
    ) -> SuggestionBudgetV1:
        budget_id = self.budget_id_for(owner, category)
        budget = self._store.get_suggestion_budget(budget_id)
        window_start, window_end = local_window_bounds(
            now,
            self._policy.timezone_id,
        )
        maximum = self._maximum_for(category)
        quiet = is_in_quiet_hours(now, self._policy.quiet_hours)
        if budget is None:
            state = self._state_for_values(
                now=now,
                presentations_used=0,
                max_presentations=maximum,
                cooldown_until=None,
                suppressed_until=None,
                quiet=quiet,
            )
            wire: dict[str, object] = {
                "schema_version": 1,
                "budget_id": budget_id,
                "javis_identity_id": self._javis_identity_id,
                "instance_id": self._instance_id,
                "owner_subject_id": owner,
                "participant_ids": [owner],
                "audience": AudienceScope.OWNER_PRIVATE.value,
                "source_event_ids": [],
                "runtime_boot_id": self._runtime_boot_id,
                "created_at_utc": _format_utc(now),
                "updated_at_utc": _format_utc(now),
                "expires_at_utc": _format_utc(window_end),
                "privacy_class": "user_private",
                "retention_class": "operational",
                "state": state.value,
                "revision": 1,
                "provenance": "intention.service",
                "category": category.value,
                "window_started_at_utc": _format_utc(window_start),
                "window_ends_at_utc": _format_utc(window_end),
                "max_presentations": maximum,
                "presentations_used": 0,
                "global_budget_revision": self._policy.global_budget_revision,
                "cooldown_until_utc": None,
                "quiet_hours": self._policy.quiet_hours.to_dict(),
                "suppressed_until_utc": None,
                "last_candidate_id": None,
                "last_presented_intention_id": None,
                "last_outcome": SuggestionOutcome.NONE.value,
            }
            created = _sealed_budget(wire)
            self._persist_budget(
                created,
                0,
                _store_key("suggestion.budget.create", budget_id),
            )
            return created

        if (
            budget.owner_subject_id != owner
            or budget.category is not category
            or budget.javis_identity_id != self._javis_identity_id
            or budget.instance_id != self._instance_id
        ):
            raise SuggestionBudgetError("budget_identity_mismatch")

        stored_start = _parse_utc(budget.window_started_at_utc)
        stored_end = _parse_utc(budget.window_ends_at_utc)
        assert stored_start is not None and stored_end is not None
        rollover = not stored_start <= now < stored_end
        if rollover:
            used = 0
            cooldown = None
        else:
            used = budget.presentations_used
            cooldown = _parse_utc(budget.cooldown_until_utc)
            if cooldown is not None and cooldown <= now:
                cooldown = None
        suppressed = _parse_utc(budget.suppressed_until_utc)
        if suppressed is not None and suppressed <= now:
            suppressed = None
        effective_maximum = max(maximum, used)
        next_state = self._state_for_values(
            now=now,
            presentations_used=used,
            max_presentations=effective_maximum,
            cooldown_until=cooldown,
            suppressed_until=suppressed,
            quiet=quiet,
        )
        expected: dict[str, object] = {
            "state": next_state.value,
            "window_started_at_utc": _format_utc(window_start if rollover else stored_start),
            "window_ends_at_utc": _format_utc(window_end if rollover else stored_end),
            "expires_at_utc": _format_utc(window_end if rollover else stored_end),
            "max_presentations": effective_maximum,
            "presentations_used": used,
            "global_budget_revision": self._policy.global_budget_revision,
            "cooldown_until_utc": None if cooldown is None else _format_utc(cooldown),
            "quiet_hours": self._policy.quiet_hours.to_dict(),
            "suppressed_until_utc": None if suppressed is None else _format_utc(suppressed),
        }
        if all(budget.to_dict()[name] == value for name, value in expected.items()):
            return budget
        refreshed = _sealed_budget(
            budget,
            revision=budget.revision + 1,
            updated_at_utc=_format_utc(now),
            runtime_boot_id=self._runtime_boot_id,
            **expected,
        )
        self._persist_budget(
            refreshed,
            budget.revision,
            _store_key(
                "suggestion.budget.refresh",
                f"{budget_id}.{refreshed.revision}.{refreshed.content_hash}",
            ),
        )
        return refreshed

    def _evaluate_budget(
        self,
        budget: SuggestionBudgetV1,
        *,
        now: datetime,
        candidate_id: str,
        presented_intention_id: str,
        request_sound: bool,
    ) -> SuggestionPresentationDecision:
        duplicate = self._consumption_for_candidate(candidate_id)
        if duplicate is not None:
            return self._deny(budget, "duplicate_candidate")
        if self._consumption_for_intention(presented_intention_id):
            return self._deny(budget, "duplicate_presented_intention")

        suppressed = _parse_utc(budget.suppressed_until_utc)
        if suppressed is not None and now < suppressed:
            return self._deny(budget, "user_suppressed")
        cooldown = _parse_utc(budget.cooldown_until_utc)
        if cooldown is not None and now < cooldown:
            return self._deny(budget, "category_cooldown")

        quiet = is_in_quiet_hours(now, budget.quiet_hours)
        safety = budget.category is SuggestionCategory.SAFETY
        if quiet and not safety:
            return self._deny(budget, "quiet_hours")
        if not safety:
            if budget.presentations_used >= budget.max_presentations:
                return self._deny(budget, "category_budget_exhausted")
            if self._global_presentations_used(budget.owner_subject_id, now) >= (
                self._policy.global_max_presentations
            ):
                return self._deny(budget, "global_budget_exhausted")

        return SuggestionPresentationDecision(
            allowed=True,
            reason_code=("safety_visual_only" if quiet else "presentation_allowed"),
            budget=budget,
            visual_allowed=True,
            sound_allowed=request_sound and not quiet,
            consumed=False,
            count_bypassed=safety,
        )

    def _state_for(
        self,
        budget: SuggestionBudgetV1,
        *,
        now: datetime,
        presentations_used: int | None = None,
        cooldown_until: datetime | None | object = ...,
        suppressed_until: datetime | None | object = ...,
        quiet: bool | None = None,
    ) -> SuggestionBudgetState:
        cooldown = (
            _parse_utc(budget.cooldown_until_utc)
            if cooldown_until is ...
            else cooldown_until
        )
        suppressed = (
            _parse_utc(budget.suppressed_until_utc)
            if suppressed_until is ...
            else suppressed_until
        )
        assert cooldown is None or isinstance(cooldown, datetime)
        assert suppressed is None or isinstance(suppressed, datetime)
        return self._state_for_values(
            now=now,
            presentations_used=(
                budget.presentations_used
                if presentations_used is None
                else presentations_used
            ),
            max_presentations=budget.max_presentations,
            cooldown_until=cooldown,
            suppressed_until=suppressed,
            quiet=(
                is_in_quiet_hours(now, budget.quiet_hours)
                if quiet is None
                else quiet
            ),
        )

    @staticmethod
    def _state_for_values(
        *,
        now: datetime,
        presentations_used: int,
        max_presentations: int,
        cooldown_until: datetime | None,
        suppressed_until: datetime | None,
        quiet: bool,
    ) -> SuggestionBudgetState:
        if suppressed_until is not None and now < suppressed_until:
            return SuggestionBudgetState.SUPPRESSED
        if quiet:
            return SuggestionBudgetState.QUIET
        if presentations_used >= max_presentations:
            return SuggestionBudgetState.EXHAUSTED
        if cooldown_until is not None and now < cooldown_until:
            return SuggestionBudgetState.COOLING_DOWN
        return SuggestionBudgetState.AVAILABLE

    def _maximum_for(self, category: SuggestionCategory) -> int:
        if category is SuggestionCategory.SAFETY:
            return _SAFETY_AUDIT_MAX_PRESENTATIONS
        return self._policy.category_max_presentations

    def _global_presentations_used(self, owner: str, now: datetime) -> int:
        window_start, window_end = local_window_bounds(now, self._policy.timezone_id)
        start_wire = _format_utc(window_start)
        end_wire = _format_utc(window_end)
        used = 0
        for category in SuggestionCategory:
            if category is SuggestionCategory.SAFETY:
                continue
            budget = self._store.get_suggestion_budget(
                self.budget_id_for(owner, category)
            )
            if (
                budget is not None
                and budget.global_budget_revision == self._policy.global_budget_revision
                and budget.window_started_at_utc == start_wire
                and budget.window_ends_at_utc == end_wire
            ):
                used += budget.presentations_used
        return used

    def _persist_budget(
        self,
        budget: SuggestionBudgetV1,
        expected_revision: int,
        idempotency_key: str,
    ) -> None:
        try:
            self._store.execute(
                PersistSuggestionBudgetCommand(budget),
                expected_revision,
                idempotency_key,
            )
        except IntentionStoreConflictError as exc:
            raise SuggestionBudgetError("budget_cas_conflict") from exc

    def _consumption_for_candidate(self, candidate_id: str) -> tuple[str, str] | None:
        row = self._read_one(
            "SELECT budget_id, presented_intention_id FROM suggestion_consumptions "
            "WHERE candidate_id = ? LIMIT 1",
            (candidate_id,),
        )
        if row is None:
            return None
        return str(row[0]), str(row[1])

    def _consumption_for_intention(self, intention_id: str) -> bool:
        return self._read_one(
            "SELECT 1 FROM suggestion_consumptions WHERE presented_intention_id = ? LIMIT 1",
            (intention_id,),
        ) is not None

    def _persisted_replay(
        self,
        store_key: str,
        *,
        owner: str,
        category: SuggestionCategory,
        candidate_id: str,
        presented_intention_id: str,
        request_sound: bool,
    ) -> SuggestionPresentationDecision | None:
        row = self._read_one(
            "SELECT command_json FROM command_idempotency WHERE idempotency_key = ?",
            (store_key,),
        )
        if row is None:
            return None
        try:
            command = json.loads(str(row[0]))
            budget = SuggestionBudgetV1.from_dict(command["budget"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SuggestionBudgetError("idempotency_record_invalid") from exc
        if (
            command.get("operation") != "consume_suggestion"
            or budget.owner_subject_id != owner
            or budget.category is not category
            or command.get("candidate_id") != candidate_id
            or command.get("presented_intention_id") != presented_intention_id
        ):
            raise SuggestionBudgetError("idempotency_conflict")
        quiet = is_in_quiet_hours(
            _parse_utc(command.get("consumed_at_utc")) or self._clock(),
            budget.quiet_hours,
        )
        return SuggestionPresentationDecision(
            allowed=True,
            reason_code=("safety_visual_only" if quiet else "presentation_allowed"),
            budget=budget,
            visual_allowed=True,
            sound_allowed=request_sound and not quiet,
            consumed=True,
            count_bypassed=category is SuggestionCategory.SAFETY,
            idempotent_replay=True,
        )

    def _read_one(
        self,
        query: str,
        parameters: tuple[object, ...],
    ) -> tuple[Any, ...] | None:
        uri = f"file:{self._store.database_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as connection:
            row = connection.execute(query, parameters).fetchone()
        return None if row is None else tuple(row)

    @staticmethod
    def _deny(
        budget: SuggestionBudgetV1,
        reason_code: str,
    ) -> SuggestionPresentationDecision:
        return SuggestionPresentationDecision(
            allowed=False,
            reason_code=reason_code,
            budget=budget,
            visual_allowed=False,
            sound_allowed=False,
            consumed=False,
            count_bypassed=False,
        )

    @staticmethod
    def _as_replay(
        decision: SuggestionPresentationDecision,
    ) -> SuggestionPresentationDecision:
        return SuggestionPresentationDecision(
            allowed=decision.allowed,
            reason_code=decision.reason_code,
            budget=decision.budget,
            visual_allowed=decision.visual_allowed,
            sound_allowed=decision.sound_allowed,
            consumed=decision.consumed,
            count_bypassed=decision.count_bypassed,
            idempotent_replay=True,
        )

    def _remember(
        self,
        key: str,
        fingerprint: str,
        decision: SuggestionPresentationDecision,
    ) -> None:
        self._idempotency[key] = _CachedDecision(fingerprint, decision)
        self._idempotency.move_to_end(key)
        while len(self._idempotency) > _MAX_IDEMPOTENCY_CACHE:
            self._idempotency.popitem(last=False)


SuggestionBudgetService = SuggestionBudgetGate
window_bounds = local_window_bounds


__all__ = [
    "DEFAULT_CATEGORY_MAX_PRESENTATIONS",
    "DEFAULT_COOLDOWN",
    "DEFAULT_GLOBAL_MAX_PRESENTATIONS",
    "DEFAULT_QUIET_END_LOCAL",
    "DEFAULT_QUIET_START_LOCAL",
    "DEFAULT_REJECTION_SUPPRESSION",
    "SuggestionBudgetError",
    "SuggestionBudgetGate",
    "SuggestionBudgetPolicy",
    "SuggestionBudgetService",
    "SuggestionPresentationDecision",
    "is_in_quiet_hours",
    "local_window_bounds",
    "window_bounds",
]
