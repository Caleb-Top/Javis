from __future__ import annotations

import inspect
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.action.approval import (
    ApprovalAccessDeniedError,
    ApprovalAuthority,
    ApprovalAuthorityError,
    ApprovalConflictError,
    ApprovalExpiredError,
    ApprovalNotResolvableError,
    ApprovalStorageError,
    AuthorizationGrantNotUsableError,
)
from core.action.contracts import (
    ActionRequestV1,
    ApprovalDecision,
    ApprovalResolutionV1,
    ApprovalState,
    GrantState,
    action_parameters_hash,
)
from core.life.memory.contracts import AccessContext


BOOT = "boot-1"
OWNER = "subject-owner"
CLIENT_HASH = "c" * 64


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, 1, 2, 3, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def _wire_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _sequence(prefix: str):
    value = 0

    def create() -> str:
        nonlocal value
        value += 1
        return f"{prefix}-{value}"

    return create


def _secrets():
    value = 0

    def create() -> str:
        nonlocal value
        value += 1
        return f"{value:02d}" + "s" * 41

    return create


def _access(
    clock: _Clock,
    *,
    owner: str = OWNER,
    client_hash: str = CLIENT_HASH,
    boot: str = BOOT,
    session: str = "session-1",
    scopes: tuple[str, ...] = ("action.execute", "action.approve"),
    expires_in_seconds: int = 1200,
) -> AccessContext:
    return AccessContext.from_dict(
        {
            "schema_version": 1,
            "context_id": f"access-{session}",
            "runtime_boot_id": boot,
            "client_id_hash": client_hash,
            "capability_scopes": list(scopes),
            "actor_subject_id": owner,
            "actor_kind": "primary_user",
            "session_id": session,
            "participant_subject_ids": [owner],
            "audience_ceiling": "owner_private",
            "identity_assurance": "desktop_confirmed",
            "purpose": "manage",
            "acl_epoch": 1,
            "issued_at_utc": _wire_time(clock.value - timedelta(minutes=1)),
            "expires_at_utc": _wire_time(
                clock.value + timedelta(seconds=expires_in_seconds)
            ),
        }
    )


def _action(
    clock: _Clock,
    *,
    action_request_id: str = "action-1",
    owner: str = OWNER,
    boot: str = BOOT,
    action_name: str = "workspace.file.write",
    parameters: dict | None = None,
    target_scope: dict | None = None,
    intention_id: str = "intention-1",
    intention_revision: int = 3,
    risk_class: str = "high",
) -> ActionRequestV1:
    normalized_parameters = parameters or {"relative_path": "notes/today.txt"}
    normalized_target = target_scope or {
        "kind": "workspace_file",
        "locator": "notes/today.txt",
    }
    parameters_hash = action_parameters_hash(
        action_name=action_name,
        parameters=normalized_parameters,
        target_scope=normalized_target,
        intention_id=intention_id,
        intention_revision=intention_revision,
    )
    return ActionRequestV1(
        schema_version=1,
        action_request_id=action_request_id,
        javis_identity_id="javis-1",
        instance_id="instance-1",
        intention_id=intention_id,
        intention_revision=intention_revision,
        commitment_id=None,
        owner_subject_id=owner,
        runtime_boot_id=boot,
        source_kind="conversation_ws",
        source_ref="request-1",
        action_name=action_name,
        capability="workspace.write",
        normalized_parameters=normalized_parameters,
        parameters_hash=parameters_hash,
        target_scope=normalized_target,
        effect_class="reversible",
        risk_class=risk_class,
        preconditions=(),
        expected_observations=(),
        idempotency_key=f"idem-{action_request_id}",
        timeout_seconds=30,
        requested_at_utc=_wire_time(clock.value - timedelta(seconds=1)),
        expires_at_utc=_wire_time(clock.value + timedelta(minutes=10)),
        policy_version="policy.v1",
    )


def _authority(
    root: Path,
    clock: _Clock,
    *,
    events: list | None = None,
) -> ApprovalAuthority:
    return ApprovalAuthority(
        data_root=root,
        runtime_boot_id=BOOT,
        now=clock,
        approval_id_factory=_sequence("approval"),
        grant_id_factory=_sequence("grant"),
        secret_factory=_secrets(),
        event_sink=events.append if events is not None else None,
    )


def _pending(
    authority: ApprovalAuthority,
    clock: _Clock,
    *,
    action: ActionRequestV1 | None = None,
    access: AccessContext | None = None,
):
    request = action or _action(clock)
    context = access or _access(clock)
    approval = authority.request_approval(
        request,
        access_context=context,
        target_summary="Workspace note",
        preview={"operation": "write", "target_alias": "today-note"},
        safety_revision=7,
    )
    return request, context, approval


