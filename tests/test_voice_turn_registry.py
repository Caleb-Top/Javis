import pytest

from voice.turn_registry import VoiceTurnRegistry, VoiceTurnRegistryError


def _register(registry, **overrides):
    values = {
        "session_id": "session-1",
        "owner_generation": 2,
        "voice_sequence": 7,
        "voice_turn": 3,
        "transcript": "  Javis， 我在这里  ",
    }
    values.update(overrides)
    return registry.register(**values)


def test_voice_turn_reserve_commit_is_single_use_and_returns_verified_provenance():
    registry = VoiceTurnRegistry("boot-1")
    reference = _register(registry)

    provenance = registry.reserve(
        reference,
        transcript="Javis， 我在这里",
        session_id="session-1",
        request_id="request-1",
    )
    committed = registry.commit(
        reference,
        session_id="session-1",
        request_id="request-1",
    )

    assert provenance.to_dict() == committed.to_dict()
    assert provenance.to_dict() == {
        "modality": "voice",
        "verification": "server_verified",
        "runtime_boot_id": "boot-1",
        "source_session_id": "session-1",
        "owner_generation": 2,
        "voice_sequence": 7,
        "voice_turn": 3,
    }
    with pytest.raises(VoiceTurnRegistryError, match="committed"):
        registry.reserve(
            reference,
            transcript="Javis， 我在这里",
            session_id="session-1",
            request_id="request-2",
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"transcript": "different"}, "transcript"),
        ({"session_id": "session-2"}, "session"),
    ],
)
def test_voice_turn_rejects_tampering(mutation, message):
    registry = VoiceTurnRegistry("boot-1")
    reference = _register(registry)
    arguments = {
        "transcript": "Javis， 我在这里",
        "session_id": "session-1",
        "request_id": "request-1",
    }
    arguments.update(mutation)

    with pytest.raises(VoiceTurnRegistryError, match=message):
        registry.reserve(reference, **arguments)


def test_voice_turn_rejects_reference_tampering_and_cross_boot():
    registry = VoiceTurnRegistry("boot-1")
    reference = _register(registry)
    altered = dict(reference, owner_generation=99)

    with pytest.raises(VoiceTurnRegistryError):
        registry.reserve(
            altered,
            transcript="Javis， 我在这里",
            session_id="session-1",
            request_id="request-1",
        )
    with pytest.raises(VoiceTurnRegistryError, match="boot"):
        VoiceTurnRegistry("boot-2").reserve(
            reference,
            transcript="Javis， 我在这里",
            session_id="session-1",
            request_id="request-1",
        )


def test_voice_turn_rollback_allows_safe_retry():
    registry = VoiceTurnRegistry("boot-1")
    reference = _register(registry)
    registry.reserve(
        reference,
        transcript="Javis， 我在这里",
        session_id="session-1",
        request_id="request-1",
    )

    assert registry.rollback(
        reference, session_id="session-1", request_id="request-1"
    )
    registry.reserve(
        reference,
        transcript="Javis， 我在这里",
        session_id="session-1",
        request_id="request-2",
    )


def test_voice_turn_expiry_and_capacity_are_bounded():
    now = [10.0]
    registry = VoiceTurnRegistry(
        "boot-1", ttl_seconds=2, capacity=2, clock=lambda: now[0]
    )
    expired = _register(registry, voice_sequence=1)
    _register(registry, voice_sequence=2)
    _register(registry, voice_sequence=3)
    assert registry.stats()["entries"] == 2

    now[0] = 13.0
    with pytest.raises(VoiceTurnRegistryError):
        registry.reserve(
            expired,
            transcript="Javis， 我在这里",
            session_id="session-1",
            request_id="request-1",
        )
    assert registry.stats()["entries"] == 0
