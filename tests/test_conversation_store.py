import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore, ConversationStoreError


class ConversationStoreTests(unittest.TestCase):
    def test_events_are_monotonic_per_session_and_survive_reopen(self):
        with tempfile.TemporaryDirectory() as root_text:
            path = Path(root_text) / "conversations.sqlite3"
            store = ConversationStore(path)
            first = store.append_event(
                "session-1",
                "request-1",
                "request.accepted",
                {"text": "hello"},
            )
            second = store.append_event(
                "session-1",
                "request-1",
                "activity.planning",
                {"detail": "planning"},
            )
            other_session = store.append_event(
                "session-2",
                "request-2",
                "request.accepted",
                {"text": "independent"},
            )

            reopened = ConversationStore(path)
            replay = reopened.events_after("session-1", sequence=1)

        self.assertEqual(first["sequence"], 1)
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(other_session["sequence"], 1)
        self.assertEqual([event["type"] for event in replay], ["activity.planning"])
        self.assertEqual(replay[0]["payload"], {"detail": "planning"})

    def test_history_excludes_interrupted_assistant_output_by_default(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = ConversationStore(Path(root_text) / "conversations.sqlite3")
            store.append_message("session-1", "request-1", "user", "old request")
            store.append_message(
                "session-1",
                "request-1",
                "assistant",
                "partial answer",
                status="interrupted",
            )
            store.append_message("session-1", "request-2", "user", "replacement")
            store.append_message(
                "session-1",
                "request-2",
                "assistant",
                "complete answer",
            )

            normal_history = store.history("session-1")
            audit_history = store.history("session-1", include_interrupted=True)

        self.assertEqual(
            [item["content"] for item in normal_history],
            ["old request", "replacement", "complete answer"],
        )
        self.assertEqual(len(audit_history), 4)
        self.assertEqual(audit_history[1]["status"], "interrupted")

    def test_idempotency_key_returns_the_original_request(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = ConversationStore(Path(root_text) / "conversations.sqlite3")
            first_request, first_created = store.claim_idempotency_key(
                "session-1",
                "message-1",
                "request-1",
            )
            second_request, second_created = store.claim_idempotency_key(
                "session-1",
                "message-1",
                "request-2",
            )
            other_session_request, other_session_created = store.claim_idempotency_key(
                "session-2",
                "message-1",
                "request-2",
            )

        self.assertEqual((first_request, first_created), ("request-1", True))
        self.assertEqual((second_request, second_created), ("request-1", False))
        self.assertEqual((other_session_request, other_session_created), ("request-2", True))

    def test_invalid_values_fail_closed_without_partial_rows(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = ConversationStore(Path(root_text) / "conversations.sqlite3")

            with self.assertRaises(ConversationStoreError):
                store.append_event("", "request-1", "request.accepted", {})
            with self.assertRaises(ConversationStoreError):
                store.append_message("session-1", "request-1", "system", "hidden")
            with self.assertRaises(ConversationStoreError):
                store.append_message(
                    "session-1",
                    "request-1",
                    "assistant",
                    "partial",
                    status="unknown",
                )

            self.assertEqual(store.events_after("session-1"), [])
            self.assertEqual(store.history("session-1"), [])

    def test_replay_and_history_limits_are_bounded(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = ConversationStore(Path(root_text) / "conversations.sqlite3")
            for index in range(4):
                store.append_event(
                    "session-1",
                    f"request-{index}",
                    "activity.planning",
                    {"index": index},
                )
                store.append_message(
                    "session-1",
                    f"request-{index}",
                    "user",
                    f"message-{index}",
                )

            replay = store.events_after("session-1", limit=2)
            history = store.history("session-1", limit=2)

        self.assertEqual([event["sequence"] for event in replay], [1, 2])
        self.assertEqual([item["content"] for item in history], ["message-2", "message-3"])


if __name__ == "__main__":
    unittest.main()
