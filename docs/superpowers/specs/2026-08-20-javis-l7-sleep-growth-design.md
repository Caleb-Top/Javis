# Javis L7 睡眠、成长与完整身体设计规格

**日期：** 2026-08-20
**状态：** 正式规格，等待按配套实施计划执行
**父规格：** `docs/superpowers/specs/2026-08-09-javis-life-os-master-design.md`
**执行基线：** `docs/superpowers/plans/2026-08-20-javis-life-os-l2-l8-execution.md`
**范围：** 睡眠编排、成长候选、技能形成与部署、完整身体运行时、旧成长路径治理

## 1. 决策摘要

L7 不允许 Javis 在后台直接训练自己、改写正式源码、安装任意 Python 文件或把审查结果直接变成正式能力。L7 交付的是一个可中断、可恢复、可审计的整理与提议系统：睡眠只处理已治理的事实和收据，成长只生成不可变候选，技能只在受限沙箱形成不可变产物，用户批准的精确产物才可通过原子部署指针进入运行时。

冻结以下架构：

1. `L7Supervisor` 是 L7 唯一编排入口，持有 `SleepCoordinator`、`RunStore` 与 `GrowthStore`；
2. `SkillForge` 只生成内容寻址的不可变 `SkillArtifact`，所有构建和验证都在本地受限沙箱中完成；
3. 正式技能加载只接受 catalog 为 `active` 且与 `SkillDeployment` 指针哈希完全一致的产物；
4. `BodyRuntime` 是身体表达的单写者，消费 `ExpressionIntent v1`、真实播放时序、`BodyCapability` 和用户偏好，生成有界 `ExpressionPlan`；
5. 所有 L7 可变状态、沙箱、收据、候选、产物和部署指针只能位于 `JAVIS_DATA_ROOT`；
6. 更新、迁移、分支和终止由 L8 `ContinuityCoordinator` 负责，L7 只先关闭现有 updater 绕过。

## 2. 目标

1. 在用户明确策略允许且机器条件满足时进入可打断睡眠窗口；
2. 进行索引修复、矛盾检测、保留策略执行、摘要和成长候选生成；
3. 让每个成长结论都绑定真实来源、验证、权限变化、用户决定和回滚依据；
4. 将技能生成、导入、验证、批准、部署和回滚收束为同一条治理链；
5. 把已存在的 Three.js/Avatar 原型接入真实 `PetSurface` 与生命表达主链；
6. 支持真实能量、词边界或音素时序驱动的诚实口型，并尊重 reduced motion；
7. 任何 L7 故障只降低睡眠、成长或 3D 表达，不破坏 L0/L1 身份、会话、打断和关闭。

## 3. 非目标

- 不把生成式反思当作事实；
- 不在睡眠时执行高风险动作、下载模型、调用任意网络或修改正式能力；
- 不在 L7 实现应用版本升级、数据迁移、多设备同步或终止；
- 不允许模型批准、签名或激活自己的候选；
- 不提供通用 Python 源码安装器；
- 不声称模型参数训练等于人格成长；
- 不用动画、服装或口型变化改写身份、关系和权限；
- 不为旧 `sleep_learning`、`SkillCreator`、`SkillManager` 或 `auto_updater` 保留治理例外。

## 4. 当前源码事实与必须关闭的缺口

