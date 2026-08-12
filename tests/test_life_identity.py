import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.life.contracts import IdentitySummary
from core.life.identity import (
    IdentityConstitutionStore,
    IdentityRecoveryRequired,
)


def _store(tmp_path, *, identity_id="identity-one", now=10.0):
    return IdentityConstitutionStore(
        tmp_path,
        id_factory=lambda: identity_id,
        now=lambda: now,
    )


def test_first_birth_creates_one_stable_identity(tmp_path):
    ids = iter(["identity-one", "identity-two"])
    store = IdentityConstitutionStore(
        tmp_path,
        id_factory=lambda: next(ids),
        now=lambda: 10.0,
    )

    first = store.load_or_create()
    second = IdentityConstitutionStore(
        tmp_path,
        id_factory=lambda: next(ids),
        now=lambda: 20.0,
    ).load_or_create()

    assert first.identity_id == "identity-one"
    assert second.identity_id == first.identity_id
    assert second.content_hash == first.content_hash
    assert store.current_path == tmp_path / "life" / "identity" / "current.json"
    assert store.version_path(first).is_file()
    assert store.recovery_path.is_file()


def test_corrupt_current_identity_requires_recovery_without_overwrite(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create()
    second = store.write_version(
        first,
        changes={
            "persona_invariants": [
                "quiet",
                "focused",
                "measured",
                "truthful",
                "patient",
            ]
        },
        approved_by="user",
    )
    store.current_path.write_text("{broken", encoding="utf-8")
    before_v1 = store.version_path(first).read_bytes()
    before_v2 = store.version_path(second).read_bytes()

    with pytest.raises(IdentityRecoveryRequired, match="current constitution"):
        store.load()

    assert store.current_path.read_text(encoding="utf-8") == "{broken"
    assert store.version_path(first).read_bytes() == before_v1
    assert store.version_path(second).read_bytes() == before_v2
    assert store.load_last_verified().content_hash == second.content_hash


def test_rollback_is_explicit_auditable_and_keeps_history_immutable(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create()
    second = store.write_version(
        first,
        changes={
            "persona_invariants": [
                "quiet",
                "focused",
                "measured",
                "truthful",
                "patient",
            ]
        },
        approved_by="user",
    )
    before_v1 = store.version_path(first).read_bytes()
    before_v2 = store.version_path(second).read_bytes()

    rolled_back = store.rollback_to_previous(approved_by="user")

    assert rolled_back.version == second.version + 1
    assert rolled_back.previous_version_hash == second.content_hash
    assert rolled_back.persona_invariants == first.persona_invariants
    assert store.version_path(first).read_bytes() == before_v1
    assert store.version_path(second).read_bytes() == before_v2
    assert store.audit_records()[-1]["action"] == "identity.rollback"


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "cloud-model"},
        {"api_key": "secret"},
        {"current_mood": "happy"},
        {"identity_id": "other-identity"},
        {"version": 99},
        {"approved_by": "model"},
    ],
)
def test_identity_rejects_runtime_secret_and_controlled_fields(tmp_path, changes):
    store = _store(tmp_path)
    identity = store.load_or_create()

    with pytest.raises(ValueError, match="forbidden identity field"):
        store.write_version(identity, changes=changes, approved_by="user")


@pytest.mark.parametrize("approved_by", ["", "   ", None])
def test_identity_rejects_missing_explicit_approval(tmp_path, approved_by):
    store = _store(tmp_path)
    identity = store.load_or_create()

    with pytest.raises(ValueError, match="approved_by"):
        store.write_version(
            identity,
            changes={"relationship_role": "long_term_partner"},
            approved_by=approved_by,
        )


