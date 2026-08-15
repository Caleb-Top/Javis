from __future__ import annotations

import sqlite3
from pathlib import Path

from core.life.contracts import canonical_content_hash
from core.life.journal import TurnReceiptJournal
from core.life.l1.contracts import TurnExperienceReceipt


def receipt(
    index: int,
    *,
    completeness: str = "incomplete",
) -> TurnExperienceReceipt:
    timestamp = f"2026-08-15T04:00:{index:02d}.000Z"
    terminal = completeness == "complete"
    payload = {
        "schema_version": 1,
        "receipt_id": f"receipt-{index:03d}",
        "session_id": "session-1",
        "request_id": f"request-{index:03d}",
        "input_provenance": {
            "modality": "text",
            "verification": "client_claimed",
            "runtime_boot_id": None,
            "source_session_id": "session-1",
            "owner_generation": None,
            "voice_sequence": None,
            "voice_turn": None,
        },
        "source_boot_id": "boot-1",
        "first_event_id": f"event-{index:03d}-start",
        "first_event_sequence": index * 2,
        "first_event_sequence_domain": "conversation.session-1",
        "started_at_utc": timestamp,
        "ended_at_utc": timestamp,
        "outcome": "completed" if terminal else "unknown",
        "terminal_event_id": f"event-{index:03d}-done" if terminal else None,
        "terminal_event_sequence": index * 2 + 1 if terminal else None,
        "terminal_event_sequence_domain": (
            "conversation.session-1" if terminal else None
        ),
        "recovery_event_id": None,
        "recovered_at_utc": None,
        "activity_kinds": (),
        "tool_count": 0,
        "approval_outcome": "none",
        "interruption_count": 0,
        "goal_verified": False,
        "model_route": "local.default",
        "response_path": "model",
        "completeness": completeness,
        "privacy_class": "local_internal",
        "retention_class": "operational",
    }
    payload["content_hash"] = canonical_content_hash(payload)
    return TurnExperienceReceipt.from_dict(payload)


def test_normal_write_flush_and_stop_succeed(tmp_path: Path) -> None:
    journal = TurnReceiptJournal(tmp_path / "life.db")
    assert journal.start()
    assert journal.enqueue(receipt(1))

    assert journal.flush(timeout=1.0)
    assert journal.stop(timeout=1.0)
    assert journal.status()["state"] == "stopped"
    assert journal.recent() == (receipt(1),)


def test_permanent_background_write_failure_degrades_flush_and_stop(
    tmp_path: Path,
) -> None:
    journal = TurnReceiptJournal(
        tmp_path / "life.db",
        max_write_retries=1,
        retry_delay_seconds=0.001,
    )

    def always_locked(_: TurnExperienceReceipt) -> None:
        raise sqlite3.OperationalError("database is locked")

    journal._write_once = always_locked  # type: ignore[attr-defined]
    assert journal.start()
    assert journal.enqueue(receipt(1))

    assert not journal.flush(timeout=1.0)
    assert journal.status()["state"] == "degraded"
    assert journal.status()["write_failures"] == 1
    assert not journal.stop(timeout=1.0)
    assert journal.status()["state"] == "degraded"


def test_sqlite_locked_write_retries_with_a_finite_budget(tmp_path: Path) -> None:
    journal = TurnReceiptJournal(
        tmp_path / "life.db",
        max_write_retries=2,
        retry_delay_seconds=0.001,
    )
    original_write_once = journal._write_once  # type: ignore[attr-defined]
    attempts = 0

    def locked_twice(value: TurnExperienceReceipt) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is busy")
        original_write_once(value)

    journal._write_once = locked_twice  # type: ignore[attr-defined]
    assert journal.start()
    assert journal.enqueue(receipt(1))

    assert journal.flush(timeout=1.0)
    assert attempts == 3
    assert journal.status()["write_failures"] == 0
    assert journal.stop(timeout=1.0)


def test_incomplete_receipts_are_filtered_and_paginated_beyond_recent_limit(
    tmp_path: Path,
) -> None:
    journal = TurnReceiptJournal(tmp_path / "life.db")
    assert journal.start()
    for index in range(35):
        assert journal.enqueue(receipt(index))
    assert journal.enqueue(receipt(59, completeness="complete"))
    assert journal.flush(timeout=2.0)

    first_page = journal.incomplete(limit=20)
    second_page = journal.incomplete(limit=20, offset=20)

    assert len(first_page) == 20
    assert len(second_page) == 15
    assert first_page + second_page == tuple(receipt(index) for index in range(35))
    assert all(item.completeness.value == "incomplete" for item in first_page + second_page)
    assert journal.stop(timeout=1.0)
