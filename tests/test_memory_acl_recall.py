from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path

from core.life.memory.contracts import (
    AccessContext,
    ExperienceEpisode,
    RecallQuery,
    Subject,
)
from core.life.memory.recall import RecallEngine, build_recall_query
from core.life.memory.service import MemoryService
from core.life.memory.store import MemoryStore
from core.prompt_builder import PromptBuilder
from tools.memory_tools import memory_recall, memory_recent


NOW = "2026-08-20T10:00:00.000Z"
LATER = "2026-08-20T10:05:00.000Z"
LATEST = "2026-08-20T10:10:00.000Z"
HASH_A = "a" * 64


def subject(subject_id: str, *, revision: int = 1) -> Subject:
    return Subject.from_dict(
        {
            "schema_version": 1,
            "subject_id": subject_id,
            "revision": revision,
            "subject_kind": "primary_user" if subject_id == "owner-a" else "known_person",
            "display_name": subject_id,
            "status": "active",
            "identity_assurance": "desktop_confirmed",
            "credential_reference_hash": None,
            "merged_into_subject_id": None,
            "session_scope_id": None,
            "created_at_utc": NOW,
            "updated_at_utc": NOW if revision == 1 else LATER,
        }
    )


def episode(
    episode_id: str,
    owner: str,
    text: str,
    *,
    audience: str = "owner_private",
    participants: tuple[str, ...] | None = None,
    revision: int = 1,
) -> ExperienceEpisode:
    participants = participants or (owner,)
    return ExperienceEpisode.from_dict(
        {
            "schema_version": 1,
            "episode_id": episode_id,
            "revision": revision,
            "owner_subject_id": owner,
            "audience": audience,
            "privacy_class": "user_private",
            "session_id": f"session-{episode_id}",
            "request_id": f"request-{episode_id}",
            "participant_subject_ids": list(participants),
            "started_at_utc": NOW,
            "ended_at_utc": LATER,
            "outcome": "completed",
            "what_happened": text,
            "javis_attention": text,
            "intent_summary": text,
            "action_summary": text,
            "verified_result_summary": "request.completed",
            "meaning_for_user": "",
            "meaning_for_javis": "",
            "source_terminal_event_id": f"terminal-{episode_id}",
            "source_terminal_sequence": 3,
            "source_sequence_domain": f"conversation_store:session-{episode_id}",
            "source_message_ids": [f"message-{episode_id}"],
            "source_event_ids": [f"accepted-{episode_id}", f"terminal-{episode_id}"],
            "source_digest": HASH_A,
            "extractor_version": "deterministic.v1",
            "confidence": 0.95,
            "status": "active",
            "retention_class": "memory_candidate",
            "expires_at_utc": None,
            "created_at_utc": LATER,
            "updated_at_utc": LATER if revision == 1 else LATEST,
        }
    )


def context(
    actor: str,
    acl_epoch: int,
    *,
    actor_kind: str = "primary_user",
    participants: tuple[str, ...] | None = None,
    purpose: str = "recall",
) -> AccessContext:
    participants = participants if participants is not None else (actor,)
    guest = actor_kind == "guest"
    return AccessContext.from_dict(
        {
            "schema_version": 1,
            "context_id": f"context-{actor}-{acl_epoch}-{'-'.join(participants)}",
            "runtime_boot_id": "boot-1",
            "client_id_hash": HASH_A,
            "capability_scopes": ["memory.read" if purpose == "recall" else "conversation"],
            "actor_subject_id": actor,
            "actor_kind": actor_kind,
            "session_id": "session-current",
            "participant_subject_ids": list(participants),
            "audience_ceiling": "guest" if guest else "explicit_shared",
            "identity_assurance": "guest" if guest else "desktop_confirmed",
            "purpose": purpose,
            "acl_epoch": acl_epoch,
            "issued_at_utc": NOW,
            "expires_at_utc": LATEST,
        }
    )


