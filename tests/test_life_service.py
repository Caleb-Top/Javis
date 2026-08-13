import threading
import time

import pytest

from core.runtime import create_runtime


def test_runtime_registers_one_life_service_with_no_startup_model_calls(
    tmp_path,
    monkeypatch,
):
    model_calls = []
    monkeypatch.setattr(
        "core.llm_client.LLMClient.complete",
        lambda *args, **kwargs: model_calls.append((args, kwargs)),
        raising=False,
    )

    runtime = create_runtime(
        root=tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "user-data",
    )
    try:
        assert runtime.life is runtime.subsystems["life"]
        assert runtime.life.snapshot().identity.identity_id
        assert runtime.life.snapshot().lifecycle_state.value in {"awake", "quiet"}
        assert runtime.life.status()["journal_state"] == "running"
        assert runtime.life.data_root == (tmp_path / "user-data").resolve()
        assert runtime.life.journal.path == (
            tmp_path / "user-data" / "life" / "journal" / "life.sqlite3"
        ).resolve()
        assert model_calls == []
    finally:
        runtime.close()

    assert runtime.life.status()["journal_state"] == "stopped"


def test_runtime_and_conversation_hub_share_the_only_event_bus(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    try:
        assert runtime.conversation_hub.event_bus is runtime.event_bus
        assert len(runtime.event_bus._handlers["*"]) == 1
        assert runtime.life.start(runtime) is False
        assert len(runtime.event_bus._handlers["*"]) == 1
    finally:
        runtime.close()


def test_life_handler_enqueues_without_waiting_for_sqlite(tmp_path, monkeypatch):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    entered = threading.Event()
    release = threading.Event()

    def slow_write(batch):
        entered.set()
        release.wait(1.0)
        return 0

    monkeypatch.setattr(runtime.life.journal, "_write_batch", slow_write)
    try:
        started = time.perf_counter()
        runtime.event_bus.publish(
            "tool.completed",
            {"tool": "system_info", "success": True},
            source="tools",
        )
        elapsed = time.perf_counter() - started

        assert elapsed < 0.05
        assert entered.wait(1.0)
    finally:
        release.set()
        runtime.close()


def test_life_service_projects_state_and_notifies_subscribers(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    seen = []
    unsubscribe = runtime.life.subscribe(
        lambda snapshot, expression: seen.append((snapshot, expression))
    )
    try:
        runtime.event_bus.publish(
            "request.accepted",
            {"session_id": "s1", "request_id": "r1"},
            source="conversation",
        )

        snapshot = runtime.life.snapshot()
        expression = runtime.life.expression()
        assert snapshot.active_session_id == "s1"
        assert snapshot.active_request_id == "r1"
        assert snapshot.lifecycle_state.value == "engaged"
        assert expression.source_snapshot_revision == snapshot.revision
        assert expression.base_state.value == "attention"
        assert seen[-1] == (snapshot, expression)

        unsubscribe()
        before = len(seen)
        runtime.event_bus.publish(
            "activity.thinking",
            {"session_id": "s1", "request_id": "r1"},
            source="conversation",
        )
        assert len(seen) == before
    finally:
        runtime.close()


def test_clean_shutdown_is_assessed_before_the_next_boot(tmp_path):
    code_root = tmp_path / "code"
    data_root = tmp_path / "data"
    first = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    first_instance = first.life.lineage_summary().instance_id
    first_boot = first.life.status()["boot_id"]
    first_events = first.life.recent_events(limit=20)
    first.close()

    second = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    try:
        assert second.life.lineage_summary().instance_id == first_instance
        assert second.life.status()["boot_id"] != first_boot
        assert second.life.status()["previous_run_reason"] == "clean_shutdown"
        assert second.life.status()["previous_run_unclean"] is False
        all_events = second.life.recent_events(limit=50)
        assert [event["event_type"] for event in first_events].count(
            "life.identity.created"
        ) == 1
        assert [event["event_type"] for event in all_events].count(
            "life.identity.created"
        ) == 1
        assert [event["event_type"] for event in all_events].count(
            "life.instance.created"
        ) == 1
    finally:
        second.close()


def test_unclean_previous_boot_enters_explicit_recovery(tmp_path):
    code_root = tmp_path / "code"
    data_root = tmp_path / "data"
    abandoned = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    abandoned.life._accepting = False
    assert abandoned.life.journal.stop(timeout=1.0) is True
    abandoned.agent_runs.close()
    abandoned.skill_catalog.close()

    runtime = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    try:
        assert runtime.life.status()["previous_run_unclean"] is True
        assert runtime.life.status()["previous_run_reason"] == "unclean_shutdown"
        assert runtime.life.snapshot().lifecycle_state.value == "recovering"
        assert runtime.life.snapshot().recovery_required is True
        assert "life.recovery.required" in {
            event["event_type"] for event in runtime.life.recent_events(limit=20)
        }
    finally:
        runtime.close()


def test_corrupt_identity_pointer_starts_from_verified_read_only_history(tmp_path):
    code_root = tmp_path / "code"
    data_root = tmp_path / "data"
    first = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    identity_id = first.life.identity_summary().identity_id
    current_path = first.life.identity_store.current_path
    first.close()
    current_path.write_text("{corrupt", encoding="utf-8")

    recovered = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    try:
        assert recovered.life.identity_summary().identity_id == identity_id
        assert recovered.life.status()["read_only_recovery"] is True
        assert recovered.life.snapshot().lifecycle_state.value == "recovering"
        assert recovered.life.snapshot().recovery_required is True
        assert current_path.read_text(encoding="utf-8") == "{corrupt"
    finally:
        recovered.close()


def test_shutdown_checkpoint_preserves_an_unfinished_request_id(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    runtime.event_bus.publish(
        "request.accepted",
        {"session_id": "s1", "request_id": "unfinished"},
        source="conversation",
    )

    assert runtime.life.stop(timeout=1.0) is True
    checkpoint = runtime.life.lineage_store._load_checkpoint(
        runtime.life.lineage_summary().instance_id
    )

    assert checkpoint.active_request_id == "unfinished"
    runtime.close()


def test_stopped_wildcard_callback_is_a_noop(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    runtime.close()
    before = runtime.life.status()["journal_accepted"]

    runtime.event_bus.publish(
        "tool.completed",
        {"tool": "system_info", "success": True},
        source="tools",
    )

    assert runtime.life.status()["journal_accepted"] == before
    assert runtime.life.stop() is True


def test_flush_timeout_never_writes_a_clean_checkpoint(tmp_path, monkeypatch):
    code_root = tmp_path / "code"
    data_root = tmp_path / "data"
    runtime = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    monkeypatch.setattr(runtime.life.journal, "flush", lambda timeout=2.0: False)

    assert runtime.life.stop(timeout=0.05) is False
    checkpoint = runtime.life.lineage_store._load_checkpoint(
        runtime.life.lineage_summary().instance_id
    )

    assert checkpoint.clean_shutdown_at is None
    assert runtime.life.status()["state"] == "degraded"
    runtime.close()


def test_life_service_rejects_a_mismatched_data_root(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    try:
        runtime.data_root = (tmp_path / "different").resolve()
        with pytest.raises(RuntimeError, match="data_root"):
            runtime.life.verify_runtime(runtime)
    finally:
        runtime.data_root = (tmp_path / "data").resolve()
        runtime.close()
