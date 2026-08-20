# Javis L8 长期连续性实施计划

> **执行规则：** 每个任务先写失败测试，再做最小实现；迁移、更新、同步和终止的每个提交都必须能独立回滚。本计划不授权在文档编写阶段修改产品源码、删除数据、构建发布包或提交 Git。

**日期：** 2026-08-20
**状态：** 待实施
**批准规格：** `docs/superpowers/specs/2026-08-20-javis-l8-continuity-design.md`
**记录基线：** `e5de6265`，实施开始时必须重新记录实际基线
**建议分支：** `codex/javis-life-os-l8-continuity-20260820`
**建议隔离目录：** `G:\Javis-worktrees\javis-life-os-l8-continuity-20260820`

## 1. 全局约束

- L8 从 L0-L7 全部合同和自动化出口通过的 exact commit 开始；缺失前序服务时不得用复制目录或 mock 数据替代生产迁移；
- `G:\Javis` 是正式源码集成区，不是用户数据根、update staging、sync inbox 或 termination temp；
- 所有测试使用 `tmp_path`/临时 Tauri app data，并显式注入 `JAVIS_DATA_ROOT`；
- 任何真实用户数据删除、D 盘迁移、设备撤销和终止都必须在自动化门通过后单独获得用户批准；
- production updater 不执行 git、shell 脚本、任意 URL、原地覆盖或 `extractall()`；
- private signing key、真实 API key、真实 device key 和未脱敏 backup 不进入仓库、fixture 或日志；
- 普通卸载继续默认保留数据；终止是独立原生流程；
- 用户工具输出和授权 workspace 不属于 app-owned state，但 continuity 不能隐藏写入这些路径；
- 本计划中的 `Commit` 仅定义未来提交边界，本次文档工作不执行提交。

## 2. 冻结文件结构

### 新建后端文件

- `core/life/l8/__init__.py`
- `core/life/l8/contracts.py`
- `core/life/l8/layout.py`
- `core/life/l8/store.py`
- `core/life/l8/manifest.py`
- `core/life/l8/lineage_graph.py`
- `core/life/l8/migration.py`
- `core/life/l8/sync.py`
- `core/life/l8/termination.py`
- `core/life/l8/coordinator.py`
- `core/life/l8/api.py`

### 新建原生文件

- `app/src-tauri/src/continuity.rs`
- `app/src-tauri/src/continuity_archive.rs`
- `app/src-tauri/src/continuity_migration.rs`
- `app/src-tauri/src/continuity_crypto.rs`
- `app/src-tauri/src/continuity_termination.rs`
- `app/src-tauri/resources/javis-release-public-key.pem`

### 新建前端文件

- `app/src/continuity/continuityTypes.ts`
- `app/src/continuity/ContinuityClient.ts`
- `app/src/continuity/ContinuitySettings.ts`
- `app/src/continuity/LineageView.ts`
- `app/src/continuity/TerminationDialog.ts`

### 主要修改文件

- `core/life/lineage.py`
- `core/life/service.py`
- `core/life/paths.py`
- `core/runtime.py`
- `core/auto_updater.py`
- `main.py`
- `utils/config_api.py`
- `utils/model_installer.py`
- `knowledge/brain.py`
- `knowledge/papers_db.py`
- `memory/episodic.py`
- `memory/semantic.py`
- `memory/procedural.py`
- `memory/indexer.py`
- `memory/session_db.py`
- `memory/procedural_materializer.py`
- `core/memory_kernel.py`
- `core/skill_creator.py`
- `core/skill_manager.py`
- `kernel/kernel.py`
- `kernel/training/sleep_learning.py`
- `tools/javis_daemon.py`
- `tools/javis_vision.py`
- `tools/vision_engine.py`
- `tools/workflow_templates.py`
- `tools/cron_scheduler.py`
- `core/workspace_manager.py`
- `utils/memory.py`
- `app/src-tauri/src/main.rs`
- `app/src-tauri/src/sidecar.rs`
- `app/src-tauri/src/runtime_bundle.rs`
- `app/src-tauri/src/bundled_ollama.rs`
- `app/src-tauri/Cargo.toml`
- `app/src-tauri/Cargo.lock`
- `app/src/settings/SettingsSurface.ts`
- `app/src/bridge/backendClient.ts`
- `app/src/main.ts`
- `app/src-tauri/windows/nsis-hooks.nsh`

### 新建测试和验证文件

- `tests/test_l8_contracts.py`
- `tests/test_l8_layout.py`
- `tests/test_l8_mutable_inventory.py`
- `tests/test_l8_manifest.py`
- `tests/test_l8_lineage_graph.py`
- `tests/test_l8_lineage_migration.py`
- `tests/test_l8_migration.py`
- `tests/test_l8_sync.py`
- `tests/test_l8_sync_domain_integration.py`
- `tests/test_l8_coordinator.py`
- `tests/test_l8_api.py`
- `tests/test_l8_termination.py`
- `tests/test_l8_release_contract.py`
- `tests/test_l8_end_to_end.py`
- `app/tests/continuitySettings.test.ts`
- `app/tests/lineageView.test.ts`
- `app/tests/terminationDialog.test.ts`
- `scripts/verify_javis_l8.py`
- `scripts/build_continuity_fixture.py`

