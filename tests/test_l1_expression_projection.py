from core.life.contracts import (
    ExpressionBaseState,
    GazeTarget,
    IdentityConstitution,
    InstanceRecord,
    LifeCycleState,
    VoiceActivity,
)
from core.life.expression import ExpressionProjector
from core.life.state import MinimalLifeStateMachine


def _machine():
    return MinimalLifeStateMachine(
        identity=IdentityConstitution.create_default(
            identity_id="identity-one",
            now=0.0,
        ),
        instance=InstanceRecord(
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
        ),
        initial_state=LifeCycleState.QUIET,
        now=0.0,
    )


def test_expression_projector_accepts_surface_state_and_explanation_overrides():
    snapshot = _machine().snapshot()

    caution = ExpressionProjector().project(
        snapshot,
        now=1.0,
        base_state=ExpressionBaseState.ATTENTION,
        explanation_code="caution",
    )
    recovering = ExpressionProjector().project(
        snapshot,
        now=2.0,
        base_state=ExpressionBaseState.ERROR,
        explanation_code="recovering",
        interrupt=True,
    )

    assert caution.base_state is ExpressionBaseState.ATTENTION
    assert caution.gaze_target is GazeTarget.USER
    assert caution.voice_activity is VoiceActivity.SILENT
    assert caution.explanation_code == "caution"
    assert recovering.base_state is ExpressionBaseState.ERROR
    assert recovering.interrupt is True
    assert recovering.explanation_code == "recovering"


def test_expression_projector_rejects_invalid_surface_overrides():
    snapshot = _machine().snapshot()
    projector = ExpressionProjector()

    try:
        projector.project(snapshot, now=1.0, base_state="curious")
    except ValueError as exc:
        assert "base_state" in str(exc)
    else:
        raise AssertionError("invalid base_state override should fail")

    try:
        projector.project(snapshot, now=1.0, explanation_code="")
    except ValueError as exc:
        assert "explanation_code" in str(exc)
    else:
        raise AssertionError("empty explanation_code should fail")
