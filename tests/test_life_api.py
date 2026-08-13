import asyncio
import json
import threading
from pathlib import Path

import httpx
from fastapi import FastAPI

from core.life.api import LifeSessionPublisher, create_life_router


class _WireValue:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return dict(self.value)


class _FakeLife:
    def __init__(self):
        self.listener = None
        self.requested_limits = []

    def identity_summary(self):
        return _WireValue({"identity_id": "identity-1", "name": "Javis"})

    def snapshot(self):
        return _WireValue({"schema_version": 1, "revision": 4})

    def lineage_summary(self):
        return _WireValue({"lineage_id": "lineage-1", "instance_id": "instance-1"})

    def recent_events(self, limit=50):
        self.requested_limits.append(limit)
        return [
            {
                "schema_version": 1,
                "event_id": "event-1",
                "event_type": "life.request.completed",
                "timestamp_utc": "2026-08-12T00:00:00.000Z",
                "monotonic_offset_ms": 0,
                "source": "conversation",
                "source_event_id": "source-1",
                "session_id": "session-1",
                "request_id": "request-1",
                "correlation_id": "request-1",
                "causation_id": None,
                "sequence": 1,
                "identity_id": "identity-1",
                "instance_id": "instance-1",
                "payload": {
                    "route": "local",
                    "nested": {"api_key": "never-return", "safe": True},
                },
                "privacy_class": "user_private",
                "retention_class": "continuity",
                "confidence": 1.0,
                "provenance": {"token": "never-return", "mapper": "v1"},
                "redaction_summary": [],
            }
        ]

    def status(self):
        return {"journal_cursor": 17}

    def subscribe(self, listener):
        self.listener = listener

        def unsubscribe():
            self.listener = None

        return unsubscribe


class _FakeHub:
    def __init__(self):
        self.events = []

    def subscribed_sessions(self):
        return ("session-1", "session-2")

    async def publish_system_event(self, session_id, event_type, payload):
        self.events.append((session_id, event_type, payload))


def test_life_router_is_read_only_and_recursively_redacts_secret_keys():
    life = _FakeLife()
    app = FastAPI()
    app.include_router(create_life_router(life))

    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            identity = await client.get("/api/life/identity")
            snapshot = await client.get("/api/life/snapshot")
            lineage = await client.get("/api/life/lineage")
            events = await client.get("/api/life/events?limit=1")
            writes = [
                await client.post(path, json={})
                for path in (
                    "/api/life/identity",
                    "/api/life/snapshot",
                    "/api/life/lineage",
                    "/api/life/events",
                )
            ]
        return identity, snapshot, lineage, events, writes

    identity, snapshot, lineage, events, writes = asyncio.run(exercise())

    assert identity.json() == {
        "ok": True,
        "identity": {"identity_id": "identity-1", "name": "Javis"},
    }
    assert snapshot.json() == {
        "ok": True,
        "snapshot": {"schema_version": 1, "revision": 4},
    }
    assert lineage.json() == {
        "ok": True,
        "lineage": {"lineage_id": "lineage-1", "instance_id": "instance-1"},
    }
    assert events.json()["cursor"] == 17
    assert events.json()["events"][0]["payload"] == {
        "route": "local",
        "nested": {"safe": True},
    }
    serialized = json.dumps(events.json(), sort_keys=True)
    assert "api_key" not in serialized
    assert "never-return" not in serialized
    assert life.requested_limits == [1]
    assert [response.status_code for response in writes] == [405, 405, 405, 405]


def test_life_session_publisher_coalesces_threaded_changes_per_loop_tick():
    life = _FakeLife()
    hub = _FakeHub()
    publisher = LifeSessionPublisher(life, hub)

    async def exercise():
        assert publisher.start(asyncio.get_running_loop()) is True
        assert publisher.start(asyncio.get_running_loop()) is False

        first_snapshot = _WireValue({"schema_version": 1, "revision": 1})
        first_expression = _WireValue({"schema_version": 1, "revision": 1})
        latest_snapshot = _WireValue({"schema_version": 1, "revision": 2})
        latest_expression = _WireValue({"schema_version": 1, "revision": 2})

        def publish_from_worker():
            life.listener(first_snapshot, first_expression)
            life.listener(latest_snapshot, latest_expression)

        worker = threading.Thread(target=publish_from_worker)
        worker.start()
        worker.join()
        for _ in range(10):
            if len(hub.events) == 4:
                break
            await asyncio.sleep(0)
        await publisher.stop()
        return life.listener

    listener_after_stop = asyncio.run(exercise())

    assert hub.events == [
        (
            "session-1",
            "life.snapshot",
            {"schema_version": 1, "revision": 2},
        ),
        (
            "session-1",
            "life.expression",
            {"schema_version": 1, "revision": 2},
        ),
        (
            "session-2",
            "life.snapshot",
            {"schema_version": 1, "revision": 2},
        ),
        (
            "session-2",
            "life.expression",
            {"schema_version": 1, "revision": 2},
        ),
    ]
    assert listener_after_stop is None


def test_main_includes_each_life_route_once_and_get_only():
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8-sig"
    )

    assert source.count(
        "app.include_router(create_life_router(runtime.life))"
    ) == 1
