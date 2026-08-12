# Javis L0-A Life Kernel Implementation Plan

**执行状态（2026-08-12）：** Task 1 已在 `eeebcbd` 完成并复验；Task 2 已在 `codex/javis-life-os-l0-foundation-20260812` 实现，并通过 770 项测试和 28 项 subtests。综合状态与后续依赖以 `2026-08-12-javis-life-os-integrated-execution.md` 为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立独立于模型、界面和 Agent 的 Javis 身份宪章、实例谱系、最小生命周期、完整生命事件信封、异步事件日志与只读生命快照。

**Architecture:** 在现有 `JarvisRuntime`、同步 `EventBus`、`ConversationHub` 与前端 `RuntimeStateCoordinator` 上增加增量 `core.life` 门面。热路径只做确定性事件映射和有界队列入队，SQLite、脱敏和索引在后台日志消费者完成；旧状态协议在迁移期保留为 fallback。

**Tech Stack:** Python 3.11、dataclasses、Enum、SQLite/FTS5、threading/queue、FastAPI、pytest/unittest、TypeScript、Node test runner、现有 WebSocket v2 协议。

**Source Design:** `docs/superpowers/specs/2026-08-09-javis-l0a-life-kernel-design.md`。本计划必须覆盖该规格，不得用执行便利缩小身份、连续性、隐私或恢复边界。

## Global Constraints

- `G:\Javis` 是唯一正式源码与集成区；实现必须先发生在 `G:\Javis-worktrees` 隔离 worktree。
- `G:\Javis-build-cache` 承载缓存和中间产物；D 盘仅用于真实用户安装与验收。
- 本计划不重新执行 P0/P1 证据封口，只把其语音、打断、会话、模型配置和安装能力作为回归门。
- 不重写 `EventBus`、`ConversationHub`、`AgentRunStore`、`ConversationStore`、`MemoryController` 或 `RuntimeStateCoordinator`。
- 身份宪章不依赖模型、网络、Agent、向量库或 prompt。
- 同步 EventBus handler 禁止 SQLite、文件大读写、网络和模型调用。
- 身份、授权、删除、恢复和行动审计事件不得因队列满而静默丢弃。
- API Key、token、密码、原始音频、图像、OCR 全文和未脱敏文件内容不得进入普通全文索引。
- 普通 API 不得修改身份宪章；所有 L0 API 均为只读或诊断型。
- `ExpressionIntent v1` 的 12 个字段必须与 L0-B 完全一致。
- 任何测试必须使用临时数据根，不能读取或修改真实用户身份数据。
- 未在 D 盘真实执行的重启、异常恢复和安装项必须标记为未验收。

---

## File Structure

### New backend files

- `core/life/__init__.py`：导出公共类型与 `LifeService`。
- `core/life/contracts.py`：身份、实例、事件、快照、表达意图数据契约和枚举。
- `core/life/paths.py`：代码根与用户数据根的确定性解析，不创建目录。
- `core/life/identity.py`：身份宪章版本链、哈希、原子写入与恢复。
- `core/life/lineage.py`：实例记录、谱系、检查点和异常关闭识别。
- `core/life/state.py`：最小生命周期状态机与快照投影。
- `core/life/privacy.py`：隐私/保留分类、payload 脱敏与索引摘要。
- `core/life/event_adapter.py`：现有 Event 到 LifeEvent 的确定性映射。
- `core/life/journal.py`：有界队列、后台 SQLite 写入、游标、幂等和状态。
- `core/life/expression.py`：LifeSnapshot 到 `ExpressionIntent v1` 的稳定投影。
- `core/life/service.py`：Runtime 子系统装配、启动、停止、订阅与恢复。
- `core/life/api.py`：只读 FastAPI router。

### New frontend files

- `app/src/life/lifeTypes.ts`：LifeSnapshot 与 ExpressionIntent v1 类型守卫。
- `app/src/life/LifeStateBridge.ts`：生命快照/表达事件到现有 RuntimeStateCoordinator 的兼容桥。

### Modified files

- `core/runtime.py`：创建并注册 `LifeService`，把它暴露为 runtime 明确字段。
- `core/conversation_hub.py`：增加受控的公共 session event 发布方法，不暴露私有 `_publish`。
- `memory/session_db.py`：把现有通配同步 SQLite 写入改为有界异步写入，并限制全文索引范围。
- `main.py`：挂载只读 life router，并把当前 session 的生命快照推送到统一会话。
- `app/src-tauri/src/sidecar.rs`：传入真实用户数据根，并先尝试所有权令牌保护的优雅关闭。
- `app/src/main.ts`：在唯一 `onEvent` 链中调用 LifeStateBridge，不新增平行 WebSocket。

### Tests

- `tests/test_life_contracts.py`
- `tests/test_life_paths.py`
- `tests/test_life_identity.py`
- `tests/test_life_lineage.py`
- `tests/test_life_state.py`
- `tests/test_life_event_adapter.py`
- `tests/test_life_journal.py`
- `tests/test_session_event_store_async.py`
- `tests/test_life_service.py`
- `tests/test_life_api.py`
- `tests/test_conversation_life_event_bridge.py`
- `app/tests/lifeStateBridge.test.ts`
- `tests/test_life_release_contract.py`

---

## Cross-Plan Integration Order

1. L0-A Task 1 first freezes the Python `ExpressionIntent v1` wire contract and its exact 12 fields.
2. L0-B Tasks 1-5 may proceed in a separate worktree because they do not consume `app/src/life/lifeTypes.ts` or modify `app/src/main.ts`.
3. L0-A Task 11 must be integrated before L0-B Task 6; L0-B then imports the committed TypeScript guard instead of recreating the protocol.
4. L0-A Task 11 owns the first `app/src/main.ts` edit. Before L0-B Tasks 7 or 9 edit that file, rebase the L0-B worktree onto the integrated L0-A commit and rerun the complete App suite.
5. Integration order is L0-A followed by the rebased L0-B. Neither branch may resolve a conflict by dropping the existing single `onEvent` chain or creating a second WebSocket.

---

## Data Root Contract (applies before Task 2)

`root` 始终表示只读源码/运行时代码根；`data_root` 表示可写用户数据基础根。两者不得隐式等同。正式入口冻结为：

```python
def create_runtime(
    root: str | Path,
    startup_side_effects: bool = True,
    *,
    data_root: str | Path | None = None,
) -> JarvisRuntime:
    ...
```

解析优先级固定为：显式 `data_root` → 非空 `JAVIS_DATA_ROOT` → 兼容默认 `Path(root) / "data"`。解析器只执行 `expanduser()`、绝对化和规范化，不创建目录、不检查网络、不回退 D 盘。

`LifeService` 始终接收基础 `data_root`；各 Store 自己追加 `life/identity`、`life/instances`、`life/journal`。任何调用点都不得把 `data_root / "life"` 再传给 `LifeService`。

隔离开发测试必须显式传 `tmp_path / "user-data"`，并断言代码根无新增文件。Tauri 安装运行时使用应用用户数据目录或用户明确配置的目录传入 `JAVIS_DATA_ROOT`，不能继续把打包代码根当用户数据根。

---

### Task 1: Lock the Life Contracts

**Files:**
- Create: `core/life/__init__.py`
- Create: `core/life/contracts.py`
- Test: `tests/test_life_contracts.py`

**Interfaces:**
- Consumes: Python standard library only.
- Produces: `LifeCycleState`, `PrivacyClass`, `RetentionClass`, `ExpressionBaseState`, `GazeTarget`, `VoiceActivity`, `IdentityConstitution`, `InstanceRecord`, `ContinuityCheckpoint`, `StartupAssessment`, `LifeEvent`, `IdentitySummary`, `InstanceSummary`, `HealthSummary`, `LifeSnapshot`, `ExpressionIntent`, `canonical_json_bytes()` and `canonical_content_hash()`.

