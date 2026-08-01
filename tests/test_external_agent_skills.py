import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from core.skill_catalog import SkillCatalog


REPO_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = REPO_ROOT / "skills" / "external" / "agent-skills"
EXPECTED_ARCHIVE_SHA256 = (
    "199a0dd07e8ad7bc25499925f1a4a3ea141b75175d051ee0e5a50eba6b2017d3"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExternalAgentSkillsTests(unittest.TestCase):
    def test_provenance_manifest_covers_every_imported_source_file(self):
        manifest = json.loads((IMPORT_ROOT / "PROVENANCE.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["upstream"], "addyosmani/agent-skills")
        self.assertEqual(manifest["source_archive_sha256"], EXPECTED_ARCHIVE_SHA256)
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(manifest["default_status"], "candidate")
        self.assertEqual(manifest["skill_count"], 24)

        listed_paths = [entry["path"] for entry in manifest["files"]]
        actual_paths = sorted(
            [
                path.relative_to(IMPORT_ROOT).as_posix()
                for path in IMPORT_ROOT.rglob("*")
                if path.is_file() and path.name != "PROVENANCE.json"
            ],
            key=str.casefold,
        )
        self.assertEqual(listed_paths, actual_paths)
        self.assertEqual(len(actual_paths), 29)
        for entry in manifest["files"]:
            self.assertEqual(sha256(IMPORT_ROOT / entry["path"]), entry["sha256"])

    def test_import_has_mit_notice_and_24_unique_governed_candidates(self):
        license_text = (IMPORT_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", license_text)
        skill_documents = sorted(IMPORT_ROOT.rglob("SKILL.md"))
        self.assertEqual(len(skill_documents), 24)

        with tempfile.TemporaryDirectory() as root_text:
            catalog = SkillCatalog(Path(root_text) / "catalog.sqlite3")
            try:
                names = catalog.discover(
                    IMPORT_ROOT / "skills",
                    source="github:addyosmani/agent-skills@main",
                    default_license="MIT",
                )
                skills = catalog.list_skills()
            finally:
                catalog.close()

        self.assertEqual(len(names), 24)
        self.assertEqual(len(set(names)), 24)
        self.assertTrue(all(skill["status"] == "candidate" for skill in skills))
        self.assertTrue(all(skill["evaluation_status"] == "untested" for skill in skills))
        self.assertTrue(all(skill["license"] == "MIT" for skill in skills))


if __name__ == "__main__":
    unittest.main()
