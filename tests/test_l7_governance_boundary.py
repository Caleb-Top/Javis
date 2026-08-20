from __future__ import annotations

import json
import zipfile
from pathlib import Path

from core import auto_updater, skill_creator, skill_manager
from core.runtime import _register_extension_tools
from core.tool_registry import ToolRegistry


MUTATION_TOOLS = {
    "update_version",
    "update_create_backup",
    "skill_install",
    "skill_enable",
    "skill_disable",
    "skill_uninstall",
    "skill_create",
    "skill_improve",
    "skill_delete",
    "skill_import",
    "skill_export",
}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_runtime_extension_registry_has_no_legacy_mutation_tools(tmp_path: Path) -> None:
    registry = ToolRegistry(permission_level="full_access")
    _register_extension_tools(registry, tmp_path)

    assert MUTATION_TOOLS.isdisjoint(registry.list_all())
    assert "current_version" not in registry.list_all()


def test_legacy_registrars_expose_read_only_tools_only(tmp_path: Path, monkeypatch) -> None:
    registry = ToolRegistry(permission_level="full_access")
    monkeypatch.setattr(skill_creator, "_creator", skill_creator.SkillCreator(str(tmp_path)))
    monkeypatch.setattr(skill_manager, "_manager", skill_manager.SkillManager(tmp_path))

    auto_updater.register_in_manifest(registry)
    skill_creator.register_in_manifest(registry)
    skill_manager.register_in_manifest(registry)

    assert MUTATION_TOOLS.isdisjoint(registry.list_all())
    assert {"current_version", "skill_list", "skill_list_installed"} <= set(registry.list_all())


def test_direct_legacy_skill_mutation_is_fail_closed_and_write_free(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skills.mkdir()
    source = tmp_path / "untrusted.py"
    source.write_text("raise RuntimeError('must never execute')\n", encoding="utf-8")
    existing = skills / "existing.py"
    existing.write_text("ORIGINAL = True\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    creator = skill_creator.SkillCreator(str(skills))
    manager = skill_manager.SkillManager(skills)
    results = [
        creator.create("new", "description", "prompt"),
        creator.improve("existing", new_prompt="changed"),
        creator.delete_skill("existing"),
        creator.import_skill(str(source)),
        creator.export_skill("existing", str(tmp_path / "export")),
        manager.install("untrusted", str(source)),
        manager.enable("untrusted"),
        manager.disable("untrusted"),
        manager.uninstall("existing"),
    ]

    assert all(result["code"] == "legacy_governance_disabled" for result in results)
    assert manager.list_all() == []
    assert _snapshot(tmp_path) == before


def test_traversal_archive_cannot_restore_into_source_tree(tmp_path: Path) -> None:
    archive = tmp_path / "hostile.zip"
    outside = tmp_path.parent / "javis-l7-traversal-proof.txt"
    outside.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr("../javis-l7-traversal-proof.txt", "escaped")
    before = _snapshot(tmp_path)

    result = auto_updater.restore_backup(str(archive))

    assert result["code"] == "native_continuity_required"
    assert not outside.exists()
    assert _snapshot(tmp_path) == before


def test_update_and_backup_calls_require_native_continuity(tmp_path: Path) -> None:
    before = _snapshot(tmp_path)

    for result in (
        auto_updater.pull_latest(),
        auto_updater.create_backup(),
        auto_updater.restore_backup(str(tmp_path / "missing.zip")),
    ):
        assert result["code"] == "native_continuity_required"

    assert _snapshot(tmp_path) == before


def test_version_is_read_only_from_release_manifest(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "release.manifest.json"
    manifest.write_text(json.dumps({"version": "v9.8.7"}), encoding="utf-8")
    monkeypatch.setattr(auto_updater, "RELEASE_MANIFEST", manifest)

    assert auto_updater.get_current_version() == "9.8.7"


def test_legacy_update_module_has_no_source_restore_or_process_execution() -> None:
    content = Path("core/auto_updater.py").read_text(encoding="utf-8")

    assert "extractall(" not in content
    assert "subprocess" not in content
    assert "shutil" not in content