**Frozen wire rules:**

- all timestamps are RFC3339 UTC strings with millisecond precision, for example `1970-01-01T00:01:40.000Z`; injected epoch floats are converted at the boundary;
- `monotonic_offset_ms`, `sequence`, `revision`, `version` and `generation` are non-negative integers;
- IDs are non-empty UTF-8 strings of at most 256 characters without control characters;
- confidence and intensity are finite floats in `[0.0, 1.0]`;
- collection fields are tuples or recursively frozen mappings internally; `frozen=True` around a mutable dict/list is not accepted;
- every `from_dict()` rejects missing fields, extra fields, unknown enum values and unknown schema versions;
- every `to_dict()` returns a newly allocated JSON-compatible structure;
- canonical bytes are exactly:

```python
json.dumps(
    payload_without_content_hash,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
```

- hashes are lowercase SHA-256 hex over those bytes; no platform newline participates.

**Exact contract fields:**

| Type | Fields |
|---|---|
| `IdentityConstitution` | `schema_version:int`, `identity_id:str`, `name:str`, `kind:str`, `relationship_role:str`, `persona_invariants:tuple[str,...]`, `values:tuple[str,...]`, `hard_boundaries:tuple[str,...]`, `created_at:str`, `version:int`, `previous_version_hash:str|None`, `content_hash:str`, `approved_by:str`, `approved_at:str` |
| `InstanceRecord` | `schema_version:int`, `identity_id:str`, `lineage_id:str`, `instance_id:str`, `parent_instance_id:str|None`, `generation:int`, `environment_fingerprint_hash:str`, `created_at:str`, `last_started_at:str|None`, `last_clean_shutdown_at:str|None`, `fork_pending_review:bool` |
| `ContinuityCheckpoint` | `schema_version:int`, `instance_id:str`, `boot_id:str`, `started_at:str`, `clean_shutdown_at:str|None`, `last_event_cursor:int`, `active_request_id:str|None`, `temporary_authority_valid:bool`, `content_hash:str` |
| `StartupAssessment` | `schema_version:int`, `instance_id:str`, `previous_boot_id:str|None`, `unclean_shutdown:bool`, `temporary_authority_valid:bool`, `last_event_cursor:int`, `active_request_id:str|None`, `reason_code:str` |
| `LifeEvent` | `schema_version:int`, `event_id:str`, `event_type:str`, `timestamp_utc:str`, `monotonic_offset_ms:int`, `source:str`, `source_event_id:str|None`, `session_id:str|None`, `request_id:str|None`, `correlation_id:str|None`, `causation_id:str|None`, `sequence:int`, `identity_id:str`, `instance_id:str`, `payload:FrozenJsonObject`, `privacy_class:PrivacyClass`, `retention_class:RetentionClass`, `confidence:float`, `provenance:FrozenJsonObject`, `redaction_summary:tuple[str,...]` |
| `IdentitySummary` | `identity_id:str`, `name:str`, `kind:str`, `relationship_role:str`, `version:int`, `content_hash:str` |
| `InstanceSummary` | `lineage_id:str`, `instance_id:str`, `parent_instance_id:str|None`, `generation:int`, `fork_pending_review:bool` |
| `HealthSummary` | `status:str`, `degraded_components:tuple[str,...]`, `reason_codes:tuple[str,...]` |
| `LifeSnapshot` | `schema_version:int`, `revision:int`, `identity:IdentitySummary`, `instance:InstanceSummary`, `lifecycle_state:LifeCycleState`, `active_session_id:str|None`, `active_request_id:str|None`, `activity:str`, `health:HealthSummary`, `degradation_level:int`, `recovery_required:bool`, `last_event_id:str|None`, `last_sequence:int`, `updated_at:str`, `explanation:str` |
| `ExpressionIntent` | `schema_version:int`, `revision:int`, `base_state:ExpressionBaseState`, `intensity:float`, `gaze_target:GazeTarget`, `voice_activity:VoiceActivity`, `transition_ms:int`, `interrupt:bool`, `source_snapshot_revision:int`, `generated_at:str`, `expires_at:str`, `explanation_code:str` |

`PrivacyClass` 的完整值为 `public_surface/local_internal/user_private/secret/biometric/restricted_system`；`RetentionClass` 为 `ephemeral/session/operational/continuity/memory_candidate/audit/never_persist`。`LifeCycleState` 与设计规格的八种状态完全一致。Expression 三个枚举必须与 L0-B 的九种 base state、四种 gaze 和三种 voice activity 完全一致。

- [x] **Step 1: Write the failing contract tests**