def _resolution(
    approval,
    clock: _Clock,
    *,
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
    approval_id: str | None = None,
    action_request_id: str | None = None,
    parameters_hash: str | None = None,
    boot: str | None = None,
    client_hash: str | None = None,
    idempotency_key: str = "resolution-1",
) -> ApprovalResolutionV1:
    return ApprovalResolutionV1(
        schema_version=1,
        decision=decision,
        approval_id=approval_id or approval.approval_id,
        action_request_id=action_request_id or approval.action_request_id,
        parameters_hash=parameters_hash or approval.parameters_hash,
        runtime_boot_id=boot or approval.runtime_boot_id,
        client_instance_hash=client_hash or approval.client_instance_hash,
        decided_at_utc=_wire_time(clock.value),
        idempotency_key=idempotency_key,
    )


def test_pending_request_is_preview_hashed_bound_and_idempotent(tmp_path: Path) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    action, access, approval = _pending(authority, clock)

    assert approval.state is ApprovalState.PENDING
    assert approval.action_name == action.action_name
    assert approval.parameters_hash == action.parameters_hash
    assert approval.owner_subject_id == access.actor_subject_id
    assert approval.client_instance_hash == access.client_id_hash
    assert approval.runtime_boot_id == BOOT
    assert approval.preview_hash != "0" * 64

    replay = authority.request_approval(
        action,
        access_context=access,
        target_summary="Workspace note",
        preview={"operation": "write", "target_alias": "today-note"},
        safety_revision=7,
    )
    assert replay == approval
    assert authority.list_pending(access_context=access) == (approval,)

    with pytest.raises(ApprovalConflictError):
        authority.request_approval(
            action,
            access_context=access,
            target_summary="Workspace note",
            preview={"operation": "delete", "target_alias": "today-note"},
            safety_revision=7,
        )


def test_approve_issues_one_exact_grant_and_never_replays_bearer(tmp_path: Path) -> None:
    clock = _Clock()
    events: list = []
    authority = _authority(tmp_path, clock, events=events)
    action, access, approval = _pending(authority, clock)
    resolution = _resolution(approval, clock)

    first = authority.resolve(resolution, access_context=access)
    replay = authority.resolve(
        _resolution(approval, clock, idempotency_key="resolution-retry"),
        access_context=access,
    )

    grant = first.authorization_grant
    assert first.decision is ApprovalDecision.APPROVE
    assert first.approval.state is ApprovalState.APPROVED
    assert grant is not None
    assert first.grant_secret is not None
    assert grant.approval_id == approval.approval_id
    assert grant.owner_subject_id == action.owner_subject_id
    assert grant.client_instance_hash == access.client_id_hash
    assert grant.runtime_boot_id == action.runtime_boot_id
    assert grant.intention_id == action.intention_id
    assert grant.intention_revision == action.intention_revision
    assert grant.action_name == action.action_name
    assert grant.parameters_hash == action.parameters_hash
    assert grant.max_uses == 1
    assert grant.uses == 0
    assert grant.state is GrantState.ISSUED
    assert replay.idempotent_replay is True
    assert replay.authorization_grant == grant
    assert replay.grant_secret is None

    with pytest.raises(ApprovalConflictError):
        authority.resolve(
            _resolution(approval, clock, decision=ApprovalDecision.DENY),
            access_context=access,
        )

    with sqlite3.connect(authority.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0] == 1
    assert [event.event_kind for event in events] == [
        "approval.requested",
        "approval.approved",
    ]


