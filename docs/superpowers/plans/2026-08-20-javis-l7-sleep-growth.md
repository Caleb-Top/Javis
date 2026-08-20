# Javis L7 睡眠、成长与完整身体实施计划

> **执行规则：** 每个任务先写失败测试，再做最小实现；每个提交必须独立可回滚。本文只定义实施，不授权在本文编写阶段修改产品源码、构建安装包或提交 Git。

**日期：** 2026-08-20
**状态：** 待实施
**批准规格：** `docs/superpowers/specs/2026-08-20-javis-l7-sleep-growth-design.md`
**记录基线：** `e5de6265`，实施开始时必须重新记录实际基线
**建议分支：** `codex/javis-life-os-l7-sleep-growth-20260820`
**建议隔离目录：** `G:\Javis-worktrees\javis-life-os-l7-sleep-growth-20260820`

## 1. 全局约束

- `G:\Javis` 只作为正式集成区；开发、沙箱和验证不能把临时状态写入源码树；
- L2 MemoryService、L5 IntentService、L6 ActionService 的权威收据合同必须先落地，缺失时用 protocol fake 做单测，但不能把 fake 接入生产并宣称 L7 完成；
- 只复用一个 LifeService、EventBus、Runtime、ConversationHub、AvatarController 和用户数据根；
- 所有新持久状态显式注入 `data_root`，生产路径只接受 `resolve_data_root()`；
- 不新增网络运行时依赖，不下载模型，不在 sleep 中训练参数；
- 不允许普通 Python subprocess 冒充安全沙箱；Windows 原生受限执行不可用时 SkillForge 失败关闭；
- 不删除旧数据。旧 `brain_data/`、`data/` 和 `config.yaml` 在 L7 只停止新增写入，完整搬迁和回滚由 L8 执行；
- Body 接线必须保留 2D/Orb fallback、拖动、上下文菜单和打开 Live 的现有行为；
- 本计划中的 `Commit` 是未来实施提交边界，本次文档工作不执行提交。

## 2. 冻结文件结构

### 新建后端文件

- `core/life/l7/__init__.py`
- `core/life/l7/contracts.py`
- `core/life/l7/layout.py`
- `core/life/l7/run_store.py`
- `core/life/l7/growth_store.py`
- `core/life/l7/sleep.py`
- `core/life/l7/supervisor.py`
- `core/life/l7/skill_forge.py`
- `core/life/l7/skill_deployment.py`
- `core/life/l7/api.py`
- `core/life/l7/legacy.py`

### 新建原生和前端文件

- `app/src-tauri/src/skill_sandbox.rs`
- `app/src/pet/body/bodyTypes.ts`
- `app/src/pet/body/BodyCapability.ts`
- `app/src/pet/body/ExpressionPlanner.ts`
- `app/src/pet/body/BodyRuntime.ts`
- `app/src/growth/growthTypes.ts`
- `app/src/growth/GrowthClient.ts`
- `app/src/growth/GrowthSettings.ts`

### 主要修改文件

- `core/runtime.py`
- `core/skill_catalog.py`
- `core/skill_creator.py`
- `core/skill_manager.py`
- `core/auto_updater.py`
- `kernel/kernel.py`
- `kernel/training/sleep_learning.py`
- `main.py`
- `app/src-tauri/src/main.rs`
- `app/src/life/lifeTypes.ts`
- `app/src/pet/PetSurface.ts`
- `app/src/pet/petTypes.ts`
- `app/src/pet/petPreferences.ts`
- `app/src/pet/avatar/AvatarSurface.ts`
- `app/src/pet/avatar/AvatarController.ts`
- `app/src/pet/avatar/SpeakingEnvelope.ts`
- `app/src/bridge/backendClient.ts`
- `app/src/settings/SettingsSurface.ts`
- `app/src/main.ts`

### 新建测试文件