def test_missing_current_with_history_never_creates_a_second_identity(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create()
    store.current_path.unlink()
    before_version = store.version_path(first).read_bytes()

    with pytest.raises(IdentityRecoveryRequired, match="current constitution is missing"):
        IdentityConstitutionStore(
            tmp_path,
            id_factory=lambda: "identity-two",
            now=lambda: 20.0,
        ).load_or_create()

    assert store.version_path(first).read_bytes() == before_version
    assert not store.current_path.exists()


def test_existing_instance_history_blocks_accidental_first_birth(tmp_path):
    instance_history = tmp_path / "life" / "instances" / "instance.json"
    instance_history.parent.mkdir(parents=True)
    instance_history.write_text("{}", encoding="utf-8")

    with pytest.raises(IdentityRecoveryRequired, match="instance history"):
        _store(tmp_path).load_or_create()

    assert not (tmp_path / "life" / "identity" / "current.json").exists()


def test_stale_current_cannot_create_a_forked_version_chain(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create()
    second = store.write_version(
        first,
        changes={"relationship_role": "long_term_partner"},
        approved_by="user",
    )

    with pytest.raises(IdentityRecoveryRequired, match="stale constitution"):
        store.write_version(
            first,
            changes={"relationship_role": "assistant"},
            approved_by="user",
        )

    assert store.load().content_hash == second.content_hash


def test_current_load_rejects_tampered_immutable_history(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create()
    second = store.write_version(
        first,
        changes={"relationship_role": "long_term_partner"},
        approved_by="user",
    )
    store.version_path(first).write_text("{broken", encoding="utf-8")

    with pytest.raises(IdentityRecoveryRequired, match="version history"):
        store.load()

    with pytest.raises(IdentityRecoveryRequired, match="no verified constitution"):
        store.load_last_verified()


def test_last_verified_ignores_an_invalid_trailing_version(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create()
    second = store.write_version(
        first,
        changes={"relationship_role": "long_term_partner"},
        approved_by="user",
    )
    invalid = store.versions_dir / f"3-{'0' * 64}.json"
    invalid.write_text("{broken", encoding="utf-8")

    with pytest.raises(IdentityRecoveryRequired, match="version history"):
        store.load()

    assert store.load_last_verified().content_hash == second.content_hash


def test_atomic_current_replace_failure_preserves_previous_active_identity(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create()
    before_current = store.current_path.read_bytes()
    real_replace = os.replace

    def fail_current_replace(source, destination):
        if Path(destination) == store.current_path:
            raise OSError("simulated current replace failure")
        return real_replace(source, destination)

    with patch("core.life.identity.os.replace", side_effect=fail_current_replace):
        with pytest.raises(OSError, match="simulated current replace failure"):
            store.write_version(
                first,
                changes={"relationship_role": "long_term_partner"},
                approved_by="user",
            )

    assert store.current_path.read_bytes() == before_current
    with pytest.raises(IdentityRecoveryRequired):
        store.load()
    assert store.load_last_verified().version == 2


def test_recovery_pointer_and_summary_match_the_active_constitution(tmp_path):
    store = _store(tmp_path)
    identity = store.load_or_create()
    pointer = json.loads(store.recovery_path.read_text(encoding="utf-8"))

    assert pointer == {
        "schema_version": 1,
        "identity_id": identity.identity_id,
        "version": identity.version,
        "content_hash": identity.content_hash,
        "version_file": store.version_path(identity).name,
    }
    assert store.summary() == IdentitySummary(
        identity_id=identity.identity_id,
        name=identity.name,
        kind=identity.kind,
        relationship_role=identity.relationship_role,
        version=identity.version,
        content_hash=identity.content_hash,
    )


def test_recovery_pointer_uses_strict_json_types(tmp_path):
    store = _store(tmp_path)
    store.load_or_create()
    pointer = json.loads(store.recovery_path.read_text(encoding="utf-8"))
    pointer["version"] = True
    store.recovery_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(IdentityRecoveryRequired, match="recovery pointer"):
        store.load()


def test_audit_metadata_is_strictly_validated(tmp_path):
    store = _store(tmp_path)
    identity = store.load_or_create()
    audit_path = store.audit_dir / f"1-{identity.content_hash}.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["source_version"] = True
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    with pytest.raises(IdentityRecoveryRequired, match="audit history"):
        store.load()


def test_first_identity_cannot_be_rolled_back(tmp_path):
    store = _store(tmp_path)
    store.load_or_create()

    with pytest.raises(ValueError, match="requires a previous version"):
        store.rollback_to_previous(approved_by="user")
