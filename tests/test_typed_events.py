import threading
import unittest

from core.events import EventBus, EventType


class TypedEventBusTests(unittest.TestCase):
    def test_string_publish_and_history_remain_compatible(self):
        bus = EventBus(max_history=3)
        seen = []
        bus.subscribe("memory.write", seen.append)

        event = bus.publish("memory.write", {"key": "name"}, source="memory")

        self.assertEqual(event.type, "memory.write")
        self.assertEqual(event.payload, {"key": "name"})
        self.assertEqual(seen, [event])
        self.assertEqual(bus.history("memory.write"), [event])

    def test_typed_publish_carries_version_and_correlation_metadata(self):
        bus = EventBus()

        started = bus.publish(
            EventType.TOOL_STARTED,
            {"tool": "screenshot"},
            source="tools",
            correlation_id="run-42",
        )
        completed = bus.publish(
            EventType.TOOL_COMPLETED,
            {"tool": "screenshot"},
            source="tools",
            correlation_id="run-42",
            causation_id=started.id,
        )

        self.assertEqual(started.type, "tool.started")
        self.assertEqual(started.schema_version, 1)
        self.assertEqual(started.correlation_id, "run-42")
        self.assertIsNone(started.causation_id)
        self.assertEqual(completed.causation_id, started.id)
        self.assertEqual(completed.sequence, started.sequence + 1)

    def test_sequences_are_unique_and_monotonic_across_threads(self):
        bus = EventBus(max_history=200)
        threads = [
            threading.Thread(target=bus.publish, args=(EventType.RUNTIME_STATUS, {"index": index}))
            for index in range(40)
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        sequences = sorted(event.sequence for event in bus.history())
        self.assertEqual(sequences, list(range(1, 41)))

    def test_enum_subscription_and_wildcard_subscription_receive_same_event(self):
        bus = EventBus()
        typed = []
        wildcard = []
        bus.subscribe(EventType.APPROVAL_REQUESTED, typed.append)
        bus.subscribe("*", wildcard.append)

        event = bus.publish(EventType.APPROVAL_REQUESTED, {"approval_id": "a1"})

        self.assertEqual(typed, [event])
        self.assertEqual(wildcard, [event])


if __name__ == "__main__":
    unittest.main()
