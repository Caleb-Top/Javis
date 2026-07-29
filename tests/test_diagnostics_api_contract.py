import unittest
from pathlib import Path


class DiagnosticsApiContractTests(unittest.TestCase):
    def test_backend_exposes_scoped_self_test(self):
        source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/diagnostics/self-test")', source)
        self.assertIn('scope = str(data.get("scope", "full"))', source)
        self.assertIn("build_report(checks, scope)", source)


if __name__ == "__main__":
    unittest.main()
