import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


os.environ["JAVIS_TEST_MODE"] = "1"
os.environ.setdefault("DEEPSEEK_API_KEY", "dummy")


class AppWorkspaceConflictTests(unittest.TestCase):
    def test_save_rejects_external_modification_conflict(self):
        import main

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.txt"
            path.write_text("external", encoding="utf-8")
            actual_modified = path.stat().st_mtime
            with (
                patch.object(main, "_resolve_workspace_path", return_value=path),
                patch("core.workspace_manager.sandbox_check_path", return_value=True),
            ):
                result = asyncio.run(main.api_workspace_save({
                    "path": "example.txt",
                    "content": "editor",
                    "expected_modified": actual_modified - 10,
                }))

            self.assertFalse(result["ok"])
            self.assertTrue(result["conflict"])
            self.assertEqual(path.read_text(encoding="utf-8"), "external")

    def test_save_returns_new_modified_timestamp(self):
        import main

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.txt"
            path.write_text("before", encoding="utf-8")
            expected_modified = path.stat().st_mtime
            with (
                patch.object(main, "_resolve_workspace_path", return_value=path),
                patch("core.workspace_manager.sandbox_check_path", return_value=True),
            ):
                result = asyncio.run(main.api_workspace_save({
                    "path": "example.txt",
                    "content": "after",
                    "expected_modified": expected_modified,
                }))

            self.assertTrue(result["ok"])
            self.assertIn("modified", result)
            self.assertEqual(path.read_text(encoding="utf-8"), "after")
