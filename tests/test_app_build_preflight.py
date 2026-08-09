import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AppBuildPreflightTests(unittest.TestCase):
    def test_current_workspace_reports_precise_offline_readiness(self):
        from scripts.app_build_preflight import collect_build_preflight

        report = collect_build_preflight(ROOT)

        self.assertEqual(report["ready_for_offline_build"], not report["blockers"])
        self.assertEqual(
            "frontend-dependencies-missing" in report["blockers"],
            not report["checks"]["frontend_dependencies"],
        )
        self.assertEqual(
            "tauri-crate-cache-missing" in report["blockers"],
            not report["checks"]["tauri_crate_cached"],
        )
        self.assertNotIn("bundle-disabled", report["blockers"])
        self.assertNotIn("bundle-icon-missing", report["blockers"])
        self.assertTrue(report["checks"]["bundle_active"])
        self.assertTrue(report["checks"]["bundle_icons"])
        self.assertTrue(report["checks"]["bundled_node"])
        self.assertTrue(report["checks"]["cargo"])
        self.assertTrue(report["checks"]["rustc"])
        self.assertTrue(report["checks"]["optional_ollama_runtime"])
        self.assertTrue(report["checks"]["optional_r1_model"])
        self.assertTrue(report["checks"]["bundled_python_runtime"])
        self.assertTrue(report["checks"]["bundled_stt_model"])
        self.assertTrue(report["checks"]["tauri_local_tools_enabled"])
        self.assertTrue(report["checks"]["tauri_local_nsis_cached"])
        json.dumps(report)

    def test_preflight_blocks_user_profile_tauri_tool_cache(self):
        from scripts.app_build_preflight import collect_build_preflight

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app" / "src-tauri").mkdir(parents=True)
            (root / "app" / "package.json").write_text('{"name":"javis-app"}', encoding="utf-8")
            (root / "app" / "src-tauri" / "tauri.conf.json").write_text(
                json.dumps({"bundle": {"active": True, "targets": ["nsis"], "useLocalToolsDir": False}}),
                encoding="utf-8",
            )

            with unittest.mock.patch(
                "scripts.app_build_preflight._drive_is_g",
                return_value=True,
            ):
                report = collect_build_preflight(root)

            self.assertIn("tauri-local-tools-disabled", report["blockers"])
            self.assertIn("tauri-local-nsis-cache-missing", report["blockers"])

    def test_preflight_is_read_only_and_contains_no_install_commands(self):
        source = (ROOT / "scripts" / "app_build_preflight.py").read_text(encoding="utf-8")

        for forbidden in ["pnpm install", "npm install", "cargo fetch", "cargo install", "winget", "Invoke-WebRequest"]:
            self.assertNotIn(forbidden, source)
        self.assertNotIn("subprocess", source)
        self.assertIn("_shared_tool_root", source)

    def test_packaging_boundary_is_part_of_readiness(self):
        from scripts.app_build_preflight import collect_build_preflight

        report = collect_build_preflight(ROOT)

        self.assertTrue(report["checks"]["package_boundary_safe"])
        self.assertIn("venv", report["package_boundary"]["exclude"])
        self.assertNotIn("ollama_models", report["package_boundary"]["bundled_release_components"])
        self.assertIn("ollama_models", report["package_boundary"]["optional_addon_components"])
        self.assertNotIn("ollama_models", report["package_boundary"]["external"])
        self.assertIn("blueprint", report["package_boundary"]["include"])
        self.assertIn("tools", report["package_boundary"]["include"])
        self.assertNotIn("start.py", report["package_boundary"]["include"])

    def test_complete_local_fixture_can_be_ready_without_network(self):
        from scripts.app_build_preflight import collect_build_preflight

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app" / "src-tauri" / "icons").mkdir(parents=True)
            (root / "app" / "src-tauri" / "target" / ".tauri" / "NSIS").mkdir(parents=True)
            (root / "app" / "node_modules" / ".bin").mkdir(parents=True)
            (root / "tools" / "nodejs").mkdir(parents=True)
            (root / "tools" / "rust" / "cargo" / "bin").mkdir(parents=True)
            (root / "tools" / "rust" / "cargo" / "registry" / "src" / "local" / "tauri-2.0.0").mkdir(parents=True)
            (root / "tools" / "ollama-runtime").mkdir(parents=True)
            (root / "tools" / "python-runtime-3.11").mkdir(parents=True)
            (root / "models" / "faster-whisper-base").mkdir(parents=True)
            model_manifest = (
                root
                / "ollama_models"
                / "models"
                / "manifests"
                / "registry.ollama.ai"
                / "library"
                / "deepseek-r1"
                / "8b"
            )
            model_manifest.parent.mkdir(parents=True)
            model_blob = root / "ollama_models" / "models" / "blobs" / "sha256-fixture"
            model_blob.parent.mkdir(parents=True)
            model_blob.write_bytes(b"model")
            model_manifest.write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "config": {"digest": "sha256:fixture", "size": 5},
                        "layers": [],
                    }
                ),
                encoding="utf-8",
            )
            for path in [
                root / "tools" / "nodejs" / "node.exe",
                root / "tools" / "rust" / "cargo" / "bin" / "cargo.exe",
                root / "tools" / "rust" / "cargo" / "bin" / "rustc.exe",
                root / "app" / "node_modules" / ".bin" / "tsc.cmd",
                root / "app" / "node_modules" / ".bin" / "vite.cmd",
                root / "app" / "src-tauri" / "icons" / "icon.ico",
                root / "app" / "src-tauri" / "target" / ".tauri" / "NSIS" / "makensis.exe",
                root / "main.py",
                root / "tools" / "ollama-runtime" / "ollama.exe",
                root / "tools" / "python-runtime-3.11" / "python.exe",
                root / "models" / "faster-whisper-base" / "model.bin",
            ]:
                path.write_bytes(b"fixture")
            (root / "app" / "package.json").write_text('{"name":"javis-app"}', encoding="utf-8")
            (root / "app" / "src-tauri" / "tauri.conf.json").write_text(
                json.dumps(
                    {
                        "bundle": {
                            "active": True,
                            "targets": ["nsis"],
                            "icon": ["icons/icon.ico"],
                            "useLocalToolsDir": True,
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = collect_build_preflight(root)

            self.assertTrue(report["ready_for_offline_build"])
            self.assertEqual(report["blockers"], [])


if __name__ == "__main__":
    unittest.main()