| 事实 | 当前风险 | L7 决策 |
|---|---|---|
| `core/runtime.py::_register_extension_tools()` 把 `core.auto_updater`、SkillCreator 和 SkillManager 注册为普通 Agent 工具 | 模型可触发正式源码更新、源码安装和删除 | 所有变更型 updater/skill 工具从 Agent registry 移除；只读查询也必须走受权 API |
| `core/skill_manager.py::install()` 从任意 `source_path` 复制 `.py` 到正式 `skills/` 并立即记为 `active` | 来源、许可证、哈希、测试、批准全部可绕过 | 旧安装入口退役；导入只能形成 `candidate` intake 和不可变产物 |
| `core/skill_creator.py::create()` 在正式源码目录写文件，后台审查通过后直接设为 `ACTIVE` | AI 可自写、自审、自激活 | 创建器改为 SkillForge 候选适配器；审查者不能激活 |
| `JarvisRuntime.load_skill()` 直接 `import skills.<id>`，不检查 catalog 状态或内容哈希 | disabled/rejected/被篡改技能仍可加载 | 只从部署指针加载，并同时核对 catalog、artifact 与 release 信任链 |
| `core/skill_catalog.py` 以名称为主键并在内容变化时覆盖同一行 | 候选正文和审批对象可变 | 内容进入不可变版本；catalog 仅索引精确版本和派生状态 |
| `core/auto_updater.py` 暴露 `git pull`，备份和日志写源码树下 `data/`，恢复使用 `ZipFile.extractall()` | 正式源码被运行时改写，存在路径穿越和恢复污染 | L7 立即禁用变更入口；L8 用原生签名、逐项校验和 staging 替代 |
| `kernel/training/sleep_learning.py` 周期性读取 `brain_data`，自动写训练缓冲并用一个全局 marker 标记巩固 | 无用户策略、无逐条证据、原始屏幕语义进入训练、失败仍更新时间 | 默认停用并隔离；只能输出候选，不能更新参数或标记正式巩固 |
| 多个 memory、knowledge、kernel、skill 和 updater 模块把状态写在源码根的 `brain_data/`、`data/`、`config.yaml` | 开发根、安装 runtime 与用户数据混杂 | 新 L7 路径零例外使用 `JAVIS_DATA_ROOT`；旧根只读迁移由 L8 收口 |
| 当前基线的 `app/src/pet/avatar/*` 已有 AvatarSurface、Controller、Mixer、SpeakingEnvelope，`PetSurface.ts` 已直接挂载 3D、性能治理和 fallback | 基础产品接线已经存在，但表达编排仍分散在 PetSurface/Avatar 类中，尚无 L7 `BodyRuntime`、`ExpressionPlan/BodyCapability` 权威边界和完整发布证据 | 接受并验证现有接线，再由 BodyRuntime 收口单写者；不得重复挂载第二套 Avatar，2D/Orb 继续作为受治理降级 |

## 5. 不变量

### 5.1 单写者

- `L7Supervisor` 单写睡眠调度和 L7 运行状态；
- `GrowthStore` 单写成长候选版本、状态事件和 head 指针；
- `SkillForge` 单写不可变 artifact 目录，写入完成后不能原地修改；
- `SkillDeploymentStore` 单写正式部署指针；
- `BodyRuntime` 单写当前身体通道，AvatarSurface、2D 和 Orb 只是 sink；
- 模型、Agent、Sleep job、前端和旧模块只能提出命令或读取投影。

### 5.2 数据根

`resolve_data_root()` 的结果是唯一受支持的数据根。L7 固定目录如下：

```text
JAVIS_DATA_ROOT/
  growth/
    policies/
    runs/
    candidates/
    artifacts/sha256/
    deployments/
    receipts/
    sandbox/
    quarantine/
  body/
    preferences/
    profiles/
    diagnostics/
```

规则：

- 不允许回退到当前工作目录、`G:\Javis`、打包 runtime 目录或模块相对目录；
- 临时文件也必须位于 `growth/sandbox/<run_id>`，完成或崩溃恢复后有界清理；
- 用户明确导出的 artifact 不属于应用状态，可写入用户通过原生文件选择器选择的路径；
- 用户工具对授权 workspace 的输出不属于生命状态，但不得被 SkillForge 当作执行来源；
- 数据根不可用时 L7 进入只读 `degraded`，不能回退写源码树。

### 5.3 热路径和资源

- 对话、语音、打断、工具终态和关闭热路径不执行睡眠 job、SQLite 长事务、模型推理、构建或索引重建；
- 每个 job 有 CPU、内存、磁盘、时长和记录数预算；
- 用户交互、播放、系统电源变化或 shutdown 可抢占睡眠；
- 被抢占 job 只在幂等检查点后重试，不根据进程内布尔值猜测完成。

### 5.4 权限和真值

- 关系熟悉度、睡眠状态和候选评分永不提高权限；
- 只有 L2-L6 权威服务确认的事实、终态收据和删除/保留规则可进入睡眠输入；
- 模型输出只可成为带 `inferred` 来源的候选内容；
- 用户批准绑定精确候选版本和 artifact 哈希，批准后内容变化必须重新审批；
- disabled、rejected、revoked、哈希不符或来源缺失的技能不可加载。

## 6. 总体架构

