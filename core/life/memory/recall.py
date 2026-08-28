"""ACL-first recall assembly with bounded request-scoped caching."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from .contracts import (
    AccessContext,
    ActorKind,
    ExperienceEpisode,
    JournalEntry,
    RecallBundle,
    RecallItem,
    RecallQuery,
    RelationshipEvent,
    SharedMemory,
    UserModelClaim,
)


class RecallStore(Protocol):
    def status(self) -> Mapping[str, Any]: ...

    def metadata(self) -> Mapping[str, Any]: ...

    def search_items(self, query: RecallQuery) -> tuple[Any, ...]: ...


class RecallEngine:
    """Convert SQL-visible memory contracts into a bounded RecallBundle."""

    def __init__(self, *, cache_capacity: int = 128) -> None:
        if type(cache_capacity) is not int or cache_capacity < 1:
            raise ValueError("cache_capacity must be positive")
        self._cache_capacity = cache_capacity
        self._cache: OrderedDict[tuple[Any, ...], tuple[Any, ...]] = OrderedDict()
        self._lock = threading.RLock()

    def recall(self, store: RecallStore, query: RecallQuery) -> RecallBundle:
        if not isinstance(query, RecallQuery):
            raise TypeError("query must be a RecallQuery")
        context = query.access_context
        if context.actor_kind is ActorKind.GUEST:
            return empty_recall_bundle(query, "guest", acl_epoch=context.acl_epoch)
        try:
            status = store.status()
            metadata = store.metadata()
        except Exception:
            return empty_recall_bundle(query, "memory_unavailable", acl_epoch=context.acl_epoch)
        if status.get("state") != "ready" or bool(status.get("read_only", True)):
            return empty_recall_bundle(
                query,
                "memory_unavailable",
                acl_epoch=context.acl_epoch,
                index_generation=int(metadata.get("index_generation", 0)),
            )
        acl_epoch = int(metadata.get("acl_epoch", 0))
        index_generation = int(metadata.get("index_generation", 0))
        if context.acl_epoch != acl_epoch:
            return empty_recall_bundle(
                query,
                "acl_epoch_stale",
                acl_epoch=acl_epoch,
                index_generation=index_generation,
            )

        key = _cache_key(query, index_generation)
        with self._lock:
            contracts = self._cache.get(key)
            if contracts is not None:
                self._cache.move_to_end(key)
        if contracts is None:
            try:
                contracts = tuple(store.search_items(query))
            except Exception:
                return empty_recall_bundle(
                    query,
                    "recall_failed",
                    acl_epoch=acl_epoch,
                    index_generation=index_generation,
                )
            with self._lock:
                self._cache[key] = contracts
                self._cache.move_to_end(key)
                while len(self._cache) > self._cache_capacity:
                    self._cache.popitem(last=False)

        recall_items: list[RecallItem] = []
        used_bytes = 0
        truncated = False
        for contract in contracts:
            item = _to_recall_item(contract, query)
            encoded = len(item.prompt_text.encode("utf-8"))
            if used_bytes + encoded > query.max_total_bytes:
                truncated = True
                break
            recall_items.append(item)
            used_bytes += encoded
            if len(recall_items) >= query.limit:
                truncated = True
                break
        return RecallBundle.from_dict(
            {
                "schema_version": 1,
                "query_id": query.query_id,
                "context_id": context.context_id,
                "items": [item.to_dict() for item in recall_items],
                "acl_epoch": acl_epoch,
                "index_generation": index_generation,
                "generated_at_utc": _utc_now(),
                "truncated": truncated,
                "reason_code": None if recall_items else "no_match",
            }
        )

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def build_recall_query(
    access_context: AccessContext,
    query_text: str,
    *,
    item_kinds: Sequence[str] = (),
    limit: int = 8,
    max_item_chars: int = 512,
    max_total_bytes: int = 4096,
) -> RecallQuery:
    if not isinstance(access_context, AccessContext):
        raise TypeError("access_context must be an AccessContext")
    now = _utc_now()
    query_id = "recall-" + hashlib.sha256(
        f"{access_context.context_id}\0{query_text}\0{now}\0{uuid.uuid4().hex}".encode(
            "utf-8"
        )
    ).hexdigest()
    return RecallQuery.from_dict(
        {
            "schema_version": 1,
            "query_id": query_id,
            "access_context": access_context.to_dict(),
            "query_text": str(query_text or "").strip()[:512],
            "item_kinds": list(item_kinds),
            "limit": limit,
            "max_item_chars": max_item_chars,
            "max_total_bytes": max_total_bytes,
            "occurred_after_utc": None,
            "occurred_before_utc": None,
            "issued_at_utc": now,
        }
    )


def empty_recall_bundle(
    query: RecallQuery,
    reason_code: str,
    *,
    acl_epoch: int | None = None,
    index_generation: int = 0,
) -> RecallBundle:
    return RecallBundle.from_dict(
        {
            "schema_version": 1,
            "query_id": query.query_id,
            "context_id": query.access_context.context_id,
            "items": [],
            "acl_epoch": query.access_context.acl_epoch if acl_epoch is None else acl_epoch,
            "index_generation": index_generation,
            "generated_at_utc": _utc_now(),
            "truncated": False,
            "reason_code": reason_code,
        }
    )


def format_recall_prompt(bundle: RecallBundle | None) -> str:
    """Render only a validated request bundle; never consult global memory."""

    if bundle is None:
        return ""
    if not isinstance(bundle, RecallBundle):
        raise TypeError("bundle must be a RecallBundle or None")
    if not bundle.items:
        return ""
    lines = ["JAVIS_REQUEST_MEMORY_V1"]
    for item in bundle.items:
        lines.append(
            f"[{item.source_citation_token}] {item.epistemic_label.value}: {item.prompt_text}"
        )
    return "\n".join(lines)


def _to_recall_item(contract: Any, query: RecallQuery) -> RecallItem:
    context = query.access_context
    if isinstance(contract, ExperienceEpisode):
        item_id = contract.episode_id
        kind = "experience_episode"
        text = contract.what_happened
        occurred = contract.ended_at_utc
        epistemic = "evidence_derived"
        confidence = contract.confidence
        audience = contract.audience.value
    elif isinstance(contract, JournalEntry):
        item_id = contract.entry_id
        kind = "journal_entry"
        text = f"{contract.title}: {contract.body}"
        occurred = contract.range_ended_at_utc
        epistemic = "interpretation"
        confidence = 1.0
        audience = contract.audience.value
    elif isinstance(contract, SharedMemory):
        item_id = contract.shared_memory_id
        kind = "shared_memory"
        text = contract.proposed_text
        occurred = contract.confirmed_at_utc or contract.updated_at_utc
        epistemic = "confirmed_shared"
        confidence = 1.0
        audience = contract.audience.value
    elif isinstance(contract, UserModelClaim):
        item_id = contract.claim_id
        kind = "user_model_claim"
        text = f"{contract.predicate}: {contract.value}"
        occurred = contract.updated_at_utc
        epistemic = (
            "explicit_statement"
            if contract.epistemic_class.value == "explicit_statement"
            else "interpretation"
        )
        confidence = contract.confidence
        audience = contract.audience.value
    elif isinstance(contract, RelationshipEvent):
        item_id = contract.relationship_event_id
        kind = "relationship_event"
        text = contract.summary
        occurred = contract.occurred_at_utc
        epistemic = "evidence_derived"
        confidence = 1.0
        audience = contract.audience.value
    else:
        raise TypeError("recall candidate must be a memory contract")
    owner_subject_id = str(getattr(contract, "owner_subject_id"))
    owner_label = (
        "shared"
        if audience in {"participants", "explicit_shared"}
        else "actor"
        if owner_subject_id == context.actor_subject_id
        else "javis"
    )
    prompt_text = _truncate_utf8(_clean_prompt_text(text), query.max_item_chars)
    citation = "citation-" + hashlib.sha256(
        f"{kind}\0{item_id}\0{getattr(contract, 'source_digest', '')}".encode("utf-8")
    ).hexdigest()
    return RecallItem.from_dict(
        {
            "item_id": item_id,
            "item_kind": kind,
            "prompt_text": prompt_text,
            "occurred_at_utc": occurred,
            "epistemic_label": epistemic,
            "source_citation_token": citation,
            "owner_label": owner_label,
            "audience": audience,
            "confidence": confidence,
        }
    )


def _cache_key(query: RecallQuery, index_generation: int) -> tuple[Any, ...]:
    context = query.access_context
    participant_hash = hashlib.sha256(
        json.dumps(sorted(context.participant_subject_ids), separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return (
        context.actor_subject_id,
        participant_hash,
        context.purpose.value,
        context.acl_epoch,
        index_generation,
        context.audience_ceiling.value,
        query.query_text.casefold(),
        tuple(kind.value for kind in query.item_kinds),
        query.limit,
        query.max_item_chars,
        query.max_total_bytes,
        query.occurred_after_utc,
        query.occurred_before_utc,
    )


def _clean_prompt_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _truncate_utf8(value: str, maximum_chars: int) -> str:
    if len(value) <= maximum_chars:
        return value
    return value[:maximum_chars].rstrip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "RecallEngine",
    "RecallStore",
    "build_recall_query",
    "empty_recall_bundle",
    "format_recall_prompt",
]
