from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.environment.contracts import SourceKind
from core.environment.grants import (
    EnvironmentGrantStore,
    EnvironmentGrantStoreError,
    GrantDuration,
    ModelTarget,
    ModelVisibility,
)
from core.tool_registry import ToolRegistry


NOW = 1_800_000_000.0
OWNER = "subject-primary"
SESSION = "session-1"
BOOT = "boot-1"


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"environment-grant-{self.value}"


@pytest.fixture
def clock():
    return [NOW]


@pytest.fixture
def store(tmp_path: Path, clock):
    return EnvironmentGrantStore(
        tmp_path,
        runtime_boot_id=BOOT,
        now=lambda: clock[0],
        grant_id_factory=Ids(),
    )


def issue(store: EnvironmentGrantStore, **overrides):
    values = {
        "owner_subject_id": OWNER,
        "source_kind": SourceKind.FOREGROUND_APP,
        "scope": "desktop-main",
        "purpose": "current_context",
        "duration": GrantDuration.SESSION,
        "ttl_seconds": 300,
        "session_id": SESSION,
        "foreground_only": False,
        "model_visibility": ModelVisibility.NONE,
        "media_egress_allowed": False,
    }
    values.update(overrides)
    return store.issue(**values)


def authorize(store: EnvironmentGrantStore, grant_id: str, **overrides):
    values = {
        "owner_subject_id": OWNER,
        "source_kind": SourceKind.FOREGROUND_APP,
        "scope": "desktop-main",
        "purpose": "current_context",
        "session_id": SESSION,
        "is_foreground": True,
    }
    values.update(overrides)
    return store.authorize(grant_id, **values)


def test_grant_is_frozen_strict_and_safe_projection_hides_raw_ids_and_scope(store):
    grant = issue(store)

    with pytest.raises(FrozenInstanceError):
        grant.scope = "other"

    projection = grant.safe_projection(active=True)
    encoded = json.dumps(projection, sort_keys=True)
    assert projection["grant_id_hash"] == grant.id_hash
    assert grant.grant_id not in encoded
    assert grant.owner_subject_id not in encoded
    assert grant.scope not in encoded
    assert "token" not in encoded.casefold()
    assert "nonce" not in encoded.casefold()


@pytest.mark.parametrize(
    "overrides",
    [
        {"purpose": "Not Canonical"},
        {"scope": "bad\nvalue"},
        {"ttl_seconds": 0},
        {"duration": GrantDuration.SESSION, "session_id": None},
        {"duration": GrantDuration.PERSISTENT, "session_id": SESSION},
        {
            "model_visibility": ModelVisibility.LOCAL_ONLY,
            "media_egress_allowed": True,
        },
    ],
)
def test_issue_rejects_unbounded_or_inconsistent_authority(store, overrides):
    with pytest.raises(ValueError):
        issue(store, **overrides)


def test_once_grant_check_does_not_consume_but_authorize_consumes_exactly_once(store):
    grant = issue(store, duration=GrantDuration.ONCE)

    assert store.check(
        grant.grant_id,
        owner_subject_id=OWNER,
        source_kind=SourceKind.FOREGROUND_APP,
        scope="desktop-main",
        purpose="current_context",
        session_id=SESSION,
        is_foreground=True,
    ).allowed
    first = authorize(store, grant.grant_id)
    second = authorize(store, grant.grant_id)

    assert first.allowed and first.consumed
    assert second.allowed is False
    assert second.reason_code == "grant_not_found"


def test_once_grant_has_single_winner_under_concurrent_consumption(store):
    grant = issue(store, duration=GrantDuration.ONCE)

    with ThreadPoolExecutor(max_workers=16) as pool:
        decisions = list(
            pool.map(lambda _: authorize(store, grant.grant_id), range(32))
        )

    assert sum(decision.allowed for decision in decisions) == 1
    assert sum(decision.consumed for decision in decisions) == 1