- `tests/test_l7_contracts.py`
- `tests/test_l7_layout.py`
- `tests/test_l7_governance_boundary.py`
- `tests/test_l7_run_store.py`
- `tests/test_l7_growth_store.py`
- `tests/test_l7_sleep_coordinator.py`
- `tests/test_l7_supervisor.py`
- `tests/test_l7_skill_forge.py`
- `tests/test_l7_skill_deployment.py`
- `tests/test_l7_api.py`
- `tests/test_l7_legacy_quarantine.py`
- `tests/test_l7_release_contract.py`
- `app/tests/bodyCapability.test.ts`
- `app/tests/expressionPlanner.test.ts`
- `app/tests/bodyRuntime.test.ts`
- `app/tests/petBodyIntegration.test.ts`
- `app/tests/avatarPetIntegration.test.ts`，若前序 B0/B1 接线已创建则扩展现有文件
- `app/tests/growthSettings.test.ts`

## 3. 依赖顺序

1. 先冻结 strict contracts 和 DataRootLayout；
2. 在任何 forge/sleep 新能力前先移除 updater、SkillCreator、SkillManager 和 runtime loader 绕过；
3. RunStore/GrowthStore 先于 coordinator；
4. 原生 sandbox 先于 SkillForge；
5. artifact 与 deployment gate 先于任何新技能加载；
6. Body contracts 和 planner 先于 PetSurface 产品接线；
7. API/UI 最后接入已验证的单写者服务；
8. 阶段出口才执行完整回归、打包和 D 盘真实验收。

## 4. 工作包

### Task 1：冻结 L7 合同与数据根布局

**文件：**

- Create: `core/life/l7/contracts.py`
- Create: `core/life/l7/layout.py`
- Create: `core/life/l7/__init__.py`
- Test: `tests/test_l7_contracts.py`
- Test: `tests/test_l7_layout.py`

**实现：**

- 实现规格中的 `SleepPolicyV1`、`SleepRunV1`、`SleepRunTransitionV1`、`SleepJobReceiptV1`；
- 实现 `GrowthCandidateV1`、`CandidateTransitionV1`、`GrowthDecisionV1`；
- 实现 `SkillArtifactV1`、`SkillDeploymentV1`；
- 为前端 wire mirror 定义 `BodyCapabilityV1` 和 `ExpressionPlanV1` 的 Python 验证合同；
- 复用 L0 canonical hash/UTC/ID 校验，不复制第二套不兼容 helper；
- `DataRootLayout.from_runtime(runtime)` 只接受与 `runtime.data_root` 相同的绝对路径；
- 创建目录前拒绝数据根位于源码根、打包 runtime 根、相对路径、空路径和 symlink/reparse 跳转；
- 所有 list/map 递归冻结，unknown field、NaN/Infinity、超限字符串和非法枚举拒绝。

**测试：** exact field set、缺失/多余字段、canonical hash、递归不可变、路径逃逸、源码根回退、Windows 大小写和 junction/reparse 情形。

**提交门：**

```powershell
python -m pytest tests/test_l7_contracts.py tests/test_l7_layout.py -q
git diff --check
```

**Commit：** `feat(life): freeze l7 growth contracts`

### Task 2：先关闭 updater 与技能治理旁路

**文件：**

- Modify: `core/runtime.py`
- Modify: `core/skill_creator.py`
- Modify: `core/skill_manager.py`
- Modify: `core/auto_updater.py`
- Modify: `tests/test_p0_runtime.py`
- Test: `tests/test_l7_governance_boundary.py`

**实现：**

- 从 `_register_extension_tools()` 删除 `core.auto_updater` 的 Agent 工具注册；
- 不再注册 `skill_install`、`skill_enable`、`skill_uninstall`、`skill_create`、`skill_improve`、`skill_delete`、`skill_import`；
- 保留兼容类时，所有变更方法返回结构化 `legacy_governance_disabled`，不能写文件或改状态；
- SkillCreator 的生成/导入逻辑后续只通过 `SkillForge.create_candidate()`，本任务先 fail closed；
- SkillManager 不再扫描源码 `skills/*.py` 后全部标记 active；
- `auto_updater.pull_latest()`、原地 restore 和源码树 backup 不再可达生产路径；
- 删除 `ZipFile.extractall()` 使用。L8 原生更新尚未落地前，restore 明确返回 `native_continuity_required`，不能做不安全兼容恢复；
- read-only 版本号来自 release manifest，不执行 git 命令；
- 否定测试枚举 registry 中所有工具，证明模型没有 updater/install/activate/delete 变更面。

