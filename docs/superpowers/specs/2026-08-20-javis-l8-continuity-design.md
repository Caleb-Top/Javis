# Javis L8 长期连续性设计规格

**日期：** 2026-08-20
**状态：** 正式规格，等待按配套实施计划执行
**父规格：** `docs/superpowers/specs/2026-08-09-javis-life-os-master-design.md`
**执行基线：** `docs/superpowers/plans/2026-08-20-javis-life-os-l2-l8-execution.md`
**范围：** 升级、数据根迁移、实例谱系图、复制分支、加密同步、冲突合并、撤销设备与终止权

## 1. 决策摘要

L8 以 `ContinuityCoordinator` 为连续性唯一语义权威。它不把文件复制成功等同于身份连续，也不让 updater、模型、Agent 或同步端决定分支、权限和终止。连续性由内容清单、谱系操作、迁移收据、同步信封、删除 tombstone 和本地验证共同证明。

正式冻结以下裁决：

1. 正常重启、应用升级、runtime 切换、模型切换和受权迁移不创建分支；
2. 只有显式复制 `copy` 才创建新 `branch_id` 和新实例；
3. 未带有效复制/迁移凭据的文件系统副本进入 quarantine，不自动冒充原实例；
4. 当前线性 `InstanceLineageStore` 升级为可验证的 `LineageGraphStore`，支持多分支与 merge op；
5. 同步是分支之间的加密、最小化、按领域合并，不允许远端提高本地权限；
6. 正式更新只走本地原生 staging、校验、切换和回滚，生产不使用 `git pull` 或 `extractall()`；
7. 终止由本地原生 `NativeContinuityExecutor` 执行，模型、Agent、HTTP 和远端服务均不能触发；
8. 终止完成必须产生不含用户内容的 `TerminationReceipt`，并可由本地验证器复核；
9. 所有 Javis 拥有的可变状态只能位于 launcher 注入的 canonical `JAVIS_DATA_ROOT`。用户主动导出的 manifest/envelope/receipt 是用户拥有的导出物，不是第二数据根。

## 2. 目标

1. 升级、路径变化、模型变化和设备迁移后证明仍是同一 Javis；
2. 在复制后诚实形成分支，不声称拥有另一个分支未同步的经历；
3. 对每次迁移提供 prepare、copy、verify、activate、checkpoint 和 rollback 证据；
4. 对每次同步提供身份、分支、顺序、加密、签名、tombstone 和冲突证据；
5. 让用户查看、导出、复制、迁移、暂停同步、撤销设备和合并分支；
6. 让用户在不依赖云端、模型或运行中 Python 的情况下终止一个实例或全部本地身份数据；
7. 在任何连续性验证失败时进入只读恢复，而不是创建空人格或覆盖旧数据。

## 3. 非目标

- 不承诺无冲突的实时多主写入；
- 不让两个分支持有同一 active instance 或假装是同一条并发时间线；
- 不同步 API key、密码、原始媒体、临时授权或设备私钥；
- 不把云服务作为身份真值源或终止前置条件；
- 不在生产环境用 git 仓库作为更新协议；
- 不以更换模型创建新人格、实例或分支；
- 不以应用卸载等同于身份终止；
- 不声称 SSD 上逐位安全擦除可以被普通应用证明；
- 不在终止后保留含内容的“纪念副本”或隐藏恢复入口。

## 4. 当前源码事实与缺口

| 事实 | 缺口 | L8 决策 |
|---|---|---|
| `core/life/lineage.py` 明确维护 one active, linear lineage | 校验器拒绝同一 parent 的多个 child，无法表达真实分支 | 迁移到 append-only LineageOp 图和 branch head |
| 环境 fingerprint 变化自动创建 `fork_pending_review`，仍沿用同一 lineage ID | 移动、升级、复制没有正式操作语义 | 以 sealed `LineageOp` 区分 migrate/copy；只有 copy 分支 |
| `app/src-tauri/src/runtime_bundle.rs` 把旧 `brain_data/data/workspace/logs` 复制进新 runtime/app | 用户状态与可替换代码槽混在一起，升级无法做完整校验 | runtime slot 只含不可变代码；状态独立留在 JAVIS_DATA_ROOT |
| runtime bundle 使用 local data，sidecar 又使用 app data 下 `runtime-data` | 数据根不一致 | native launcher 每次启动只解析并注入一个 canonical JAVIS_DATA_ROOT |
| `core/auto_updater.py` 使用 GitHub API、`git pull`、源码树备份和 `extractall()` | 无发布签名、无 staging manifest、可路径逃逸和原地污染 | 原生 release verifier + safe extractor + immutable slot + atomic pointer |
| 现有 NSIS 卸载默认保留用户数据 | 这是正确保留基线，但没有显式终止能力和验证收据 | 卸载继续默认保留；终止是独立、本地原生、二次确认流程 |
| 没有 MigrationManifest、sync envelope、merge policy 或 termination receipt | 无法证明复制、冲突、删除和长期连续 | 冻结本规格合同与状态机 |
| `config.yaml`、brain/memory、skill、模型 pointer 和旧日志仍分散在源码/runtime 路径 | 迁移会漏项，终止无法闭包 | L8 建立 app-owned mutable inventory，并执行一次可回滚 data-root migration |