def test_deny_is_enum_only_idempotent_and_never_issues_grant(tmp_path: Path) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    _action_request, access, approval = _pending(authority, clock)
    denial = _resolution(approval, clock, decision=ApprovalDecision.DENY)

    first = authority.resolve(denial, access_context=access)
    replay = authority.resolve(denial, access_context=access)

    assert first.decision is ApprovalDecision.DENY
    assert first.approval.state is ApprovalState.DENIED
    assert first.authorization_grant is None
    assert first.grant_secret is None
    assert replay.idempotent_replay is True
    with sqlite3.connect(authority.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM grants").fetchone()[0] == 0
    with pytest.raises(ApprovalConflictError):
        authority.resolve(
            _resolution(approval, clock, decision=ApprovalDecision.APPROVE),
            access_context=access,
        )


def test_concurrent_conflicting_decisions_have_one_pending_cas_winner(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    _action_request, access, approval = _pending(authority, clock)
    results = []
    conflicts = []
    lock = threading.Lock()

    def decide(decision: ApprovalDecision) -> None:
        try:
            result = authority.resolve(
                _resolution(
                    approval,
                    clock,
                    decision=decision,
                    idempotency_key=f"resolution-{decision.value}",
                ),
                access_context=access,
            )
            with lock:
                results.append(result)
        except ApprovalConflictError as exc:
            with lock:
                conflicts.append(exc)

    threads = [
        threading.Thread(target=decide, args=(ApprovalDecision.APPROVE,)),
        threading.Thread(target=decide, args=(ApprovalDecision.DENY,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(conflicts) == 1
    diagnostics = authority.diagnostics()
    terminal_count = (
        diagnostics["approval_counts"]["approved"]
        + diagnostics["approval_counts"]["denied"]
    )
    assert terminal_count == 1
    expected_grants = 1 if results[0].decision is ApprovalDecision.APPROVE else 0
    assert diagnostics["grant_counts"]["issued"] == expected_grants


@pytest.mark.parametrize(
    "case",
    ["id_guess", "action", "hash", "session", "client", "owner", "boot"],
)
def test_resolution_guess_and_cross_binding_share_one_rejection(
    case: str, tmp_path: Path
) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    _action_request, access, approval = _pending(authority, clock)
    kwargs = {}
    candidate_access = access
    if case == "id_guess":
        kwargs["approval_id"] = "approval-guessed"
    elif case == "action":
        kwargs["action_request_id"] = "action-guessed"
    elif case == "hash":
        kwargs["parameters_hash"] = "f" * 64
    elif case == "session":
        candidate_access = _access(clock, session="session-2")
    elif case == "client":
        candidate_access = _access(clock, client_hash="d" * 64)
        kwargs["client_hash"] = "d" * 64
    elif case == "owner":
        candidate_access = _access(clock, owner="subject-other")
    elif case == "boot":
        candidate_access = _access(clock, boot="boot-2")
        kwargs["boot"] = "boot-2"

    with pytest.raises(ApprovalNotResolvableError) as raised:
        authority.resolve(
            _resolution(approval, clock, **kwargs),
            access_context=candidate_access,
        )

    assert raised.value.reason_code == "approval_not_resolvable"
    assert authority.list_pending(access_context=access) == (approval,)


def test_scope_and_access_expiry_fail_before_resolution(tmp_path: Path) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    _action_request, _access_context, approval = _pending(authority, clock)

    with pytest.raises(ApprovalAccessDeniedError, match="runtime_scope_required"):
        authority.resolve(
            _resolution(approval, clock),
            access_context=_access(clock, scopes=("action.execute",)),
        )

    expired_access = _access(clock, expires_in_seconds=1)
    clock.advance(seconds=2)
    with pytest.raises(ApprovalAccessDeniedError, match="access_context_expired"):
        authority.resolve(
            _resolution(approval, clock), access_context=expired_access
        )

    nonlocal_payload = _access(clock).to_dict()
    nonlocal_payload["actor_kind"] = "known_person"
    nonlocal_payload["identity_assurance"] = "verified"
    with pytest.raises(ApprovalAccessDeniedError, match="local_primary_user_required"):
        authority.resolve(
            _resolution(approval, clock),
            access_context=AccessContext.from_dict(nonlocal_payload),
        )


def test_expiry_and_cancel_are_persistent_cas_terminal_states(tmp_path: Path) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    _action_request, access, approval = _pending(authority, clock)
    clock.advance(seconds=301)

    with pytest.raises(ApprovalExpiredError):
        authority.resolve(_resolution(approval, clock), access_context=access)
    assert authority.diagnostics()["approval_counts"]["expired"] == 1

    clock = _Clock()
    authority = _authority(tmp_path / "cancel", clock)
    _action_request, access, approval = _pending(authority, clock)
    cancelled = authority.cancel(
        approval.approval_id,
        action_request_id=approval.action_request_id,
        parameters_hash=approval.parameters_hash,
        access_context=access,
    )
    replay = authority.cancel(
        approval.approval_id,
        action_request_id=approval.action_request_id,
        parameters_hash=approval.parameters_hash,
        access_context=access,
    )
    assert cancelled.state is ApprovalState.CANCELLED
    assert replay == cancelled
    with pytest.raises(ApprovalNotResolvableError):
        authority.resolve(_resolution(approval, clock), access_context=access)


def test_preview_tamper_is_detected_before_resolution(tmp_path: Path) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    _action_request, access, approval = _pending(authority, clock)
    with sqlite3.connect(authority.path) as connection:
        connection.execute(
            "UPDATE approvals SET preview_json = ? WHERE approval_id = ?",
            (json.dumps({"operation": "delete"}), approval.approval_id),
        )

    with pytest.raises(ApprovalStorageError):
        authority.resolve(_resolution(approval, clock), access_context=access)


def test_grant_is_exactly_one_use_under_concurrency(tmp_path: Path) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    action, access, approval = _pending(authority, clock)
    issued = authority.resolve(_resolution(approval, clock), access_context=access)
    grant = issued.authorization_grant
    secret = issued.grant_secret
    assert grant is not None and secret is not None
    successes = []
    failures = []
    lock = threading.Lock()

    def consume() -> None:
        try:
            result = authority.consume_grant(
                grant.grant_id,
                secret,
                action_request=action,
                access_context=access,
                safety_revision=7,
            )
            with lock:
                successes.append(result)
        except AuthorizationGrantNotUsableError as exc:
            with lock:
                failures.append(exc)

    threads = [threading.Thread(target=consume) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 1
    assert successes[0].state is GrantState.CONSUMED
    assert successes[0].uses == 1
    assert len(failures) == 7
    with sqlite3.connect(authority.path) as connection:
        assert connection.execute(
            "SELECT state, uses FROM grants WHERE grant_id = ?", (grant.grant_id,)
        ).fetchone() == ("consumed", 1)


@pytest.mark.parametrize(
    "case",
    [
        "id_guess",
        "secret",
        "session",
        "client",
        "owner",
        "boot",
        "action",
        "parameters",
        "target",
        "intention",
        "revision",
        "safety",
    ],
)
def test_grant_rejects_every_cross_binding_before_exact_use(
    case: str, tmp_path: Path
) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    action, access, approval = _pending(authority, clock)
    issued = authority.resolve(_resolution(approval, clock), access_context=access)
    grant = issued.authorization_grant
    secret = issued.grant_secret
    assert grant is not None and secret is not None
    grant_id = grant.grant_id
    candidate_secret = secret
    candidate_action = action
    candidate_access = access
    safety_revision = 7

    if case == "id_guess":
        grant_id = "grant-guessed"
    elif case == "secret":
        candidate_secret = "x" * 43
    elif case == "session":
        candidate_access = _access(clock, session="session-2")
    elif case == "client":
        candidate_access = _access(clock, client_hash="d" * 64)
    elif case == "owner":
        candidate_action = _action(clock, owner="subject-other")
        candidate_access = _access(clock, owner="subject-other")
    elif case == "boot":
        candidate_action = _action(clock, boot="boot-2")
        candidate_access = _access(clock, boot="boot-2")
    elif case == "action":
        candidate_action = _action(clock, action_name="workspace.file.delete")
    elif case == "parameters":
        candidate_action = _action(
            clock, parameters={"relative_path": "notes/other.txt"}
        )
    elif case == "target":
        candidate_action = _action(
            clock,
            target_scope={"kind": "workspace_file", "locator": "notes/other.txt"},
        )
    elif case == "intention":
        candidate_action = _action(clock, intention_id="intention-2")
    elif case == "revision":
        candidate_action = _action(clock, intention_revision=4)
    elif case == "safety":
        safety_revision = 8

    with pytest.raises(AuthorizationGrantNotUsableError):
        authority.consume_grant(
            grant_id,
            candidate_secret,
            action_request=candidate_action,
            access_context=candidate_access,
            safety_revision=safety_revision,
        )

    consumed = authority.consume_grant(
        grant.grant_id,
        secret,
        action_request=action,
        access_context=access,
        safety_revision=7,
    )
    assert consumed.state is GrantState.CONSUMED


def test_grant_expiry_and_boot_client_revocation_are_fail_closed(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    authority = _authority(tmp_path / "expiry", clock)
    action, access, approval = _pending(authority, clock)
    issued = authority.resolve(_resolution(approval, clock), access_context=access)
    assert issued.authorization_grant is not None and issued.grant_secret is not None
    clock.advance(seconds=61)
    with pytest.raises(
        AuthorizationGrantNotUsableError, match="authorization_grant_expired"
    ):
        authority.consume_grant(
            issued.authorization_grant.grant_id,
            issued.grant_secret,
            action_request=action,
            access_context=access,
            safety_revision=7,
        )

    clock = _Clock()
    authority = _authority(tmp_path / "client", clock)
    _action_one, access, pending_one = _pending(authority, clock)
    issued_one = authority.resolve(
        _resolution(pending_one, clock), access_context=access
    )
    action_two = _action(clock, action_request_id="action-2")
    _action_two, _access_two, pending_two = _pending(
        authority, clock, action=action_two, access=access
    )
    revoked = authority.revoke_client(CLIENT_HASH)
    assert revoked.pending_cancelled == 1
    assert revoked.grants_revoked == 1
    assert issued_one.authorization_grant is not None
    with pytest.raises(AuthorizationGrantNotUsableError):
        authority.consume_grant(
            issued_one.authorization_grant.grant_id,
            issued_one.grant_secret or "",
            action_request=_action_one,
            access_context=access,
            safety_revision=7,
        )
    with pytest.raises(ApprovalNotResolvableError):
        authority.resolve(
            _resolution(pending_two, clock), access_context=access
        )

    clock = _Clock()
    authority = _authority(tmp_path / "boot", clock)
    action_one, access, pending_one = _pending(authority, clock)
    issued_one = authority.resolve(
        _resolution(pending_one, clock), access_context=access
    )
    action_two = _action(clock, action_request_id="action-2")
    _pending(authority, clock, action=action_two, access=access)
    boot_result = authority.revoke_boot(BOOT)
    assert boot_result.pending_cancelled == 1
    assert boot_result.grants_revoked == 1
    assert issued_one.authorization_grant is not None
    with pytest.raises(AuthorizationGrantNotUsableError):
        authority.consume_grant(
            issued_one.authorization_grant.grant_id,
            issued_one.grant_secret or "",
            action_request=action_one,
            access_context=access,
            safety_revision=7,
        )


def test_grant_secret_is_absent_from_repr_diagnostics_db_and_events(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    events: list = []
    authority = _authority(tmp_path, clock, events=events)
    action, access, approval = _pending(authority, clock)
    result = authority.resolve(_resolution(approval, clock), access_context=access)
    grant = result.authorization_grant
    secret = result.grant_secret
    assert grant is not None and secret is not None

    diagnostic_text = json.dumps(authority.diagnostics(), sort_keys=True)
    event_text = json.dumps(
        [event.to_dict() for event in events] + [result.to_event_dict()],
        sort_keys=True,
    )
    repr_text = repr((authority, result, grant, events))
    public_response = result.to_dict()

    assert public_response["grant_secret"] == secret
    assert "grant_secret_digest" not in json.dumps(public_response)
    assert secret not in diagnostic_text
    assert secret not in event_text
    assert secret not in repr_text
    assert grant.grant_secret_digest not in event_text
    assert "grant_secret" not in event_text

    with sqlite3.connect(authority.path) as connection:
        grant_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(grants)")
        }
        stored_digest = connection.execute(
            "SELECT grant_secret_digest FROM grants WHERE grant_id = ?",
            (grant.grant_id,),
        ).fetchone()[0]
    assert "grant_secret" not in grant_columns
    assert "grant_secret_digest" in grant_columns
    assert stored_digest == grant.grant_secret_digest
    for candidate in authority.path.parent.glob(f"{authority.path.name}*"):
        assert secret.encode("ascii") not in candidate.read_bytes()

    authority.consume_grant(
        grant.grant_id,
        secret,
        action_request=action,
        access_context=access,
        safety_revision=7,
    )
    with pytest.raises(AuthorizationGrantNotUsableError) as raised:
        authority.consume_grant(
            grant.grant_id,
            secret,
            action_request=action,
            access_context=access,
            safety_revision=7,
        )
    assert secret not in repr(raised.value)
    assert secret not in str(raised.value)


def test_public_authority_api_has_no_boolean_approval_parameter(tmp_path: Path) -> None:
    clock = _Clock()
    authority = _authority(tmp_path, clock)
    for name, method in inspect.getmembers(authority, predicate=callable):
        if name.startswith("_"):
            continue
        signature = inspect.signature(method)
        assert "approved" not in signature.parameters
        assert "confirmed" not in signature.parameters

    _action_request, access, approval = _pending(authority, clock)
    result = authority.resolve(
        _resolution(approval, clock, decision=ApprovalDecision.DENY),
        access_context=access,
    )
    assert "approved" not in result.to_dict()
    with pytest.raises(ValueError, match="unexpected field"):
        ApprovalResolutionV1.from_dict(
            {**_resolution(approval, clock).to_dict(), "approved": True}
        )
