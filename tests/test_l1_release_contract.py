from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATIONS = ROOT / "docs" / "JAVIS_OPERATIONS.md"
ACCEPTANCE = ROOT / "docs" / "verification" / "JAVIS_L1_D_DRIVE_ACCEPTANCE.md"
INTEGRATED = (
    ROOT
    / "docs"
    / "superpowers"
    / "plans"
    / "2026-08-12-javis-life-os-integrated-execution.md"
)


def test_l1_release_contract_keeps_read_only_life_api_and_hot_path_boundaries():
    api = (ROOT / "core" / "life" / "api.py").read_text(encoding="utf-8")
    assert "@router.post" not in api
    assert "@router.put" not in api
    assert "@router.patch" not in api
    assert "@router.delete" not in api

    hot_path_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            [ROOT / "core" / "conversation_hub.py"]
            + sorted((ROOT / "core" / "life" / "l1").glob("*.py"))
            + sorted((ROOT / "voice").glob("*.py"))
        )
    )
    assert "sqlite3" not in hot_path_sources

    tool_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [ROOT / "main.py", *sorted((ROOT / "tools").glob("*.py"))]
    )
    assert 'ToolDef("life' not in tool_sources
    assert 'ToolDef("inner' not in tool_sources


def test_operations_define_l1_release_evidence_without_claiming_manual_success():
    operations = OPERATIONS.read_text(encoding="utf-8")
    section = operations.split("## L1 Presence Release Evidence", 1)[1].split(
        "\n## ", 1
    )[0]

    required = (
        "JAVIS_L1_D_DRIVE_ACCEPTANCE.md",
        "/api/life/inner-state",
        "voice_provenance",
        "life.inner_state.changed",
        "exact invocation offline",
        "malicious local webpage rejection",
        "verified microphone provenance",
        "delayed playback stop barge-in",
        "Live/Code/Pet revision agreement",
        "NOT EXECUTED",
    )
    for field in required:
        assert field in section

    assert "Result: PASS" not in section
    assert "Overall: PASS" not in section


def test_l1_d_drive_acceptance_template_requires_truthful_manual_evidence():
    acceptance = ACCEPTANCE.read_text(encoding="utf-8")
    required_fields = (
        "Status: NOT EXECUTED",
        "Package SHA-256",
        "Source commit",
        "Package path",
        "JAVIS_DATA_ROOT",
        "Exact invocation offline result",
        "Malicious local webpage rejection",
        "Verified microphone provenance",
        "Delayed playback stop barge-in",
        "Model route before switch",
        "Model route after switch",
        "Restart recovery result",
        "Live surface revision",
        "Code surface revision",
        "Pet surface revision",
        "[ ] PASS  [ ] FAIL",
    )
    for field in required_fields:
        assert field in acceptance

    assert "Result: PASS" not in acceptance
    assert "Overall: PASS" not in acceptance


def test_integrated_execution_plan_tracks_the_scoped_task12_release_slice():
    integrated = INTEGRATED.read_text(encoding="utf-8")
    required = (
        "L1 Task 12 release contracts (docs/static gates)",
        "tests/test_l1_release_contract.py",
        "docs/verification/JAVIS_L1_D_DRIVE_ACCEPTANCE.md",
        "manual installer and D-drive evidence remains NOT EXECUTED",
    )
    for field in required:
        assert field in integrated