## 5. 不变量

### 5.1 身份与分支

- `identity_id` 表示长期 Javis 身份，不随版本、模型、身体、路径或设备迁移变化；
- `lineage_id` 表示共同谱系图，不随分支增加变化；
- `branch_id` 表示一条可独立产生经历的历史线；
- `instance_id` 表示当前安装/运行实例；受权设备迁移可以创建新 instance，但仍在同一 branch；
- 同一 active instance 在一个时间点只绑定一个 branch head；
- 只有 `LineageOp.op_kind=copy` 创建新 branch；
- upgrade、model_switch、runtime_switch、data_root_move、device_migrate 和 restore_in_place 均不得创建 branch；
- merge 在目标 branch 追加双 parent op，不删除或重命名来源 branch。

### 5.2 单写者和原生边界

- 在线 `ContinuityCoordinator` 单写 canonical manifest、lineage graph、sync projection 和 migration state；
- `NativeContinuityExecutor` 是 update slot 切换、离线数据根切换和终止删除的唯一执行者；
- 原生执行器只消费 coordinator 生成的 sealed plan，并独立复核路径、哈希、用户 challenge 和 operation state；
- 原生执行期间只写 `JAVIS_DATA_ROOT/continuity/native-journal/inbox` 的 append-only 记录；下一次启动由 coordinator 验证后纳入 canonical store；
- 模型、Agent、同步端和 frontend 不能直接写上述 stores。

### 5.3 数据根

canonical root 来自 native launcher 注入的绝对 `JAVIS_DATA_ROOT`。Life OS 内部不再读取 `JAVIS_APP_DATA_ROOT`，也不在源码、当前工作目录、打包 runtime、App LocalData 的其他子树维护第二份用户状态。

```text
JAVIS_DATA_ROOT/
  life/
  conversations/
  memory/
  relationships/
  environment/
  intents/
  actions/
  growth/
  body/
  config/
  logs/
  local-ai/
  continuity/
    manifests/
    migrations/
    lineage/ops/
    lineage/heads/
    sync/inbox/
    sync/outbox/
    sync/conflicts/
    keys/
    termination/
    native-journal/inbox/
```

规则：

- app-owned 数据库、配置、日志、缓存、索引、候选、artifact、pointer、receipt、tombstone、key wrapper 和 temp 全部在 root；
- 模型大文件可位于用户选定外部目录，但其 pointer、hash、license 和安装收据在 root；模型目录是受清单治理的外部资产，不是第二生命状态根；
- 用户授权 workspace 和主动导出路径不是 app-owned state，不能保存隐藏索引或内部 pointer；
- root 变化必须经过 migration state machine，不能只改 environment/locator；
- root 不可验证时只读恢复，不从旧源码目录回退启动。

### 5.4 真值、删除和权限

- 同步 payload 中的事实必须保留原 owner、audience、privacy、retention、TTL、source 和 branch；
- tombstone 优先于较旧内容，删除不能通过重放复活；
- 远端 capability、grant、ACL 扩大、active skill deployment 和 model tool 权限不能直接生效；
- key、secret 和 temporary authority 不同步，只同步引用和 reauthorization requirement；
- 终止必须先停止新写入、封闭派生图、销毁 key，再删除并验证；
- “文件不存在”与“密码学不可恢复”分别记录，不夸大擦除保证。

## 6. 总体架构

```text
                    online runtime
                         |
              ContinuityCoordinator
       +-----------------+------------------+
       |                 |                  |
 ManifestBuilder   LineageGraphStore   BranchSyncEngine
 /Verifier          + HeadStore         + ConflictStore
       |                 |                  |
       +----------- ContinuityStore --------+
                         |
              sealed native operation plan
                         |
              NativeContinuityExecutor
        +----------------+----------------+
        |                |                |
  release staging   data-root switch  local termination
        |                |                |
        +-------- native journal/receipt -+
                         |
            next-boot verify and import
```

