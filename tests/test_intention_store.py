from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from core.intention.contracts import (
    CommitmentV1,
    IntentionTransitionEventV1,
    IntentionV1,
    SuggestionBudgetV1,
    ThinkingCheckpointV1,
    VerificationRecordV1,
    canonical_content_hash,
)
from core.intention.store import (
    ConsumeSuggestionCommand,
    IntentionStore,
    IntentionStoreBusyError,
    IntentionStoreClosedError,
    IntentionStoreConflictError,
    IntentionStoreIdempotencyConflictError,
    IntentionStoreQueueFullError,
    PersistCheckpointCommand,
    PersistIntentionCommand,
    PersistSuggestionBudgetCommand,
    PersistVerificationCommand,
    RecordDerivationCommand,
    StorePriority,
)


T0 = "2026-08-20T12:00:00.000Z"
T05 = "2026-08-20T12:05:00.000Z"
T10 = "2026-08-20T12:10:00.000Z"
T60 = "2026-08-20T13:00:00.000Z"
T120 = "2026-08-20T14:00:00.000Z"
T24H = "2026-08-21T12:00:00.000Z"


def _seal(payload: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(payload)
    result.pop("content_hash", None)
    result["content_hash"] = canonical_content_hash(result)
    return result


def _intention_wire(
    *,
    intention_id: str = "intention-1",
    state: str = "candidate",
    revision: int = 1,
    updated: str = T0,
    commitment_id: str | None = None,
) -> dict[str, object]:
    return _seal(
        {
            "schema_version": 1,
            "intention_id": intention_id,
            "javis_identity_id": "identity-1",
            "instance_id": "instance-1",
            "owner_subject_id": "subject-owner",
            "participant_ids": ["subject-owner"],
            "audience": "owner_private",
            "source_event_ids": [f"event-{intention_id}"],
            "runtime_boot_id": "boot-1",
            "created_at_utc": T0,
            "updated_at_utc": updated,
            "expires_at_utc": T120,
            "privacy_class": "user_private",
            "retention_class": "continuity",
            "state": state,
            "revision": revision,
            "provenance": "conversation",
            "kind": "request",
            "trigger_kind": "explicit_command",
            "why_summary": "The owner explicitly requested this result.",
            "expected_user_value": "The result is ready and verified.",
            "goal_statement": "Create the requested result and verify it.",
            "success_criteria": [
                {
                    "schema_version": 1,
                    "criterion_id": "criterion-1",
                    "description": "The requested result is visible on read-back.",
                    "required": True,
                    "verifier_kind": "deterministic",
                    "evidence_requirements": ["file.readback"],
                    "freshness_seconds": 3600,
                    "subjective": False,
                    "user_confirmation_required": None,
                }
            ],
            "execution_budget": {
                "schema_version": 1,
                "max_plan_steps": 8,
                "max_action_attempts": 3,
                "max_wall_seconds": 900,
                "max_model_tokens": 8192,
            },
            "risk_ceiling": "low",
            "capability_hints": ["workspace.write", "workspace.read"],
            "urgency": "normal",
            "interruption_cost": "low",
            "valid_until_utc": T120,
            "cancellation_conditions": ["user.cancelled"],
            "commitment_id": commitment_id,
            "latest_checkpoint_id": None,
            "latest_verification_id": None,
            "state_reason_code": f"state.{state}",
            "supersedes_intention_id": None,
        }
    )


def _transition(
    *,
    intention_id: str = "intention-1",
    transition_id: str = "transition-1",
    from_state: str | None = None,
    to_state: str = "candidate",
    expected_revision: int = 0,
    key: str = "command-1",
    evidence_refs: list[str] | None = None,
) -> IntentionTransitionEventV1:
    return IntentionTransitionEventV1.from_dict(
        _seal(
            {
                "schema_version": 1,
                "transition_id": transition_id,
                "intention_id": intention_id,
                "from_state": from_state,
                "to_state": to_state,
                "expected_revision": expected_revision,
                "resulting_revision": expected_revision + 1,
                "idempotency_key": key,
                "reason_code": f"state.{to_state}",
                "evidence_refs": evidence_refs or [],
                "runtime_boot_id": "boot-1",
                "occurred_at_utc": T05,
            }
        )
    )


def _create_command(
    *, intention_id: str = "intention-1", key: str = "command-1"
) -> PersistIntentionCommand:
    return PersistIntentionCommand(
        intention=IntentionV1.from_dict(_intention_wire(intention_id=intention_id)),
        transition=_transition(
            intention_id=intention_id,
            transition_id=f"transition-{intention_id}",
            key=key,
        ),
    )


def _commitment() -> CommitmentV1:
    return CommitmentV1.from_dict(
        _seal(
            {
                "schema_version": 1,
                "commitment_id": "commitment-1",
                "javis_identity_id": "identity-1",
                "instance_id": "instance-1",
                "owner_subject_id": "subject-owner",
                "participant_ids": ["subject-owner"],
                "audience": "owner_private",
                "source_event_ids": ["event-intention-1"],
                "runtime_boot_id": "boot-1",
                "created_at_utc": T0,
                "updated_at_utc": T0,
                "expires_at_utc": T120,
                "privacy_class": "user_private",
                "retention_class": "continuity",
                "state": "active",
                "revision": 1,
                "provenance": "conversation",
                "intention_id": "intention-1",
                "accepted_from_event_id": "event-intention-1",
                "promise_summary": "Javis will track this request through verification.",
                "due_at_utc": T60,
                "resume_policy": "reconcile_then_ask",
                "current_blockers": [],
                "next_review_at_utc": None,
                "last_checkpoint_id": None,
                "last_action_receipt_id": None,
                "last_recovery_receipt_id": None,
                "verification_record_ids": [],
                "state_reason_code": "commitment.activated",
            }
        )
    )


def _accepted_command(key: str = "accepted-1") -> PersistIntentionCommand:
    intention = IntentionV1.from_dict(
        _intention_wire(state="accepted", commitment_id="commitment-1")
    )
    return PersistIntentionCommand(
        intention=intention,
        transition=_transition(to_state="accepted", key=key),
        commitment=_commitment(),
    )


def _checkpoint() -> ThinkingCheckpointV1:
    return ThinkingCheckpointV1.from_dict(
        _seal(
            {
                "schema_version": 1,
                "checkpoint_id": "checkpoint-1",
                "intention_id": "intention-1",
                "commitment_id": None,
                "plan_id": "plan-1",
                "plan_revision": 1,
                "stage": "awaiting_action",
                "completed_step_ids": [],
                "action_receipt_ids": [],
                "evidence_refs": [],
                "decision_summaries": [],
                "assumptions": [],
                "open_questions": [],
                "next_safe_step": {
                    "code": "request.action",
                    "summary": "Submit one bounded action request.",
                    "capability_hint": "workspace.write",
                },
                "blocker_codes": [],
                "budget_snapshot": {
                    "schema_version": 1,
                    "max_plan_steps": 8,
                    "max_action_attempts": 3,
                    "max_wall_seconds": 900,
                    "max_model_tokens": 8192,
                },
                "created_at_utc": T10,
            }
        )
    )


def _verification() -> VerificationRecordV1:
    return VerificationRecordV1.from_dict(
        _seal(
            {
                "schema_version": 1,
                "verification_id": "verification-1",
                "javis_identity_id": "identity-1",
                "instance_id": "instance-1",
                "owner_subject_id": "subject-owner",
                "participant_ids": ["subject-owner"],
                "audience": "owner_private",
                "source_event_ids": ["event-intention-1"],
                "runtime_boot_id": "boot-1",
                "created_at_utc": T10,
                "updated_at_utc": T10,
                "expires_at_utc": T60,
                "privacy_class": "user_private",
                "retention_class": "audit",
                "state": "satisfied",
                "revision": 1,
                "provenance": "goal.verifier",
                "intention_id": "intention-1",
                "intention_revision": 1,
                "commitment_id": None,
                "requested_by": "intention_service",
                "verifier_kind": "deterministic",
                "verifier_version": "exact.readback.v1",
                "criterion_results": [
                    {
                        "schema_version": 1,
                        "criterion_id": "criterion-1",
                        "outcome": "satisfied",
                        "evidence_refs": ["receipt-1"],
                        "fresh_until_utc": T60,
                        "method": "exact.content.readback",
                        "explanation_code": "content.matches",
                    }
                ],
                "evidence_refs": ["receipt-1"],
                "observed_at_utc": T05,
                "result": "satisfied",
                "limitations": [],
                "conflicting_evidence_refs": [],
            }
        )
    )


def _budget_wire(
    *,
    revision: int = 1,
    used: int = 0,
    candidate_id: str | None = None,
    intention_id: str | None = None,
) -> dict[str, object]:
    return _seal(
        {
            "schema_version": 1,
            "budget_id": "budget-1",
            "javis_identity_id": "identity-1",
            "instance_id": "instance-1",
            "owner_subject_id": "subject-owner",
            "participant_ids": ["subject-owner"],
            "audience": "owner_private",
            "source_event_ids": [],
            "runtime_boot_id": "boot-1",
            "created_at_utc": T0,
            "updated_at_utc": T0 if revision == 1 else T05,
            "expires_at_utc": T24H,
            "privacy_class": "user_private",
            "retention_class": "operational",
            "state": "available",
            "revision": revision,
            "provenance": "intention.service",
            "category": "workflow",
            "window_started_at_utc": T0,
            "window_ends_at_utc": T24H,
            "max_presentations": 3,
            "presentations_used": used,
            "global_budget_revision": revision,
            "cooldown_until_utc": None,
            "quiet_hours": {
                "timezone_id": "Asia/Shanghai",
                "starts_at_local": "22:00",
                "ends_at_local": "08:00",
            },
            "suppressed_until_utc": None,
            "last_candidate_id": candidate_id,
            "last_presented_intention_id": intention_id,
            "last_outcome": "none",
        }
    )


def test_bootstrap_uses_scoped_path_wal_fk_timeout_and_independent_reads(tmp_path):
    with IntentionStore(tmp_path, busy_timeout_ms=321) as store:
        assert store.database_path == tmp_path / "intentions" / "intentions.sqlite3"
        store.execute(_create_command(), 0, "command-1")
        assert store.get_intention("intention-1") is not None
        with sqlite3.connect(store.database_path) as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert {
            "intentions",
            "intention_transitions",
            "commitments",
            "checkpoints",
            "verification_records",
            "suggestion_budgets",
            "suggestion_consumptions",
            "command_idempotency",
            "derivations",
        } <= tables


def test_duplicate_command_replays_exact_result_and_payload_conflict_is_rejected(tmp_path):
    with IntentionStore(tmp_path) as store:
        command = _create_command()
        first = store.execute(command, 0, "command-1")
        second = store.execute(command, 0, "command-1")
        assert second == first
        different = RecordDerivationCommand(
            "derivation-1", "intention", "intention-1", ("source-1",), T0
        )
        with pytest.raises(IntentionStoreIdempotencyConflictError):
            store.execute(different, 0, "command-1")
        assert len(store.list_transitions("intention-1")) == 1


def test_transition_and_current_row_use_one_cas_transaction(tmp_path):
    with IntentionStore(tmp_path) as store:
        store.execute(_create_command(), 0, "command-1")
        cancelled = IntentionV1.from_dict(
            _intention_wire(state="cancelled", revision=2, updated=T05)
        )
        transition = _transition(
            transition_id="transition-2",
            from_state="candidate",
            to_state="cancelled",
            expected_revision=1,
            key="command-2",
        )
        result = store.execute(
            PersistIntentionCommand(cancelled, transition), 1, "command-2"
        )
        assert result.revision == 2
        stale = PersistIntentionCommand(
            cancelled,
            _transition(
                transition_id="transition-stale",
                from_state="candidate",
                to_state="cancelled",
                expected_revision=1,
                key="command-stale",
            ),
        )
        with pytest.raises(IntentionStoreConflictError):
            store.execute(stale, 1, "command-stale")
        assert store.get_intention("intention-1").revision == 2
        assert [item.resulting_revision for item in store.list_transitions("intention-1")] == [1, 2]


def test_intention_and_commitment_are_inserted_atomically_and_must_match(tmp_path):
    with IntentionStore(tmp_path) as store:
        store.execute(_accepted_command(), 0, "accepted-1")
        assert store.get_commitment("commitment-1").state.value == "active"
        bad = _accepted_command("accepted-bad")
        object.__setattr__(bad.commitment, "state", bad.commitment.state.WAITING)
        with pytest.raises(IntentionStoreConflictError):
            store.execute(bad, 0, "accepted-bad")


def test_accepting_an_existing_candidate_attaches_new_commitment_in_same_transaction(tmp_path):
    with IntentionStore(tmp_path) as store:
        store.execute(_create_command(), 0, "command-1")
        accepted = IntentionV1.from_dict(
            _intention_wire(
                state="accepted",
                revision=2,
                updated=T05,
                commitment_id="commitment-1",
            )
        )
        transition = _transition(
            transition_id="transition-accept",
            from_state="candidate",
            to_state="accepted",
            expected_revision=1,
            key="accept-command",
        )
        store.execute(
            PersistIntentionCommand(accepted, transition, _commitment()),
            1,
            "accept-command",
        )
        assert store.get_intention("intention-1").commitment_id == "commitment-1"
        assert store.get_commitment("commitment-1").revision == 1


def test_checkpoint_and_verification_parent_revision_and_immutability(tmp_path):
    with IntentionStore(tmp_path) as store:
        store.execute(_create_command(), 0, "command-1")
        store.execute(PersistCheckpointCommand(_checkpoint()), 1, "checkpoint-command")
        store.execute(
            PersistVerificationCommand(_verification()), 1, "verification-command"
        )
        assert store.get_checkpoint("checkpoint-1").intention_id == "intention-1"
        assert store.get_verification("verification-1").result.value == "satisfied"
        with sqlite3.connect(store.database_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                connection.execute(
                    "UPDATE verification_records SET result='unsatisfied' "
                    "WHERE verification_id='verification-1'"
                )
            connection.rollback()
            connection.execute("PRAGMA foreign_keys = ON")
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                connection.execute(
                    "INSERT INTO checkpoints (checkpoint_id, intention_id, commitment_id, "
                    "intention_revision, content_hash, payload_json) "
                    "VALUES ('orphan', 'missing', NULL, 1, ?, '{}')",
                    ("0" * 64,),
                )
        report = store.scan_integrity()
        assert report.ok
        assert report.rows_scanned >= 5


def test_suggestion_consumption_is_unique_and_budget_cas_is_atomic(tmp_path):
    with IntentionStore(tmp_path) as store:
        initial = SuggestionBudgetV1.from_dict(_budget_wire())
        store.execute(
            PersistSuggestionBudgetCommand(initial), 0, "budget-create"
        )
        consumed = SuggestionBudgetV1.from_dict(
            _budget_wire(
                revision=2,
                used=1,
                candidate_id="candidate-1",
                intention_id="suggestion-1",
            )
        )
        command = ConsumeSuggestionCommand(
            consumed, "candidate-1", "suggestion-1", T05
        )
        first = store.execute(command, 1, "budget-consume")
        assert store.execute(command, 1, "budget-consume") == first
        assert store.get_suggestion_budget("budget-1").presentations_used == 1
        duplicate = ConsumeSuggestionCommand(
            SuggestionBudgetV1.from_dict(
                _budget_wire(
                    revision=3,
                    used=2,
                    candidate_id="candidate-1",
                    intention_id="suggestion-2",
                )
            ),
            "candidate-1",
            "suggestion-2",
            T10,
        )
        with pytest.raises(IntentionStoreConflictError):
            store.execute(duplicate, 2, "budget-consume-duplicate")
        assert store.get_suggestion_budget("budget-1").revision == 2


def test_fault_before_commit_rolls_back_entire_command_and_retry_succeeds(tmp_path):
    fail_once = True

    def injector(stage, command):
        nonlocal fail_once
        if stage == "before_commit" and fail_once:
            fail_once = False
            raise RuntimeError("simulated crash")

    with IntentionStore(tmp_path, fault_injector=injector) as store:
        with pytest.raises(RuntimeError, match="simulated crash"):
            store.execute(_create_command(), 0, "command-1")
        assert store.get_intention("intention-1") is None
        assert store.execute(_create_command(), 0, "command-1").revision == 1


def test_locked_sqlite_fails_boundedly_without_killing_writer(tmp_path):
    with IntentionStore(tmp_path, busy_timeout_ms=50) as store:
        lock = sqlite3.connect(store.database_path, isolation_level=None)
        lock.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        try:
            with pytest.raises(IntentionStoreBusyError):
                store.execute(_create_command(), 0, "locked-command")
        finally:
            lock.execute("ROLLBACK")
            lock.close()
        assert time.monotonic() - started < 1.0
        assert store.execute(
            _create_command(key="retry-command"), 0, "retry-command"
        ).revision == 1


def test_only_one_store_can_hold_the_writer_lease(tmp_path):
    first = IntentionStore(tmp_path)
    try:
        with pytest.raises(IntentionStoreConflictError, match="writer"):
            IntentionStore(tmp_path)
    finally:
        first.close()
    with IntentionStore(tmp_path) as reopened:
        assert reopened.scan_integrity().ok


def test_normal_queue_is_bounded_while_critical_reserve_remains_available(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def injector(stage, command):
        if stage == "before_commit" and isinstance(command, RecordDerivationCommand):
            if command.derivation_id == "normal-1":
                entered.set()
                assert release.wait(2)

    with IntentionStore(
        tmp_path, queue_capacity=1, critical_reserve=1, fault_injector=injector
    ) as store:
        results: list[object] = []

        def submit(command, key):
            try:
                results.append(store.execute(command, 0, key))
            except BaseException as exc:
                results.append(exc)

        normal = RecordDerivationCommand(
            "normal-1", "test", "normal-1", ("source-1",), T0
        )
        normal_thread = threading.Thread(target=submit, args=(normal, "normal-key"))
        normal_thread.start()
        assert entered.wait(1)
        with pytest.raises(IntentionStoreQueueFullError):
            store.execute(
                RecordDerivationCommand(
                    "normal-2", "test", "normal-2", ("source-2",), T0
                ),
                0,
                "normal-key-2",
            )
        critical = RecordDerivationCommand(
            "critical-1",
            "test",
            "critical-1",
            ("source-critical",),
            T0,
            priority=StorePriority.CRITICAL,
        )
        critical_thread = threading.Thread(
            target=submit, args=(critical, "critical-key")
        )
        critical_thread.start()
        release.set()
        normal_thread.join(2)
        critical_thread.join(2)
        assert len(results) == 2
        assert not any(isinstance(item, BaseException) for item in results)


def test_shutdown_drains_accepted_commands_and_rejects_post_close_race(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def injector(stage, command):
        if stage == "before_commit":
            entered.set()
            assert release.wait(2)

    store = IntentionStore(tmp_path, fault_injector=injector)
    result: list[object] = []
    submitter = threading.Thread(
        target=lambda: result.append(store.execute(_create_command(), 0, "command-1"))
    )
    submitter.start()
    assert entered.wait(1)
    closer = threading.Thread(target=store.close)
    closer.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            store.execute(
                RecordDerivationCommand(
                    "late", "test", "late", ("source-late",), T0
                ),
                0,
                "late-command",
            )
        except IntentionStoreClosedError:
            break
        except IntentionStoreQueueFullError:
            time.sleep(0.01)
    else:
        pytest.fail("shutdown did not stop command acceptance")
    release.set()
    submitter.join(2)
    closer.join(2)
    assert len(result) == 1
    assert store.closed
    with pytest.raises(IntentionStoreClosedError):
        store.execute(_create_command(key="after-close"), 0, "after-close")


def test_integrity_scan_detects_payload_tampering(tmp_path):
    with IntentionStore(tmp_path) as store:
        store.execute(_create_command(), 0, "command-1")
        with sqlite3.connect(store.database_path) as connection:
            payload = json.loads(
                connection.execute(
                    "SELECT payload_json FROM intentions WHERE intention_id='intention-1'"
                ).fetchone()[0]
            )
            payload["goal_statement"] = "tampered"
            connection.execute(
                "UPDATE intentions SET payload_json=? WHERE intention_id='intention-1'",
                (json.dumps(payload),),
            )
        report = store.scan_integrity()
        assert not report.ok
        assert any("intention-1" in error for error in report.errors)


def test_backup_restore_rolls_back_and_store_remains_usable(tmp_path):
    backup = tmp_path / "backups" / "intentions.sqlite3"
    with IntentionStore(tmp_path) as store:
        store.execute(_create_command(), 0, "command-1")
        store.backup_to(backup)
        extra = RecordDerivationCommand(
            "derivation-extra", "test", "extra", ("source-extra",), T0
        )
        store.execute(extra, 0, "extra-command")
        before = store.scan_integrity().rows_scanned
        store.restore_from_backup(backup)
        after = store.scan_integrity().rows_scanned
        assert after < before
        result = store.execute(
            RecordDerivationCommand(
                "derivation-after", "test", "after", ("source-after",), T0
            ),
            0,
            "after-restore-command",
        )
        assert result.state == "recorded"


def test_v0_fixture_migrates_with_backup_and_preserves_legacy_data(tmp_path):
    database = tmp_path / "intentions" / "intentions.sqlite3"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_fixture (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_fixture VALUES ('preserved')")
        connection.execute("PRAGMA user_version = 0")
    with IntentionStore(tmp_path) as store:
        assert store.scan_integrity().schema_version == 1
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT value FROM legacy_fixture").fetchone()[0] == "preserved"
        assert database.with_suffix(".pre-migration.sqlite3").is_file()
