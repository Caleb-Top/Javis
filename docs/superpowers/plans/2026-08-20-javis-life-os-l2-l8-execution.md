# Javis Life OS L2-L8 综合执行计划

**日期：** 2026-08-20  
**状态：** 执行中  
**集成基线：** `main` at `adf9d3e`  
**正式源码：** `G:\Javis`

## 1. 目的

本计划承接已合入主线的 L0-A 与 L1，并把 L0-B、L2-L8 组织为一条可验证的生产实现链。每个阶段必须同时交付合同、权威服务、生产接线、降级、自动化和用户验收模板；文件存在、接口可调用或模型能够描述均不等于阶段完成。

事实优先级固定为：用户最新决定 -> 当前源码与测试 -> 已审阅总体规格 -> 阶段规格 -> 本执行计划 -> 历史实现。

## 2. 不变量

1. Javis 身份由 LifeService 与身份宪章持有，模型、Agent、身体、关系和设备不能重写身份。
2. 每类长期事实只有一个写入权威；旧系统只能作为受隔离迁移源。
3. 模型只读受预算快照，不能写内在状态、记忆、关系、权限、承诺结果或成长结论。
4. 所有事实带来源、主体、受众、隐私、保留、时间和有效期；过期即 unknown，不继续陈述为真。
5. 授权由身份、能力令牌、对象 ACL、用户 grant 与动作风险共同决定；关系永不提高权限。
6. 原始音频、屏幕帧、摄像头帧和未最小化工具负载默认不持久化、不进入 EventBus、不发送云端。
7. 删除先封禁召回，再完成派生闭包；纠错使用 supersede，不静默改写历史证据。
8. 所有异步投影可重放、幂等、有界；热路径不得同步执行 SQLite、模型推理、资源下载或索引重建。
9. 每个阶段故障只降低该能力，不破坏身份、会话、打断、关闭和恢复基线。

## 3. 权威服务边界

| 领域 | 单写者 | 证据源 | 只读消费者 |
|---|---|---|---|
| 身份、谱系、生命周期 | `LifeService` | Identity/Lineage/LifeJournal | 身体、模型、UI、后续服务 |
| 当前临场状态 | L1 deterministic reducer | Governed observations | 身体、模型、UI |
| 身体表达 | `AvatarController` | `ExpressionIntent v1` | Three.js/2D/Orb handles |
| 自传与共同记忆 | `MemoryService` | ConversationStore + terminal receipt | Prompt、日记、关系投影、UI |
| 主体与关系历史 | `RelationshipService` | Memory evidence + explicit confirmation | Prompt、UI、建议预算 |
| 环境事实 | `EnvironmentService` | Governed local sources | Prompt、注意选择器、UI |
| 意图、承诺与目标 | `IntentService` | 用户请求 + verified action result | Planner、Prompt、UI |
| 行动授权与收据 | `ActionService` | Policy decision + tool execution | IntentService、审计、UI |
| 睡眠与成长候选 | `GrowthService` | 已确认记忆与执行证据 | 用户审阅、技能沙箱 |
| 升级、迁移与终止 | `ContinuityService` | Release manifest + lineage + user decision | Runtime、installer、sync UI |

## 4. 阶段顺序

### B0-B1：基础身体

固定 Three.js/VRM 依赖与离线资产合同；实现透明 Surface、程序化 Lightform、Pet 集成、表达仲裁、诚实口型、打断、性能降级、预览和 D 盘验收模板。身体不得成为第二状态权威。

### L2：自传记忆

1. 服务端生成 `AccessContext`，客户端不能声明主体。
2. `ConversationStore` 保留原始对话证据，terminal receipt 触发幂等经历候选投影。
3. `MemoryService` 统一 Episode、Journal、SharedMemory、DerivationEdge、DeletionRequest、FTS 与召回预算。
4. 失败、取消、来源缺失和未验证结果不能成为共同经历。
5. 日记是生成性反思，不能自动升级为用户事实。
6. 旧 Brain/episodic/全局 FTS 进入 `unowned/quarantined`，停止新增写入。

出口：跨天与重启后可从真实证据识别共同经历；删除标记在数据库、FTS、缓存、提示词、重启和重建索引后均为零命中。

### L3：关系与多人边界

1. 建立 `Subject`、`IdentityBinding`、`SessionParticipant`、`UserModelClaim` 与 `RelationshipEvent`。
2. 区分 primary、authorized、visitor、unknown；识别失败进入隔离 guest session。
3. private/shared/public 在 SQL 查询前执行 owner/audience/ACL 过滤。
4. 推断偏好与显式确认分离；共同记忆 MVP 需要参与者明确确认。
5. 熟悉度只作为可解释投影，不能进入权限判断。

出口：多人并发、会话 ID 猜测、识别失败、关系变化和导出/删除均不能跨主体泄漏。

### L4：Computer Twin

1. 新增 capability scopes：`environment.read`、`environment.observe`、`workspace.read`、`screen.capture`、`camera.capture`。
2. capability 仅认证本地客户端；采集仍需独立 `EnvironmentGrant`，包含范围、用途、once/session/persistent、到期、前台限制与外发许可。
3. `EnvironmentObservationV1` 经最小化与分类后进入有界 reducer，产生带 TTL 的 `EnvironmentSnapshotV1`。
4. 原始帧 TTL 为零；OCR/对象事实默认 10 秒且不持久化；工作区和系统健康使用独立 TTL。
5. 模型只读 `JAVIS_ENVIRONMENT_CONTEXT_V1`，每回合刷新、严格预算，不含正文、完整路径、OCR 全文或命令行。

出口：授权撤销立即停止采集和投影；过期事实变 unknown；本地/云模型均无法绕过 media egress 边界。

### L5：意图与承诺

