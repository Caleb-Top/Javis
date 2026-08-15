import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.life.lineage import InstanceLineageStore, LineageRecoveryRequired


def _store(tmp_path, *, instance_ids=None, now=10.0):
    ids = iter(instance_ids or ["instance-one", "instance-two", "instance-three"])
    return InstanceLineageStore(
        tmp_path,
        instance_id_factory=lambda: next(ids),
        lineage_id_factory=lambda: "lineage-one",
        now=lambda: now,
    )


def test_restart_keeps_instance_but_copy_creates_reviewable_fork(tmp_path):
    store = _store(tmp_path)

    first = store.load_or_create("identity-one", environment_fingerprint="env-a")
    same = store.load_or_create("identity-one", environment_fingerprint="env-a")
    fork = store.load_or_create("identity-one", environment_fingerprint="env-b")

    assert same.instance_id == first.instance_id
    assert fork.instance_id != first.instance_id
    assert fork.parent_instance_id == first.instance_id
    assert fork.lineage_id == first.lineage_id
    assert fork.generation == first.generation + 1
    assert fork.fork_pending_review is True


def test_unclean_shutdown_expires_temporary_authority(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")
    first_assessment = store.assess_previous_run(instance.instance_id)
    assert first_assessment.reason_code == "first_start"
    store.mark_started(instance.instance_id, boot_id="boot-one")

    restarted = _store(tmp_path, instance_ids=["unused"], now=20.0)
    assessment = restarted.assess_previous_run(instance.instance_id)

    assert assessment.previous_boot_id == "boot-one"
    assert assessment.unclean_shutdown is True
    assert assessment.temporary_authority_valid is False
    assert assessment.reason_code == "unclean_shutdown"


def test_assessment_happens_before_current_boot_is_marked_started(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")
    store.assess_previous_run(instance.instance_id)
    store.mark_started(instance.instance_id, boot_id="boot-one")
    store.mark_clean_shutdown(
        instance.instance_id,
        boot_id="boot-one",
        last_event_cursor=42,
        active_request_id=None,
    )

    assessment = store.assess_previous_run(instance.instance_id)

    assert assessment.previous_boot_id == "boot-one"
    assert assessment.unclean_shutdown is False
    assert assessment.temporary_authority_valid is False
    assert assessment.last_event_cursor == 42
    assert assessment.reason_code == "clean_shutdown"
    store.mark_started(instance.instance_id, boot_id="boot-two")


def test_mark_started_rejects_calls_that_skip_previous_run_assessment(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")

    with pytest.raises(LineageRecoveryRequired, match="assess_previous_run"):
        store.mark_started(instance.instance_id, boot_id="boot-one")

    assert not store.checkpoint_path(instance.instance_id).exists()


def test_clean_shutdown_rejects_stale_boot_without_mutation(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")
    store.assess_previous_run(instance.instance_id)
    store.mark_started(instance.instance_id, boot_id="boot-one")
    checkpoint_path = store.checkpoint_path(instance.instance_id)
    before_checkpoint = checkpoint_path.read_bytes()

    with pytest.raises(LineageRecoveryRequired, match="boot_id"):
        store.mark_clean_shutdown(
            instance.instance_id,
            boot_id="boot-stale",
            last_event_cursor=1,
            active_request_id=None,
        )

    assert checkpoint_path.read_bytes() == before_checkpoint


def test_clean_shutdown_records_cursor_but_never_temporary_authority(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")
    store.assess_previous_run(instance.instance_id)
    started = store.mark_started(instance.instance_id, boot_id="boot-one")
    assert started.temporary_authority_valid is True

    closed = store.mark_clean_shutdown(
        instance.instance_id,
        boot_id="boot-one",
        last_event_cursor=9,
        active_request_id="request-one",
    )

    assert closed.last_event_cursor == 9
    assert closed.active_request_id == "request-one"
    assert closed.temporary_authority_valid is False
    checkpoint_text = store.checkpoint_path(instance.instance_id).read_text(
        encoding="utf-8"
    )
    assert "token" not in checkpoint_text.lower()
    assert "approval" not in checkpoint_text.lower()


def test_environment_fingerprint_is_hashed_and_never_persisted_raw(tmp_path):
    raw_fingerprint = "private-device-fingerprint"
    store = _store(tmp_path, instance_ids=["instance-one"])

    instance = store.load_or_create("identity-one", raw_fingerprint)

    assert len(instance.environment_fingerprint_hash) == 64
    assert instance.environment_fingerprint_hash != raw_fingerprint
    persisted = b"".join(
        path.read_bytes()
        for path in store.instances_root.rglob("*")
        if path.is_file()
    )
    assert raw_fingerprint.encode("utf-8") not in persisted


def test_identity_mismatch_never_reuses_an_existing_instance(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    first = store.load_or_create("identity-one", "env-a")

    with pytest.raises(LineageRecoveryRequired, match="identity_id"):
        store.load_or_create("identity-two", "env-a")

    assert store.load_active().instance_id == first.instance_id


def test_missing_or_corrupt_active_pointer_with_records_requires_recovery(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    first = store.load_or_create("identity-one", "env-a")
    before_record = store.record_path(first.instance_id).read_bytes()
    store.active_path.unlink()

    with pytest.raises(LineageRecoveryRequired, match="active instance pointer"):
        store.load_or_create("identity-one", "env-a")

    assert store.record_path(first.instance_id).read_bytes() == before_record
    store.active_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(LineageRecoveryRequired, match="active instance pointer"):
        store.load_active()


def test_tampered_checkpoint_hash_requires_recovery(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")
    store.assess_previous_run(instance.instance_id)
    store.mark_started(instance.instance_id, boot_id="boot-one")
    checkpoint_path = store.checkpoint_path(instance.instance_id)
    checkpoint_path.write_text("{}", encoding="utf-8")

    with pytest.raises(LineageRecoveryRequired, match="checkpoint"):
        store.assess_previous_run(instance.instance_id)


def test_reusing_previous_boot_id_is_rejected_after_assessment(tmp_path):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")
    store.assess_previous_run(instance.instance_id)
    store.mark_started(instance.instance_id, boot_id="boot-one")
    store.mark_clean_shutdown(
        instance.instance_id,
        boot_id="boot-one",
        last_event_cursor=2,
        active_request_id=None,
    )
    store.assess_previous_run(instance.instance_id)

    with pytest.raises(LineageRecoveryRequired, match="boot_id"):
        store.mark_started(instance.instance_id, boot_id="boot-one")


@pytest.mark.parametrize("cursor", [-1, True, 1.5, "1"])
def test_clean_shutdown_rejects_invalid_event_cursor(tmp_path, cursor):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")
    store.assess_previous_run(instance.instance_id)
    store.mark_started(instance.instance_id, boot_id="boot-one")

    with pytest.raises(ValueError, match="last_event_cursor"):
        store.mark_clean_shutdown(
            instance.instance_id,
            boot_id="boot-one",
            last_event_cursor=cursor,
            active_request_id=None,
        )


def test_failed_active_pointer_replace_leaves_an_explicit_recovery_state(tmp_path):
    store = _store(tmp_path)
    first = store.load_or_create("identity-one", "env-a")
    before_active = store.active_path.read_bytes()
    real_replace = os.replace

    def fail_active_replace(source, destination):
        if Path(destination) == store.active_path:
            raise OSError("simulated active replace failure")
        return real_replace(source, destination)

    with patch("core.life.lineage.os.replace", side_effect=fail_active_replace):
        with pytest.raises(OSError, match="simulated active replace failure"):
            store.load_or_create("identity-one", "env-b")

    assert store.active_path.read_bytes() == before_active
    assert store.record_path(first.instance_id).is_file()
    with pytest.raises(LineageRecoveryRequired, match="lineage tip"):
        store.load_or_create("identity-one", "env-a")


def test_partial_started_write_enters_recovery_instead_of_faking_a_clean_state(
    tmp_path,
):
    store = _store(tmp_path, instance_ids=["instance-one"])
    instance = store.load_or_create("identity-one", "env-a")
    store.assess_previous_run(instance.instance_id)
    record_path = store.record_path(instance.instance_id)
    real_replace = os.replace

    def fail_record_replace(source, destination):
        if Path(destination) == record_path:
            raise OSError("simulated record replace failure")
        return real_replace(source, destination)

    with patch("core.life.lineage.os.replace", side_effect=fail_record_replace):
        with pytest.raises(OSError, match="simulated record replace failure"):
            store.mark_started(instance.instance_id, boot_id="boot-one")

    with pytest.raises(LineageRecoveryRequired, match="never started"):
        store.load_active()


def test_stale_assessment_cannot_overwrite_a_newer_boot(tmp_path):
    first_store = _store(tmp_path, instance_ids=["instance-one"])
    instance = first_store.load_or_create("identity-one", "env-a")
    first_store.assess_previous_run(instance.instance_id)

    second_store = _store(tmp_path, instance_ids=["unused"])
    second_store.assess_previous_run(instance.instance_id)
    second_store.mark_started(instance.instance_id, boot_id="boot-two")

    with pytest.raises(LineageRecoveryRequired, match="assessment is stale"):
        first_store.mark_started(instance.instance_id, boot_id="boot-one")
