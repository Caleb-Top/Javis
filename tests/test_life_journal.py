import sqlite3
import threading
import time

from core.life.contracts import LifeEvent, PrivacyClass, RetentionClass
from core.life.journal import LifeEventJournal


def make_life_event(
    event_id="life-1",
    *,
    event_type="life.test",
    correlation_id=None,
    sequence=1,
    retention="operational",
    privacy=PrivacyClass.LOCAL_INTERNAL,
    payload=None,
):
    return LifeEvent(
        schema_version=1,
        event_id=event_id,
        event_type=event_type,
        timestamp_utc="1970-01-01T00:00:01.000Z",
        monotonic_offset_ms=1,
        source="test",
        source_event_id=event_id,
        session_id="session-1",
        request_id=None,
        correlation_id=correlation_id,
        causation_id=None,
        sequence=sequence,
        identity_id="identity-1",
        instance_id="instance-1",
        payload=payload or {"safe": True},
        privacy_class=privacy,
        retention_class=RetentionClass(retention),
        confidence=1.0,
        provenance={"fixture": "tests/test_life_journal.py"},
        redaction_summary=("safe test event",),
    )


def test_construction_creates_no_worker_and_enqueue_is_valid_before_start(tmp_path):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=2)

    assert journal.status()["state"] == "paused"
    assert journal.enqueue(make_life_event("queued")) is True
    assert journal.pending_event_ids() == ("queued",)

    journal.start()
    assert journal.flush(timeout=1.0) is True
    assert journal.stop(timeout=1.0) is True


def test_journal_preserves_full_envelope_and_is_idempotent(tmp_path):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=8)
    journal.start()
    event = make_life_event(
        event_id="life-1",
        correlation_id="run-1",
        sequence=4,
    )

    assert journal.enqueue(event) is True
    assert journal.enqueue(event) is False
    assert journal.flush(timeout=1.0) is True
    row = journal.recent(limit=1)[0]

    assert row["event_id"] == "life-1"
    assert row["correlation_id"] == "run-1"
    assert row["sequence"] == 4
    assert row["payload"] == {"safe": True}
    assert row["provenance"]["fixture"] == "tests/test_life_journal.py"
    assert journal.stop(timeout=1.0) is True


def test_persisted_event_id_remains_idempotent_after_restart(tmp_path):
    path = tmp_path / "life.sqlite3"
    first = LifeEventJournal(path, capacity=4)
    first.start()
    assert first.enqueue(make_life_event("same-event")) is True
    assert first.stop(timeout=1.0) is True

    second = LifeEventJournal(path, capacity=4)
    second.start()
    assert second.enqueue(make_life_event("same-event")) is False
    assert len(second.recent(limit=10)) == 1
    assert second.stop(timeout=1.0) is True


def test_never_persist_event_is_rejected_before_queueing(tmp_path):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=4)
    event = make_life_event(
        "secret-1",
        retention="never_persist",
        privacy=PrivacyClass.SECRET,
        payload={"diagnostic": "payload_rejected"},
    )

    assert journal.enqueue(event) is False
    assert journal.pending_event_ids() == ()
    assert journal.status()["dropped_never_persist"] == 1


def test_biometric_event_is_rejected_even_with_incorrect_retention(tmp_path):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=4)
    event = make_life_event(
        "biometric-1",
        retention="operational",
        privacy=PrivacyClass.BIOMETRIC,
        payload={"diagnostic": "payload_rejected"},
    )

    assert journal.enqueue(event) is False
    assert journal.status()["dropped_never_persist"] == 1


def test_slow_sqlite_does_not_block_enqueue(tmp_path, monkeypatch):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=8)
    journal.start()
    entered = threading.Event()
    release = threading.Event()

    def slow_write(batch):
        entered.set()
        release.wait(1.0)

    monkeypatch.setattr(journal, "_write_batch", slow_write)
    started = time.perf_counter()
    assert journal.enqueue(make_life_event(event_id="life-1")) is True
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05
    assert entered.wait(1.0)
    release.set()
    assert journal.stop(timeout=1.0) is True


