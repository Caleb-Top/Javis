from core.life.l1.contracts import InnerStateSnapshot
from core.prompt_builder import PromptBuilder, build_runtime_state_summary


def _inner_state(revision: int, *, caution: float = 0.1, blockedness: float = 0.0):
    return InnerStateSnapshot.from_dict(
        {
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
                "caution": caution,
                "curiosity": 0.25,
                "blockedness": blockedness,
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
    )


def test_runtime_state_summary_is_bounded_structured_and_no_leak():
    summary = build_runtime_state_summary(_inner_state(8, caution=0.32))

    assert summary.startswith("JAVIS_RUNTIME_STATE_V1\n")
    assert "phase=engaged" in summary
    assert "attention=active_user_request" in summary
    assert "caution=0.32" in summary
    assert "certainty=0.50" in summary
    assert "blockedness=0.00" in summary
    assert "guidance=" in summary
    assert "evidence-1" not in summary
    assert len(summary.encode("utf-8")) <= 512


def test_prompt_builder_injects_runtime_state_summary_and_refreshes_inside_step_window():
    current = {"value": _inner_state(8, caution=0.10, blockedness=0.0)}
    builder = PromptBuilder(brain=None)
    builder.set_runtime_state_provider(lambda: current["value"])

    first = builder.build(phase="planning", step=0)
    current["value"] = _inner_state(9, caution=0.35, blockedness=0.30)
    second = builder.build(phase="planning", step=1)

    assert "JAVIS_RUNTIME_STATE_V1" in first
    assert "caution=0.10" in first
    assert "caution=0.35" in second
    assert "blockedness=0.30" in second
    assert first != second