## 3. 依赖顺序

1. strict contracts 和 canonical data-root layout 先落地；
2. 全仓 mutable inventory 和 legacy migration 先于正式 manifest；
3. ContinuityManifest 先于 lineage migration、data migration、sync 和 termination；
4. LineageGraphStore 先于 copy/sync/merge；
5. 原生 archive verifier 先于任何 update activation；
6. native migration journal 先于 coordinator 调用；
7. crypto/key store 先于 SyncEnvelope；
8. TerminationPlanner 先于 native delete executor；
9. API/UI 只能接已验证 coordinator，不得直接拼原生命令参数；
10. 最后执行 fault injection、全量回归、安装包和 D 盘真实验收。

## 4. 工作包

### Task 1：冻结 L8 wire contracts

**文件：**

- Create: `core/life/l8/__init__.py`
- Create: `core/life/l8/contracts.py`
- Test: `tests/test_l8_contracts.py`

**实现：**

- 精确实现 `ContinuityManifestV1`、`ContinuityEntryV1`、`MigrationReceiptV1`、`MigrationPhaseReceiptV1`；
- 实现 `LineageOpV1`、`BranchHeadV1`、`BranchSyncRunV1`、`SyncEnvelopeV1`、`SyncConflictV1`；
- 实现 `TerminationPlanV1`、`TerminationReceiptV1`；
- 定义固定 canonical JSON/CBOR profile、hash 和 signature input；
- 所有相对路径使用 `/` wire separator，拒绝绝对路径、drive、UNC、`..`、NUL、ADS 和 Windows reserved name；
- strict missing/extra、bounded count/bytes/nesting、finite number、UTC、ID、hash、signature algorithm 校验；
- TerminationReceipt 递归 forbidden-field scan，确保 content/path/secret 不进入；
- 生成跨 Python/Rust/TypeScript 的 golden fixtures，fixture 只含合成 ID 和内容 hash。

**测试：** 字段全集、unknown/missing、canonical bytes、signature vector、path corpus、超限 envelope、receipt content-free、schema future version 和 hash tamper。

**提交门：**

```powershell
python -m pytest tests/test_l8_contracts.py -q
git diff --check
```

**Commit：** `feat(continuity): freeze l8 wire contracts`

### Task 2：统一 native launcher 与 Python 的 canonical JAVIS_DATA_ROOT

**文件：**

- Create: `core/life/l8/layout.py`
- Modify: `core/life/paths.py`
- Modify: `core/runtime.py`
- Modify: `app/src-tauri/src/sidecar.rs`
- Modify: `app/src-tauri/src/runtime_bundle.rs`
- Modify: `app/src-tauri/src/bundled_ollama.rs`
- Test: `tests/test_l8_layout.py`
- Modify: `tests/test_life_paths.py`
- Modify: `tests/test_owned_runtime_shutdown.py`

**实现：**

- native launcher 每次启动解析一个绝对 canonical root，向 sidecar 只注入 `JAVIS_DATA_ROOT`；
- 删除 `JAVIS_APP_DATA_ROOT` 与 app_data/app_local_data/runtime-data 三套状态根语义；
- packaged runtime slot 与 data root 明确分离，runtime slot 只含签名代码；
- `ContinuityLayout` 定义 specs 第 5.3 节目录并返回 typed paths；
- Python store 构造必须显式接收 runtime.data_root/layout，禁止模块 import 时固化 cwd/source paths；
- root ID 为规范化 root + local salt 的 hash，不把绝对路径写 wire/log；
- 拒绝 root 位于 runtime/source、root 自身 reparse、非绝对、不可写或与 LifeService 不一致；
- local model 外部目录只通过 data-root pointer/receipt 引用。

**测试：** env/explicit/native 一致、大小写、盘符、UNC policy、symlink/junction、app data/local data 差异、只读源码树、packaged runtime replacement 和三次重启。

**提交门：**

```powershell
python -m pytest tests/test_l8_layout.py tests/test_life_paths.py tests/test_owned_runtime_shutdown.py -q
cargo test --manifest-path app/src-tauri/Cargo.toml continuity_root
git diff --check
```

**Commit：** `fix(runtime): establish one canonical data root`

### Task 3：建立全仓 mutable inventory 并停止源码根写入

**文件：**

