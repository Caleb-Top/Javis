from dataclasses import replace

import pytest

from core.life.contracts import (
    ExpressionBaseState,
    GazeTarget,
    HealthSummary,
    IdentityConstitution,
    InstanceRecord,
    LifeCycleState,
    VoiceActivity,
)
from core.life.expression import ExpressionProjector, StaleSnapshotError
from core.life.state import (
    ALLOWED_LIFECYCLE_TRANSITIONS,
    InvalidLifeTransition,
    MinimalLifeStateMachine,
)


def _identity():
    return IdentityConstitution.create_default(identity_id="identity-one", now=0.0)


def _instance():
    return InstanceRecord(
        schema_version=1,
        identity_id="identity-one",
        lineage_id="lineage-one",
        instance_id="instance-one",
        parent_instance_id=None,
        generation=0,
        environment_fingerprint_hash="a" * 64,
        created_at="1970-01-01T00:00:00.000Z",
        last_started_at=None,
        last_clean_shutdown_at=None,
        fork_pending_review=False,
    )


def _machine(*, initial_state=LifeCycleState.QUIET):
    return MinimalLifeStateMachine(
        identity=_identity(),
        instance=_instance(),
        initial_state=initial_state,
        now=0.0,
    )


def test_quiet_request_listening_terminal_order_is_safe():
    machine = _machine()
    machine.apply("request.accepted", {"request_id": "r1"}, now=10.0)
    machine.apply("voice.listening", {"request_id": "r2"}, now=12.0)
    machine.apply("request.completed", {"request_id": "r1"}, now=13.0)

    assert machine.snapshot().activity == "listening"
    assert machine.snapshot().active_request_id == "r2"


def test_matching_terminal_clears_only_the_active_request():
    machine = _machine()
    machine.apply(
        "request.accepted",
        {"request_id": "r1", "session_id": "session-one"},
        now=1.0,
    )
    machine.apply("request.completed", {"request_id": "r1"}, now=2.0)
    snapshot = machine.snapshot()

    assert snapshot.lifecycle_state is LifeCycleState.QUIET
    assert snapshot.activity == "quiet"
    assert snapshot.active_request_id is None
    assert snapshot.active_session_id == "session-one"


def test_stale_sequence_and_duplicate_last_event_do_not_rewind_state():
    machine = _machine()
    machine.apply(
        "voice.listening",
        {"request_id": "r2", "event_id": "e2", "sequence": 2},
        now=12.0,
    )
    before = machine.snapshot()

    applied = machine.apply(
        "request.completed",
        {"request_id": "r2", "event_id": "e1", "sequence": 1},
        now=13.0,
    )
    duplicate = machine.apply(
        "request.completed",
        {"request_id": "r2", "event_id": "e2", "sequence": 3},
        now=14.0,
    )

    assert applied is False
    assert duplicate is False
    assert machine.snapshot() == before


def test_sequence_zero_is_valid_once_then_rejected_as_stale():
    machine = _machine()

    first = machine.apply(
        "request.accepted",
        {"request_id": "r1", "event_id": "e0", "sequence": 0},
        now=1.0,
    )
    before = machine.snapshot()
    stale = machine.apply(
        "activity.thinking",
        {"request_id": "r1", "event_id": "e1", "sequence": 0},
        now=2.0,
    )

    assert first is True
    assert stale is False
    assert machine.snapshot() == before


def test_wall_clock_rollback_never_rewinds_snapshot_time():
    machine = _machine()
    machine.apply("request.accepted", {"request_id": "r1"}, now=10.0)
    first = machine.snapshot()
    machine.apply("activity.thinking", {"request_id": "r1"}, now=5.0)
    second = machine.snapshot()

    assert second.updated_at == first.updated_at
    assert second.revision > first.revision


def test_illegal_transition_is_rejected_without_mutation():
    machine = _machine()
    machine.apply("runtime.stopping", {}, now=1.0)
    before = machine.snapshot()

    with pytest.raises(InvalidLifeTransition, match="stopping"):
        machine.apply("request.accepted", {"request_id": "r1"}, now=2.0)

    assert machine.snapshot() == before