**测试：** 任意 source path、路径穿越文件名、后台 review、直接调用 legacy 方法、Agent tool 搜索、`extractall` 静态扫描、源码树文件快照前后不变。

**提交门：**

```powershell
python -m pytest tests/test_l7_governance_boundary.py tests/test_p0_runtime.py -q
rg -n 'extractall\(' core
git diff --check
```

`rg` 预期对生产实现零命中；测试 fixture/comment 命中必须人工说明。

**Commit：** `fix(security): close runtime growth bypasses`

### Task 3：实现 append-only RunStore

**文件：**

- Create: `core/life/l7/run_store.py`
- Test: `tests/test_l7_run_store.py`

**实现：**

- SQLite 位于 `JAVIS_DATA_ROOT/growth/runs/runs.sqlite3`；
- run header、transition、job checkpoint、receipt reference 分表保存；
- 对 `(run_id, idempotency_key)`、transition ID 和 job receipt ID 建唯一约束；
- 状态投影只来自合法 transition，terminal 不可重开；
- 使用 `BEGIN IMMEDIATE` + expected revision 做 CAS；
- `claim_active(identity_id, instance_id)` 保证同一实例最多一个非终态 run；
- 崩溃恢复把遗留 `running/checkpointing/committing` 投影为 `interrupted`，不改原事件；
- 查询返回有界列表，错误层不含 job 输入正文。

**测试：** 重放、乱序、重复调度、并发 claim、stale revision、崩溃、WAL 重启、损坏行、terminal 复活和慢 SQLite 不进入 EventBus 热路径。

**提交门：**

```powershell
python -m pytest tests/test_l7_run_store.py tests/test_life_journal.py -q
git diff --check
```

**Commit：** `feat(life): persist bounded sleep runs`

### Task 4：实现不可变 GrowthStore

**文件：**

- Create: `core/life/l7/growth_store.py`
- Modify: `core/skill_catalog.py`
- Test: `tests/test_l7_growth_store.py`
- Modify: `tests/test_skill_catalog.py`

**实现：**

- candidates 表禁止 UPDATE 正文，逻辑修改调用 `create_next_version()`；
- 保存 previous version/hash，并验证 version 单调连续；
- transition 与 decision append-only，approval 绑定 exact candidate/artifact/test receipt；
- head 指针使用 expected revision 原子更新，不删除旧版本；
- 将 SkillCatalog 从 name-only 当前行扩展为精确 `catalog_record_id + artifact/content_hash`；
- catalog 发现内容变化时创建新 candidate record，不覆盖 active 版本；
- 保留现有查询兼容投影，但所有执行查询必须指定精确版本；
- disabled/rejected/revoked 立即使部署 gate 失败。

**测试：** 批准后内容变化、同名多版本、旧批准复用、UPDATE 拒绝、并发 head、状态非法跳转、rejected 复活、catalog rediscovery 和 hash collision fail closed。

**提交门：**

```powershell
python -m pytest tests/test_l7_growth_store.py tests/test_skill_catalog.py -q
git diff --check
```

**Commit：** `feat(growth): version immutable candidates`

### Task 5：实现 SleepCoordinator 状态机和 job 预算

**文件：**

- Create: `core/life/l7/sleep.py`
- Test: `tests/test_l7_sleep_coordinator.py`

**实现：**

- 通过注入 clock、power probe、idle probe、health probe 和 stores 保持核心可测；
- 实现 manual/quiet_hours/idle 调度和 DST 单窗口幂等键；
- preflight 精确检查 policy revision、审批、AC、电量、CPU、磁盘、数据根、shutdown 与 active Action；
- job registry 仅允许规格列出的六类 kind；
- 每个 job 接收 cancellation token、record/byte/time 预算和 source checkpoint；
- network 默认为 deny；存在 explicit grant 也只能传给声明需要网络的 revalidation adapter，首版所有内置 job 均不需要网络；
- 用户活动、播放、Action 和 shutdown 触发有界取消；
- 每个 job 写 checkpoint/receipt 后才能推进 run；
- summary 和 candidate 只引用上游 evidence ID，不自行把模型输出升级为事实。