- Create: `tests/test_l8_mutable_inventory.py`
- Modify: `utils/config_api.py`
- Modify: `knowledge/brain.py`
- Modify: `knowledge/papers_db.py`
- Modify: `memory/episodic.py`
- Modify: `memory/semantic.py`
- Modify: `memory/procedural.py`
- Modify: `memory/indexer.py`
- Modify: `memory/session_db.py`
- Modify: `memory/procedural_materializer.py`
- Modify: `core/memory_kernel.py`
- Modify: `core/skill_creator.py`
- Modify: `core/skill_manager.py`
- Modify: `core/auto_updater.py`
- Modify: `kernel/kernel.py`
- Modify: `kernel/training/sleep_learning.py`
- Modify: `tools/javis_daemon.py`
- Modify: `tools/javis_vision.py`
- Modify: `tools/vision_engine.py`
- Modify: `tools/workflow_templates.py`
- Modify: `tools/cron_scheduler.py`
- Modify: `core/workspace_manager.py`
- Modify: `utils/memory.py`
- Modify: `utils/model_installer.py`

**实现：**

- 生成并版本化 app-owned mutable inventory：owner service、logical domain、legacy paths、new typed path、required/derived、secret、migration handler；
- 将模块级 `Path(.../brain_data|data|config.yaml)` 改为构造注入或 runtime layout；
- 用户授权 workspace 输出明确标记 user-owned，不纳入 app state；workspace 内部 manifest 若是 app-owned 则迁到 data root；
- config 主文件迁到 `JAVIS_DATA_ROOT/config/config.yaml`，secret 改为 protected key ref；
- updater log/backup、skill market/review/generated、sleep meta、cron state、daemon state 全部停写源码树；
- runtime bundle 不再调用 `migrate_persistent_state()` 把状态复制进 incoming code slot；
- 兼容读取旧路径只由 migration contributor 使用，正常服务不得 dual write；
- 测试运行完整启动/会话/sleep/skill/model/status/shutdown 后比较 source/runtime tree hash 不变；
- 静态 AST test 拒绝新增 app-owned 模块相对写路径，允许列表必须按 owner 审核。

**测试：** source tree read-only、cwd 改变、packaged root read-only、legacy only、new only、dual presence、secret migration hint、workspace distinction 和 app-owned inventory 完整性。

**提交门：**

```powershell
python -m pytest tests/test_l8_mutable_inventory.py tests/test_life_paths.py tests/test_model_installer.py -q
rg -n 'Path\("brain_data|parent\.parent / "brain_data|parent\.parent / "data|CONFIG_PATH = .*config\.yaml' core kernel knowledge memory tools utils
git diff --check
```

`rg` 的生产写路径预期零命中；文档字符串或明确 legacy contributor 命中必须进入 allowlist test。

**Commit：** `refactor(storage): root mutable state under javis data`

### Task 4：实现 ContinuityManifest contributor 与 verifier

**文件：**

- Create: `core/life/l8/manifest.py`
- Create: `core/life/l8/store.py`
- Modify: `core/life/service.py`
- Test: `tests/test_l8_manifest.py`

**实现：**

- `ManifestContributor` protocol：domain、schema、quiesce/checkpoint、entries、external refs、tombstone high-water、verify；
- 为 Life、conversation、L2 memory、L3 relationship、L4 environment、L5 intent、L6 action、L7 growth/body、config/model refs 注册 contributor；
- coordinator 先 quiesce 写入，再执行 SQLite WAL checkpoint，再枚举；
- 文件枚举使用 root handle/relative path，不跟随链接/reparse；
- verifier 检查逐 entry hash、SQLite integrity、record count、schema、引用闭包、derivation graph、identity/branch 和 key refs；
- optional derived index 必须标 skipped/rebuilt，不能从 manifest 消失；
- manifest immutable、hash-chained、签封，store 位于 `continuity/manifests`；
- manifest generation 失败释放 quiesce lease，不留下半 manifest。

**测试：** contributor 缺席、WAL 未 checkpoint、hash tamper、dangling derivation、missing tombstone、unknown domain、symlink/reparse、并发写、optional rebuild 和 manifest replay。

**提交门：**

```powershell
python -m pytest tests/test_l8_manifest.py tests/test_life_journal.py tests/test_unified_conversation_integration.py -q
git diff --check
```

**Commit：** `feat(continuity): build verifiable state manifests`

### Task 5：把线性 InstanceLineageStore 迁移为 LineageGraphStore

**文件：**

- Create: `core/life/l8/lineage_graph.py`
- Modify: `core/life/lineage.py`
- Modify: `core/life/contracts.py`
- Modify: `core/life/service.py`
- Test: `tests/test_l8_lineage_graph.py`
- Test: `tests/test_l8_lineage_migration.py`
- Modify: `tests/test_life_lineage.py`
- Modify: `tests/test_life_contracts.py`

**实现：**

- append-only op store，按 op_id/content hash/signature 唯一；
- BranchHead CAS 指向 verified op，heads 可从 DAG 重建；
- 实现 birth/restart/upgrade/runtime_switch/model_switch/data_root_move/device_migrate/restore_in_place/copy/sync/merge/revoke/terminate op validation；
- 断言只有 copy 可创建新 branch ID；其他 op 出现新 branch 直接拒绝；
- device_migrate 可创建新 instance 并链接 predecessor，不创建 branch；
- merge 要求两个 parent heads 和领域 sync receipts，target branch ID 保持；
- 旧线性 records 转为 root birth + restart/environment history op，创建一个 branch；
- 迁移采用 dry-run -> graph verify -> active head CAS -> receipt，失败保留旧 active pointer；
- 兼容 LifeSnapshot 暂时投影旧 lineage fields，并新增 branch summary；
- `fork_pending_review` 映射为 `untrusted_copy_detected`，必须用户选择后才能写 copy/migration op。

