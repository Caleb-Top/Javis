import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_conversation_watchdogs.py"


def _node_runtime() -> str | None:
    configured = os.environ.get("JAVIS_TEST_NODE", "").strip()
    candidates = [
        configured,
        str(ROOT / "tools" / "nodejs" / "node.exe"),
        r"G:\Javis\tools\nodejs\node.exe",
        shutil.which("node") or "",
    ]
    return next((value for value in candidates if value and Path(value).is_file()), None)


class ConversationWatchdogHarnessTests(unittest.TestCase):
    def test_node_release_harness_never_sets_timeout_overrides_directly(self):
        source = (ROOT / "scripts" / "verify_conversation_watchdogs.ts").read_text(
            encoding="utf-8"
        )

        self.assertIn("...backendClientTimeoutOverrides(timeScale)", source)
        self.assertNotIn("requestTimeouts:", source)

    def test_release_timing_omits_timeout_overrides_but_scaled_mode_injects_them(self):
        node = _node_runtime()
        if node is None:
            self.skipTest("Node 22 runtime is unavailable")
        script = """
import {
  backendClientTimeoutOverrides,
  watchdogTiming,
} from './scripts/conversation_watchdog_timing.ts';
const release = backendClientTimeoutOverrides(1);
const scaled = backendClientTimeoutOverrides(0.01);
process.stdout.write(JSON.stringify({
  releaseHasOverride: Object.prototype.hasOwnProperty.call(release, 'requestTimeouts'),
  releaseTiming: watchdogTiming(1),
  scaled,
}));
"""
        result = subprocess.run(
            [
                node,
                "--experimental-strip-types",
                "--input-type=module",
                "--eval",
                script,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        self.assertFalse(report["releaseHasOverride"])
        self.assertEqual(
            report["releaseTiming"],
            {"acceptedMs": 3000, "firstResponseMs": 15000, "overallMs": 60000},
        )
        self.assertEqual(
            report["scaled"]["requestTimeouts"],
            {"acceptedMs": 300, "firstResponseMs": 1500, "overallMs": 3000},
        )

    def test_real_loopback_websocket_harness_is_repeatable_and_cleans_every_request(self):
        node = _node_runtime()
        if node is None:
            self.skipTest("Node 22 runtime is unavailable")
        with tempfile.TemporaryDirectory(
            prefix=".watchdog-harness-",
            dir=ROOT,
        ) as work_dir:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--node",
                    node,
                    "--work-dir",
                    work_dir,
                    "--time-scale",
                    "0.01",
                    "--passes",
                    "2",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["qualification"], "source-regression")
        self.assertEqual(report["passes"], 2)
        self.assertEqual(report["active_requests"], 0)
        self.assertEqual(report["subscribers"], 0)
        self.assertEqual(
            [scenario["phase"] for scenario in report["runs"][-1]["scenarios"]],
            ["accepted", "first-response", "overall"],
        )
        for run in report["runs"]:
            self.assertEqual(
                [scenario["timeout_count"] for scenario in run["scenarios"]],
                [1, 1, 1],
            )
            self.assertEqual(
                [scenario["timeout_source"] for scenario in run["scenarios"]],
                ["scaled-harness-override"] * 3,
            )


if __name__ == "__main__":
    unittest.main()