```text
L2 Memory receipts -----+
L5 Intent receipts -----+--> L7Supervisor
L6 Action receipts -----+      |-- SleepCoordinator --> bounded jobs
Life/Power/User policy -+      |-- RunStore          --> append-only run evidence
                              |-- GrowthStore       --> immutable candidates
                              +-- SkillForge        --> sandbox --> SkillArtifact
                                                        |
user approval + catalog evaluation ---------------------+
                                                        v
                                              SkillDeployment pointer
                                                        |
                                                        v
                                               governed skill loader

ExpressionIntent + playback timing + BodyCapability + preferences
                              |
                              v
                         BodyRuntime
                              |
                    ExpressionPlan single writer
                     /          |           \
                 3D sink     2D sink      Orb sink
```

### 6.1 L7Supervisor

职责：

- 接收用户策略、生命周期、电源、空闲、健康和上游收据；
- 串行决定是否创建 `SleepRun`；
- 维护同一 identity/instance 仅一个 active sleep run；
- 对 job 施加预算、取消、检查点和恢复；
- 将 job 输出路由为索引收据、保留收据、摘要或成长候选；
- 在自身故障时停止新 job，不影响会话与关闭。

它不直接执行技能、动作、模型下载、部署或身体渲染。

### 6.2 SleepCoordinator

职责：根据精确 `SleepPolicy` 和实时 preflight 创建并驱动 `SleepRun`。允许的首版 job kind：

- `index_verify`：验证并修复可重建索引；
- `contradiction_scan`：生成矛盾记录，不自动裁决用户事实；
- `retention_apply`：执行已生效的保留/删除策略并写逐条收据；
- `summary_build`：从仍有效的最小事实生成次日摘要；
- `growth_propose`：从足量真实证据形成候选；
- `artifact_revalidate`：重新验证已部署 artifact 的哈希和兼容性，不自动升级。

不允许的 job：模型下载、参数训练、git 操作、源码写入、技能激活、高风险 Action、未授权网络、原始媒体上传。

### 6.3 RunStore

保存 immutable run header、append-only state transition、job checkpoint 和 receipt reference。投影可以重建，SQLite WAL/索引损坏不能改变源收据。每次写入使用 run revision CAS 和幂等键。

### 6.4 GrowthStore

保存逻辑 `candidate_id` 下的不可变版本链、append-only 状态事件、批准/拒绝/撤销决定和 head 指针。正文、测试要求、权限差异或回滚方案任何变化都创建新版本。

### 6.5 SkillForge

职责：

1. 将本地导入、生成式草稿或程序性候选复制到 intake；
2. 拒绝绝对成员路径、`..`、链接、reparse point、设备文件、大小/数量超限和未声明文件；
3. 规范化清单、来源、许可证、依赖、SBOM 和 capability 请求；
4. 在无网络、无密钥、无用户数据、受限 token 与 Windows Job Object 约束的沙箱中构建和测试；
5. 输出内容寻址、只读的 `SkillArtifact` 与验证收据；
6. 将结果交回 GrowthStore，绝不直接改 catalog 为 active 或切换部署。

原生受限沙箱不可用时必须失败关闭，不得退化为普通 `subprocess`。

### 6.6 BodyRuntime

职责：

- 验证 `BodyCapability` 与 Avatar manifest 哈希；
- 将 `ExpressionIntent v1`、播放生命周期和可用时序映射为 `ExpressionPlan`；
- 统一口型、眼神、眨眼、姿态、动作和颜色通道；
- 丢弃过期 revision，按 request/generation 精确打断；
- 根据性能、reduced motion 和能力缺失选择确定性降级；
- 验证并接管现有 AvatarSurface 到 `PetSurface` 的真实产品接线，避免出现并行 writer。

BodyRuntime 不读取长期记忆正文、不定义情绪、不写 LifeSnapshot。

## 7. 共同合同规则

所有 L7 合同：

- 使用严格 schema，未知字段拒绝；
- 时间为 UTC RFC3339，时长为非负整数毫秒；
- ID 由权威服务端生成，客户端不能声明 identity、owner 或批准者；
- 递归拒绝 secret、token、password、原始音频、原始屏幕、隐藏推理和任意源码正文进入普通日志；
- canonical JSON 使用 UTF-8、排序 key 和固定数字格式；
- `content_hash` 为除自身字段外 canonical payload 的 SHA-256；
- 持久对象包含父执行计划要求的 identity、instance、owner、audience、privacy、retention、provenance 和 source IDs；
- 命令携带 `idempotency_key`，状态变更携带 `expected_revision`。

## 8. SleepPolicy 与 SleepRun