**测试：** 多 child、DAG cycle、缺 parent、双 head、head CAS、upgrade/model switch 不分支、copy 分支、device migration、merge 双 parent、线性迁移、迁移崩溃和重放。

**提交门：**

```powershell
python -m pytest tests/test_l8_lineage_graph.py tests/test_l8_lineage_migration.py tests/test_life_lineage.py tests/test_life_service.py -q
git diff --check
```

**Commit：** `feat(life): represent explicit continuity branches`

### Task 6：实现签名 release 验证和安全逐项解包

**文件：**

- Create: `app/src-tauri/src/continuity_archive.rs`
- Create: `app/src-tauri/resources/javis-release-public-key.pem`
- Modify: `app/src-tauri/src/runtime_bundle.rs`
- Modify: `app/src-tauri/src/main.rs`
- Modify: `app/src-tauri/Cargo.toml`
- Modify: `app/src-tauri/Cargo.lock`
- Modify: `core/auto_updater.py`
- Modify: `scripts/stage_javis_runtime.py`
- Modify: `scripts/app_package_manifest.py`
- Modify: `scripts/finalize_javis_release.py`
- Modify: `scripts/javis_release_gate.py`
- Test: Rust module tests in `continuity_archive.rs`
- Test: `tests/test_l8_release_contract.py`
- Modify: `tests/test_app_v3_integrated_release.py`

**实现：**

- 定义 versioned signed release manifest：release ID/version、publisher key ID、runtime archive hash/size、tree entries、schema compatibility、minimum launcher、signature；
- 仓库只保存 public key 和合成 test key fixture，private release key 由发布环境注入；
- 下载或本地导入先写 data-root continuity staging，流式限制 bytes/time；
- 验证 publisher signature 后再接受 archive；
- 预枚举所有 member，拒绝绝对/parent/UNC/drive/ADS/reserved/trailing/case collision、link/reparse、重复、bomb 和声明不一致；
- 仅 regular file/declared directory 逐项写 fresh slot，使用 create-new/no-follow 语义；
- 解包后按 signed tree 重新 hash，再执行 import probe；
- runtime slot immutable，active/previous 指针由 native journal CAS 切换；
- `core/auto_updater.py` 只保留兼容只读状态 façade，不执行 network/git/backup/restore；
- 全仓生产代码 `extractall()` 零命中。

**测试：** bad signature、hash、truncated archive、`..`、absolute、UNC、ADS、case collision、reserved name、symlink、zip bomb、extra/missing file、old launcher、slot tamper、switch crash 和 previous rollback。

**提交门：**

```powershell
cargo test --manifest-path app/src-tauri/Cargo.toml continuity_archive
python -m pytest tests/test_l8_release_contract.py tests/test_app_v3_integrated_release.py -q
rg -n "extractall\(|git pull|subprocess.*git" core app/src-tauri/src
git diff --check
```

生产实现预期无 `extractall` 和 git update 命中。

**Commit：** `feat(native): verify and stage signed releases`

### Task 7：实现 migration engine 与 native activation journal

**文件：**

- Create: `core/life/l8/migration.py`
- Create: `app/src-tauri/src/continuity_migration.rs`
- Create: `app/src-tauri/src/continuity.rs`
- Modify: `app/src-tauri/src/main.rs`
- Test: `tests/test_l8_migration.py`
- Test: Rust module tests in `continuity_migration.rs`

**实现：**

- Python `MigrationEngine` 驱动 planned/preparing/prepared/copying/copied/verifying/ready_to_activate；
- 生成 sealed native plan，包含 operation/manifest/root IDs、relative entry hashes、target handle token、expected pointer revision 和 rollback target；
- native 重新加载 plan/hash，验证 challenge 和 root handles，不接受 frontend 任意 path；
- copy 到空 staging root，每 entry 使用 temp/fsync/rename，更新 append-only phase journal；
- schema migration 在 copy 后、activate 前执行，migration ID/old/new hash 写 receipt；
- verify 通过后 native CAS pointer，写 pointer before/after 和 activation journal；
- 新 runtime 启动后 coordinator 写 checkpoint，才能清理 previous；
- 每个故障注入点可判定 old/new/staging 三者，不出现双 active；
- data_root_move 和 device_migrate 不创建 branch；source 继续写入时拒绝 activate；
- retry 新 operation 引用旧 receipt。

**测试：** 每阶段 crash、磁盘满、权限、锁文件、WAL、hash mismatch、schema failure、pointer stale、source changed、rollback failure、idempotent retry 和 no-branch assertions。

**提交门：**

