import tempfile
import unittest
from pathlib import Path

from core.events import EventBus, EventType
from core.skill_catalog import SkillCatalog, SkillGovernanceError


def write_skill(
    root: Path,
    directory: str,
    *,
    name: str,
    description: str,
    license_name: str | None,
    version: str = "1.0.0",
    tags: tuple[str, ...] = (),
    body: str = "Use approved Javis tools to complete the task.",
) -> Path:
    skill_dir = root / directory
    skill_dir.mkdir(parents=True, exist_ok=True)
    license_line = f"license: {license_name}\n" if license_name is not None else ""
    tag_text = ", ".join(tags)
    document = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"{license_line}"
        f"version: {version}\n"
        f"tags: [{tag_text}]\n"
        "---\n"
        f"{body}\n"
    )
    path = skill_dir / "SKILL.md"
    path.write_text(document, encoding="utf-8")
    return path


class SkillCatalogTests(unittest.TestCase):
    def test_runtime_owns_persistent_skill_catalog_under_its_root(self):
        from core.runtime import create_runtime

        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runtime = create_runtime(root, startup_side_effects=False)
            catalog_path = runtime.skill_catalog.path
            shared_bus = runtime.skill_catalog.event_bus
            runtime.close()

        self.assertEqual(catalog_path, (root / "data" / "skills" / "catalog.sqlite3").resolve())
        self.assertIs(shared_bus, runtime.event_bus)

    def test_discovers_frontmatter_as_governed_candidates(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            write_skill(
                root,
                "browser-research",
                name="browser-research",
                description="Research websites and summarize evidence",
                license_name="MIT",
                version="1.2.0",
                tags=("web", "research"),
            )
            catalog = SkillCatalog(root / "catalog.sqlite3")

            discovered = catalog.discover(root, source="agent-skills")
            skill = catalog.get("browser-research")
            catalog.close()

        self.assertEqual(discovered, ["browser-research"])
        self.assertEqual(skill["status"], "candidate")
        self.assertEqual(skill["evaluation_status"], "untested")
        self.assertEqual(skill["license"], "MIT")
        self.assertEqual(skill["version"], "1.2.0")
        self.assertEqual(skill["source"], "agent-skills")
        self.assertEqual(skill["tags"], ["web", "research"])
        self.assertEqual(len(skill["content_hash"]), 64)

    def test_search_is_deterministic_and_persists_across_reopen(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            database = root / "catalog.sqlite3"
            write_skill(
                root,
                "research",
                name="web-research",
                description="Collect reliable web evidence",
                license_name="MIT",
                tags=("browser", "evidence"),
            )
            write_skill(
                root,
                "files",
                name="workspace-files",
                description="Organize local workspace files",
                license_name="Apache-2.0",
                tags=("files", "workspace"),
            )
            catalog = SkillCatalog(database)
            catalog.discover(root, source="test-source")
            catalog.close()

            reopened = SkillCatalog(database)
            results = reopened.search("web evidence")
            all_skills = reopened.list_skills()
            reopened.close()

        self.assertEqual([item["name"] for item in results], ["web-research"])
        self.assertEqual([item["name"] for item in all_skills], ["web-research", "workspace-files"])

    def test_promotion_requires_permissive_license_and_passing_evaluation(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            write_skill(
                root,
                "approved",
                name="approved-skill",
                description="An approved skill used for tests",
                license_name="MIT",
            )
            catalog = SkillCatalog(root / "catalog.sqlite3")
            catalog.discover(root, source="test-source")

            with self.assertRaises(SkillGovernanceError):
                catalog.promote("approved-skill", "staged")
            catalog.record_evaluation("approved-skill", "passed", score=0.94)
            staged = catalog.promote("approved-skill", "staged")
            active = catalog.promote("approved-skill", "active")
            catalog.close()

        self.assertEqual(staged["status"], "staged")
        self.assertEqual(active["status"], "active")

    def test_unknown_gpl_and_failed_evaluation_are_blocked(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            write_skill(
                root,
                "unknown",
                name="unknown-skill",
                description="Missing license metadata",
                license_name=None,
            )
            write_skill(
                root,
                "gpl",
                name="gpl-skill",
                description="Strong copyleft test skill",
                license_name="GPL-3.0",
            )
            write_skill(
                root,
                "failed",
                name="failed-skill",
                description="Evaluation failure test skill",
                license_name="MIT",
            )
            catalog = SkillCatalog(root / "catalog.sqlite3")
            catalog.discover(root, source="test-source")
            for name in ("unknown-skill", "gpl-skill"):
                catalog.record_evaluation(name, "passed", score=1.0)
                with self.assertRaises(SkillGovernanceError):
                    catalog.promote(name, "staged")
            catalog.record_evaluation("failed-skill", "failed", score=0.2)
            with self.assertRaises(SkillGovernanceError):
                catalog.promote("failed-skill", "staged")
            catalog.close()

    def test_changed_content_invalidates_evaluation_and_activation(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            path = write_skill(
                root,
                "mutable",
                name="mutable-skill",
                description="Content change governance test",
                license_name="MIT",
            )
            catalog = SkillCatalog(root / "catalog.sqlite3")
            catalog.discover(root, source="test-source")
            catalog.record_evaluation("mutable-skill", "passed", score=1.0)
            catalog.promote("mutable-skill", "staged")
            catalog.promote("mutable-skill", "active")

            path.write_text(path.read_text(encoding="utf-8") + "Changed.\n", encoding="utf-8")
            catalog.discover(root, source="test-source")
            changed = catalog.get("mutable-skill")
            catalog.close()

        self.assertEqual(changed["status"], "candidate")
        self.assertEqual(changed["evaluation_status"], "untested")

    def test_catalog_publishes_governance_events(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            write_skill(
                root,
                "events",
                name="event-skill",
                description="Governance event test skill",
                license_name="MIT",
            )
            bus = EventBus()
            catalog = SkillCatalog(root / "catalog.sqlite3", event_bus=bus)
            catalog.discover(root, source="test-source")
            catalog.record_evaluation("event-skill", "passed", score=0.9)
            catalog.promote("event-skill", "staged")
            catalog.close()

        self.assertEqual(bus.history(EventType.SKILL_DISCOVERED)[0].payload["name"], "event-skill")
        self.assertEqual(bus.history(EventType.SKILL_EVALUATED)[0].payload["status"], "passed")
        self.assertEqual(bus.history(EventType.SKILL_STATUS_CHANGED)[0].payload["status"], "staged")


if __name__ == "__main__":
    unittest.main()
