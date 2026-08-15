import base64
import asyncio
import io
import os
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
os.environ["JAVIS_TEST_MODE"] = "1"
os.environ["JAVIS_DISABLE_STARTUP_SIDE_EFFECTS"] = "1"


class FakePlaybackApiManager:
    def __init__(self):
        self.reserve_calls = 0
        self.play_calls = []
        self.stop_calls = []

    def reserve(self):
        self.reserve_calls += 1
        return 7

    def play_reserved_wav(self, *args, **kwargs):
        self.play_calls.append((args, kwargs))
        return {"ok": True, "playback_id": "playback-1", "generation": 7}

    def stop(self, **kwargs):
        self.stop_calls.append(kwargs)
        return {"ok": True, "stopped": bool(kwargs)}


class NativeAudioContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_desktop_voice_uses_native_backend_instead_of_webview_permissions(self):
        voice = self.read("app/src/live/VoiceCapture.ts")
        main = self.read("app/src/main.ts")

        self.assertIn("/ws_voice_stream", voice)
        self.assertIn('type: "audio.stream.start"', voice)
        self.assertIn('type: "audio.stream.stop"', voice)
        self.assertIn('"/api/voice/capture/probe"', voice)
        self.assertIn("createVoiceCapture(client,", main)
        for forbidden in (
            "navigator.mediaDevices",
            "getUserMedia",
            "getDisplayMedia",
            "MediaRecorder",
        ):
            self.assertNotIn(forbidden, voice)

    def test_code_surface_cannot_request_browser_microphone_or_camera(self):
        code_surface = self.read("app/src/code/CodeSurface.ts")

        self.assertNotIn("microphone", code_surface)
        self.assertNotIn("camera", code_surface)
        self.assertNotIn("clipboard-read", code_surface)
        self.assertNotIn("clipboard-write", code_surface)

    def test_tauri_shell_does_not_register_webview_device_permission_bridges(self):
        tauri_main = self.read("app/src-tauri/src/main.rs")
        cargo = self.read("app/src-tauri/Cargo.toml")

        self.assertNotIn("configure_screen_capture", tauri_main)
        self.assertNotIn("ScreenCaptureStartingEventHandler", tauri_main)
        self.assertNotIn("ICoreWebView2_27", tauri_main)
        self.assertNotIn("webview2-com", cargo)

    def test_backend_exposes_native_capture_endpoints_and_diagnostics(self):
        main = self.read("main.py")

        self.assertIn("from voice.native_capture import", main)
        self.assertIn('@app.post("/api/voice/capture/start")', main)
        self.assertIn('@app.post("/api/voice/capture/stop")', main)
        self.assertIn('@app.post("/api/voice/capture/probe")', main)
        self.assertIn("VoiceDiagnosticsCollector(", main)
        self.assertIn("capture_getter=get_capture_diagnostics", main)
        self.assertIn("return await voice_diagnostics_collector.collect()", main)
        self.assertIn("preload_model", main)

    def test_app_continuous_voice_uses_a_native_backend_stream(self):
        voice = self.read("app/src/live/VoiceCapture.ts")
        main = self.read("main.py")

        self.assertIn("/ws_voice_stream", voice)
        self.assertIn("audio.stream.start", voice)
        self.assertIn("transcript.partial", voice)
        self.assertIn("transcript.final", voice)
        self.assertIn('@app.websocket("/ws_voice_stream")', main)
        self.assertIn("serve_continuous_voice_stream", main)

    def test_backend_wires_life_observers_and_scoped_playback_identity(self):
        main = self.read("main.py")

        self.assertIn("PlaybackLifecyclePublisher", main)
        self.assertIn("duration_sink=runtime.life.observe_playback", main)
        self.assertIn("life_observer=runtime.life.observe_voice_event", main)
        self.assertIn('session_id=data.get("session_id")', main)
        self.assertIn('request_id=data.get("request_id")', main)
        self.assertNotIn('session_id=str(data.get("session_id")', main)
        self.assertNotIn('request_id=str(data.get("request_id")', main)
        for field in ("playback_id", "generation", "session_id", "request_id"):
            self.assertIn(f'{field}=data.get("{field}")', main)

    def test_speak_endpoint_forwards_session_and_request_identity(self):
        import main

        manager = FakePlaybackApiManager()
        with (
            patch.object(main, "_require_runtime_http"),
            patch.object(main, "native_playback_manager", manager),
            patch("voice.tts.synthesize", new=AsyncMock(return_value=(
                base64.b64encode(b"wav").decode("ascii"),
                "audio/wav",
            ))),
        ):
            result = asyncio.run(main.api_voice_playback_speak(
                object(),
                {
                    "text": "hello",
                    "session_id": "session-1",
                    "request_id": "request-1",
                },
            ))

        self.assertTrue(result["ok"])
        self.assertEqual(manager.reserve_calls, 1)
        self.assertEqual(manager.play_calls[0][1], {
            "session_id": "session-1",
            "request_id": "request-1",
        })

    def test_stop_endpoint_forwards_complete_scope_and_preserves_empty_fallback(self):
        import main

        manager = FakePlaybackApiManager()
        identity = {
            "playback_id": "playback-1",
            "generation": 7,
            "session_id": "session-1",
            "request_id": "request-1",
        }
        with (
            patch.object(main, "_require_runtime_http"),
            patch.object(main, "native_playback_manager", manager),
        ):
            scoped = asyncio.run(main.api_voice_playback_stop(object(), identity))
            fallback = asyncio.run(main.api_voice_playback_stop(object(), {}))

        self.assertTrue(scoped["stopped"])
        self.assertFalse(fallback["stopped"])
        self.assertEqual(manager.stop_calls, [identity, {}])

    def test_native_capture_encodes_pcm_as_valid_wav(self):
        from voice.native_capture import frames_to_wav_base64

        encoded = frames_to_wav_base64(
            [b"\x00\x00\x10\x00" * 128],
            sample_width=2,
            channels=1,
            rate=16_000,
        )
        payload = base64.b64decode(encoded, validate=True)
        with wave.open(io.BytesIO(payload), "rb") as stream:
            self.assertEqual(stream.getnchannels(), 1)
            self.assertEqual(stream.getsampwidth(), 2)
            self.assertEqual(stream.getframerate(), 16_000)
            self.assertGreater(stream.getnframes(), 0)

    def test_system_capture_uses_the_isolated_wasapi_helper(self):
        from voice.native_capture import NativeCaptureManager

        manager = NativeCaptureManager()
        expected = {"ok": True, "source": "system"}
        with (
            patch("pathlib.Path.is_file", return_value=True),
            patch.object(manager, "_start_wasapi", return_value=expected) as helper,
        ):
            started = manager.start("system")

        self.assertEqual(started, expected)
        helper.assert_called_once_with()

    def test_release_runtime_requires_native_audio_dependencies(self):
        from scripts.javis_release_runtime import REQUIRED_MEMBERS

        for required in (
            "app/voice/native_capture.py",
            "app/voice/native/javis-wasapi-loopback.exe",
            "python/Lib/site-packages/pyaudio/__init__.py",
            "python/Lib/site-packages/pyaudio/_portaudio.cp311-win_amd64.pyd",
        ):
            self.assertIn(required, REQUIRED_MEMBERS)

    def test_system_audio_has_built_in_wasapi_loopback_helper(self):
        native_capture = self.read("voice/native_capture.py")
        helper_source = self.read("voice/native/WASAPILoopbackRecorder.cs")
        build = self.read("scripts/build_javis_app_v3.ps1")

        self.assertIn("WASAPI_HELPER", native_capture)
        self.assertIn("AUDCLNT_STREAMFLAGS_LOOPBACK", helper_source)
        self.assertIn("IAudioCaptureClient", helper_source)
        self.assertIn("WASAPILoopbackRecorder.cs", build)
        self.assertIn("javis-wasapi-loopback.exe", build)

    def test_portaudio_is_isolated_from_the_backend_process(self):
        native_capture = self.read("voice/native_capture.py")
        worker = self.read("voice/native_capture_worker.py")

        self.assertIn("MICROPHONE_WORKER", native_capture)
        self.assertIn("subprocess.Popen", native_capture)
        self.assertNotIn("import pyaudio", native_capture)
        self.assertNotIn("pyaudio.PyAudio()", native_capture)
        self.assertIn("import pyaudio", worker)
        self.assertIn("pyaudio.PyAudio()", worker)

    def test_release_runtime_requires_isolated_microphone_worker(self):
        from scripts.javis_release_runtime import REQUIRED_MEMBERS

        for required in (
            "app/voice/native_capture_worker.py",
            "app/voice/streaming_pipeline.py",
            "app/voice/continuous_capture.py",
            "app/voice/streaming_ws.py",
            "app/voice/native_playback.py",
        ):
            self.assertIn(required, REQUIRED_MEMBERS)

    def test_app_screen_capture_uses_native_tauri_command(self):
        tauri_main = self.read("app/src-tauri/src/main.rs")
        native_screen = self.read("app/src-tauri/src/screen_capture.rs")
        control_drawer = self.read("app/src/panels/ControlDrawer.ts")
        main = self.read("main.py")

        self.assertIn("capture_screen_native", tauri_main)
        self.assertIn("BitBlt", native_screen)
        self.assertIn("DuplicateOutput", native_screen)
        self.assertIn('invoke<string>("capture_screen_native")', control_drawer)
        self.assertIn("image_bgra_base64", control_drawer)
        self.assertIn("image_bgra_base64", main)
        self.assertNotIn("分析当前屏幕\",\n      \"只提取", control_drawer)

    def test_installer_preserves_user_data_even_during_silent_uninstall(self):
        config = self.read("app/src-tauri/tauri.conf.json")
        hook = self.read("app/src-tauri/windows/nsis-hooks.nsh")
        verifier = self.read("scripts/verify_javis_v3_installer.ps1")

        self.assertIn("installerHooks", config)
        self.assertIn("NSIS_HOOK_PREUNINSTALL", hook)
        self.assertIn("StrCpy $DeleteAppDataCheckboxState 0", hook)
        self.assertIn("Installed App binary baseline", verifier)
        self.assertIn("Activated runtime hash", verifier)
        self.assertIn("Same-version reinstall replaces stale App", verifier)


if __name__ == "__main__":
    unittest.main()
