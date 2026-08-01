import unittest
from pathlib import Path
from unittest.mock import patch

from utils import config_api


ROOT = Path(__file__).resolve().parents[1]


class PortableWorkspacePathTests(unittest.TestCase):
    def test_relative_config_paths_resolve_from_config_directory(self):
        config_path = ROOT / "tmp" / "portable-path-test" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            "paths:\n"
            "  model_dir: ollama_models/models\n"
            "  workspace_dir: workspace\n"
            "  output_dir: uploads\n"
            "  backup_dir: backups\n",
            encoding="utf-8",
        )
        try:
            with patch.object(config_api, "CONFIG_PATH", config_path):
                paths = config_api.get_path_settings()
        finally:
            config_path.unlink(missing_ok=True)
            config_path.parent.rmdir()

        self.assertEqual(paths["model_dir"], str((config_path.parent / "ollama_models/models").resolve()))
        self.assertEqual(paths["workspace_dir"], str((config_path.parent / "workspace").resolve()))
        self.assertEqual(paths["output_dir"], str((config_path.parent / "uploads").resolve()))
        self.assertEqual(paths["backup_dir"], str((config_path.parent / "backups").resolve()))

    def test_active_project_files_do_not_bind_to_the_old_drive(self):
        forbidden = "D:" + "\\" + "Javis"
        forbidden_slash = "D:" + "/" + "Javis"
        candidates = [ROOT / "config.yaml", ROOT / "app" / "preview.html"]
        for directory, patterns in (
            (ROOT / "core", ("*.py",)),
            (ROOT / "utils", ("*.py",)),
            (ROOT / "scripts", ("*.py", "*.ps1")),
            (ROOT / "tests", ("*.py",)),
            (ROOT / "app" / "src", ("*.ts", "*.json")),
            (ROOT / "app" / "tests", ("*.ts",)),
        ):
            for pattern in patterns:
                candidates.extend(directory.rglob(pattern))

        offenders = []
        for path in candidates:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if forbidden.lower() in text.lower() or forbidden_slash.lower() in text.lower():
                offenders.append(str(path.relative_to(ROOT)))

        self.assertEqual(offenders, [])

    def test_g_rooted_development_environment_script_exists(self):
        script = ROOT / "scripts" / "javis_dev_env.ps1"
        self.assertTrue(script.is_file())
        text = script.read_text(encoding="utf-8")
        for name in (
            "JAVIS_ROOT",
            "PIP_CACHE_DIR",
            "PNPM_HOME",
            "CARGO_HOME",
            "CARGO_TARGET_DIR",
            "TEMP",
            "TMP",
        ):
            self.assertIn(name, text)
        self.assertNotIn("C:\\", text)
        self.assertNotIn("D:\\", text)


if __name__ == "__main__":
    unittest.main()
