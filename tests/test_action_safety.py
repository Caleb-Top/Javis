from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.action.contracts import (
    ActionSourceKind,
    ApprovalAuthorityHealth,
    EffectClass,
    JournalHealth,
    ReceiptChainHealth,
    SafetyMode,
)
from core.action.safety import (
    LocalSafetyApproval,
    SafetyAdmissionError,
    SafetyConflictError,
    SafetyControlContext,
    SafetyController,
    SafetyResetDeniedError,
    SafetyStateUnavailableError,
)


HASH_A = "a" * 64


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.value
        self.value += timedelta(milliseconds=1)
        return value


def _ids():
    sequence = 0

    def create() -> str:
        nonlocal sequence
        sequence += 1
        return f"safety-{sequence}"

    return create


def _context(*scopes: str, source: ActionSourceKind = ActionSourceKind.CONTROL_HTTP):
    return SafetyControlContext(
        runtime_boot_id="boot-1",
        client_instance_hash="b" * 64,
        scopes=frozenset(scopes),
        source_kind=source,
        binding_source="packaged_desktop",
    )


def _controller(data_root: Path, *, verifier=None) -> SafetyController:
    return SafetyController(
        data_root=data_root,
        javis_identity_id="javis-1",
        instance_id="instance-1",
        runtime_boot_id="boot-1",
        permission_revision=7,
        policy_version="policy.v1",
        now=_Clock(),
        id_factory=_ids(),
        file_sync=lambda _descriptor: None,
        local_approval_verifier=verifier,
    )


def test_initialize_persists_immutable_snapshot_and_current_pointer(tmp_path: Path) -> None:
    controller = _controller(tmp_path)

    snapshot = controller.initialize()

    assert snapshot.mode is SafetyMode.NORMAL
    assert controller.current_snapshot() == snapshot
    pointer = json.loads(controller.current_pointer_path.read_text(encoding="utf-8"))
    assert pointer["safety_revision"] == 1
    assert pointer["content_hash"] == snapshot.content_hash
    assert controller.snapshot_directory.joinpath(pointer["snapshot_file"]).is_file()

    reloaded = _controller(tmp_path)
    assert reloaded.current_snapshot() == snapshot


