from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import core.runtime as runtime_module
from core.cancellation import CancellationToken, RequestCancelled
from core.conversation_store import ConversationStore
from core.life.memory.api import _strict_body
from core.life.memory.contracts import Subject
from core.life.memory.service import MemoryService
from core.life.memory.store import DATABASE_RELATIVE_PATH, MemoryStore
from knowledge.brain import Brain


NOW = "2026-08-20T10:00:00.000Z"


def _subject() -> Subject:
    return Subject.from_dict(
        {
            "schema_version": 1,
            "subject_id": "subject-release-user",
            "revision": 1,
            "subject_kind": "primary_user",
            "display_name": "Release fixture user",
            "status": "active",
            "identity_assurance": "desktop_confirmed",
            "credential_reference_hash": None,
            "merged_into_subject_id": None,
            "session_scope_id": None,
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
        }
    )


def _access_projection(session_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "context_id": f"context-{session_id}",
        "actor_subject_id": "subject-release-user",
        "actor_kind": "primary_user",
        "session_id": session_id,
        "participant_subject_ids": ["subject-release-user", "subject-javis"],
        "audience_ceiling": "owner_private",
        "identity_assurance": "desktop_confirmed",
        "acl_epoch": 0,
    }


def _append_terminal(store: ConversationStore, outcome: str) -> None:
    session_id = f"session-{outcome}"
    request_id = f"request-{outcome}"
    store.accept_request(
        session_id,
        request_id,
        f"key-{outcome}",
        "This unsuccessful request must never become autobiographical memory.",
        {"access_projection": _access_projection(session_id)},
    )
    store.append_event(session_id, request_id, f"request.{outcome}", {})


def _row_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as db:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_memory_service_runs_mutations_on_its_dedicated_writer(tmp_path: Path) -> None:
    """The concrete store sees writes only on the service-owned writer thread."""

    caller_thread_id = threading.get_ident()
    mutation_threads: list[int] = []

    class RecordingMemoryStore(MemoryStore):
        def put_subject(self, subject: Subject, *, writer_token: object) -> bool:
            mutation_threads.append(threading.get_ident())
            return super().put_subject(subject, writer_token=writer_token)

    service = MemoryService(tmp_path / "memory", store_factory=RecordingMemoryStore).start()
    try:
        assert service.put_subject(_subject()).result(timeout=20) is True
        writer_thread_id = service.status()["writer_thread_id"]
        assert mutation_threads == [writer_thread_id]
        assert writer_thread_id != caller_thread_id
    finally:
        assert service.shutdown(timeout=20)


def test_failed_and_cancelled_terminals_insert_no_episode(tmp_path: Path) -> None:
    """Authoritative unsuccessful terminals may write receipts, never memory objects."""

    conversations = ConversationStore(tmp_path / "conversations.sqlite3")
    for outcome in ("failed", "cancelled"):
        _append_terminal(conversations, outcome)

    data_root = tmp_path / "data"
    service = MemoryService(
        data_root,
        conversation_store=conversations,
        reconcile_interval_seconds=60,
    ).start()
    try:
        assert service.reconcile_once().result(timeout=20) == {
            "scanned": 2,
            "advanced": 2,
            "pending": 0,
        }
        database = data_root / DATABASE_RELATIVE_PATH
        assert _row_count(database, "terminal_projection_receipts") == 2
        for table in (
            "memory_items",
            "experience_episodes",
            "journal_entries",
            "shared_memories",
            "user_model_claims",
            "relationship_events",
        ):
            assert _row_count(database, table) == 0, table
    finally:
        assert service.shutdown(timeout=20)


@pytest.mark.parametrize(
    "payload",
    [
        {"owner_subject_id": "client-chosen-owner"},
        {"audience": "explicit_shared"},
        {"subject": {"subject_id": "client-chosen-subject"}},
        {"proposal": {"access_context": {"actor_kind": "primary_user"}}},
        {"items": [{"participant_subject_ids": ["client-chosen-participant"]}]},
    ],
)
def test_client_authority_fields_are_rejected_recursively(payload: dict[str, object]) -> None:
    """Memory HTTP payloads cannot supply ownership, audience, subject, or ACL authority."""

    with pytest.raises(HTTPException) as captured:
        _strict_body(payload, set(payload))
    assert captured.value.status_code == 400
    assert captured.value.detail["code"] == "reserved_access_field"


def test_authority_words_inside_user_text_are_not_false_positives() -> None:
    body = {"proposed_text": "The phrase owner_subject_id is ordinary user text."}
    assert _strict_body(body, {"proposed_text"}) == body


def test_formal_runtime_outcomes_never_call_legacy_brain_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise completed, failed, and cancelled Agent paths with legacy writers trapped."""

    calls = {"learn_fact": 0, "record_experience": 0, "learn_style": 0}

    def trap(name: str):
        def trapped(*_args: object, **_kwargs: object) -> None:
            calls[name] += 1
            raise AssertionError(f"formal runtime called legacy Brain.{name}")

        return trapped

    for method_name in calls:
        monkeypatch.setattr(Brain, method_name, trap(method_name))

    runtime = runtime_module.create_runtime(
        tmp_path / "source",
        startup_side_effects=False,
        data_root=tmp_path / "runtime-data",
    )

    class Engine:
        mode = "completed"

        async def chat_with_fallback(self, *_args: object, **_kwargs: object):
            if self.mode == "failed":
                raise RuntimeError("synthetic release-contract failure")
            return (
                SimpleNamespace(tool_calls=(), text="bounded reply", reasoning_content=""),
                SimpleNamespace(is_fallback=False, model="synthetic"),
            )

    engine = Engine()
    runtime.agent.engine = engine
    runtime.agent.max_retries = 0

    async def collect(text: str, token: CancellationToken | None = None):
        return [event async for event in runtime.agent.chat(text, cancellation=token)]

    try:
        completed = asyncio.run(collect("complete the compatibility fixture"))
        assert completed[-1] == {"type": "done", "success": True}

        runtime.agent.reset()
        engine.mode = "failed"
        failed = asyncio.run(collect("fail the compatibility fixture"))
        assert failed[-1]["type"] == "error"

        runtime.agent.reset()
        cancelled = CancellationToken()
        cancelled.cancel("release-contract cancellation")
        with pytest.raises(RequestCancelled):
            asyncio.run(collect("cancel the compatibility fixture", cancelled))

        assert calls == {"learn_fact": 0, "record_experience": 0, "learn_style": 0}
        assert runtime.learner is None
        assert runtime.brain._read_only is True
        assert not (tmp_path / "runtime-data" / "memory" / "legacy-brain").exists()
    finally:
        runtime.close()