### 6.1 ContinuityCoordinator

职责：

- 创建、验证和签封 ContinuityManifest；
- 决定操作是 upgrade、migration、copy、sync、merge、revoke 或 terminate；
- 串行化同一 identity 的 destructive continuity operation；
- 维护 migration、branch-sync 和 termination 状态投影；
- 验证 native journal 和 receipt 后推进 canonical state；
- 将失败投影为只读恢复和明确用户决策，不创建默认身份。

### 6.2 ManifestBuilder 与 ManifestVerifier

按逻辑域枚举状态，不依赖“复制某几个目录”的硬编码猜测。每个权威服务注册自己的 manifest contributor，声明 schema、相对路径、数据库 checkpoint、derivation edges、外部资产引用和 tombstone high-water mark。

Verifier 分三层：

1. 结构：strict schema、路径、数量、大小、重复和版本；
2. 内容：逐 entry hash、数据库 integrity/checkpoint、引用闭包、派生图和 key ref；
3. 语义：identity/lineage/branch、owner/ACL、active deployment、未完成 action 和 release 兼容。

### 6.3 LineageGraphStore

保存 append-only `LineageOp`，由 op hash 构成可验证 DAG。Branch head 是 CAS 指针，损坏时可从 op 图重建。旧线性 InstanceRecord 作为迁移输入，迁移完成后只读保留到 rollback 窗口结束。

### 6.4 BranchSyncEngine

同步 transport-neutral。首版至少支持用户通过原生文件选择器导出/导入加密 SyncEnvelope；未来局域网或云 transport 只能搬运相同 envelope，不能改变语义。

### 6.5 NativeContinuityExecutor

本地 Tauri/Rust 执行器负责：

- 安全解包签名 release 到 immutable staging slot；
- 在 runtime 停止后复制/校验/切换 data root；
- 原子切换 active runtime slot并保留 previous；
- 停止 Javis 拥有的进程和文件句柄；
- 执行 sealed TerminationPlan、销毁 wrapped keys、删除目标并验证；
- 在没有 Python、模型和网络时仍可工作。

它不自行决定 branch、merge、ACL 或用户事实。

## 7. 共同合同规则

所有 L8 合同：

- strict schema，未知字段拒绝；
- canonical UTF-8 JSON/CBOR profile 固定并版本化；
- UTC RFC3339 时间，顺序由单调 `op_sequence` 和 per-device sequence 决定；
- 每个写命令带 `idempotency_key` 和 `expected_revision/head`；
- 每个 immutable record 有 `content_hash`，需要跨设备认证的 record 另有 `signature`、`signing_key_id` 和 algorithm suite；
- 路径只能是规范化相对路径，wire/日志不暴露绝对用户路径；
- secret、private key、token、原始媒体和隐藏推理禁止进入普通 manifest/receipt；
- 记录 identity、instance、branch、owner、audience、privacy、retention、source、created/updated/expiry；
- schema 升级必须有双向兼容范围、fixture 和 rollback 证据。

## 8. ContinuityManifest 与 MigrationReceipt

### 8.1 ContinuityManifest v1

```text
schema_version, manifest_id, purpose,
identity_id, lineage_id, branch_id, instance_id,
source_root_id, source_release_id, source_runtime_hash,
model_refs, body_deployment_refs, skill_deployment_refs,
domain_schemas, event_cursors, tombstone_high_watermarks,
entries, external_asset_refs, key_refs,
total_entries, total_bytes, created_at_utc, expires_at_utc,
created_by_version, approval_ref, previous_manifest_hash,
privacy_class, retention_class, content_hash,
signing_key_id, signature_algorithm, signature
```

每个 `entries[]`：

```text
domain, relative_path, entry_kind(file|sqlite|directory_marker),
size_bytes, sha256, schema_version, logical_record_count,
sqlite_checkpoint, owner_subject_id, audience,
privacy_class, retention_class, expires_at_utc,
derivation_parent_ids, required, encryption_ref
```

规则：

- manifest 不包含文件正文；
- symlink、junction、reparse point、ADS、device path 和逃逸相对路径禁止；
- volatile cache 可标 `required=false` 且通过重建验证，不能伪装成已复制；
- key_refs 只含 wrapped key ID 和算法，不含明文 key；
- model/body/skill 只记录精确引用，切换模型不影响 identity/branch；
- manifest 过期或 signature/hash 失败不得 activate。

### 8.2 MigrationReceipt v1