1. 用 `IntentRecord` 区分请求、建议、承诺和目标；每项绑定主体、会话、证据与截止条件。
2. 明确状态机：candidate -> accepted -> active -> waiting/blocked -> verifying -> completed/failed/cancelled/expired。
3. Planner 只能提出步骤；`IntentService` 单写承诺状态，工具结果必须经验证器后才能完成目标。
4. 重启只恢复有证据、未过期、可安全继续的承诺；瞬时思考链不持久化。
5. 主动建议使用可见预算、冷却和静默时段，不以关系或模型热情突破限制。

出口：Javis 能说明承诺来源、当前状态、阻塞与验证依据，并能安全取消或恢复。

### L6：行动与恢复

1. `ActionRequest` 经 capability、主体 ACL、grant、风险、幂等键与用户确认产生 `AuthorizationDecision`。
2. 每次执行形成 `ActionReceipt`：计划、参数摘要、授权、开始/终态、验证、影响范围、撤销与恢复信息。
3. 高风险动作默认 preview/confirm；不可逆动作必须显式标注且不能由关系或模型自行授权。
4. 中断、超时、重启和部分成功进入 reconciliation；安全模式禁止新增高风险动作但保留查询、导出和恢复。
5. 工具必须在最小工作区根和路径白名单内执行，屏幕/摄像头不能沿用低风险只读默认放行。

出口：动作可停、可审计、可验证、能恢复或清楚声明不可撤销；旧终态不能完成新请求。

### L7：睡眠与成长

1. `SleepWindow` 只在用户允许、空闲、供电和安全条件成立时进入。
2. 睡眠任务仅处理索引修复、矛盾检测、遗忘执行、摘要和成长候选；不执行高风险动作、不下载模型、不修改正式能力。
3. `GrowthCandidate` 必须包含来源证据、预期收益、风险、测试、回滚、权限变化与用户决定。
4. 技能形成在隔离沙箱完成，经过测试和用户批准后才能安装；模型不能自签名升级。
5. 身体扩展完整口型与个性动作，但继续只消费受治理意图并尊重 reduced motion。

出口：Javis 会休息和整理，成长过程可审阅、可拒绝、可回滚，正式能力不发生静默漂移。

### L8：长期连续性

1. `MigrationManifest` 覆盖 schema、身份、谱系、记忆、ACL、密钥引用、资产、模型引用与校验和。
2. 迁移执行 prepare -> copy -> verify -> activate -> checkpoint；失败回滚旧权威，不只修改路径指针。
3. 设备复制默认创建显式 branch；合并采用领域策略和冲突记录，不伪装成同一实例并发写入。
4. 同步端只接收加密、最小化、带主体和删除 tombstone 的记录；远端不能提高本地权限。
5. 用户拥有导出、暂停、分支、撤销设备、删除和终止权；终止要生成无内容证明并完成密钥与派生删除闭包。

出口：换版本、路径、模型和设备仍可证明身份连续；冲突、分支与终止状态真实可见。

## 5. 共同合同

所有新持久对象至少包含：

`schema_version`、`record_id`、`javis_identity_id`、`instance_id`、`owner_subject_id`、`participant_ids`、`audience`、`source_event_ids`、`created_at_utc`、`updated_at_utc`、`expires_at_utc`、`privacy_class`、`retention_class`、`state`、`provenance`、`content_hash`。

公共规则：

- ID 由服务端生成；外部 ID 先规范化并做对象授权。
- 时间为 UTC RFC3339；排序使用服务端 sequence/revision，不使用客户端墙钟决定新旧。
- 所有写命令带幂等键；重放不得制造第二条事实、承诺或动作。
- 序列化严格拒绝未知字段；schema 变更必须版本升级和迁移测试。
- 内容、索引、缓存、提示词和同步记录均进入 derivation graph。

## 6. 自动化证据门

每阶段必须覆盖：

1. 合同：严格 schema、范围、枚举、版本、未知字段和时间。
2. 权限：主体、受众、capability、grant、风险和对象 ACL 矩阵。
3. 真值：来源缺失、失败、取消、过期、冲突和推断不得升级为确认事实。
4. 并发：重复、乱序、重放、关闭竞争、删除竞争和旧终态。
5. 隐私：秘密、媒体、正文、路径、命令行和跨主体信息不进入错误层。
6. 恢复：崩溃、损坏、索引重建、迁移回滚、设备分支和安全模式。
7. 生产接线：真实会话、工具、前端与 runtime 路径，禁止只测孤立类。
8. 降级：任一后续服务失败时 L0/L1 身份、对话、打断和关闭仍可用。

## 7. 提交与发布顺序

1. 每阶段按 `contracts -> store/service -> adapters/projectors -> API/context -> UI -> release evidence` 提交。
2. 共享文件只能由当前集成工作包修改；其他工作包通过已提交接口消费。
3. 每个提交执行 `git diff --check`、定向测试和关联回归；阶段出口执行全量 Python、App、TypeScript、Vite 和可用的 Rust/Tauri 门。
4. GitHub 只接收工作树干净、提交可追溯的 `main`。
5. 安装包和 D 盘验收从精确提交构建，记录 SHA-256；未执行项保持 `NOT EXECUTED`。

## 8. 当前台账

| 工作包 | 状态 |
|---|---|
| L0-A / L1 主线集成 | 完成，`80813d6c` |
| L0-B 精确 3D 依赖合同 | 完成，`adf9d3e` |
| L0-B Manifest 至发布闭环 | 执行中 |
| L2-L3 规格与实施计划 | 编写中 |
| L4 规格与实施计划 | 待编写，安全审计完成 |
| L5-L8 规格与实施计划 | 审计中 |
| L2-L8 产品实现 | 待前序合同提交后依赖执行 |