```python
import dataclasses

import pytest

from core.life.contracts import (
    ContinuityCheckpoint,
    ExpressionIntent,
    ExpressionBaseState,
    GazeTarget,
    HealthSummary,
    IdentityConstitution,
    IdentitySummary,
    InstanceRecord,
    InstanceSummary,
    LifeEvent,
    LifeCycleState,
    PrivacyClass,
    RetentionClass,
    LifeSnapshot,
    StartupAssessment,
    VoiceActivity,
    canonical_content_hash,
)


def test_default_constitution_is_javis_and_model_independent():
    identity = IdentityConstitution.create_default(
        identity_id="identity-1",
        now=100.0,
    )
    data = identity.to_dict()
    assert data["name"] == "Javis"
    assert "model" not in data
    assert "api_key" not in data
    assert identity.verify_hash()


def test_expression_intent_v1_has_exact_wire_keys():
    intent = ExpressionIntent(
        schema_version=1,
        revision=4,
        base_state=ExpressionBaseState.IDLE,
        intensity=0.2,
        gaze_target=GazeTarget.NONE,
        voice_activity=VoiceActivity.SILENT,
        transition_ms=180,
        interrupt=False,
        source_snapshot_revision=4,
        generated_at="1970-01-01T00:01:41.000Z",
        expires_at="1970-01-01T00:01:46.000Z",
        explanation_code="quiet",
    )
    assert set(intent.to_dict()) == {
        "schema_version", "revision", "base_state", "intensity",
        "gaze_target", "voice_activity", "transition_ms", "interrupt",
        "source_snapshot_revision", "generated_at", "expires_at",
        "explanation_code",
    }


def test_enum_sets_and_canonical_hash_are_frozen():
    assert {item.value for item in LifeCycleState} == {
        "booting", "awake", "quiet", "engaged", "degraded",
        "recovering", "stopping", "offline",
    }
    assert {item.value for item in PrivacyClass} == {
        "public_surface", "local_internal", "user_private", "secret",
        "biometric", "restricted_system",
    }
    assert {item.value for item in RetentionClass} == {
        "ephemeral", "session", "operational", "continuity",
        "memory_candidate", "audit", "never_persist",
    }
    assert {item.value for item in ExpressionBaseState} == {
        "idle", "attention", "listening", "thinking", "speaking",
        "executing", "blocked", "error", "offline",
    }
    assert {item.value for item in GazeTarget} == {
        "none", "user", "content", "task",
    }
    assert {item.value for item in VoiceActivity} == {
        "silent", "listening", "speaking",
    }
    assert canonical_content_hash({"b": 1, "a": "贾维斯"}) == (
        "e6362375790e9ed55a0541d5cc1b62e2ef45366c5ff17b0d49620f819e96bb06"
    )


def test_all_public_contract_field_names_are_frozen():
    expected = {
        IdentityConstitution: (
            "schema_version", "identity_id", "name", "kind",
            "relationship_role", "persona_invariants", "values",
            "hard_boundaries", "created_at", "version",
            "previous_version_hash", "content_hash", "approved_by", "approved_at",
        ),
        InstanceRecord: (
            "schema_version", "identity_id", "lineage_id", "instance_id",
            "parent_instance_id", "generation", "environment_fingerprint_hash",
            "created_at", "last_started_at", "last_clean_shutdown_at",
            "fork_pending_review",
        ),
        ContinuityCheckpoint: (
            "schema_version", "instance_id", "boot_id", "started_at",
            "clean_shutdown_at", "last_event_cursor", "active_request_id",
            "temporary_authority_valid", "content_hash",
        ),
        StartupAssessment: (
            "schema_version", "instance_id", "previous_boot_id",
            "unclean_shutdown", "temporary_authority_valid",
            "last_event_cursor", "active_request_id", "reason_code",
        ),
        LifeEvent: (
            "schema_version", "event_id", "event_type", "timestamp_utc",
            "monotonic_offset_ms", "source", "source_event_id", "session_id",
            "request_id", "correlation_id", "causation_id", "sequence",
            "identity_id", "instance_id", "payload", "privacy_class",
            "retention_class", "confidence", "provenance", "redaction_summary",
        ),
        IdentitySummary: (
            "identity_id", "name", "kind", "relationship_role", "version",
            "content_hash",
        ),
        InstanceSummary: (
            "lineage_id", "instance_id", "parent_instance_id", "generation",
            "fork_pending_review",
        ),
        HealthSummary: ("status", "degraded_components", "reason_codes"),
        LifeSnapshot: (
            "schema_version", "revision", "identity", "instance",
            "lifecycle_state", "active_session_id", "active_request_id",
            "activity", "health", "degradation_level", "recovery_required",
            "last_event_id", "last_sequence", "updated_at", "explanation",
        ),
        ExpressionIntent: (
            "schema_version", "revision", "base_state", "intensity",
            "gaze_target", "voice_activity", "transition_ms", "interrupt",
            "source_snapshot_revision", "generated_at", "expires_at",
            "explanation_code",
        ),
    }
    for contract, field_names in expected.items():
        assert tuple(field.name for field in dataclasses.fields(contract)) == field_names


def test_round_trip_rejects_extra_fields_and_does_not_share_mutable_state():
    identity = IdentityConstitution.create_default(
        identity_id="identity-1",
        now=100.0,
    )
    wire = identity.to_dict()
    assert IdentityConstitution.from_dict(wire) == identity
    wire["persona_invariants"].append("mutated outside")
    assert "mutated outside" not in identity.persona_invariants
    invalid = identity.to_dict() | {"model": "forbidden"}
    with pytest.raises(ValueError, match="unexpected field"):
        IdentityConstitution.from_dict(invalid)


@pytest.mark.parametrize(
    ("field", "value"),
    [("intensity", -0.1), ("intensity", 1.1), ("transition_ms", -1)],
)
def test_expression_rejects_out_of_range_values(field, value):
    kwargs = {
        "schema_version": 1,
        "revision": 1,
        "base_state": "idle",
        "intensity": 0.2,
        "gaze_target": "none",
        "voice_activity": "silent",
        "transition_ms": 100,
        "interrupt": False,
        "source_snapshot_revision": 1,
        "generated_at": "1970-01-01T00:00:01.000Z",
        "expires_at": "1970-01-01T00:00:02.000Z",
        "explanation_code": "quiet",
    }
    kwargs[field] = value
    with pytest.raises(ValueError):
        ExpressionIntent.from_dict(kwargs)
```

- [x] **Step 2: Run the contract test and verify red**

Run:

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_contracts.py -q
```

Expected: collection fails because `core.life.contracts` does not exist.

- [x] **Step 3: Implement the immutable data contracts**

Use frozen dataclasses, string enums and recursive freeze/thaw helpers. `IdentityConstitution.create_default()` must calculate `content_hash` from canonical JSON excluding the hash field itself. Every `from_dict()` enforces the frozen wire rules above. `LifeEvent` must enforce `secret → never_persist` unless payload contains only a dedicated secure-store reference.

Task 1 is contract-only: it must not create directories, write files, inspect hardware, register EventBus handlers, implement lifecycle transitions or map snapshots to expressions. `quiet → idle`, expiration and expression revision belong only to Task 5 `ExpressionProjector`.

```python
class LifeCycleState(str, Enum):
    BOOTING = "booting"
    AWAKE = "awake"
    QUIET = "quiet"
    ENGAGED = "engaged"
    DEGRADED = "degraded"
    RECOVERING = "recovering"
    STOPPING = "stopping"
    OFFLINE = "offline"


@dataclass(frozen=True)
class ExpressionIntent:
    schema_version: int
    revision: int
    base_state: ExpressionBaseState
    intensity: float
    gaze_target: GazeTarget
    voice_activity: VoiceActivity
    transition_ms: int
    interrupt: bool
    source_snapshot_revision: int
    generated_at: str
    expires_at: str
    explanation_code: str
```

- [x] **Step 4: Run the focused tests**

Run the Task 1 command. Expected: all tests pass.

- [x] **Step 5: Commit the contract boundary**

```powershell
git add core/life/__init__.py core/life/contracts.py tests/test_life_contracts.py
git commit -m "feat(life): define identity and event contracts"
```

---

### Task 2: Separate Code Root from User Data Root

**Files:**
- Create: `core/life/paths.py`
- Modify: `core/runtime.py`
- Test: `tests/test_life_paths.py`

**Interfaces:**
- Consumes: code `root`, optional explicit `data_root`, environment mapping.
- Produces: `resolve_data_root(root, *, explicit=None, environ=os.environ)` and the kw-only `create_runtime(..., data_root=None)` contract.

- [x] **Step 1: Write failing path-precedence and no-code-root-write tests**

```python
from pathlib import Path

import pytest

from core.life.paths import resolve_data_root
from core.runtime import create_runtime


def test_data_root_precedence_is_explicit_then_env_then_compat_default(tmp_path):
    code = tmp_path / "code"
    explicit = tmp_path / "explicit-data"
    env = tmp_path / "env-data"
    assert resolve_data_root(
        code, explicit=explicit, environ={"JAVIS_DATA_ROOT": str(env)}
    ) == explicit.resolve()
    assert resolve_data_root(
        code, environ={"JAVIS_DATA_ROOT": str(env)}
    ) == env.resolve()
    assert resolve_data_root(code, environ={}) == (code / "data").resolve()


@pytest.mark.asyncio
async def test_explicit_data_root_keeps_runtime_databases_out_of_code_root(tmp_path):
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
        await runtime.aclose()
```

- [x] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_paths.py -q
```

Expected: collection fails because `core.life.paths` and the runtime `data_root` field do not exist.

- [x] **Step 3: Implement the resolver and migrate runtime-owned stores**

`resolve_data_root()` performs no I/O. `create_runtime()` resolves once, stores `JarvisRuntime.data_root`, and uses it for `SkillCatalog`, `AgentRunStore` and `ConversationStore`. It continues to use code `root` for `config.yaml`, source skills and executables. Do not silently catch an invalid explicit data path and fall back to the code root.

- [x] **Step 4: Run focused and runtime regressions**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_paths.py tests/test_runtime_agent_fusion.py tests/test_conversation_store.py -q
```

- [x] **Step 5: Commit the root boundary**

```powershell
git add core/life/paths.py core/runtime.py tests/test_life_paths.py
git commit -m "refactor(runtime): separate code and user data roots"
```

---

### Task 3: Implement the Versioned Identity Constitution

**Files:**
- Create: `core/life/identity.py`
- Test: `tests/test_life_identity.py`

**Interfaces:**
- Consumes: `IdentityConstitution` from Task 1 and the base user `data_root` from Task 2.
- Produces: `IdentityConstitutionStore.load_or_create()`, `load()`, `load_last_verified()`, `write_version(current, *, changes, approved_by)`, `rollback_to_previous(*, approved_by)` and `summary()`.

- [ ] **Step 1: Write failing first-birth, restart, mutation and corruption tests**

```python
def test_first_birth_creates_one_stable_identity(tmp_path):
    ids = iter(["identity-one", "identity-two"])
    store = IdentityConstitutionStore(tmp_path, id_factory=lambda: next(ids), now=lambda: 10.0)
    first = store.load_or_create()
    second = IdentityConstitutionStore(tmp_path, id_factory=lambda: next(ids), now=lambda: 20.0).load_or_create()
    assert first.identity_id == "identity-one"
    assert second.identity_id == first.identity_id
    assert second.content_hash == first.content_hash