### 8.1 SleepPolicy v1

```text
schema_version, policy_id, identity_id, owner_subject_id,
revision, enabled, approval_id, approved_at_utc,
schedule_mode(manual|quiet_hours|idle), timezone,
quiet_start_local, quiet_end_local, idle_min_seconds,
require_ac_power, minimum_battery_percent,
maximum_cpu_percent, minimum_free_bytes,
network_policy(deny|explicit_job_grants),
allowed_job_kinds, max_run_seconds, max_job_seconds,
max_input_records, max_output_bytes, wake_on_user_activity,
effective_at_utc, expires_at_utc, created_at_utc,
privacy_class, retention_class, provenance, content_hash
```

规则：

- `enabled` 不是永久授权，每次 run 仍执行实时 preflight；
- quiet hours 明确 timezone 和夏令时折叠策略，同一窗口不能重复调度；
- `network_policy=explicit_job_grants` 仍要求具体 job 的短期 grant，默认 deny；
- 策略变更产生新 revision，正在运行的 run 继续绑定旧 revision，收紧安全项可立即取消；
- 没有用户批准、策略过期或数据根不健康时不得调度。

### 8.2 SleepRun v1

`SleepRun` 是从不可变 header 与 append-only transition 投影出的当前视图：

```text
schema_version, run_id, policy_id, policy_revision,
identity_id, instance_id, owner_subject_id,
trigger_kind, trigger_event_id, idempotency_key,
state, revision, scheduled_at_utc, started_at_utc, ended_at_utc,
preflight_snapshot_hash, source_checkpoint_ids,
job_specs, current_job_id, completed_job_ids,
receipt_ids, candidate_version_ids,
cancel_reason_code, failure_reason_code,
resource_usage, created_at_utc, updated_at_utc,
privacy_class, retention_class, provenance, content_hash
```

`job_specs` 只含 kind、预算、输入 checkpoint 和预期输出类型，不含记忆正文。每个 job receipt 记录输入集合哈希、输出集合哈希、处理数量、跳过原因和错误码。

## 9. 睡眠状态机

```text
scheduled
  -> preflight
      -> skipped
      -> running
          -> cancelling -> cancelled
          -> checkpointing -> running
          -> committing -> completed
          -> failed
          -> timed_out
```

规则：

- `scheduled -> preflight` 需要调度幂等键唯一；
- preflight 检查用户策略、空闲、电源、电量、磁盘、CPU、运行时健康、数据根和 shutdown 状态；
- `skipped` 是带 reason code 的终态，不算失败；
- 用户输入、语音、Action 开始或 shutdown 触发 `cancelling`；
- `committing` 只提交已形成的逐 job 收据和候选，不执行部署；
- 崩溃时 `running/checkpointing/committing` 在重启后投影为 `interrupted`，验证 checkpoint 后创建恢复 transition；不能直接标成 completed；
- terminal run 不可重开，重试创建新 run 并引用原 run。

## 10. 不可变 GrowthCandidate 版本

### 10.1 GrowthCandidate v1

每个版本是独立不可变对象：

```text
schema_version, candidate_id, candidate_version_id,
version_number, previous_version_id, previous_content_hash,
identity_id, instance_id, owner_subject_id,
candidate_kind(fact|relationship|procedure|expression_profile|skill|system_change),
title, bounded_summary, source_evidence_ids, source_evidence_hash,
minimum_evidence_count, evidence_window,
expected_benefit, applicability, assumptions,
risk_level, risk_reasons, permission_delta,
validation_plan, acceptance_thresholds,
rollback_plan, rollback_artifact_refs,
requires_user_approval, created_by,
created_at_utc, expires_at_utc,
privacy_class, retention_class, provenance, content_hash
```

`candidate_version_id` 由 `candidate_id + version_number + content_hash` 生成。正文、来源集合、风险、测试、权限差异、适用环境或回滚字段变化都必须创建下一版本。禁止 UPDATE 不可变 candidate 表。

### 10.2 CandidateTransition v1

状态不写回 candidate 正文，而以 append-only transition 保存：

```text
transition_id, candidate_version_id, from_state, to_state,
expected_revision, actor_kind, actor_id, decision_id,
reason_code, receipt_ids, artifact_id,
created_at_utc, idempotency_key, content_hash
```

用户决定必须由本地已认证用户上下文产生，模型只能是 `proposer`，不能是 `approver`。

