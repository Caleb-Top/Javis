import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gateway.conversation_stall_harness import (
    AFTER_ACK_TRIGGER,
    AFTER_DELTA_TRIGGER,
    NO_ACK_TRIGGER,
    ConversationStallHarness,
    StallMode,
)


class ConversationStallHarnessTests(unittest.TestCase):
    @staticmethod
    def socket(host: str | None):
        client = None if host is None else SimpleNamespace(host=host)
        return SimpleNamespace(client=client)

    def test_environment_requires_both_explicit_test_gates(self):
        cases = (
            ({}, False),
            ({"JAVIS_TEST_MODE": "1"}, False),
            ({"JAVIS_STALL_HARNESS": "1"}, False),
            ({"JAVIS_TEST_MODE": "1", "JAVIS_STALL_HARNESS": "0"}, False),
            ({"JAVIS_TEST_MODE": "1", "JAVIS_STALL_HARNESS": "1"}, True),
        )

        for environment, expected in cases:
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                self.assertIs(ConversationStallHarness.from_environment().enabled, expected)

    def test_exact_triggers_select_all_three_modes_on_ipv4_loopback(self):
        harness = ConversationStallHarness(enabled=True)
        socket = self.socket("127.0.0.1")

        self.assertIs(harness.mode_for(socket, NO_ACK_TRIGGER), StallMode.NO_ACK)
        self.assertIs(harness.mode_for(socket, AFTER_ACK_TRIGGER), StallMode.AFTER_ACK)
        self.assertIs(harness.mode_for(socket, AFTER_DELTA_TRIGGER), StallMode.AFTER_DELTA)

    def test_ipv6_and_ipv4_mapped_loopback_are_allowed(self):
        harness = ConversationStallHarness(enabled=True)

        self.assertIs(
            harness.mode_for(self.socket("::1"), AFTER_ACK_TRIGGER),
            StallMode.AFTER_ACK,
        )
        self.assertIs(
            harness.mode_for(self.socket("::ffff:127.0.0.1"), AFTER_ACK_TRIGGER),
            StallMode.AFTER_ACK,
        )

    def test_disabled_remote_missing_and_invalid_clients_fail_closed(self):
        enabled = ConversationStallHarness(enabled=True)
        disabled = ConversationStallHarness(enabled=False)

        self.assertIsNone(disabled.mode_for(self.socket("127.0.0.1"), NO_ACK_TRIGGER))
        self.assertIsNone(enabled.mode_for(self.socket("192.0.2.10"), NO_ACK_TRIGGER))
        self.assertIsNone(enabled.mode_for(self.socket(None), NO_ACK_TRIGGER))
        self.assertIsNone(enabled.mode_for(self.socket("localhost"), NO_ACK_TRIGGER))

    def test_trigger_matching_is_exact_after_outer_whitespace_only(self):
        harness = ConversationStallHarness(enabled=True)
        socket = self.socket("127.0.0.1")

        self.assertIs(harness.mode_for(socket, f"  {NO_ACK_TRIGGER}\n"), StallMode.NO_ACK)
        self.assertIsNone(harness.mode_for(socket, f"{NO_ACK_TRIGGER} extra"))
        self.assertIsNone(harness.mode_for(socket, NO_ACK_TRIGGER.upper()))


if __name__ == "__main__":
    unittest.main()
