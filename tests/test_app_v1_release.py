import json
import tomllib
import unittest
from pathlib import Path

from scripts.app_build_preflight import _shared_tool_root
from scripts.javis_release_version import load_version_contract


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TAURI = APP / "src-tauri"
CURRENT_VERSION = load_version_contract(ROOT)["version"]
DEPENDENCY_ROOT = _shared_tool_root(ROOT)


class AppReleaseContinuityTests(unittest.TestCase):
    def test_active_app_manifests_are_v3_and_v1_archive_is_preserved(self):
        package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
        tauri = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
        cargo = tomllib.loads((TAURI / "Cargo.toml").read_text(encoding="utf-8"))

        self.assertEqual(package["version"], CURRENT_VERSION)
        self.assertEqual(tauri["version"], CURRENT_VERSION)
        self.assertEqual(cargo["package"]["version"], CURRENT_VERSION)
        self.assertTrue(
            (DEPENDENCY_ROOT / "artifacts/Javis-v1.0.0-archive/Javis_1.0.0_x64-setup.exe").is_file()
        )

    def test_windows_test_bundle_is_enabled_with_existing_icon(self):
        config = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
        bundle = config["bundle"]

        self.assertTrue(bundle["active"])
        self.assertFalse(config["identifier"].endswith(".app"))
        self.assertIn("nsis", bundle["targets"])
        self.assertTrue(bundle["icon"])
        for icon in bundle["icon"]:
            self.assertTrue((TAURI / icon).resolve().is_file(), icon)

    def test_live_state_uses_single_static_coordinator_import(self):
        live_state = (APP / "src" / "live" / "liveState.ts").read_text(encoding="utf-8")

        self.assertIn('import { runtimeStateCoordinator }', live_state)
        self.assertNotIn('import("../state/RuntimeStateCoordinator")', live_state)

    def test_v1_release_manifest_declares_integrated_surfaces(self):
        manifest = json.loads((APP / "v1.manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(manifest["channel"], "test")
        self.assertEqual(
            set(manifest["surfaces"]),
            {"live-orb", "desktop-pet", "code", "control", "conversation"},
        )
        self.assertEqual(manifest["backend"]["port"], 8080)
        self.assertTrue(manifest["web_debug_preserved"])

    def test_launcher_prefers_built_v1_executable(self):
        launcher = (ROOT / "scripts" / "start_javis_app.py").read_text(encoding="utf-8")

        self.assertIn("find_built_app", launcher)
        self.assertIn("javis-app.exe", launcher)
        self.assertIn("x86_64-pc-windows-gnu", launcher)
        self.assertIn("subprocess.Popen([str(built_app)]", launcher)

    def test_v1_build_script_runs_preflight_and_never_installs(self):
        script = (ROOT / "scripts" / "build_javis_app_v1.ps1").read_text(encoding="utf-8")

        self.assertIn("app_build_preflight.py", script)
        self.assertIn("npm.cmd", script)
        self.assertIn("run build", script)
        self.assertIn("tauri build", script)
        self.assertIn("stable-x86_64-pc-windows-gnu", script)
        self.assertIn("javis-app.exe", script)
        for forbidden in ["npm install", "npm ci", "cargo install", "winget", "ollama pull"]:
            self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