**测试：** 策略禁用、过期、AC、电量、资源、夏令时、重复 timer、用户唤醒、取消竞态、timeout、partial receipt、crash replay 和 shutdown。

**提交门：**

```powershell
python -m pytest tests/test_l7_sleep_coordinator.py tests/test_owned_runtime_shutdown.py -q
git diff --check
```

**Commit：** `feat(life): coordinate governed sleep runs`

### Task 6：组装 L7Supervisor 与上游权威适配器

**文件：**

- Create: `core/life/l7/supervisor.py`
- Create: `core/life/l7/legacy.py`
- Modify: `core/runtime.py`
- Modify: `core/life/service.py`
- Test: `tests/test_l7_supervisor.py`
- Test: `tests/test_l7_legacy_quarantine.py`

**实现：**

- `L7Supervisor.start(runtime)` 验证唯一 data root 并以 subsystem 注册；
- 只接收 L2-L6 经过 schema 验证的 receipt/checkpoint，不订阅原始通配 payload；
- 内存队列有界，落库与 job 执行在 worker，不阻塞 EventBus；
- supervisor 停止时先拒绝新 run，再取消当前 job，写 checkpoint，最后关闭 stores；
- 对 `kernel.training.sleep_learning.SleepLearning` 提供 quarantine adapter：只能读取其状态用于迁移诊断，不能 `start()`、`enter_sleep()` 或训练；
- `JavisKernel.start()` 不再自动启动 legacy sleep/training；若未来训练需要，必须成为新的 system-change candidate 并单独获批；
- legacy `brain_data/sleep_meta.json` 仅只读生成迁移提示，不把 `.consolidated` 当正式证据。

**测试：** runtime start/stop、双 supervisor、队列过载、错误隔离、上游假收据拒绝、legacy start 禁止、参数不变化、源码根不产生 marker。

**提交门：**

```powershell
python -m pytest tests/test_l7_supervisor.py tests/test_l7_legacy_quarantine.py tests/test_life_service.py -q
git diff --check
```

**Commit：** `feat(life): supervise sleep and growth`

### Task 7：实现 Windows 原生 SkillForge 沙箱

**文件：**

- Create: `app/src-tauri/src/skill_sandbox.rs`
- Modify: `app/src-tauri/src/main.rs`
- Modify: `app/src-tauri/Cargo.toml`，仅在确有需要且锁文件同步时
- Test: Rust module tests in `app/src-tauri/src/skill_sandbox.rs`
- Test: `tests/test_l7_skill_forge.py` 的 native contract fixtures

**实现：**

- 原生命令只接受 sealed sandbox plan 路径和 plan hash，不接受任意命令字符串；
- 解析 plan 后再次验证 run ID、工作目录、可执行文件 allowlist、参数、超时和输出上限；
- 工作目录必须是 `JAVIS_DATA_ROOT/growth/sandbox/<run_id>` 的非链接子目录；
- 使用受限 token、Windows Job Object、进程树 kill-on-close、CPU/内存/进程数限制；
- 清空敏感环境，仅传固定 locale、temp、artifact input/output；
- 防火墙/能力层明确拒绝网络，不能仅依赖“没有 URL”；
- 拒绝 symlink、junction、reparse point、hardlink 逃逸和 ADS；
- stdout/stderr 截断、脱敏，只返回 reason code、计数和摘要 hash；
- timeout/cancel 杀死整个 job tree 并验证无子进程残留；
- 非 Windows 或原生能力不可用返回 `sandbox_unavailable`。

**测试：** 路径穿越、junction、hardlink、ADS、网络连接、读取环境 token、写源码根、fork child、超时、输出洪泛和取消。

**提交门：**

```powershell
cargo test --manifest-path app/src-tauri/Cargo.toml skill_sandbox
python -m pytest tests/test_l7_skill_forge.py -q -k native_contract
git diff --check
```

**Commit：** `feat(native): add restricted skill forge sandbox`

