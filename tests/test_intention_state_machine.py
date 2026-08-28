from __future__ import annotations

import pytest

from core.intention.contracts import (
    INTENTION_STATE_TRANSITIONS,
    TERMINAL_INTENTION_STATES,
    IntentionState,
)
from core.intention.service import IntentionService


@pytest.mark.parametrize("from_state", [None, *list(IntentionState)])
@pytest.mark.parametrize("to_state", list(IntentionState))
def test_every_state_pair_matches_the_frozen_transition_table(from_state, to_state):
    expected = to_state in INTENTION_STATE_TRANSITIONS[from_state]
    assert IntentionService.can_transition(from_state, to_state) is expected


@pytest.mark.parametrize("terminal", sorted(TERMINAL_INTENTION_STATES, key=lambda item: item.value))
@pytest.mark.parametrize("target", list(IntentionState))
def test_terminal_states_have_no_reopen_edge(terminal, target):
    assert IntentionService.can_transition(terminal, target) is False


@pytest.mark.parametrize(
    "source",
    [
        IntentionState.CANDIDATE,
        IntentionState.ACCEPTED,
        IntentionState.ACTIVE,
        IntentionState.WAITING,
        IntentionState.BLOCKED,
        IntentionState.VERIFYING,
    ],
)
def test_no_state_has_an_implicit_self_update_edge(source):
    assert IntentionService.can_transition(source, source) is False


def test_completed_has_one_declared_predecessor():
    predecessors = {
        source
        for source, targets in INTENTION_STATE_TRANSITIONS.items()
        if IntentionState.COMPLETED in targets
    }
    assert predecessors == {IntentionState.VERIFYING}
