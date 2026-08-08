import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import config_api
from utils.system_diagnostics import probe_remote_provider


class ModelConnectionSettingsTests(unittest.TestCase):
    def test_all_remote_providers_expose_full_compatible_model_catalogs(self):
        settings = config_api.get_model_connection_settings()
        catalogs = {provider["id"]: provider["models"] for provider in settings["providers"]}

        self.assertEqual(set(catalogs), set(config_api.CLOUD_PROVIDERS))
        self.assertEqual(len(catalogs["deepseek"]), 2)
        self.assertGreaterEqual(len(catalogs["glm"]), 13)
        self.assertGreaterEqual(len(catalogs["kimi"]), 10)
        self.assertGreaterEqual(len(catalogs["qwen"]), 12)
        self.assertGreaterEqual(len(catalogs["openai"]), 15)
        self.assertGreaterEqual(len(catalogs["anthropic"]), 9)
        self.assertIn("deepseek-v4-pro", {model["id"] for model in catalogs["deepseek"]})
        self.assertIn("glm-5.2", {model["id"] for model in catalogs["glm"]})
        self.assertIn("gpt-5.2", {model["id"] for model in catalogs["openai"]})
        self.assertIn("claude-opus-5", {model["id"] for model in catalogs["anthropic"]})

    def test_provider_catalog_sync_merges_account_models_without_exposing_key(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, *_):
                return json.dumps({"data": [{"id": "account-preview-model"}]}).encode()

        with patch.object(config_api.request, "urlopen", return_value=Response()) as urlopen:
            result = config_api.discover_remote_provider_models(
                "deepseek",
                "https://api.deepseek.com/v1",
                "private-key",
            )

        self.assertTrue(result["connected"])
        self.assertIn("account-preview-model", {model["id"] for model in result["models"]})
        self.assertNotIn("private-key", json.dumps(result, ensure_ascii=False))
        sent_request = urlopen.call_args.args[0]
        self.assertEqual(sent_request.headers["Authorization"], "Bearer private-key")

    def test_live_and_code_routes_can_be_independent_or_shared(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            with patch.object(config_api, "CONFIG_PATH", config_path):
                result = config_api.set_model_connection_settings({
                    "share_live_code": False,
                    "routes": {
                        "live": {
                            "source": "local",
                            "local": {"model": "qwen2.5:7b", "base_url": "http://127.0.0.1:11435/v1"},
                            "remote": {"provider": "deepseek", "model": "deepseek-chat", "base_url": "https://api.deepseek.com/v1"},
                        },
                        "code": {
                            "source": "remote",
                            "local": {"model": "deepseek-r1:8b", "base_url": "http://127.0.0.1:11435/v1"},
                            "remote": {"provider": "openai", "model": "gpt-4o", "base_url": "https://api.openai.com/v1"},
                        },
                    },
                })
                live = config_api.get_model_route("live")
                code = config_api.get_model_route("code")

                self.assertTrue(result["applied"])
                self.assertEqual(live["source"], "local")
                self.assertEqual(code["source"], "remote")
                self.assertEqual(code["remote"]["provider"], "openai")

                config_api.set_model_connection_settings({
                    "share_live_code": True,
                    "routes": result["routes"],
                })
                self.assertEqual(config_api.get_model_route("code"), config_api.get_model_route("live"))

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

    def test_retired_javis_default_moves_to_managed_port_without_overriding_custom_urls(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            with (
                patch.object(config_api, "CONFIG_PATH", config_path),
                patch.dict("os.environ", {"JAVIS_BUNDLED_OLLAMA_URL": "http://127.0.0.1:11435/v1"}),
            ):
                legacy = config_api.set_model_connection_settings({
                    "source": "local",
                    "local": {"model": "qwen2.5:7b", "base_url": "http://localhost:11434/v1"},
                })
                self.assertTrue(legacy["applied"])
                self.assertEqual(
                    config_api.get_model_connection_settings()["local"]["base_url"],
                    "http://127.0.0.1:11435/v1",
                )
                custom = config_api.set_model_connection_settings({
                    "source": "local",
                    "local": {"model": "qwen2.5:7b", "base_url": "http://192.168.1.20:11434/v1"},
                })
                self.assertTrue(custom["applied"])
                self.assertEqual(
                    config_api.get_model_connection_settings()["local"]["base_url"],
                    "http://192.168.1.20:11434/v1",
                )

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
