import asyncio
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from core import llm_client
from core.agent import Agent, _model_failure_event
from core.engine import InferenceEngine
from core.llm_client import (
    LLMResponse,
    ModelRuntimeUnavailableError,
    ModelSetupRequiredError,
)
from utils import config_api


ROOT = Path(__file__).resolve().parents[1]


def route_config(*, local_model: str, source: str = "local") -> dict:
    return {
        "model": {
            "provider": "local" if source == "local" else "openai",
            "name": local_model if source == "local" else "code-cloud",
            "share_live_code": False,
            "local": {
                "name": local_model,
                "base_url": "http://127.0.0.1:22435/v1",
                "api_key": "ollama",
            },
            "routes": {
                "live": {
                    "source": "local",
                    "local": {
                        "model": "live-local",
                        "base_url": "http://127.0.0.1:11435/v1",
                    },
                    "remote": {
                        "provider": "deepseek",
                        "model": "live-cloud",
                        "base_url": "https://api.deepseek.com/v1",
                    },
                },
                "code": {
                    "source": source,
                    "local": {
                        "model": local_model,
                        "base_url": "http://127.0.0.1:22435/v1",
                    },
                    "remote": {
                        "provider": "openai",
                        "model": "code-cloud",
                        "base_url": "https://api.openai.com/v1",
                    },
                },
            },
        }
    }


class CleanInstallModelSetupTests(unittest.TestCase):
    def test_clean_install_has_no_phantom_local_model_and_requires_setup(self):
        with tempfile.TemporaryDirectory() as root, \
            patch.object(config_api, "CONFIG_PATH", Path(root) / "missing-config.yaml"), \
            patch.dict(
                os.environ,
                {"JAVIS_BUNDLED_MODEL": "", "JAVIS_BUNDLED_OLLAMA_URL": ""},
                clear=False,
            ):
            config = config_api._default_config()
            settings = config_api.get_model_connection_settings()

        self.assertEqual(config["model"]["name"], "")
        self.assertEqual(config["model"]["local"]["name"], "")
        self.assertEqual(settings["active_model"], "")
        self.assertTrue(settings["setup_required"])
        self.assertEqual(settings["setup_recommended_action"], "install_local_model")
        self.assertEqual(settings["setup_required_routes"], ["live", "code"])

    def test_explicit_bundled_model_environment_remains_supported(self):
        with tempfile.TemporaryDirectory() as root, \
            patch.object(config_api, "CONFIG_PATH", Path(root) / "missing-config.yaml"), \
            patch.dict(
                os.environ,
                {
                    "JAVIS_BUNDLED_MODEL": "addon-model:8b",
                    "JAVIS_BUNDLED_OLLAMA_URL": "http://127.0.0.1:11435/v1",
                },
                clear=False,
            ):
            config = config_api._default_config()
            settings = config_api.get_model_connection_settings()

        self.assertEqual(config["model"]["local"]["name"], "addon-model:8b")
        self.assertFalse(settings["setup_required"])

    def test_legacy_local_name_migrates_without_becoming_a_new_default(self):
        merged = config_api._merge_defaults({
            "model": {"provider": "local", "name": "legacy-local:7b"},
        })

        self.assertEqual(merged["model"]["local"]["name"], "legacy-local:7b")

    def test_packaged_config_does_not_reintroduce_r1_as_a_default(self):
        if config_api.yaml is None:
            self.skipTest("PyYAML unavailable")
        config = config_api.yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(config["model"]["name"], "")
        self.assertEqual(config["model"]["local"]["name"], "")


class LlmFailFastTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_local_route_fails_without_importing_or_contacting_provider(self):
        config = route_config(local_model="", source="local")
        config["model"]["routes"]["live"]["local"]["model"] = ""
        config["model"]["local"]["name"] = ""
        config["model"]["name"] = ""
        with patch.object(llm_client, "lcfg", return_value=config):
            client = llm_client.LLMClient()

        started = time.monotonic()
        with patch.object(
            client,
            "_ensure_connected",
            side_effect=AssertionError("unconfigured routes must not contact a provider"),
        ):
            with self.assertRaisesRegex(RuntimeError, "model_setup_required"):
                await client.chat_with_tools([], [], "system")

        self.assertLess(time.monotonic() - started, 0.2)
        self.assertFalse(client.is_ready)
        self.assertTrue(client.setup_required)

    async def test_offline_ollama_is_probed_once_without_retry_sleep(self):
        calls = 0

        class FakeAsyncClient:
            def __init__(self, *_, **__):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, *_args, **_kwargs):
                nonlocal calls
                calls += 1
                raise OSError("offline")

        client = object.__new__(llm_client.LLMClient)
        client.provider = "local"
        client.model = "configured:7b"
        client.base_url = "http://127.0.0.1:11435/v1"
        fake_httpx = types.SimpleNamespace(AsyncClient=FakeAsyncClient)

        with patch.dict(sys.modules, {"httpx": fake_httpx}), (
            patch.object(asyncio, "sleep", side_effect=AssertionError("must not retry-sleep"))
        ):
            started = time.monotonic()
            with self.assertRaisesRegex(RuntimeError, "model_runtime_unavailable"):
                await client._ensure_connected()

        self.assertEqual(calls, 1)
        self.assertLess(time.monotonic() - started, 0.2)

    async def test_connected_ollama_without_selected_model_opens_setup_path(self):
        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"models": [{"name": "another-model:latest"}]}

        class FakeAsyncClient:
            def __init__(self, *_, **__):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, *_args, **_kwargs):
                return Response()

        client = object.__new__(llm_client.LLMClient)
        client.provider = "local"
        client.model = "selected-model:7b"
        client.base_url = "http://127.0.0.1:11435/v1"

        with patch.dict(sys.modules, {"httpx": types.SimpleNamespace(AsyncClient=FakeAsyncClient)}):
            with self.assertRaisesRegex(RuntimeError, "model_setup_required"):
                await client._ensure_connected()


