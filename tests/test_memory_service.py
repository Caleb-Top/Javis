from __future__ import annotations

import queue
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from core.life.memory.contracts import DeletionRequest, Subject
from core.life.memory.service import (
    MemoryQueueFullError,
    MemoryService,
    MemoryServiceUnavailable,
)
from core.life.memory.store import MemoryStore


NOW = "2026-08-20T10:00:00.000Z"
HASH_A = "a" * 64


def subject(subject_id: str) -> Subject:
    return Subject.from_dict(
        {
            "schema_version": 1,
            "subject_id": subject_id,
            "revision": 1,
            "subject_kind": "primary_user",
            "display_name": "Primary user",
            "status": "active",
            "identity_assurance": "desktop_confirmed",
            "credential_reference_hash": None,
            "merged_into_subject_id": None,
            "session_scope_id": None,
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
        }
    )


def deletion_request(request_id: str = "deletion-1") -> DeletionRequest:
    return DeletionRequest.from_dict(
        {
            "schema_version": 1,
            "deletion_request_id": request_id,
            "revision": 1,
            "actor_subject_id": "subject-user",
            "scope": "item",
            "target_selector": {
                "item_kind": "journal_entry",
                "item_id": "journal-1",
                "subject_id": None,
                "session_id": None,
                "range_started_at_utc": None,
                "range_ended_at_utc": None,
            },
            "source_handling": "derived_only",
            "state": "accepted",
            "progress_cursor": None,
            "attempt": 0,
            "last_reason_code": None,
            "created_at_utc": NOW,
            "updated_at_utc": NOW,
            "completed_at_utc": None,
        }
    )


class TerminalSource:
    def __init__(self, *, outcome: str = "failed") -> None:
        self.outcome = outcome

    def scan_terminal_events(self, after_row_id: int = 0, limit: int = 100):
        events = []
        if after_row_id < 11:
            events.append(
                {
                    "terminal_row_id": 11,
                    "event_id": "terminal-event-1",
                    "session_id": "session-1",
                    "request_id": "request-1",
                    "sequence": 3,
                    "sequence_domain": "conversation_store:session-1",
                    "outcome": self.outcome,
                }
            )
        return {
            "source_store_id": "conversation-store-1",
            "next_row_id": 11 if events else after_row_id,
            "has_more": False,
            "events": events,
        }

    def read_request_evidence(self, session_id: str, request_id: str):
        return {
            "source_store_id": "conversation-store-1",
            "session_id": session_id,
            "request_id": request_id,
            "sequence_domain": f"conversation_store:{session_id}",
            "redacted": False,
            "terminal_conflict": False,
            "terminal_events": [
                {
                    "event_id": "terminal-event-1",
                    "sequence": 3,
                    "type": f"request.{self.outcome}",
                }
            ],
            "evidence_ready": True,
            "access_projection": {
                "actor_kind": "primary_user",
                "actor_subject_id": "subject-user",
                "participant_subject_ids": ["subject-user", "subject-javis"],
                "audience_ceiling": "owner_private",
                "identity_assurance": "desktop_confirmed",
                "acl_epoch": 0,
            },
        }


def test_store_is_created_and_all_mutations_run_on_the_single_writer(tmp_path: Path):
    writer_ids: list[int] = []
    store_holder: list[MemoryStore] = []

    class RecordingStore(MemoryStore):
        def put_subject(self, value, *, writer_token):
            writer_ids.append(threading.get_ident())
            return super().put_subject(value, writer_token=writer_token)

    def factory(*args, **kwargs):
        store = RecordingStore(*args, **kwargs)
        store_holder.append(store)
        return store

    service = MemoryService(tmp_path, store_factory=factory).start()
    try:
        assert service.put_subject(subject("subject-1")).result(timeout=2) is True
        assert service.put_subject(subject("subject-2")).result(timeout=2) is True
        status = service.status()
        assert status["state"] == "ready"
        assert writer_ids == [status["writer_thread_id"], status["writer_thread_id"]]
        assert store_holder[0].writer_thread_id == status["writer_thread_id"]
    finally:
        assert service.shutdown()


