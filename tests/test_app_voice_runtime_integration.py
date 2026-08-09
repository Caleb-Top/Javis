import asyncio
import base64
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagedVoiceRuntimeTests(unittest.TestCase):
    def test_installer_gate_executes_voice_reconnect_and_frontend_lifecycle_suites(self):
        if os.name != "nt" or shutil.which("powershell.exe") is None:
            self.skipTest("the installer source gate is Windows PowerShell-only")

        node = next(
            (
                candidate
                for candidate in (
                    ROOT / "tools/nodejs/node.exe",
                    Path("G:/Javis/tools/nodejs/node.exe"),
                    Path(shutil.which("node") or ""),
                )
                if candidate.is_file()
            ),
            None,
        )
        if node is None:
            self.skipTest("Node.js is unavailable for the installer source gate")

        environment = os.environ.copy()
        environment.pop("JAVIS_TEST_PYTHON", None)
        default_python = next(
            (
                candidate
                for candidate in (
                    ROOT / "venv/Scripts/python.exe",
                    Path("G:/Javis/venv/Scripts/python.exe"),
                )
                if candidate.is_file()
            ),
            None,
        )
        if default_python is None:
            environment["JAVIS_TEST_PYTHON"] = sys.executable

        environment.pop("JAVIS_TEST_NODE", None)
        if node not in (ROOT / "tools/nodejs/node.exe", Path("G:/Javis/tools/nodejs/node.exe")):
            environment["JAVIS_TEST_NODE"] = str(node)
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts/verify_javis_v3_installer.ps1"),
                "-SourceRoot",
                str(ROOT),
                "-SourceRegressionOnly",
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Source voice reconnect regression: PASS", output)
        self.assertIn("Source voice observability regression: PASS", output)
        self.assertIn("Source real watchdog stall harness: PASS", output)
        self.assertIn("Source frontend voice lifecycle: PASS", output)

    def test_runtime_overlays_venv_packages_and_local_models(self):
        from scripts.javis_release_runtime import runtime_entries

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            python_root = Path(tmp) / "python-base"
            site_packages = Path(tmp) / "site-packages"
            model_root = Path(tmp) / "faster-whisper-base"

            (root / "tools/Tesseract-OCR").mkdir(parents=True)
            (root / "main.py").write_text("print('ok')", encoding="utf-8")
            (root / "tools/Tesseract-OCR/tesseract.exe").write_bytes(b"MZ")
            (python_root / "Lib/site-packages").mkdir(parents=True)
            (python_root / "python.exe").write_bytes(b"MZ")
            (python_root / "Lib/site-packages/stale.py").write_text("STALE = True", encoding="utf-8")
            (site_packages / "faster_whisper").mkdir(parents=True)
            (site_packages / "faster_whisper/__init__.py").write_text("", encoding="utf-8")
            model_root.mkdir(parents=True)
            (model_root / "model.bin").write_bytes(b"local-model")

            entries = list(runtime_entries(
                root,
                python_root,
                site_packages_root=site_packages,
                stt_model_root=model_root,
                allow_incomplete=True,
            ))
            names = {entry.archive_name for entry in entries}

            self.assertIn("python/Lib/site-packages/faster_whisper/__init__.py", names)
            self.assertIn("app/models/faster-whisper-base/model.bin", names)
            self.assertIn("app/tools/Tesseract-OCR/tesseract.exe", names)
            self.assertNotIn("python/Lib/site-packages/stale.py", names)

    def test_release_contract_requires_voice_ocr_assets(self):
        from scripts.javis_release_runtime import REQUIRED_MEMBERS

        for required in (
            "python/Lib/site-packages/faster_whisper/__init__.py",
            "python/Lib/site-packages/pytesseract/__init__.py",
            "python/Lib/site-packages/edge_tts/__init__.py",
            "app/models/faster-whisper-base/model.bin",
            "app/tools/Tesseract-OCR/tesseract.exe",
        ):
            self.assertIn(required, REQUIRED_MEMBERS)

    def test_stt_loads_packaged_model_without_network_resolution(self):
        import voice.stt as stt

        original_model = stt._model
        original_dir = stt.PACKAGED_MODEL_DIR
        original_module = sys.modules.get("faster_whisper")
        calls = []

        class FakeWhisperModel:
            def __init__(self, source, **kwargs):
                calls.append((source, kwargs))

        try:
            with tempfile.TemporaryDirectory() as tmp:
                model_dir = Path(tmp)
                (model_dir / "model.bin").write_bytes(b"model")
                stt.PACKAGED_MODEL_DIR = model_dir
                stt._model = None
                sys.modules["faster_whisper"] = types.SimpleNamespace(WhisperModel=FakeWhisperModel)

                model = stt._get_model()

                self.assertIsInstance(model, FakeWhisperModel)
                self.assertEqual(Path(calls[0][0]), model_dir)
                self.assertTrue(calls[0][1]["local_files_only"])
        finally:
            stt._model = original_model
            stt.PACKAGED_MODEL_DIR = original_dir
            if original_module is None:
                sys.modules.pop("faster_whisper", None)
            else:
                sys.modules["faster_whisper"] = original_module

    @unittest.skipUnless(os.name == "nt", "Windows SAPI is Windows-only")
    def test_tts_prefers_offline_sapi_and_returns_wav_mime(self):
        import voice.tts as tts

        original_cache = tts.CACHE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tts.CACHE_DIR = Path(tmp)
                audio, mime = asyncio.run(tts.synthesize("Javis 离线语音测试"))
                decoded = base64.b64decode(audio, validate=True)

                self.assertEqual(mime, "audio/wav")
                self.assertTrue(decoded.startswith(b"RIFF"))
                self.assertGreater(len(decoded), 1024)
        finally:
            tts.CACHE_DIR = original_cache

    def test_ocr_configures_string_tesseract_path(self):
        from perception.adapters.ocr import OcrAdapter
        import tools.setup as setup

        original_exe = setup.TESSERACT_EXE
        original_module = sys.modules.get("pytesseract")
        original_tessdata = os.environ.get("TESSDATA_PREFIX")
        fake = types.SimpleNamespace(
            pytesseract=types.SimpleNamespace(tesseract_cmd=""),
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tool_root = Path(tmp) / "Tesseract-OCR"
                tool_root.mkdir()
                executable = tool_root / "tesseract.exe"
                executable.write_bytes(b"MZ")
                setup.TESSERACT_EXE = str(executable)
                sys.modules["pytesseract"] = fake

                loaded = OcrAdapter._load_pytesseract()

                self.assertIs(loaded, fake)
                self.assertEqual(fake.pytesseract.tesseract_cmd, str(executable))
                self.assertEqual(os.environ["TESSDATA_PREFIX"], str(tool_root / "tessdata"))
        finally:
            setup.TESSERACT_EXE = original_exe
            if original_module is None:
                sys.modules.pop("pytesseract", None)
            else:
                sys.modules["pytesseract"] = original_module
            if original_tessdata is None:
                os.environ.pop("TESSDATA_PREFIX", None)
            else:
                os.environ["TESSDATA_PREFIX"] = original_tessdata

    def test_backend_and_app_preserve_tts_mime(self):
        main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        gateway_source = (ROOT / "gateway/conversation_ws.py").read_text(encoding="utf-8")
        diagnostics = (ROOT / "app/src/panels/DiagnosticsPanel.ts").read_text(encoding="utf-8")

        self.assertIn('"mime": mime', main_source)
        self.assertIn('response.mime || "audio/mpeg"', diagnostics)
        self.assertIn("await asyncio.to_thread(self.transcribe, audio)", gateway_source)

    def test_app_uses_native_audio_and_backend_screen_capture(self):
        voice_capture = (ROOT / "app/src/live/VoiceCapture.ts").read_text(encoding="utf-8")
        control = (ROOT / "app/src/panels/ControlDrawer.ts").read_text(encoding="utf-8")

        self.assertIn('"/api/voice/capture/probe"', voice_capture)
        self.assertIn("/ws_voice_stream", voice_capture)
        self.assertNotIn("navigator.mediaDevices", voice_capture)
        self.assertIn('"/api/perception/screen/analyze"', control)

    def test_build_uses_current_python_311_environment(self):
        from scripts import stage_javis_runtime

        stage = (ROOT / "scripts/stage_javis_runtime.py").read_text(encoding="utf-8")
        build = (ROOT / "scripts/build_javis_app_v3.ps1").read_text(encoding="utf-8")

        self.assertIn("sys.base_prefix", stage)
        self.assertIn("sys.prefix", stage)
        self.assertIn("--site-packages", stage)
        self.assertIn("--stt-model-root", stage)
        self.assertNotIn("python-embed", build)
        self.assertTrue(callable(stage_javis_runtime._discover_stt_model))

    def test_runtime_activation_probes_voice_and_ocr_imports(self):
        rust_probe = (ROOT / "app/src-tauri/src/runtime_bundle.rs").read_text(encoding="utf-8")
        release_probe = (ROOT / "scripts/verify_javis_release.py").read_text(encoding="utf-8")

        for dependency in (
            "faster_whisper",
            "pytesseract",
            "win32com.client",
            "av",
            "ctranslate2",
        ):
            self.assertIn(dependency, rust_probe)
            self.assertIn(dependency, release_probe)


if __name__ == "__main__":
    unittest.main()