@pytest.mark.parametrize("condition", ["missing", "corrupt_pointer", "corrupt_snapshot"])
def test_missing_or_corrupt_state_is_closed(condition: str, tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    if condition != "missing":
        snapshot = controller.initialize()
        if condition == "corrupt_pointer":
            controller.current_pointer_path.write_text("{broken", encoding="utf-8")
        else:
            pointer = json.loads(controller.current_pointer_path.read_text(encoding="utf-8"))
            path = controller.snapshot_directory / pointer["snapshot_file"]
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["mode"] = "normal" if snapshot.mode is not SafetyMode.NORMAL else "safe"
            path.write_text(json.dumps(payload), encoding="utf-8")

    closed = controller.current_snapshot()

    assert closed.mode is SafetyMode.SAFE
    assert closed.effect_admission.value == "closed"
    with pytest.raises(SafetyStateUnavailableError):
        controller.require_current()
    with pytest.raises(SafetyAdmissionError):
        controller.require_effect_admission(
            effect_class=EffectClass.REVERSIBLE,
            action_request_id="action-1",
            expected_revision=closed.safety_revision,
        )


def test_trip_is_persistent_and_requires_exact_scope(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    initial = controller.initialize()

    with pytest.raises(SafetyAdmissionError):
        controller.trip(
            expected_revision=initial.safety_revision,
            context=_context("permission.admin", "root"),
        )

    tripped = controller.trip(
        expected_revision=initial.safety_revision,
        context=_context("control.fuse.trip"),
    )

    assert tripped.mode is SafetyMode.SAFE
    assert tripped.fuse_tripped is True
    assert tripped.fuse_reason_code == "manual_fuse_trip"
    assert _controller(tmp_path).current_snapshot() == tripped
    with pytest.raises(SafetyConflictError):
        controller.trip(
            expected_revision=initial.safety_revision,
            context=_context("control.fuse.trip"),
        )


def test_safe_mode_keeps_every_ordinary_effect_adapter_at_zero_calls(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    initial = controller.initialize()
    safe = controller.trip(
        expected_revision=initial.safety_revision,
        context=_context("control.fuse.trip"),
    )
    adapter_calls = 0

    for effect_class in (
        EffectClass.REVERSIBLE,
        EffectClass.COMPENSATABLE,
        EffectClass.IRREVERSIBLE,
    ):
        decision = controller.admit_effect(
            effect_class=effect_class,
            action_request_id=f"action-{effect_class.value}",
            expected_revision=safe.safety_revision,
        )
        if decision.allowed:
            adapter_calls += 1

    assert adapter_calls == 0
    read_only = controller.admit_effect(
        effect_class=EffectClass.NONE,
        action_request_id="read-only",
        expected_revision=safe.safety_revision,
    )
    assert read_only.allowed is True


def test_recovery_only_accepts_one_exact_process_local_permit(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    initial = controller.initialize()
    recovery = controller.update_health(
        expected_revision=initial.safety_revision,
        receipt_chain_health=ReceiptChainHealth.TAMPERED,
    )
    context = _context("action.recover", source=ActionSourceKind.RECOVERY)
    permit = controller.executor_controls.issue_recovery_permit(
        expected_revision=recovery.safety_revision,
        context=context,
        action_request_id="recovery-action-1",
        source_action_request_id="uncertain-action-1",
        authorization_grant_id_hash=HASH_A,
    )

    denied = controller.admit_effect(
        effect_class=EffectClass.COMPENSATABLE,
        action_request_id="different-action",
        expected_revision=recovery.safety_revision,
        authorization_grant_id_hash=HASH_A,
        recovery_permit=permit,
    )
    assert denied.allowed is False

    wrong_source = controller.admit_effect(
        effect_class=EffectClass.COMPENSATABLE,
        action_request_id="recovery-action-1",
        expected_revision=recovery.safety_revision,
        source_action_request_id="different-source-action",
        authorization_grant_id_hash=HASH_A,
        recovery_permit=permit,
    )
    assert wrong_source.allowed is False

    allowed = controller.admit_effect(
        effect_class=EffectClass.COMPENSATABLE,
        action_request_id="recovery-action-1",
        expected_revision=recovery.safety_revision,
        source_action_request_id="uncertain-action-1",
        authorization_grant_id_hash=HASH_A,
        recovery_permit=permit,
    )
    assert allowed.allowed is True

    reused = controller.admit_effect(
        effect_class=EffectClass.COMPENSATABLE,
        action_request_id="recovery-action-1",
        expected_revision=recovery.safety_revision,
        source_action_request_id="uncertain-action-1",
        authorization_grant_id_hash=HASH_A,
        recovery_permit=permit,
    )
    assert reused.allowed is False
    permit._consumed = False
    assert not controller.admit_effect(
        effect_class=EffectClass.COMPENSATABLE,
        action_request_id="recovery-action-1",
        expected_revision=recovery.safety_revision,
        source_action_request_id="uncertain-action-1",
        authorization_grant_id_hash=HASH_A,
        recovery_permit=permit,
    ).allowed
    with pytest.raises(TypeError):
        json.dumps(permit)


def test_recovery_permit_rejects_scope_source_grant_and_revision_bypasses(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    initial = controller.initialize()
    recovery = controller.update_health(
        expected_revision=initial.safety_revision,
        receipt_chain_health=ReceiptChainHealth.GAP,
    )

    for context in (
        _context("root", source=ActionSourceKind.RECOVERY),
        _context("action.recover", source=ActionSourceKind.AGENT),
        _context("action.recover", source=ActionSourceKind.SUBAGENT),
    ):
        with pytest.raises(SafetyAdmissionError):
            controller.executor_controls.issue_recovery_permit(
                expected_revision=recovery.safety_revision,
                context=context,
                action_request_id="recovery-action-1",
                source_action_request_id="uncertain-action-1",
                authorization_grant_id_hash=HASH_A,
            )

    valid = controller.executor_controls.issue_recovery_permit(
        expected_revision=recovery.safety_revision,
        context=_context("action.recover", source=ActionSourceKind.RECOVERY),
        action_request_id="recovery-action-1",
        source_action_request_id="uncertain-action-1",
        authorization_grant_id_hash=HASH_A,
    )
    assert not controller.admit_effect(
        effect_class=EffectClass.COMPENSATABLE,
        action_request_id="recovery-action-1",
        expected_revision=recovery.safety_revision,
        source_action_request_id="uncertain-action-1",
        authorization_grant_id_hash="c" * 64,
        recovery_permit=valid,
    ).allowed

    newer = controller.enter_recovery_only(
        expected_revision=recovery.safety_revision,
        reason_code="receipt_chain_gap",
    )
    assert not controller.admit_effect(
        effect_class=EffectClass.COMPENSATABLE,
        action_request_id="recovery-action-1",
        expected_revision=newer.safety_revision,
        source_action_request_id="uncertain-action-1",
        authorization_grant_id_hash=HASH_A,
        recovery_permit=valid,
    ).allowed


def test_reset_requires_scope_local_verified_approval_and_healthy_reconciliation(
    tmp_path: Path,
) -> None:
    expected_approval = LocalSafetyApproval(
        approval_id="approval-1",
        runtime_boot_id="boot-1",
        safety_revision=2,
        client_instance_hash="b" * 64,
        decided_at_utc="2026-08-20T01:02:03.000Z",
        expires_at_utc="2026-08-20T01:07:03.000Z",
    )

    def verifier(approval, snapshot, context) -> bool:
        return approval == expected_approval and snapshot.safety_revision == 2 and context.is_local

    controller = _controller(tmp_path, verifier=verifier)
    initial = controller.initialize()
    safe = controller.trip(
        expected_revision=initial.safety_revision,
        context=_context("control.fuse.trip"),
    )

    with pytest.raises(SafetyResetDeniedError):
        controller.reset_request(
            expected_revision=safe.safety_revision,
            context=_context("root", "permission.admin"),
            local_approval=True,
        )
    with pytest.raises(SafetyResetDeniedError):
        controller.reset_request(
            expected_revision=safe.safety_revision,
            context=_context("control.fuse.reset", source=ActionSourceKind.AGENT),
            local_approval=expected_approval,
        )

    reset = controller.reset_request(
        expected_revision=safe.safety_revision,
        context=_context("control.fuse.reset"),
        local_approval=expected_approval,
    )
    assert reset.mode is SafetyMode.NORMAL
    assert reset.fuse_tripped is False


def test_reset_fails_when_health_or_reconciliation_is_incomplete(tmp_path: Path) -> None:
    controller = _controller(tmp_path, verifier=lambda *_: True)
    initial = controller.initialize()
    reconciling = controller.begin_reconciliation(
        expected_revision=initial.safety_revision,
        uncertain_action_ids=("action-uncertain",),
        reconcile_cursor="cursor-1",
    )
    approval = LocalSafetyApproval(
        approval_id="approval-1",
        runtime_boot_id="boot-1",
        safety_revision=reconciling.safety_revision,
        client_instance_hash="b" * 64,
        decided_at_utc="2026-08-20T01:02:03.000Z",
        expires_at_utc="2026-08-20T01:07:03.000Z",
    )

    with pytest.raises(SafetyResetDeniedError):
        controller.reset_request(
            expected_revision=reconciling.safety_revision,
            context=_context("control.fuse.reset"),
            local_approval=approval,
        )

    recovery = controller.update_health(
        expected_revision=reconciling.safety_revision,
        journal_health=JournalHealth.UNAVAILABLE,
        receipt_chain_health=ReceiptChainHealth.UNAVAILABLE,
        approval_authority_health=ApprovalAuthorityHealth.UNAVAILABLE,
    )
    with pytest.raises(SafetyResetDeniedError):
        controller.reset_request(
            expected_revision=recovery.safety_revision,
            context=_context("control.fuse.reset"),
            local_approval=approval,
        )


def test_cancel_and_stop_are_internal_controls_with_exact_scopes(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    initial = controller.initialize()

    with pytest.raises(SafetyAdmissionError):
        controller.cancel(
            action_request_id="action-1",
            context=_context("action.execute"),
        )
    cancelled = controller.cancel(
        action_request_id="action-1",
        context=_context("control.cancel"),
    )
    assert cancelled.operation == "cancel"
    assert cancelled.adapter_invoked is False

    shutdown = controller.stop(
        expected_revision=initial.safety_revision,
        context=_context("runtime.shutdown"),
    )
    assert shutdown.mode is SafetyMode.SHUTDOWN
    assert shutdown.effect_admission.value == "closed"


def test_pointer_path_escape_and_snapshot_substitution_fail_closed(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    controller.initialize()
    pointer = json.loads(controller.current_pointer_path.read_text(encoding="utf-8"))
    pointer["snapshot_file"] = "../../outside.json"
    controller.current_pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    assert controller.current_snapshot().mode is SafetyMode.SAFE
    with pytest.raises(SafetyStateUnavailableError):
        controller.require_current()


def test_current_pointer_cannot_roll_back_persistent_fuse(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    initial = controller.initialize()
    old_pointer = controller.current_pointer_path.read_bytes()
    controller.trip(
        expected_revision=initial.safety_revision,
        context=_context("control.fuse.trip"),
    )

    controller.current_pointer_path.write_bytes(old_pointer)

    assert controller.current_snapshot().mode is SafetyMode.SAFE
    with pytest.raises(SafetyStateUnavailableError):
        controller.require_current()


def test_failed_current_pointer_switch_leaves_controller_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _controller(tmp_path)
    initial = controller.initialize()
    original = controller._atomic_write

    def fail_pointer(path: Path, data: bytes) -> None:
        if path == controller.current_pointer_path:
            raise OSError("injected pointer failure")
        original(path, data)

    monkeypatch.setattr(controller, "_atomic_write", fail_pointer)
    with pytest.raises(SafetyStateUnavailableError):
        controller.trip(
            expected_revision=initial.safety_revision,
            context=_context("control.fuse.trip"),
        )

    assert controller.current_snapshot().effect_admission.value == "closed"
    with pytest.raises(SafetyStateUnavailableError):
        controller.require_current()
