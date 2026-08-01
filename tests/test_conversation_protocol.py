import unittest

from core.conversation_protocol import (
    ConversationProtocolError,
    legacy_wire_events,
    normalize_client_message,
)


class ConversationProtocolTests(unittest.TestCase):
    def test_legacy_message_normalizes_to_conversation_command(self):
        command = normalize_client_message({
            "type": "message",
            "payload": {
                "text": "hello",
                "session_id": "s1",
                "request_id": "r1",
            },
        })

        self.assertEqual(command.type, "conversation.message")
        self.assertEqual(command.session_id, "s1")
        self.assertEqual(command.request_id, "r1")
        self.assertEqual(command.idempotency_key, "r1")
        self.assertTrue(command.legacy)

    def test_canonical_message_requires_object_payload_and_valid_identifiers(self):
        with self.assertRaises(ConversationProtocolError) as invalid_payload:
            normalize_client_message({"type": "conversation.message", "payload": []})
        with self.assertRaises(ConversationProtocolError) as missing_session:
            normalize_client_message({
                "type": "conversation.message",
                "payload": {"text": "hello", "request_id": "r1"},
            })
        with self.assertRaises(ConversationProtocolError) as oversized_request:
            normalize_client_message({
                "type": "conversation.message",
                "payload": {
                    "text": "hello",
                    "session_id": "s1",
                    "request_id": "r" * 257,
                },
            })

        self.assertEqual(invalid_payload.exception.code, "invalid_payload")
        self.assertEqual(missing_session.exception.code, "invalid_session_id")
        self.assertEqual(oversized_request.exception.code, "invalid_request_id")

    def test_unknown_command_is_rejected_with_structured_code(self):
        with self.assertRaises(ConversationProtocolError) as captured:
            normalize_client_message({"type": "launch_everything", "payload": {}})

        self.assertEqual(captured.exception.code, "unknown_command")

    def test_activity_event_has_legacy_thinking_projection(self):
        projected = legacy_wire_events({
            "type": "activity.planning",
            "session_id": "s1",
            "request_id": "r1",
            "sequence": 3,
            "payload": {"detail": "Planning"},
        })

        self.assertEqual(projected, [{
            "type": "thinking",
            "content": "Planning",
            "detail": "Planning",
            "activity": "planning",
            "session_id": "s1",
            "request_id": "r1",
            "sequence": 3,
        }])

    def test_terminal_and_approval_events_keep_legacy_contract(self):
        approval = legacy_wire_events({
            "type": "approval.required",
            "session_id": "s1",
            "request_id": "r1",
            "sequence": 4,
            "payload": {
                "approval_id": "a1",
                "tool": "file_write",
                "reason": "confirm",
                "params": {"path": "x"},
            },
        })
        cancelled = legacy_wire_events({
            "type": "request.cancelled",
            "session_id": "s1",
            "request_id": "r1",
            "sequence": 5,
            "payload": {"reason": "voice barge-in"},
        })

        self.assertEqual(approval[0]["type"], "confirm_required")
        self.assertEqual(approval[0]["approval_id"], "a1")
        self.assertEqual(cancelled[0]["type"], "done")
        self.assertTrue(cancelled[0]["cancelled"])


if __name__ == "__main__":
    unittest.main()