def test_corrupt_current_identity_requires_recovery_without_overwrite(tmp_path):
    store = IdentityConstitutionStore(tmp_path, id_factory=lambda: "identity-one", now=lambda: 10.0)
    first = store.load_or_create()
    second = store.write_version(
        first,
        changes={"persona_invariants": ["quiet", "focused", "measured", "truthful"]},
        approved_by="user",
    )
    store.current_path.write_text("{broken", encoding="utf-8")
    before_v1 = store.version_path(first).read_bytes()
    before_v2 = store.version_path(second).read_bytes()
    with pytest.raises(IdentityRecoveryRequired, match="current constitution"):
        store.load()
    assert store.current_path.read_text(encoding="utf-8") == "{broken"
    assert store.version_path(first).read_bytes() == before_v1
    assert store.version_path(second).read_bytes() == before_v2
    assert store.load_last_verified().content_hash == second.content_hash


def test_rollback_is_explicit_and_auditable(tmp_path):
    store = IdentityConstitutionStore(tmp_path, id_factory=lambda: "identity-one", now=lambda: 10.0)
    first = store.load_or_create()
    second = store.write_version(
        first,
        changes={"persona_invariants": ["quiet", "focused", "measured", "truthful"]},
        approved_by="user",
    )
    rolled_back = store.rollback_to_previous(approved_by="user")
    assert rolled_back.version == second.version + 1
    assert rolled_back.previous_version_hash == second.content_hash
    assert rolled_back.persona_invariants == first.persona_invariants
    assert store.audit_records()[-1]["action"] == "identity.rollback"
```

- [ ] **Step 2: Run tests and verify the missing implementation failure**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_identity.py -q
```

- [ ] **Step 3: Implement atomic storage and version validation**

`IdentityConstitutionStore` receives the base `data_root` and itself stores files under `<data_root>/life/identity/`:

- `current.json` for the active constitution;
- `versions/<version>-<hash>.json` for immutable history;
- `recovery.json` for the last known valid version pointer.

`load()` never mutates disk and never silently rolls back. If `current.json` is corrupt, missing while versions exist, or its hash/version chain is invalid, raise `IdentityRecoveryRequired` and let `LifeService` enter read-only recovery. `load_last_verified()` scans immutable versions and returns the highest valid version; in the test above that is v2. `rollback_to_previous()` is a separate, explicitly approved operation: it creates a new version whose semantic fields match the prior version and appends an audit record; it never rewrites v1/v2 history.

Write to a sibling temporary file, `flush()` and `os.fsync()`, then `os.replace()`. Initial `approved_by` is `built_in_constitution`; later versions require a non-empty explicit approver. Never store model configuration or user secrets.

- [ ] **Step 4: Add rejection tests for model, secret and unapproved fields**

```python
def test_identity_rejects_runtime_and_secret_fields(tmp_path):
    store = IdentityConstitutionStore(tmp_path)
    identity = store.load_or_create()
    with pytest.raises(ValueError, match="forbidden identity field"):
        store.write_version(identity, changes={"model": "cloud-model"}, approved_by="user")
    with pytest.raises(ValueError, match="approved_by"):
        store.write_version(identity, changes={"persona_invariants": ["quiet"]}, approved_by="")
```

- [ ] **Step 5: Run Task 1 and Task 3 tests together**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_contracts.py tests/test_life_identity.py -q
```

- [ ] **Step 6: Commit identity storage**

```powershell
git add core/life/identity.py tests/test_life_identity.py
git commit -m "feat(life): persist versioned identity constitution"
```

---

### Task 4: Add Instance Lineage and Continuity Checkpoints

**Files:**
- Create: `core/life/lineage.py`
- Test: `tests/test_life_lineage.py`

**Interfaces:**
- Consumes: `identity_id`, data root, deterministic environment fingerprint, injected clock and ID factory.
- Produces: `InstanceLineageStore.load_or_create()`, `assess_previous_run(instance_id)`, `mark_started(instance_id, *, boot_id)`, `mark_clean_shutdown(instance_id, *, boot_id, last_event_cursor, active_request_id)` and `fork_for_environment()`.

- [ ] **Step 1: Write the failing lineage tests**

```python
def test_restart_keeps_instance_but_copy_creates_reviewable_fork(tmp_path):
    instance_ids = iter(["instance-one", "instance-two"])
    store = InstanceLineageStore(
        tmp_path,
        instance_id_factory=lambda: next(instance_ids),
        lineage_id_factory=lambda: "lineage-one",
        now=lambda: 10.0,
    )
    first = store.load_or_create("identity-one", environment_fingerprint="env-a")
    same = store.load_or_create("identity-one", environment_fingerprint="env-a")
    fork = store.load_or_create("identity-one", environment_fingerprint="env-b")
    assert same.instance_id == first.instance_id
    assert fork.instance_id != first.instance_id
    assert fork.parent_instance_id == first.instance_id
    assert fork.fork_pending_review is True


def test_unclean_shutdown_expires_temporary_authority(tmp_path):
    store = InstanceLineageStore(
        tmp_path,
        instance_id_factory=lambda: "instance-one",
        lineage_id_factory=lambda: "lineage-one",
        now=lambda: 10.0,
    )
    instance = store.load_or_create("identity-one", "env-a")
    store.mark_started(instance.instance_id, boot_id="boot-one")
    restarted = InstanceLineageStore(
        tmp_path,
        instance_id_factory=lambda: "unused",
        lineage_id_factory=lambda: "unused",
        now=lambda: 20.0,
    )
    assessment = restarted.assess_previous_run(instance.instance_id)
    assert assessment.unclean_shutdown is True
    assert assessment.temporary_authority_valid is False


def test_assessment_happens_before_current_boot_is_marked_started(tmp_path):
    store = InstanceLineageStore(
        tmp_path,
        instance_id_factory=lambda: "instance-one",
        lineage_id_factory=lambda: "lineage-one",
        now=lambda: 10.0,
    )
    instance = store.load_or_create("identity-one", "env-a")
    store.mark_started(instance.instance_id, boot_id="boot-one")
    store.mark_clean_shutdown(
        instance.instance_id,
        boot_id="boot-one",
        last_event_cursor=42,
        active_request_id=None,
    )
    assessment = store.assess_previous_run(instance.instance_id)
    assert assessment.unclean_shutdown is False
    assert assessment.last_event_cursor == 42
    store.mark_started(instance.instance_id, boot_id="boot-two")
```

- [ ] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_lineage.py -q
```

- [ ] **Step 3: Implement `InstanceLineageStore`**

`InstanceLineageStore` receives the base `data_root`; it uses JSON records under `<data_root>/life/instances/` and one atomic `active.json` pointer. The environment fingerprint must be injected; the store must not collect hardware serials itself. A clean shutdown checkpoint records the last life event cursor and active request ID but never stores temporary approval tokens.

Startup order is a hard protocol:

```python
assessment = store.assess_previous_run(instance.instance_id)
store.mark_started(instance.instance_id, boot_id=current_boot_id)
# runtime runs
store.mark_clean_shutdown(
    instance.instance_id,
    boot_id=current_boot_id,
    last_event_cursor=journal.cursor,
    active_request_id=conversation_hub.active_request_id,
)
```

