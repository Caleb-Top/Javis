import tempfile
import unittest
from pathlib import Path

from core.action_policy import evaluate_action
from core.tool_guardrails import ToolGuard


class ActionPolicyTests(unittest.TestCase):
    def test_perception_and_desktop_control_are_auto_allowed(self):
        for tool in ("screenshot", "camera_snapshot", "mouse_click", "keyboard_type", "set_volume"):
            self.assertEqual(evaluate_action(tool, {}).action, "allow")

    def test_deletion_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "notes.txt"
            decision = evaluate_action("file_delete", {"path": str(target)})

        self.assertEqual(decision.action, "confirm")

    def test_modifying_javis_source_requires_confirmation(self):
        root = Path("D:/Javis")
        decision = evaluate_action(
            "file_write",
            {"path": str(root / "core/agent.py"), "content": "changed"},
            app_root=root,
        )

        self.assertEqual(decision.action, "confirm")
        self.assertIn("Javis", decision.reason)

    def test_windows_core_deletion_is_permanently_denied(self):
        decision = evaluate_action(
            "file_delete",
            {"path": "C:/Windows/System32/kernel32.dll"},
        )

        self.assertEqual(decision.action, "deny")

    def test_full_access_guard_still_requires_delete_confirmation(self):
        guard = ToolGuard("critical")
        params = {"path": "D:/Documents/example.txt"}

        blocked = guard.pre_check("file_delete", params, confirmed=False)
        allowed = guard.pre_check("file_delete", params, confirmed=True)

        self.assertIn("确认", blocked)
        self.assertIsNone(allowed)

    def test_confirmation_cannot_override_windows_core_deny(self):
        guard = ToolGuard("critical")

        blocked = guard.pre_check(
            "file_delete",
            {"path": "C:/Windows/System32/config/SYSTEM"},
            confirmed=True,
        )

        self.assertIn("禁止", blocked)


if __name__ == "__main__":
    unittest.main()
