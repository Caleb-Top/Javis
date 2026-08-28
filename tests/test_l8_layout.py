from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.life.l8.layout import ContinuityLayout


def test_layout_freezes_the_complete_spec_topology(tmp_path: Path) -> None:
    source = tmp_path / "source"
    data = tmp_path / "data"
    source.mkdir()
    layout = ContinuityLayout.from_roots(data, source_root=source).ensure_directories()

    expected = {
        "life", "conversations", "memory", "relationships", "environment",
        "intents", "actions", "growth", "body", "config", "logs", "local-ai",
        "continuity/manifests", "continuity/migrations", "continuity/lineage/ops",
        "continuity/lineage/heads", "continuity/sync/inbox", "continuity/sync/outbox",
        "continuity/sync/conflicts", "continuity/keys", "continuity/termination",
        "continuity/native-journal/inbox",
    }
    actual = {
        path.relative_to(data).as_posix()
        for path in data.rglob("*")
        if path.is_dir() and not any(child.is_dir() for child in path.iterdir())
    }
    assert expected.issuperset(actual)
    assert all((data / relative).is_dir() for relative in expected)
    assert layout.intents == data / "intents"


def test_data_root_must_be_absolute_canonical_and_separate(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="absolute"):
        ContinuityLayout.from_roots("relative", source_root=source)
    with pytest.raises(ValueError, match="separate"):
        ContinuityLayout.from_roots(source / "data", source_root=source)
    with pytest.raises(ValueError, match="separate"):
        ContinuityLayout.from_roots(source, source_root=source / "nested")


def test_runtime_and_explicit_layout_resolve_identically(tmp_path: Path) -> None:
    runtime = SimpleNamespace(root=tmp_path / "source", data_root=tmp_path / "data")
    runtime.root.mkdir()
    assert ContinuityLayout.from_runtime(runtime) == ContinuityLayout.from_roots(
        runtime.data_root,
        source_root=runtime.root,
    )


def test_safe_path_rejects_escape_and_reparse_swap(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    layout = ContinuityLayout.from_roots(tmp_path / "data", source_root=source)
    with pytest.raises(ValueError, match="below"):
        layout.assert_safe_path(tmp_path / "outside")

    import core.life.l8.layout as module

    original = module._is_reparse
    monkeypatch.setattr(
        module,
        "_is_reparse",
        lambda path: path == layout.data_root or original(path),
    )
    with pytest.raises(ValueError, match="reparse"):
        layout.ensure_directories()


def test_root_id_is_stable_salted_and_does_not_expose_the_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    layout = ContinuityLayout.from_roots(tmp_path / "data", source_root=source)
    first = layout.root_id()
    second = ContinuityLayout.from_roots(
        layout.data_root,
        source_root=source,
    ).root_id()

    assert first == second
    assert len(first) == 64
    assert first != hashlib.sha256(str(layout.data_root).encode()).hexdigest()
    assert str(layout.data_root) not in first
    assert (layout.keys / "root-id.salt").stat().st_size == 32


def test_symlinked_data_root_is_rejected_when_host_allows_it(tmp_path: Path) -> None:
    source = tmp_path / "source"
    outside = tmp_path / "outside"
    link = tmp_path / "link"
    source.mkdir()
    outside.mkdir()
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("host cannot create a symlink")
    with pytest.raises(ValueError, match="reparse"):
        ContinuityLayout.from_roots(link, source_root=source)
