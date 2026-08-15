from __future__ import annotations

import io
import math
import struct
import threading
import time
import wave

import pytest

from core.life.l1.clock import ManualClock
from core.life.l1.contracts import PlaybackOutcome
from voice.native_playback import NativePlaybackManager
from voice.playback_events import PlaybackLifecyclePublisher


NOW = "2026-08-12T08:00:00.000Z"


def _wav_tone(duration_ms: int = 100, rate: int = 16_000) -> bytes:
    samples = [
        int(4000 * math.sin(2 * math.pi * 220 * index / rate))
        for index in range(rate * duration_ms // 1000)
    ]
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


class FakePlayer:
    def __init__(self, *, fail: bool = False):
        self.played = []
        self.stops = 0
        self.fail = fail

    def play(self, path):
        self.played.append(path)
        if self.fail:
            raise RuntimeError("player unavailable")

    def stop(self):
        self.stops += 1


class FakeVoiceService:
    def __init__(self):
        self.frames = []

    def ingest_playback_pcm(self, pcm: bytes, source_rate: int):
        self.frames.append((pcm, source_rate))


def _manager(*, player=None, sink=None):
    events = [] if sink is None else sink
    publisher = PlaybackLifecyclePublisher(
        runtime_boot_id="boot-1",
        sink=events.append,
        clock=ManualClock(NOW),
    )
    manager = NativePlaybackManager(
        service=FakeVoiceService(),
        player=player or FakePlayer(),
        lifecycle=publisher,
    )
    return manager, events, publisher


def _wait_inactive(manager: NativePlaybackManager, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while manager.status()["active"] and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not manager.status()["active"]


def test_native_playback_emits_request_scoped_started_and_completed_once():
    manager, events, publisher = _manager()

    result = manager.play_wav(
        _wav_tone(),
        session_id="session-1",
        request_id="request-1",
    )
    _wait_inactive(manager)

    assert result["ok"] is True
    assert result["playback_id"]
    assert result["generation"] >= 1
    assert [event.outcome for event in events] == [
        PlaybackOutcome.STARTED,
        PlaybackOutcome.COMPLETED,
    ]
    assert {event.playback_id for event in events} == {result["playback_id"]}
    assert {event.generation for event in events} == {result["generation"]}
    assert {event.session_id for event in events} == {"session-1"}
    assert {event.request_id for event in events} == {"request-1"}
    assert all(event.runtime_boot_id == "boot-1" for event in events)
    assert publisher.stats() == {"published": 2, "sink_failures": 0}


def test_native_playback_passes_real_duration_only_with_started_lifecycle():
    observed = []
    publisher = PlaybackLifecyclePublisher(
        runtime_boot_id="boot-1",
        duration_sink=lambda event, duration: observed.append((event, duration)),
        clock=ManualClock(NOW),
    )
    manager = NativePlaybackManager(
        service=FakeVoiceService(),
        player=FakePlayer(),
        lifecycle=publisher,
    )

    manager.play_wav(
        _wav_tone(duration_ms=125),
        session_id="session-1",
        request_id="request-1",
    )
    _wait_inactive(manager)

    assert [event.outcome for event, _ in observed] == [
        PlaybackOutcome.STARTED,
        PlaybackOutcome.COMPLETED,
    ]
    assert observed[0][1] == pytest.approx(0.125)
    assert observed[1][1] is None


def test_legacy_single_argument_sink_ignores_optional_duration():
    events = []
    publisher = PlaybackLifecyclePublisher(
        runtime_boot_id="boot-1",
        sink=events.append,
        clock=ManualClock(NOW),
    )

    event = publisher.publish(
        playback_id="playback-1",
        generation=1,
        session_id="session-1",
        request_id="request-1",
        outcome=PlaybackOutcome.STARTED,
        reason_code="playback_started",
        playback_duration_seconds=0.125,
    )

    assert events == [event]


def test_stop_is_idempotent_and_late_worker_cannot_emit_completed():
    manager, events, _ = _manager()
    result = manager.play_wav(
        _wav_tone(duration_ms=500),
        session_id="session-1",
        request_id="request-1",
    )

    first = manager.stop(
        playback_id=result["playback_id"],
        generation=result["generation"],
        session_id="session-1",
        request_id="request-1",
    )
    second = manager.stop(
        playback_id=result["playback_id"],
        generation=result["generation"],
        session_id="session-1",
        request_id="request-1",
    )
    time.sleep(0.05)

    assert first["stopped"] is True
    assert second["stopped"] is False
    assert [event.outcome for event in events] == [
        PlaybackOutcome.STARTED,
        PlaybackOutcome.STOPPED,
    ]


def test_stale_scoped_stop_does_not_cancel_new_playback():
    manager, events, _ = _manager()
    first = manager.play_wav(
        _wav_tone(duration_ms=500),
        session_id="session-1",
        request_id="request-old",
    )
    reservation = manager.reserve()
    second = manager.play_reserved_wav(
        _wav_tone(duration_ms=300),
        reservation=reservation,
        session_id="session-1",
        request_id="request-new",
    )

    stale = manager.stop(
        playback_id=first["playback_id"],
        generation=first["generation"],
        session_id="session-1",
        request_id="request-old",
    )

    assert stale["stale"] is True
    assert manager.status()["active"] is True
    assert manager.status()["playback_id"] == second["playback_id"]
    manager.stop(
        playback_id=second["playback_id"],
        generation=second["generation"],
        session_id="session-1",
        request_id="request-new",
    )
    outcomes_by_request = {}
    for event in events:
        outcomes_by_request.setdefault(event.request_id, []).append(event.outcome)
    assert outcomes_by_request["request-old"] == [
        PlaybackOutcome.STARTED,
        PlaybackOutcome.CANCELLED,
    ]
    assert outcomes_by_request["request-new"] == [
        PlaybackOutcome.STARTED,
        PlaybackOutcome.STOPPED,
    ]


def test_scoped_stop_rejects_non_string_identity_without_cancelling_playback():
    manager, _, _ = _manager()
    current = manager.play_wav(
        _wav_tone(duration_ms=500),
        session_id="session-1",
        request_id="request-1",
    )

    with pytest.raises(ValueError, match="playback_id"):
        manager.stop(
            playback_id={"unexpected": "object"},
            generation=current["generation"],
            session_id="session-1",
            request_id="request-1",
        )

    assert manager.status()["playback_id"] == current["playback_id"]
    manager.stop(
        playback_id=current["playback_id"],
        generation=current["generation"],
        session_id="session-1",
        request_id="request-1",
    )


def test_playback_failure_emits_failed_terminal_and_clears_active_state():
    manager, events, _ = _manager(player=FakePlayer(fail=True))

    with pytest.raises(RuntimeError, match="player unavailable"):
        manager.play_wav(
            _wav_tone(),
            session_id="session-1",
            request_id="request-1",
        )

    assert manager.status()["active"] is False
    assert [event.outcome for event in events] == [PlaybackOutcome.FAILED]


def test_governed_lifecycle_requires_canonical_session_and_request_ids():
    manager, events, _ = _manager()

    with pytest.raises(ValueError, match="session_id"):
        manager.play_wav(_wav_tone())

    assert events == []


def test_legacy_manager_without_lifecycle_remains_compatible():
    player = FakePlayer()
    manager = NativePlaybackManager(service=FakeVoiceService(), player=player)

    result = manager.play_wav(_wav_tone())
    _wait_inactive(manager)

    assert result["ok"] is True
    assert len(player.played) == 1


def test_lifecycle_sink_failure_isolated_from_audio_playback():
    calls = []

    def broken_sink(event):
        calls.append(event)
        raise RuntimeError("life reducer unavailable")

    publisher = PlaybackLifecyclePublisher(
        runtime_boot_id="boot-1",
        sink=broken_sink,
        clock=ManualClock(NOW),
    )
    manager = NativePlaybackManager(
        service=FakeVoiceService(),
        player=FakePlayer(),
        lifecycle=publisher,
    )

    result = manager.play_wav(
        _wav_tone(),
        session_id="session-1",
        request_id="request-1",
    )
    _wait_inactive(manager)

    assert result["ok"] is True
    assert len(calls) == 2
    assert publisher.stats() == {"published": 0, "sink_failures": 2}


def test_publisher_validates_bounded_identifiers_before_calling_sink():
    publisher = PlaybackLifecyclePublisher(
        runtime_boot_id="boot-1",
        sink=lambda event: None,
        clock=ManualClock(NOW),
    )

    with pytest.raises(ValueError, match="request_id"):
        publisher.publish(
            playback_id="playback-1",
            generation=1,
            session_id="session-1",
            request_id="",
            outcome=PlaybackOutcome.STARTED,
            reason_code="playback_started",
        )