### 10.3 候选状态机

```text
proposed
  -> evidence_ready
      -> sandboxing
          -> validation_failed -> proposed(new version only)
          -> validated
              -> awaiting_approval
                  -> rejected
                  -> expired
                  -> approved
                      -> deploying
                          -> deployment_failed
                          -> active
                              -> revoked
                              -> rolling_back -> rolled_back
```

规则：

- `validation_failed -> proposed` 不能复用原版本，修正后创建新版本；
- 任何 `permission_delta` 扩大都必须显式用户批准；
- `approved` 绑定 candidate version、artifact hash、测试收据和权限差异；
- `active` 只在部署指针切换并回读验证后成立；
- 撤销候选不会删除证据，部署回滚不会恢复已撤销权限；
- 过期、rejected、revoked 版本不能再次部署，需新版本。

## 11. SkillArtifact 与 SkillDeployment

### 11.1 SkillArtifact v1

```text
schema_version, artifact_id, artifact_kind,
candidate_version_id, skill_name, skill_version,
payload_sha256, manifest_sha256, total_bytes, file_count,
entrypoints, source_manifest, source_archive_sha256,
license_spdx, attribution_refs, sbom_ref,
dependency_lock_hash, required_capabilities,
sandbox_policy_version, build_receipt_id,
test_receipt_ids, compatibility_range,
created_at_utc, created_by_forge_version,
privacy_class, retention_class, content_hash
```

`artifact_id` 是完整 manifest 与 payload 的内容地址。目录写入采用 temp + fsync + rename，完成后只读；同一 ID 出现不同字节时进入 quarantine 并触发恢复。

### 11.2 SkillDeployment v1

部署记录不可变，`deployments/active.json` 是带 revision 的原子指针：

```text
schema_version, deployment_id, skill_name,
artifact_id, artifact_payload_sha256,
candidate_version_id, catalog_record_id,
catalog_status_required(active), approval_id,
required_capabilities, granted_capability_refs,
previous_deployment_id, deployed_at_utc,
deployed_by, rollback_receipt_id,
revision, content_hash
```

加载必须同时满足：

1. 指针 schema、revision 和哈希有效；
2. artifact 路径位于 `JAVIS_DATA_ROOT/growth/artifacts/sha256`；
3. 目录无 symlink/reparse point，payload 哈希匹配；
4. catalog 精确版本状态为 `active` 且 content hash 匹配；
5. 用户批准绑定同一 candidate/artifact；
6. capability grant 仍有效；
7. runtime/release 兼容范围成立。

任一条件失败则该技能不加载，运行时保留 always-on 安全工具并发出不含路径正文的诊断。不得回退导入 `skills/<name>.py`。

### 11.3 旧路径治理

- `SkillManager.install/enable/uninstall` 不再注册为 Agent 工具；
- `SkillCreator.create/improve/import/delete` 不再操作正式 `skills/`；
- 外部源码通过用户原生选择器进入只读 intake，先复制、再哈希，源路径不保存为执行路径；
- release 内置技能通过签名 release manifest 初始化为受信 artifact，同样通过部署指针加载；
- `discover_skills()` 不 import 未治理模块；发现阶段只解析静态 manifest；
- catalog 的 disabled/rejected/revoked 状态在下一次调用前生效，不能依赖重启。

## 12. BodyCapability 与 ExpressionPlan

### 12.1 BodyCapability v1

```text
schema_version, capability_id, body_id, body_version,
manifest_sha256, renderer_kind, supported_channels,
channel_ranges, viseme_set, supported_gestures,
supported_postures, supports_gaze, supports_blink,
supports_energy_mouth, supports_word_timing, supports_phonemes,
interrupt_latency_budget_ms, performance_tiers,
reduced_motion_substitutions, fallback_body_id,
license_ref, created_at_utc, content_hash
```

BodyCapability 只描述可做什么，不描述 Javis 当前心情、关系或权限。

### 12.2 ExpressionPlan v1

```text
schema_version, plan_id, body_id, capability_id,
source_expression_id, source_expression_revision,
source_snapshot_revision, request_id, playback_generation,
generated_at_utc, expires_at_utc, priority, interrupt,
timing_source(none|energy|word|phoneme),
channel_tracks, reduced_motion_applied,
unsupported_channels, fallback_actions,
transition_ms, content_hash
```

约束：

