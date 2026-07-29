import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import config_api
from utils.system_diagnostics import probe_remote_provider


class ModelConnectionSettingsTests(unittest.TestCase):
    def test_settings_round_trip_keeps_local_and_remote_profiles(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            with patch.object(config_api, "CONFIG_PATH", config_path):
                result = config_api.set_model_connection_settings({
                    "source": "remote",
                    "local": {
                        "model": "qwen2.5:7b",
                        "base_url": "http://127.0.0.1:11434/v1",
                    },
                    "remote": {
                        "provider": "deepseek",
                        "model": "deepseek-chat",
                        "base_url": "https://api.deepseek.com/v1",
                        "api_key": "secret-value",
                    },
                })
                settings = config_api.get_model_connection_settings()
                persisted = config_path.read_text(encoding="utf-8")

        self.assertTrue(result["applied"])
        self.assertEqual(settings["source"], "remote")
        self.assertEqual(settings["local"]["model"], "qwen2.5:7b")
        self.assertEqual(settings["remote"]["provider"], "deepseek")
        self.assertEqual(settings["remote"]["model"], "deepseek-chat")
        self.assertTrue(settings["remote"]["has_key"])
        self.assertNotIn("api_key", settings["remote"])
        self.assertNotIn("secret-value", json.dumps(settings, ensure_ascii=False))
        self.assertNotIn("secret-value", persisted)

    def test_invalid_model_endpoint_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            with patch.object(config_api, "CONFIG_PATH", config_path):
                result = config_api.set_model_connection_settings({
                    "source": "local",
                    "local": {
                        "model": "qwen2.5:7b",
                        "base_url": "file:///tmp/not-an-api",
                    },
                })

        self.assertFalse(result["applied"])
        self.assertIn("HTTP", result["error"])
        self.assertFalse(config_path.exists())

    def test_remote_probe_reports_authentication_failure_without_leaking_key(self):
        class Unauthorized:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, *_):
                return b"{}"

        from urllib.error import HTTPError

        error = HTTPError(
            "https://api.deepseek.com/v1/models",
            401,
            "Unauthorized",
            {},
            None,
        )
        with patch("utils.system_diagnostics.urllib.request.urlopen", side_effect=error):
            result = probe_remote_provider(
                "deepseek",
                "https://api.deepseek.com/v1",
                "private-key",
                "deepseek-chat",
            )

        self.assertEqual(result["id"], "remote_model_connection")
        self.assertEqual(result["status"], "fail")
        self.assertIn("认证", result["message"])
        self.assertNotIn("private-key", result["message"])


if __name__ == "__main__":
    unittest.main()
