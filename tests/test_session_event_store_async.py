import json
import sqlite3
import threading
import time

from core.events import Event, EventBus
from memory.session_db import SessionEventStore


def test_attached_session_store_does_not_write_sqlite_in_publish_thread(
    tmp_path,
    monkeypatch,
):
    store = SessionEventStore(tmp_path / "events.sqlite3")
    bus = EventBus()
    store.attach(bus)
    entered = threading.Event()
    release = threading.Event()

    def slow_record(event):
        entered.set()
        release.wait(1.0)

    monkeypatch.setattr(store, "_record_event_db", slow_record)
    started = time.perf_counter()
    bus.publish("tool.completed", {"tool": "screenshot", "success": True})
    elapsed = time.perf_counter() - started

    assert elapsed < 0.05
    assert entered.wait(1.0)
    release.set()
    assert store.close(timeout=1.0) is True


def test_recent_events_flushes_pending_writes_for_read_your_writes(tmp_path):
    store = SessionEventStore(tmp_path / "events.sqlite3")
    bus = EventBus()
    store.attach(bus)
    source = bus.publish(
        "user.preference",
        {"key": "theme", "value": "dark"},
        source="unit",
    )

    rows = store.recent_events(limit=5)

    assert rows[-1]["event_id"] == source.id
    assert rows[-1]["payload"] == {"key": "theme", "value": "dark"}
    assert store.close(timeout=1.0) is True


def test_session_store_redacts_secret_fields_before_queueing(tmp_path):
    path = tmp_path / "events.sqlite3"
    store = SessionEventStore(path)
    bus = EventBus()
    store.attach(bus)
    bus.publish(
        "provider.configured",
        {"api_key": "sk-private", "provider": "local"},
        source="unit",
    )

    rows = store.recent_events(limit=1)
    assert store.close(timeout=1.0) is True

    assert "sk-private" not in json.dumps(rows)
    assert rows == []


def test_session_store_rejects_biometric_and_malformed_metadata(tmp_path):
    store = SessionEventStore(tmp_path / "events.sqlite3")
    bus = EventBus()
    store.attach(bus)
    bus.publish("voice.audio.captured", {"audio": b"private"}, source="voice")
    malformed = Event(
        id="bad\nevent",
        type="unit.invalid",
        payload={"value": 1},
        source="unit",
        timestamp=float("nan"),
        sequence=-1,
    )

    assert store.record_event(malformed) is False
    assert store.recent_events(limit=10) == []
    assert store.close(timeout=1.0) is True


def test_session_store_indexes_only_explicit_redacted_summary(tmp_path):
    path = tmp_path / "events.sqlite3"
    store = SessionEventStore(path)
    bus = EventBus()
    store.attach(bus)
    bus.publish(
        "tool.completed",
        {
            "tool": "web_search",
            "task": "research",
            "success": True,
            "params": {"query": "needle-private-query"},
        },
        source="tools",
    )
    assert store.close(timeout=1.0) is True

    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT content FROM memory_fts WHERE ref_kind = 'event'"
        ).fetchall()

    serialized = json.dumps(rows)
    assert "needle-private-query" not in serialized
    assert "web_search" in serialized


def test_repeated_attach_does_not_register_duplicate_wildcard_handlers(tmp_path):
    store = SessionEventStore(tmp_path / "events.sqlite3")
    bus = EventBus()
    assert store.attach(bus) is True
    assert store.attach(bus) is False
    source = bus.publish("unit.memory", {"value": 42}, source="unit")

    rows = store.recent_events(limit=10)

    assert [row["event_id"] for row in rows] == [source.id]
    assert store.close(timeout=1.0) is True


def test_redundant_internal_receipts_are_not_session_memory(tmp_path):
    store = SessionEventStore(tmp_path / "events.sqlite3")
    bus = EventBus()
    store.attach(bus)
    bus.publish("event_store.registered", {"path": "G:/private"}, source="runtime")
    bus.publish(
        "memory.candidate.applied",
        {"candidate_id": "c1", "content": "private"},
        source="memory",
    )

    assert store.recent_events(limit=10) == []
    assert store.close(timeout=1.0) is True


def test_closed_store_rejects_new_events_and_reports_stopped(tmp_path):
    store = SessionEventStore(tmp_path / "events.sqlite3")
    bus = EventBus()
    store.attach(bus)
    assert store.close(timeout=1.0) is True

    bus.publish("unit.after_close", {"value": 1}, source="unit")

    assert store.status()["state"] == "stopped"
    assert store.recent_events(event_type="unit.after_close") == []
