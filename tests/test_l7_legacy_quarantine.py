from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from core.life.l7.legacy import (
    LegacySleepLearningQuarantine,
    LegacySleepQuarantinedError,
)
from kernel.kernel import JavisKernel
from knowledge.brain import Brain


class _TrainingEngine:
    def __init__(self):
        self.parameters = {"weight": [1.0, 2.0], "step": 7}
        self.start_calls = 0

    def start(self):
        self.start_calls += 1


class _LegacySleep:
    def __init__(self):
        self.training_engine = _TrainingEngine()
        self.is_sleeping = False
        self._running = False
        self._last_sleep_time = 123.0
        self.start_calls = 0
        self.sleep_calls = 0

    def start(self):
        self.start_calls += 1

    def enter_sleep(self):
        self.sleep_calls += 1


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_quarantine_adapter_exposes_status_but_forbids_start_sleep_and_training(tmp_path: Path):
    legacy = _LegacySleep()
    before = copy.deepcopy(legacy.training_engine.parameters)
    adapter = LegacySleepLearningQuarantine(
        source_root=tmp_path / "source",
        data_root=tmp_path / "data",
        legacy=legacy,
    )

    assert adapter.status() == {
        "state": "quarantined",
        "monitor_running": False,
        "is_sleeping": False,
        "legacy_state_available": True,
    }
    for operation in (adapter.start, adapter.enter_sleep, adapter.train):
        with pytest.raises(LegacySleepQuarantinedError):
            operation()

    assert legacy.start_calls == 0
    assert legacy.sleep_calls == 0
    assert legacy.training_engine.start_calls == 0
    assert legacy.training_engine.parameters == before


def test_sleep_meta_is_read_only_hint_and_consolidated_marker_is_never_evidence(tmp_path: Path):
    source = tmp_path / "source"
    data = tmp_path / "data"
    brain_data = source / "brain_data"
    episodes = brain_data / "episodes"
    episodes.mkdir(parents=True)
    (brain_data / "sleep_meta.json").write_text(
        json.dumps({"last_sleep_time": 1_700_000_000, "unknown": "ignored"}),
        encoding="utf-8",
    )
    (episodes / ".consolidated").write_text("forged marker", encoding="utf-8")
    before = _tree_hash(source)

    adapter = LegacySleepLearningQuarantine(source_root=source, data_root=data)
    hint = adapter.migration_hint()

    assert hint.meta_present is True
    assert hint.meta_valid is True
    assert hint.marker_ignored is True
    assert hint.formal_evidence_ids == ()
    assert hint.reason_codes == ("legacy_sleep_meta_unverified", "legacy_marker_ignored")
    assert _tree_hash(source) == before
    assert not data.exists()


def test_invalid_or_oversized_legacy_meta_fails_closed_without_writes(tmp_path: Path):
    source = tmp_path / "source"
    brain_data = source / "brain_data"
    brain_data.mkdir(parents=True)
    (brain_data / "sleep_meta.json").write_text("{" + "x" * 70_000, encoding="utf-8")
    before = _tree_hash(source)

    hint = LegacySleepLearningQuarantine(
        source_root=source,
        data_root=tmp_path / "data",
    ).migration_hint()

    assert hint.meta_present is True
    assert hint.meta_valid is False
    assert hint.last_sleep_at_utc is None
    assert hint.formal_evidence_ids == ()
    assert _tree_hash(source) == before
    assert not (tmp_path / "data").exists()


def test_javis_kernel_start_does_not_start_legacy_training_or_sleep():
    training = _TrainingEngine()
    legacy_sleep = _LegacySleep()
    kernel = JavisKernel.__new__(JavisKernel)
    kernel.training_engine = training
    kernel.sleep_learning = legacy_sleep
    kernel._started = False

    kernel.start()

    assert kernel._started is True
    assert training.start_calls == 0
    assert legacy_sleep.start_calls == 0


def test_runtime_brain_read_only_mode_never_mutates_or_creates_legacy_state(tmp_path: Path):
    data_root = tmp_path / "data"
    brain = Brain(data_root=data_root, read_only=True)

    brain.learn_fact("must not persist")
    brain.record_experience("intent", "action", "success")
    brain.learn_style("short request", "long assistant response")
    brain.compress()
    brain._flush()

    assert brain.get_stats()["facts_count"] == 0
    assert brain.get_stats()["experiences_count"] == 0
    assert not (data_root / "memory" / "legacy-brain").exists()