def test_queue_pressure_rejects_critical_mutation_explicitly(tmp_path: Path):
    entered = threading.Event()
    release = threading.Event()

    class BlockingStore(MemoryStore):
        def put_subject(self, value, *, writer_token):
            if value.subject_id == "subject-blocking":
                entered.set()
                assert release.wait(2)
            return super().put_subject(value, writer_token=writer_token)

    service = MemoryService(
        tmp_path,
        queue_capacity=1,
        critical_enqueue_timeout=0.01,
        store_factory=BlockingStore,
    ).start()
    first = service.put_subject(subject("subject-blocking"))
    assert entered.wait(1)
    second = service.put_subject(subject("subject-queued"))
    with pytest.raises(MemoryQueueFullError):
        service.put_subject(subject("subject-rejected"))
    assert service.status()["metrics"]["critical_rejected"] == 1
    release.set()
    assert first.result(timeout=2) is True
    assert second.result(timeout=2) is True
    assert service.shutdown()


def test_priority_queue_runs_critical_command_before_queued_normal_work(tmp_path: Path):
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    class RecordingStore(MemoryStore):
        def put_subject(self, value, *, writer_token):
            if value.subject_id == "subject-blocking":
                order.append("blocking")
                entered.set()
                assert release.wait(2)
            else:
                order.append("normal")
            return super().put_subject(value, writer_token=writer_token)

        def delete_item(self, item_kind, item_id, *, writer_token):
            order.append("critical")
            return super().delete_item(
                item_kind,
                item_id,
                writer_token=writer_token,
            )

    service = MemoryService(tmp_path, store_factory=RecordingStore).start()
    try:
        blocking = service.put_subject(subject("subject-blocking"))
        assert entered.wait(1)
        normal = service.put_subject(subject("subject-normal"))
        critical = service.delete_item("journal_entry", "missing-journal")
        release.set()

        assert blocking.result(timeout=2) is True
        assert critical.result(timeout=2) is False
        assert normal.result(timeout=2) is True
        assert order == ["blocking", "critical", "normal"]
    finally:
        assert service.shutdown()


def test_shutdown_cannot_overtake_an_admitted_command(tmp_path: Path):
    put_entered = threading.Event()
    allow_put = threading.Event()
    shutdown_finished = threading.Event()
    accepted: list[object] = []
    shutdown_results: list[bool] = []

    class GatedQueue(queue.PriorityQueue):
        def put(self, item, block=True, timeout=None):
            put_entered.set()
            assert allow_put.wait(2)
            return super().put(item, block=block, timeout=timeout)

    service = MemoryService(tmp_path, queue_capacity=4)
    service._queue = GatedQueue(maxsize=4)
    service.start()

    submitter = threading.Thread(
        target=lambda: accepted.append(service.put_subject(subject("subject-race")))
    )

    def stop_service():
        shutdown_results.append(service.shutdown(timeout=2))
        shutdown_finished.set()

    submitter.start()
    assert put_entered.wait(1)
    stopper = threading.Thread(target=stop_service)
    stopper.start()
    assert shutdown_finished.wait(0.05) is False

    allow_put.set()
    submitter.join(2)
    stopper.join(2)
    assert not submitter.is_alive()
    assert not stopper.is_alive()
    assert shutdown_results == [True]
    assert accepted[0].result(timeout=0) is True


def test_shutdown_stops_admission_and_drains_accepted_critical_work(tmp_path: Path):
    service = MemoryService(tmp_path, queue_capacity=4).start()
    first = service.put_subject(subject("subject-1"))
    second = service.put_subject(subject("subject-2"))

    assert service.shutdown(timeout=3, drain=True)
    assert first.result(timeout=0) is True
    assert second.result(timeout=0) is True
    assert service.status()["state"] == "stopped"


