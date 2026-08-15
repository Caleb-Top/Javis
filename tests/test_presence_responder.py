from __future__ import annotations

import pytest

from core.life.l1.contracts import (
    InputModality,
    InputProvenance,
    InputVerification,
)
from core.life.l1.wake import (
    DEDUPE_WINDOW_MS,
    DETERMINISTIC_LOCAL_LANE,
    PRESENCE_RESPONSE_TEXT,
    PresenceResponder,
    is_exact_invocation,
    normalize_invocation,
)


def _provenance(
    modality: InputModality = InputModality.TEXT,
    *,
    boot: str | None = None,
    session: str | None = None,
    generation: int | None = None,
    sequence: int | None = None,
    turn: int | None = None,
) -> InputProvenance:
    verification = (
        InputVerification.SERVER_VERIFIED
        if modality is InputModality.VOICE and boot is not None
        else InputVerification.CLIENT_CLAIMED
    )
    return InputProvenance(
        modality,
        verification,
        boot,
        session,
        generation,
        sequence,
        turn,
    )


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        ("Javis", "javis"),
        ("  JAVIS!  ", "javis"),
        ("？Jarvis。", "jarvis"),
        ("贾维斯？", "贾维斯"),
        ("“Javis！”", "javis"),
        ("ＪＡＶＩＳ！", "javis"),
    ],
)
def test_normalization_accepts_only_governed_boundary_variants(text, normalized):
    assert normalize_invocation(text) == normalized
    assert is_exact_invocation(text)


@pytest.mark.parametrize(
    "text",
    [
        "Javis 在吗",
        "Javis 帮我打开文件",
        "你好 Javis",
        "小贾维斯",
        "Javis, please",
        "Ja vis",
        "Javis！我在这里",
        "【Javis】",
        "",
    ],
)
def test_complete_requests_and_non_aliases_never_match(text):
    assert not is_exact_invocation(text)


def test_exact_invocation_returns_fixed_local_decision():
    responder = PresenceResponder()

    decision = responder.decide(
        "Javis!",
        session_id="session-1",
        request_id="request-1",
        idempotency_key="key-1",
        input_provenance=_provenance(),
        now_monotonic_ms=1000,
    )

    assert decision.exact_invocation
    assert decision.should_respond
    assert not decision.duplicate
    assert decision.response_text == PRESENCE_RESPONSE_TEXT == "我在"
    assert decision.execution_lane == DETERMINISTIC_LOCAL_LANE


def test_non_match_stays_on_exclusive_lane_without_response():
    decision = PresenceResponder().decide(
        "Javis 帮我打开文件",
        session_id="session-1",
        request_id="request-1",
        input_provenance=_provenance(),
        now_monotonic_ms=1000,
    )

    assert not decision.exact_invocation
    assert not decision.should_respond
    assert not decision.duplicate
    assert decision.response_text is None
    assert decision.execution_lane == "exclusive"


def test_same_request_or_idempotency_is_deduplicated_within_1500ms():
    responder = PresenceResponder()
    provenance = _provenance()
    first = responder.decide(
        "Javis",
        session_id="session-1",
        request_id="request-1",
        idempotency_key="key-1",
        input_provenance=provenance,
        now_monotonic_ms=1000,
    )
    same_request = responder.decide(
        "Jarvis",
        session_id="session-1",
        request_id="request-1",
        idempotency_key="key-2",
        input_provenance=provenance,
        now_monotonic_ms=2000,
    )
    same_idempotency = responder.decide(
        "贾维斯",
        session_id="session-1",
        request_id="request-2",
        idempotency_key="key-1",
        input_provenance=provenance,
        now_monotonic_ms=2200,
    )

    assert first.should_respond
    assert same_request.duplicate and not same_request.should_respond
    assert same_idempotency.duplicate and not same_idempotency.should_respond