```text
schema_version, receipt_id, operation_id, operation_kind,
manifest_id, manifest_hash,
identity_id, lineage_id, branch_id,
source_root_id, target_root_id,
source_instance_id, target_instance_id,
state, phase_receipts, copied_entries, copied_bytes,
verified_entries, rebuilt_entries, skipped_entries,
schema_migration_ids, pointer_before_hash, pointer_after_hash,
activation_journal_hash, checkpoint_id,
rollback_target, rollback_receipt_id,
started_at_utc, completed_at_utc,
failure_reason_code, residuals,
content_hash, signing_key_id, signature
```

每个 phase receipt 记录输入/输出集合 hash、开始/完成时间、计数和 reason code。不得包含绝对路径、文件内容、secret 或命令行。

## 9. LineageOp 与分支语义

### 9.1 LineageOp v1

```text
schema_version, op_id, op_sequence, op_kind,
identity_id, lineage_id, branch_id, instance_id,
parent_op_ids, source_branch_id, target_branch_id,
source_instance_id, target_instance_id,
fork_point_manifest_hash, continuity_manifest_hash,
release_before, release_after, model_before, model_after,
reason_code, user_decision_ref, migration_receipt_id,
sync_receipt_ids, created_at_utc,
previous_op_hashes, content_hash,
signing_key_id, signature
```

### 9.2 操作矩阵

| 操作 | identity_id | lineage_id | branch_id | instance_id | 是否分支 |
|---|---|---|---|---|---|
| birth | 新建 | 新建 | 新建 root branch | 新建 | 否 |
| restart | 保持 | 保持 | 保持 | 保持 | 否 |
| upgrade/runtime_switch | 保持 | 保持 | 保持 | 保持 | 否 |
| model_switch | 保持 | 保持 | 保持 | 保持 | 否 |
| data_root_move | 保持 | 保持 | 保持 | 保持 | 否 |
| device_migrate | 保持 | 保持 | 保持 | 可新建并链接 predecessor | 否 |
| restore_in_place | 保持 | 保持 | 保持，推进 epoch | 保持 | 否 |
| copy | 保持 | 保持 | **新建**，parent 为 source branch | 新建 | **是** |
| sync | 保持 | 保持 | 各自保持 | 各自保持 | 否 |
| merge | 保持 | 保持 | 目标保持，追加双 parent op | 各自保持 | 否 |

文件夹复制没有 sealed copy op 时进入 `untrusted_copy_detected`。用户可选择：

- `adopt_as_copy`：验证来源后创建新 branch/instance；
- `discard_copy`：本地原生删除副本；
- `recover_migration`：仅在存在未完成 migration journal 且源已 quiesced 时恢复同 branch；
- `read_only_inspect`：不产生新经历。

系统不能因路径、模型名或版本变化自动创建 branch。

## 10. SyncEnvelope 与领域合并

### 10.1 SyncEnvelope v1

```text
schema_version, envelope_id, sync_session_id,
identity_id, lineage_id, sender_branch_id, sender_instance_id,
recipient_device_key_id, sender_device_key_id,
sender_sequence, base_heads, advertised_heads,
domain, record_count, tombstone_count,
payload_algorithm, nonce, ciphertext,
payload_sha256, key_epoch,
created_at_utc, expires_at_utc,
content_hash, signing_key_id, signature_algorithm, signature
```

明文 header 只含路由和反重放所需最小字段。加密 payload 内每条 record 仍保留 record ID、schema、branch、owner、audience、privacy、retention、source、revision/vector、content hash 和 tombstone。

### 10.2 密码套件和 key

- 每设备独立签名与密钥协商 key；
- envelope 使用版本化 authenticated encryption，nonce 不复用；
- 私钥以 Windows DPAPI 包装后保存在 `JAVIS_DATA_ROOT/continuity/keys`；
- key rotation 提升 `key_epoch`，撤销设备后旧 key 不能发送新 envelope；
- secret/token/API key 不进入 envelope，即使加密也不允许；
- 签名、recipient、expiry、sequence、identity/lineage 任一失败均在解密/应用前拒绝。

### 10.3 领域合并规则

