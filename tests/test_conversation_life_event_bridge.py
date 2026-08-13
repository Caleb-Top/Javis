import asyncio
import json

import pytest

from core.agent_runs import AgentRunStore
from core.conversation_hub import ConversationHub, ConversationRequest
from core.conversation_store import ConversationStore, ConversationStoreError
from core.events import EventBus
from core.life.event_adapter import LifeEventAdapter


def _hub(tmp_path):
    bus = EventBus()
    hub = ConversationHub(
        ConversationStore(tmp_path / "conversations.sqlite3"),
        AgentRunStore(tmp_path / "runs.sqlite3"),
        event_bus=bus,
    )
    return hub, bus


def test_public_system_publish_is_durable_allowlisted_and_session_scoped(tmp_path):
    hub, bus = _hub(tmp_path)

    async def exercise():
        subscription = await hub.attach(" session-1 ")
        assert hub.subscribed_sessions() == ("session-1",)
        await hub.publish_system_event(
            "session-1",
            "life.snapshot",
            {"revision": 2},
        )
        event = await subscription.get()
        with pytest.raises(ConversationStoreError):
            await hub.publish_system_event(
                "session-1",
                "request.completed",
                {"success": True},
            )
        await subscription.close()
        assert hub.subscribed_sessions() == ()
        await hub.shutdown()
        return event

    event = asyncio.run(exercise())

    assert event["type"] == "life.snapshot"
    assert event["request_id"] == ""
    assert event["payload"] == {"revision": 2}
    assert hub.store.events_after("session-1") == [event]
    assert bus.history() == []


def test_conversation_lifecycle_reaches_bus_without_private_text(tmp_path):
    hub, bus = _hub(tmp_path)
    seen = []
    bus.subscribe("*", seen.append)

    async def runner(request, token):
        await token.checkpoint()
        yield {"type": "text_delta", "text": "private model sentence"}
        yield {"type": "done", "success": True, "detail": "private detail"}

    async def exercise():
        await hub.submit(
            ConversationRequest(
                session_id="session-1",
                request_id="request-1",
                text="private user sentence",
                interaction_mode="live",
            ),
            runner,
        )
        await hub.wait_for_terminal("request-1")
        await hub.shutdown()

    asyncio.run(exercise())

    lifecycle = [event for event in seen if event.source == "conversation"]
    assert [event.type for event in lifecycle] == [
        "request.accepted",
        "request.completed",
    ]
    serialized = json.dumps([event.payload for event in lifecycle])
    assert "private user sentence" not in serialized
    assert "private model sentence" not in serialized
    assert "private detail" not in serialized

    accepted = lifecycle[0]
    canonical = hub.store.events_after("session-1", sequence=0)
    canonical_accepted = next(
        event for event in canonical if event["type"] == "request.accepted"
    )
    assert accepted.payload == {
        "source_event_id": canonical_accepted["event_id"],
        "source_sequence": canonical_accepted["sequence"],
        "source_sequence_domain": "conversation_store:session-1",
        "session_id": "session-1",
        "request_id": "request-1",
        "correlation_id": "request-1",
        "interaction_mode": "live",
    }
    assert accepted.id != accepted.payload["source_event_id"]
    assert accepted.sequence == 1
    assert accepted.payload["source_sequence_domain"].startswith(
        "conversation_store:"
    )


def test_bridge_excludes_tool_data_free_form_activity_and_response_delta(tmp_path):
    hub, bus = _hub(tmp_path)
    seen = []
    bus.subscribe("*", seen.append)

    async def runner(request, token):
        yield {
            "type": "activity",
            "activity": "planning",
            "detail": "private plan",
        }
        yield {
            "type": "tool_start",
            "tool": "system_info",
            "params": {"api_key": "never-publish"},
        }
        yield {
            "type": "tool_result",
            "tool": "system_info",
            "success": True,
            "data": "private tool output",
        }
        yield {"type": "text_delta", "text": "private delta"}
        yield {"type": "done", "success": True}

    async def exercise():
        await hub.submit(
            ConversationRequest("session-1", "request-1", "private", "code"),
            runner,
        )
        await hub.wait_for_terminal("request-1")
        await hub._publish(
            "session-1",
            "request-1",
            "approval.required",
            {
                "approval_id": "approval-1",
                "tool": "file_write",
                "reason": "private approval reason",
                "params": {"path": "private-path"},
            },
        )
        await hub.shutdown()

    asyncio.run(exercise())

    bridged = [event for event in seen if event.source == "conversation"]
    assert [event.type for event in bridged] == [
        "request.accepted",
        "activity.tool_started",
        "activity.tool_completed",
        "request.completed",
        "approval.required",
    ]
    started = bridged[1].payload
    completed = bridged[2].payload
    approval = bridged[4].payload
    assert started["tool"] == "system_info"
    assert completed["tool"] == "system_info"
    assert completed["success"] is True
    assert approval["approval_id"] == "approval-1"
    assert approval["tool"] == "file_write"
    serialized = json.dumps([event.payload for event in bridged])
    for forbidden in (
        "planning",
        "private plan",
        "api_key",
        "never-publish",
        "private tool output",
        "private delta",
        "private approval reason",
        "private-path",
    ):
        assert forbidden not in serialized


def test_bridge_publishes_only_after_canonical_append_succeeds(tmp_path, monkeypatch):
    hub, bus = _hub(tmp_path)
    seen = []
    bus.subscribe("*", seen.append)

    def fail_append(*args, **kwargs):
        raise ConversationStoreError("disk unavailable")

    monkeypatch.setattr(hub.store, "append_event", fail_append)

    with pytest.raises(ConversationStoreError, match="disk unavailable"):
        asyncio.run(
            hub._publish(
                "session-1",
                "request-1",
                "request.completed",
                {"detail": "private"},
            )
        )

    assert seen == []


def test_life_adapter_preserves_canonical_conversation_source_domain():
    bus = EventBus()
    bridged = bus.publish(
        "request.accepted",
        {
            "source_event_id": "canonical-event-1",
            "source_sequence": 7,
            "source_sequence_domain": "conversation_store:session-1",
            "session_id": "session-1",
            "request_id": "request-1",
            "correlation_id": "request-1",
            "interaction_mode": "live",
        },
        source="conversation",
        correlation_id="request-1",
        causation_id="canonical-event-1",
    )

    mapped = LifeEventAdapter(
        identity_id="identity-1",
        instance_id="instance-1",
        now=lambda: 1.0,
    ).map(bridged)[0]

    assert mapped.source_event_id == "canonical-event-1"
    assert mapped.sequence == bridged.sequence
    assert mapped.provenance["source_sequence"] == 7
    assert mapped.provenance["source_sequence_domain"] == (
        "conversation_store:session-1"
    )
    assert mapped.provenance["transport_event_id"] == bridged.id
    assert mapped.provenance["transport_sequence"] == bridged.sequence
