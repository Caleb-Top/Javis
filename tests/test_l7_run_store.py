from __future__ import annotations

import inspect
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.life.l7.contracts import (
    SleepJobReceiptV1,
    SleepRunState,
    SleepRunTransitionV1,
    SleepRunV1,
    canonical_content_hash,
)
from core.life.l7.run_store import (
    RunStore,
    RunStoreCASMismatchError,
    RunStoreClaimError,
    RunStoreClosedError,
    RunStoreConflictError,
    RunStoreIntegrityError,
)


T0 = "2026-08-20T01:00:00.000Z"
T1 = "2026-08-20T01:00:01.000Z"
T2 = "2026-08-20T01:00:02.000Z"
T3 = "2026-08-20T01:00:03.000Z"
T4 = "2026-08-20T01:00:04.000Z"
HASH_A = "a" * 64
HASH_B = "b" * 64
PERSISTENCE = {
    "privacy_class": "local_internal",
    "retention_class": "operational",
    "provenance": {"adapter": "test", "source_ids": ["source-1"]},
}


def _signed(payload: dict) -> dict:
    result = dict(payload)
    result["content_hash"] = canonical_content_hash(result)
    return result


def _run_wire(run_id: str = "run-1", **overrides) -> dict:
    value = {
        "schema_version": 1,
        "run_id": run_id,
        "policy_id": "policy-1",
        "policy_revision": 1,
        "identity_id": "identity-1",
        "instance_id": "instance-1",
        "owner_subject_id": "owner-1",
        "trigger_kind": "manual",
        "trigger_event_id": f"trigger-{run_id}",
        "idempotency_key": f"schedule-{run_id}",
        "state": "scheduled",
        "revision": 1,
        "scheduled_at_utc": T0,
        "started_at_utc": None,
        "ended_at_utc": None,
        "preflight_snapshot_hash": None,
        "source_checkpoint_ids": [],
        "job_specs": [
            {
                "job_id": "job-1",
                "kind": "index_verify",
                "max_records": 10,
                "max_output_bytes": 1024,
            }
        ],
        "current_job_id": None,
        "completed_job_ids": [],
        "receipt_ids": [],
        "candidate_version_ids": [],
        "cancel_reason_code": None,
        "failure_reason_code": None,
        "resource_usage": {},
        "created_at_utc": T0,
        "updated_at_utc": T0,
        **PERSISTENCE,
    }
    value.update(overrides)
    return _signed(value)


def _transition_wire(
    *,
    transition_id: str,
    from_state: str,
    to_state: str,
    expected_revision: int,
    created_at_utc: str,
    run_id: str = "run-1",
    **overrides,
) -> dict:
    value = {
        "schema_version": 1,
        "transition_id": transition_id,
        "run_id": run_id,
        "from_state": from_state,
        "to_state": to_state,
        "expected_revision": expected_revision,
        "reason_code": "test_progress",
        "checkpoint_id": None,
        "receipt_ids": [],
        "created_at_utc": created_at_utc,
        "idempotency_key": f"idem-{transition_id}",
    }
    value.update(overrides)
    return _signed(value)


def _receipt_wire(run_id: str = "run-1", **overrides) -> dict:
    value = {
        "schema_version": 1,
        "receipt_id": "receipt-1",
        "run_id": run_id,
        "job_id": "job-1",
        "job_kind": "index_verify",
        "status": "completed",
        "input_set_hash": HASH_A,
        "output_set_hash": HASH_B,
        "processed_count": 5,
        "skipped_count": 0,
        "reason_code": None,
        "error_code": None,
        "checkpoint_id": "checkpoint-1",
        "source_ids": ["source-1"],
        "started_at_utc": T2,
        "ended_at_utc": T3,
        "duration_ms": 1000,
        "output_bytes": 32,
        **PERSISTENCE,
    }
    value.update(overrides)
    return _signed(value)


def _run(store: RunStore, run_id: str = "run-1", **overrides):
    return store.create_run(SleepRunV1.from_dict(_run_wire(run_id, **overrides)))


def _transition(store: RunStore, **values):
    return store.append_transition(
        SleepRunTransitionV1.from_dict(_transition_wire(**values))
    )


def _advance_to_running(store: RunStore, run_id: str = "run-1"):
    _transition(
        store,
        transition_id=f"{run_id}-preflight",
        run_id=run_id,
        from_state="scheduled",
        to_state="preflight",
        expected_revision=1,
        created_at_utc=T1,
    )
    return _transition(
        store,
        transition_id=f"{run_id}-running",
        run_id=run_id,
        from_state="preflight",
        to_state="running",
        expected_revision=2,
        created_at_utc=T2,
    )


