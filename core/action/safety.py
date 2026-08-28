"""Fail-closed persistent safety state for governed Javis actions.

The controller owns immutable ``SafetySnapshotV1`` files and atomically moves
one small current pointer.  It never invokes an effect adapter.  Callers must
obtain an admission decision immediately before the executor crosses that
boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import unicodedata
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.action.contracts import (
    SAFETY_REASON_CODES,
    ActionSourceKind,
    ApprovalAuthorityHealth,
    EffectAdmission,
    EffectClass,
    JournalHealth,
    ReceiptChainHealth,
    SafetyMode,
    SafetySnapshotV1,
    canonical_content_hash,
    canonical_json_bytes,
)


_POINTER_SCHEMA_VERSION = 1
_MAX_POINTER_BYTES = 4096
_MAX_SNAPSHOT_BYTES = 32 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_SNAPSHOT_FILE = re.compile(r"snapshot-(\d{20})-([0-9a-f]{64})\.json")
_LOCAL_BINDINGS = frozenset({"packaged_desktop", "loopback_web", "sidecar"})
_RESET_SOURCE = ActionSourceKind.CONTROL_HTTP
_RECOVERY_SOURCE = ActionSourceKind.RECOVERY
_DYNAMIC_HEALTH_REASONS = frozenset(
    {
        "approval_authority_unavailable",
        "journal_unavailable",
        "receipt_chain_gap",
        "receipt_chain_tampered",
    }
)


class SafetyError(RuntimeError):
    """Base class for stable safety controller failures."""

    reason_code = "safety_error"

    def __init__(self, reason_code: str | None = None) -> None:
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class SafetyStateUnavailableError(SafetyError):
    reason_code = "safety_state_unavailable"


class SafetyConflictError(SafetyError):
    reason_code = "stale_safety_revision"


class SafetyAdmissionError(SafetyError):
    reason_code = "safety_admission_denied"


class SafetyResetDeniedError(SafetyError):
    reason_code = "safety_reset_denied"


def _bounded_text(value: Any, field_name: str, *, maximum: int = 256) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not normalized or len(normalized.encode("utf-8")) > maximum:
        raise ValueError(f"{field_name} must be a bounded non-empty string")
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise ValueError(f"{field_name} must not contain control characters")
    return normalized


def _stable_code(value: Any, field_name: str) -> str:
    normalized = _bounded_text(value, field_name, maximum=128)
    if _CODE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a stable lowercase code")
    return normalized


def _sha256(value: Any, field_name: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256 hex")
    return value


def _source(value: Any) -> ActionSourceKind:
    if isinstance(value, ActionSourceKind):
        return value
    try:
        return ActionSourceKind(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("source_kind must be a known ActionSourceKind") from exc


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("clock must return an aware datetime")
    return value.astimezone(UTC)


def _wire_time(value: datetime) -> str:
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be canonical RFC3339 UTC") from exc
    return parsed.replace(tzinfo=UTC)


def _normalize_scopes(scopes: Iterable[str]) -> frozenset[str]:
    if isinstance(scopes, (str, bytes, bytearray)):
        raise ValueError("scopes must be an iterable of exact scope strings")
    normalized = frozenset(_stable_code(scope, "scope") for scope in scopes)
    if not normalized or any("*" in scope for scope in normalized):
        raise ValueError("scopes must be non-empty and must not contain wildcards")
    return normalized


@dataclass(frozen=True, slots=True)
class SafetyControlContext:
    """Server-built context for executor-internal safety controls."""

    runtime_boot_id: str
    client_instance_hash: str
    scopes: frozenset[str]
    source_kind: ActionSourceKind
    binding_source: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "runtime_boot_id", _bounded_text(self.runtime_boot_id, "runtime_boot_id")
        )
        object.__setattr__(
            self,
            "client_instance_hash",
            _sha256(self.client_instance_hash, "client_instance_hash"),
        )
        object.__setattr__(self, "scopes", _normalize_scopes(self.scopes))
        object.__setattr__(self, "source_kind", _source(self.source_kind))
        object.__setattr__(
            self,
            "binding_source",
            _stable_code(self.binding_source, "binding_source"),
        )

    @property
    def is_local(self) -> bool:
        return self.binding_source in _LOCAL_BINDINGS

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass(frozen=True, slots=True)
class LocalSafetyApproval:
    """Exact reset approval projection verified by the approval authority."""

    approval_id: str
    runtime_boot_id: str
    safety_revision: int
    client_instance_hash: str
    decided_at_utc: str
    expires_at_utc: str

    def __post_init__(self) -> None:
        for name in ("approval_id", "runtime_boot_id"):
            object.__setattr__(self, name, _bounded_text(getattr(self, name), name))
        if type(self.safety_revision) is not int or self.safety_revision < 1:
            raise ValueError("safety_revision must be a positive integer")
        object.__setattr__(
            self,
            "client_instance_hash",
            _sha256(self.client_instance_hash, "client_instance_hash"),
        )
        decided = _parse_time(self.decided_at_utc)
        expires = _parse_time(self.expires_at_utc)
        if not decided < expires or (expires - decided).total_seconds() > 300:
            raise ValueError("reset approval must expire within five minutes")


@dataclass(frozen=True, slots=True)
class SafetyAdmissionDecision:
    allowed: bool
    reason_code: str
    safety_revision: int
    mode: SafetyMode


@dataclass(frozen=True, slots=True)
class SafetyControlResult:
    operation: str
    target_id: str
    safety_revision: int
    adapter_invoked: bool = False


class RecoverySafetyPermit:
    """Process-local, one-use proof for one exact recovery action."""

    __slots__ = (
        "action_request_id",
        "source_action_request_id",
        "authorization_grant_id_hash",
        "runtime_boot_id",
        "safety_revision",
        "_nonce",
        "_tag",
        "_consumed",
        "_lock",
    )

    def __init__(
        self,
        *,
        action_request_id: str,
        source_action_request_id: str,
        authorization_grant_id_hash: str,
        runtime_boot_id: str,
        safety_revision: int,
        nonce: str,
        tag: str,
    ) -> None:
        self.action_request_id = action_request_id
        self.source_action_request_id = source_action_request_id
        self.authorization_grant_id_hash = authorization_grant_id_hash
        self.runtime_boot_id = runtime_boot_id
        self.safety_revision = safety_revision
        self._nonce = nonce
        self._tag = tag
        self._consumed = False
        self._lock = threading.Lock()

    @property
    def consumed(self) -> bool:
        with self._lock:
            return self._consumed

    def __repr__(self) -> str:
        return (
            "RecoverySafetyPermit("
            f"action_request_id={self.action_request_id!r}, "
            f"safety_revision={self.safety_revision}, consumed={self.consumed})"
        )

    def __reduce__(self):
        raise TypeError("RecoverySafetyPermit is process-local and cannot be serialized")

    def to_dict(self) -> dict[str, object]:
        raise TypeError("RecoverySafetyPermit is process-local and cannot be serialized")


class SafetyExecutorControls:
    """Narrow capability object held by ActionExecutor, never by the model."""

    __slots__ = ("_controller",)

    def __init__(self, controller: "SafetyController") -> None:
        self._controller = controller

    def trip(self, **kwargs: Any) -> SafetySnapshotV1:
        return self._controller.trip(**kwargs)

    def cancel(self, **kwargs: Any) -> SafetyControlResult:
        return self._controller.cancel(**kwargs)

    def stop(self, **kwargs: Any) -> SafetySnapshotV1:
        return self._controller.stop(**kwargs)

    def reset_request(self, **kwargs: Any) -> SafetySnapshotV1:
        return self._controller.reset_request(**kwargs)

    def issue_recovery_permit(self, **kwargs: Any) -> RecoverySafetyPermit:
        return self._controller._issue_recovery_permit(**kwargs)

    def __reduce__(self):
        raise TypeError("SafetyExecutorControls is process-local and cannot be serialized")


class SafetyController:
    """Own persistent safety state and decide pre-adapter effect admission."""

    def __init__(
        self,
        *,
        data_root: str | os.PathLike[str],
        javis_identity_id: str,
        instance_id: str,
        runtime_boot_id: str,
        permission_revision: int,
        policy_version: str,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        file_sync: Callable[[int], None] | None = None,
        local_approval_verifier: Callable[
            [LocalSafetyApproval, SafetySnapshotV1, SafetyControlContext], bool
        ]
        | None = None,
    ) -> None:
        root = Path(data_root).expanduser()
        if not root.is_absolute():
            root = root.absolute()
        self._data_root = root.resolve(strict=False)
        self._javis_identity_id = _bounded_text(javis_identity_id, "javis_identity_id")
        self._instance_id = _bounded_text(instance_id, "instance_id")
        self._runtime_boot_id = _bounded_text(runtime_boot_id, "runtime_boot_id")
        if type(permission_revision) is not int or permission_revision < 0:
            raise ValueError("permission_revision must be a non-negative integer")
        self._permission_revision = permission_revision
        self._policy_version = _stable_code(policy_version, "policy_version")
        self._now = now or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"safety-{secrets.token_hex(16)}")
        self._file_sync = file_sync or os.fsync
        self._local_approval_verifier = local_approval_verifier
        self._storage_directory = self._data_root / "actions" / "safety"
        self._snapshot_directory = self._storage_directory / "snapshots"
        self._current_pointer_path = self._storage_directory / "current.json"
        self._permit_key = secrets.token_bytes(32)
        self._spent_permit_nonces: set[str] = set()
        self._spent_permit_order: deque[str] = deque()
        self._lock = threading.RLock()
        self._executor_controls = SafetyExecutorControls(self)

    @property
    def current_pointer_path(self) -> Path:
        return self._current_pointer_path

    @property
    def snapshot_directory(self) -> Path:
        return self._snapshot_directory

    @property
    def executor_controls(self) -> SafetyExecutorControls:
        return self._executor_controls

    def initialize(self) -> SafetySnapshotV1:
        """Create the only allowed healthy genesis; never repair ambiguous state."""

        with self._lock:
            self._prepare_directories_locked()
            if self._current_pointer_path.exists():
                raise SafetyConflictError("safety_state_already_initialized")
            if any(self._snapshot_directory.iterdir()):
                raise SafetyStateUnavailableError("orphaned_safety_snapshots")
            snapshot = self._build_snapshot(
                safety_revision=1,
                mode=SafetyMode.NORMAL,
                reason_codes=(),
                fuse_tripped=False,
                fuse_reason_code=None,
                journal_health=JournalHealth.HEALTHY,
                receipt_chain_health=ReceiptChainHealth.HEALTHY,
                approval_authority_health=ApprovalAuthorityHealth.HEALTHY,
                uncertain_action_ids=(),
                reconcile_cursor=None,
                last_reconciled_at_utc=None,
            )
            self._persist_snapshot_locked(snapshot)
            return snapshot

    def current_snapshot(self) -> SafetySnapshotV1:
        """Return current state, substituting a valid closed view on any fault."""

        try:
            return self.require_current()
        except SafetyStateUnavailableError:
            return self._closed_snapshot()

    def require_current(self) -> SafetySnapshotV1:
        with self._lock:
            return self._load_current_locked()

    def state_available(self) -> bool:
        try:
            self.require_current()
        except SafetyStateUnavailableError:
            return False
        return True

    def trip(
        self,
        *,
        expected_revision: int,
        context: SafetyControlContext,
        reason_code: str = "manual_fuse_trip",
    ) -> SafetySnapshotV1:
        self._require_context(context, scope="control.fuse.trip", local=True)
        reason = self._reason(reason_code)
        with self._lock:
            current = self._current_for_update_locked(expected_revision)
            if current.mode not in (SafetyMode.NORMAL, SafetyMode.SAFE):
                raise SafetyAdmissionError("fuse_trip_mode_denied")
            if current.fuse_tripped and current.fuse_reason_code == reason:
                return current
            reasons = set(current.reason_codes)
            reasons.add(reason)
            return self._replace_locked(
                current,
                mode=SafetyMode.SAFE,
                reason_codes=reasons,
                fuse_tripped=True,
                fuse_reason_code=reason,
                reconcile_cursor=None,
            )

    def cancel(
        self,
        *,
        action_request_id: str,
        context: SafetyControlContext,
    ) -> SafetyControlResult:
        action_id = _bounded_text(action_request_id, "action_request_id")
        self._require_boot(context)
        if not ({"control.cancel", "conversation.cancel"} & context.scopes):
            raise SafetyAdmissionError("runtime_scope_required")
        if context.source_kind in {
            ActionSourceKind.AGENT,
            ActionSourceKind.SUBAGENT,
            ActionSourceKind.CRON,
        }:
            raise SafetyAdmissionError("control_source_denied")
        snapshot = self.current_snapshot()
        return SafetyControlResult(
            operation="cancel",
            target_id=action_id,
            safety_revision=snapshot.safety_revision,
            adapter_invoked=False,
        )

    def stop(
        self,
        *,
        expected_revision: int,
        context: SafetyControlContext,
    ) -> SafetySnapshotV1:
        self._require_context(context, scope="runtime.shutdown", local=True)
        with self._lock:
            current = self._current_for_update_locked(expected_revision)
            if current.mode is SafetyMode.SHUTDOWN:
                return current
            reasons = set(current.reason_codes)
            reasons.add("shutdown_requested")
            return self._replace_locked(
                current,
                mode=SafetyMode.SHUTDOWN,
                reason_codes=reasons,
                reconcile_cursor=None,
            )

    def reset_request(
        self,
        *,
        expected_revision: int,
        context: SafetyControlContext,
        local_approval: object,
    ) -> SafetySnapshotV1:
        try:
            self._require_context(
                context,
                scope="control.fuse.reset",
                local=True,
                exact_source=_RESET_SOURCE,
            )
        except SafetyAdmissionError as exc:
            raise SafetyResetDeniedError(exc.reason_code) from exc
        if not isinstance(local_approval, LocalSafetyApproval):
            raise SafetyResetDeniedError("local_reset_approval_required")
        with self._lock:
            current = self._current_for_update_locked(expected_revision)
            if current.mode is not SafetyMode.SAFE:
                raise SafetyResetDeniedError("safety_reset_mode_denied")
            if (
                local_approval.runtime_boot_id != self._runtime_boot_id
                or local_approval.safety_revision != current.safety_revision
                or local_approval.client_instance_hash != context.client_instance_hash
            ):
                raise SafetyResetDeniedError("reset_approval_binding_mismatch")
            now = _utc(self._now())
            if not (
                _parse_time(local_approval.decided_at_utc)
                <= now
                < _parse_time(local_approval.expires_at_utc)
            ):
                raise SafetyResetDeniedError("reset_approval_expired")
            if not current.fuse_tripped:
                raise SafetyResetDeniedError("persistent_fuse_not_tripped")
            if current.uncertain_action_ids or current.reconcile_cursor is not None:
                raise SafetyResetDeniedError("reconciliation_incomplete")
            if (
                current.journal_health is not JournalHealth.HEALTHY
                or current.receipt_chain_health is not ReceiptChainHealth.HEALTHY
                or current.approval_authority_health
                is not ApprovalAuthorityHealth.HEALTHY
            ):
                raise SafetyResetDeniedError("safety_dependencies_unhealthy")
            verifier = self._local_approval_verifier
            try:
                verified = bool(verifier and verifier(local_approval, current, context))
            except Exception as exc:
                raise SafetyResetDeniedError("reset_approval_verifier_failed") from exc
            if not verified:
                raise SafetyResetDeniedError("reset_approval_unverified")
            return self._replace_locked(
                current,
                mode=SafetyMode.NORMAL,
                reason_codes=(),
                fuse_tripped=False,
                fuse_reason_code=None,
                reconcile_cursor=None,
            )

    def update_health(
        self,
        *,
        expected_revision: int,
        journal_health: JournalHealth | str | None = None,
        receipt_chain_health: ReceiptChainHealth | str | None = None,
        approval_authority_health: ApprovalAuthorityHealth | str | None = None,
    ) -> SafetySnapshotV1:
        with self._lock:
            current = self._current_for_update_locked(expected_revision)
            journal = self._enum_or_current(journal_health, JournalHealth, current.journal_health)
            chain = self._enum_or_current(
                receipt_chain_health, ReceiptChainHealth, current.receipt_chain_health
            )
            approval = self._enum_or_current(
                approval_authority_health,
                ApprovalAuthorityHealth,
                current.approval_authority_health,
            )
            mode, reasons = self._derive_mode(
                current=current,
                journal=journal,
                chain=chain,
                approval=approval,
            )
            return self._replace_locked(
                current,
                mode=mode,
                reason_codes=reasons,
                journal_health=journal,
                receipt_chain_health=chain,
                approval_authority_health=approval,
            )

    def begin_reconciliation(
        self,
        *,
        expected_revision: int,
        uncertain_action_ids: Iterable[str],
        reconcile_cursor: str,
    ) -> SafetySnapshotV1:
        uncertain = self._uncertain_ids(uncertain_action_ids)
        cursor = _bounded_text(reconcile_cursor, "reconcile_cursor")
        with self._lock:
            current = self._current_for_update_locked(expected_revision)
            if current.receipt_chain_health is not ReceiptChainHealth.HEALTHY:
                raise SafetyAdmissionError("receipt_chain_unhealthy")
            reasons = set(current.reason_codes)
            reasons.add("startup_reconciliation")
            if uncertain:
                reasons.add("unknown_inflight_effect")
            return self._replace_locked(
                current,
                mode=SafetyMode.RECONCILING,
                reason_codes=reasons,
                uncertain_action_ids=uncertain,
                reconcile_cursor=cursor,
            )

    def complete_reconciliation(
        self,
        *,
        expected_revision: int,
        remaining_uncertain_action_ids: Iterable[str] = (),
        reconcile_cursor: str | None = None,
    ) -> SafetySnapshotV1:
        remaining = self._uncertain_ids(remaining_uncertain_action_ids)
        with self._lock:
            current = self._current_for_update_locked(expected_revision)
            if current.mode is not SafetyMode.RECONCILING:
                raise SafetyAdmissionError("reconciliation_not_active")
            reasons = set(current.reason_codes) - {
                "startup_reconciliation",
                "unknown_inflight_effect",
            }
            if remaining:
                if reconcile_cursor is None:
                    raise ValueError("reconcile_cursor is required while uncertainty remains")
                reasons.update({"startup_reconciliation", "unknown_inflight_effect"})
                mode = SafetyMode.RECONCILING
                cursor = _bounded_text(reconcile_cursor, "reconcile_cursor")
            else:
                cursor = None
                mode, derived = self._derive_mode(
                    current=current,
                    journal=current.journal_health,
                    chain=current.receipt_chain_health,
                    approval=current.approval_authority_health,
                    base_reasons=reasons,
                    ignore_reconciling=True,
                )
                reasons = set(derived)
            return self._replace_locked(
                current,
                mode=mode,
                reason_codes=reasons,
                uncertain_action_ids=remaining,
                reconcile_cursor=cursor,
                last_reconciled_at_utc=_wire_time(_utc(self._now())),
            )

    def enter_recovery_only(
        self,
        *,
        expected_revision: int,
        reason_code: str,
    ) -> SafetySnapshotV1:
        reason = self._reason(reason_code)
        with self._lock:
            current = self._current_for_update_locked(expected_revision)
            reasons = set(current.reason_codes)
            reasons.add(reason)
            return self._replace_locked(
                current,
                mode=SafetyMode.RECOVERY_ONLY,
                reason_codes=reasons,
                reconcile_cursor=None,
            )

    def admit_effect(
        self,
        *,
        effect_class: EffectClass | str,
        action_request_id: str,
        expected_revision: int,
        source_action_request_id: str | None = None,
        authorization_grant_id_hash: str | None = None,
        recovery_permit: RecoverySafetyPermit | None = None,
    ) -> SafetyAdmissionDecision:
        action_id = _bounded_text(action_request_id, "action_request_id")
        effect = effect_class if isinstance(effect_class, EffectClass) else EffectClass(effect_class)
        with self._lock:
            try:
                snapshot = self._load_current_locked()
            except SafetyStateUnavailableError:
                return SafetyAdmissionDecision(
                    allowed=False,
                    reason_code="safety_state_unavailable",
                    safety_revision=0,
                    mode=SafetyMode.SAFE,
                )
            if type(expected_revision) is not int or expected_revision != snapshot.safety_revision:
                return self._decision(snapshot, False, "stale_safety_revision")
            if effect is EffectClass.NONE:
                return self._decision(snapshot, True, "read_only_admitted")
            if (
                snapshot.mode is SafetyMode.NORMAL
                and snapshot.effect_admission is EffectAdmission.OPEN
            ):
                return self._decision(snapshot, True, "effect_admitted")
            if snapshot.mode is SafetyMode.RECOVERY_ONLY:
                allowed = self._consume_exact_recovery_permit(
                    snapshot=snapshot,
                    action_request_id=action_id,
                    source_action_request_id=source_action_request_id,
                    authorization_grant_id_hash=authorization_grant_id_hash,
                    permit=recovery_permit,
                )
                return self._decision(
                    snapshot,
                    allowed,
                    "recovery_effect_admitted"
                    if allowed
                    else "exact_recovery_permit_required",
                )
            return self._decision(snapshot, False, "effect_admission_closed")

    def require_effect_admission(self, **kwargs: Any) -> SafetyAdmissionDecision:
        decision = self.admit_effect(**kwargs)
        if not decision.allowed:
            raise SafetyAdmissionError(decision.reason_code)
        return decision

    def _issue_recovery_permit(
        self,
        *,
        expected_revision: int,
        context: SafetyControlContext,
        action_request_id: str,
        source_action_request_id: str,
        authorization_grant_id_hash: str,
    ) -> RecoverySafetyPermit:
        self._require_context(
            context,
            scope="action.recover",
            exact_source=_RECOVERY_SOURCE,
        )
        action_id = _bounded_text(action_request_id, "action_request_id")
        source_id = _bounded_text(source_action_request_id, "source_action_request_id")
        grant_hash = _sha256(authorization_grant_id_hash, "authorization_grant_id_hash")
        with self._lock:
            snapshot = self._current_for_update_locked(expected_revision)
            if snapshot.mode is not SafetyMode.RECOVERY_ONLY:
                raise SafetyAdmissionError("recovery_only_mode_required")
            nonce = secrets.token_hex(32)
            tag = self._permit_tag(
                action_request_id=action_id,
                source_action_request_id=source_id,
                authorization_grant_id_hash=grant_hash,
                runtime_boot_id=self._runtime_boot_id,
                safety_revision=snapshot.safety_revision,
                nonce=nonce,
            )
            return RecoverySafetyPermit(
                action_request_id=action_id,
                source_action_request_id=source_id,
                authorization_grant_id_hash=grant_hash,
                runtime_boot_id=self._runtime_boot_id,
                safety_revision=snapshot.safety_revision,
                nonce=nonce,
                tag=tag,
            )

    def _consume_exact_recovery_permit(
        self,
        *,
        snapshot: SafetySnapshotV1,
        action_request_id: str,
        source_action_request_id: str | None,
        authorization_grant_id_hash: str | None,
        permit: RecoverySafetyPermit | None,
    ) -> bool:
        if not isinstance(permit, RecoverySafetyPermit):
            return False
        try:
            grant_hash = _sha256(
                authorization_grant_id_hash, "authorization_grant_id_hash"
            )
            source_id = _bounded_text(
                source_action_request_id, "source_action_request_id"
            )
        except ValueError:
            return False
        expected_tag = self._permit_tag(
            action_request_id=permit.action_request_id,
            source_action_request_id=permit.source_action_request_id,
            authorization_grant_id_hash=permit.authorization_grant_id_hash,
            runtime_boot_id=permit.runtime_boot_id,
            safety_revision=permit.safety_revision,
            nonce=permit._nonce,
        )
        with permit._lock:
            if permit._consumed or permit._nonce in self._spent_permit_nonces:
                return False
            exact = (
                hmac.compare_digest(permit._tag, expected_tag)
                and permit.action_request_id == action_request_id
                and permit.source_action_request_id == source_id
                and permit.authorization_grant_id_hash == grant_hash
                and permit.runtime_boot_id == self._runtime_boot_id
                and permit.safety_revision == snapshot.safety_revision
            )
            if exact:
                permit._consumed = True
                if len(self._spent_permit_order) >= 4096:
                    expired_nonce = self._spent_permit_order.popleft()
                    self._spent_permit_nonces.discard(expired_nonce)
                self._spent_permit_order.append(permit._nonce)
                self._spent_permit_nonces.add(permit._nonce)
            return exact

    def _permit_tag(self, **values: Any) -> str:
        payload = "\0".join(str(values[name]) for name in sorted(values)).encode("utf-8")
        return hmac.new(self._permit_key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _decision(
        snapshot: SafetySnapshotV1, allowed: bool, reason_code: str
    ) -> SafetyAdmissionDecision:
        return SafetyAdmissionDecision(
            allowed=allowed,
            reason_code=reason_code,
            safety_revision=snapshot.safety_revision,
            mode=snapshot.mode,
        )

    def _current_for_update_locked(self, expected_revision: int) -> SafetySnapshotV1:
        if type(expected_revision) is not int or expected_revision < 1:
            raise SafetyConflictError()
        current = self._load_current_locked()
        if current.safety_revision != expected_revision:
            raise SafetyConflictError()
        return current

    def _replace_locked(self, current: SafetySnapshotV1, **updates: Any) -> SafetySnapshotV1:
        values: dict[str, Any] = {
            "mode": current.mode,
            "reason_codes": current.reason_codes,
            "fuse_tripped": current.fuse_tripped,
            "fuse_reason_code": current.fuse_reason_code,
            "journal_health": current.journal_health,
            "receipt_chain_health": current.receipt_chain_health,
            "approval_authority_health": current.approval_authority_health,
            "uncertain_action_ids": current.uncertain_action_ids,
            "reconcile_cursor": current.reconcile_cursor,
            "last_reconciled_at_utc": current.last_reconciled_at_utc,
        }
        values.update(updates)
        snapshot = self._build_snapshot(
            safety_revision=current.safety_revision + 1,
            **values,
        )
        self._persist_snapshot_locked(snapshot)
        return snapshot

    def _build_snapshot(self, *, safety_revision: int, **values: Any) -> SafetySnapshotV1:
        mode = values["mode"]
        if not isinstance(mode, SafetyMode):
            mode = SafetyMode(mode)
        admission = {
            SafetyMode.NORMAL: EffectAdmission.OPEN,
            SafetyMode.SAFE: EffectAdmission.CLOSED,
            SafetyMode.RECONCILING: EffectAdmission.CLOSED,
            SafetyMode.RECOVERY_ONLY: EffectAdmission.RECOVERY_ONLY,
            SafetyMode.SHUTDOWN: EffectAdmission.CLOSED,
        }[mode]
        reasons = tuple(sorted(self._reason(reason) for reason in values["reason_codes"]))
        payload: dict[str, Any] = {
            "schema_version": 1,
            "safety_snapshot_id": _bounded_text(
                self._id_factory(), "safety_snapshot_id"
            ),
            "javis_identity_id": self._javis_identity_id,
            "instance_id": self._instance_id,
            "safety_revision": safety_revision,
            "runtime_boot_id": self._runtime_boot_id,
            "mode": mode.value,
            "reason_codes": list(reasons),
            "effect_admission": admission.value,
            "fuse_tripped": values["fuse_tripped"],
            "fuse_reason_code": values["fuse_reason_code"],
            "journal_health": self._enum_value(values["journal_health"], JournalHealth),
            "receipt_chain_health": self._enum_value(
                values["receipt_chain_health"], ReceiptChainHealth
            ),
            "approval_authority_health": self._enum_value(
                values["approval_authority_health"], ApprovalAuthorityHealth
            ),
            "uncertain_action_ids": list(values["uncertain_action_ids"]),
            "reconcile_cursor": values["reconcile_cursor"],
            "last_reconciled_at_utc": values["last_reconciled_at_utc"],
            "permission_revision": self._permission_revision,
            "policy_version": self._policy_version,
            "created_at_utc": _wire_time(_utc(self._now())),
            "content_hash": "0" * 64,
        }
        payload["content_hash"] = canonical_content_hash(payload)
        return SafetySnapshotV1.from_dict(payload)

    def _closed_snapshot(self) -> SafetySnapshotV1:
        return self._build_snapshot(
            safety_revision=1,
            mode=SafetyMode.SAFE,
            reason_codes=("journal_unavailable",),
            fuse_tripped=False,
            fuse_reason_code=None,
            journal_health=JournalHealth.UNAVAILABLE,
            receipt_chain_health=ReceiptChainHealth.UNAVAILABLE,
            approval_authority_health=ApprovalAuthorityHealth.UNAVAILABLE,
            uncertain_action_ids=(),
            reconcile_cursor=None,
            last_reconciled_at_utc=None,
        )

    def _load_current_locked(self) -> SafetySnapshotV1:
        try:
            self._assert_layout_safe_locked(require_existing=True)
            pointer = self._read_json(self._current_pointer_path, _MAX_POINTER_BYTES)
            expected_keys = {
                "schema_version",
                "safety_snapshot_id",
                "safety_revision",
                "snapshot_file",
                "content_hash",
            }
            if set(pointer) != expected_keys or pointer["schema_version"] != _POINTER_SCHEMA_VERSION:
                raise ValueError("invalid current pointer schema")
            snapshot_id = _bounded_text(pointer["safety_snapshot_id"], "safety_snapshot_id")
            revision = pointer["safety_revision"]
            if type(revision) is not int or revision < 1:
                raise ValueError("invalid safety revision")
            content_hash = _sha256(pointer["content_hash"], "content_hash")
            filename = pointer["snapshot_file"]
            if type(filename) is not str:
                raise ValueError("invalid snapshot filename")
            match = _SNAPSHOT_FILE.fullmatch(filename)
            if match is None:
                raise ValueError("invalid snapshot filename")
            if int(match.group(1)) != revision or match.group(2) != content_hash:
                raise ValueError("snapshot pointer binding mismatch")
            snapshot_files = []
            for candidate in self._snapshot_directory.iterdir():
                candidate_match = _SNAPSHOT_FILE.fullmatch(candidate.name)
                if (
                    candidate_match is None
                    or not candidate.is_file()
                    or self._is_reparse(candidate)
                ):
                    raise ValueError("ambiguous safety snapshot directory")
                snapshot_files.append(
                    (int(candidate_match.group(1)), candidate_match.group(2))
                )
            if not snapshot_files:
                raise ValueError("missing safety snapshot")
            revisions = [item[0] for item in snapshot_files]
            if len(revisions) != len(set(revisions)) or max(revisions) != revision:
                raise ValueError("current pointer is not the unique latest revision")
            path = self._snapshot_directory / filename
            if path.parent != self._snapshot_directory or self._is_reparse(path):
                raise ValueError("unsafe snapshot path")
            payload = self._read_json(path, _MAX_SNAPSHOT_BYTES)
            snapshot = SafetySnapshotV1.from_dict(payload)
            if (
                snapshot.safety_snapshot_id != snapshot_id
                or snapshot.safety_revision != revision
                or not hmac.compare_digest(snapshot.content_hash, content_hash)
                or snapshot.javis_identity_id != self._javis_identity_id
                or snapshot.instance_id != self._instance_id
                or snapshot.runtime_boot_id != self._runtime_boot_id
                or snapshot.permission_revision != self._permission_revision
                or snapshot.policy_version != self._policy_version
            ):
                raise ValueError("snapshot current binding mismatch")
            return snapshot
        except SafetyStateUnavailableError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise SafetyStateUnavailableError() from exc

    def _persist_snapshot_locked(self, snapshot: SafetySnapshotV1) -> None:
        self._prepare_directories_locked()
        filename = (
            f"snapshot-{snapshot.safety_revision:020d}-{snapshot.content_hash}.json"
        )
        snapshot_path = self._snapshot_directory / filename
        if snapshot_path.exists():
            raise SafetyConflictError("safety_snapshot_collision")
        snapshot_bytes = canonical_json_bytes(snapshot) + b"\n"
        if len(snapshot_bytes) > _MAX_SNAPSHOT_BYTES:
            raise SafetyStateUnavailableError("safety_snapshot_too_large")
        pointer = {
            "schema_version": _POINTER_SCHEMA_VERSION,
            "safety_snapshot_id": snapshot.safety_snapshot_id,
            "safety_revision": snapshot.safety_revision,
            "snapshot_file": filename,
            "content_hash": snapshot.content_hash,
        }
        pointer_bytes = (
            json.dumps(pointer, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        try:
            self._atomic_write(snapshot_path, snapshot_bytes)
            self._atomic_write(self._current_pointer_path, pointer_bytes)
        except SafetyError:
            raise
        except OSError as exc:
            raise SafetyStateUnavailableError("safety_snapshot_write_failed") from exc

    def _prepare_directories_locked(self) -> None:
        actions_directory = self._data_root / "actions"
        for path in (actions_directory, self._storage_directory, self._snapshot_directory):
            if path.exists() and self._is_reparse(path):
                raise SafetyStateUnavailableError("safety_storage_path_unsafe")
            path.mkdir(exist_ok=True)
        self._assert_layout_safe_locked(require_existing=True)

    def _assert_layout_safe_locked(self, *, require_existing: bool) -> None:
        if require_existing and (
            not self._storage_directory.is_dir() or not self._snapshot_directory.is_dir()
        ):
            raise SafetyStateUnavailableError()
        for path in (
            self._data_root / "actions",
            self._storage_directory,
            self._snapshot_directory,
            self._current_pointer_path,
        ):
            if path.exists() and self._is_reparse(path):
                raise SafetyStateUnavailableError("safety_storage_path_unsafe")
        try:
            self._storage_directory.resolve(strict=require_existing).relative_to(self._data_root)
            self._snapshot_directory.resolve(strict=require_existing).relative_to(self._data_root)
        except (OSError, ValueError) as exc:
            raise SafetyStateUnavailableError("safety_storage_path_unsafe") from exc

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        if path.is_symlink():
            return True
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except OSError:
            return False
        return bool(attributes & 0x400)

    @staticmethod
    def _read_json(path: Path, maximum: int) -> Mapping[str, Any]:
        if not path.is_file() or path.is_symlink() or SafetyController._is_reparse(path):
            raise ValueError("unsafe or missing JSON file")
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("JSON file exceeds size limit")
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("JSON root must be an object")
        return payload

    def _atomic_write(self, path: Path, data: bytes) -> None:
        if path.exists() and SafetyController._is_reparse(path):
            raise SafetyStateUnavailableError("safety_storage_path_unsafe")
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                self._file_sync(stream.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)
            SafetyController._sync_directory(path.parent)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _sync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor: int | None = None
        try:
            descriptor = os.open(directory, os.O_RDONLY)
            os.fsync(descriptor)
        except OSError:
            return
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _require_context(
        self,
        context: SafetyControlContext,
        *,
        scope: str,
        local: bool = False,
        exact_source: ActionSourceKind | None = None,
    ) -> None:
        if not isinstance(context, SafetyControlContext):
            raise SafetyAdmissionError("server_control_context_required")
        self._require_boot(context)
        if not context.has_scope(scope):
            raise SafetyAdmissionError("runtime_scope_required")
        if local and not context.is_local:
            raise SafetyAdmissionError("local_control_required")
        if exact_source is not None and context.source_kind is not exact_source:
            raise SafetyAdmissionError("control_source_denied")
        if exact_source is None and context.source_kind in {
            ActionSourceKind.AGENT,
            ActionSourceKind.SUBAGENT,
            ActionSourceKind.CRON,
        }:
            raise SafetyAdmissionError("control_source_denied")

    def _require_boot(self, context: SafetyControlContext) -> None:
        if context.runtime_boot_id != self._runtime_boot_id:
            raise SafetyAdmissionError("runtime_boot_mismatch")

    @staticmethod
    def _enum_or_current(value: Any, enum_type: type, current: Any) -> Any:
        if value is None:
            return current
        if isinstance(value, enum_type):
            return value
        try:
            return enum_type(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {enum_type.__name__}") from exc

    @staticmethod
    def _enum_value(value: Any, enum_type: type) -> str:
        return SafetyController._enum_or_current(value, enum_type, None).value

    @staticmethod
    def _reason(value: str) -> str:
        reason = _stable_code(value, "reason_code")
        if reason not in SAFETY_REASON_CODES:
            raise ValueError("reason_code is outside the frozen safety allowlist")
        return reason

    @staticmethod
    def _uncertain_ids(values: Iterable[str]) -> tuple[str, ...]:
        if isinstance(values, (str, bytes, bytearray)):
            raise ValueError("uncertain_action_ids must be an iterable")
        result = tuple(_bounded_text(value, "uncertain_action_id") for value in values)
        if len(result) > 128 or len(set(result)) != len(result):
            raise ValueError("uncertain_action_ids must be unique and contain at most 128 IDs")
        return tuple(sorted(result))

    def _derive_mode(
        self,
        *,
        current: SafetySnapshotV1,
        journal: JournalHealth,
        chain: ReceiptChainHealth,
        approval: ApprovalAuthorityHealth,
        base_reasons: Iterable[str] | None = None,
        ignore_reconciling: bool = False,
    ) -> tuple[SafetyMode, tuple[str, ...]]:
        reasons = set(current.reason_codes if base_reasons is None else base_reasons)
        reasons.difference_update(_DYNAMIC_HEALTH_REASONS)
        if journal is not JournalHealth.HEALTHY:
            reasons.add("journal_unavailable")
        if chain is ReceiptChainHealth.GAP:
            reasons.add("receipt_chain_gap")
        elif chain is ReceiptChainHealth.TAMPERED:
            reasons.add("receipt_chain_tampered")
        elif chain is ReceiptChainHealth.UNAVAILABLE:
            reasons.add("receipt_chain_gap")
        if approval is not ApprovalAuthorityHealth.HEALTHY:
            reasons.add("approval_authority_unavailable")
        if current.mode is SafetyMode.SHUTDOWN:
            reasons.add("shutdown_requested")
            mode = SafetyMode.SHUTDOWN
        elif chain is not ReceiptChainHealth.HEALTHY:
            mode = SafetyMode.RECOVERY_ONLY
        elif current.uncertain_action_ids and not ignore_reconciling:
            reasons.update({"startup_reconciliation", "unknown_inflight_effect"})
            mode = SafetyMode.RECONCILING
        elif current.fuse_tripped or journal is not JournalHealth.HEALTHY or approval is not ApprovalAuthorityHealth.HEALTHY:
            mode = SafetyMode.SAFE
        elif reasons:
            mode = SafetyMode.SAFE
        else:
            mode = SafetyMode.NORMAL
        return mode, tuple(sorted(reasons))


__all__ = [
    "LocalSafetyApproval",
    "RecoverySafetyPermit",
    "SafetyAdmissionDecision",
    "SafetyAdmissionError",
    "SafetyConflictError",
    "SafetyControlContext",
    "SafetyControlResult",
    "SafetyController",
    "SafetyError",
    "SafetyExecutorControls",
    "SafetyResetDeniedError",
    "SafetyStateUnavailableError",
]
