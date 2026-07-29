import json
import tempfile
import unittest
from pathlib import Path


class AppPhaseSupervisorTests(unittest.TestCase):
    def make_supervisor(self, root: Path):
        from scripts.app_phase_supervisor import BuildSupervisor

        return BuildSupervisor(
            state_path=root / "state.json",
            log_path=root / "events.jsonl",
            max_same_failures=3,
        )

    def test_rejects_phase_progression_until_previous_phase_completes(self):
        with tempfile.TemporaryDirectory() as td:
            supervisor = self.make_supervisor(Path(td))

            result = supervisor.record("phase-3", "start", "started", "too early")

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "phase-order")

    def test_repeated_failure_signature_trips_bounded_loop_fuse(self):
        with tempfile.TemporaryDirectory() as td:
            supervisor = self.make_supervisor(Path(td))
            supervisor.record("phase-2", "start", "started", "begin")

            first = supervisor.record("phase-2", "tests", "failed", "boom", "same-error")
            second = supervisor.record("phase-2", "tests", "failed", "boom", "same-error")
            third = supervisor.record("phase-2", "tests", "failed", "boom", "same-error")

            self.assertEqual(first["failure_count"], 1)
            self.assertEqual(second["failure_count"], 2)
            self.assertTrue(third["blocked"])
            self.assertEqual(supervisor.status()["run_status"], "blocked")

    def test_successful_checkpoint_resets_failure_counter(self):
        with tempfile.TemporaryDirectory() as td:
            supervisor = self.make_supervisor(Path(td))
            supervisor.record("phase-2", "start", "started", "begin")
            supervisor.record("phase-2", "tests", "failed", "boom", "same-error")

            supervisor.record("phase-2", "implementation", "checkpoint", "green")
            result = supervisor.record("phase-2", "tests", "failed", "boom", "same-error")

            self.assertEqual(result["failure_count"], 1)
            self.assertFalse(result["blocked"])

    def test_log_redacts_credentials_and_state_write_is_valid_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            supervisor = self.make_supervisor(root)

            supervisor.record(
                "phase-2",
                "probe",
                "started",
                "Authorization: Bearer sk-test-secret token=abc123 password=hunter2",
            )

            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            log_text = (root / "events.jsonl").read_text(encoding="utf-8")
            self.assertEqual(state["current_phase"], "phase-2")
            self.assertNotIn("sk-test-secret", log_text)
            self.assertNotIn("abc123", log_text)
            self.assertNotIn("hunter2", log_text)
            self.assertIn("[REDACTED]", log_text)

    def test_completed_phase_allows_next_phase(self):
        with tempfile.TemporaryDirectory() as td:
            supervisor = self.make_supervisor(Path(td))
            supervisor.record("phase-2", "start", "started", "begin")
            supervisor.record("phase-2", "verify", "completed", "passed")

            result = supervisor.record("phase-3", "start", "started", "begin")

            self.assertTrue(result["ok"])
            self.assertEqual(supervisor.status()["current_phase"], "phase-3")

    def test_final_verification_marks_run_completed(self):
        with tempfile.TemporaryDirectory() as td:
            supervisor = self.make_supervisor(Path(td))
            for phase in ("phase-2", "phase-3", "phase-4", "verification"):
                supervisor.record(phase, "start", "started", "begin")
                supervisor.record(phase, "verify", "completed", "passed")

            self.assertEqual(supervisor.status()["run_status"], "completed")

    def test_completed_app_flow_can_enter_release_hardening(self):
        with tempfile.TemporaryDirectory() as td:
            supervisor = self.make_supervisor(Path(td))
            for phase in ("phase-2", "phase-3", "phase-4", "verification"):
                supervisor.record(phase, "start", "started", "begin")
                supervisor.record(phase, "verify", "completed", "passed")

            phase_five = supervisor.record("phase-5", "start", "started", "release hardening")
            supervisor.record("phase-5", "verify", "completed", "passed")
            release = supervisor.record("release-verification", "start", "started", "final release audit")

            self.assertTrue(phase_five["ok"])
            self.assertTrue(release["ok"])
            self.assertEqual(supervisor.status()["current_phase"], "release-verification")

    def test_completed_release_flow_can_enter_build_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            supervisor = self.make_supervisor(Path(td))
            phases = ("phase-2", "phase-3", "phase-4", "verification", "phase-5", "release-verification")
            for phase in phases:
                supervisor.record(phase, "start", "started", "begin")
                supervisor.record(phase, "verify", "completed", "passed")

            phase_six = supervisor.record("phase-6", "start", "started", "build readiness")
            supervisor.record("phase-6", "verify", "completed", "passed")
            verification = supervisor.record(
                "build-readiness-verification", "start", "started", "final readiness audit"
            )

            self.assertTrue(phase_six["ok"])
            self.assertTrue(verification["ok"])
            self.assertEqual(supervisor.status()["current_phase"], "build-readiness-verification")


if __name__ == "__main__":
    unittest.main()
