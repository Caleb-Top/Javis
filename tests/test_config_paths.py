import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from core.engine import InferenceEngine
from utils import config_api


class ConfigPathSettingsTests(unittest.TestCase):
    def test_engine_status_uses_explicit_configured_model_directory(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            config_path = base / "config.yaml"
            configured_models = base / "configured-models"
            config_path.write_text(
                "paths:\n"
                "  model_dir: configured-models\n",
                encoding="utf-8",
            )
            llm = type("StubLlm", (), {"provider": "local", "model": "test-model"})()

            with patch.object(config_api, "CONFIG_PATH", config_path), patch.dict(
                os.environ,
                {"OLLAMA_MODELS": str(base / "stale-models")},
            ):
                status = InferenceEngine(llm).get_power_status()

        self.assertEqual(status["local_model_path"], str(configured_models.resolve()))

    def test_configured_relative_paths_are_rooted_at_the_config_file(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            config_path.write_text(
                "paths:\n"
                "  workspace_dir: custom-workspace\n",
                encoding="utf-8",
            )

            with patch.object(config_api, "CONFIG_PATH", config_path):
                paths = config_api.get_path_settings()

            self.assertEqual(
                paths["workspace_dir"],
                str((config_path.parent / "custom-workspace").resolve()),
            )

    def test_path_settings_round_trip_as_absolute_existing_directories(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            config_path = base / "config.yaml"
            selected = {}
            for key in ("model_dir", "workspace_dir", "output_dir", "backup_dir"):
                directory = base / key
                directory.mkdir()
                selected[key] = str(directory)

            with patch.object(config_api, "CONFIG_PATH", config_path):
                result = config_api.set_path_settings(selected)
                self.assertTrue(result["applied"])
                self.assertEqual(config_api.get_path_settings(), selected)

    def test_path_settings_reject_relative_or_missing_directories(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            with patch.object(config_api, "CONFIG_PATH", config_path):
                relative = config_api.set_path_settings({"workspace_dir": "relative/path"})
                missing = config_api.set_path_settings({
                    "workspace_dir": str(Path(root) / "missing"),
                })

            self.assertFalse(relative["applied"])
            self.assertIn("绝对路径", relative["error"])
            self.assertFalse(missing["applied"])
            self.assertIn("不存在", missing["error"])

    def test_saving_the_same_model_directory_does_not_request_restart(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            config_path = base / "config.yaml"
            model_dir = base / "models"
            model_dir.mkdir()

            with patch.object(config_api, "CONFIG_PATH", config_path):
                first = config_api.set_path_settings({"model_dir": str(model_dir)})
                second = config_api.set_path_settings({"model_dir": str(model_dir)})

            self.assertEqual(first["restart_required"], ["model_dir"])
            self.assertEqual(second["restart_required"], [])
            self.assertEqual(second["changed"], [])


if __name__ == "__main__":
    unittest.main()