def test_allowed_lifecycle_transitions_are_explicit_data():
    assert LifeCycleState.AWAKE in ALLOWED_LIFECYCLE_TRANSITIONS[LifeCycleState.BOOTING]
    assert LifeCycleState.ENGAGED in ALLOWED_LIFECYCLE_TRANSITIONS[LifeCycleState.QUIET]
    assert LifeCycleState.QUIET in ALLOWED_LIFECYCLE_TRANSITIONS[LifeCycleState.ENGAGED]
    assert LifeCycleState.RECOVERING in ALLOWED_LIFECYCLE_TRANSITIONS[
        LifeCycleState.DEGRADED
    ]
    assert LifeCycleState.ENGAGED not in ALLOWED_LIFECYCLE_TRANSITIONS[
        LifeCycleState.STOPPING
    ]


def test_boot_recovery_and_health_transitions_are_explainable():
    machine = _machine(initial_state=LifeCycleState.BOOTING)
    machine.apply("runtime.ready", {}, now=1.0)
    assert machine.snapshot().lifecycle_state is LifeCycleState.AWAKE
    machine.apply("runtime.quiet", {}, now=2.0)
    assert machine.snapshot().lifecycle_state is LifeCycleState.QUIET
    machine.apply(
        "health.degraded",
        {
            "components": ["journal"],
            "reason_codes": ["journal_unavailable"],
            "degradation_level": 2,
        },
        now=3.0,
    )
    degraded = machine.snapshot()
    assert degraded.lifecycle_state is LifeCycleState.DEGRADED
    assert degraded.health.degraded_components == ("journal",)
    assert degraded.degradation_level == 2
    machine.apply("life.recovery.required", {"reason_codes": ["identity"]}, now=4.0)
    assert machine.snapshot().recovery_required is True
    assert machine.snapshot().lifecycle_state is LifeCycleState.RECOVERING
    machine.apply("health.recovered", {}, now=5.0)
    recovered = machine.snapshot()
    assert recovered.lifecycle_state is LifeCycleState.AWAKE
    assert recovered.health.status == "healthy"
    assert recovered.recovery_required is False


def test_degraded_runtime_can_show_listening_without_claiming_full_health():
    machine = _machine()
    machine.apply("health.degraded", {"components": ["journal"]}, now=1.0)
    machine.apply("voice.listening", {"request_id": "r1"}, now=2.0)
    snapshot = machine.snapshot()

    assert snapshot.lifecycle_state is LifeCycleState.DEGRADED
    assert snapshot.activity == "listening"
    assert snapshot.active_request_id == "r1"


def test_offline_is_a_client_projection_not_a_backend_checkpoint_state():
    machine = _machine()
    machine.apply("client.offline", {}, now=1.0)

    assert machine.snapshot().lifecycle_state is LifeCycleState.OFFLINE
    assert machine.backend_lifecycle_state is LifeCycleState.QUIET

    machine.apply("client.reconnected", {}, now=2.0)
    assert machine.snapshot().lifecycle_state is LifeCycleState.QUIET


def test_invalid_request_id_does_not_partially_mutate_state():
    machine = _machine()
    before = machine.snapshot()

    with pytest.raises(ValueError, match="request_id"):
        machine.apply("request.accepted", {"request_id": "bad\nrequest"}, now=1.0)

    assert machine.snapshot() == before


def test_non_utf8_request_id_does_not_partially_mutate_state():
    machine = _machine()
    before = machine.snapshot()

    with pytest.raises(ValueError, match="UTF-8"):
        machine.apply("request.accepted", {"request_id": "\ud800"}, now=1.0)

    assert machine.snapshot() == before


def test_id_only_constructor_is_explicitly_unresolved_not_fabricated():
    machine = MinimalLifeStateMachine(
        identity_id="identity-one",
        instance_id="instance-one",
        now=0.0,
    )
    snapshot = machine.snapshot()

    assert snapshot.identity.identity_id == "identity-one"
    assert snapshot.identity.kind == "unresolved"
    assert snapshot.identity.relationship_role == "unresolved"
    assert snapshot.instance.lineage_id == "unresolved"