```powershell
python -m pytest tests/test_l8_migration.py -q
cargo test --manifest-path app/src-tauri/Cargo.toml continuity_migration
git diff --check
```

**Commit：** `feat(continuity): migrate with native rollback journals`

### Task 8：组装 ContinuityCoordinator 与升级/模型语义

**文件：**

- Create: `core/life/l8/coordinator.py`
- Modify: `core/runtime.py`
- Modify: `core/life/service.py`
- Modify: `main.py`
- Modify: `utils/config_api.py`
- Test: `tests/test_l8_coordinator.py`

**实现：**

- coordinator 注册为 runtime subsystem，验证 data root、stores、lineage head 和 native inbox；
- 同一 identity 只允许一个 destructive operation lease；read-only manifest/export 可并发但绑定同一 checkpoint；
- 启动时导入 native journal：验证 plan/receipt/pointer/hash 后才推进状态；
- journal 缺失/矛盾进入 `recovering_read_only`，LifeService 不创建新 identity；
- upgrade orchestration：manifest -> native stage/switch -> post-start verify -> checkpoint/rollback；
- model settings 每次 exact 变化追加 model_switch op，但 identity/lineage/branch/instance 不变；
- UI surface switch 和 body switch不写 lineage op；
- runtime status 暴露 bounded continuity summary；
- close 顺序在 Life clean checkpoint 前停止新 continuity operation并完成有界 journal flush。

**测试：** operation lease、启动 inbox、bad native receipt、upgrade no branch、model no branch、多次 model switch、rollback、read-only recovery、shutdown race 和 subsystem failure isolation。

**提交门：**

```powershell
python -m pytest tests/test_l8_coordinator.py tests/test_life_service.py tests/test_model_connection_settings.py tests/test_owned_runtime_shutdown.py -q
git diff --check
```

**Commit：** `feat(life): coordinate long-term continuity`

### Task 9：实现 device key、SyncEnvelope 加密与反重放

**文件：**

- Create: `app/src-tauri/src/continuity_crypto.rs`
- Modify: `app/src-tauri/src/continuity.rs`
- Modify: `app/src-tauri/Cargo.toml`
- Modify: `app/src-tauri/Cargo.lock`
- Create: `core/life/l8/sync.py`
- Test: Rust module tests in `continuity_crypto.rs`
- Test: `tests/test_l8_sync.py`

**实现：**

- 选择并冻结 audited versioned suite：device signing、key agreement、AEAD、KDF；算法 ID 写合同；
- 每设备生成独立 key，private material 经 Windows DPAPI 包装后写 `continuity/keys`；
- pairing 显示短 fingerprint 并绑定 identity/lineage/allowed domains；
- envelope header 最小化，payload 加密并签名；
- sender sequence、envelope ID、expiry、recipient、identity/lineage、key epoch 在解密前验证；
- nonce 唯一，key rotation 和 device revoke 有测试；
- secret/token/API key/temporary grant 字段在加密前递归拒绝，不以“已加密”为豁免；
- import 先由 native picker 复制到 inbox，再由 Python verifier 解析，不执行 payload；
- export 只通过 native save handle，不接受任意字符串路径；
- crypto failure 只记录 reason code/ID hash。

**测试：** known vectors、nonce reuse、wrong recipient、bad signature/tag、expired、replay、sequence rollback、revoked key、old epoch、secret field、oversize payload 和 corrupt key wrapper。

**提交门：**

```powershell
cargo test --manifest-path app/src-tauri/Cargo.toml continuity_crypto
python -m pytest tests/test_l8_sync.py -q -k "envelope or replay or key"
git diff --check
```

**Commit：** `feat(sync): encrypt and authenticate branch envelopes`

### Task 10：实现 BranchSyncEngine、领域合并和 conflict store

**文件：**

- Modify: `core/life/l8/sync.py`
- Modify: `core/life/l8/lineage_graph.py`
- Test: `tests/test_l8_sync.py`
- Test: `tests/test_l8_sync_domain_integration.py`

**实现：**

- 实现 created/pairing/authenticated/exchanging/reconciling/awaiting_resolution/applying/verifying/synced 状态机；
- 按 domain transaction 应用，envelope 重放幂等；
- tombstone/high-water 先于内容，旧内容不得复活；
- Identity divergent chain、Memory fact、Relationship/ACL、Intent、Action receipt、Growth、Skill deployment、Body preference、Model ref 分别实现规格策略；
- ACL 默认取不扩大访问的结果，remote grant/temporary authority 丢弃并生成 reauthorization requirement；
- Growth candidate/artifact 到达端保持非 active，不能远程切部署；
- active intent 冲突进入 waiting_user，禁止双重动作；
- conflict immutable 保存 source heads/record hashes/reason，用户决定 append-only；
- merge 在目标 branch 追加双 parent LineageOp，保留 source branch；
- synced receipt 明确 domain heads、conflict count 和 partial 状态。

**测试：** 各领域矩阵、tombstone race、乱序、重复、partial sync、ACL escalation、remote active skill、active intent double execution、conflict decision replay、merge provenance 和 revoked/terminated branch。

