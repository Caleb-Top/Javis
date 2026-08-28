"""Single-writer approval authority for governed Javis actions.

The authority owns the durable approval state machine and exact one-use
authorization grants.  Bearer values are returned once, kept out of object
representations/events/diagnostics, and persisted only as SHA-256 digests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.action.contracts import (
    ActionRequestV1,
    ApprovalDecision,
    ApprovalRequestV1,
    ApprovalResolutionV1,
    ApprovalState,
    AuthorizationGrantV1,
    GrantIssuerKind,
    GrantState,
    RiskClass,
    canonical_json_bytes,
    canonical_preview_hash,
    canonical_target_scope_hash,
)
from core.life.memory.contracts import AccessContext, ActorKind, IdentityAssurance


DATABASE_RELATIVE_PATH = Path("actions") / "actions.sqlite3"
_HASH = re.compile(r"[0-9a-f]{64}")
_SECRET = re.compile(r"[A-Za-z0-9_-]{43,512}")
_CODE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_APPROVAL_STATES = tuple(state.value for state in ApprovalState)
_GRANT_STATES = tuple(state.value for state in GrantState)
_LOCAL_APPROVAL_SCOPE = "action.approve"
_EXECUTION_SCOPE = "action.execute"
_CANCELLATION_SCOPES = frozenset({_LOCAL_APPROVAL_SCOPE, "conversation.cancel"})


class ApprovalAuthorityError(RuntimeError):
    """Base class for stable, secret-free authority failures."""

    reason_code = "approval_authority_error"

    def __init__(self, reason_code: str | None = None) -> None:
        self.reason_code = reason_code or self.reason_code
        super().__init__(self.reason_code)


class ApprovalAccessDeniedError(ApprovalAuthorityError):
    reason_code = "approval_access_denied"


class ApprovalNotResolvableError(ApprovalAuthorityError):
    reason_code = "approval_not_resolvable"


class ApprovalConflictError(ApprovalAuthorityError):
    reason_code = "approval_decision_conflict"


class ApprovalExpiredError(ApprovalAuthorityError):
    reason_code = "approval_expired"


class AuthorizationGrantNotUsableError(ApprovalAuthorityError):
    reason_code = "authorization_grant_not_usable"


class ApprovalStorageError(ApprovalAuthorityError):
    reason_code = "approval_storage_unavailable"


@dataclass(frozen=True, slots=True)
class ApprovalAuthorityEvent:
    """Deliberately narrow event projection; never includes bearer material."""

    event_kind: str
    approval_id: str | None
    action_request_id: str | None
    state: str
    recorded_at_utc: str
    grant_ref_hash_prefix: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "event_kind": self.event_kind,
            "approval_id": self.approval_id,
            "action_request_id": self.action_request_id,
            "state": self.state,
            "recorded_at_utc": self.recorded_at_utc,
            "grant_ref_hash_prefix": self.grant_ref_hash_prefix,
        }


@dataclass(frozen=True, slots=True)
class ApprovalResolutionResult:
    """Resolution response.  ``grant_secret`` is populated only once."""

    decision: ApprovalDecision
    approval: ApprovalRequestV1 = field(repr=False)
    authorization_grant: AuthorizationGrantV1 | None = field(default=None, repr=False)
    grant_secret: str | None = field(default=None, repr=False)
    idempotent_replay: bool = False

    @property
    def grant(self) -> AuthorizationGrantV1 | None:
        return self.authorization_grant

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "decision": self.decision.value,
            "approval_id": self.approval.approval_id,
            "action_request_id": self.approval.action_request_id,
            "approval_state": self.approval.state.value,
            "idempotent_replay": self.idempotent_replay,
            "authorization_grant": None,
            "grant_secret": self.grant_secret,
        }
        if self.authorization_grant is not None:
            payload["authorization_grant"] = _public_grant_projection(
                self.authorization_grant
            )
        return payload

    def to_event_dict(self) -> dict[str, object]:
        grant = self.authorization_grant
        return {
            "decision": self.decision.value,
            "approval_id": self.approval.approval_id,
            "action_request_id": self.approval.action_request_id,
            "approval_state": self.approval.state.value,
            "grant_id": grant.grant_id if grant is not None else None,
            "grant_state": grant.state.value if grant is not None else None,
            "idempotent_replay": self.idempotent_replay,
        }


@dataclass(frozen=True, slots=True)
class ApprovalRevocationResult:
    pending_cancelled: int
    grants_revoked: int


def _public_grant_projection(grant: AuthorizationGrantV1) -> dict[str, object]:
    payload = grant.to_dict()
    payload.pop("grant_secret_digest", None)
    return payload


def _bounded_text(value: Any, field_name: str, *, maximum: int = 256) -> str:
    if type(value) is not str:
        raise ValueError(f"{field_name} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    size = len(normalized.encode("utf-8"))
    if not normalized or size > maximum:
        raise ValueError(f"{field_name} must be a bounded non-empty string")
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise ValueError(f"{field_name} must not contain control characters")
    return normalized


def _sha256(value: Any, field_name: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256 hex")
    return value


def _stable_code(value: Any, field_name: str) -> str:
    normalized = _bounded_text(value, field_name, maximum=128)
    if _CODE.fullmatch(normalized) is None:
        raise ValueError(f"{field_name} must be a stable lowercase code")
    return normalized


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


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _same(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class ApprovalAuthority:
    """The only durable writer for approvals and approval-issued grants."""

    def __init__(
        self,
        data_root: str | os.PathLike[str] | None = None,
        *,
        runtime_boot_id: str,
        database_path: str | os.PathLike[str] | None = None,
        now: Callable[[], datetime] | None = None,
        approval_id_factory: Callable[[], str] | None = None,
        grant_id_factory: Callable[[], str] | None = None,
        secret_factory: Callable[[], str] | None = None,
        event_sink: Callable[[ApprovalAuthorityEvent], None] | None = None,
        approval_ttl_seconds: int = 300,
        grant_ttl_seconds: int = 300,
        sqlite_timeout_seconds: float = 5.0,
    ) -> None:
        if (data_root is None) == (database_path is None):
            raise ValueError("provide exactly one of data_root or database_path")
        if database_path is None:
            root = Path(data_root).expanduser()  # type: ignore[arg-type]
            if not root.is_absolute():
                root = root.absolute()
            path = root.resolve(strict=False) / DATABASE_RELATIVE_PATH
        else:
            path = Path(database_path).expanduser()
            if not path.is_absolute():
                path = path.absolute()
            path = path.resolve(strict=False)
        self._path = path
        self._runtime_boot_id = _bounded_text(runtime_boot_id, "runtime_boot_id")
        if type(approval_ttl_seconds) is not int or not 1 <= approval_ttl_seconds <= 300:
            raise ValueError("approval_ttl_seconds must be in [1, 300]")
        if type(grant_ttl_seconds) is not int or not 1 <= grant_ttl_seconds <= 300:
            raise ValueError("grant_ttl_seconds must be in [1, 300]")
        if not isinstance(sqlite_timeout_seconds, (int, float)) or not 0 < sqlite_timeout_seconds <= 60:
            raise ValueError("sqlite_timeout_seconds must be in (0, 60]")
        self._now = now or (lambda: datetime.now(UTC))
        self._approval_id_factory = approval_id_factory or (
            lambda: f"approval-{secrets.token_hex(16)}"
        )
        self._grant_id_factory = grant_id_factory or (
            lambda: f"grant-{secrets.token_hex(16)}"
        )
        self._secret_factory = secret_factory or (lambda: secrets.token_urlsafe(32))
        self._event_sink = event_sink
        self._approval_ttl_seconds = approval_ttl_seconds
        self._grant_ttl_seconds = grant_ttl_seconds
        self._sqlite_timeout_seconds = float(sqlite_timeout_seconds)
        self._schema_lock = threading.Lock()
        self._initialize_schema()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def database_path(self) -> Path:
        return self._path

    @property
    def runtime_boot_id(self) -> str:
        return self._runtime_boot_id

    def __repr__(self) -> str:
        return f"ApprovalAuthority(runtime_boot_id={self._runtime_boot_id!r})"

    def request_approval(
        self,
        action_request: ActionRequestV1,
        *,
        access_context: AccessContext,
        target_summary: str,
        preview: Mapping[str, Any],
        safety_revision: int,
        ttl_seconds: int | None = None,
    ) -> ApprovalRequestV1:
        """Create one immutable pending approval for an exact ActionRequest."""

        if not isinstance(action_request, ActionRequestV1):
            raise TypeError("action_request must be ActionRequestV1")
        now = self._clock()
        self._require_access(
            access_context,
            now=now,
            required_scopes=frozenset({_EXECUTION_SCOPE}),
            owner_subject_id=action_request.owner_subject_id,
            client_instance_hash=None,
            runtime_boot_id=action_request.runtime_boot_id,
        )
        if action_request.runtime_boot_id != self._runtime_boot_id:
            raise ApprovalAccessDeniedError()
        if not (_parse_time(action_request.requested_at_utc) <= now < _parse_time(action_request.expires_at_utc)):
            raise ApprovalAccessDeniedError("action_request_not_current")
        if type(safety_revision) is not int or safety_revision < 1:
            raise ValueError("safety_revision must be a positive integer")
        ttl = self._approval_ttl_seconds if ttl_seconds is None else ttl_seconds
        if type(ttl) is not int or not 1 <= ttl <= self._approval_ttl_seconds:
            raise ValueError(
                f"ttl_seconds must be in [1, {self._approval_ttl_seconds}]"
            )
        requested_at = _wire_time(now)
        expires = min(
            now + timedelta(seconds=ttl),
            _parse_time(action_request.expires_at_utc),
        )
        expires_at = _wire_time(expires)
        if expires_at <= requested_at:
            raise ApprovalExpiredError("approval_window_unavailable")
        preview_hash = canonical_preview_hash(preview)
        target_scope_hash = canonical_target_scope_hash(action_request.target_scope)
        preview_json = canonical_json_bytes(preview).decode("utf-8")
        session_id_digest = _digest_text(access_context.session_id)

        for _attempt in range(16):
            approval_id = _bounded_text(
                self._approval_id_factory(), "approval_id"
            )
            approval = ApprovalRequestV1(
                schema_version=1,
                approval_id=approval_id,
                action_request_id=action_request.action_request_id,
                action_name=action_request.action_name,
                parameters_hash=action_request.parameters_hash,
                target_summary=target_summary,
                risk_class=action_request.risk_class,
                reversibility=action_request.effect_class,
                preview=preview,
                preview_hash=preview_hash,
                owner_subject_id=action_request.owner_subject_id,
                client_instance_hash=access_context.client_id_hash,
                runtime_boot_id=action_request.runtime_boot_id,
                requested_at_utc=requested_at,
                expires_at_utc=expires_at,
                state=ApprovalState.PENDING,
            )
            try:
                existing = self._insert_approval(
                    approval=approval,
                    action_request=action_request,
                    preview_json=preview_json,
                    target_scope_hash=target_scope_hash,
                    session_id_digest=session_id_digest,
                    safety_revision=safety_revision,
                )
            except sqlite3.IntegrityError as exc:
                if self._approval_id_exists(approval_id):
                    continue
                raise ApprovalStorageError() from exc
            if existing is not None:
                return existing
            self._emit(
                event_kind="approval.requested",
                approval_id=approval.approval_id,
                action_request_id=approval.action_request_id,
                state=approval.state.value,
                recorded_at=now,
            )
            return approval
        raise ApprovalStorageError("approval_id_factory_exhausted")

    def resolve(
        self,
        resolution: ApprovalResolutionV1,
        *,
        access_context: AccessContext,
    ) -> ApprovalResolutionResult:
        """CAS a pending approval using the structured decision enum."""

        if not isinstance(resolution, ApprovalResolutionV1):
            raise TypeError("resolution must be ApprovalResolutionV1")
        now = self._clock()
        try:
            self._require_access(
                access_context,
                now=now,
                required_scopes=frozenset({_LOCAL_APPROVAL_SCOPE}),
                owner_subject_id=None,
                client_instance_hash=resolution.client_instance_hash,
                runtime_boot_id=resolution.runtime_boot_id,
            )
        except ApprovalAccessDeniedError as exc:
            if exc.reason_code in {"runtime_boot_mismatch", "client_binding_mismatch"}:
                raise ApprovalNotResolvableError() from None
            raise
        if resolution.runtime_boot_id != self._runtime_boot_id:
            raise ApprovalNotResolvableError()

        connection = self._connect()
        event: tuple[str, str, str, str, str | None] | None = None
        expired = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?",
                (resolution.approval_id,),
            ).fetchone()
            if row is None or not self._resolution_matches(
                row, resolution, access_context
            ):
                raise ApprovalNotResolvableError()
            approval = self._approval_from_row(row)
            state = approval.state
            if state is ApprovalState.PENDING and now >= _parse_time(approval.expires_at_utc):
                changed = connection.execute(
                    "UPDATE approvals SET state = ? WHERE approval_id = ? AND state = ?",
                    (
                        ApprovalState.EXPIRED.value,
                        approval.approval_id,
                        ApprovalState.PENDING.value,
                    ),
                ).rowcount
                if changed != 1:
                    raise ApprovalConflictError("approval_cas_lost")
                connection.commit()
                expired = True
                event = (
                    "approval.expired",
                    approval.approval_id,
                    approval.action_request_id,
                    ApprovalState.EXPIRED.value,
                    None,
                )
            elif state is ApprovalState.PENDING:
                decided = _parse_time(resolution.decided_at_utc)
                if not _parse_time(approval.requested_at_utc) <= decided < _parse_time(
                    approval.expires_at_utc
                ):
                    raise ApprovalNotResolvableError()
                result, event = self._resolve_pending(
                    connection=connection,
                    row=row,
                    approval=approval,
                    resolution=resolution,
                    now=now,
                )
                connection.commit()
            else:
                result = self._replay_resolution(
                    connection=connection,
                    row=row,
                    approval=approval,
                    resolution=resolution,
                )
                connection.commit()
        except ApprovalAuthorityError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
            if connection.in_transaction:
                connection.rollback()
            raise ApprovalStorageError() from exc
        finally:
            connection.close()
        if event is not None:
            self._emit_tuple(event, now)
        if expired:
            raise ApprovalExpiredError()
        return result

    def resolve_approval(
        self,
        resolution: ApprovalResolutionV1,
        *,
        access_context: AccessContext,
    ) -> ApprovalResolutionResult:
        return self.resolve(resolution, access_context=access_context)

    def cancel(
        self,
        approval_id: str,
        *,
        action_request_id: str,
        parameters_hash: str,
        access_context: AccessContext,
    ) -> ApprovalRequestV1:
        """CAS an exactly bound pending approval to cancelled."""

        approval_ref = _bounded_text(approval_id, "approval_id")
        action_ref = _bounded_text(action_request_id, "action_request_id")
        parameter_ref = _sha256(parameters_hash, "parameters_hash")
        now = self._clock()
        try:
            self._require_access(
                access_context,
                now=now,
                required_scopes=_CANCELLATION_SCOPES,
                owner_subject_id=None,
                client_instance_hash=access_context.client_id_hash,
                runtime_boot_id=access_context.runtime_boot_id,
                any_scope=True,
            )
        except ApprovalAccessDeniedError as exc:
            if exc.reason_code == "runtime_boot_mismatch":
                raise ApprovalNotResolvableError() from None
            raise
        if access_context.runtime_boot_id != self._runtime_boot_id:
            raise ApprovalNotResolvableError()

        connection = self._connect()
        event: tuple[str, str, str, str, str | None] | None = None
        expired = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_ref,)
            ).fetchone()
            if row is None or not self._cancellation_matches(
                row,
                action_request_id=action_ref,
                parameters_hash=parameter_ref,
                access_context=access_context,
            ):
                raise ApprovalNotResolvableError()
            approval = self._approval_from_row(row)
            if approval.state is ApprovalState.CANCELLED:
                connection.commit()
                return approval
            if approval.state is not ApprovalState.PENDING:
                raise ApprovalConflictError("approval_not_pending")
            next_state = (
                ApprovalState.EXPIRED
                if now >= _parse_time(approval.expires_at_utc)
                else ApprovalState.CANCELLED
            )
            changed = connection.execute(
                "UPDATE approvals SET state = ? WHERE approval_id = ? AND state = ?",
                (next_state.value, approval_ref, ApprovalState.PENDING.value),
            ).rowcount
            if changed != 1:
                raise ApprovalConflictError("approval_cas_lost")
            updated = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (approval_ref,)
            ).fetchone()
            if updated is None:
                raise ApprovalStorageError()
            result = self._approval_from_row(updated)
            connection.commit()
            expired = next_state is ApprovalState.EXPIRED
            event = (
                "approval.expired" if expired else "approval.cancelled",
                result.approval_id,
                result.action_request_id,
                result.state.value,
                None,
            )
        except ApprovalAuthorityError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
            if connection.in_transaction:
                connection.rollback()
            raise ApprovalStorageError() from exc
        finally:
            connection.close()
        if event is not None:
            self._emit_tuple(event, now)
        if expired:
            raise ApprovalExpiredError()
        return result

    def consume_grant(
        self,
        grant_id: str,
        grant_secret: str,
        *,
        action_request: ActionRequestV1,
        access_context: AccessContext,
        safety_revision: int,
    ) -> AuthorizationGrantV1:
        """Atomically consume one exact grant with a ``uses = 0 -> 1`` CAS."""

        grant_ref = _bounded_text(grant_id, "grant_id")
        if type(grant_secret) is not str or _SECRET.fullmatch(grant_secret) is None:
            raise AuthorizationGrantNotUsableError()
        if not isinstance(action_request, ActionRequestV1):
            raise TypeError("action_request must be ActionRequestV1")
        if type(safety_revision) is not int or safety_revision < 1:
            raise ValueError("safety_revision must be a positive integer")
        now = self._clock()
        try:
            self._require_access(
                access_context,
                now=now,
                required_scopes=frozenset({_EXECUTION_SCOPE}),
                owner_subject_id=action_request.owner_subject_id,
                client_instance_hash=None,
                runtime_boot_id=action_request.runtime_boot_id,
            )
        except ApprovalAccessDeniedError as exc:
            if exc.reason_code in {"runtime_boot_mismatch", "owner_binding_mismatch"}:
                raise AuthorizationGrantNotUsableError() from None
            raise
        presented_digest = _digest_text(grant_secret)
        connection = self._connect()
        event: tuple[str, str, str, str, str | None] | None = None
        expired = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM grants WHERE grant_id = ?", (grant_ref,)
            ).fetchone()
            if row is None or not self._grant_matches(
                row=row,
                presented_digest=presented_digest,
                action_request=action_request,
                access_context=access_context,
                safety_revision=safety_revision,
            ):
                raise AuthorizationGrantNotUsableError()
            grant = self._grant_from_row(row)
            if grant.state is not GrantState.ISSUED or grant.uses != 0:
                raise AuthorizationGrantNotUsableError()
            if now >= _parse_time(grant.expires_at_utc):
                connection.execute(
                    "UPDATE grants SET state = ?, revoked_at_utc = ?, revocation_reason = ? "
                    "WHERE grant_id = ? AND state = ? AND uses = 0",
                    (
                        GrantState.EXPIRED.value,
                        _wire_time(now),
                        "ttl_expired",
                        grant_ref,
                        GrantState.ISSUED.value,
                    ),
                )
                connection.commit()
                expired = True
            else:
                changed = connection.execute(
                    "UPDATE grants SET uses = 1, state = ?, consumed_at_utc = ? "
                    "WHERE grant_id = ? AND state = ? AND uses = 0",
                    (
                        GrantState.CONSUMED.value,
                        _wire_time(now),
                        grant_ref,
                        GrantState.ISSUED.value,
                    ),
                ).rowcount
                if changed != 1:
                    raise AuthorizationGrantNotUsableError()
                updated = connection.execute(
                    "SELECT * FROM grants WHERE grant_id = ?", (grant_ref,)
                ).fetchone()
                if updated is None:
                    raise ApprovalStorageError()
                result = self._grant_from_row(updated)
                connection.commit()
                event = (
                    "grant.consumed",
                    result.approval_id or "",
                    action_request.action_request_id,
                    result.state.value,
                    _digest_text(result.grant_id)[:16],
                )
        except ApprovalAuthorityError:
            if connection.in_transaction:
                connection.rollback()
            raise
        except (sqlite3.Error, ValueError, TypeError) as exc:
            if connection.in_transaction:
                connection.rollback()
            raise ApprovalStorageError() from exc
        finally:
            connection.close()
        if expired:
            raise AuthorizationGrantNotUsableError("authorization_grant_expired")
        if event is not None:
            self._emit_tuple(event, now)
        return result

    def expire_pending(self) -> int:
        """Expire all elapsed pending approvals and issued grants."""

        now = self._clock()
        wire_now = _wire_time(now)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                approval_count = connection.execute(
                    "UPDATE approvals SET state = ? WHERE state = ? AND expires_at_utc <= ?",
                    (
                        ApprovalState.EXPIRED.value,
                        ApprovalState.PENDING.value,
                        wire_now,
                    ),
                ).rowcount
                connection.execute(
                    "UPDATE grants SET state = ?, revoked_at_utc = ?, revocation_reason = ? "
                    "WHERE state = ? AND uses = 0 AND expires_at_utc <= ?",
                    (
                        GrantState.EXPIRED.value,
                        wire_now,
                        "ttl_expired",
                        GrantState.ISSUED.value,
                        wire_now,
                    ),
                )
                return approval_count
        except sqlite3.Error as exc:
            raise ApprovalStorageError() from exc

    def revoke_boot(self, runtime_boot_id: str) -> ApprovalRevocationResult:
        return self._revoke("runtime_boot_id", _bounded_text(runtime_boot_id, "runtime_boot_id"))

    def revoke_client(self, client_instance_hash: str) -> ApprovalRevocationResult:
        return self._revoke(
            "client_instance_hash",
            _sha256(client_instance_hash, "client_instance_hash"),
        )

    def list_pending(
        self, *, access_context: AccessContext
    ) -> tuple[ApprovalRequestV1, ...]:
        """Return only pending approvals bound to this exact access session."""

        now = self._clock()
        self._require_access(
            access_context,
            now=now,
            required_scopes=frozenset({_LOCAL_APPROVAL_SCOPE}),
            owner_subject_id=None,
            client_instance_hash=access_context.client_id_hash,
            runtime_boot_id=access_context.runtime_boot_id,
        )
        session_digest = _digest_text(access_context.session_id)
        try:
            connection = self._connect()
            rows = connection.execute(
                "SELECT * FROM approvals WHERE state = ? AND owner_subject_id = ? "
                "AND client_instance_hash = ? AND runtime_boot_id = ? "
                "AND session_id_digest = ? AND expires_at_utc > ? ORDER BY requested_at_utc",
                (
                    ApprovalState.PENDING.value,
                    access_context.actor_subject_id,
                    access_context.client_id_hash,
                    self._runtime_boot_id,
                    session_digest,
                    _wire_time(now),
                ),
            ).fetchall()
            return tuple(self._approval_from_row(row) for row in rows)
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ApprovalStorageError() from exc
        finally:
            if "connection" in locals():
                connection.close()

    def diagnostics(self) -> dict[str, object]:
        """Return bounded health/count data with no IDs, hashes, or bearer values."""

        try:
            connection = self._connect()
            approval_counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM approvals GROUP BY state"
                )
            }
            grant_counts = {
                str(row["state"]): int(row["count"])
                for row in connection.execute(
                    "SELECT state, COUNT(*) AS count FROM grants GROUP BY state"
                )
            }
            return {
                "health": "healthy",
                "approval_counts": {
                    state: approval_counts.get(state, 0) for state in _APPROVAL_STATES
                },
                "grant_counts": {
                    state: grant_counts.get(state, 0) for state in _GRANT_STATES
                },
            }
        except sqlite3.Error as exc:
            raise ApprovalStorageError() from exc
        finally:
            if "connection" in locals():
                connection.close()

    def _insert_approval(
        self,
        *,
        approval: ApprovalRequestV1,
        action_request: ActionRequestV1,
        preview_json: str,
        target_scope_hash: str,
        session_id_digest: str,
        safety_revision: int,
    ) -> ApprovalRequestV1 | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM approvals WHERE action_request_id = ?",
                (approval.action_request_id,),
            ).fetchone()
            if existing is not None:
                if not self._same_approval_request(
                    existing,
                    approval=approval,
                    action_request=action_request,
                    preview_json=preview_json,
                    target_scope_hash=target_scope_hash,
                    session_id_digest=session_id_digest,
                    safety_revision=safety_revision,
                ):
                    raise ApprovalConflictError("action_request_approval_conflict")
                result = self._approval_from_row(existing)
                connection.commit()
                return result
            connection.execute(
                """
                INSERT INTO approvals (
                    approval_id, action_request_id, javis_identity_id, instance_id,
                    intention_id, intention_revision, action_name, capability,
                    parameters_hash, target_scope_hash, target_summary, risk_class,
                    reversibility, preview_json, preview_hash, owner_subject_id,
                    client_instance_hash, session_id_digest, runtime_boot_id,
                    policy_version, safety_revision, requested_at_utc, expires_at_utc,
                    state, decision, decided_at_utc, resolution_idempotency_digest,
                    grant_id
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, NULL, NULL, NULL, NULL
                )
                """,
                (
                    approval.approval_id,
                    approval.action_request_id,
                    action_request.javis_identity_id,
                    action_request.instance_id,
                    action_request.intention_id,
                    action_request.intention_revision,
                    approval.action_name,
                    action_request.capability,
                    approval.parameters_hash,
                    target_scope_hash,
                    approval.target_summary,
                    approval.risk_class.value,
                    approval.reversibility.value,
                    preview_json,
                    approval.preview_hash,
                    approval.owner_subject_id,
                    approval.client_instance_hash,
                    session_id_digest,
                    approval.runtime_boot_id,
                    action_request.policy_version,
                    safety_revision,
                    approval.requested_at_utc,
                    approval.expires_at_utc,
                    approval.state.value,
                ),
            )
            connection.commit()
            return None
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _resolve_pending(
        self,
        *,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        approval: ApprovalRequestV1,
        resolution: ApprovalResolutionV1,
        now: datetime,
    ) -> tuple[
        ApprovalResolutionResult,
        tuple[str, str, str, str, str | None],
    ]:
        terminal_state = (
            ApprovalState.APPROVED
            if resolution.decision is ApprovalDecision.APPROVE
            else ApprovalState.DENIED
        )
        grant: AuthorizationGrantV1 | None = None
        secret: str | None = None
        if resolution.decision is ApprovalDecision.APPROVE:
            grant, secret = self._build_unique_grant(
                connection=connection,
                row=row,
                approval=approval,
                now=now,
            )
        changed = connection.execute(
            "UPDATE approvals SET state = ?, decision = ?, decided_at_utc = ?, "
            "resolution_idempotency_digest = ?, grant_id = ? "
            "WHERE approval_id = ? AND state = ?",
            (
                terminal_state.value,
                resolution.decision.value,
                _wire_time(now),
                _digest_text(resolution.idempotency_key),
                grant.grant_id if grant is not None else None,
                approval.approval_id,
                ApprovalState.PENDING.value,
            ),
        ).rowcount
        if changed != 1:
            raise ApprovalConflictError("approval_cas_lost")
        if grant is not None:
            self._insert_grant(connection, grant, row["session_id_digest"])
        updated_row = connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval.approval_id,)
        ).fetchone()
        if updated_row is None:
            raise ApprovalStorageError()
        updated = self._approval_from_row(updated_row)
        result = ApprovalResolutionResult(
            decision=resolution.decision,
            approval=updated,
            authorization_grant=grant,
            grant_secret=secret,
            idempotent_replay=False,
        )
        event = (
            f"approval.{terminal_state.value}",
            approval.approval_id,
            approval.action_request_id,
            terminal_state.value,
            _digest_text(grant.grant_id)[:16] if grant is not None else None,
        )
        return result, event

    def _replay_resolution(
        self,
        *,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        approval: ApprovalRequestV1,
        resolution: ApprovalResolutionV1,
    ) -> ApprovalResolutionResult:
        expected = {
            ApprovalDecision.APPROVE: ApprovalState.APPROVED,
            ApprovalDecision.DENY: ApprovalState.DENIED,
        }[resolution.decision]
        if approval.state is not expected:
            if approval.state is ApprovalState.EXPIRED:
                raise ApprovalExpiredError()
            if approval.state is ApprovalState.CANCELLED:
                raise ApprovalNotResolvableError()
            raise ApprovalConflictError()
        grant: AuthorizationGrantV1 | None = None
        if resolution.decision is ApprovalDecision.APPROVE:
            grant_id = row["grant_id"]
            grant_row = connection.execute(
                "SELECT * FROM grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
            if grant_row is None:
                raise ApprovalStorageError("approval_grant_missing")
            grant = self._grant_from_row(grant_row)
        return ApprovalResolutionResult(
            decision=resolution.decision,
            approval=approval,
            authorization_grant=grant,
            grant_secret=None,
            idempotent_replay=True,
        )

    def _build_unique_grant(
        self,
        *,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        approval: ApprovalRequestV1,
        now: datetime,
    ) -> tuple[AuthorizationGrantV1, str]:
        maximum_ttl = 60 if approval.risk_class in (RiskClass.HIGH, RiskClass.CRITICAL) else 300
        expires = min(
            now + timedelta(seconds=min(self._grant_ttl_seconds, maximum_ttl)),
            _parse_time(approval.expires_at_utc),
        )
        issued_at = _wire_time(now)
        expires_at = _wire_time(expires)
        if expires_at <= issued_at:
            raise ApprovalExpiredError("grant_window_unavailable")
        for _attempt in range(32):
            grant_id = _bounded_text(self._grant_id_factory(), "grant_id")
            secret = self._secret_factory()
            if type(secret) is not str or _SECRET.fullmatch(secret) is None:
                raise ApprovalStorageError("grant_secret_factory_invalid")
            digest = _digest_text(secret)
            collision = connection.execute(
                "SELECT 1 FROM grants WHERE grant_id = ? OR grant_secret_digest = ?",
                (grant_id, digest),
            ).fetchone()
            if collision is not None:
                continue
            grant = AuthorizationGrantV1(
                schema_version=1,
                grant_id=grant_id,
                grant_secret_digest=digest,
                approval_id=approval.approval_id,
                policy_decision_id=approval.approval_id,
                owner_subject_id=approval.owner_subject_id,
                client_instance_hash=approval.client_instance_hash,
                runtime_boot_id=approval.runtime_boot_id,
                intention_id=str(row["intention_id"]),
                intention_revision=int(row["intention_revision"]),
                action_name=approval.action_name,
                parameters_hash=approval.parameters_hash,
                capability=str(row["capability"]),
                target_scope_hash=str(row["target_scope_hash"]),
                risk_class=approval.risk_class,
                issued_at_utc=issued_at,
                expires_at_utc=expires_at,
                max_uses=1,
                uses=0,
                issuer_kind=GrantIssuerKind.LOCAL_USER_APPROVAL,
                policy_version=str(row["policy_version"]),
                safety_revision=int(row["safety_revision"]),
                state=GrantState.ISSUED,
            )
            return grant, secret
        raise ApprovalStorageError("grant_factory_exhausted")

    @staticmethod
    def _insert_grant(
        connection: sqlite3.Connection,
        grant: AuthorizationGrantV1,
        session_id_digest: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO grants (
                grant_id, grant_secret_digest, approval_id, policy_decision_id,
                owner_subject_id, client_instance_hash, session_id_digest,
                runtime_boot_id, intention_id, intention_revision, action_name,
                parameters_hash, capability, target_scope_hash, risk_class,
                issued_at_utc, expires_at_utc, max_uses, uses, issuer_kind,
                policy_version, safety_revision, state, consumed_at_utc,
                revoked_at_utc, revocation_reason
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                NULL, NULL, NULL
            )
            """,
            (
                grant.grant_id,
                grant.grant_secret_digest,
                grant.approval_id,
                grant.policy_decision_id,
                grant.owner_subject_id,
                grant.client_instance_hash,
                session_id_digest,
                grant.runtime_boot_id,
                grant.intention_id,
                grant.intention_revision,
                grant.action_name,
                grant.parameters_hash,
                grant.capability,
                grant.target_scope_hash,
                grant.risk_class.value,
                grant.issued_at_utc,
                grant.expires_at_utc,
                grant.max_uses,
                grant.uses,
                grant.issuer_kind.value,
                grant.policy_version,
                grant.safety_revision,
                grant.state.value,
            ),
        )

    def _revoke(self, column: str, value: str) -> ApprovalRevocationResult:
        if column not in {"runtime_boot_id", "client_instance_hash"}:
            raise ValueError("invalid revocation binding")
        now = self._clock()
        wire_now = _wire_time(now)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                pending = connection.execute(
                    f"UPDATE approvals SET state = ? WHERE {column} = ? AND state = ?",
                    (
                        ApprovalState.CANCELLED.value,
                        value,
                        ApprovalState.PENDING.value,
                    ),
                ).rowcount
                grants = connection.execute(
                    f"UPDATE grants SET state = ?, revoked_at_utc = ?, revocation_reason = ? "
                    f"WHERE {column} = ? AND state = ? AND uses = 0",
                    (
                        GrantState.REVOKED.value,
                        wire_now,
                        f"{column}_revoked",
                        value,
                        GrantState.ISSUED.value,
                    ),
                ).rowcount
            return ApprovalRevocationResult(
                pending_cancelled=pending,
                grants_revoked=grants,
            )
        except sqlite3.Error as exc:
            raise ApprovalStorageError() from exc

    def _same_approval_request(
        self,
        row: sqlite3.Row,
        *,
        approval: ApprovalRequestV1,
        action_request: ActionRequestV1,
        preview_json: str,
        target_scope_hash: str,
        session_id_digest: str,
        safety_revision: int,
    ) -> bool:
        exact = {
            "javis_identity_id": action_request.javis_identity_id,
            "instance_id": action_request.instance_id,
            "intention_id": action_request.intention_id,
            "action_name": approval.action_name,
            "capability": action_request.capability,
            "parameters_hash": approval.parameters_hash,
            "target_scope_hash": target_scope_hash,
            "target_summary": approval.target_summary,
            "risk_class": approval.risk_class.value,
            "reversibility": approval.reversibility.value,
            "preview_json": preview_json,
            "preview_hash": approval.preview_hash,
            "owner_subject_id": approval.owner_subject_id,
            "client_instance_hash": approval.client_instance_hash,
            "session_id_digest": session_id_digest,
            "runtime_boot_id": approval.runtime_boot_id,
            "policy_version": action_request.policy_version,
        }
        return (
            all(_same(str(row[key]), value) for key, value in exact.items())
            and int(row["intention_revision"]) == action_request.intention_revision
            and int(row["safety_revision"]) == safety_revision
        )

    def _resolution_matches(
        self,
        row: sqlite3.Row,
        resolution: ApprovalResolutionV1,
        access_context: AccessContext,
    ) -> bool:
        return all(
            (
                _same(str(row["action_request_id"]), resolution.action_request_id),
                _same(str(row["parameters_hash"]), resolution.parameters_hash),
                _same(str(row["runtime_boot_id"]), resolution.runtime_boot_id),
                _same(str(row["client_instance_hash"]), resolution.client_instance_hash),
                _same(str(row["owner_subject_id"]), access_context.actor_subject_id),
                _same(str(row["session_id_digest"]), _digest_text(access_context.session_id)),
            )
        )

    def _cancellation_matches(
        self,
        row: sqlite3.Row,
        *,
        action_request_id: str,
        parameters_hash: str,
        access_context: AccessContext,
    ) -> bool:
        return all(
            (
                _same(str(row["action_request_id"]), action_request_id),
                _same(str(row["parameters_hash"]), parameters_hash),
                _same(str(row["runtime_boot_id"]), access_context.runtime_boot_id),
                _same(str(row["client_instance_hash"]), access_context.client_id_hash),
                _same(str(row["owner_subject_id"]), access_context.actor_subject_id),
                _same(str(row["session_id_digest"]), _digest_text(access_context.session_id)),
            )
        )

    def _grant_matches(
        self,
        *,
        row: sqlite3.Row,
        presented_digest: str,
        action_request: ActionRequestV1,
        access_context: AccessContext,
        safety_revision: int,
    ) -> bool:
        return all(
            (
                _same(str(row["grant_secret_digest"]), presented_digest),
                _same(str(row["owner_subject_id"]), action_request.owner_subject_id),
                _same(str(row["owner_subject_id"]), access_context.actor_subject_id),
                _same(str(row["client_instance_hash"]), access_context.client_id_hash),
                _same(str(row["session_id_digest"]), _digest_text(access_context.session_id)),
                _same(str(row["runtime_boot_id"]), action_request.runtime_boot_id),
                _same(str(row["runtime_boot_id"]), self._runtime_boot_id),
                _same(str(row["intention_id"]), action_request.intention_id),
                _same(str(row["action_name"]), action_request.action_name),
                _same(str(row["parameters_hash"]), action_request.parameters_hash),
                _same(str(row["capability"]), action_request.capability),
                _same(
                    str(row["target_scope_hash"]),
                    canonical_target_scope_hash(action_request.target_scope),
                ),
                _same(str(row["risk_class"]), action_request.risk_class.value),
                _same(str(row["policy_version"]), action_request.policy_version),
            )
        ) and (
            int(row["intention_revision"]) == action_request.intention_revision
            and int(row["safety_revision"]) == safety_revision
        )

    def _approval_from_row(self, row: sqlite3.Row) -> ApprovalRequestV1:
        preview = json.loads(str(row["preview_json"]))
        if not isinstance(preview, Mapping):
            raise ValueError("stored preview must be an object")
        if not _same(canonical_preview_hash(preview), str(row["preview_hash"])):
            raise ValueError("stored preview hash mismatch")
        return ApprovalRequestV1(
            schema_version=1,
            approval_id=str(row["approval_id"]),
            action_request_id=str(row["action_request_id"]),
            action_name=str(row["action_name"]),
            parameters_hash=str(row["parameters_hash"]),
            target_summary=str(row["target_summary"]),
            risk_class=str(row["risk_class"]),
            reversibility=str(row["reversibility"]),
            preview=preview,
            preview_hash=str(row["preview_hash"]),
            owner_subject_id=str(row["owner_subject_id"]),
            client_instance_hash=str(row["client_instance_hash"]),
            runtime_boot_id=str(row["runtime_boot_id"]),
            requested_at_utc=str(row["requested_at_utc"]),
            expires_at_utc=str(row["expires_at_utc"]),
            state=str(row["state"]),
        )

    @staticmethod
    def _grant_from_row(row: sqlite3.Row) -> AuthorizationGrantV1:
        return AuthorizationGrantV1(
            schema_version=1,
            grant_id=str(row["grant_id"]),
            grant_secret_digest=str(row["grant_secret_digest"]),
            approval_id=str(row["approval_id"]) if row["approval_id"] is not None else None,
            policy_decision_id=str(row["policy_decision_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            client_instance_hash=str(row["client_instance_hash"]),
            runtime_boot_id=str(row["runtime_boot_id"]),
            intention_id=str(row["intention_id"]),
            intention_revision=int(row["intention_revision"]),
            action_name=str(row["action_name"]),
            parameters_hash=str(row["parameters_hash"]),
            capability=str(row["capability"]),
            target_scope_hash=str(row["target_scope_hash"]),
            risk_class=str(row["risk_class"]),
            issued_at_utc=str(row["issued_at_utc"]),
            expires_at_utc=str(row["expires_at_utc"]),
            max_uses=int(row["max_uses"]),
            uses=int(row["uses"]),
            issuer_kind=str(row["issuer_kind"]),
            policy_version=str(row["policy_version"]),
            safety_revision=int(row["safety_revision"]),
            state=str(row["state"]),
        )

    def _require_access(
        self,
        context: AccessContext,
        *,
        now: datetime,
        required_scopes: frozenset[str],
        owner_subject_id: str | None,
        client_instance_hash: str | None,
        runtime_boot_id: str,
        any_scope: bool = False,
    ) -> None:
        if not isinstance(context, AccessContext):
            raise ApprovalAccessDeniedError("server_access_context_required")
        if (
            context.actor_kind is not ActorKind.PRIMARY_USER
            or context.identity_assurance is not IdentityAssurance.DESKTOP_CONFIRMED
        ):
            raise ApprovalAccessDeniedError("local_primary_user_required")
        if context.runtime_boot_id != runtime_boot_id or runtime_boot_id != self._runtime_boot_id:
            raise ApprovalAccessDeniedError("runtime_boot_mismatch")
        scopes = set(context.capability_scopes)
        scope_ok = bool(scopes & required_scopes) if any_scope else required_scopes <= scopes
        if not scope_ok:
            raise ApprovalAccessDeniedError("runtime_scope_required")
        if not (_parse_time(context.issued_at_utc) <= now < _parse_time(context.expires_at_utc)):
            raise ApprovalAccessDeniedError("access_context_expired")
        if owner_subject_id is not None and context.actor_subject_id != owner_subject_id:
            raise ApprovalAccessDeniedError("owner_binding_mismatch")
        if client_instance_hash is not None and context.client_id_hash != client_instance_hash:
            raise ApprovalAccessDeniedError("client_binding_mismatch")

    def _clock(self) -> datetime:
        return _utc(self._now())

    def _approval_id_exists(self, approval_id: str) -> bool:
        try:
            connection = self._connect()
            return connection.execute(
                "SELECT 1 FROM approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone() is not None
        except sqlite3.Error as exc:
            raise ApprovalStorageError() from exc
        finally:
            if "connection" in locals():
                connection.close()

    def _emit_tuple(
        self,
        values: tuple[str, str, str, str, str | None],
        recorded_at: datetime,
    ) -> None:
        kind, approval_id, action_request_id, state, grant_prefix = values
        self._emit(
            event_kind=kind,
            approval_id=approval_id or None,
            action_request_id=action_request_id or None,
            state=state,
            recorded_at=recorded_at,
            grant_ref_hash_prefix=grant_prefix,
        )

    def _emit(
        self,
        *,
        event_kind: str,
        approval_id: str | None,
        action_request_id: str | None,
        state: str,
        recorded_at: datetime,
        grant_ref_hash_prefix: str | None = None,
    ) -> None:
        sink = self._event_sink
        if sink is None:
            return
        event = ApprovalAuthorityEvent(
            event_kind=_stable_code(event_kind, "event_kind"),
            approval_id=approval_id,
            action_request_id=action_request_id,
            state=_stable_code(state, "state"),
            recorded_at_utc=_wire_time(recorded_at),
            grant_ref_hash_prefix=grant_ref_hash_prefix,
        )
        try:
            sink(event)
        except Exception:
            return

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path,
            timeout=self._sqlite_timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize_schema(self) -> None:
        with self._schema_lock:
            try:
                self._prepare_storage_path()
                connection = sqlite3.connect(
                    self._path,
                    timeout=self._sqlite_timeout_seconds,
                    isolation_level=None,
                )
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("PRAGMA foreign_keys = ON")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS approvals (
                        approval_id TEXT PRIMARY KEY,
                        action_request_id TEXT NOT NULL UNIQUE,
                        javis_identity_id TEXT NOT NULL,
                        instance_id TEXT NOT NULL,
                        intention_id TEXT NOT NULL,
                        intention_revision INTEGER NOT NULL CHECK (intention_revision >= 1),
                        action_name TEXT NOT NULL,
                        capability TEXT NOT NULL,
                        parameters_hash TEXT NOT NULL,
                        target_scope_hash TEXT NOT NULL,
                        target_summary TEXT NOT NULL,
                        risk_class TEXT NOT NULL CHECK (risk_class IN ('low','medium','high','critical')),
                        reversibility TEXT NOT NULL CHECK (reversibility IN ('none','reversible','compensatable','irreversible')),
                        preview_json TEXT NOT NULL,
                        preview_hash TEXT NOT NULL,
                        owner_subject_id TEXT NOT NULL,
                        client_instance_hash TEXT NOT NULL,
                        session_id_digest TEXT NOT NULL,
                        runtime_boot_id TEXT NOT NULL,
                        policy_version TEXT NOT NULL,
                        safety_revision INTEGER NOT NULL CHECK (safety_revision >= 1),
                        requested_at_utc TEXT NOT NULL,
                        expires_at_utc TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (state IN ('pending','approved','denied','expired','cancelled')),
                        decision TEXT CHECK (decision IS NULL OR decision IN ('approve','deny')),
                        decided_at_utc TEXT,
                        resolution_idempotency_digest TEXT,
                        grant_id TEXT,
                        CHECK (
                            (state IN ('pending','expired','cancelled') AND decision IS NULL)
                            OR (state = 'approved' AND decision = 'approve')
                            OR (state = 'denied' AND decision = 'deny')
                        )
                    );
                    CREATE INDEX IF NOT EXISTS idx_approvals_binding_state
                        ON approvals(runtime_boot_id, client_instance_hash, owner_subject_id, state);
                    CREATE INDEX IF NOT EXISTS idx_approvals_expiry
                        ON approvals(state, expires_at_utc);

                    CREATE TABLE IF NOT EXISTS grants (
                        grant_id TEXT PRIMARY KEY,
                        grant_secret_digest TEXT NOT NULL UNIQUE,
                        approval_id TEXT UNIQUE REFERENCES approvals(approval_id) ON DELETE RESTRICT,
                        policy_decision_id TEXT NOT NULL,
                        owner_subject_id TEXT NOT NULL,
                        client_instance_hash TEXT NOT NULL,
                        session_id_digest TEXT NOT NULL,
                        runtime_boot_id TEXT NOT NULL,
                        intention_id TEXT NOT NULL,
                        intention_revision INTEGER NOT NULL CHECK (intention_revision >= 1),
                        action_name TEXT NOT NULL,
                        parameters_hash TEXT NOT NULL,
                        capability TEXT NOT NULL,
                        target_scope_hash TEXT NOT NULL,
                        risk_class TEXT NOT NULL CHECK (risk_class IN ('low','medium','high','critical')),
                        issued_at_utc TEXT NOT NULL,
                        expires_at_utc TEXT NOT NULL,
                        max_uses INTEGER NOT NULL CHECK (max_uses = 1),
                        uses INTEGER NOT NULL CHECK (uses IN (0, 1)),
                        issuer_kind TEXT NOT NULL CHECK (issuer_kind IN ('policy','local_user_approval','recovery_approval')),
                        policy_version TEXT NOT NULL,
                        safety_revision INTEGER NOT NULL CHECK (safety_revision >= 1),
                        state TEXT NOT NULL CHECK (state IN ('issued','consumed','expired','revoked')),
                        consumed_at_utc TEXT,
                        revoked_at_utc TEXT,
                        revocation_reason TEXT,
                        CHECK ((state = 'consumed' AND uses = 1) OR (state != 'consumed' AND uses = 0))
                    );
                    CREATE INDEX IF NOT EXISTS idx_grants_binding_state
                        ON grants(runtime_boot_id, client_instance_hash, owner_subject_id, state);
                    CREATE INDEX IF NOT EXISTS idx_grants_expiry
                        ON grants(state, expires_at_utc);
                    """
                )
            except (OSError, sqlite3.Error) as exc:
                raise ApprovalStorageError() from exc
            finally:
                if "connection" in locals():
                    connection.close()

    def _prepare_storage_path(self) -> None:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists() and self._is_reparse(self._path):
            raise ApprovalStorageError("approval_storage_path_unsafe")
        if self._is_reparse(parent):
            raise ApprovalStorageError("approval_storage_path_unsafe")

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        if path.is_symlink():
            return True
        try:
            attributes = getattr(path.lstat(), "st_file_attributes", 0)
        except OSError:
            return False
        return bool(attributes & 0x400)


__all__ = [
    "ApprovalAccessDeniedError",
    "ApprovalAuthority",
    "ApprovalAuthorityError",
    "ApprovalAuthorityEvent",
    "ApprovalConflictError",
    "ApprovalExpiredError",
    "ApprovalNotResolvableError",
    "ApprovalResolutionResult",
    "ApprovalRevocationResult",
    "ApprovalStorageError",
    "AuthorizationGrantNotUsableError",
    "DATABASE_RELATIVE_PATH",
]
