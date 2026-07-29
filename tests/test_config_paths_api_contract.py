import unittest
from pathlib import Path


class ConfigPathApiContractTests(unittest.TestCase):
    def test_main_exposes_paths_and_uses_configured_storage_roots(self):
        source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/config/paths")', source)
        self.assertIn('@app.post("/api/config/paths")', source)
        self.assertIn('get_path_settings()["output_dir"]', source)
        self.assertIn('get_path_settings()["workspace_dir"]', source)


if __name__ == "__main__":
    unittest.main()