**提交门：**

```powershell
python -m pytest tests/test_l8_sync.py tests/test_l8_lineage_graph.py tests/test_l8_sync_domain_integration.py -q
git diff --check
```

**Commit：** `feat(sync): reconcile explicit continuity branches`

### Task 11：接入 continuity API、分支图和冲突 UI

**文件：**

- Create: `core/life/l8/api.py`
- Modify: `main.py`
- Create: `app/src/continuity/continuityTypes.ts`
- Create: `app/src/continuity/ContinuityClient.ts`
- Create: `app/src/continuity/ContinuitySettings.ts`
- Create: `app/src/continuity/LineageView.ts`
- Modify: `app/src/bridge/backendClient.ts`
- Modify: `app/src/settings/SettingsSurface.ts`
- Test: `tests/test_l8_api.py`
- Test: `app/tests/continuitySettings.test.ts`
- Test: `app/tests/lineageView.test.ts`

**实现：**

- exact 实现规格 online API，命令要求 server-derived subject、runtime capability、expected revision/head；
- prepare API 只生成 sealed plan，不执行 native switch/delete；
- native invoke 参数只含 plan ID/hash/challenge/opaque picker handle；
- UI 明确区分 move/copy，copy 文案和 confirmation 显示将创建 branch；
- LineageView 展示 branch/head/fork/merge/未同步/conflict，不把 merge 后画成单线；
- model/release/instance/branch summary 来自 coordinator，不从 localStorage 猜；
- conflict 逐 domain 决策，stale head 刷新；
- envelope import 先 inspect sender fingerprint、domain、count、expiry 和 conflicts，再申请应用；
- Agent tool catalog 不含 activate release、copy、merge approval、device revoke 或 termination。

**测试：** subject spoof、scope、stale head、move/copy 误选、opaque handle、模型工具不可见、branch rendering、partial sync、conflict stale 和 redaction。

**提交门：**

```powershell
python -m pytest tests/test_l8_api.py tests/test_runtime_access.py -q
npm --prefix app test
npm --prefix app run build
git diff --check
```

**Commit：** `feat(app): expose honest continuity controls`

### Task 12：实现 TerminationPlanner 和 content-free receipt

**文件：**

- Create: `core/life/l8/termination.py`
- Modify: `core/life/l8/coordinator.py`
- Test: `tests/test_l8_termination.py`
- Modify: `tests/test_l8_contracts.py`

**实现：**

- planner 只从刚验证的 ContinuityManifest、derivation closure、lineage scope 和 external asset policy 构建 target set；
- scope 支持 instance/branch/identity_all_local，默认 preserve installed app；
- plan 使用 root ID/entry hash，不含 frontend supplied arbitrary path；
- 生成一次性短期 challenge，绑定 trusted window session、plan hash、scope 和 authorization；
- challenge 阶段由 native 生成一次性 receipt signing key，公钥/key ID 写入 sealed plan，私钥只驻留原生进程内存；
- 明确 `irreversible_after_state=deleting`；
- sealing 计划包含停止 writer、SQLite checkpoint、tombstone high-water、sync outbox policy、key IDs、canary 和 receipt target hash；
- receipt builder 只接受 native phase结果，递归禁止 path/content/secret；
- assurance level 依据 key destroy + absence 证据选择，不声称 bit overwrite；
- partial residual 只保存 reason code/entry hash，不保存文件名；
- retry 生成新 plan/challenge，引用 previous receipt。

**测试：** scope closure、derived index/cache/log/artifact、external model preserve/delete、arbitrary path injection、challenge expiry/replay、content scan、assurance honesty、partial/retry 和 remote/Agent caller rejection。

**提交门：**

```powershell
python -m pytest tests/test_l8_termination.py tests/test_l8_contracts.py -q
git diff --check
```

**Commit：** `feat(continuity): seal local termination plans`

### Task 13：实现本地原生 termination executor 与 verifier

**文件：**

- Create: `app/src-tauri/src/continuity_termination.rs`
- Modify: `app/src-tauri/src/continuity.rs`
- Modify: `app/src-tauri/src/main.rs`
- Modify: `app/src-tauri/src/sidecar.rs`
- Create: `app/src/continuity/TerminationDialog.ts`
- Modify: `app/src/continuity/ContinuitySettings.ts`
- Test: Rust module tests in `continuity_termination.rs`
- Test: `app/tests/terminationDialog.test.ts`
- Modify: `tests/test_l8_termination.py`

**实现：**