def test_session_close_and_boot_rotation_remove_only_volatile_grants(store):
    session = issue(store)
    once = issue(store, duration=GrantDuration.ONCE)
    persistent = issue(
        store,
        duration=GrantDuration.PERSISTENT,
        session_id=None,
        purpose="device_health",
    )

    assert store.close_session(SESSION) == 2
    assert store.get(session.grant_id) is None
    assert store.get(once.grant_id) is None
    assert store.get(persistent.grant_id) == persistent

    new_session = issue(store, session_id="session-2")
    assert store.rotate_boot("boot-2") == 1
    assert store.get(new_session.grant_id) is None
    decision = authorize(
        store,
        persistent.grant_id,
        purpose="device_health",
        session_id=None,
    )
    assert decision.reason_code == "persistent_reactivation_required"


def test_persistent_grant_is_atomic_reloads_inactive_and_revocation_survives(
    tmp_path: Path, clock
):
    ids = Ids()
    first = EnvironmentGrantStore(
        tmp_path,
        runtime_boot_id=BOOT,
        now=lambda: clock[0],
        grant_id_factory=ids,
    )
    grant = issue(
        first,
        duration=GrantDuration.PERSISTENT,
        session_id=None,
        model_visibility=ModelVisibility.LOCAL_ONLY,
    )
    persisted = json.loads(first.storage_path.read_text(encoding="utf-8"))
    assert persisted["revision"] == 1
    assert len(persisted["grants"]) == 1
    assert not tuple(first.storage_path.parent.glob("*.tmp"))

    reopened = EnvironmentGrantStore(
        tmp_path,
        runtime_boot_id="boot-2",
        now=lambda: clock[0],
        grant_id_factory=ids,
    )
    inactive = authorize(
        reopened,
        grant.grant_id,
        session_id=None,
        model_target=ModelTarget.LOCAL,
    )
    assert inactive.reason_code == "persistent_reactivation_required"
    assert reopened.activate_persistent(grant.grant_id, owner_subject_id=OWNER)
    assert authorize(
        reopened,
        grant.grant_id,
        session_id=None,
        model_target=ModelTarget.LOCAL,
    ).allowed

    assert reopened.revoke(grant.grant_id, owner_subject_id=OWNER)
    after_revoke = json.loads(reopened.storage_path.read_text(encoding="utf-8"))
    assert after_revoke["revision"] == 2
    final = EnvironmentGrantStore(
        tmp_path,
        runtime_boot_id="boot-3",
        now=lambda: clock[0],
    )
    assert final.activate_persistent(grant.grant_id, owner_subject_id=OWNER) is False
    assert authorize(
        final, grant.grant_id, session_id=None
    ).reason_code == "grant_revoked"