def query(access: AccessContext, text: str, *, limit: int = 8) -> RecallQuery:
    return RecallQuery.from_dict(
        {
            "schema_version": 1,
            "query_id": f"query-{access.context_id}-{text}",
            "access_context": access.to_dict(),
            "query_text": text,
            "item_kinds": ["experience_episode"],
            "limit": limit,
            "max_item_chars": 512,
            "max_total_bytes": 4096,
            "occurred_after_utc": None,
            "occurred_before_utc": None,
            "issued_at_utc": NOW,
        }
    )


def seed(store: MemoryStore, token: object):
    for item in (subject("owner-a"), subject("owner-b")):
        store.put_subject(item, writer_token=token)


def test_two_owners_with_same_keyword_never_cross_recall(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    try:
        seed(store, token)
        store.put_item(
            episode("episode-a", "owner-a", "private-needle owner A boundary"),
            writer_token=token,
        )
        store.put_item(
            episode("episode-b", "owner-b", "private-needle owner B boundary"),
            writer_token=token,
        )
        epoch = store.metadata()["acl_epoch"]
        engine = RecallEngine()
        owner_a = engine.recall(store, query(context("owner-a", epoch), "private-needle"))
        owner_b = engine.recall(
            store,
            query(
                context("owner-b", epoch, actor_kind="known_person"),
                "private-needle",
            ),
        )
        assert [item.item_id for item in owner_a.items] == ["episode-a"]
        assert [item.item_id for item in owner_b.items] == ["episode-b"]
        assert "owner B" not in str(owner_a.to_dict())
        assert "owner A" not in str(owner_b.to_dict())
    finally:
        store.close()


def test_temp_visible_set_is_materialized_before_fts_rank(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    traces: list[str] = []
    try:
        seed(store, token)
        store.put_item(
            episode("visible", "owner-a", "rank-needle visible text"),
            writer_token=token,
        )
        store.put_item(
            episode("hidden", "owner-b", "rank-needle hidden text"),
            writer_token=token,
        )
        original = store._search_connection

        @contextmanager
        def traced_connection():
            with original() as db:
                db.set_trace_callback(traces.append)
                yield db

        store._search_connection = traced_connection
        epoch = store.metadata()["acl_epoch"]
        found = store.search_items(query(context("owner-a", epoch), "rank-needle"))
        assert [item.episode_id for item in found] == ["visible"]
        normalized = [statement.casefold() for statement in traces]
        insert_index = next(
            index
            for index, statement in enumerate(normalized)
            if "insert into visible_memory_ids" in statement
        )
        rank_index = next(
            index
            for index, statement in enumerate(normalized)
            if "join memory_fts" in statement and statement.startswith("select")
        )
        assert insert_index < rank_index
        assert "owner-a" in normalized[insert_index]
    finally:
        store.close()


def test_guest_and_stale_acl_fail_closed_before_search():
    class CountingStore:
        calls = 0

        @staticmethod
        def status():
            return {"state": "ready", "read_only": False}

        @staticmethod
        def metadata():
            return {"acl_epoch": 5, "index_generation": 7}

        def search_items(self, recall_query):
            self.calls += 1
            return ()

    store = CountingStore()
    engine = RecallEngine()
    guest = engine.recall(
        store,
        query(context("guest-1", 0, actor_kind="guest", participants=()), "needle"),
    )
    stale = engine.recall(store, query(context("owner-a", 4), "needle"))
    assert guest.reason_code == "guest"
    assert stale.reason_code == "acl_epoch_stale"
    assert store.calls == 0


def test_acl_revocation_and_generation_change_invalidate_cached_result(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    try:
        seed(store, token)
        first_episode = episode(
            "shared-episode",
            "owner-a",
            "revocation-needle shared boundary",
            audience="participants",
            participants=("owner-a", "owner-b"),
        )
        store.put_item(first_episode, writer_token=token)
        first_epoch = store.metadata()["acl_epoch"]
        engine = RecallEngine()
        actor_b = context(
            "owner-b",
            first_epoch,
            actor_kind="known_person",
            participants=("owner-a", "owner-b"),
        )
        assert len(engine.recall(store, query(actor_b, "revocation-needle")).items) == 1

        revoked = episode(
            "shared-episode",
            "owner-a",
            "revocation-needle shared boundary",
            audience="participants",
            participants=("owner-a",),
            revision=2,
        )
        store.put_item(revoked, writer_token=token)
        second_epoch = store.metadata()["acl_epoch"]
        assert second_epoch > first_epoch
        stale = engine.recall(store, query(actor_b, "revocation-needle"))
        assert stale.reason_code == "acl_epoch_stale"
        fresh_actor_b = context(
            "owner-b",
            second_epoch,
            actor_kind="known_person",
            participants=("owner-a", "owner-b"),
        )
        assert engine.recall(store, query(fresh_actor_b, "revocation-needle")).items == ()
    finally:
        store.close()


def test_cache_key_isolates_actor_participants_and_purpose():
    class CountingStore:
        calls = 0

        @staticmethod
        def status():
            return {"state": "ready", "read_only": False}

        @staticmethod
        def metadata():
            return {"acl_epoch": 2, "index_generation": 3}

        def search_items(self, recall_query):
            self.calls += 1
            return ()

    store = CountingStore()
    engine = RecallEngine()
    first = context("owner-a", 2, participants=("owner-a",))
    changed_participants = context("owner-a", 2, participants=("owner-a", "owner-b"))
    conversation = context("owner-a", 2, participants=("owner-a",), purpose="conversation")
    engine.recall(store, query(first, "needle"))
    engine.recall(store, query(first, "needle"))
    engine.recall(store, query(changed_participants, "needle"))
    engine.recall(store, query(conversation, "needle"))
    assert store.calls == 3


def test_bundle_caps_items_text_and_total_bytes(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    try:
        store.put_subject(subject("owner-a"), writer_token=token)
        for index in range(10):
            store.put_item(
                episode(
                    f"episode-{index}",
                    "owner-a",
                    "bounded-needle " + (str(index) * 600),
                ),
                writer_token=token,
            )
        epoch = store.metadata()["acl_epoch"]
        recall_query = query(context("owner-a", epoch), "bounded-needle", limit=8)
        bundle = RecallEngine().recall(store, recall_query)
        assert len(bundle.items) == 8
        assert all(len(item.prompt_text) <= 512 for item in bundle.items)
        assert sum(len(item.prompt_text.encode("utf-8")) for item in bundle.items) <= 4096
        assert bundle.truncated is True
    finally:
        store.close()


def test_memory_service_degraded_recall_returns_empty_without_legacy_fallback(tmp_path: Path):
    def broken_factory(*args, **kwargs):
        raise OSError("offline")

    access = context("owner-a", 0)
    service = MemoryService(tmp_path, store_factory=broken_factory).start()
    try:
        bundle = service.recall(build_recall_query(access, "private-needle")).result(timeout=20)
        assert bundle.items == ()
        assert bundle.reason_code == "memory_unavailable"
    finally:
        assert service.shutdown(timeout=20)


def test_prompt_builder_uses_only_the_validated_request_bundle(tmp_path: Path):
    token = object()
    store = MemoryStore(tmp_path, writer_token=token)
    try:
        store.put_subject(subject("owner-a"), writer_token=token)
        store.put_item(
            episode("episode-prompt", "owner-a", "request-scoped-only"),
            writer_token=token,
        )
        access = context("owner-a", store.metadata()["acl_epoch"])
        bundle = RecallEngine().recall(store, query(access, "request-scoped-only"))
    finally:
        store.close()

    builder = PromptBuilder(brain=object())
    builder.build_layer2 = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("legacy global memory must not be read")
    )
    prompt = builder.build(recall_bundle=bundle)

    assert "JAVIS_REQUEST_MEMORY_V1" in prompt
    assert "request-scoped-only" in prompt
    assert bundle.items[0].source_citation_token in prompt


def test_legacy_memory_tools_fail_closed_without_request_access_context():
    expected = "server-owned request access context"
    assert expected in asyncio.run(memory_recall("private-needle"))
    assert expected in asyncio.run(memory_recent())
