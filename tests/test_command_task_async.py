import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from control.command_tasks import CommandTaskRunner


class FakeProcess:
    def __init__(self):
        self.pid = 4242
        self.returncode = None
        self.terminated = False
        self._done = threading.Event()

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        self._done.wait(timeout or 1)
        return ("partial output", "")

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self._done.set()

    def kill(self):
        self.terminate()


class AsyncCommandTaskTests(unittest.TestCase):
    def test_started_command_can_be_polled_and_cancelled(self):
        with tempfile.TemporaryDirectory() as td:
            runner = CommandTaskRunner(Path(td), permission_level="trusted")
            process = FakeProcess()
            with (
                patch("control.command_tasks.subprocess.Popen", return_value=process),
                patch("control.command_tasks.subprocess.run", side_effect=lambda *args, **kwargs: process.terminate()),
            ):
                started = runner.start_command("echo hello", shell="cmd", cwd=td)
                self.assertTrue(started["ok"])
                task_id = started["task"]["task_id"]
                self.assertEqual(runner.get_task(task_id)["task"]["status"], "running")

                cancelled = runner.cancel_task(task_id)
                self.assertTrue(cancelled["ok"])
                for _ in range(50):
                    result = runner.get_task(task_id)
                    if result["task"]["status"] == "cancelled":
                        break
                    time.sleep(0.01)

            self.assertTrue(process.terminated)
            self.assertEqual(result["task"]["status"], "cancelled")

    def test_unknown_task_cannot_be_cancelled(self):
        with tempfile.TemporaryDirectory() as td:
            runner = CommandTaskRunner(Path(td), permission_level="trusted")

            result = runner.cancel_task("missing")

            self.assertFalse(result["ok"])
            self.assertIn("not found", result["error"])
