import tempfile
import unittest
from pathlib import Path

from utils import system_diagnostics


class SystemDiagnosticsTests(unittest.TestCase):
    def test_directory_check_can_create_and_verify_a_writable_storage_root(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "output"
            check = system_diagnostics.check_directory(
                "output",
                "导入与输出",
                target,
                create=True,
                writable=True,
            )

            self.assertEqual(check["status"], "pass")
            self.assertTrue(target.is_dir())
            self.assertIn("可写", check["message"])

    def test_report_exposes_actionable_counts(self):
        report = system_diagnostics.build_report([
            {"id": "runtime", "label": "运行时", "status": "pass", "message": "正常"},
            {"id": "model", "label": "模型", "status": "warn", "message": "未连接"},
            {"id": "memory", "label": "记忆", "status": "fail", "message": "不可用"},
        ], scope="full")

        self.assertFalse(report["ok"])
        self.assertEqual(report["counts"], {"pass": 1, "warn": 1, "fail": 1})
        self.assertEqual(report["overall"], "fail")


if __name__ == "__main__":
    unittest.main()