Never call `mark_started()` before assessing the previous boot. `mark_clean_shutdown()` must reject a stale or mismatched boot ID.

- [ ] **Step 4: Run lineage and identity tests**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_identity.py tests/test_life_lineage.py -q
```

- [ ] **Step 5: Commit lineage storage**

```powershell
git add core/life/lineage.py tests/test_life_lineage.py
git commit -m "feat(life): track instance lineage and checkpoints"
```

---

### Task 5: Build the Minimal Lifecycle State Machine and Snapshot Projector

**Files:**
- Create: `core/life/state.py`
- Create: `core/life/expression.py`
- Test: `tests/test_life_state.py`

**Interfaces:**
- Consumes: `IdentityConstitution`, `InstanceRecord`, normalized observations.
- Produces: `MinimalLifeStateMachine.apply(event_type, payload, now)`, `snapshot()`, and `ExpressionProjector.project(snapshot, now)`.

- [ ] **Step 1: Write failing transition and stale-event tests**

```python
def test_quiet_request_listening_terminal_order_is_safe():
    machine = MinimalLifeStateMachine(
        identity_id="identity-one",
        instance_id="instance-one",
        now=0.0,
    )
    machine.apply("request.accepted", {"request_id": "r1"}, now=10.0)
    machine.apply("voice.listening", {"request_id": "r2"}, now=12.0)
    machine.apply("request.completed", {"request_id": "r1"}, now=13.0)
    assert machine.snapshot().activity == "listening"
    assert machine.snapshot().active_request_id == "r2"


def test_expression_revision_and_expiry_are_monotonic():
    machine = MinimalLifeStateMachine(
        identity_id="identity-one",
        instance_id="instance-one",
        now=0.0,
    )
    first = ExpressionProjector().project(machine.snapshot(), now=10.0)
    machine.apply("request.accepted", {"request_id": "r1"}, now=11.0)
    second = ExpressionProjector().project(machine.snapshot(), now=11.0)
    assert second.revision > first.revision
    assert second.expires_at > second.generated_at
```

- [ ] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_state.py -q
```

- [ ] **Step 3: Implement the explicit transition table**

Define the allowed lifecycle transitions as data, not nested implicit conditionals. Track `revision`, `active_session_id`, `active_request_id`, `activity`, `health`, `degradation_level`, `recovery_required`, `last_event_id`, `last_sequence`, `updated_at` and `explanation`.

Terminal events may clear only the matching request. `offline` is accepted only as a client projection and is not persisted as the backend's final lifecycle checkpoint.

- [ ] **Step 4: Implement exact ExpressionIntent mapping**

```python
BASE_STATE_BY_ACTIVITY = {
    "quiet": "idle",
    "attention": "attention",
    "listening": "listening",
    "thinking": "thinking",
    "speaking": "speaking",
    "executing": "executing",
    "blocked": "blocked",
    "error": "error",
    "offline": "offline",
}
```

Clamp intensity to `[0.0, 1.0]`; restrict gaze to `none/user/content/task`; restrict voice activity to `silent/listening/speaking`.

- [ ] **Step 5: Run Tasks 1 and 3-5 tests**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_contracts.py tests/test_life_identity.py tests/test_life_lineage.py tests/test_life_state.py -q
```

- [ ] **Step 6: Commit lifecycle and expression projection**

```powershell
git add core/life/state.py core/life/expression.py tests/test_life_state.py
git commit -m "feat(life): add minimal lifecycle and expression projection"
```

---

### Task 6: Add Privacy Classification and Deterministic Event Mapping

**Files:**
- Create: `core/life/privacy.py`
- Create: `core/life/event_adapter.py`
- Test: `tests/test_life_event_adapter.py`

**Interfaces:**
- Consumes: existing `core.events.Event`, current identity/instance IDs.
- Produces: `PrivacyPolicy.classify()`, `redact_payload()`, `index_summary()` and `LifeEventAdapter.map(event)`.

- [ ] **Step 1: Write failing mapping and privacy tests**

```python
def test_tool_payload_mapping_preserves_existing_fields_without_inventing_task():
    source = Event(
        id="e1", type="tool.completed", source="tools",
        payload={"tool": "screenshot", "duration_ms": 31, "success": True},
        correlation_id="run-1", causation_id="e0", sequence=7,
    )
    adapter = LifeEventAdapter(
        identity_id="identity-one",
        instance_id="instance-one",
        now=lambda: 100.0,
    )
    mapped = adapter.map(source)[0]
    assert mapped.source_event_id == "e1"
    assert mapped.correlation_id == "run-1"
    assert mapped.causation_id == "e0"
    assert mapped.payload["duration_ms"] == 31
    assert "task" not in mapped.payload


def test_secrets_are_redacted_and_never_indexed():
    payload = {"api_key": "sk-secret", "text": "private prompt"}
    policy = PrivacyPolicy()
    redacted = policy.redact_payload("provider.configured", payload)
    assert "sk-secret" not in json.dumps(redacted)
    assert policy.index_summary("provider.configured", payload) == ""
```

- [ ] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_event_adapter.py -q
```

- [ ] **Step 3: Implement allowlisted mapping rules**

Create an explicit map for runtime, subsystem, thinking, tool, approval, agent_run, memory and request/activity event families. Unknown events either become `life.observation.unknown` with a redacted summary or are ignored according to retention policy. Never copy a payload wholesale before classification.

- [ ] **Step 4: Add regression tests for malformed and oversized payloads**

Limit string values, nesting depth, collection length and total serialized bytes. Cyclic or unserializable objects produce a diagnostic event without raising into EventBus.

- [ ] **Step 5: Run focused tests and existing typed-event tests**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_event_adapter.py tests/test_typed_events.py -q
```

- [ ] **Step 6: Commit mapping and privacy**

```powershell
git add core/life/privacy.py core/life/event_adapter.py tests/test_life_event_adapter.py
git commit -m "feat(life): classify and map runtime events safely"
```

---

### Task 7: Implement the Asynchronous Life Event Journal

**Files:**
- Create: `core/life/journal.py`
- Modify: `memory/session_db.py`
- Test: `tests/test_life_journal.py`
- Test: `tests/test_session_event_store_async.py`

**Interfaces:**
- Consumes: `LifeEvent` and `PrivacyPolicy.index_summary()`.
- Produces: `LifeEventJournal.start()`, `enqueue()`, `recent()`, `status()`, `flush()`, `stop()`.

- [ ] **Step 1: Write failing persistence, idempotency and slow-I/O tests**

```python
import time

from core.life.contracts import LifeEvent, PrivacyClass, RetentionClass
from core.life.journal import LifeEventJournal


def make_life_event(
    event_id="life-1",
    *,
    correlation_id=None,
    sequence=1,
    retention="operational",
):
    return LifeEvent(
        schema_version=1,
        event_id=event_id,
        event_type="life.test",
        timestamp_utc="1970-01-01T00:00:01.000Z",
        monotonic_offset_ms=1,
        source="test",
        source_event_id=event_id,
        session_id="session-1",
        request_id=None,
        correlation_id=correlation_id,
        causation_id=None,
        sequence=sequence,
        identity_id="identity-1",
        instance_id="instance-1",
        payload={"safe": True},
        privacy_class=PrivacyClass.LOCAL_INTERNAL,
        retention_class=RetentionClass(retention),
        confidence=1.0,
        provenance={"fixture": "tests/test_life_journal.py"},
        redaction_summary=("safe test event",),
    )


def paused_journal(tmp_path, capacity):
    return LifeEventJournal(tmp_path / "life.sqlite3", capacity=capacity)


