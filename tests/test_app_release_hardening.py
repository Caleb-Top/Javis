import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class AppReleaseHardeningTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (APP / relative).read_text(encoding="utf-8")

    def test_sidecar_manager_has_probe_ownership_and_bounded_restart(self):
        sidecar = self.read("src-tauri/src/sidecar.rs")
        backend = (ROOT / "main.py").read_text(encoding="utf-8")

        for contract in [
            "struct SidecarManager",
            "ownership_token",
            "owned_pid",
            "probe_backend",
            "TcpStream::connect_timeout",
            "JAVIS_PYTHON",
            "CREATE_NO_WINDOW",
            "MAX_RESTART_ATTEMPTS",
            "stop_owned",
        ]:
            self.assertIn(contract, sidecar)
        self.assertNotIn("/IM python", sidecar)
        self.assertNotIn("taskkill //F //IM python.exe", sidecar)
        self.assertIn('root.join("main.py")', sidecar)
        self.assertIn('.arg("-u")', sidecar)
        self.assertNotIn('root.join("start.py")', sidecar)
        self.assertIn('service\\\":\\\"javis', sidecar)
        self.assertIn('s["service"]="javis"', backend)

    def test_tauri_exposes_sidecar_commands_and_lifecycle_hooks(self):
        main = self.read("src-tauri/src/main.rs")

        for command in ["sidecar_status", "sidecar_start", "sidecar_stop", "sidecar_restart"]:
            self.assertIn(command, main)
        self.assertIn("generate_handler!", main)
        self.assertIn("SidecarManager", main)
        self.assertIn("RunEvent::Exit", main)

    def test_tray_close_to_hide_and_explicit_quit_are_present(self):
        main = self.read("src-tauri/src/main.rs")

        self.assertIn("TrayIconBuilder", main)
        for menu_id in ['"show"', '"pause"', '"restart"', '"diagnostics"', '"quit"']:
            self.assertIn(menu_id, main)
        self.assertIn("WindowEvent::CloseRequested", main)
        self.assertIn("api.prevent_close()", main)
        self.assertIn("window.hide()", main)
        self.assertIn("explicit_quit", main)

    def test_appdata_logger_rotates_and_redacts(self):
        logger = self.read("src-tauri/src/app_log.rs")
        main = self.read("src-tauri/src/main.rs")

        self.assertIn("app_data_dir", logger)
        self.assertIn('.join("logs")', logger)
        self.assertIn('.join("app")', logger)
        self.assertIn("10 * 1024 * 1024", logger)
        self.assertIn("REDACTED", logger)
        self.assertIn("write_app_log", main)

    def test_csp_is_local_only_and_not_null(self):
        config = json.loads(self.read("src-tauri/tauri.conf.json"))
        csp = config["app"]["security"]["csp"]

        self.assertIsInstance(csp, str)
        self.assertIn("default-src 'self'", csp)
        self.assertIn("http://127.0.0.1:8080", csp)
        self.assertIn("ws://127.0.0.1:8080", csp)
        self.assertNotIn("*", csp)

    def test_frontend_connects_sidecar_error_boundary_and_diagnostics(self):
        sidecar = self.read("src/bridge/sidecarClient.ts")
        boundary = self.read("src/app/AppErrorBoundary.ts")
        diagnostics = self.read("src/panels/DiagnosticsPanel.ts")
        main = self.read("src/main.ts")

        self.assertIn('@tauri-apps/api/core', sidecar)
        for command in ["sidecar_status", "sidecar_start", "sidecar_stop", "sidecar_restart"]:
            self.assertIn(command, sidecar)
        self.assertIn("await sidecar.status()", main)
        self.assertNotIn("sidecar.ensureStarted()", main)
        self.assertIn('addEventListener("error"', boundary)
        self.assertIn('addEventListener("unhandledrejection"', boundary)
        self.assertIn("恢复 Live", boundary)
        self.assertIn("/api/runtime/status", diagnostics)
        self.assertIn("未提供自动安装", diagnostics)

    def test_release_hardening_does_not_add_install_commands(self):
        combined = "\n".join([
            self.read("src-tauri/src/main.rs"),
            self.read("src-tauri/src/sidecar.rs"),
            self.read("src/panels/DiagnosticsPanel.ts"),
        ])

        for forbidden in ["pnpm install", "npm install", "cargo install", "winget install", "ollama pull"]:
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
