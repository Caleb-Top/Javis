from pathlib import Path

import pytest

from core.life.paths import resolve_data_root
from core.runtime import create_runtime


def test_data_root_precedence_is_explicit_then_env_then_compat_default(tmp_path):
    code = tmp_path / "code"
    explicit = tmp_path / "explicit-data"
    env = tmp_path / "env-data"

    assert resolve_data_root(
        code,
        explicit=explicit,
        environ={"JAVIS_DATA_ROOT": str(env)},
    ) == explicit.resolve()
    assert resolve_data_root(
        code,
        environ={"JAVIS_DATA_ROOT": str(env)},
    ) == env.resolve()
    assert resolve_data_root(code, environ={}) == (code / "data").resolve()


def test_resolver_is_side_effect_free_and_blank_env_uses_compat_default(tmp_path):
    code = tmp_path / "missing-code"
    resolved = resolve_data_root(code, environ={"JAVIS_DATA_ROOT": "   "})

    assert resolved == (code / "data").resolve()
    assert not code.exists()
    assert not resolved.exists()


@pytest.mark.parametrize("explicit", ["", "   "])
def test_explicit_blank_data_root_is_rejected_instead_of_falling_back(tmp_path, explicit):
    with pytest.raises(ValueError, match="data_root must not be empty"):
        resolve_data_root(tmp_path / "code", explicit=explicit, environ={})


def test_explicit_data_root_keeps_runtime_databases_out_of_code_root(tmp_path):
    code = tmp_path / "readonly-code"
    data = tmp_path / "user-data"
    code.mkdir()

    runtime = create_runtime(code, startup_side_effects=False, data_root=data)
    try:
        assert runtime.root == code.resolve()
        assert runtime.data_root == data.resolve()
        assert not (code / "data").exists()
        assert (data / "skills" / "catalog.sqlite3").exists()
        assert (data / "agent_runs" / "runs.sqlite3").exists()
        assert (data / "conversations" / "conversations.sqlite3").exists()
    finally:
        runtime.close()


def test_runtime_uses_environment_data_root_when_explicit_value_is_absent(
    tmp_path,
    monkeypatch,
):
    code = tmp_path / "code"
    data = tmp_path / "environment-data"
    code.mkdir()
    monkeypatch.setenv("JAVIS_DATA_ROOT", str(data))

    runtime = create_runtime(code, startup_side_effects=False)
    try:
        assert runtime.data_root == data.resolve()
        assert runtime.skill_catalog.path.is_relative_to(data.resolve())
        assert runtime.agent_runs.path.is_relative_to(data.resolve())
        assert runtime.conversation_store.path.is_relative_to(data.resolve())
        assert not (code / "data").exists()
    finally:
        runtime.close()