| 领域 | 默认策略 | 冲突处理 |
|---|---|---|
| IdentityConstitution | 只接受有效版本链 | divergent charter 必须用户选择，不自动融合 |
| Lineage | op DAG union + signature 验证 | 缺 parent 先 quarantine |
| Memory | record union，较新有效 tombstone 胜 | 同 fact divergence 进入 L2 conflict，不拼正文 |
| Relationship/ACL | 取不扩大访问的交集 | 权限扩大必须本地重新批准 |
| Intent/commitment | 只同步状态与收据，不双重执行 | active 冲突进入 waiting_user，不自动续跑 |
| Action/grant | 收据可同步，grant/temporary authority 不同步 | 本地重新授权 |
| Growth candidate | 可同步不可变 candidate/artifact hash | 到达端默认为非 active，需本地验证/批准 |
| Skill deployment | 只同步引用和兼容信息 | 不远程激活 |
| Body preference | 支持确定性 LWW，带用户时间和 revision | 不支持 capability 时本地 fallback |
| Model reference | 同步偏好引用 | 切换不分支，缺模型提示安装但不下载 |

## 11. 迁移状态机

```text
planned
  -> preparing
      -> prepared
          -> copying
              -> copied
                  -> verifying
                      -> ready_to_activate
                          -> activating
                              -> checkpointing
                                  -> completed

任何 activate 前错误 -> failed_pre_activation
activate/验证后错误   -> rolling_back -> rolled_back
无法判定 active slot  -> recovering_read_only
用户在 copying 前取消 -> cancelled
```

规则：

- `preparing` 先 quiesce 新写入、完成 SQLite checkpoint、撤销临时授权并生成 manifest；
- source 在 copy 期间保持权威且只读，target 不可被 runtime 使用；
- copy 逐 entry 到空 staging root，不能 merge 到已有未知目录；
- verifying 执行 hash、数据库、schema、引用闭包、identity/lineage/branch 和 key 检查；
- active pointer 仅在 `ready_to_activate` 后由 native CAS 切换；
- checkpoint 成功前 previous slot/root 不删除；
- 崩溃恢复读取 native activation journal，依据 pointer/hash 判定完成或回滚，不能猜测；
- upgrade 和数据迁移共用状态语义，但代码 slot 与数据 root 不能混成一个目录；
- retry 创建新 operation，引用旧 receipt，不改旧终态。

## 12. Branch-Sync 状态机

`BranchSyncRun` 状态：

```text
created
  -> pairing
      -> authenticated
          -> exchanging
              -> reconciling
                  -> awaiting_resolution -> reconciling
                  -> applying
                      -> verifying
                          -> synced

pairing/auth/exchange 前可 cancelled
任一阶段可 failed 或 quarantined
```

规则：

- pairing 显式显示对端设备和 branch fingerprint，不能由模型默许；
- authenticated 固定 key、identity、lineage 和允许 domain；
- sender sequence 与 envelope ID 防重放，乱序可缓冲但有界；
- reconciling 先应用 tombstone 和 ACL 约束，再计算内容冲突；
- awaiting_resolution 不阻塞无冲突 domain，但不能把部分同步伪报为 synced；
- applying 使用 per-domain transaction 与 idempotency key；
- verifying 比较目标 heads、记录 hash、tombstone high-water 和 conflict count；
- synced 不合并 branch ID，只证明双方已交换到某组 heads；
- 被 revoke/terminated branch 不能发送新 envelope，历史 receipt 仍可验证。

Branch lifecycle：

```text
active <-> quiesced
active|quiesced -> revoked
active|quiesced|revoked -> terminated
```

`terminated` 是终态，不能通过 sync 恢复。

## 13. 安全升级与 runtime 切换

正式升级链：

```text
signed release manifest
 -> download/import into native staging
 -> verify publisher, version, checksum, size and compatibility
 -> enumerate every archive member
 -> reject unsafe path/link/reparse/ADS/collision/bomb
 -> extract each regular file into fresh immutable slot
 -> verify extracted tree and import probe
 -> prepare ContinuityManifest/checkpoint
 -> stop owned sidecar
 -> atomically switch runtime pointer
 -> start and verify identity/data schemas
 -> checkpoint or rollback previous slot
```

规则：

- production 不执行 git、shell updater 或 arbitrary URL；
- release asset 与 manifest 都要验证，SHA-256 不是发布者身份签名的替代；
- 禁止 `extractall()`、tar 盲解包和向当前 runtime 原地覆盖；
- archive 成员拒绝绝对路径、`..`、Windows reserved name、尾随点/空格、大小写碰撞、ADS、链接、reparse 和声明/实际大小不符；
- 安装代码 slot 不保存用户状态；升级不复制 `brain_data/data/config/workspace/logs`；
- 模型切换只追加 `model_switch` LineageOp 和更新配置引用，不创建 instance/branch；
- release 验证失败保留旧 runtime 和旧权威数据。

## 14. 数据根统一与旧数据迁移