def test_expression_revision_and_expiry_are_monotonic():
    machine = _machine()
    projector = ExpressionProjector()
    first = projector.project(machine.snapshot(), now=10.0)
    machine.apply("request.accepted", {"request_id": "r1"}, now=11.0)
    second = projector.project(machine.snapshot(), now=11.0)

    assert second.revision > first.revision
    assert second.expires_at > second.generated_at


@pytest.mark.parametrize(
    ("event_type", "expected_base", "expected_gaze", "expected_voice"),
    [
        ("runtime.quiet", ExpressionBaseState.IDLE, GazeTarget.NONE, VoiceActivity.SILENT),
        (
            "request.accepted",
            ExpressionBaseState.ATTENTION,
            GazeTarget.USER,
            VoiceActivity.SILENT,
        ),
        (
            "voice.listening",
            ExpressionBaseState.LISTENING,
            GazeTarget.USER,
            VoiceActivity.LISTENING,
        ),
        ("activity.thinking", ExpressionBaseState.THINKING, GazeTarget.CONTENT, VoiceActivity.SILENT),
        ("voice.speaking", ExpressionBaseState.SPEAKING, GazeTarget.USER, VoiceActivity.SPEAKING),
        ("tool.started", ExpressionBaseState.EXECUTING, GazeTarget.TASK, VoiceActivity.SILENT),
        ("approval.requested", ExpressionBaseState.BLOCKED, GazeTarget.TASK, VoiceActivity.SILENT),
        ("activity.error", ExpressionBaseState.ERROR, GazeTarget.NONE, VoiceActivity.SILENT),
        ("client.offline", ExpressionBaseState.OFFLINE, GazeTarget.NONE, VoiceActivity.SILENT),
    ],
)
def test_expression_maps_all_nine_states_exactly(
    event_type,
    expected_base,
    expected_gaze,
    expected_voice,
):
    machine = _machine()
    payload = {"request_id": "r1"} if event_type != "runtime.quiet" else {}
    machine.apply(event_type, payload, now=1.0)

    intent = ExpressionProjector().project(machine.snapshot(), now=1.0)

    assert intent.base_state is expected_base
    assert intent.gaze_target is expected_gaze
    assert intent.voice_activity is expected_voice
    assert set(intent.to_dict()) == {
        "schema_version",
        "revision",
        "base_state",
        "intensity",
        "gaze_target",
        "voice_activity",
        "transition_ms",
        "interrupt",
        "source_snapshot_revision",
        "generated_at",
        "expires_at",
        "explanation_code",
    }


def test_expression_clamps_intensity_and_rejects_unknown_channels():
    snapshot = _machine().snapshot()
    high = ExpressionProjector().project(snapshot, now=1.0, intensity=3.0)
    low = ExpressionProjector().project(snapshot, now=1.0, intensity=-2.0)

    assert high.intensity == 1.0
    assert low.intensity == 0.0
    with pytest.raises(ValueError, match="gaze_target"):
        ExpressionProjector().project(snapshot, now=1.0, gaze_target="camera")
    with pytest.raises(ValueError, match="voice_activity"):
        ExpressionProjector().project(snapshot, now=1.0, voice_activity="singing")


def test_expression_rejects_stale_snapshots_and_clock_rollback():
    machine = _machine()
    old = machine.snapshot()
    machine.apply("request.accepted", {"request_id": "r1"}, now=2.0)
    new = machine.snapshot()
    projector = ExpressionProjector()
    first = projector.project(new, now=10.0)

    with pytest.raises(StaleSnapshotError, match="stale"):
        projector.project(old, now=11.0)

    repeated = projector.project(new, now=5.0)
    assert repeated.generated_at == first.generated_at


def test_expression_error_health_never_leaks_component_details():
    snapshot = _machine().snapshot()
    snapshot = replace(
        snapshot,
        activity="error",
        health=HealthSummary(
            status="degraded",
            degraded_components=("secret-provider-name",),
            reason_codes=("internal-detail",),
        ),
    )

    intent = ExpressionProjector().project(snapshot, now=1.0)

    assert intent.explanation_code == "error"
    assert "secret-provider-name" not in str(intent.to_dict())