def test_shutdown_without_drain_reports_success_when_writer_stops(tmp_path: Path):
    service = MemoryService(tmp_path).start()

    assert service.shutdown(timeout=2, drain=False) is True
    assert service.status()["state"] == "stopped"


def test_startup_migration_failure_is_degraded_and_reads_fail_closed(tmp_path: Path):
    initial = MemoryStore(tmp_path, writer_token=object())
    path = initial.path
    initial.close()
    db = sqlite3.connect(path)
    try:
        db.execute("DROP TABLE memory_fts")
        db.execute("DROP TABLE memory_fts_rebuilds")
        db.execute("DELETE FROM schema_migrations WHERE version = 2")
        db.execute("UPDATE memory_meta SET schema_version = 1")
        db.execute("CREATE TABLE memory_fts (item_id TEXT, item_kind TEXT, searchable_text TEXT)")
        db.execute("PRAGMA user_version = 1")
        db.commit()
    finally:
        db.close()

    service = MemoryService(tmp_path).start()
    try:
        status = service.status()
        assert status["state"] == "degraded"
        assert status["reason_code"] == "schema_migration_failed"
        assert service.get_subject("subject-private").result(timeout=0) is None
        with pytest.raises(MemoryServiceUnavailable):
            service.put_subject(subject("subject-private"))
        conversation_completed = True
        assert conversation_completed is True
    finally:
        assert service.shutdown()


def test_store_factory_failure_is_unavailable_without_blocking_other_runtime_work(
    tmp_path: Path,
):
    def broken_factory(*args, **kwargs):
        raise OSError("unavailable")

    service = MemoryService(tmp_path, store_factory=broken_factory).start()
    try:
        assert service.status()["reason_code"] == "store_startup_failed"
        assert service.get_subject("subject-private").result(timeout=0) is None
        with pytest.raises(MemoryServiceUnavailable):
            service.put_subject(subject("subject-private"))
        assert str(tmp_path) not in str(service.status())
    finally:
        assert service.shutdown()


def test_receipt_failure_never_advances_terminal_cursor(tmp_path: Path):
    store_holder: list[MemoryStore] = []

    class ReceiptFailureStore(MemoryStore):
        def complete_terminal_projection(self, receipt_values, *, writer_token):
            raise RuntimeError("receipt write failed")

    def factory(*args, **kwargs):
        store = ReceiptFailureStore(*args, **kwargs)
        store_holder.append(store)
        return store

    service = MemoryService(
        tmp_path,
        conversation_store=TerminalSource(outcome="failed"),
        reconcile_interval_seconds=60,
        store_factory=factory,
    ).start()
    try:
        with pytest.raises(RuntimeError, match="receipt write failed"):
            service.reconcile_once().result(timeout=2)
        assert store_holder[0].metadata()["terminal_cursor"] == 0
        assert service.status()["metrics"]["commands_failed"] == 1
    finally:
        assert service.shutdown()


def test_successful_excluded_terminal_advances_cursor_after_receipt(tmp_path: Path):
    service = MemoryService(
        tmp_path,
        conversation_store=TerminalSource(outcome="cancelled"),
        reconcile_interval_seconds=60,
    ).start()
    try:
        result = service.reconcile_once().result(timeout=2)
        assert result == {"scanned": 1, "advanced": 1, "pending": 0}
        status = service.status()
        assert status["store"]["terminal_cursor"] == 11
        receipt = service.get_terminal_receipt(
            "conversation-store-1", "session-1", "request-1"
        ).result(timeout=2)
        assert receipt["projection_state"] == "excluded"
        assert receipt["reason_code"] == "terminal_cancelled"
    finally:
        assert service.shutdown()