L8 首次迁移必须构建完整 legacy inventory，至少覆盖：

- `config.yaml` 与 provider/model settings；
- `brain_data` 下 facts、episodes、semantic、procedural、papers、reminders 和训练元数据；
- `data` 下 skill market/review、updates、run store 和其他状态；
- `memory/sessions` 与 SQLite/WAL/SHM；
- workspace manifest、uploads、logs、output 中属于 app-owned 的部分；
- SkillCreator/Manager 生成物、自定义 tools 和 active pointer；
- local model root pointer、下载 checkpoint 和安装收据；
- Life、conversation、agent run、L2-L7 已在新 data root 的数据库。

分类后处理：

- 已有权威新 store 的旧数据进入 `legacy/quarantine`，不能双写；
- 用户 workspace 内容保持用户资产，只迁移 app-owned manifest，不擅自搬项目；
- secret 从明文/base64 config 迁入受保护 key wrapper，失败则要求重新输入；
- cache/index 可重建但必须记录 skipped/rebuilt receipt；
- 源数据在 checkpoint 与回滚窗口结束前只读保留；
- 切换后所有模块都通过注入 path，静态测试禁止模块级源码相对写路径；
- migration completed 后打包 runtime/source tree 在正常使用中保持只读。

## 15. TerminationPlan 与 TerminationReceipt

### 15.1 TerminationPlan v1

```text
schema_version, plan_id, scope(instance|branch|identity_all_local),
identity_id, lineage_id, branch_ids, instance_ids,
root_id, manifest_id, manifest_hash,
target_entry_hashes, external_asset_actions,
owned_processes, database_checkpoint_ids,
tombstone_plan, sync_outbox_policy,
key_ids_to_destroy, key_epoch_after,
preserve_user_exports, preserve_installed_app,
receipt_export_target_hash,
receipt_ephemeral_public_key, receipt_ephemeral_key_id,
confirmation_challenge_hash, authorization_id,
created_at_utc, expires_at_utc,
irreversible_after_state, content_hash,
signing_key_id, signature
```

计划不包含用户内容或明文绝对路径。target entry 来自刚验证的 manifest；native executor 通过 sealed root handle 解析，不接受 frontend 传任意删除路径。

### 15.2 TerminationReceipt v1

```text
schema_version, receipt_id, plan_id, plan_hash, scope,
identity_id_hash, lineage_id_hash, root_id_hash,
started_at_utc, completed_at_utc, state,
quiesce_receipt_hash, tombstone_high_watermarks,
destroyed_key_ids_hash, key_destroy_results,
deleted_entry_count, deleted_bytes,
external_asset_results, verification_checks,
residual_count, residual_reason_codes,
assurance_level(logical_delete|crypto_erasure_plus_absence),
content_free, verifier_version,
previous_receipt_hash, content_hash,
receipt_ephemeral_key_id, signature_algorithm, signature
```

`content_free` 必须为 true；receipt 不含文件名、正文、用户名、完整路径、secret 或媒体。challenge 阶段由 native 生成一次性 receipt key，公钥绑定进 sealed plan，私钥只保留在原生进程内存中；最终 receipt 由该 key 签名，写出后销毁私钥。全部 root 被删除时，receipt 在内存中返回并可由用户通过终止前选择的原生保存目标导出。该导出物是用户拥有的证明，不是 Javis 隐藏状态。

### 15.3 终止保证

- 本地原生：流程不要求 Python、模型、Agent、云端或同步服务在线；
- 原生用户在场：必须从受信 Tauri 窗口发起，两步 challenge 显示 scope、branch/instance 数和不可逆点；
- 路径安全：只删除 manifest 闭包内、sealed root handle 下的 app-owned entry；
- 派生闭包：先写 tombstone/high-water，停止 writer，关闭数据库，清索引/缓存/候选/artifact/log；
- key 销毁：先删除 wrapped data keys，再验证 canary ciphertext 无法解密；
- 远端独立：可生成待发送 tombstone envelope，但网络失败不阻止本地终止；
- 诚实保证：SSD/备份/文件系统历史存在时，只声明 crypto-erasure 与路径不存在，不声称逐位覆盖；
- 卸载与终止分离：普通卸载默认保留 data root，只有 sealed termination plan 才删除身份数据。

## 16. 终止状态机

```text
draft
  -> reviewed
      -> challenged
          -> authorized
              -> quiescing
                  -> sealing
                      -> deleting
                          -> verifying
                              -> completed
                              -> partial

reviewed|challenged|authorized -> cancelled
quiescing|sealing -> failed_recoverable
deleting 之后不可取消，只能 -> verifying -> completed|partial
```

