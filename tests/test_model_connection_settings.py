import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from utils import config_api
from utils.system_diagnostics import probe_remote_provider


class ModelConnectionSettingsTests(unittest.TestCase):
    @staticmethod
    def _route(
        source: str,
        *,
        local_model: str,
        local_url: str,
        remote_provider: str,
        remote_model: str,
        remote_url: str,
    ) -> dict:
        return {
            "source": source,
            "local": {"model": local_model, "base_url": local_url},
            "remote": {
                "provider": remote_provider,
                "model": remote_model,
                "base_url": remote_url,
            },
        }

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

    def test_legacy_config_migrates_once_and_survives_three_restart_cycles(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            config_path.write_text(
                "model:\n  provider: local\n  name: legacy-local\n  local:\n    name: legacy-local\n    base_url: http://127.0.0.1:11435/v1\n",
                encoding="utf-8",
            )
            with patch.object(config_api, "CONFIG_PATH", config_path):
                for _ in range(3):
                    loaded = config_api.load_config()
                    self.assertEqual(loaded["schema_version"], config_api.CONFIG_SCHEMA_VERSION)
                    self.assertEqual(loaded["model"]["local"]["name"], "legacy-local")
                    config_api.save_config(loaded)

                persisted = config_api.yaml.safe_load(config_path.read_text(encoding="utf-8"))

            self.assertEqual(persisted["schema_version"], config_api.CONFIG_SCHEMA_VERSION)
            self.assertEqual(persisted["model"]["local"]["name"], "legacy-local")

    def test_atomic_save_failure_preserves_the_previous_config(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            with patch.object(config_api, "CONFIG_PATH", config_path):
                original = config_api._default_config()
                original["model"]["local"]["name"] = "before"
                config_api.save_config(original)
                original_bytes = config_path.read_bytes()
                changed = config_api.load_config()
                changed["model"]["local"]["name"] = "after"
                real_replace = config_api.os.replace

                def fail_target_replace(source, destination):
                    if Path(destination) == config_path:
                        raise OSError("simulated atomic replace failure")
                    return real_replace(source, destination)

                with patch.object(config_api.os, "replace", side_effect=fail_target_replace):
                    with self.assertRaisesRegex(OSError, "atomic replace"):
                        config_api.save_config(changed)

            self.assertEqual(config_path.read_bytes(), original_bytes)
            self.assertEqual(config_api.yaml.safe_load(original_bytes)["model"]["local"]["name"], "before")
            self.assertEqual(list(Path(root).glob("config.yaml.*")), [])

    def test_concurrent_saves_leave_one_complete_version(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            errors: list[BaseException] = []
            barrier = threading.Barrier(3)

            def save(name: str) -> None:
                try:
                    config = config_api._default_config()
                    config["model"]["local"]["name"] = name
                    barrier.wait(timeout=1)
                    config_api.save_config(config)
                except BaseException as error:
                    errors.append(error)

            with patch.object(config_api, "CONFIG_PATH", config_path):
                workers = [threading.Thread(target=save, args=(name,)) for name in ("alpha", "beta")]
                for worker in workers:
                    worker.start()
                barrier.wait(timeout=1)
                for worker in workers:
                    worker.join(timeout=2)
                loaded = config_api.load_config()

            self.assertEqual(errors, [])
            self.assertIn(loaded["model"]["local"]["name"], {"alpha", "beta"})
            self.assertEqual(loaded["schema_version"], config_api.CONFIG_SCHEMA_VERSION)
            self.assertEqual(list(Path(root).glob("config.yaml.*")), [])

    def test_load_recovers_an_interrupted_config_transaction(self):
        for recovery_source, target_state in (("bak", "corrupt"), ("tmp", "missing")):
            with self.subTest(source=recovery_source), tempfile.TemporaryDirectory() as root:
                config_path = Path(root) / "config.yaml"
                recovered = config_api._default_config()
                recovered["model"]["local"]["name"] = f"recovered-{recovery_source}"
                sidecar = Path(str(config_path) + f".{recovery_source}")
                sidecar.write_text(
                    config_api.yaml.safe_dump(recovered, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )
                Path(str(config_path) + ".journal").write_text("{}", encoding="utf-8")
                if target_state == "corrupt":
                    config_path.write_text("model: [", encoding="utf-8")

                with patch.object(config_api, "CONFIG_PATH", config_path):
                    loaded = config_api.load_config()

                self.assertEqual(loaded["model"]["local"]["name"], f"recovered-{recovery_source}")
                self.assertEqual(list(Path(root).glob("config.yaml.*")), [])

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

    def test_all_live_code_source_combinations_round_trip_without_cross_overwrite(self):
        matrices = (
            ("local", "remote"),
            ("remote", "local"),
            ("remote", "remote"),
            ("local", "local"),
        )
        for live_source, code_source in matrices:
            with self.subTest(live=live_source, code=code_source):
                with tempfile.TemporaryDirectory() as root:
                    config_path = Path(root) / "config.yaml"
                    live_route = self._route(
                        live_source,
                        local_model="live-local:1",
                        local_url="http://127.0.0.1:11435/v1",
                        remote_provider="deepseek",
                        remote_model="live-cloud",
                        remote_url="https://api.deepseek.com/v1",
                    )
                    code_route = self._route(
                        code_source,
                        local_model="code-local:2",
                        local_url="http://127.0.0.1:22435/v1",
                        remote_provider="openai",
                        remote_model="code-cloud",
                        remote_url="https://api.openai.com/v1",
                    )
                    with patch.object(config_api, "CONFIG_PATH", config_path):
                        result = config_api.set_model_connection_settings({
                            "share_live_code": False,
                            "routes": {"live": live_route, "code": code_route},
                        })
                        reloaded_live = config_api.get_model_route("live")
                        reloaded_code = config_api.get_model_route("code")

                        self.assertTrue(result["applied"])
                        self.assertEqual(reloaded_live, live_route)
                        self.assertEqual(reloaded_code, code_route)

                        changed_code = copy.deepcopy(code_route)
                        changed_code["source"] = "remote" if code_source == "local" else "local"
                        changed_code["local"]["model"] = "code-only-change:3"
                        changed_code["remote"]["model"] = "code-only-cloud-change"
                        updated = config_api.set_model_connection_settings({
                            "share_live_code": False,
                            "routes": {"code": changed_code},
                        })

                        self.assertTrue(updated["applied"])
                        self.assertEqual(config_api.get_model_route("live"), live_route)
                        self.assertEqual(config_api.get_model_route("code"), changed_code)

    def test_shared_route_uses_live_as_single_profile_then_can_split_without_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            config_path = Path(root) / "config.yaml"
            live_route = self._route(
                "remote",
                local_model="shared-local",
                local_url="http://127.0.0.1:11435/v1",
                remote_provider="deepseek",
                remote_model="shared-cloud",
                remote_url="https://api.deepseek.com/v1",
            )
            ignored_code = self._route(
                "local",
                local_model="must-not-win",
                local_url="http://127.0.0.1:22435/v1",
                remote_provider="openai",
                remote_model="must-not-win",
                remote_url="https://api.openai.com/v1",
            )
            with patch.object(config_api, "CONFIG_PATH", config_path):
                shared = config_api.set_model_connection_settings({
                    "share_live_code": True,
                    "routes": {"live": live_route, "code": ignored_code},
                })
                self.assertTrue(shared["applied"])
                self.assertEqual(config_api.get_model_route("live"), live_route)
                self.assertEqual(config_api.get_model_route("code"), live_route)

                split_code = copy.deepcopy(ignored_code)
                split = config_api.set_model_connection_settings({
                    "share_live_code": False,
                    "routes": {"code": split_code},
                })
                self.assertTrue(split["applied"])
                self.assertEqual(config_api.get_model_route("live"), live_route)
                self.assertEqual(config_api.get_model_route("code"), split_code)

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