### Task 8：实现 SkillForge intake、构建与不可变 artifact

**文件：**

- Create: `core/life/l7/skill_forge.py`
- Modify: `core/skill_creator.py`
- Modify: `core/skill_manager.py`
- Test: `tests/test_l7_skill_forge.py`

**实现：**

- intake 从用户原生选择的本地文件/目录或生成草稿复制到 data root，源路径不成为运行路径；
- 先枚举后复制，校验成员总数、总字节、单文件大小、大小写碰撞、Unicode 规范、链接/reparse、扩展名和完整 provenance manifest；
- 许可证不在 allowlist 时 quarantine；依赖必须锁定且不能在运行时下载；
- SkillCreator 适配为 `propose_generated_skill()`，只写 candidate draft；
- SkillManager 兼容导入适配为 `propose_imported_skill()`，不再 install/active；
- 调用原生 sandbox 执行静态检查、合同测试和受限行为测试；
- 生成 SBOM、capability diff、build/test receipts；
- artifact temp 写入、fsync、hash 后原子 rename 到 `artifacts/sha256/<prefix>/<hash>`；
- 已存在同 ID 必须逐字节一致，否则 quarantine；
- artifact 目录完成后只读，任何改进产生新 candidate/artifact。

**测试：** 源码安装旁路、生成直升 active、manifest 漏项、许可证、依赖漂移、zip bomb、大小写冲突、artifact 并发、同 ID 不同内容、沙箱失败和清理。

**提交门：**

```powershell
python -m pytest tests/test_l7_skill_forge.py tests/test_p0_runtime.py -q
git diff --check
```

**Commit：** `feat(growth): forge immutable skill artifacts`

### Task 9：实现部署指针和受治理 runtime loader

**文件：**

- Create: `core/life/l7/skill_deployment.py`
- Modify: `core/runtime.py`
- Modify: `core/skill_catalog.py`
- Test: `tests/test_l7_skill_deployment.py`
- Modify: `tests/test_skill_catalog.py`
- Modify: `tests/test_p0_runtime.py`

**实现：**

- `SkillDeploymentStore.activate()` 验证 exact candidate、artifact、approval、evaluation、catalog active、capability grants 和兼容范围；
- 写 immutable deployment record，再以 expected revision 原子切换 active pointer；
- pointer 回读并重新 hash 后才写 candidate `active` transition；
- rollback 只能指向已验证 previous deployment，写新 deployment/receipt，不倒改旧记录；
- runtime loader 不再 `import skills.<id>`；从 artifact 创建隔离 module namespace，并在每次 load 前复核 pointer、catalog 和 hash；
- `discover_skills()` 静态读取 release manifest 与 deployment index，不 import candidate；
- release 内置技能通过受信 release manifest 初始化 exact artifact/deployment；
- always-on 安全工具不由用户技能覆盖，名称冲突拒绝；
- disable/revoke 在下一次工具解析前撤出，不等待进程重启。

**测试：** catalog disabled、hash 篡改、approval 版本错配、grant 过期、stale pointer、并发 activate、rollback、内置迁移、module cache 污染和 always-on 覆盖。

**提交门：**

```powershell
python -m pytest tests/test_l7_skill_deployment.py tests/test_skill_catalog.py tests/test_p0_runtime.py -q
git diff --check
```

**Commit：** `feat(runtime): load only governed deployments`

### Task 10：接入 L7 API、事件与用户审阅面

**文件：**

- Create: `core/life/l7/api.py`
- Modify: `main.py`
- Create: `app/src/growth/growthTypes.ts`
- Create: `app/src/growth/GrowthClient.ts`
- Create: `app/src/growth/GrowthSettings.ts`
- Modify: `app/src/bridge/backendClient.ts`
- Modify: `app/src/settings/SettingsSurface.ts`
- Test: `tests/test_l7_api.py`
- Test: `app/tests/growthSettings.test.ts`

**实现：**