class RouteScopedFallbackTests(unittest.TestCase):
    def test_code_fallback_uses_code_local_profile_instead_of_hardcoded_r1(self):
        class FakeLlm:
            provider = "openai"
            model = "code-cloud"
            route_name = "code"
            config = route_config(local_model="code-local:9b", source="remote")

            def switch_provider(self, provider, model, base_url=""):
                self.provider = provider
                self.model = model
                self.base_url = base_url

        client = FakeLlm()
        engine = InferenceEngine(client)
        engine._switch_to_local()

        self.assertEqual(client.provider, "local")
        self.assertEqual(client.model, "code-local:9b")
        self.assertEqual(client.base_url, "http://127.0.0.1:22435/v1")

    def test_missing_route_local_fallback_is_actionable_instead_of_phantom_r1(self):
        class FakeLlm:
            provider = "openai"
            model = "code-cloud"
            route_name = "code"
            config = route_config(local_model="", source="remote")

            def switch_provider(self, *_args, **_kwargs):
                raise AssertionError("must not synthesize a local model")

        with self.assertRaisesRegex(RuntimeError, "model_setup_required"):
            InferenceEngine(FakeLlm())._switch_to_local()


class BoundedLocalFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_engine_calls_an_already_local_route_only_once(self):
        class OfflineLocalLlm:
            provider = "local"
            model = "selected-model:7b"
            route_name = "live"
            config = route_config(local_model="selected-model:7b")

            def __init__(self):
                self.calls = 0

            async def chat_with_tools(self, *_args, **_kwargs):
                self.calls += 1
                raise ModelRuntimeUnavailableError(
                    "http://127.0.0.1:11435",
                    "offline",
                )

        llm = OfflineLocalLlm()

        with self.assertRaisesRegex(RuntimeError, "model_runtime_unavailable"):
            await InferenceEngine(llm).chat_with_fallback([], [], "system")

        self.assertEqual(llm.calls, 1)

    async def test_agent_does_not_retry_a_terminal_local_runtime_failure(self):
        class EmptyTools:
            def get_schemas(self):
                return []

            def get(self, _name):
                return None

        class OfflineEngine:
            def __init__(self):
                self.calls = 0

            async def chat_with_fallback(self, *_args, **_kwargs):
                self.calls += 1
                raise ModelRuntimeUnavailableError(
                    "http://127.0.0.1:11435",
                    "offline",
                )

        engine = OfflineEngine()
        agent = Agent(object(), EmptyTools(), engine=engine)
        agent.max_retries = 3
        events = [
            event
            async for event in agent.chat(
                "perform a local runtime availability check",
                interaction_mode="code",
                conversation_cards=[],
            )
        ]

        errors = [event for event in events if event.get("type") == "error"]
        self.assertEqual(engine.calls, 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].get("code"), "model_runtime_unavailable")
        self.assertEqual(errors[0].get("recovery_action"), "restart_local_runtime")
        self.assertEqual(errors[0].get("route"), "code")
        self.assertEqual(errors[0].get("reason"), "managed_runtime_offline")
        self.assertNotIn("base_url", errors[0])
        self.assertNotIn("127.0.0.1:11435", str(errors[0]))
        self.assertIn("Model & Storage", str(errors[0].get("message")))

    def test_external_runtime_failure_opens_route_settings_without_leaking_endpoint(self):
        endpoint = "https://user:secret@example.invalid:11434/v1?token=private"
        event = _model_failure_event(
            ModelRuntimeUnavailableError(endpoint, "connection refused"),
            "code",
        )

        self.assertEqual(event.get("recovery_action"), "open_model_settings")
        self.assertEqual(event.get("route"), "code")
        self.assertEqual(event.get("reason"), "external_runtime_unavailable")
        self.assertNotIn("base_url", event)
        self.assertNotIn(endpoint, str(event))

    async def test_live_fast_path_returns_model_setup_action_without_falling_through(self):
        class EmptyTools:
            def get_schemas(self):
                return []

            def get(self, _name):
                return None

        class MissingModelEngine:
            def __init__(self):
                self.brief_calls = 0
                self.full_calls = 0

            async def chat_brief_with_fallback(self, *_args, **_kwargs):
                self.brief_calls += 1
                raise ModelSetupRequiredError("live", "selected_local_model_not_installed")

            async def chat_with_fallback(self, *_args, **_kwargs):
                self.full_calls += 1
                return LLMResponse(text="must not be reached"), None

        engine = MissingModelEngine()
        agent = Agent(object(), EmptyTools(), engine=engine)
        agent.max_retries = 3
        events = [
            event
            async for event in agent.chat(
                "你好，能听见吗",
                interaction_mode="live",
                conversation_cards=[],
            )
        ]

        errors = [event for event in events if event.get("type") == "error"]
        self.assertEqual(engine.brief_calls, 1)
        self.assertEqual(engine.full_calls, 0)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].get("code"), "model_setup_required")
        self.assertEqual(errors[0].get("recovery_action"), "open_model_settings")
        self.assertEqual(errors[0].get("route"), "live")
        self.assertEqual(errors[0].get("reason"), "selected_local_model_not_installed")