- track 数、关键帧数、时长、幅度和插值类型严格有界；
- phoneme 仅在真实 TTS timing 可用时声明，能量包络不能伪装成音素口型；
- request/generation 终止后，旧 plan 不能恢复；
- 未支持动作使用 `fallback_actions`，不得自行推断夸张表达；
- reduced motion 开启时禁用大幅位移、摇摆和快速闪烁，保留可理解状态；
- 个性动作 profile 必须来自 active `expression_profile` candidate，且不覆盖安全/打断通道。

## 13. BodyRuntime 产品接线

正式链路固定为：

```text
LifeStateBridge ExpressionIntent
  + request-scoped playback lifecycle
  + native playback energy/word/phoneme timing
  + selected BodyCapability
  + PetPreferences/reduced motion
      -> BodyRuntime
      -> ExpressionPlan
      -> AvatarController/ExpressionMixer/AvatarSurface
      -> deterministic 2D or Orb fallback
```

要求：

- `PetSurface` 创建真实 body host，不再只有测试代码引用 Avatar 模块；
- 初始化异步失败时 2D 立即可用，不能留下空白窗口；
- 3D、2D、Orb 同时只能有一个可见 sink 和一个表达 writer；
- speaking start/energy/stop 与 request ID、playback generation 对齐；
- barge-in 在延迟预算内清空口型和说话动作；
- context loss、加载失败和性能降级保留交互、拖动、打开 Live 与状态文本；
- 身体选择和 reduced motion 偏好存入 `JAVIS_DATA_ROOT/body/preferences`，不以 localStorage 作为权威事实。

## 14. API、事件与用户决定面

### 14.1 后端只读与命令 API

- `GET /api/life/l7/sleep/policy`
- `PUT /api/life/l7/sleep/policy`，要求本地授权与 expected revision
- `POST /api/life/l7/sleep/run`，仅手动请求，不绕过 preflight
- `POST /api/life/l7/sleep/run/{id}/cancel`
- `GET /api/life/l7/sleep/runs`
- `GET /api/life/l7/growth/candidates`
- `GET /api/life/l7/growth/candidates/{version_id}`
- `POST /api/life/l7/growth/candidates/{version_id}/decision`
- `POST /api/life/l7/growth/deployments/{id}/rollback`
- `GET /api/life/l7/body/capabilities`

技能导入、部署、回滚和候选批准必须使用 scope 精确的短期 runtime capability。模型不可见批准和部署命令工具。

### 14.2 事件

- `life.sleep.scheduled|started|cancelled|completed|failed`
- `life.growth.candidate.created|validated|decided`
- `life.skill.artifact.created|quarantined`
- `life.skill.deployment.changed|rolled_back`
- `life.body.plan.applied|degraded`

事件 payload 仅含 ID、状态、reason code、计数、revision 和哈希摘要，不含候选正文、源码、路径、记忆内容或媒体。

### 14.3 用户体验

设置面必须让用户完成：

- 查看并修改睡眠时间、空闲、电源、网络和资源边界；
- 看见当前/最近 sleep run、跳过或失败原因；
- 审阅候选来源摘要、风险、权限变化、测试与回滚；
- 对精确版本批准、拒绝、撤销或回滚；
- 选择身体、性能档和 reduced motion；
- 看见技能验证失败或身体降级，而不是被静默替换。

## 15. 故障与恢复

| 故障 | 行为 |
|---|---|
| 数据根不可写 | 停止调度和候选写入，进入只读 degraded |
| RunStore 损坏 | 从 append-only 收据恢复；无法验证则隔离，不重跑有副作用 job |
| 睡眠被用户唤醒 | 有界取消、写 checkpoint、立即让出资源 |
| 模型或索引服务失败 | 仅对应 job 失败，摘要/候选不冒充完成 |
| sandbox 不可用 | SkillForge 失败关闭，不使用普通进程替代 |
| artifact 篡改 | quarantine、禁用部署、回滚到验证通过的 previous deployment |
| catalog/部署不一致 | 拒绝加载并保留基本会话能力 |
| 3D/WebGL 失败 | 回退 2D/Orb，BodyRuntime 清理旧 writer |
| 播放 timing 缺失 | 使用诚实 energy 或静态 speaking，不声明 phoneme |
| shutdown 竞争 | 不接收新 run/forge；等待有界 checkpoint 后由 L0 owned shutdown 继续 |

## 16. 隐私与安全