- 精确实现规格 API，所有命令要求 runtime capability、server-derived subject 和 expected revision；
- candidate decision 只接受 `approve|reject|revoke`，批准必须回显 candidate/artifact hash；
- 不向 Agent registry 暴露批准、部署、回滚或 policy 修改工具；
- API 列表分页、正文有界，默认不返回源代码、完整 evidence 或路径；
- EventBus 仅发 ID/status/reason/revision/hash；
- 设置面展示 sleep policy/run、candidate 风险/权限/测试/回滚和部署状态；
- 用户拒绝后不重复提示；pending candidate 使用摘要计数，不打断当前会话；
- UI 对 stale revision 刷新，不做 last-write-wins。

**测试：** subject spoof、无 scope、过期 token、stale revision、secret/path redaction、分页、重复 decision、模型工具列表和 UI 失败状态。

**提交门：**

```powershell
python -m pytest tests/test_l7_api.py tests/test_runtime_access.py -q
npm --prefix app test
git diff --check
```

**Commit：** `feat(app): review sleep and growth decisions`

### Task 11：冻结 Body wire contract 和 ExpressionPlanner

**文件：**

- Create: `app/src/pet/body/bodyTypes.ts`
- Create: `app/src/pet/body/BodyCapability.ts`
- Create: `app/src/pet/body/ExpressionPlanner.ts`
- Modify: `app/src/life/lifeTypes.ts`
- Test: `app/tests/bodyCapability.test.ts`
- Test: `app/tests/expressionPlanner.test.ts`
- Modify: `tests/test_l7_release_contract.py`

**实现：**

- TypeScript exact mirror `BodyCapabilityV1`、`ExpressionPlanV1`；
- strict parser 拒绝 unknown field、过期 plan、超限 track/keyframe 和不支持枚举；
- planner 是纯函数，输入 ExpressionIntent、capability、playback timing、preferences 和 injected now；
- request ID、playback generation、source revision 与 expires_at 全部传播；
- phoneme/word/energy timing 诚实降级；
- reduced motion 替代和 unsupported channel fallback 确定性；
- expression profile 只提供有界节奏参数，安全/interrupt 优先级不可覆盖。

**测试：** Python/TypeScript 字段一致、stale intent、generation、未知 viseme、关键帧预算、reduced motion、timing 降级和 deterministic output。

**提交门：**

```powershell
python -m pytest tests/test_l7_release_contract.py -q
npm --prefix app test
git diff --check
```

**Commit：** `feat(body): freeze expression plans`

### Task 12：实现 BodyRuntime 单写者

**文件：**

- Create: `app/src/pet/body/BodyRuntime.ts`
- Modify: `app/src/pet/avatar/AvatarController.ts`
- Modify: `app/src/pet/avatar/AvatarSurface.ts`
- Modify: `app/src/pet/avatar/SpeakingEnvelope.ts`
- Test: `app/tests/bodyRuntime.test.ts`
- Modify: `app/tests/avatarController.test.ts`
- Modify: `app/tests/avatarSurface.test.ts`
- Modify: `app/tests/speakingEnvelope.test.ts`

**实现：**

- BodyRuntime 拥有 planner、AvatarController、当前 sink 和 playback generation；
- 同一时刻只有一个 writer，旧 plan/revision/generation 丢弃；
- `applyExpressionIntent()`、`onPlaybackStarted()`、`onPlaybackTiming()`、`onPlaybackStopped()`、`interrupt()`、`setPreferences()`、`dispose()` 幂等；
- plan 过期和无输入回到 neutral/quiet，不停在 speaking；
- WebGL context loss 切 fallback，恢复时不重放过期计划；
- dispose 清除 RAF、listener、Three resources 和 speaking envelope；
- diagnostics 只含 capability、tier、fallback reason、revision 和延迟。

**测试：** 单写者、stale revision、barge-in、stop 乱序、context loss/recovery、double dispose、RAF 泄漏、fallback 和 reduced motion 动态切换。

**提交门：**

```powershell
npm --prefix app test
npm --prefix app run build
git diff --check
```

**Commit：** `feat(body): arbitrate the production body runtime`

### Task 13：验收并由 BodyRuntime 收口 PetSurface 现有 Avatar 接线

**文件：**

