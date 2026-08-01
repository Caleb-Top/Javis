import tempfile
import threading
import unittest
from pathlib import Path

from core.agent_runs import AgentRunStateError, AgentRunStore
from core.events import EventBus, EventType


class AgentRunStoreTests(unittest.TestCase):
    def test_runtime_owns_and_closes_agent_run_store(self):
        from core.runtime import create_runtime

        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            runtime = create_runtime(root, startup_side_effects=False)
            store_path = runtime.agent_runs.path
            shared_bus = runtime.agent_runs.event_bus
            runtime.close()

        self.assertEqual(store_path, (root / "data" / "agent_runs" / "runs.sqlite3").resolve())
        self.assertIs(shared_bus, runtime.event_bus)

    def test_full_run_graph_persists_across_reopen(self):
        with tempfile.TemporaryDirectory() as root_text:
            database = Path(root_text) / "agent-runs.sqlite3"
            store = AgentRunStore(database)
            run = store.create_run("Prepare release", metadata={"channel": "test"})
            task = store.add_task(run["id"], "Verify build")
            step = store.start_step(
                run["id"],
                task["id"],
                "run-tests",
                kind="tool",
                input_data={"suite": "all"},
            )
            first = store.append_delta(run["id"], step["id"], "stdout", "10 passed")
            second = store.append_delta(run["id"], step["id"], "stdout", "20 passed")
            checkpoint = store.create_checkpoint(run["id"], {"phase": "tests-complete"})
            store.finish_step(step["id"], status="completed", output_data={"passed": 20})
            store.finish_task(task["id"], status="completed")
            store.finish_run(run["id"], status="completed")
            store.close()

            reopened = AgentRunStore(database)
            graph = reopened.get_run(run["id"], include_graph=True)
            reopened.close()

        self.assertEqual(first["sequence"], 1)
        self.assertEqual(second["sequence"], 2)
        self.assertEqual(checkpoint["sequence"], 1)
        self.assertEqual(graph["status"], "completed")
        self.assertEqual(graph["metadata"], {"channel": "test"})
        self.assertEqual(graph["tasks"][0]["steps"][0]["deltas"][1]["content"], "20 passed")
        self.assertEqual(graph["checkpoints"][0]["state"], {"phase": "tests-complete"})

    def test_approval_can_resume_after_explicit_approval(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = AgentRunStore(Path(root_text) / "agent-runs.sqlite3")
            run = store.create_run("Modify application")
            task = store.add_task(run["id"], "Apply patch")
            step = store.start_step(run["id"], task["id"], "write-file")
            approval = store.request_approval(
                run["id"],
                step["id"],
                action="file_write",
                request={"path": "core/agent.py"},
            )

            resolved = store.resolve_approval(approval["id"], approved=True, response={"by": "Eric"})
            graph = store.get_run(run["id"], include_graph=True)
            store.close()

        self.assertEqual(resolved["status"], "approved")
        self.assertEqual(graph["status"], "running")
        self.assertEqual(graph["tasks"][0]["steps"][0]["status"], "running")
        self.assertEqual(graph["approvals"][0]["response"], {"by": "Eric"})

    def test_resume_auto_denies_unresolved_approvals_and_blocks_run(self):
        with tempfile.TemporaryDirectory() as root_text:
            database = Path(root_text) / "agent-runs.sqlite3"
            store = AgentRunStore(database)
            run = store.create_run("Delete generated build")
            task = store.add_task(run["id"], "Cleanup")
            step = store.start_step(run["id"], task["id"], "delete-files")
            approval = store.request_approval(run["id"], step["id"], action="file_delete")
            store.create_checkpoint(run["id"], {"safe": "before-delete"})
            store.close()

            reopened = AgentRunStore(database)
            resumed = reopened.resume_run(run["id"])
            approval_after = reopened.get_approval(approval["id"])
            reopened.close()

        self.assertEqual(resumed["run"]["status"], "blocked")
        self.assertEqual(resumed["checkpoint"]["state"], {"safe": "before-delete"})
        self.assertEqual(approval_after["status"], "denied")
        self.assertEqual(approval_after["response"]["reason"], "auto-denied on resume")

    def test_invalid_transitions_and_writes_to_finished_steps_fail(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = AgentRunStore(Path(root_text) / "agent-runs.sqlite3")
            run = store.create_run("Transition checks")
            task = store.add_task(run["id"], "Task")
            step = store.start_step(run["id"], task["id"], "Step")
            store.finish_step(step["id"], status="completed")

            with self.assertRaises(AgentRunStateError):
                store.append_delta(run["id"], step["id"], "text", "too late")
            store.cancel_run(run["id"], reason="test cancel")
            with self.assertRaises(AgentRunStateError):
                store.add_task(run["id"], "After cancel")
            with self.assertRaises(AgentRunStateError):
                store.finish_run(run["id"], status="completed")
            store.close()

    def test_cancel_propagates_to_tasks_steps_and_pending_approvals(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = AgentRunStore(Path(root_text) / "agent-runs.sqlite3")
            run = store.create_run("Cancel graph")
            task = store.add_task(run["id"], "Task")
            step = store.start_step(run["id"], task["id"], "Step")
            store.request_approval(run["id"], step["id"], action="system_execute")

            store.cancel_run(run["id"], reason="user stopped")
            graph = store.get_run(run["id"], include_graph=True)
            store.close()

        self.assertEqual(graph["status"], "cancelled")
        self.assertEqual(graph["tasks"][0]["status"], "cancelled")
        self.assertEqual(graph["tasks"][0]["steps"][0]["status"], "cancelled")
        self.assertEqual(graph["approvals"][0]["status"], "cancelled")

    def test_concurrent_deltas_receive_unique_stable_sequences(self):
        with tempfile.TemporaryDirectory() as root_text:
            store = AgentRunStore(Path(root_text) / "agent-runs.sqlite3")
            run = store.create_run("Concurrent stream")
            task = store.add_task(run["id"], "Stream")
            step = store.start_step(run["id"], task["id"], "tokens")
            threads = [
                threading.Thread(
                    target=store.append_delta,
                    args=(run["id"], step["id"], "text", f"token-{index}"),
                )
                for index in range(30)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            graph = store.get_run(run["id"], include_graph=True)
            store.close()

        sequences = [item["sequence"] for item in graph["tasks"][0]["steps"][0]["deltas"]]
        self.assertEqual(sequences, list(range(1, 31)))

    def test_lifecycle_events_are_correlated_to_the_run(self):
        with tempfile.TemporaryDirectory() as root_text:
            bus = EventBus()
            store = AgentRunStore(Path(root_text) / "agent-runs.sqlite3", event_bus=bus)
            run = store.create_run("Event graph")
            task = store.add_task(run["id"], "Task")
            step = store.start_step(run["id"], task["id"], "Step")
            store.append_delta(run["id"], step["id"], "text", "working")
            store.create_checkpoint(run["id"], {"step": 1})
            store.close()

        created = bus.history(EventType.AGENT_RUN_CREATED)[0]
        delta = bus.history(EventType.AGENT_DELTA_APPENDED)[0]
        checkpoint = bus.history(EventType.AGENT_CHECKPOINT_CREATED)[0]
        self.assertEqual(created.correlation_id, run["id"])
        self.assertEqual(delta.correlation_id, run["id"])
        self.assertEqual(checkpoint.correlation_id, run["id"])


if __name__ == "__main__":
    unittest.main()
