import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class AppPackagingTests(unittest.TestCase):
    def test_app_environment_probe_reports_no_app_when_missing(self):
        from scripts.app_env_probe import probe_app_environment

        result = probe_app_environment(ROOT / "_missing_app_probe_root")

        self.assertFalse(result["app_dir_exists"])
        self.assertIn("node", result)
        self.assertIn("rustc", result)
        self.assertIn("cargo", result)

    def test_package_manifest_excludes_large_runtime_dirs(self):
        from scripts.app_package_manifest import build_app_package_manifest

        result = build_app_package_manifest(ROOT)

        self.assertIn("core", result["include"])
        self.assertIn("blueprint", result["include"])
        self.assertIn("tools", result["include"])
        self.assertIn("web", result["include"])
        self.assertNotIn("start.py", result["include"])
        self.assertIn("venv", result["exclude"])
        self.assertIn("ollama_models", result["external"])
        self.assertIn("data", result["external"])

    def test_app_scaffold_has_live_entry_files(self):
        root = ROOT / "app"

        self.assertTrue((root / "package.json").exists())
        self.assertTrue((root / "index.html").exists())
        self.assertTrue((root / "src/main.ts").exists())
        self.assertTrue((root / "src/styles.css").exists())

    def test_app_entry_is_live_orb_not_code_first(self):
        main = (ROOT / "app" / "src" / "main.ts").read_text(encoding="utf-8")
        stage = (ROOT / "app" / "src" / "live" / "LiveStage.ts").read_text(encoding="utf-8")

        self.assertIn("renderLiveStage", main)
        self.assertIn('data-surface", "live', main)
        self.assertIn("live-stage", stage)
        self.assertIn("live-orb", stage)
        self.assertIn("Javis 已待命", stage)
        self.assertNotIn("Code Surface", main)

    def test_live_state_file_defines_required_states(self):
        text = (ROOT / "app" / "src" / "live" / "liveState.ts").read_text(encoding="utf-8")

        for state in ["idle", "listening", "thinking", "speaking", "executing", "blocked", "error", "offline"]:
            self.assertIn(state, text)
        self.assertIn("setLiveState", text)

    def test_backend_client_connects_to_existing_ws(self):
        text = (ROOT / "app" / "src" / "bridge" / "backendClient.ts").read_text(encoding="utf-8")
        endpoints = (ROOT / "app" / "src" / "bridge" / "backendEndpoints.ts").read_text(encoding="utf-8")

        self.assertIn('DEFAULT_HTTP_ORIGIN = "http://127.0.0.1:8080"', endpoints)
        self.assertIn("VITE_JAVIS_BACKEND_URL", endpoints)
        self.assertIn("`${endpoints.websocket}/ws`", text)
        self.assertIn("recent_cards", text)
        self.assertIn("thinking", text)
        self.assertIn("executing", text)

    def test_tauri_config_is_app_first_and_not_web_first(self):
        cfg = json.loads((ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))

        self.assertEqual(cfg["productName"], "Javis")
        self.assertEqual(cfg["app"]["windows"][0]["title"], "Javis Live")
        self.assertEqual(cfg["build"]["frontendDist"], "../dist")

    def test_live_shell_has_voice_and_code_controls(self):
        main = (ROOT / "app" / "src" / "main.ts").read_text(encoding="utf-8")
        stage = (ROOT / "app" / "src" / "live" / "LiveStage.ts").read_text(encoding="utf-8")

        self.assertIn("voice-control", stage)
        self.assertIn("code-control", stage)
        self.assertIn("status-rail", stage)
        self.assertIn("openCodeSurface", main)

    def test_code_surface_is_hidden_by_default_and_summonable(self):
        code = (ROOT / "app" / "src" / "code" / "CodeSurface.ts").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "src" / "styles.css").read_text(encoding="utf-8")
        config = json.loads((ROOT / "app" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))

        self.assertIn("openCodeSurface", code)
        self.assertIn("closeCodeSurface", code)
        self.assertIn('data-surface="live"', code)
        self.assertIn("legacy-web-frame", code)
        self.assertIn('[data-surface="code"]', styles)
        self.assertIn("frame-src http://127.0.0.1:8080", config["app"]["security"]["csp"])

    def test_live_and_code_shell_install_window_drag_regions(self):
        main = (ROOT / "app" / "src" / "main.ts").read_text(encoding="utf-8")
        stage = (ROOT / "app" / "src" / "live" / "LiveStage.ts").read_text(encoding="utf-8")
        styles = (ROOT / "app" / "src" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("installWindowDragRegions", main)
        self.assertIn("window-drag-region", stage)
        self.assertIn("data-tauri-drag-region", stage)
        self.assertIn("data-tauri-drag-region", (ROOT / "app" / "src" / "code" / "CodeSurface.ts").read_text(encoding="utf-8"))
        self.assertIn("-webkit-app-region: drag", styles)
        self.assertIn("-webkit-app-region: no-drag", styles)

    def test_backend_client_checks_http_status(self):
        text = (ROOT / "app" / "src" / "bridge" / "backendClient.ts").read_text(encoding="utf-8")

        self.assertIn("/api/status", text)
        self.assertIn("checkBackendHealth", text)
        self.assertIn("offline", text)

    def test_live_voice_capture_sends_existing_voice_protocol(self):
        voice = (ROOT / "app" / "src" / "live" / "VoiceCapture.ts").read_text(encoding="utf-8")
        backend = (ROOT / "app" / "src" / "bridge" / "backendClient.ts").read_text(encoding="utf-8")
        main = (ROOT / "app" / "src" / "main.ts").read_text(encoding="utf-8")

        self.assertIn('"/api/voice/capture/start"', voice)
        self.assertNotIn("MediaRecorder", voice)
        self.assertNotIn("getUserMedia", voice)
        self.assertIn("sendVoice", backend)
        self.assertIn('"type": "voice"', backend)
        self.assertIn("createVoiceCapture", main)

    def test_static_preview_exists_without_build_dependencies(self):
        preview = ROOT / "app" / "preview.html"

        self.assertTrue(preview.exists())
        text = preview.read_text(encoding="utf-8")
        self.assertIn("live-orb", text)
        self.assertIn("Javis 已待命", text)
        self.assertIn("无需安装依赖", text)

    def test_app_launcher_prefers_live_app_and_preserves_web(self):
        launcher = ROOT / "scripts" / "start_javis_app.py"

        self.assertTrue(launcher.exists())
        text = launcher.read_text(encoding="utf-8")
        backend = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("app/preview.html", text)
        self.assertIn('ROOT / "main.py"', text)
        self.assertIn('env["PORT"]', text)
        self.assertIn('payload.get("service") == "javis"', text)
        self.assertNotIn('ROOT / "start.py"', text)
        self.assertIn('os.environ.get("PORT", sc.get("port", 8087))', backend)
        self.assertIn("web/ remains debug", text)
        self.assertNotIn("pip install", text)
        self.assertNotIn("npm install", text)
        self.assertNotIn("Remove-Item", text)

    def test_app_launcher_rejects_non_javis_status_service(self):
        from scripts.start_javis_app import backend_is_ready

        class Response:
            status = 200

            def __init__(self, payload: bytes):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return self.payload

        with patch("scripts.start_javis_app.urllib.request.urlopen", return_value=Response(b'{"service":"other"}')):
            self.assertFalse(backend_is_ready(8080))
        with patch("scripts.start_javis_app.urllib.request.urlopen", return_value=Response(b'{"service":"javis"}')):
            self.assertTrue(backend_is_ready(8080))


if __name__ == "__main__":
    unittest.main()