- Modify: `app/src/pet/PetSurface.ts`
- Modify: `app/src/pet/petTypes.ts`
- Modify: `app/src/pet/petPreferences.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/styles.css`
- Modify: `app/src/bridge/backendClient.ts`
- Test: `app/tests/petBodyIntegration.test.ts`
- Modify: `app/tests/avatarPetIntegration.test.ts`，若该前序测试已存在
- Modify: `app/tests/voiceBargeIn.test.ts`
- Modify: `app/tests/petPreferences.test.ts`
- Modify: `app/tests/petWindowLayout.test.ts`

**实现：**

- 先确认前序 B0/B1 已在 PetSurface 创建 body host、挂载 AvatarSurface、处理性能治理/context recovery；已有实现和测试必须保留，不重复创建第二个 host/controller；
- 将 PetSurface 内现有 AvatarSurface、AvatarController、SpeakingEnvelope 和 fallback ownership 逐步移交给 BodyRuntime，PetSurface 只保留产品容器、用户交互和 skin 选择；
- PetSurface 通过单一 BodyRuntime 异步启动身体；
- 初始化期间和失败时 2D sprite 立即可用，不显示空 canvas；
- LifeStateBridge 的 ExpressionIntent 进入 BodyRuntime，不再平行直接改多个 surface；
- native playback 生命周期传 request/generation，能量/词/音素 timing 只用于对应 request；
- barge-in 先 stop playback generation，再清 BodyRuntime speaking channels；
- 皮肤选择映射到已验证 BodyCapability；未验证 manifest 只可作为 2D fallback；
- reduced motion 和 body selection 从后端 data-root preferences 投影，localStorage 仅做一次 legacy import；
- 拖动、缩放、context menu、open Live 和窗口边界回归。

**测试：** production import/construct 证据、3D first frame、失败 fallback、非空 host、语音打断、旧 generation、窗口缩放和 localStorage migration once。

**提交门：**

```powershell
npm --prefix app test
npm --prefix app run build
git diff --check
```

**Commit：** `feat(app): connect the living body to pet mode`

### Task 14：统一 L7 数据根、恢复与发布合同

**文件：**

- Modify: `core/runtime.py`
- Modify: `kernel/training/sleep_learning.py`
- Modify: `utils/config_api.py`，仅添加 data-root 注入接口，不在 L7 执行全量迁移
- Modify: `app/src-tauri/src/sidecar.rs`
- Modify: `app/src-tauri/src/runtime_bundle.rs`
- Create: `tests/test_l7_release_contract.py`
- Modify: `tests/test_life_paths.py`
- Modify: `tests/test_owned_runtime_shutdown.py`
- Modify: `tests/test_app_v3_integrated_release.py`

**实现：**

- 原生层解析一次 canonical `JAVIS_DATA_ROOT`，sidecar、L7、body preferences 和未来 continuity 使用同一路径；
- 区分 immutable packaged runtime 与 mutable user data；runtime bundle 不再把旧 `brain_data/data/workspace/logs` 复制进新代码槽；
- L7 启动 path audit，发现任意 L7 写目标在源码/runtime 下即 fail closed；
- shutdown 顺序固定：停止接收 run -> cancel/checkpoint -> stop body listeners -> close stores -> Life clean checkpoint -> sidecar exit；
- source tree snapshot 测试覆盖启动、手动 sleep、candidate、forge failure 和 shutdown；
- release manifest 声明 L7 schema、artifact layout、native sandbox 能力和 fallback；
- 不在 L7 静默移动旧数据，仅产生 L8 可消费的 legacy inventory。

**测试：** `JAVIS_DATA_ROOT` 环境/显式优先级、app data/local data 不一致、只读源码树、bundle replacement、三次重启、异常关闭和 legacy inventory 幂等。

**提交门：**

```powershell
python -m pytest tests/test_l7_release_contract.py tests/test_life_paths.py tests/test_owned_runtime_shutdown.py tests/test_app_v3_integrated_release.py -q
cargo test --manifest-path app/src-tauri/Cargo.toml
git diff --check
```

**Commit：** `fix(runtime): root all l7 state in user data`

### Task 15：端到端证据、回归和阶段出口

