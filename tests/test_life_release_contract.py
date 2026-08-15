from pathlib import Path

from core.life.api import create_life_router
from core.life.contracts import ExpressionIntent
from core.life.event_adapter import _RULES


ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ROOT / "docs" / "JAVIS_OPERATIONS.md"
ACCEPTANCE = ROOT / "docs" / "verification" / "JAVIS_L0A_D_DRIVE_ACCEPTANCE.md"


class _ReadOnlyLifeStub:
    pass


PRODUCTION_EXPLICIT_EVENT_TYPES = {
    "life.identity.created",
    "life.instance.created",
    "runtime.created",
    "subsystem.registered",
    "event_store.registered",
    "tool.started",
    "tool.completed",
    "tool.failed",
    "approval.requested",
    "approval.required",
    "approval.resolved",
    "life.recovery.required",
}


def test_life_kernel_release_contract_is_present_and_read_only():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    runtime = (ROOT / "core" / "runtime.py").read_text(encoding="utf-8")
    api = (ROOT / "core" / "life" / "api.py").read_text(encoding="utf-8")

    assert "create_life_router" in main
    assert "runtime.life" in main
    assert "LifeService" in runtime
    assert "@router.post" not in api
    assert "@router.put" not in api
    assert "@router.patch" not in api
    assert "@router.delete" not in api

    router = create_life_router(_ReadOnlyLifeStub())
    methods = {
        route.path: route.methods
        for route in router.routes
        if route.path.startswith("/api/life/")
    }
    assert methods == {
        "/api/life/identity": {"GET"},
        "/api/life/snapshot": {"GET"},
        "/api/life/lineage": {"GET"},
        "/api/life/inner-state": {"GET"},
        "/api/life/events": {"GET"},
    }


def test_life_data_never_targets_source_or_d_drive():
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "core" / "life").glob("*.py"))
    )
    assert "D:\\" not in sources
    assert "brain_data/rules" not in sources
    assert 'ROOT / "life"' not in sources
    assert "ROOT / 'life'" not in sources


def test_explicit_event_map_contains_only_production_publishers():
    assert set(_RULES) == PRODUCTION_EXPLICIT_EVENT_TYPES


def test_expression_intent_fields_match_the_typescript_bridge_exactly():
    python_fields = set(ExpressionIntent.__dataclass_fields__)
    typescript = (ROOT / "app" / "src" / "life" / "lifeTypes.ts").read_text(
        encoding="utf-8"
    )
    block = typescript.split("export type ExpressionIntent = {", 1)[1].split(
        "};", 1
    )[0]
    typescript_fields = {
        line.strip().split(":", 1)[0]
        for line in block.splitlines()
        if ":" in line
    }

    assert len(python_fields) == 12
    assert typescript_fields == python_fields


def test_operations_define_complete_configured_root_recovery_steps():
    operations = OPERATIONS.read_text(encoding="utf-8")
    recovery = operations.split("## Life Kernel Recovery", 1)[1].split("\n## ", 1)[0]

    assert "JAVIS_DATA_ROOT" in recovery
    assert "/api/life/identity" in recovery
    assert "/api/runtime/status" in recovery
    assert "/api/life/events?limit=500" in recovery
    assert "rollback_to_previous" in recovery
    assert "assess_previous_run" in recovery
    assert "temporary_authority_valid" in recovery
    assert "D:\\" not in recovery


def test_d_drive_acceptance_template_requires_truthful_evidence():
    acceptance = ACCEPTANCE.read_text(encoding="utf-8")
    required_fields = (
        "Status: NOT EXECUTED",
        "Package SHA-256",
        "Source commit",
        "Identity ID before restart",
        "Identity ID after restart",
        "Instance ID before restart",
        "Instance ID after restart",
        "Model route before switch",
        "Model route after switch",
        "Abnormal shutdown result",
        "Corruption recovery result",
        "Live snapshot revision",
        "Code snapshot revision",
        "Pet snapshot revision",
        "[ ] PASS  [ ] FAIL",
    )
    for field in required_fields:
        assert field in acceptance

    assert "Result: PASS" not in acceptance
    assert "Overall: PASS" not in acceptance