class DesktopLifecycleContractTests(unittest.TestCase):
    def test_sidecar_does_not_inject_a_phantom_r1_and_reports_ollama_recovery(self):
        source = (ROOT / "app" / "src-tauri" / "src" / "sidecar.rs").read_text(encoding="utf-8")
        self.assertNotIn('.env("JAVIS_BUNDLED_MODEL", "deepseek-r1:8b")', source)
        self.assertIn("ollama_installed", source)
        self.assertIn("ollama_recovery", source)
        self.assertNotIn("let ollama_state = self.ollama.start(app)?;", source)

    def test_desktop_opens_model_storage_wizard_for_unconfigured_runtime(self):
        source = (ROOT / "app" / "src" / "main.ts").read_text(encoding="utf-8")
        self.assertIn("setup_required", source)
        self.assertIn('settingsSurface?.open("live", "storage")', source)
        self.assertIn("snapshot.ollama", source)

    def test_ollama_recovery_is_backgrounded_and_reports_its_lifecycle(self):
        source = (ROOT / "app" / "src-tauri" / "src" / "sidecar.rs").read_text(
            encoding="utf-8"
        )
        ensure = source.split("fn ensure_ollama_started", 1)[1].split(
            "pub fn start", 1
        )[0]

        self.assertIn("thread::spawn", ensure)
        self.assertIn("ollama_startup", source)
        for state in ('"starting"', '"ready"', '"failed"', '"not-installed"'):
            self.assertIn(state, source)

    def test_stale_ollama_start_worker_cannot_overwrite_a_new_restart_generation(self):
        source = (ROOT / "app" / "src-tauri" / "src" / "sidecar.rs").read_text(
            encoding="utf-8"
        )
        bundled_source = (
            ROOT / "app" / "src-tauri" / "src" / "bundled_ollama.rs"
        ).read_text(encoding="utf-8")

        self.assertIn("ollama_start_generation", source)
        self.assertIn("runtime.ollama_start_generation == start_generation", source)
        stop_body = source.split("pub fn stop_owned", 1)[1].split(
            "pub fn restart", 1
        )[0]
        self.assertIn("ollama_start_generation", stop_body)
        self.assertIn('ollama_startup = "idle"', stop_body)
        self.assertIn("generation: u64", bundled_source)
        self.assertIn("child_generation: Option<u64>", bundled_source)
        self.assertIn("fn stop_generation", bundled_source)
        self.assertIn("runtime.generation != start_generation", bundled_source)
        self.assertIn("runtime.child_generation != Some(start_generation)", bundled_source)

    def test_python_backend_is_spawned_before_ollama_recovery_is_scheduled(self):
        source = (ROOT / "app" / "src-tauri" / "src" / "sidecar.rs").read_text(
            encoding="utf-8"
        )
        start_body = source.split("pub fn start", 1)[1].split(
            "pub fn stop_owned", 1
        )[0]
        offline_path = start_body.split("ProbeResult::Offline => {}", 1)[1]

        self.assertLess(
            offline_path.index("command.spawn()"),
            offline_path.index("self.ensure_ollama_started(app)"),
        )


if __name__ == "__main__":
    unittest.main()