def test_completed_owner_terminal_without_extractable_evidence_is_not_selected(
    tmp_path: Path,
):
    service = MemoryService(
        tmp_path,
        conversation_store=TerminalSource(outcome="completed"),
        reconcile_interval_seconds=60,
    ).start()
    try:
        result = service.reconcile_once().result(timeout=2)
        assert result == {"scanned": 1, "advanced": 1, "pending": 0}
        assert service.status()["store"]["terminal_cursor"] == 11
        receipt = service.get_terminal_receipt(
            "conversation-store-1", "session-1", "request-1"
        ).result(timeout=2)
        assert receipt["projection_state"] == "not_selected"
        assert receipt["reason_code"] == "evidence_ineligible"
    finally:
        assert service.shutdown()


def test_deletion_worker_cache_deduplicates_and_remains_bounded(tmp_path: Path):
    service = MemoryService(tmp_path, deletion_cache_capacity=1).start()
    try:
        assert service.put_subject(subject("subject-user")).result(timeout=2) is True
        request = deletion_request()
        first = service.submit_deletion(request)
        replay = service.submit_deletion(request)
        assert replay is first
        assert first.result(timeout=2) is True

        second = service.submit_deletion(deletion_request("deletion-2"))
        assert second.result(timeout=2) is True
        assert service.status()["deletion_cache_size"] == 1
    finally:
        assert service.shutdown()


def test_concurrent_deletion_submission_reuses_one_cached_future(tmp_path: Path):
    mutation_entered = threading.Event()
    allow_mutation_return = threading.Event()
    futures: list[object] = []

    class GatedService(MemoryService):
        gate_first_deletion = True

        def _submit_store_mutation(self, method_name, *args, **kwargs):
            future = super()._submit_store_mutation(method_name, *args, **kwargs)
            if method_name == "put_deletion_request" and self.gate_first_deletion:
                self.gate_first_deletion = False
                mutation_entered.set()
                assert allow_mutation_return.wait(2)
            return future

    service = GatedService(tmp_path).start()
    try:
        assert service.put_subject(subject("subject-user")).result(timeout=2) is True
        request = deletion_request()
        first = threading.Thread(target=lambda: futures.append(service.submit_deletion(request)))
        second = threading.Thread(target=lambda: futures.append(service.submit_deletion(request)))

        first.start()
        assert mutation_entered.wait(1)
        second.start()
        time.sleep(0.05)
        assert len(futures) == 0
        allow_mutation_return.set()
        first.join(2)
        second.join(2)

        assert not first.is_alive()
        assert not second.is_alive()
        assert len(futures) == 2
        assert futures[0] is futures[1]
        assert futures[0].result(timeout=2) is True
    finally:
        allow_mutation_return.set()
        assert service.shutdown()


def test_failed_deletion_is_removed_from_cache_so_retry_can_run(tmp_path: Path):
    service = MemoryService(tmp_path).start()
    try:
        request = deletion_request()
        failed = service.submit_deletion(request)
        with pytest.raises(sqlite3.IntegrityError):
            failed.result(timeout=2)

        assert service.put_subject(subject("subject-user")).result(timeout=2) is True
        retried = service.submit_deletion(request)
        assert retried is not failed
        assert retried.result(timeout=2) is True
    finally:
        assert service.shutdown()


def test_read_pool_has_bounded_ingress_and_returns_fail_soft_fallback(tmp_path: Path):
    entered = threading.Event()
    release = threading.Event()

    class BlockingReadStore(MemoryStore):
        def get_subject(self, subject_id):
            entered.set()
            assert release.wait(2)
            return super().get_subject(subject_id)

    service = MemoryService(
        tmp_path,
        read_workers=1,
        read_queue_capacity=0,
        store_factory=BlockingReadStore,
    ).start()
    try:
        first = service.get_subject("subject-1")
        assert entered.wait(1)
        rejected = service.get_subject("subject-2")
        assert rejected.result(timeout=0) is None
        assert service.status()["metrics"]["reads_rejected"] == 1
        release.set()
        assert first.result(timeout=2) is None
    finally:
        assert service.shutdown()
