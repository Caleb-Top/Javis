import json
import shutil
import tempfile
import unittest
from pathlib import Path

from core.runtime import create_runtime
from core.skill_catalog import SkillCatalog, SkillGovernanceError, provenance_sha256


REPO_ROOT = Path(__file__).resolve().parents[1]
IMPORT_ROOT = REPO_ROOT / "skills" / "external" / "agent-skills"
EXPECTED_ARCHIVE_SHA256 = (
    "199a0dd07e8ad7bc25499925f1a4a3ea141b75175d051ee0e5a50eba6b2017d3"
)


class ExternalAgentSkillsTests(unittest.TestCase):
    def test_provenance_hash_is_stable_across_windows_newlines(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            lf = root / "lf.md"
            crlf = root / "crlf.md"
            lf.write_bytes(b"alpha\nbeta\n")
            crlf.write_bytes(b"alpha\r\nbeta\r\n")

            self.assertEqual(provenance_sha256(lf), provenance_sha256(crlf))

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
            self.assertEqual(
                provenance_sha256(IMPORT_ROOT / entry["path"]),
                entry["sha256"],
            )

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

    def test_manifest_discovery_verifies_integrity_before_registering(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            imported = root / "agent-skills"
            shutil.copytree(IMPORT_ROOT, imported)
            catalog = SkillCatalog(root / "catalog.sqlite3")
            try:
                names = catalog.discover_manifest(imported / "PROVENANCE.json")
            finally:
                catalog.close()

        self.assertEqual(len(names), 24)

    def test_manifest_discovery_rejects_a_tampered_import_atomically(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            imported = root / "agent-skills"
            shutil.copytree(IMPORT_ROOT, imported)
            document = next((imported / "skills").rglob("SKILL.md"))
            document.write_text(document.read_text(encoding="utf-8") + "\nTampered.\n", encoding="utf-8")
            catalog = SkillCatalog(root / "catalog.sqlite3")
            try:
                with self.assertRaises(SkillGovernanceError):
                    catalog.discover_manifest(imported / "PROVENANCE.json")
                stats = catalog.stats()
            finally:
                catalog.close()

        self.assertEqual(stats["total"], 0)

    def test_runtime_auto_indexes_verified_external_imports_as_candidates(self):
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            destination = root / "skills" / "external" / "agent-skills"
            destination.parent.mkdir(parents=True)
            shutil.copytree(IMPORT_ROOT, destination)
            runtime = create_runtime(root, startup_side_effects=False)
            try:
                stats = runtime.skill_catalog.stats()
                skills = runtime.skill_catalog.list_skills()
                runtime_status = runtime.get_runtime_status()
            finally:
                runtime.close()

        self.assertEqual(stats["total"], 24)
        self.assertTrue(all(skill["status"] == "candidate" for skill in skills))
        self.assertEqual(runtime_status["skill_count"], stats["total"])
        self.assertEqual(runtime_status["operational_skill_count"], 0)


if __name__ == "__main__":
    unittest.main()