def test_fixed_layout_wal_and_no_sql_or_event_bus_api(tmp_path: Path):
    store = RunStore(tmp_path)

    assert store.path == tmp_path / "growth" / "runs" / "runs.sqlite3"
    assert store.connection_settings() == {
        "journal_mode": "wal",
        "foreign_keys": 1,
        "query_only": 1,
        "schema_version": 1,
    }
    assert not hasattr(store, "execute")
    assert "event_bus" not in inspect.signature(RunStore).parameters


def test_schedule_is_idempotent_and_conflicting_content_fails(tmp_path: Path):
    store = RunStore(tmp_path)
    first = _run(store)
    replay = _run(store)

    assert first == replay
    assert first.state is SleepRunState.SCHEDULED
    assert first.revision == 1
    with pytest.raises(RunStoreConflictError, match="different content"):
        _run(store, policy_revision=2)


def test_transition_cas_order_and_idempotency(tmp_path: Path):
    store = RunStore(tmp_path)
    _run(store)
    wire = _transition_wire(
        transition_id="transition-1",
        from_state="scheduled",
        to_state="preflight",
        expected_revision=1,
        created_at_utc=T1,
    )
    transition = SleepRunTransitionV1.from_dict(wire)

    first = store.append_transition(transition)
    replay = store.append_transition(transition)
    assert first == replay
    assert first.state is SleepRunState.PREFLIGHT
    assert first.revision == 2
    assert store.list_transitions("run-1") == (transition,)

    stale = SleepRunTransitionV1.from_dict(
        _transition_wire(
            transition_id="transition-stale",
            from_state="preflight",
            to_state="running",
            expected_revision=1,
            created_at_utc=T2,
        )
    )
    with pytest.raises(RunStoreCASMismatchError, match="stale"):
        store.append_transition(stale)


def test_checkpoint_receipt_and_transition_references_share_hash_chain(tmp_path: Path):
    store = RunStore(tmp_path)
    _run(store)
    _advance_to_running(store)

    checkpoint = store.record_job_checkpoint(
        "run-1",
        "job-1",
        "checkpoint-1",
        expected_revision=3,
        created_at_utc=T2,
        content_hash=HASH_A,
        idempotency_key="checkpoint-write-1",
    )
    replay = store.record_job_checkpoint(
        "run-1",
        "job-1",
        "checkpoint-1",
        expected_revision=3,
        created_at_utc=T2,
        content_hash=HASH_A,
        idempotency_key="checkpoint-write-1",
    )
    assert replay == checkpoint

    receipt = SleepJobReceiptV1.from_dict(_receipt_wire())
    snapshot = store.record_job_receipt(
        receipt, expected_revision=4, idempotency_key="receipt-write-1"
    )
    assert snapshot.revision == 5
    assert store.list_job_receipts("run-1") == (receipt,)

    snapshot = _transition(
        store,
        transition_id="transition-committing",
        from_state="running",
        to_state="committing",
        expected_revision=5,
        created_at_utc=T3,
        checkpoint_id="checkpoint-1",
        receipt_ids=["receipt-1"],
    )
    assert snapshot.revision == 6
    assert snapshot.last_sequence == 6
    assert store.verify_integrity().ok


def test_missing_checkpoint_or_receipt_reference_fails_closed(tmp_path: Path):
    store = RunStore(tmp_path)
    _run(store)
    _advance_to_running(store)

    with pytest.raises(RunStoreConflictError, match="checkpoint"):
        _transition(
            store,
            transition_id="transition-missing-checkpoint",
            from_state="running",
            to_state="committing",
            expected_revision=3,
            created_at_utc=T3,
            checkpoint_id="missing-checkpoint",
        )
    assert store.get_run("run-1").revision == 3


def test_terminal_run_cannot_be_reopened_or_receive_job_writes(tmp_path: Path):
    store = RunStore(tmp_path)
    _run(store)
    _advance_to_running(store)
    committing = _transition(
        store,
        transition_id="transition-committing",
        from_state="running",
        to_state="committing",
        expected_revision=3,
        created_at_utc=T3,
    )
    completed = _transition(
        store,
        transition_id="transition-completed",
        from_state="committing",
        to_state="completed",
        expected_revision=committing.revision,
        created_at_utc=T4,
    )
    assert completed.terminal
    assert completed.ended_at_utc == T4

    with pytest.raises(RunStoreConflictError, match="terminal"):
        store.record_job_checkpoint(
            "run-1",
            "job-1",
            "checkpoint-after-terminal",
            expected_revision=completed.revision,
            created_at_utc=T4,
            content_hash=HASH_A,
            idempotency_key="after-terminal",
        )


