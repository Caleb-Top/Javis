import asyncio
import threading
import unittest

from voice.runtime_diagnostics import VoiceDiagnosticsCollector


class VoiceDiagnosticsCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_collect_returns_the_versioned_bounded_schema(self):
        collector = VoiceDiagnosticsCollector(
            capture_getter=lambda: {"available": True},
            continuous_getter=lambda: {"running": True},
            gateway_getter=lambda: {"active_tasks": 3},
            playback_getter=lambda: {"active": False},
            stt_getter=lambda: {"available": True},
            tts_getter=lambda: {"available": True},
        )

        result = await collector.collect()

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(
            set(result),
            {
                "schema_version",
                "capture",
                "continuous",
                "gateway",
                "playback",
                "stt",
                "tts",
            },
        )
        self.assertTrue(result["continuous"]["running"])
        self.assertEqual(result["gateway"]["active_tasks"], 3)

    async def test_slow_capture_probe_runs_off_loop_and_concurrent_reads_share_cache(self):
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        def slow_capture():
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(timeout=2)
            return {"available": True, "probe": calls}

        collector = VoiceDiagnosticsCollector(
            capture_getter=slow_capture,
            continuous_getter=lambda: {},
            gateway_getter=lambda: {},
            playback_getter=lambda: {},
            stt_getter=lambda: {},
            tts_getter=lambda: {},
            capture_ttl=30,
        )
        first = asyncio.create_task(collector.collect())
        second = asyncio.create_task(collector.collect())

        await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), timeout=1.5)
        heartbeat_ran = False

        async def heartbeat():
            nonlocal heartbeat_ran
            await asyncio.sleep(0)
            heartbeat_ran = True

        await asyncio.wait_for(heartbeat(), timeout=0.2)
        self.assertTrue(heartbeat_ran)
        release.set()
        first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(calls, 1)
        self.assertEqual(first_result["capture"], second_result["capture"])

    async def test_new_collector_does_not_inherit_a_previous_process_cache(self):
        calls = 0

        def capture():
            nonlocal calls
            calls += 1
            return {"generation": calls}

        def make_collector():
            return VoiceDiagnosticsCollector(
                capture_getter=capture,
                continuous_getter=lambda: {},
                gateway_getter=lambda: {},
                playback_getter=lambda: {},
                stt_getter=lambda: {},
                tts_getter=lambda: {},
                capture_ttl=30,
            )

        first = await make_collector().collect()
        second = await make_collector().collect()

        self.assertEqual(first["capture"]["generation"], 1)
        self.assertEqual(second["capture"]["generation"], 2)


if __name__ == "__main__":
    unittest.main()
