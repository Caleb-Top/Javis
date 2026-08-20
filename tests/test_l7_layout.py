from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from core.life.l7 import layout as layout_module
from core.life.l7.layout import DataRootLayout


@dataclass
class RuntimeFixture:
    root: Path | str
    data_root: Path | str
    package_root: Path | str | None = None


def _runtime(tmp_path: Path) -> RuntimeFixture:
    return RuntimeFixture(root=tmp_path / "source", data_root=tmp_path / "user-data")


def test_layout_is_fixed_below_the_one_runtime_data_root(tmp_path: Path):
    runtime = _runtime(tmp_path)
    layout = DataRootLayout.from_runtime(runtime)

    assert layout.data_root == tmp_path / "user-data"
    assert layout.policies == layout.data_root / "growth" / "policies"
    assert layout.runs == layout.data_root / "growth" / "runs"
    assert layout.candidates == layout.data_root / "growth" / "candidates"
    assert layout.artifacts_sha256 == layout.data_root / "growth" / "artifacts" / "sha256"
    assert layout.deployments == layout.data_root / "growth" / "deployments"
    assert layout.receipts == layout.data_root / "growth" / "receipts"
    assert layout.sandbox == layout.data_root / "growth" / "sandbox"
    assert layout.quarantine == layout.data_root / "growth" / "quarantine"
    assert layout.body_preferences == layout.data_root / "body" / "preferences"
    assert layout.body_profiles == layout.data_root / "body" / "profiles"
    assert layout.body_diagnostics == layout.data_root / "body" / "diagnostics"
    assert not layout.data_root.exists()


def test_ensure_directories_creates_only_the_frozen_layout(tmp_path: Path):
    runtime = _runtime(tmp_path)
    layout = DataRootLayout.from_runtime(runtime).ensure_directories()

    for path in (
        layout.policies,
        layout.runs,
        layout.candidates,
        layout.artifacts_sha256,
        layout.deployments,
        layout.receipts,
        layout.sandbox,
        layout.quarantine,
        layout.body_preferences,
        layout.body_profiles,
        layout.body_diagnostics,
    ):
        assert path.is_dir()
        assert path.is_relative_to(layout.data_root)


def test_runtime_data_root_must_be_absolute_nonempty_and_exact(tmp_path: Path):
    runtime = _runtime(tmp_path)
    with pytest.raises(ValueError, match="match runtime.data_root"):
        DataRootLayout.from_runtime(runtime, tmp_path / "other-data")

    runtime.data_root = "relative-data"
    with pytest.raises(ValueError, match="absolute"):
        DataRootLayout.from_runtime(runtime)

    runtime.data_root = "  "
    with pytest.raises(ValueError, match="empty"):
        DataRootLayout.from_runtime(runtime)


def test_windows_case_variants_compare_as_the_same_runtime_root(tmp_path: Path):
    runtime = RuntimeFixture(root=tmp_path / "source", data_root=tmp_path / "User-Data")
    layout = DataRootLayout.from_runtime(runtime, tmp_path / "user-data")
    assert layout.data_root == tmp_path / "user-data"


@pytest.mark.parametrize("nested", ["", "data", "nested/data"])
def test_source_tree_data_roots_fail_before_any_directory_is_created(tmp_path: Path, nested: str):
    source = tmp_path / "source"
    data = source if not nested else source / nested
    runtime = RuntimeFixture(root=source, data_root=data)

    with pytest.raises(ValueError, match="outside source"):
        DataRootLayout.from_runtime(runtime)
    assert not data.exists()


def test_packaged_runtime_root_is_also_read_only(tmp_path: Path):
    package_root = tmp_path / "installed-runtime"
    data = package_root / "data"
    runtime = RuntimeFixture(
        root=tmp_path / "source",
        data_root=data,
        package_root=package_root,
    )
    with pytest.raises(ValueError, match="packaged runtime"):
        DataRootLayout.from_runtime(runtime)
    assert not data.exists()


def test_assert_safe_path_rejects_escape_and_accepts_descendants(tmp_path: Path):
    layout = DataRootLayout.from_runtime(_runtime(tmp_path))
    assert layout.assert_safe_path(layout.sandbox / "run-1") == layout.sandbox / "run-1"
    with pytest.raises(ValueError, match="within runtime.data_root"):
        layout.assert_safe_path(tmp_path / "elsewhere")


def test_symlink_ancestor_is_rejected_without_creating_children(tmp_path: Path):
    target = tmp_path / "actual-data"
    target.mkdir()
    link = tmp_path / "linked-data"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    runtime = RuntimeFixture(root=tmp_path / "source", data_root=link)
    with pytest.raises(ValueError, match="symlink or reparse"):
        DataRootLayout.from_runtime(runtime)
    assert list(target.iterdir()) == []


def test_reparse_or_junction_detection_fails_closed_before_mkdir(tmp_path: Path, monkeypatch):
    data = tmp_path / "junction-data"
    data.mkdir()
    runtime = RuntimeFixture(root=tmp_path / "source", data_root=data)
    original = layout_module._is_reparse

    def fake_is_reparse(path: Path) -> bool:
        return path == data or original(path)

    monkeypatch.setattr(layout_module, "_is_reparse", fake_is_reparse)
    with pytest.raises(ValueError, match="reparse"):
        DataRootLayout.from_runtime(runtime)
    assert not (data / "growth").exists()


def test_reparse_swap_after_layout_creation_is_detected_before_directory_creation(
    tmp_path: Path, monkeypatch
):
    layout = DataRootLayout.from_runtime(_runtime(tmp_path))
    original = layout_module._is_reparse

    def fake_is_reparse(path: Path) -> bool:
        return path == layout.data_root or original(path)

    monkeypatch.setattr(layout_module, "_is_reparse", fake_is_reparse)
    layout.data_root.mkdir()
    with pytest.raises(ValueError, match="reparse"):
        layout.ensure_directories()
    assert not layout.growth.exists()