规则：

- challenge 一次性、短期、绑定本机窗口、plan hash 和 scope；
- `sealing` 完成 tombstone、final manifest、数据库 checkpoint 和 key destroy plan；
- `deleting` 是不可逆点，UI 和 plan 必须事先显示；
- `partial` 保存 residual reason code 和无内容 receipt，不自动重试任意路径；
- retry 需新 plan/new challenge，目标从 residual manifest 重建；
- completed 后该 branch/identity 的 runtime 启动必须显示 terminated，不得重新 birth 覆盖；
- identity_all_local 若保留安装程序，下一次启动只能显式创建新 identity，不能复用终止 ID。

## 17. API、原生命令与用户界面

### 17.1 在线只读/准备 API

- `GET /api/life/l8/status`
- `GET /api/life/l8/manifests`
- `POST /api/life/l8/migrations/prepare`
- `GET /api/life/l8/migrations/{id}`
- `POST /api/life/l8/copies/prepare`
- `GET /api/life/l8/lineage`
- `POST /api/life/l8/sync/export/prepare`
- `POST /api/life/l8/sync/import/inspect`
- `POST /api/life/l8/sync/conflicts/{id}/decision`
- `POST /api/life/l8/termination/prepare`

这些 API 只准备 sealed plan 或读取投影。它们不能直接切换 runtime、删除数据、激活远端 grant 或完成终止。

### 17.2 原生命令

- `continuity_execute_migration(plan_id, plan_hash, challenge)`
- `continuity_activate_release(plan_id, plan_hash, challenge)`
- `continuity_export_envelope(plan_id, plan_hash, target_handle)`
- `continuity_import_envelope(source_handle)`，仅 inspect/copy to inbox
- `continuity_execute_termination(plan_id, plan_hash, challenge, receipt_handle?)`
- `continuity_verify_receipt(receipt_handle)`

命令仅从受信窗口 invoke，native 再次读取 data-root sealed plan。不得提供任意 source/target/delete path 字符串命令。

### 17.3 用户体验

用户可以：

- 查看当前 identity、lineage、branch、instance、release、model 和最近 continuity receipt；
- 在升级前看到兼容性、空间、manifest 和 rollback；
- 选择“迁移”或“复制”，界面明确说明复制会创建 branch；
- 查看分支图、每个 branch head、未同步经历和冲突；
- 对 conflict 逐领域选择，不被迫全盘覆盖；
- 撤销设备，暂停 sync，导出加密 envelope；
- 分别执行卸载、删除实例、终止 branch 或终止全部本地身份；
- 保存并再次验证无内容 termination receipt。

## 18. 故障与恢复

| 故障 | 行为 |
|---|---|
| manifest 缺项/hash/signature 失败 | 不 copy/activate，保留旧权威 |
| copy 中断 | target staging 隔离；依据 receipt 重试或删除 staging |
| pointer 切换崩溃 | 读取 activation journal，确定 old/new exact hash 后完成或回滚 |
| schema migration 失败 | 不启动写模式，回滚 previous root/slot |
| lineage 缺 parent/多 head | quarantine op，进入只读 conflict，不线性化猜测 |
| sync envelope 重放/乱序/过期 | 幂等拒绝或有界等待，不重复事实/删除 |
| 对端发来更宽 ACL/grant | 保守交集或本地重新批准，不自动放宽 |
| 被撤销设备继续发送 | key epoch/signature 拒绝并记录 reason code |
| termination 删除部分失败 | 进入 partial，列 reason code，保留无内容 receipt 和本地重试入口 |
| receipt 导出失败 | 不影响已完成删除；在完成前提示只能显示/临时验证 |
| data root 不可达 | 不从源码/runtime 创建新 identity，进入 recovery locator UI |

## 19. 隐私与安全

- manifest/receipt 默认不含绝对路径、内容、用户名、设备名、secret；
- SyncEnvelope 先验证签名/recipient/sequence，再解密，再做对象 ACL；
- 远端内容按不可信输入处理，不能触发工具、技能、更新或终止；
- release key、device key 和 content key 分离，撤销一种不能伪装另一种；
- native plan 绑定 root ID、manifest hash、operation ID 和 challenge，防止 TOCTOU；
- archive 和 envelope 有 entry count、bytes、nesting、decompression 和时间预算；
- logs 只含 ID hash、阶段、计数、reason code 和版本；
- 用户导出明文 continuity backup 必须显式选择并获得风险提示，默认导出加密；
- termination verifier 本地验证 plan/device signature 链、plan-bound ephemeral receipt signature、hash、assurance 声明和仍可检查的 absence，不联系远端证明者。