- Sleep job 按 owner、audience、privacy、retention、TTL 和 deletion tombstone 在查询前过滤；
- 原始屏幕、相机、音频、完整对话和 secret 不进入候选或 forge 日志；
- sandbox 不继承环境 token、用户 HOME、浏览器 profile、SSH/Git 凭据或 `JAVIS_DATA_ROOT` 全量访问；
- artifact 可以包含技能实现，但普通 API、模型上下文和日志只暴露摘要与哈希；
- 外部 source license 未批准、清单不完整或 hash 不匹配时停在 quarantine；
- 安装/部署不授予 capability，capability 仍由 L6 授权；
- updater、git、源码写入和模型下载不属于 sleep 或 skill capability；
- 身体动作不编码秘密，不根据私人记忆在访客面前泄露关系信息。

## 17. 验收场景

### 17.1 睡眠

1. 用户未启用策略时，积累再多经历也不自动 sleep；
2. 安静时段但使用电池且策略要求 AC 时，run 以明确 reason `ac_required` 跳过；
3. sleep 中用户说话，run 进入 cancelling，语音和会话立即获得资源；
4. 进程在 committing 时崩溃，重启后不会重复删除、重复建 candidate 或伪报 completed；
5. 次日摘要不包含已删除、过期、其他 subject 私有或未确认事实。

### 17.2 成长与技能

1. 修改已验证 candidate 的一个字符会产生新 version，旧批准不能复用；
2. 任意 source path 安装、SkillCreator 自建和后台 review 都不能直接成为 active；
3. disabled/rejected skill 即使文件仍在也无法通过 `load_skill`；
4. sandbox 无网络、无密钥、无源码根写权限，并在超时后清理；
5. artifact 激活后被篡改，下一次加载拒绝并可回滚；
6. 回滚恢复 exact previous artifact，不恢复已撤销 grant。

### 17.3 身体

1. 产品 Pet 模式实际创建 AvatarSurface，而不是只在单测中存在；
2. 3D 首帧失败时 2D 立即可见且仍可打开 Live；
3. 真实 TTS phoneme timing 可驱动 viseme，无 timing 时不伪称完整音素口型；
4. barge-in 后旧 playback generation 的关键帧不能复活嘴部动作；
5. reduced motion 在 3D、2D 和 Orb 都一致生效；
6. active expression profile 能改变有界动作节奏，但不能改变 Identity、关系或权限。

## 18. 自动化证据门

1. **合同门：** strict schema、未知字段、canonical hash、不可变版本、时间和预算边界；
2. **治理门：** creator、manager、catalog、loader、updater 的所有绕过路径均有否定测试；
3. **状态机门：** 正常、取消、崩溃、重放、乱序、并发和旧终态；
4. **沙箱门：** 路径穿越、链接/reparse、zip bomb、网络、secret、进程树和资源预算；
5. **数据根门：** 源码根只读运行，L7 不产生任何新文件；
6. **Body 门：** 真实 PetSurface 接线、非空画面、fallback、context loss、口型和打断；
7. **回归门：** L0/L1 身份、会话、语音、打断、关闭和安装基线；
8. **真实体验门：** D 盘安装环境跨夜、重启、候选审阅、回滚和低配降级。

## 19. 出口标准

L7 只有同时满足以下条件才完成：

1. `L7Supervisor(SleepCoordinator/RunStore/GrowthStore)` 成为生产唯一入口；
2. sleep 受用户策略和实时 preflight 约束，可中断、可恢复、可解释；
3. 旧 `sleep_learning` 不再后台训练或标记巩固；
4. GrowthCandidate 是不可变版本，批准绑定精确版本；
5. SkillForge 产物内容寻址、沙箱验证且不可原地修改；
6. SkillCreator、SkillManager、catalog 和 runtime loader 不存在直达 active 的旁路；
7. `core.auto_updater` 不再是 Agent 可调用的变更能力，`extractall` 不再用于正式恢复；
8. 所有 L7 可变状态只在 `JAVIS_DATA_ROOT`；
9. BodyRuntime 已接入产品 PetSurface，并覆盖口型、动作、打断、reduced motion 与降级；
10. 用户可审阅、拒绝、批准、撤销和回滚成长；
11. 任一 L7 子系统失败时，身份、会话、语音、打断和关闭仍可用；
12. 自动化、构建和 D 盘真实体验证据均有可追溯收据，未执行项明确标记 `NOT EXECUTED`。