def test_journal_preserves_full_envelope_and_is_idempotent(tmp_path):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=8)
    journal.start()
    event = make_life_event(event_id="life-1", correlation_id="run-1", sequence=4)
    assert journal.enqueue(event) is True
    assert journal.enqueue(event) is False
    assert journal.flush(timeout=1.0) is True
    row = journal.recent(limit=1)[0]
    assert row["event_id"] == "life-1"
    assert row["correlation_id"] == "run-1"
    assert row["sequence"] == 4
    journal.stop(timeout=1.0)


def test_slow_sqlite_does_not_block_enqueue(tmp_path, monkeypatch):
    journal = LifeEventJournal(tmp_path / "life.sqlite3", capacity=8)
    journal.start()
    monkeypatch.setattr(journal, "_write_batch", lambda batch: time.sleep(0.2))
    started = time.perf_counter()
    assert journal.enqueue(make_life_event(event_id="life-1")) is True
    assert time.perf_counter() - started < 0.05
    journal.stop(timeout=1.0)
```

- [ ] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_journal.py -q
```

- [ ] **Step 3: Implement schema, worker and priority queue rules**

Use one daemon worker with explicit `start()`/`stop()` and bounded `queue.Queue`. Construction creates no worker; `enqueue()` is valid before `start()` so overload behavior can be tested deterministically. Create `events` and allowed-summary FTS tables. Critical identity, approval, deletion, recovery and action-result events reserve queue capacity; duplicate surface state events coalesce by `(event_type, correlation_id)`.

- [ ] **Step 4: Add overload and shutdown tests**

```python
def test_full_queue_drops_low_value_but_preserves_audit(tmp_path):
    journal = paused_journal(tmp_path, capacity=2)
    assert journal.enqueue(make_life_event("low-1", retention="ephemeral"))
    assert journal.enqueue(make_life_event("low-2", retention="ephemeral"))
    assert journal.enqueue(make_life_event("audit-1", retention="audit"))
    assert journal.status()["dropped_ephemeral"] == 1
    assert "audit-1" in journal.pending_event_ids()
```

- [ ] **Step 5: Move the existing SessionEventStore off the synchronous wildcard hot path**

Change `SessionEventStore.attach(bus)` to enqueue an allowlisted, redacted event record and let one owned worker call the existing SQLite code. Preserve `recent_events()`, memory candidates and evolution candidates. Index only the explicit redacted summary; never index the original full payload.

```python
def test_attached_session_store_does_not_write_sqlite_in_publish_thread(tmp_path, monkeypatch):
    store = SessionEventStore(tmp_path / "events.sqlite3")
    bus = EventBus()
    store.attach(bus)
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(store, "_record_event_db", lambda event: (entered.set(), release.wait(1)))
    started = time.perf_counter()
    bus.publish("tool.completed", {"tool": "screenshot", "success": True})
    assert time.perf_counter() - started < 0.05
    assert entered.wait(1)
    release.set()
    store.close()
```

- [ ] **Step 6: Run journal, adapter and SessionEventStore regressions**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_journal.py tests/test_life_event_adapter.py tests/test_session_event_store_async.py tests/test_p0_runtime.py -q
```

- [ ] **Step 7: Commit both asynchronous journals**

```powershell
git add core/life/journal.py memory/session_db.py tests/test_life_journal.py tests/test_session_event_store_async.py
git commit -m "feat(life): persist life events off the hot path"
```

---

### Task 8: Assemble `LifeService` in `JarvisRuntime`

**Files:**
- Create: `core/life/service.py`
- Modify: `core/runtime.py`
- Modify: `core/conversation_hub.py`
- Test: `tests/test_life_service.py`

**Interfaces:**
- Consumes: stores, state machine, adapter, journal, expression projector and Runtime EventBus.
- Produces: `LifeService.start(runtime)`, `stop()`, `snapshot()`, `identity_summary()`, `lineage_summary()`, `recent_events()` and `subscribe(listener)`.

- [ ] **Step 1: Write the failing runtime assembly test**

```python
def test_runtime_registers_one_life_service_with_no_startup_model_calls(tmp_path):
    runtime = create_runtime(
        root=tmp_path / "code",
        startup_side_effects=False,
        data_root=tmp_path / "user-data",
    )
    try:
        assert runtime.life is runtime.subsystems["life"]
        assert runtime.life.snapshot().identity.identity_id
        assert runtime.life.snapshot().lifecycle_state.value in {"awake", "quiet"}
        assert runtime.life.status()["journal_state"] == "running"
    finally:
        runtime.close()
    assert runtime.life.status()["journal_state"] == "stopped"
```

- [ ] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_service.py -q
```

- [ ] **Step 3: Implement service start/stop and runtime field**

Add `life: LifeService` to `JarvisRuntime`. During `create_runtime()`, instantiate it with the base `runtime.data_root` from Task 2; never pass `data_root / "life"`. Register it after the existing stores exist and before `runtime.created` is published. Pass the existing runtime `EventBus` into `ConversationHub`; do not create a second bus.

Startup performs `assess_previous_run()` before `mark_started(current_boot_id)`. `start()` installs exactly one wildcard handler. Because the existing EventBus has no unsubscribe, `stop()` atomically disables that handler before flushing; repeated `start()` is rejected and stopped callbacks become no-ops. It then writes the clean checkpoint and joins the journal worker. EventBus API expansion is not required for L0-A.

- [ ] **Step 4: Prove no synchronous SQLite in the handler**

Patch `journal._write_batch` to block and publish `tool.completed`; assert `EventBus.publish()` returns before the block is released.

- [ ] **Step 5: Run runtime regressions**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_service.py tests/test_runtime_agent_fusion.py tests/test_p0_runtime.py -q
```

- [ ] **Step 6: Commit runtime assembly**

```powershell
git add core/life/service.py core/runtime.py core/conversation_hub.py tests/test_life_service.py
git commit -m "feat(life): assemble life service in runtime"
```

---

### Task 9: Add an Owned Graceful Desktop Shutdown

**Files:**
- Modify: `main.py`
- Modify: `app/src-tauri/src/sidecar.rs`
- Test: `tests/test_owned_runtime_shutdown.py`
- Test: `app/src-tauri/src/sidecar.rs` inline Rust tests

**Interfaces:**
- Consumes: per-process `JAVIS_SIDECAR_OWNERSHIP`, FastAPI lifespan, Tauri-owned child PID.
- Produces: loopback-only `POST /api/runtime/shutdown`, finite graceful wait, force-kill fallback.

- [ ] **Step 1: Write failing ownership and lifecycle tests**

Python tests prove missing/wrong tokens return 403 without closing runtime, the correct constant-time token comparison schedules server exit only after `runtime.aclose()` completes, and the endpoint is unavailable to non-loopback clients. Inject the shutdown callback; tests must not terminate the pytest process.

Rust tests use an injected HTTP requester and child handle to prove this order:

```text
POST loopback shutdown with ownership token
→ wait up to 5 seconds for owned child exit
→ if exited: clear ownership without kill
→ if request/timeout fails: kill and wait
```

- [ ] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_owned_runtime_shutdown.py -q
Push-Location app/src-tauri
try { cargo test sidecar --quiet } finally { Pop-Location }
```

- [ ] **Step 3: Implement the protected handshake**

`main.py` reads the ownership token once at process start, compares with `hmac.compare_digest()`, rejects an empty configured token, and accepts only loopback peers. The endpoint sets a shutdown-requested flag; the ASGI server exits through its normal lifespan so `runtime.aclose()` and LifeService clean checkpoint run exactly once.

Tauri must pass a real user-data root in `JAVIS_DATA_ROOT`—the app data directory or the user's explicit configured directory—not the packaged code `root`. `stop_owned()` copies the token/PID without holding the mutex across HTTP/wait operations, requests graceful shutdown, polls the owned child for at most 5 seconds, and only then uses `kill()` as a fallback. It never sends the token to any non-loopback address and never stops an attached, unowned backend.