def test_dedupe_window_is_inclusive_and_expired_identity_can_respond_again():
    responder = PresenceResponder()
    arguments = {
        "session_id": "session-1",
        "request_id": "request-1",
        "input_provenance": _provenance(),
    }

    assert responder.decide("Javis", now_monotonic_ms=0, **arguments).should_respond
    assert responder.decide(
        "Javis", now_monotonic_ms=DEDUPE_WINDOW_MS, **arguments
    ).duplicate
    assert responder.decide(
        "Javis", now_monotonic_ms=DEDUPE_WINDOW_MS * 2 + 1, **arguments
    ).should_respond


def test_new_physical_call_can_respond_immediately_after_previous_completion():
    responder = PresenceResponder()
    provenance = _provenance()

    first = responder.decide(
        "Javis",
        session_id="session-1",
        request_id="request-1",
        input_provenance=provenance,
        now_monotonic_ms=1000,
    )
    next_call = responder.decide(
        "Javis",
        session_id="session-1",
        request_id="request-2",
        input_provenance=provenance,
        now_monotonic_ms=1100,
    )

    assert first.should_respond
    assert next_call.should_respond


def test_different_sessions_and_modalities_do_not_cross_dedupe():
    responder = PresenceResponder()
    text = _provenance()
    legacy_voice = InputProvenance(
        InputModality.VOICE,
        InputVerification.SERVER_TRANSCRIBED,
        None,
        None,
        None,
        None,
        None,
    )

    assert responder.decide(
        "Javis",
        session_id="session-1",
        request_id="same-request",
        input_provenance=text,
        now_monotonic_ms=1000,
    ).should_respond
    assert responder.decide(
        "Javis",
        session_id="session-2",
        request_id="same-request",
        input_provenance=text,
        now_monotonic_ms=1100,
    ).should_respond
    assert responder.decide(
        "Javis",
        session_id="session-1",
        request_id="same-request",
        input_provenance=legacy_voice,
        now_monotonic_ms=1200,
    ).should_respond


def test_voice_dedupe_uses_complete_provenance_identity_not_bare_sequence():
    responder = PresenceResponder()

    def call(provenance, request_id, now):
        return responder.decide(
            "Javis",
            session_id="session-1",
            request_id=request_id,
            input_provenance=provenance,
            now_monotonic_ms=now,
        )

    original = _provenance(
        InputModality.VOICE,
        boot="boot-1",
        session="session-1",
        generation=2,
        sequence=7,
        turn=3,
    )
    same_physical_call = _provenance(
        InputModality.VOICE,
        boot="boot-1",
        session="session-1",
        generation=2,
        sequence=7,
        turn=3,
    )
    another_boot = _provenance(
        InputModality.VOICE,
        boot="boot-2",
        session="session-1",
        generation=2,
        sequence=7,
        turn=3,
    )
    another_generation = _provenance(
        InputModality.VOICE,
        boot="boot-1",
        session="session-1",
        generation=3,
        sequence=7,
        turn=3,
    )
    another_turn = _provenance(
        InputModality.VOICE,
        boot="boot-1",
        session="session-1",
        generation=2,
        sequence=7,
        turn=4,
    )

    assert call(original, "request-1", 1000).should_respond
    assert call(same_physical_call, "request-2", 1100).duplicate
    assert call(another_boot, "request-3", 1200).should_respond
    assert call(another_generation, "request-4", 1300).should_respond
    assert call(another_turn, "request-5", 1400).should_respond


def test_verified_voice_session_must_match_request_session():
    responder = PresenceResponder()
    provenance = _provenance(
        InputModality.VOICE,
        boot="boot-1",
        session="session-other",
        generation=2,
        sequence=7,
        turn=3,
    )

    with pytest.raises(ValueError, match="session"):
        responder.decide(
            "Javis",
            session_id="session-1",
            request_id="request-1",
            input_provenance=provenance,
            now_monotonic_ms=1000,
        )


@pytest.mark.parametrize("now", [-1, float("inf"), float("nan"), True])
def test_invalid_monotonic_time_is_rejected(now):
    with pytest.raises((TypeError, ValueError), match="monotonic"):
        PresenceResponder().decide(
            "Javis",
            session_id="session-1",
            request_id="request-1",
            input_provenance=_provenance(),
            now_monotonic_ms=now,
        )
