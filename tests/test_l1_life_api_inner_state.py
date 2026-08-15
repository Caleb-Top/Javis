import asyncio
import threading

import httpx
from fastapi import FastAPI

from core.life.api import LifeSessionPublisher, create_life_router


class _WireValue:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return dict(self.value)


def _inner_state_wire(revision: int) -> dict:
    return {
        "schema_version": 1,
        "source_life_snapshot_revision": revision,
        "identity_id": "identity-1",
        "instance_id": "instance-1",
        "generated_at_utc": "2026-08-12T00:00:03.000Z",
        "phase": "engaged",
        "attention": {
            "mode": "engaged",
            "target_kind": "request",
            "target_id": "request-1",
            "priority": 70,
            "since_utc": "2026-08-12T00:00:01.000Z",
            "expires_at_utc": "2026-08-12T00:01:00.000Z",
            "source_observation_id": "observation-1",
        },
        "homeostasis": {
            "updated_at_utc": "2026-08-12T00:00:03.000Z",
            "activation": 0.4,
            "cognitive_load": 0.25,
            "certainty": 0.5,
            "caution": 0.1,
            "curiosity": 0.25,
            "blockedness": 0.0,
            "social_presence": 0.3,
        },
        "affects": [
            {
                "kind": "cautious",
                "intensity": 0.4,
                "confidence": 0.9,
                "reason_code": "tool_risk_observed",
                "evidence_ids": ["evidence-1"],
                "valid_until_utc": "2026-08-12T00:00:30.000Z",
            }
        ],
        "presence": {
            "mode": "engaged",
            "intensity": 0.6,
            "session_id": "session-1",
            "source_observation_id": "observation-1",
            "reason_code": "request_started",
            "since_utc": "2026-08-12T00:00:01.000Z",
            "expires_at_utc": "2026-08-12T00:01:00.000Z",
        },
        "last_observation_id": "observation-1",
        "degraded": False,
    }


class _FakeLife:
    def __init__(self, *, snapshot_revision: int = 4, inner_state_revision: int | None = 4):
        self.listener = None
        self.snapshot_revision = snapshot_revision
        self.inner_state_revision = inner_state_revision

    def identity_summary(self):
        return _WireValue({"identity_id": "identity-1", "name": "Javis"})

    def snapshot(self):
        return _WireValue({"schema_version": 1, "revision": self.snapshot_revision})

    def lineage_summary(self):
        return _WireValue({"lineage_id": "lineage-1", "instance_id": "instance-1"})

    def recent_events(self, limit=50):
        return []

    def status(self):
        return {"journal_cursor": 17}

    def subscribe(self, listener):
        self.listener = listener

        def unsubscribe():
            self.listener = None

        return unsubscribe

    def inner_state_snapshot(self):
        if self.inner_state_revision is None:
            raise RuntimeError("L1 unavailable")
        return _WireValue(_inner_state_wire(self.inner_state_revision))


class _FakeHub:
    def __init__(self):
        self.events = []

    def subscribed_sessions(self):
        return ("session-1",)

    async def publish_system_event(self, session_id, event_type, payload):
        self.events.append((session_id, event_type, payload))


def test_life_router_exposes_authoritative_inner_state_and_rejects_stale_or_missing_l1():
    authoritative_life = _FakeLife(snapshot_revision=4, inner_state_revision=4)
    stale_life = _FakeLife(snapshot_revision=4, inner_state_revision=3)
    unavailable_life = _FakeLife(snapshot_revision=4, inner_state_revision=None)

    async def exercise(life):
        app = FastAPI()
        app.include_router(create_life_router(life))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.get("/api/life/inner-state")

    authoritative = asyncio.run(exercise(authoritative_life))
    stale = asyncio.run(exercise(stale_life))
    unavailable = asyncio.run(exercise(unavailable_life))

    assert authoritative.status_code == 200
    assert authoritative.json() == {
        "ok": True,
        "inner_state": _inner_state_wire(4),
    }
    assert stale.status_code == 503
    assert unavailable.status_code == 503


def test_life_session_publisher_emits_inner_state_only_when_revision_matches_snapshot():
    life = _FakeLife(snapshot_revision=4, inner_state_revision=4)
    hub = _FakeHub()
    publisher = LifeSessionPublisher(life, hub)

    async def exercise():
        assert publisher.start(asyncio.get_running_loop()) is True
        snapshot = _WireValue({"schema_version": 1, "revision": 4})
        expression = _WireValue({"schema_version": 1, "revision": 4})

        def publish_from_worker():
            life.listener(snapshot, expression)

        worker = threading.Thread(target=publish_from_worker)
        worker.start()
        worker.join()
        for _ in range(10):
            if len(hub.events) == 3:
                break
            await asyncio.sleep(0)
        await publisher.stop()

    asyncio.run(exercise())

    assert hub.events == [
        ("session-1", "life.snapshot", {"schema_version": 1, "revision": 4}),
        ("session-1", "life.expression", {"schema_version": 1, "revision": 4}),
        ("session-1", "life.inner_state.changed", _inner_state_wire(4)),
    ]
