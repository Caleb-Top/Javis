import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MainVoiceObservabilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "main.py").read_text(encoding="utf-8")

    def test_loopback_stall_harness_is_injected_without_a_public_control_endpoint(self):
        self.assertIn(
            "from gateway.conversation_stall_harness import ConversationStallHarness",
            self.source,
        )
        self.assertIn(
            "conversation_stall_harness = ConversationStallHarness.from_environment()",
            self.source,
        )
        self.assertIn("stall_harness=conversation_stall_harness", self.source)
        self.assertNotIn('/api/test/stall', self.source)
        self.assertNotIn('/api/voice/stall', self.source)

    def test_voice_diagnostics_use_the_non_blocking_versioned_collector(self):
        self.assertIn("VoiceDiagnosticsCollector(", self.source)
        self.assertIn("gateway_getter=get_gateway_diagnostics", self.source)
        self.assertIn(
            "return await voice_diagnostics_collector.collect()",
            self.source,
        )
        endpoint = self.source.index('@app.get("/api/voice/diagnostics")')
        endpoint_end = self.source.index('@app.post("/api/voice/playback/speak")', endpoint)
        implementation = self.source[endpoint:endpoint_end]
        self.assertNotIn("get_capture_diagnostics()", implementation)


if __name__ == "__main__":
    unittest.main()