- [ ] **Step 4: Run Python, Rust and shutdown regressions**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_owned_runtime_shutdown.py tests/test_life_lineage.py tests/test_life_service.py -q
Push-Location app/src-tauri
try { cargo test --quiet } finally { Pop-Location }
```

- [ ] **Step 5: Commit the shutdown boundary**

```powershell
git add main.py app/src-tauri/src/sidecar.rs tests/test_owned_runtime_shutdown.py
git commit -m "fix(runtime): checkpoint before owned sidecar exit"
```

---

### Task 10: Add Read-Only Life APIs and Session Push

**Files:**
- Create: `core/life/api.py`
- Modify: `core/conversation_hub.py`
- Modify: `main.py`
- Test: `tests/test_life_api.py`
- Test: `tests/test_conversation_life_event_bridge.py`

**Interfaces:**
- Consumes: `LifeService`, active ConversationHub subscriptions and the runtime EventBus injected in Task 8.
- Produces: `GET /api/life/identity`, `/api/life/snapshot`, `/api/life/lineage`, `/api/life/events`; `ConversationHub.publish_system_event()`, `subscribed_sessions()`, allowlisted conversation observations; `life.snapshot` and `life.expression` session events.

- [ ] **Step 1: Write failing API authorization and redaction tests**

```python
def test_life_snapshot_api_is_read_only_and_redacted(client):
    response = client.get("/api/life/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    serialized = json.dumps(body)
    assert "api_key" not in serialized
    assert client.post("/api/life/snapshot", json={}).status_code == 405


@pytest.mark.asyncio
async def test_public_system_publish_reaches_attached_session(runtime):
    subscription = await runtime.conversation_hub.attach("session-1")
    await runtime.conversation_hub.publish_system_event(
        "session-1", "life.snapshot", {"revision": 2}
    )
    event = await subscription.get()
    assert event["type"] == "life.snapshot"
    assert event["payload"]["revision"] == 2


async def immediate_success_runner(request, token):
    await token.checkpoint()
    yield {"type": "done", "success": True, "detail": "completed"}


@pytest.mark.asyncio
async def test_conversation_lifecycle_reaches_runtime_bus_without_private_text(runtime):
    seen = []
    runtime.event_bus.subscribe("*", seen.append)
    await runtime.conversation_hub.submit(
        ConversationRequest(
            session_id="session-1",
            request_id="request-1",
            text="private user sentence",
            interaction_mode="live",
        ),
        immediate_success_runner,
    )
    await runtime.conversation_hub.wait_for_terminal("request-1")
    lifecycle = [event for event in seen if event.source == "conversation"]
    assert [event.type for event in lifecycle] == [
        "request.accepted", "request.completed",
    ]
    assert "private user sentence" not in json.dumps(
        [event.payload for event in lifecycle]
    )
    accepted = lifecycle[0]
    canonical = runtime.conversation_store.events_after("session-1", sequence=0)
    canonical_accepted = next(event for event in canonical if event["type"] == "request.accepted")
    assert accepted.payload["source_event_id"] == canonical_accepted["event_id"]
    assert accepted.payload["source_sequence"] == canonical_accepted["sequence"]
    assert accepted.payload["source_sequence_domain"] == "conversation_store:session-1"
    assert accepted.payload["session_id"] == "session-1"
    assert accepted.payload["request_id"] == "request-1"
    assert accepted.payload["correlation_id"] == "request-1"
    assert accepted.payload["interaction_mode"] == "live"
```

- [ ] **Step 2: Run and verify red**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_api.py -q
```

- [ ] **Step 3: Implement the router and public hub method**

`publish_system_event(session_id, event_type, payload)` must allow only `life.snapshot`, `life.expression` and future explicitly registered system event types. It calls the existing durable `_publish` path with an empty request ID and must not expose arbitrary event injection to HTTP clients.

`subscribed_sessions()` returns an immutable tuple of normalized session IDs. It does not expose subscriber queues.

Add a private `_publish_runtime_observation()` called by `_publish()` only after the ConversationStore append succeeds. It publishes the same event type on the injected runtime EventBus for this closed allowlist: `request.accepted`, `request.cancellation_pending`, `request.completed`, `request.cancelled`, `request.failed`, `activity.understanding`, validated `activity.*` states and `approval.required`.

Every bridge payload preserves the canonical conversation `event_id` as `source_event_id`, its session sequence as `source_sequence`, `source_sequence_domain="conversation_store:<session_id>"`, normalized `session_id`, `request_id`, `correlation_id=request_id` and the request's `interaction_mode`. Activity code, tool name, success boolean and bounded diagnostic code may be added when allowlisted. The EventBus's own event ID/sequence remain a separate transport domain and never replace these source fields.

The bridge must drop user text, `response.delta`, model output, tool params/data, approval params and free-form private detail. `life.snapshot` and `life.expression` are outbound system events and must not loop back into the runtime observation bridge.

- [ ] **Step 4: Wire main without adding write routes**

Create the router from `runtime.life`, include it once, and register a LifeService listener that schedules snapshot/expression publication only for `subscribed_sessions()`. Capture the FastAPI event loop during startup and use `loop.call_soon_threadsafe()` before `asyncio.create_task()` so EventBus publications from worker threads never call asyncio APIs directly. Coalesce multiple state changes per event-loop tick.

- [ ] **Step 5: Run API and conversation integration tests**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_api.py tests/test_conversation_life_event_bridge.py tests/test_unified_conversation_integration.py tests/test_conversation_watchdog_harness.py -q
```

- [ ] **Step 6: Commit API and session push**

```powershell
git add core/life/api.py core/conversation_hub.py main.py tests/test_life_api.py tests/test_conversation_life_event_bridge.py
git commit -m "feat(life): expose read-only life snapshots"
```

---

### Task 11: Add the Frontend Compatibility Bridge

**Files:**
- Create: `app/src/life/lifeTypes.ts`
- Create: `app/src/life/LifeStateBridge.ts`
- Modify: `app/src/main.ts`
- Test: `app/tests/lifeStateBridge.test.ts`

**Interfaces:**
- Consumes: `BackendEvent` types `life.snapshot` and `life.expression`.
- Produces: `LifeStateBridge.handle(event)`, `snapshot()`, `subscribeExpression(listener)`; existing RuntimeStateCoordinator signals.

- [ ] **Step 1: Write failing TypeScript guard and stale-revision tests**

```typescript
type LifeSurfaceState =
  | "idle" | "attention" | "listening" | "thinking" | "speaking"
  | "executing" | "blocked" | "error" | "offline";

function lifeSnapshotEvent(revision: number, activity: LifeSurfaceState) {
  return {
    type: "life.snapshot",
    payload: {
      schema_version: 1,
      revision,
      identity: {
        identity_id: "identity-1", name: "Javis", kind: "local_digital_life",
        relationship_role: "partner", version: 1, content_hash: "a".repeat(64),
      },
      instance: {
        instance_id: "instance-1", lineage_id: "lineage-1",
        parent_instance_id: null, generation: 0, fork_pending_review: false,
      },
      lifecycle_state: activity === "offline" ? "degraded" : "awake",
      active_session_id: "session-1",
      active_request_id: null,
      activity,
      health: {
        status: activity === "error" ? "degraded" : "healthy",
        degraded_components: activity === "error" ? ["test"] : [],
        reason_codes: activity === "error" ? ["test_error"] : [],
      },
      degradation_level: activity === "offline" ? 1 : 0,
      recovery_required: activity === "offline",
      last_event_id: `life-${revision}`,
      last_sequence: revision,
      updated_at: `1970-01-01T00:00:0${revision}.000Z`,
      explanation: "test-fixture",
    },
  } as const;
}


test("rejects malformed life events and ignores stale revisions", () => {
  const seen: string[] = [];
  const bridge = createLifeStateBridge({
    signal: (signal) => seen.push(signal.state),
  });
  assert.equal(bridge.handle({ type: "life.snapshot", payload: { revision: "bad" } }), false);
  assert.equal(bridge.handle(lifeSnapshotEvent(3, "thinking")), true);
  assert.equal(bridge.handle(lifeSnapshotEvent(2, "speaking")), false);
  assert.deepEqual(seen, ["thinking"]);
});

test("offline remains a client projection and reconnect accepts a newer snapshot", () => {
  const bridge = createLifeStateBridge({ signal: () => undefined });
  bridge.projectOffline("backend unavailable");
  assert.equal(bridge.snapshot().state, "offline");
  bridge.handle(lifeSnapshotEvent(4, "idle"));
  assert.equal(bridge.snapshot().state, "idle");
});
```

- [ ] **Step 2: Run and verify red**

```powershell
Set-Location app
pnpm.cmd test
```

Expected: TypeScript test import fails because the bridge does not exist.

- [ ] **Step 3: Implement strict wire guards and compatibility mapping**

Do not cast arbitrary `BackendEvent.payload` directly. Validate schema version, revision, state enum, timestamps and intent fields. Map life states to existing LiveState. Unknown or unsupported life events return `false`, allowing existing BackendClient state handling to remain authoritative.

- [ ] **Step 4: Wire one call in `main.ts`**

Inside the existing `onEvent` callback, call `lifeStateBridge.handle(event)` before normal conversation rendering. Do not create another WebSocket or add another direct writer to Pet/Orb.

- [ ] **Step 5: Run all App tests and build**

```powershell
Set-Location app
pnpm.cmd test
pnpm.cmd build
```

- [ ] **Step 6: Commit the compatibility bridge**

```powershell
git add app/src/life/lifeTypes.ts app/src/life/LifeStateBridge.ts app/src/main.ts app/tests/lifeStateBridge.test.ts
git commit -m "feat(app): bridge life snapshots to existing surfaces"
```

---

### Task 12: Lock Recovery, Privacy, Release and Regression Contracts

**Files:**
- Create: `tests/test_life_release_contract.py`
- Modify: `docs/JAVIS_OPERATIONS.md`
- Create: `docs/verification/JAVIS_L0A_D_DRIVE_ACCEPTANCE.md`

**Interfaces:**
- Consumes: complete L0-A runtime and frontend bridge.
- Produces: source-level release gates, operator recovery steps and D-drive acceptance checklist.

- [ ] **Step 1: Write the failing release contract test**

```python
def test_life_kernel_release_contract_is_present_and_read_only():
    root = Path(__file__).resolve().parents[1]
    main = (root / "main.py").read_text(encoding="utf-8")
    runtime = (root / "core/runtime.py").read_text(encoding="utf-8")
    assert "create_life_router" in main
    assert "runtime.life" in main
    assert "LifeService" in runtime
    api = (root / "core/life/api.py").read_text(encoding="utf-8")
    assert "@router.post" not in api
    assert "@router.put" not in api
    assert "@router.patch" not in api
    assert "@router.delete" not in api


def test_life_data_never_targets_source_or_d_drive():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (Path("core/life")).glob("*.py")
    )
    assert "D:\\\\" not in source
    assert "brain_data/rules" not in source
