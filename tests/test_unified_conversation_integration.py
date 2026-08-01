import asyncio
import tempfile
import unittest
from pathlib import Path

from scripts.verify_unified_conversation import run_verification


ROOT = Path(__file__).resolve().parents[1]


class UnifiedConversationIntegrationTests(unittest.TestCase):
    def test_live_code_cancel_replay_and_persistence(self):
        verification_root = ROOT / "tmp" / "unified-conversation-tests"
        verification_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=verification_root) as temporary:
            result = asyncio.run(
                run_verification(ROOT, data_root=Path(temporary))
            )

        self.assertTrue(result["ok"], result)
        for required in (
            "two_surface_delivery",
            "cancel_replace",
            "cursor_replay",
            "no_stale_delta",
            "conversation_persistence",
            "agent_run_persistence",
            "graceful_shutdown",
            "g_rooted_data",
        ):
            self.assertTrue(result["checks"][required]["passed"], required)


if __name__ == "__main__":
    unittest.main()
