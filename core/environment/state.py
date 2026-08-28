"""In-memory reducer for governed, minimized environment facts."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Collection
from datetime import datetime, timedelta, timezone
from typing import Any

from core.environment.contracts import (
    MAX_FACTS,
    MAX_SNAPSHOT_TTL_SECONDS,
    MAX_STALE_KEYS,
    EnvironmentFact,
    EnvironmentObservationV1,
    EnvironmentSnapshotV1,
    FactKey,
    SourceHealth,
    SourceKind,
    validate_observation_for_reducer,
)


DEFAULT_SEEN_CAPACITY = 2_048
DEFAULT_STALE_TTL_SECONDS = 300
DEFAULT_SNAPSHOT_TTL_SECONDS = 5


def _wire_time(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    milliseconds = value.microsecond // 1_000
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _bounded_positive_int(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{field} must be between 1 and {maximum}")
    return value


class EnvironmentReducer:
    """Reduce observations into one bounded, boot-scoped snapshot.

    The reducer performs no I/O. It retains minimized contracts only and never
    stores source payloads or raw buffers.
    """

    def __init__(
        self,
        runtime_boot_id: str,
        *,
        now: Callable[[], float | datetime] | None = None,
        max_facts: int = MAX_FACTS,
        max_stale_keys: int = MAX_STALE_KEYS,
        seen_capacity: int = DEFAULT_SEEN_CAPACITY,
        stale_ttl_seconds: int = DEFAULT_STALE_TTL_SECONDS,
        snapshot_ttl_seconds: int = DEFAULT_SNAPSHOT_TTL_SECONDS,
    ) -> None:
        if type(runtime_boot_id) is not str or not runtime_boot_id.strip():
            raise ValueError("runtime_boot_id must be a non-empty string")
        self.runtime_boot_id = runtime_boot_id.strip()
        self._max_facts = _bounded_positive_int(max_facts, "max_facts", MAX_FACTS)
        self._max_stale_keys = _bounded_positive_int(
            max_stale_keys, "max_stale_keys", MAX_STALE_KEYS
        )
        self._seen_capacity = _bounded_positive_int(
            seen_capacity, "seen_capacity", 65_536
        )
        self._stale_ttl_seconds = _bounded_positive_int(
            stale_ttl_seconds, "stale_ttl_seconds", 86_400
        )
        self._snapshot_ttl_seconds = _bounded_positive_int(
            snapshot_ttl_seconds,
            "snapshot_ttl_seconds",
            MAX_SNAPSHOT_TTL_SECONDS,
        )
        self._now = now or time.time
        self._lock = threading.RLock()
        self._facts: dict[FactKey, EnvironmentFact] = {}
        self._stale: OrderedDict[FactKey, datetime] = OrderedDict()
        self._source_health: dict[SourceKind, SourceHealth] = {
            source: SourceHealth.UNKNOWN
            for source in SourceKind
            if source is not SourceKind.RAW_MEDIA
        }
        self._source_sequences: dict[SourceKind, int] = {}
        self._seen_observation_ids: set[str] = set()
        self._seen_observation_order: deque[str] = deque()
        self._seen_source_event_ids: set[str] = set()
        self._seen_source_event_order: deque[str] = deque()
        self._revision = 0
        self._permission_revision = 0

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def permission_revision(self) -> int:
        with self._lock:
            return self._permission_revision

    @property
    def fact_count(self) -> int:
        with self._lock:
            return len(self._facts)

    @property
    def stale_count(self) -> int:
        with self._lock:
            return len(self._stale)

    def reduce(self, observation: EnvironmentObservationV1) -> bool:
        """Apply one observation; duplicates and out-of-order input are no-ops."""

        if not isinstance(observation, EnvironmentObservationV1):
            raise TypeError("observation must be an EnvironmentObservationV1")
        with self._lock:
            if observation.runtime_boot_id != self.runtime_boot_id:
                raise ValueError("runtime_boot_id does not match the active runtime boot")
            if (
                observation.observation_id in self._seen_observation_ids
                or observation.source_event_id in self._seen_source_event_ids
            ):
                return False
            previous_sequence = self._source_sequences.get(observation.source_kind)
            if previous_sequence is not None and observation.sequence <= previous_sequence:
                return False

            now = self._clock()
            validate_observation_for_reducer(
                observation,
                expected_runtime_boot_id=self.runtime_boot_id,
                previous_sequence=previous_sequence,
                received_at_utc=now,
                seen_observation_ids=self._seen_observation_ids,
                seen_source_event_ids=self._seen_source_event_ids,
            )
            self._expire_locked(now)
            for fact in observation.to_facts():
                self._facts[fact.key] = fact
                self._stale.pop(fact.key, None)
            self._source_sequences[observation.source_kind] = observation.sequence
            self._source_health[observation.source_kind] = SourceHealth.HEALTHY
            self._remember_locked(
                observation.observation_id,
                self._seen_observation_ids,
                self._seen_observation_order,
            )
            self._remember_locked(
                observation.source_event_id,
                self._seen_source_event_ids,
                self._seen_source_event_order,
            )
            self._evict_excess_facts_locked(now)
            self._revision += 1
            return True

    ingest = reduce

    def set_source_health(
        self, source_kind: SourceKind | str, health: SourceHealth | str
    ) -> bool:
        source = SourceKind(source_kind)
        value = SourceHealth(health)
        if source is SourceKind.RAW_MEDIA:
            raise ValueError("raw media cannot enter environment state")
        with self._lock:
            now = self._clock()
            changed = self._expire_locked(now)
            if self._source_health.get(source) is value:
                if changed:
                    self._revision += 1
                return changed
            self._source_health[source] = value
            if value is SourceHealth.DISABLED:
                changed = self._invalidate_locked(now, sources={source}) or changed
            self._revision += 1
            return True

    def set_permission_revision(
        self,
        revision: int,
        *,
        invalidated_grant_id_hashes: Collection[str] = (),
        invalidated_sources: Collection[SourceKind | str] = (),
    ) -> bool:
        if type(revision) is not int or revision < 0:
            raise ValueError("permission revision must be a non-negative integer")
        hashes = frozenset(invalidated_grant_id_hashes)
        if any(type(value) is not str or len(value) != 64 for value in hashes):
            raise ValueError("invalidated grant IDs must be SHA-256 hex strings")
        sources = frozenset(SourceKind(value) for value in invalidated_sources)
        if SourceKind.RAW_MEDIA in sources:
            raise ValueError("raw media cannot enter environment state")
        with self._lock:
            if revision < self._permission_revision:
                return False
            now = self._clock()
            changed = self._expire_locked(now)
            changed = self._invalidate_locked(
                now, grant_hashes=hashes, sources=sources
            ) or changed
            if revision == self._permission_revision and not changed:
                return False
            self._permission_revision = revision
            self._revision += 1
            return True

    def snapshot(self) -> EnvironmentSnapshotV1:
        with self._lock:
            now = self._clock()
            if self._expire_locked(now):
                self._revision += 1
            return self._snapshot_locked(now)

    def reset(self) -> bool:
        """Discard all volatile facts while keeping this boot identity."""

        with self._lock:
            if not self._facts and not self._stale and not self._source_sequences:
                return False
            self._facts.clear()
            self._stale.clear()
            self._source_sequences.clear()
            self._seen_observation_ids.clear()
            self._seen_observation_order.clear()
            self._seen_source_event_ids.clear()
            self._seen_source_event_order.clear()
            self._source_health = {
                source: SourceHealth.UNKNOWN
                for source in SourceKind
                if source is not SourceKind.RAW_MEDIA
            }
            self._revision += 1
            return True

    def _snapshot_locked(self, now: datetime) -> EnvironmentSnapshotV1:
        facts = tuple(sorted(self._facts.values(), key=lambda fact: fact.key))
        expires_at = now + timedelta(seconds=self._snapshot_ttl_seconds)
        if facts:
            expires_at = min(
                expires_at,
                min(_parse_time(fact.valid_until_utc) for fact in facts),
            )
        generated_wire = _wire_time(now)
        expires_wire = _wire_time(expires_at)
        if expires_wire <= generated_wire:
            expires_wire = _wire_time(now + timedelta(milliseconds=1))
        return EnvironmentSnapshotV1(
            schema_version=1,
            revision=self._revision,
            runtime_boot_id=self.runtime_boot_id,
            generated_at_utc=generated_wire,
            expires_at_utc=expires_wire,
            facts=facts,
            stale_keys=tuple(self._stale),
            source_health={
                source.value: health
                for source, health in self._source_health.items()
            },
            permission_revision=self._permission_revision,
        )

    def _clock(self) -> datetime:
        value = self._now()
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise RuntimeError("environment clock must return an aware datetime")
            current = value.astimezone(timezone.utc)
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RuntimeError("environment clock must return a finite epoch or datetime")
            epoch = float(value)
            if not math.isfinite(epoch):
                raise RuntimeError("environment clock must return a finite epoch or datetime")
            try:
                current = datetime.fromtimestamp(epoch, timezone.utc)
            except (OverflowError, OSError, ValueError) as exc:
                raise RuntimeError("environment clock returned an invalid epoch") from exc
        return current.replace(microsecond=(current.microsecond // 1_000) * 1_000)

    def _expire_locked(self, now: datetime) -> bool:
        changed = False
        expired_keys = tuple(
            key
            for key, fact in self._facts.items()
            if _parse_time(fact.valid_until_utc) <= now
        )
        for key in expired_keys:
            self._facts.pop(key, None)
            self._mark_stale_locked(key, now)
            changed = True
        unknown_keys = tuple(
            key for key, stale_until in self._stale.items() if stale_until <= now
        )
        for key in unknown_keys:
            self._stale.pop(key, None)
            changed = True
        return changed

    def _invalidate_locked(
        self,
        now: datetime,
        *,
        grant_hashes: Collection[str] = (),
        sources: Collection[SourceKind] = (),
    ) -> bool:
        hashes = frozenset(grant_hashes)
        source_set = frozenset(sources)
        keys = tuple(
            key
            for key, fact in self._facts.items()
            if fact.grant_id_hash in hashes or fact.source_kind in source_set
        )
        for key in keys:
            self._facts.pop(key, None)
            self._mark_stale_locked(key, now)
        return bool(keys)

    def _evict_excess_facts_locked(self, now: datetime) -> None:
        excess = len(self._facts) - self._max_facts
        if excess <= 0:
            return
        ordered = sorted(
            self._facts.values(),
            key=lambda fact: (
                fact.valid_until_utc,
                fact.observed_at_utc,
                fact.source_kind.value,
                fact.key,
            ),
        )
        for fact in ordered[:excess]:
            self._facts.pop(fact.key, None)
            self._mark_stale_locked(fact.key, now)

    def _mark_stale_locked(self, key: FactKey, now: datetime) -> None:
        self._stale.pop(key, None)
        self._stale[key] = now + timedelta(seconds=self._stale_ttl_seconds)
        while len(self._stale) > self._max_stale_keys:
            self._stale.popitem(last=False)

    def _remember_locked(
        self, value: str, values: set[str], order: deque[str]
    ) -> None:
        values.add(value)
        order.append(value)
        while len(order) > self._seen_capacity:
            values.discard(order.popleft())


__all__ = [
    "DEFAULT_SEEN_CAPACITY",
    "DEFAULT_SNAPSHOT_TTL_SECONDS",
    "DEFAULT_STALE_TTL_SECONDS",
    "EnvironmentReducer",
]