def test_one_active_claim_per_identity_instance_under_concurrency(tmp_path: Path):
    store = RunStore(tmp_path)

    def create(run_id: str):
        try:
            return _run(store, run_id)
        except RunStoreClaimError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(create, ("run-a", "run-b")))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    claimed = store.claim_active("identity-1", "instance-1")
    assert claimed is not None
    assert claimed.run_id == winners[0].run_id
    loser_id = "run-b" if claimed.run_id == "run-a" else "run-a"
    with pytest.raises(RunStoreClaimError, match="another run"):
        store.claim_active("identity-1", "instance-1", run_id=loser_id)


def test_restart_projects_crashed_running_state_to_interrupted(tmp_path: Path):
    first = RunStore(tmp_path)
    _run(first)
    _advance_to_running(first)
    first.close()

    second = RunStore(tmp_path)
    snapshot = second.get_run("run-1")
    assert snapshot is not None
    assert snapshot.state is SleepRunState.INTERRUPTED
    assert snapshot.revision == 4
    transitions = second.list_transitions("run-1")
    assert transitions[-1].from_state is SleepRunState.RUNNING
    assert transitions[-1].to_state is SleepRunState.INTERRUPTED
    assert transitions[-1].reason_code == "crash_recovery"
    assert second.verify_integrity().ok


def test_wal_restart_preserves_receipts_and_chain(tmp_path: Path):
    first = RunStore(tmp_path)
    _run(first)
    _advance_to_running(first)
    first.record_job_checkpoint(
        "run-1",
        "job-1",
        "checkpoint-1",
        expected_revision=3,
        created_at_utc=T2,
        content_hash=HASH_A,
        idempotency_key="checkpoint-write-1",
    )
    receipt = SleepJobReceiptV1.from_dict(_receipt_wire())
    first.record_job_receipt(
        receipt, expected_revision=4, idempotency_key="receipt-write-1"
    )
    first.close()

    second = RunStore(tmp_path, recover_on_open=False)
    assert second.list_job_receipts("run-1") == (receipt,)
    report = second.verify_integrity()
    assert report.ok
    assert report.runs_scanned == 1
    assert report.events_scanned == 5


def test_append_only_guards_block_mutation_and_delete(tmp_path: Path):
    store = RunStore(tmp_path)
    _run(store)

    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "UPDATE run_headers SET content_hash = ? WHERE run_id = ?",
                (HASH_B, "run-1"),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM run_chain WHERE run_id = ?", ("run-1",))


def test_corrupt_projection_is_rejected_on_reopen(tmp_path: Path):
    store = RunStore(tmp_path)
    _run(store)
    store.close()

    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE run_projection SET revision = 99 WHERE run_id = ?", ("run-1",)
        )

    with pytest.raises(RunStoreIntegrityError, match="integrity"):
        RunStore(tmp_path)


def test_bounded_queries_and_closed_store_fail_closed(tmp_path: Path):
    store = RunStore(tmp_path)
    _run(store)

    with pytest.raises(ValueError, match="limit"):
        store.list_runs(limit=257)
    with pytest.raises(ValueError, match="limit"):
        store.list_transitions("run-1", limit=0)
    store.close()
    with pytest.raises(RunStoreClosedError):
        store.get_run("run-1")


def test_errors_do_not_echo_job_input_content(tmp_path: Path):
    store = RunStore(tmp_path)
    _run(store)
    _advance_to_running(store)
    secret_body = "private job input body"

    store.record_job_checkpoint(
        "run-1",
        "job-1",
        "checkpoint-1",
        expected_revision=3,
        created_at_utc=T2,
        content_hash=HASH_A,
        idempotency_key="checkpoint-write-1",
    )
    with pytest.raises(RunStoreConflictError) as caught:
        store.record_job_checkpoint(
            "run-1",
            "job-1",
            "checkpoint-1",
            expected_revision=3,
            created_at_utc=T2,
            content_hash=HASH_B,
            idempotency_key="checkpoint-write-1",
        )
    assert secret_body not in str(caught.value)
