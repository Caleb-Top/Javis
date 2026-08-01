import inspect
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AppReleaseBlockerTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_sidebar_collapse_uses_one_layout_state(self):
        script = self.read("web/js/app.js")
        styles = self.read("web/css/style.css")

        self.assertIn("sidebar-collapsed", script)
        self.assertIn("body.sidebar-collapsed .input-area", styles)
        self.assertIn("body.sidebar-collapsed .controls-bar", styles)
        self.assertIn("body.sidebar-collapsed .permission-bar", styles)
        self.assertIn("--sidebar-w: 0px", styles)

    def test_live_messages_request_the_live_interaction_mode(self):
        client = self.read("app/src/bridge/backendClient.ts")
        gateway = self.read("gateway/conversation_ws.py")

        self.assertIn('interaction_mode: "live"', client)
        self.assertIn('interaction_mode=str(command.payload.get("interaction_mode")', gateway)

    def test_live_fast_classifier_keeps_actions_on_full_agent_path(self):
        from core.agent import _is_live_fast_dialogue

        self.assertTrue(_is_live_fast_dialogue("你好"))
        self.assertTrue(_is_live_fast_dialogue("仅回复：LIVE_FAST_OK"))
        self.assertFalse(_is_live_fast_dialogue("打开记事本"))
        self.assertFalse(_is_live_fast_dialogue("运行命令 dir"))
        self.assertFalse(_is_live_fast_dialogue("分析这个项目并修改所有文件"))

    def test_live_exact_reply_is_deterministic(self):
        from core.agent import _extract_live_exact_reply

        self.assertEqual(_extract_live_exact_reply("仅回复：LIVE_FAST_OK"), "LIVE_FAST_OK")
        self.assertEqual(_extract_live_exact_reply('Reply exactly: "READY"'), "READY")
        self.assertIsNone(_extract_live_exact_reply("请简单回复我"))

    def test_local_brief_chat_disables_long_reasoning_and_limits_output(self):
        from core.llm_client import LLMClient

        source = inspect.getsource(LLMClient.chat_brief)
        self.assertIn("/api/generate", source)
        self.assertIn('"raw": True', source)
        self.assertIn("</think>", source)
        self.assertIn("num_predict", source)
        self.assertIn("keep_alive", source)

    def test_browser_preview_does_not_derive_websocket_from_sidecar(self):
        main = self.read("app/src/main.ts")
        client = self.read("app/src/bridge/backendClient.ts")

        self.assertIn("ConnectionSnapshot", client)
        self.assertIn("mergeConnectionDetails", main)
        self.assertIn("if (!isTauriRuntime()) return;", main)
        self.assertNotIn('WebSocket: online ? "连接中" : "离线"', main)

    def test_control_actions_use_non_blocking_drawer_confirmation(self):
        control = self.read("app/src/panels/ControlDrawer.ts")

        self.assertNotIn("window.confirm", control)
        self.assertIn("requestActionConfirmation", control)
        self.assertIn("pending-action-confirm", control)
        self.assertIn('data-action-confirm="accept"', control)
        self.assertIn('data-action-confirm="cancel"', control)

    def test_audio_diagnostics_cover_microphone_system_audio_stt_and_tts(self):
        voice = self.read("app/src/live/VoiceCapture.ts")
        diagnostics = self.read("app/src/panels/DiagnosticsPanel.ts")
        main = self.read("app/src/main.ts")

        for contract in ["probeMicrophone", "probeSystemAudio", "selfTest"]:
            self.assertIn(contract, voice)
        self.assertIn('"/api/voice/capture/probe"', voice)
        self.assertIn("/ws_voice_stream", voice)
        self.assertIn('type: "audio.stream.start"', voice)
        self.assertNotIn("navigator.mediaDevices", voice)
        self.assertNotIn("MediaRecorder", voice)
        for contract in ["audio-diagnostics", "麦克风自检", "系统音频自检", "STT", "TTS"]:
            self.assertIn(contract, diagnostics)
        self.assertIn("URL.createObjectURL", diagnostics)
        self.assertIn("URL.revokeObjectURL", diagnostics)
        self.assertNotIn("new Audio(`data:", diagnostics)
        self.assertIn("voiceCapture", diagnostics)
        self.assertIn("createDiagnosticsPanel(client, sidecar, voiceCapture, {", main)


if __name__ == "__main__":
    unittest.main()