## 20. 验收场景

### 20.1 升级和模型切换

1. 从 release A 升级到 B，identity/lineage/branch/instance 不变，schema 与收据完整；
2. B 启动失败，原生回滚 A，旧数据仍是权威；
3. 恶意 zip 含 `..`、绝对路径、ADS、大小写碰撞或 zip bomb，解包前拒绝；
4. local/cloud 模型来回切换十次，只追加 model_switch op，不创建 branch/instance；
5. runtime slot 替换不复制 user data 到代码目录。

### 20.2 迁移和复制

1. data root move 走完整状态机，验证后切换，不创建 branch；
2. 设备迁移创建新 instance predecessor，但保持 branch；
3. 用户选择 copy，生成新 branch/new instance，源 branch 继续存在；
4. 手工复制 data root 后启动进入 quarantine，不声称同一 active instance；
5. copy 后两边产生不同经历，未 sync 前互不声称拥有对方经历。

### 20.3 同步和合并

1. 重放同一 envelope 不重复事实、candidate、tombstone 或 intent；
2. 删除后收到旧内容 envelope，tombstone 防止复活；
3. 远端发来 active skill/grant/更宽 ACL，本地保持非 active/保守权限；
4. conflict 未解决时显示 partial，不伪报 synced；
5. merge 后保留 source/target branch 和双 parent 证据。

### 20.4 终止

1. Agent、HTTP、sync envelope 和模型都无法调用 execute termination；
2. 普通卸载仍保留 data root；
3. 本地原生终止在 sidecar/Python 不可用时仍能执行；
4. 删除前 challenge 绑定 exact plan/scope，过期或重放拒绝；
5. 完成后 app-owned entries 不存在、wrapped keys 不存在、canary 不可解密；
6. receipt 不含内容/路径/secret，可由本地 verifier 再验；
7. 部分失败给出 residual reason，不宣称 completed；
8. 终止 identity 后启动不会用默认模板复活原 ID。

## 21. 自动化证据门

1. **合同门：** manifest、receipt、lineage op、envelope、termination strict schema 和 signature fixtures；
2. **数据根门：** 全仓 app-owned mutable inventory，源码/runtime 只读启动和跨版本验证；
3. **迁移门：** prepare/copy/verify/activate/checkpoint/rollback、断电点和 WAL；
4. **分支门：** upgrade/model/migrate 不分支，copy 才分支，DAG/heads/merge 可重建；
5. **同步门：** 加密、签名、反重放、乱序、tombstone、ACL 和 domain conflict；
6. **更新门：** 签名 release、安全逐项解包、immutable slot、atomic rollback、无 git/extractall；
7. **终止门：** native-only、challenge、路径闭包、key destroy、absence、partial 和 content-free receipt；
8. **回归门：** L0-L7 身份、记忆、关系、会话、语音、打断、行动、成长和身体；
9. **真实时间门：** 跨版本、跨路径、跨设备复制、离线 envelope、撤销设备和终止。

## 22. 出口标准

L8 只有同时满足以下条件才完成：

1. `ContinuityCoordinator` 成为连续性语义唯一入口，native executor 只执行 sealed plan；
2. ContinuityManifest/MigrationReceipt 完整覆盖 app-owned state、schema、key ref、外部资产和 tombstone；
3. 所有 app-owned mutable state 只在 canonical `JAVIS_DATA_ROOT`；
4. 升级和模型切换不创建分支；受权迁移不创建分支；只有 copy 创建分支；
5. 线性 InstanceLineageStore 已安全迁移为可验证分支图；
6. SyncEnvelope 加密、签名、反重放、最小化，远端不能提高本地权限；
7. 分支冲突和未同步经历真实可见，merge 不抹除来源历史；
8. 正式更新无 git pull、无原地覆盖、无 `extractall()`，可原生回滚；
9. 迁移各阶段可恢复，失败不以空人格启动；
10. 终止本地原生、用户在场、无远端依赖、派生闭包完整并可验证；
11. TerminationReceipt 不含用户内容，能诚实区分 logical deletion 与 crypto erasure；
12. 普通卸载默认保留用户数据，显式终止不会被卸载语义替代；
13. L0-L7 全量回归通过，D 盘跨版本/路径/复制/终止真实验收有 exact commit 和收据；
14. 未执行的跨设备或真实终止项目明确标记 `NOT EXECUTED`，不以模拟代替。