```

- [ ] **Step 2: Run and fix the contract if it exposes an unplanned write path**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_release_contract.py -q
```

- [ ] **Step 3: Document exact recovery operations**

Add operations for viewing identity summary, identifying recovery mode, exporting diagnostic events, restoring the previous constitution, and confirming no temporary authority survived an unclean shutdown. Commands must point to the configured user-data root, never hard-code D drive.

- [ ] **Step 4: Write the D-drive acceptance checklist**

The checklist must record package hash, source commit, identity ID before/after restart, instance ID before/after restart, model route before/after switch, abnormal shutdown result, corruption recovery result, Live/Code/Pet snapshot revisions and explicit PASS/FAIL evidence fields.

- [ ] **Step 5: Run the complete automated gates**

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest tests/test_life_*.py tests/test_typed_events.py tests/test_unified_conversation_integration.py tests/test_p0_runtime.py -q
Set-Location app
pnpm.cmd test
pnpm.cmd build
```

Expected: every command exits 0. This does not mark D-drive scenarios passed.

- [ ] **Step 6: Commit release contracts and operations**

```powershell
git add tests/test_life_release_contract.py docs/JAVIS_OPERATIONS.md docs/verification/JAVIS_L0A_D_DRIVE_ACCEPTANCE.md
git commit -m "test(life): lock recovery and release contracts"
```

---

## Spec Coverage Matrix

| Approved L0-A design area | Implementation tasks | Executable evidence |
|---|---:|---|
| Incremental life facade and no subsystem rewrite | 1, 8, 10, 11 | contract, runtime assembly, API and App regressions |
| Code root / user data root separation | 2, 8, 9, 12 | precedence, no-source-write, Tauri data-root and release tests |
| Identity constitution fields, version chain, approval and rollback | 1, 3 | `test_life_contracts.py`, `test_life_identity.py` |
| User/Javis identity separation and model independence | 1, 3, 12 | forbidden-field and release-source assertions |
| Birth, restart, copy/move fork and abnormal-shutdown lineage | 4, 8, 9, 12 | lineage, graceful/forced shutdown tests plus D-drive recovery matrix |
| Complete 21-field event envelope, privacy and retention dimensions | 1, 6, 7 | contract, adapter, journal persistence tests |
| Existing publisher mapping, real conversation bridge and high-frequency-event exclusion | 6, 8, 10, 12 | parameterized event map, production ConversationHub bridge and publisher-source review |
| Bounded asynchronous journaling, priority, idempotency and cursors | 7 | slow-I/O, overload, duplicate and shutdown tests |
| Minimal lifecycle, snapshot, stale terminal protection | 5, 8 | transition and runtime service tests |
| Read-only backend exits and unified-session push | 10 | API method, redaction and ConversationHub tests |
| Exact 12-field L0-B interface and frontend compatibility fallback | 1, 5, 11 | Python wire-key and TypeScript guard/revision tests |
| Corruption, unwritable journal, overload and protocol recovery | 3, 4, 7, 11, 12 | recovery tests, status diagnostics and acceptance checklist |
| Security, privacy, automated gates and D-drive truthfulness | 6, 7, 12 | redacted fixtures, release contract and explicit PASS/FAIL evidence |

---

## Final Verification

- [ ] Run `git diff --check` and confirm no whitespace errors.
- [ ] Run every Python test named in Task 12 Step 5 from the isolated worktree.
- [ ] Run the complete App test suite and production build.
- [ ] Search `core/life`, `app/src/life` and `tests/test_life_*.py` for unfinished placeholder markers and remove them.
- [ ] Confirm `git status --short` contains only intended L0-A files before integration.
- [ ] Review the event map against actual publishers; remove any mapping whose source event cannot be produced.
- [ ] Confirm no API Key, token, raw audio, image or OCR fixture is present in the committed event database.
- [ ] Confirm L0-B's 12-field contract matches `ExpressionIntent.to_dict()` exactly.
- [ ] Build the installer only after the feature branch has passed review and been integrated into `G:\Javis`.
- [ ] Execute the D-drive checklist on the exact built package; record failures honestly and return fixes to a worktree.

## Execution Boundary

Completing this plan produces L0-A only. It does not authorize L1 emotions, L2 autobiographical memory, Agent expansion, proactive actions or full migration/sync. L0-B may be implemented in parallel only through the shared `ExpressionIntent v1` contract and must not write LifeState.