**文件：**

- Create: `scripts/verify_javis_l7.py`
- Create: `tests/test_l7_end_to_end.py`
- Modify: `scripts/javis_release_gate.py`
- Modify: `scripts/app_package_manifest.py`
- Create: `docs/superpowers/evidence/<date>-javis-l7-evidence.md`，只保存脱敏证据索引，不能回写本设计/计划裁决

**实现：**

- 构造两次重启和一次中断的 sleep 场景，验证 run/receipt/candidate 幂等；
- 构造生成技能 -> sandbox fail -> 新版本 -> validate -> 用户批准 -> deploy -> tamper -> rollback；
- 构造 3D start -> speaking timing -> barge-in -> WebGL loss -> 2D fallback；
- 验证 updater/install/create/load 四类旁路均被拒绝；
- 验证源码树 hash 前后不变，所有 app-owned mutable 文件位于 data root；
- 记录 exact git SHA、构建命令、产物 SHA-256、自动化结果和未执行项；
- D 盘真实验收必须从 exact commit 构建：跨夜策略、AC/电池、用户唤醒、候选审阅、技能回滚、低配/reduced motion、安装重启；
- D 盘未执行前阶段状态保持 `AUTOMATED COMPLETE / REAL USER NOT EXECUTED`。

**提交门：**

```powershell
python -m pytest -q
npm --prefix app test
npm --prefix app run build
cargo test --manifest-path app/src-tauri/Cargo.toml
python scripts/verify_javis_l7.py
git diff --check
git status --short
```

`git status --short` 只允许本阶段声明文件和证据；任何运行时数据、沙箱、数据库、模型或构建缓存进入工作树都阻止提交。

**Commit：** `test(life): seal l7 sleep growth evidence`

## 5. 提交序列与合入门

建议严格按 Task 1-15 每任务一个提交。每个提交共同要求：

1. 失败测试先出现并能证明风险；
2. `git diff --check` 通过；
3. 定向测试通过；
4. 不包含 runtime data、模型、artifact payload、sandbox 或构建缓存；
5. 不修改 L2-L6 权威合同来迁就 L7 fake；
6. 提交说明写明安全降级和回滚方式；
7. 修改 shared file 前确认前序阶段已合入，并保留他人并行变更。

合入主线前再执行全量门。任何一项失败都不得用“仅文档/仅 UI/测试环境特例”跳过。

## 6. 规格覆盖矩阵

| 规格要求 | 实施任务 |
|---|---|
| L7Supervisor/SleepCoordinator/RunStore/GrowthStore | 1, 3, 4, 5, 6 |
| SleepPolicy/Run 合同与状态机 | 1, 3, 5 |
| 不可变 GrowthCandidate 版本 | 1, 4 |
| SkillForge 不可变产物与沙箱 | 7, 8 |
| SkillArtifact/Deployment 指针 | 1, 8, 9 |
| SkillCreator/Manager/loader/catalog 绕过 | 2, 4, 8, 9 |
| updater 和 extractall 风险 | 2, 14 |
| sleep_learning 风险 | 5, 6, 14 |
| 所有可变状态位于 JAVIS_DATA_ROOT | 1, 3, 4, 8, 14, 15 |
| ExpressionPlan/BodyCapability | 1, 11 |
| BodyRuntime 与产品接线 | 12, 13 |
| 用户审阅、拒绝、批准、回滚 | 9, 10 |
| 自动化和真实体验出口 | 15 |

## 7. 阶段出口

只有 Task 1-15 全部通过，且 L7 正式规格第 19 节十二项出口标准均有证据，才能标记 L7 完成。以下结果不算完成：

- 只有后台类，没有 runtime/API/UI 接线；
- candidate 可审阅但 artifact 仍从源码路径执行；
- sandbox 单测通过但生产退化为普通 subprocess；
- Avatar 模块有单测但 PetSurface 未构造；
- 自动测试通过但 D 盘跨夜、打断和回滚未执行；
- 关闭一条旁路但保留另一条 source install、direct ACTIVE 或 catalog bypass；
- 把旧数据复制到新 runtime 目录后声称数据根统一。