- Tauri command 只从 trusted window 接收 plan ID/hash/challenge 和可选 receipt save handle；
- native 从 canonical data root 读取 sealed plan，复核 signature/hash/expiry/root ID/manifest/authorization；
- 两步 UI：review scope/count/保留项，第二步输入一次性 challenge；
- quiescing 停止新请求，撤销 runtime access，停止 sidecar、owned Python、Javis-owned Ollama，等待文件句柄关闭；
- sealing 写 final tombstone/checkpoint，关闭 SQLite，准备 key destroy；
- deleting 先销毁 wrapped keys，再按 manifest relative targets 使用 root handle 删除；不跟随 reparse，不跨 volume/root；
- verifying 检查 target absence、wrapped keys absence、canary 不可解密、进程不存在和 residual；
- identity_all_local 删除 data root 时，在删除前将 receipt builder 保持内存，并通过用户 save handle 写 content-free receipt；
- final receipt 使用 plan-bound ephemeral key 签名，写出后销毁 private key；verifier 校验 plan/device signature 链和 ephemeral receipt signature，全程不联网；
- `continuity_verify_receipt` 校验 schema/hash/signature/content_free/本地 absence；
- deleting 后取消请求被拒绝，流程必须进入 completed 或 partial。

**测试：** Python/sidecar 不可用、无网络、wrong window、challenge replay、TOCTOU plan、junction swap、locked file、process respawn、key destroy fail、save fail、partial、receipt verify 和 no-content binary scan。

**提交门：**

```powershell
cargo test --manifest-path app/src-tauri/Cargo.toml continuity_termination
python -m pytest tests/test_l8_termination.py -q
npm --prefix app test
git diff --check
```

**Commit：** `feat(native): execute verifiable local termination`

### Task 14：保持卸载保留语义并封闭恢复路径

**文件：**

- Modify: `app/src-tauri/windows/nsis-hooks.nsh`
- Modify: `app/src-tauri/src/runtime_bundle.rs`
- Modify: `app/src-tauri/src/main.rs`
- Modify: `app/src/main.ts`
- Modify: `tests/test_app_native_audio.py` 中既有卸载保留合同，必要时迁移到专用测试
- Modify: `tests/test_app_v3_integrated_release.py`
- Test: `tests/test_l8_release_contract.py`

**实现：**

- 普通卸载和 silent uninstall 继续默认不删除 `JAVIS_DATA_ROOT`；
- 卸载器不新增“顺便终止”静默参数；若产品提供入口，只能启动同一 native sealed termination UI；
- terminated marker/receipt hash 存活到删除执行结束，安装残留启动不能复用旧 identity；
- data root 丢失、manifest 损坏、lineage 多 head、activation journal 矛盾时进入 recovery UI；
- recovery UI 只提供 inspect、选择旧 root、回滚 slot、导出诊断、adopt-as-copy/discard，不自动 birth；
- runtime bundle 不再把旧 runtime/app 状态偷偷复制回来；
- safe mode 保留只读导出和 receipt verify，不允许 action/growth/sync 写入。

**测试：** silent uninstall preserve、terminated reinstall、新 birth explicit、missing root、bad lineage、ambiguous slot、legacy runtime state copy absent 和 recovery no-write。

**提交门：**

```powershell
python -m pytest tests/test_l8_release_contract.py tests/test_app_v3_integrated_release.py tests/test_app_native_audio.py -q
npm --prefix app test
cargo test --manifest-path app/src-tauri/Cargo.toml
git diff --check
```

**Commit：** `fix(recovery): preserve data without reviving identities`

### Task 15：端到端 fault injection 与全阶段回归

**文件：**

- Create: `scripts/build_continuity_fixture.py`
- Create: `scripts/verify_javis_l8.py`
- Create: `tests/test_l8_end_to_end.py`
- Modify: `scripts/javis_release_gate.py`
- Modify: `scripts/app_package_manifest.py`

**实现：**

- 生成全合成 L0-L7 数据 fixture：identity、branch、memory、ACL、intent/action receipts、growth artifact、body prefs、model ref、tombstone；
- 场景 A：release A -> B -> post-start fail -> rollback A，身份/branch 不变；
- 场景 B：local/cloud model 十次切换，无 branch/instance 增长；
- 场景 C：data root move，按每个状态机边注入 crash，最终 old 或 new 只有一个 active；
- 场景 D：device migrate 无 branch；copy 有新 branch；手工复制 quarantine；
- 场景 E：两 branch 离线修改、加密 envelope、tombstone、ACL escalation、conflict、merge；
- 场景 F：普通 uninstall 保留；instance/branch/identity termination completed/partial；receipt 再验证；
- 比较 source/runtime tree hash，证明正常运行和迁移不写代码槽；
- release gate 静态拒绝 git updater、`extractall`、状态进 runtime 和 Agent destructive continuity tools；
- 记录 exact git SHA、release/archive/manifest hash、测试输出和 `NOT EXECUTED` 项。

**提交门：**

```powershell
python -m pytest -q
npm --prefix app test
npm --prefix app run build
cargo test --manifest-path app/src-tauri/Cargo.toml
python scripts/verify_javis_l8.py
git diff --check
git status --short
```

`git status --short` 只允许声明的源码、测试、fixture schema 和证据索引；不得包含真实 key、真实 data root、envelope、backup、receipt、runtime slot 或构建缓存。

**Commit：** `test(continuity): seal l8 lifecycle evidence`