def test_stop_timeout_is_bounded_and_can_finish_after_writer_release(
    tmp_path,
    monkeypatch,
):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=4)
    journal.start()
    entered = threading.Event()
    release = threading.Event()

    def blocked_write(batch):
        entered.set()
        release.wait(1.0)
        return 0

    monkeypatch.setattr(journal, "_write_batch", blocked_write)
    assert journal.enqueue(make_life_event("blocked")) is True
    assert entered.wait(1.0)
    started = time.perf_counter()

    assert journal.stop(timeout=0.05) is False
    assert time.perf_counter() - started < 0.2
    assert journal.status()["state"] == "degraded"

    release.set()
    assert journal.stop(timeout=1.0) is True


def test_full_queue_drops_low_value_but_preserves_audit(tmp_path):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=2)
    assert journal.enqueue(make_life_event("low-1", retention="ephemeral"))
    assert journal.enqueue(make_life_event("low-2", retention="ephemeral"))

    assert journal.enqueue(make_life_event("audit-1", retention="audit"))

    assert journal.status()["dropped_ephemeral"] == 1
    assert "audit-1" in journal.pending_event_ids()
    assert len(journal.pending_event_ids()) == 2
    assert journal.enqueue(make_life_event("low-1", retention="ephemeral")) is False


def test_operational_action_result_also_reserves_queue_capacity(tmp_path):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=1)
    assert journal.enqueue(make_life_event("surface", retention="ephemeral"))
    completed = make_life_event(
        "tool-result",
        event_type="life.tool.completed",
        retention="operational",
    )

    assert journal.enqueue(completed) is True
    assert journal.pending_event_ids() == ("tool-result",)
    assert journal.status()["dropped_ephemeral"] == 1


def test_duplicate_surface_state_coalesces_by_type_and_correlation(tmp_path):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=3)
    first = make_life_event(
        "surface-1",
        event_type="life.surface.state",
        correlation_id="surface",
        retention="ephemeral",
        privacy=PrivacyClass.PUBLIC_SURFACE,
        payload={"revision": 1},
    )
    latest = make_life_event(
        "surface-2",
        event_type="life.surface.state",
        correlation_id="surface",
        retention="ephemeral",
        privacy=PrivacyClass.PUBLIC_SURFACE,
        payload={"revision": 2},
    )

    assert journal.enqueue(first) is True
    assert journal.enqueue(latest) is True
    assert journal.pending_event_ids() == ("surface-2",)
    assert journal.status()["coalesced"] == 1


def test_writer_failure_is_reported_without_killing_the_worker(tmp_path, monkeypatch):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=4)
    journal.start()
    real_write = journal._write_batch
    calls = 0

    def fail_once(batch):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("disk unavailable")
        return real_write(batch)

    monkeypatch.setattr(journal, "_write_batch", fail_once)
    assert journal.enqueue(make_life_event("fails")) is True
    assert journal.flush(timeout=1.0) is True
    assert journal.enqueue(make_life_event("recovers")) is True
    assert journal.flush(timeout=1.0) is True

    assert journal.status()["write_failures"] == 1
    assert journal.recent(limit=5)[-1]["event_id"] == "recovers"
    assert journal.stop(timeout=1.0) is True


def test_full_payload_is_not_copied_into_search_index(tmp_path):
    path = tmp_path / "life.sqlite3"
    journal = LifeEventJournal(path, capacity=4)
    journal.start()
    event = make_life_event(
        "private-1",
        payload={"private_text": "needle-private-prompt"},
    )
    assert journal.enqueue(event) is True
    assert journal.stop(timeout=1.0) is True

    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT summary FROM event_summaries").fetchall()

    assert "needle-private-prompt" not in repr(rows)
