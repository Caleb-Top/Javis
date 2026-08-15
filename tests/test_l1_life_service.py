from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import time

import pytest

from core.life.l1.contracts import (
    InputProvenance,
    PlaybackLifecycleEvent,
    PlaybackOutcome,
)
from core.life.l1.clock import ManualClock
from core.life.l1.observation_bridge import ProjectionRejected
from core.runtime import create_runtime


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _conversation_payload(session_id: str, request_id: str) -> dict:
    return {
        "session_id": session_id,
        "request_id": request_id,
        "input_provenance": InputProvenance.unknown().to_dict(),
    }


def test_runtime_exposes_one_revision_aligned_inner_state(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    try:
        inner = runtime.life.inner_state_snapshot()
        life = runtime.life.snapshot()

        assert inner.source_life_snapshot_revision == life.revision
        assert inner.identity_id == life.identity.identity_id
        assert inner.instance_id == life.instance.instance_id
        assert inner.attention.mode.value == "idle"
        assert runtime.agent.prompt_builder.build_runtime_state().startswith(
            "JAVIS_RUNTIME_STATE_V1"
        )
    finally:
        runtime.close()


def test_real_conversation_events_drive_inner_state_and_persist_thin_receipt(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    try:
        payload = _conversation_payload("session-1", "request-1")
        runtime.event_bus.publish("request.accepted", payload, source="conversation")

        engaged = runtime.life.inner_state_snapshot()
        assert engaged.attention.mode.value == "engaged"
        assert engaged.attention.target_id == "request-1"
        assert engaged.homeostasis.cognitive_load > 0.05
        assert engaged.source_life_snapshot_revision == runtime.life.snapshot().revision

        runtime.event_bus.publish("request.completed", payload, source="conversation")
        assert runtime.life.journal.flush(timeout=1.0)

        quiet = runtime.life.inner_state_snapshot()
        receipts = runtime.life.recent_turn_receipts()
        assert quiet.attention.mode.value == "idle"
        assert quiet.source_life_snapshot_revision == runtime.life.snapshot().revision
        assert len(receipts) == 1
        assert receipts[0].request_id == "request-1"
        assert receipts[0].outcome.value == "completed"
        assert receipts[0].verify_hash()
    finally:
        runtime.close()


def test_l1_semantic_change_advances_authoritative_life_revision(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    try:
        payload = _conversation_payload("session-1", "request-1")
        runtime.event_bus.publish("request.accepted", payload, source="conversation")
        before = runtime.life.snapshot().revision

        runtime.event_bus.publish("approval.required", payload, source="conversation")

        after = runtime.life.snapshot().revision
        inner = runtime.life.inner_state_snapshot()
        assert after == before + 1
        assert inner.source_life_snapshot_revision == after
        assert inner.attention.mode.value == "awaiting_approval"
        assert inner.homeostasis.caution > 0.1
    finally:
        runtime.close()


def test_playback_duration_and_generation_scope_the_speaking_claim(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    boot_id = runtime.life.status()["boot_id"]

    def lifecycle(generation: int, outcome: PlaybackOutcome) -> PlaybackLifecycleEvent:
        return PlaybackLifecycleEvent(
            schema_version=1,
            playback_id=f"playback-{generation}",
            generation=generation,
            runtime_boot_id=boot_id,
            session_id="session-1",
            request_id="request-1",
            outcome=outcome,
            occurred_at_utc=_timestamp(),
            reason_code=f"playback_{outcome.value}",
        )

    try:
        runtime.life.observe_playback(lifecycle(1, PlaybackOutcome.STARTED), 0.1)
        first = runtime.life.inner_state_snapshot()
        runtime.life.observe_playback(lifecycle(2, PlaybackOutcome.STARTED), 0.2)
        second = runtime.life.inner_state_snapshot()
        runtime.life.observe_playback(lifecycle(1, PlaybackOutcome.STOPPED))

        current = runtime.life.inner_state_snapshot()
        assert first.attention.mode.value == "speaking"
        assert first.attention.target_id != second.attention.target_id
        assert current.attention.target_id == second.attention.target_id

        runtime.life.observe_playback(lifecycle(2, PlaybackOutcome.COMPLETED))
        assert runtime.life.inner_state_snapshot().attention.mode.value == "idle"
    finally:
        runtime.close()


def test_restart_marks_persisted_incomplete_turn_as_interrupted(tmp_path):
    code_root = tmp_path / "code"
    data_root = tmp_path / "data"
    abandoned = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    payload = _conversation_payload("session-restart", "request-restart")
    abandoned.event_bus.publish("request.accepted", payload, source="conversation")
    assert abandoned.life.receipt_journal.flush(timeout=1.0)

    abandoned.life._accepting = False
    assert abandoned.life.journal.stop(timeout=1.0)
    assert abandoned.life.receipt_journal.stop(timeout=1.0)
    abandoned.agent_runs.close()
    abandoned.skill_catalog.close()

    recovered = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    try:
        receipts = recovered.life.recent_turn_receipts()
        assert len(receipts) == 1
        assert receipts[0].request_id == "request-restart"
        assert receipts[0].outcome.value == "interrupted"
        assert receipts[0].completeness.value == "interrupted_by_restart"
        assert receipts[0].recovery_event_id
        assert receipts[0].verify_hash()
    finally:
        recovered.close()


def test_lazy_attention_expiry_advances_public_revision_on_next_observation(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    monotonic = runtime.life._l1_clock.read().monotonic_seconds
    clock = ManualClock(
        datetime.now(timezone.utc) + timedelta(seconds=1),
        monotonic,
    )
    runtime.life._l1_clock = clock
    try:
        payload = _conversation_payload("session-expiry", "request-expiry")
        runtime.event_bus.publish("request.accepted", payload, source="conversation")
        before = runtime.life.snapshot().revision
        assert runtime.life.inner_state_snapshot().attention.mode.value == "engaged"

        clock.advance(61.0)
        runtime.event_bus.publish(
            "activity.tool_completed",
            {**payload, "success": True},
            source="conversation",
        )

        assert runtime.life.snapshot().revision == before + 1
        assert runtime.life.inner_state_snapshot().attention.mode.value == "idle"
    finally:
        runtime.close()


def test_clean_restart_receipt_recovery_has_a_traceable_event_id(tmp_path):
    code_root = tmp_path / "code"
    data_root = tmp_path / "data"
    first = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    first.event_bus.publish(
        "request.accepted",
        _conversation_payload("session-clean", "request-clean"),
        source="conversation",
    )
    first.close()

    second = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    try:
        receipt = second.life.recent_turn_receipts()[0]
        assert receipt.completeness.value == "interrupted_by_restart"
        assert receipt.recovery_event_id
        assert receipt.recovered_at_utc
    finally:
        second.close()


def test_speaking_ttl_actively_returns_public_state_to_idle(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    boot_id = runtime.life.status()["boot_id"]
    started = PlaybackLifecycleEvent(
        schema_version=1,
        playback_id="playback-expiry",
        generation=1,
        runtime_boot_id=boot_id,
        session_id="session-expiry",
        request_id="request-expiry",
        outcome=PlaybackOutcome.STARTED,
        occurred_at_utc=_timestamp(),
        reason_code="playback_started",
    )
    try:
        runtime.life.observe_playback(started, 0.0)
        before = runtime.life.snapshot().revision
        assert runtime.life.inner_state_snapshot().attention.mode.value == "speaking"

        deadline = time.monotonic() + 3.0
        while (
            runtime.life.inner_state_snapshot().attention.mode.value == "speaking"
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        assert runtime.life.inner_state_snapshot().attention.mode.value == "idle"
        assert runtime.life.snapshot().revision == before + 1
    finally:
        runtime.close()


def test_restart_recovers_every_incomplete_receipt_beyond_recent_window(tmp_path):
    code_root = tmp_path / "code"
    data_root = tmp_path / "data"
    first = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    for index in range(35):
        first.event_bus.publish(
            "request.accepted",
            _conversation_payload("session-many", f"request-{index:02d}"),
            source="conversation",
        )
    first.close()

    second = create_runtime(
        code_root,
        startup_side_effects=False,
        data_root=data_root,
    )
    try:
        assert second.life.receipt_journal.incomplete(limit=1024) == ()
        with sqlite3.connect(second.life.receipt_journal.path) as database:
            recovered = database.execute(
                "SELECT COUNT(*) FROM turn_experience_receipts "
                "WHERE completeness = 'interrupted_by_restart'"
            ).fetchone()[0]
        assert recovered == 35
        in_memory = second.life._receipt_projector.recent_receipts()
        assert [item.request_id for item in in_memory] == [
            f"request-{index:02d}" for index in range(3, 35)
        ]
    finally:
        second.close()


def test_receipt_write_failure_never_creates_a_clean_checkpoint(
    tmp_path,
    monkeypatch,
):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )

    def fail_write(_receipt):
        raise OSError("forced receipt write failure")

    monkeypatch.setattr(runtime.life.receipt_journal, "_write_once", fail_write)
    runtime.event_bus.publish(
        "request.accepted",
        _conversation_payload("session-failure", "request-failure"),
        source="conversation",
    )
    assert not runtime.life.receipt_journal.flush(timeout=1.0)
    assert not runtime.life.stop(timeout=1.0)

    checkpoint = runtime.life.lineage_store._load_checkpoint(
        runtime.life.lineage_summary().instance_id
    )
    assert checkpoint.clean_shutdown_at is None
    assert runtime.life.status()["state"] == "degraded"
    runtime.close()


def test_terminal_playback_generation_cannot_be_revived_by_replayed_start(tmp_path):
    runtime = create_runtime(
        tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "data",
    )
    boot_id = runtime.life.status()["boot_id"]

    def event(playback_id: str, generation: int, outcome: PlaybackOutcome):
        return PlaybackLifecycleEvent(
            schema_version=1,
            playback_id=playback_id,
            generation=generation,
            runtime_boot_id=boot_id,
            session_id="session-replay",
            request_id="request-replay",
            outcome=outcome,
            occurred_at_utc=_timestamp(),
            reason_code=f"playback_{outcome.value}",
        )

    try:
        first_start = event("playback-1", 1, PlaybackOutcome.STARTED)
        runtime.life.observe_playback(first_start, 0.1)
        runtime.life.observe_playback(
            event("playback-1", 1, PlaybackOutcome.STOPPED)
        )
        runtime.life.observe_playback(
            event("playback-2", 2, PlaybackOutcome.STARTED),
            0.2,
        )

        with pytest.raises(ProjectionRejected, match="terminal"):
            runtime.life.observe_playback(first_start, 0.1)

        runtime.life.observe_playback(
            event("playback-2", 2, PlaybackOutcome.COMPLETED)
        )
        assert runtime.life.inner_state_snapshot().attention.mode.value == "idle"
    finally:
        runtime.close()