def test_persistent_write_failure_rolls_back_memory_and_cleans_temporary_file(
    store, monkeypatch
):
    original_replace = os.replace

    def fail_replace(source, destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(EnvironmentGrantStoreError, match="grant_store_write_failed"):
        issue(
            store,
            duration=GrantDuration.PERSISTENT,
            session_id=None,
        )
    monkeypatch.setattr(os, "replace", original_replace)

    assert store.list_for_owner(OWNER) == ()
    environment_root = store.storage_path.parent
    assert not environment_root.exists() or not tuple(environment_root.glob("*.tmp"))


def test_foreground_only_is_enforced_without_consuming_once_grant(store):
    grant = issue(
        store,
        duration=GrantDuration.ONCE,
        foreground_only=True,
    )

    background = authorize(store, grant.grant_id, is_foreground=False)
    foreground = authorize(store, grant.grant_id, is_foreground=True)

    assert background.reason_code == "foreground_required"
    assert foreground.allowed and foreground.consumed


@pytest.mark.parametrize(
    ("visibility", "target", "allowed"),
    [
        (ModelVisibility.NONE, ModelTarget.NONE, True),
        (ModelVisibility.NONE, ModelTarget.LOCAL, False),
        (ModelVisibility.NONE, ModelTarget.GOVERNED_CLOUD, False),
        (ModelVisibility.LOCAL_ONLY, ModelTarget.LOCAL, True),
        (ModelVisibility.LOCAL_ONLY, ModelTarget.GOVERNED_CLOUD, False),
        (ModelVisibility.GOVERNED_CLOUD, ModelTarget.LOCAL, True),
        (ModelVisibility.GOVERNED_CLOUD, ModelTarget.GOVERNED_CLOUD, True),
    ],
)
def test_model_visibility_matrix(store, visibility, target, allowed):
    grant = issue(store, model_visibility=visibility)

    decision = authorize(store, grant.grant_id, model_target=target)

    assert decision.allowed is allowed
    if not allowed:
        assert decision.reason_code == "model_visibility_denied"


def test_media_egress_is_explicit_and_not_inferred_from_cloud_visibility(store):
    text_only = issue(
        store,
        model_visibility=ModelVisibility.GOVERNED_CLOUD,
    )
    media = issue(
        store,
        source_kind=SourceKind.RAW_MEDIA,
        scope="screen-capture",
        purpose="visual_assistance",
        model_visibility=ModelVisibility.GOVERNED_CLOUD,
        media_egress_allowed=True,
    )

    denied = authorize(
        store,
        text_only.grant_id,
        model_target=ModelTarget.GOVERNED_CLOUD,
        media_egress=True,
    )
    allowed = authorize(
        store,
        media.grant_id,
        source_kind=SourceKind.RAW_MEDIA,
        scope="screen-capture",
        purpose="visual_assistance",
        model_target=ModelTarget.GOVERNED_CLOUD,
        media_egress=True,
    )

    assert denied.reason_code == "media_egress_denied"
    assert allowed.allowed and allowed.media_egress_allowed

    wrong_target = authorize(
        store,
        media.grant_id,
        source_kind=SourceKind.RAW_MEDIA,
        scope="screen-capture",
        purpose="visual_assistance",
        model_target=ModelTarget.LOCAL,
        media_egress=True,
    )
    assert wrong_target.reason_code == "media_egress_target_required"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"owner_subject_id": "subject-other"}, "owner_mismatch"),
        ({"source_kind": SourceKind.DEVICE_HEALTH}, "source_mismatch"),
        ({"scope": "other"}, "scope_mismatch"),
        ({"purpose": "other_purpose"}, "purpose_mismatch"),
        ({"session_id": "session-other"}, "session_mismatch"),
    ],
)
def test_authorization_matches_every_server_owned_dimension(store, overrides, reason):
    grant = issue(store)

    decision = authorize(store, grant.grant_id, **overrides)

    assert decision.allowed is False
    assert decision.reason_code == reason


def test_expiry_and_revocation_fail_closed_without_reviving_authority(store, clock):
    expired = issue(store, ttl_seconds=1)
    revoked = issue(store)
    assert store.revoke(revoked.grant_id, owner_subject_id=OWNER)
    clock[0] += 2

    assert authorize(store, expired.grant_id).reason_code == "grant_expired"
    assert authorize(store, revoked.grant_id).reason_code == "grant_not_found"


def test_invalid_persistent_file_fails_closed(tmp_path: Path):
    path = tmp_path / "environment" / "grants.v1.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"schema_version":1,"revision":0,"grants":[],"extra":1}')

    with pytest.raises(EnvironmentGrantStoreError, match="grant_store_invalid"):
        EnvironmentGrantStore(tmp_path, runtime_boot_id=BOOT)


def test_environment_storage_symlink_cannot_escape_data_root(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("creating a Windows symlink requires optional host privileges")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "environment").symlink_to(outside, target_is_directory=True)
    store = EnvironmentGrantStore(
        tmp_path,
        runtime_boot_id=BOOT,
        now=lambda: NOW,
        grant_id_factory=Ids(),
    )

    with pytest.raises(EnvironmentGrantStoreError, match="grant_store_path_unsafe"):
        issue(
            store,
            duration=GrantDuration.PERSISTENT,
            session_id=None,
        )

    assert not (outside / "grants.v1.json").exists()


def test_grant_mutation_is_not_exposed_as_a_model_tool():
    registry = ToolRegistry()
    forbidden = {
        "environment_grant_create",
        "environment_grant_issue",
        "environment_grant_extend",
        "environment_grant_revoke",
        "environment_grant_activate",
        "grant_create",
        "grant_issue",
        "grant_revoke",
    }

    schemas = registry.get_schemas()
    assert forbidden.isdisjoint(
        schema["function"]["name"] for schema in schemas
    )
