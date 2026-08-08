import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AppRuntimeUpgradeRegressionTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_runtime_helpers_never_open_console_windows(self):
        process = self.read("app/src-tauri/src/process_command.rs")
        runtime = self.read("app/src-tauri/src/runtime_bundle.rs")
        main = self.read("app/src-tauri/src/main.rs")

        self.assertIn("mod process_command;", main)
        self.assertIn("CREATE_NO_WINDOW", process)
        self.assertIn("creation_flags", process)
        self.assertIn("pub fn hidden_command", process)
        self.assertNotIn("Command::new(", runtime)
        for program in ('"tar.exe"', '"powershell.exe"', '"certutil.exe"', "&python"):
            self.assertIn(f"hidden_command({program})", runtime)

    def test_desktop_waits_for_packaged_runtime_before_connecting(self):
        frontend = self.read("app/src/main.ts")

        self.assertIn("await sidecar.status()", frontend)
        self.assertNotIn("sidecar.ensureStarted()", frontend)
        self.assertIn('listen("javis://runtime-ready"', frontend)

    def test_sidecar_rejects_an_old_javis_backend(self):
        sidecar = self.read("app/src-tauri/src/sidecar.rs")
        backend = self.read("main.py")

        self.assertIn('s["desktop_api_version"]=2', backend)
        self.assertIn('desktop_api_version\\\":2', sidecar)
        self.assertIn('continuous_voice\\\":true', sidecar)

    def test_installer_verification_checks_current_desktop_contract(self):
        verifier = self.read("scripts/verify_javis_v3_installer.ps1")

        self.assertIn('$Status.desktop_api_version -eq 2', verifier)
        self.assertIn('/api/diagnostics/self-test', verifier)
        self.assertIn('/ws_voice_stream', verifier)
        self.assertIn('No visible helper console', verifier)


if __name__ == "__main__":
    unittest.main()