### Task 16：D 盘和双设备真实验收

**前置：** Task 1-15 全绿、工作树干净、从 exact commit 构建签名测试 release；每个破坏性场景使用专用合成 identity，不使用用户现有真实 Javis 数据。

**文件：**

- Evidence: `artifacts/evidence/l8/<exact-commit>/`，保存脱敏 machine-readable 结果、hash 和 `NOT EXECUTED` 标记
- Create: `docs/superpowers/evidence/<date>-javis-l8-real-acceptance.md`，只保存证据索引，不保存用户内容、key 或绝对私密路径

**验收步骤：**

1. D 盘现有安装普通升级，验证 identity/lineage/branch/model/skills/body/承诺；
2. 切换本地/云模型并重启，证明不分支；
3. 将合成 data root 迁到另一目录，注入一次断电/kill，验证恢复；
4. 在第二台或隔离 VM 执行 device migration，证明新 instance 同 branch；
5. 执行 copy，两个 branch 分别产生合成经历，再离线 envelope sync/冲突/merge；
6. 撤销第二设备，旧 key envelope 被拒绝；
7. 普通卸载，证明 data root 保留；
8. 对合成 identity 执行本地原生 branch termination 和 identity_all_local termination；
9. 保存 content-free receipt，在离线 verifier 中复核；
10. 检查 source/runtime slot、data root、Windows 进程和残留 reason；
11. 记录视频/截图只能作为行为证据，仍需 manifest/receipt/hash 和跨重启结果。

**真实验收门：**

- 不使用真实私密记忆做删除演示；
- exact commit/release hash/manifest hash/receipt hash 全部记录；
- 终止前备份仅限合成 fixture，且备份本身在验收后按同样流程清理；
- 任何 partial 必须修复并用新 plan 重试，不能人工删残留后把旧 receipt 改成 completed；
- 未具备第二设备、签名环境或真实终止批准时，相关项明确 `NOT EXECUTED`，L8 不标 final complete。

**提交门：**

```powershell
python scripts/verify_javis_l8.py --evidence-root artifacts/evidence/l8
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_javis_v3_installer.ps1
git diff --check
git status --short
```

验收脚本参数若与实现时实际 CLI 不同，应先更新本计划对应的脚本合同测试；不得用手工口头结论替代 machine-readable evidence。

**Commit：** `docs(evidence): record l8 real continuity acceptance`

## 5. 提交序列与合入门

建议 Task 1-15 每任务一个独立提交，Task 16 只提交脱敏证据索引。共同门：

1. 失败测试能复现该任务关闭的真实风险；
2. 定向 Python/TypeScript/Rust 测试通过；
3. `git diff --check` 通过；
4. schema/crypto/release fixture 不含真实 key 或内容；
5. migration/update/termination 改动说明每个 crash point 的 old/new 权威判定；
6. shared file 修改保留 L0-L7 与其他工作者已合入变更；
7. 任何 destructive native command 都有否定测试证明 HTTP/Agent/remote 不可达；
8. 任何 path 删除/移动都由 sealed root handle 和 manifest 闭包决定；
9. 未通过定向门不得继续下一个依赖任务。

## 6. 规格覆盖矩阵

| 规格要求 | 实施任务 |
|---|---|
| ContinuityCoordinator | 4, 7, 8 |
| 所有可变状态仅在 JAVIS_DATA_ROOT | 2, 3, 4, 15 |
| ContinuityManifest/MigrationReceipt | 1, 4, 7 |
| 迁移状态机和回滚 | 7, 8, 15 |
| LineageOp 与真正分支图 | 1, 5 |
| 升级/模型切换不分支，copy 才分支 | 5, 8, 15 |
| SyncEnvelope、加密、权限不提升 | 1, 9, 10 |
| Branch-sync 状态机和 merge | 5, 10, 11 |
| 安全升级、无 git/extractall | 6, 8, 15 |
| TerminationPlan/Receipt | 1, 12 |
| 本地原生可验证终止 | 13, 14, 16 |
| 普通卸载保留 | 14, 15, 16 |
| 跨版本/路径/设备真实证据 | 15, 16 |

## 7. 阶段出口

L8 只有 Task 1-16 对应证据齐全，且正式规格第 22 节十四项出口全部满足，才能标记完成。以下不算完成：

- 复制目录后只比较文件数量；
- upgrade/model switch 仍因 fingerprint 变化产生 fork；
- lineage schema 加了 branch 字段但校验仍只允许单 child；
- envelope 加密但远端 active skill/grant 可直接生效；
- updater 关闭 `extractall` 却仍原地 tar 解包或 git pull；
- data root pointer 统一但模块仍写源码 `brain_data/data/config.yaml`；
- termination 由 Python/HTTP 执行，或没有 native challenge；
- 删除成功但无 content-free receipt/absence/key 验证；
- 普通卸载默认删除用户数据；
- 自动化 fixture 通过但真实跨版本、复制或终止未执行且未标 `NOT EXECUTED`。
