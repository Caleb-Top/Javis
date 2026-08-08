import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


class AppV3IntegratedReleaseTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_all_release_versions_are_v3(self):
        package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((APP / "package-lock.json").read_text(encoding="utf-8"))
        tauri = json.loads((APP / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
        cargo = tomllib.loads((APP / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
        manifest = json.loads((APP / "release.manifest.json").read_text(encoding="utf-8"))

        for version in (
            package["version"],
            lock["version"],
            lock["packages"][""]["version"],
            tauri["version"],
            cargo["package"]["version"],
            manifest["version"],
        ):
            self.assertEqual(version, "3.0.0")

    def test_release_manifest_declares_complete_backend_and_five_systems(self):
        manifest = json.loads((APP / "release.manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["backend"]["packaged"])
        self.assertTrue(manifest["backend"]["complete_source"])
        self.assertTrue(manifest["data"]["preserved"])
        self.assertEqual(
            set(manifest["systems"]),
            {"core", "memory", "perception", "control", "evolution"},
        )
        self.assertTrue(manifest["external_components"]["ollama_models"])
        self.assertIsNone(manifest["models"]["bundled_default"])
        self.assertEqual(manifest["models"]["optional_addon"], "Javis-R1-8B-Addon")
        self.assertTrue(manifest["models"]["user_switchable"])
        self.assertTrue(manifest["external_components"]["cuda_training_stack"])
        self.assertEqual(manifest["runtime"]["install_root"], "%LOCALAPPDATA%/local.javis.desktop/runtime")
        self.assertEqual(manifest["data"]["root"], "%LOCALAPPDATA%/local.javis.desktop")

    def test_runtime_manifest_contract_is_v3_and_complete(self):
        from scripts.javis_release_runtime import (
            BACKEND_INCLUDE,
            EXTERNAL_COMPONENTS,
            REQUIRED_MEMBERS,
            SYSTEMS,
            VERSION,
        )

        self.assertEqual(VERSION, "3.0.0")
        self.assertEqual(set(SYSTEMS), {"core", "memory", "perception", "control", "evolution"})
        self.assertIn("ollama_models", EXTERNAL_COMPONENTS)
        self.assertIn("cuda_training_stack", EXTERNAL_COMPONENTS)
        for path in ("main.py", "blueprint", "core", "memory", "perception", "control", "evolution"):
            self.assertIn(path, BACKEND_INCLUDE)
        self.assertIn("python/python.exe", REQUIRED_MEMBERS)
        self.assertIn("app/models/faster-whisper-base/model.bin", REQUIRED_MEMBERS)

    def test_runtime_archive_does_not_duplicate_outer_installer_payloads(self):
        from scripts.javis_release_runtime import should_exclude

        self.assertTrue(
            should_exclude(Path("app/tools/ollama-runtime/ollama.exe"), is_python=False)
        )
        self.assertTrue(
            should_exclude(Path("app/tools/python-runtime-3.11/python.exe"), is_python=False)
        )

    def test_runtime_bootstrap_binds_to_package_version_and_preserves_state(self):
        runtime = self.read("app/src-tauri/src/runtime_bundle.rs")
        sidecar = self.read("app/src-tauri/src/sidecar.rs")
        for contract in (
            'env!("CARGO_PKG_VERSION")',
            "verify_checksum",
            "runtime.new",
            "runtime.previous",
            "run_import_probe",
            "migrate_persistent_state",
            "brain_data",
            "workspace",
            "config.yaml",
            "skills",
        ):
            self.assertIn(contract, runtime)
        self.assertIn("packaged_root", sidecar)
        self.assertIn("JAVIS_DATA_ROOT", sidecar)

    def test_runtime_bootstrap_does_not_block_the_first_window(self):
        main = self.read("app/src-tauri/src/main.rs")
        frontend = self.read("app/src/main.ts")

        self.assertIn("runtime_bundle::runtime_path(app.handle())", main)
        self.assertIn("std::thread::spawn", main)
        self.assertIn("runtime_bundle::ensure_runtime(&runtime_app)", main)
        self.assertIn("state.sidecar.start(&runtime_app)", main)
        self.assertIn('listen("javis://runtime-ready"', frontend)
        self.assertIn('listen<string>("javis://runtime-error"', frontend)
        self.assertLess(
            main.index("app.manage(AppState::new"),
            main.index("std::thread::spawn"),
        )

    def test_runtime_activation_recovers_interrupted_upgrade_and_stale_backend(self):
        runtime = self.read("app/src-tauri/src/runtime_bundle.rs")

        for contract in (
            "runtime_is_ready",
            "activate_incoming",
            "stop_stale_packaged_python",
            "JAVIS_APP_DATA_ROOT",
            "JAVIS_RUNTIME_ROOT",
            "ExecutablePath",
            "runtime.new",
        ):
            self.assertIn(contract, runtime)
        self.assertLess(
            runtime.index("if runtime_is_ready(&runtime"),
            runtime.index("verify_checksum(&archive"),
        )
        self.assertNotIn('taskkill.exe").args(["/F", "/IM", "python.exe"]', runtime)

    def test_runtime_extraction_ignores_nonportable_zip_timestamps(self):
        runtime = self.read("app/src-tauri/src/runtime_bundle.rs")

        self.assertIn('hidden_command("tar.exe")', runtime)
        self.assertIn('.args(["-m", "-xf"])', runtime)

    def test_webview2_loader_is_bundled_beside_the_app_executable(self):
        tauri = json.loads(
            (APP / "src-tauri/tauri.conf.json").read_text(encoding="utf-8")
        )
        build = self.read("scripts/build_javis_app_v3.ps1")
        installer_verify = self.read("scripts/verify_javis_v3_installer.ps1")

        self.assertIsInstance(tauri["bundle"]["resources"], dict)
        self.assertEqual(
            tauri["bundle"]["resources"]["resources/WebView2Loader.dll"],
            "WebView2Loader.dll",
        )
        for contract in (
            "webview2-com-sys-*",
            'x64\\WebView2Loader.dll',
            '$JavisWebView2LoaderResource',
            "Get-FileHash",
        ):
            self.assertIn(contract, build)
        self.assertIn('Join-Path $AppExe.DirectoryName "WebView2Loader.dll"', installer_verify)
        self.assertIn('"Installed WebView2 loader"', installer_verify)

    def test_v3_build_produces_a_loadable_main_setup_and_optional_r1_addon(self):
        build = self.read("scripts/build_javis_app_v3.ps1")
        installer_verify = self.read("scripts/verify_javis_v3_installer.ps1")
        for contract in (
            "app_build_preflight.py",
            "--strict",
            "stage_javis_runtime.py",
            "verify_javis_release.py",
            "verify_javis_v3_installer.ps1",
            "javis_release_layout.py",
            "javis_full_installer.py",
            "tools\\ollama-runtime",
            "tools\\python-runtime-3.11",
            "models\\faster-whisper-base",
            "--python-root",
            "--stt-model-root",
            "deepseek-r1:8b",
            "Javis-v3.0.0-Setup.exe",
            "--build-addon",
            "Javis-R1-8B-Addon",
            "GetBinaryType",
            "--output-dir",
            "SHA256SUMS.txt",
        ):
            self.assertIn(contract, build)
        self.assertNotIn("GetFolderPath(\"Desktop\")", build)
        self.assertNotIn("Compress-Archive", build)
        for forbidden in (
            "pip install",
            "npm install",
            "npm ci",
            "cargo install",
            "Invoke-WebRequest",
            "ollama pull",
        ):
            self.assertNotIn(forbidden, build)
        for contract in (
            "/S",
            "runtime-version.json",
            "/api/status",
            "preservation",
            "Uninstall",
            "Wait-InstallerRemoval",
            "Uninstall registry cleanup",
            "JAVIS_APP_DATA_ROOT",
            "Installed App window",
            "Wait-JavisProcessesStopped",
            "ExecutablePath",
            "VerifierProcessIds",
            "$DataRoot",
            "/DATA=",
            "Main setup excludes Ollama",
            "Main setup excludes R1 weights",
        ):
            self.assertIn(contract, installer_verify)
        self.assertNotIn("Join-Path $env:TEMP", installer_verify)

    def test_v3_build_uses_the_installed_rust_toolchain_without_rustup_sync(self):
        build = self.read("scripts/build_javis_app_v3.ps1")

        self.assertIn("$JavisRustToolchainBin", build)
        self.assertIn('Join-Path $JavisRustToolchainBin "cargo.exe"', build)
        self.assertIn('Join-Path $JavisRustToolchainBin "rustc.exe"', build)
        self.assertNotIn("$env:RUSTUP_TOOLCHAIN", build)
        self.assertNotIn("$env:CARGO_BUILD_TARGET", build)
        self.assertNotIn("build --target", build)
        self.assertIn('"src-tauri\\target\\release"', build)


if __name__ == "__main__":
    unittest.main()
