# Javis L0-A 生命内核设计规格

**日期：** 2026-08-09

**状态：** 总体方向已批准，待子规格书面审阅

**父规格：** `docs/superpowers/specs/2026-08-09-javis-life-os-master-design.md`

**范围：** 身份宪章、实例谱系、启动连续性、最小生命周期、统一生命事件信封、异步生命事件日志和只读状态出口

**非授权声明：** 本文不授权实施，不修改 `G:\Javis` 正式源码，不触发 D 盘安装验收

## 1. 目标

L0-A 建立一个不依赖具体模型、界面、对话或 Agent 的最小生命主体，使 Javis 在首次启动、重启、模型切换和界面切换后仍然是同一个 Javis，并为基础 3D 身体提供唯一、稳定、可解释的状态与表达来源。

L0-A 不追求完整情绪、长期记忆、用户画像、Computer Twin 或自主成长。它先解决五个基础问题：

1. 谁是 Javis，谁是用户，二者不能混为同一身份记录；
2. 当前安装实例从哪个身份继承而来；
3. Javis 当前处于启动、清醒、安静、退化还是恢复状态；
4. 现有运行事件如何成为有因果、顺序、隐私和保留规则的生命事件；
5. 后端、Live、Code、Pet 和未来 3D 身体如何读取同一个生命快照。

## 2. 当前源码事实

### 2.1 已有基础

- `core/events.py` 已提供同步 `EventBus`、有界内存历史和 `Event` 信封；
- 现有 `Event` 已有 `id`、`type`、`payload`、`source`、`timestamp`、`schema_version`、`correlation_id`、`causation_id` 和 `sequence`；
- `core/runtime.py` 已支持 `register_subsystem()`、`close()` 和 `aclose()` 生命周期；
- `ConversationHub`、`AgentRunStore`、ToolRegistry、Memory 与 Skill 已产生真实运行事件；
- `core/agent.py` 已触发 `USER_PROMPT_SUBMIT`；
- `ConversationHub` 已在生产路径使用 `AgentRunRecorder`；
- `app/src/state/RuntimeStateCoordinator.ts` 已折叠 Live、Code、语音、工具和权限状态；
- `BackendClient` 已通过统一会话 WebSocket 接收请求和活动事件。

### 2.2 真实缺口

- `CoreMemoryKernel` 只尝试从不存在的 `brain_data/rules/core_identity.md` 读取用户名；
- 现有 `CoreIdentity` 表达的是“用户是谁”，不是“Javis 是谁”；
- 身份、用户、实例、谱系、宪章和生命版本没有独立数据模型；
- `SessionEventStore` 只持久化事件 ID、类型、来源、时间和 payload，丢弃 schema、correlation、causation 与 sequence；
- `SessionEventStore` 作为通配订阅者在同步 EventBus 热路径执行 SQLite I/O；
- 原始 payload 默认进入全文索引，隐私范围过宽；
- 后端 EventBus 事件、ConversationHub 的 `activity.*` 和前端 RuntimeSnapshot 是三种并行语义；
- 前端快照只有 8 个表面状态，不能表达身份版本、生命周期、降级原因与状态来源；
- 没有统一的生命快照 API、事件游标和恢复模式；
- 现有模型、Agent 或提示词可以表现人格，但没有独立于模型的身份事实源。

## 3. 方案比较

### 3.1 方案 A：增量生命内核门面

在现有 Runtime、EventBus、ConversationHub、SessionEventStore 和前端状态协调器之上增加独立的 LifeKernel 门面、身份存储、事件适配器和异步生命日志。

优点：

- 不重写现有运行主链；
- 可按事件类型逐步接线；
- 失败时可退回现有 RuntimeSnapshot；
- 身份和模型彻底解耦；
- L0-B、L1 和后续记忆系统有稳定契约。

缺点：

- 初期存在旧事件和生命事件两种协议；
- 需要维护明确的映射层；
- 旧事件库暂时仍保留，需防止双写造成性能问题。

### 3.2 方案 B：重写 Runtime、事件库和记忆入口

用新的生命运行时一次性替代现有 EventBus、SessionEventStore、RuntimeStateCoordinator 和 CoreMemoryKernel。

优点是最终结构看起来更统一。缺点是改动面巨大，会同时危及语音、打断、会话、工具、发布基线和 D 盘安装结果，且无法分阶段验证。

此方案不采用。

### 3.3 方案 C：仅用系统提示词保存身份

把名字、人格、价值观和关系写入 prompt，让所有模型按提示扮演 Javis。

优点是工作量小。缺点是模型切换、上下文截断、提示注入和记忆污染都会改变身份，也无法形成可验证谱系与恢复机制。

此方案明确排除。

### 3.4 正式决策

采用方案 A。L0-A 是对现有系统的约束性门面和连续性根，不是第二套 Agent 或第二套记忆系统。

## 4. 架构边界

L0-A 分为七个职责单一的组件。

### 4.1 IdentityConstitutionStore

职责：创建、读取、验证和迁移 Javis 身份宪章。

它依赖用户数据根目录和版本迁移器，不依赖模型、Agent、网络或向量数据库。

它不负责保存当前状态、用户画像、聊天记录或模型配置。

### 4.2 InstanceLineageStore

职责：维护当前安装实例、父实例、谱系和连续性检查点。

它区分：

- `identity_id`：同一个 Javis 的长期身份；
- `instance_id`：一次具体安装实例；
- `lineage_id`：一组具有共同来源的实例谱系；
- `parent_instance_id`：迁移或复制来源；
- `generation`：谱系代次。

L0-A 只记录谱系，不实现多设备同步和冲突合并；后者属于 L8。

### 4.3 MinimalLifeState

职责：维护 L0 阶段最小生命周期，不提前实现 L1 的完整内稳态。

状态限定为：

- `booting`：正在读取身份和验证连续性；
- `awake`：身份完整、运行时可用；
- `quiet`：可用但当前没有活动意图；
- `engaged`：存在用户交互或有效请求；
- `degraded`：部分能力不可用但身份和只读状态可用；
- `recovering`：身份、事件日志或连续性检查失败，禁止危险写操作；
- `stopping`：正在形成关闭检查点；
- `offline`：后端主体不可达，仅前端外壳可用。

注意、认知负荷、好奇、受阻和功能性情绪属于 L1，不在 L0-A 写死。

`offline` 是前端对“生命主体当前不可达”的投影状态，不要求已离线的后端自行发布。后端持久生命周期以最后检查点和异常关闭标记为准；重新连接后由最新生命快照替换前端 offline 投影。

### 4.4 LifeEventAdapter

职责：把已证明存在的运行事件映射为生命观察和生命周期事件。

它只转换语义，不执行 SQLite I/O、不调用模型、不生成长期记忆。

### 4.5 LifeEventJournal

职责：异步、按隐私规则持久化生命事件，并支持游标读取、幂等写入和恢复检查。

它不替代 ConversationStore、AgentRunStore 或未来 MemoryManager。它保存跨器官的因果主干和必要摘要。

### 4.6 LifeSnapshotProjector

职责：把身份版本、实例信息、最小生命周期、当前活动、健康和最近因果事件折叠成只读快照。

身体、诊断界面和状态栏只读该快照，不直接拼接多个数据库状态。

### 4.7 LifeService

职责：作为 `JarvisRuntime` 子系统统一启动和停止上述组件，发布状态，形成关闭检查点，并暴露只读状态。

LifeService 不拥有 Agent，不包装 ToolRegistry，也不建立第二套会话。

## 5. 身份宪章

### 5.1 必填字段

身份宪章包含：

- `schema_version`；
- `identity_id`；
- `name`，固定初始值为 `Javis`；
- `kind`，值为本地数字生命伙伴与个人认知操作系统；
- `relationship_role`，初始值为伙伴；
- `persona_invariants`，包含安静、专注、克制、真实；
- `values`，包含用户数据归属、诚实、最小权限、可逆、尊重与连续；
- `hard_boundaries`；
- `created_at`；
- `version`；
- `previous_version_hash`；
- `content_hash`；
- `approved_by`；
- `approved_at`。

### 5.2 明确不保存

身份宪章不保存：

- 当前模型或 API 提供商；
- 当前心情或动画；
- 工具数量；
- 用户密码、API Key 或生物识别数据；
- 当前对话；
- 运行时权限 token；
- 自动推断的用户喜好；
- 未经用户批准的人格变化。

### 5.3 身份修改规则

普通对话、模型输出、工具调用、记忆整理和成长任务都不能直接修改身份宪章。

合法修改必须满足：

1. 创建新版本而不是覆盖旧版本；
2. 引用前一版本哈希；
3. 明确列出字段差异；
4. 通过宪章兼容性校验；
5. 获得用户批准；
6. 写入审计事件；
7. 能恢复上一版本。

## 6. 用户身份与 Javis 身份分离

现有 `CoreMemoryKernel` 的用户名读取能力保留为用户核心事实兼容入口，但不得继续命名为 Javis 身份事实源。

迁移规则：

- 如果旧 `core_identity.md` 存在且能解析用户名，只导入为 `primary_user.display_name` 候选；
- 旧文件内容不自动成为生命宪章；
- 解析到的“主人”措辞迁移为历史来源，不保留为关系角色默认值；
- 用户姓名缺失不阻止 Javis 身份创建；
- Javis 首次启动不虚构用户姓名和关系历史；
- 用户事实和 Javis 身份使用不同文件、不同模型和不同访问接口。

## 7. 实例谱系与连续性

### 7.1 首次出生

没有身份宪章时：

1. 创建新的 `identity_id`；
2. 创建新的 `lineage_id`；
3. 创建 `instance_id`，代次为 0；
4. 写入初始宪章；
5. 写入出生检查点；
6. 发布 `life.identity.created` 和 `life.instance.created`；
7. 进入 `awake`，随后在无活动时进入 `quiet`。

首次出生不生成虚假共同记忆、不自动声称认识用户。

### 7.2 正常重启

1. 加载并验证宪章哈希和版本链；
2. 加载实例记录；
3. 读取最近一次完整关闭检查点；
4. 验证事件游标和日志可读性；
5. 恢复仍有效的最小生命周期信息；
6. 发布恢复事件；
7. 进入 `awake` 或 `degraded`。

重启不恢复已过期临时意图，也不把上次异常终止伪装成正常关闭。

### 7.3 复制与移动检测

L0-A 记录运行目录、用户数据目录标识和实例指纹。当身份数据被复制到新环境但没有迁移凭据时，不自动认定为同一运行实例：

- 保留 `identity_id` 和 `lineage_id`；
- 创建新的 `instance_id`；
- 记录父实例或未知来源；
- 标记 `fork_pending_review`；
- 不自动合并两个实例后续经历。

完整迁移和同步在 L8 处理。

### 7.4 异常关闭

启动时若最后检查点不是完整关闭：

- 发布 `life.recovery.required`；
- 检查未完成行动和授权；
- 所有临时授权失效；
- 不自动重放可能产生副作用的动作；
- 进入 `degraded` 或 `recovering`；
- 用户确认安全后才恢复正常写操作。

## 8. 生命事件信封

### 8.1 完整字段

每个生命事件包含：

- `schema_version`；
- `event_id`；
- `event_type`；
- `timestamp_utc`；
- `monotonic_offset_ms`，用于同进程顺序判断；
- `source`；
- `source_event_id`，映射旧事件时保留原 ID；
- `session_id`；
- `request_id`；
- `correlation_id`；
- `causation_id`；
- `sequence`；
- `identity_id`；
- `instance_id`；
- `payload`；
- `privacy_class`；
- `retention_class`；
- `confidence`；
- `provenance`；
- `redaction_summary`。

### 8.2 隐私分类

- `public_surface`：可显示在状态界面；
- `local_internal`：仅本地生命内核；
- `user_private`：用户私人数据；
- `secret`：密钥、token、密码或认证材料，不进入普通日志；
- `biometric`：声音、人脸等生物识别，默认不持久化原始数据；
- `restricted_system`：系统敏感路径、进程和安全信息。

### 8.3 保留分类

- `ephemeral`：仅内存或短时缓冲；
- `session`：会话结束后可清理；
- `operational`：诊断期保留；
- `continuity`：维持身份和事件因果所需；
- `memory_candidate`：等待后续记忆治理；
- `audit`：授权和高风险行动审计；
- `never_persist`：禁止持久化。

隐私分类和保留分类是两个维度。`secret` 必须同时是 `never_persist` 或经专用安全存储引用，不能进入 payload 全文索引。

## 9. 现有事件映射

映射只使用已在生产路径证明存在的事件。

| 现有事件 | L0-A 生命语义 | 默认保留 |
|---|---|---|
| `runtime.created` | 实例运行时已创建 | continuity |
| `subsystem.registered` | 器官上线观察 | operational |
| `event_store.registered` | 事件存储可用观察 | operational |
| `thinking.started/completed` | 当前请求进入或离开认知活动 | session |
| `tool.started/completed/failed` | 行动尝试和结果观察 | operational/audit |
| `approval.requested/resolved` | 授权边界事件 | audit |
| `agent_run.created/updated/completed/failed/cancelled` | 认知执行生命周期 | operational |
| `memory.written/recalled` | 记忆器官活动观察 | operational |
| ConversationHub `request.*` | 用户交互和请求生命周期 | session/continuity |
| ConversationHub `activity.*` | 前端可见活动摘要 | session |

映射不得假定 `tool.completed` 存在 `task` 字段，也不得把 `duration_ms` 错读为 `latency_ms`。需要任务语义时从 AgentRun、request、plan 或明确的关联 ID 获取。

L0-A 不凭空新增“情绪事件”。状态只由真实生命周期和请求活动派生。

## 10. 异步生命日志

### 10.1 写入原则

现有同步 EventBus 发布后，LifeEventAdapter 只做有界映射并把结果放入内存队列。SQLite 写入、脱敏和索引在单独后台消费者完成。

### 10.2 队列规则

- 队列容量固定且可配置；
- `audit`、身份、授权、删除和恢复事件不能静默丢弃；
- 重复表面状态允许按 key 合并；
- 高频音频电平不进入生命日志；
- 队列达到预警水位时发布健康退化事件；
- 队列满时停止接受低价值事件而不是阻塞工具热路径；
- 停止运行时在限定时间内冲刷关键事件，超时记录未完整关闭。

### 10.3 数据库字段

生命日志表保存完整信封字段，payload 使用结构化 JSON。索引仅覆盖经过允许的类型、来源、时间、关联 ID 和脱敏摘要。

不对以下内容默认全文索引：

- 原始用户输入；
- 文件内容；
- 屏幕 OCR 全文；
- API Key、token 和认证头；
- 原始音频或图像；
- 未脱敏工具参数；
- 生物识别数据。

### 10.4 幂等与顺序

- `event_id` 唯一；
- 同一 `source_event_id` 映射一次；
- 进程内使用 sequence 和 monotonic offset；
- 跨重启按检查点和数据库游标继续；
- 墙钟回拨不能改变已记录因果顺序；
- 前端重连重放使用游标，不重复形成经历。

## 11. 最小生命状态机

允许的主要迁移：

```text
booting → awake → quiet
quiet ↔ engaged
awake/quiet/engaged → degraded
degraded → awake 或 recovering
recovering → awake、degraded 或 stopping
任意正常状态 → stopping
前端无法连接主体 → offline
offline → awake 或 degraded
```

非法迁移被拒绝并记录诊断，不直接写入状态。

终态请求不能让前端从新的 listening 状态错误回到 idle。已有 RuntimeStateCoordinator 的 requestId 与 terminal 保护继续保留，LifeSnapshotProjector 需要继承同类语义。

## 12. 生命快照

只读快照包含：

- `schema_version`；
- `revision`；
- `identity` 安全摘要；
- `instance` 安全摘要；
- `lifecycle_state`；
- `active_session_id`；
- `active_request_id`；
- `activity`；
- `health`；
- `degradation_level`；
- `recovery_required`；
- `last_event_id` 与 `last_sequence`；
- `updated_at`；
- `explanation`，说明当前状态的直接来源。

快照不包含 API Key、原始提示词、完整用户输入、隐藏推理、工具敏感参数或长期记忆正文。

## 13. 后端和前端出口

L0-A 提供：

- 只读身份摘要；
- 只读实例谱系摘要；
- 当前生命快照；
- 经脱敏的诊断事件游标；
- WebSocket `life.snapshot` 推送；
- WebSocket `life.expression` 最小表达推送，供 L0-B 使用；
- 恢复状态和健康诊断。

身份宪章不能通过通用 `POST /api/life` 修改。宪章变更必须走独立、可审查、需要明确确认的未来管理流程。

前端在生命端点不可用时继续使用现有 RuntimeStateCoordinator，不导致 Pet、Live 或 Code 无法启动。

## 14. L0-B 表达接口

L0-A 向身体提供最小 `ExpressionIntent v1`：

- `schema_version`；
- `revision`；
- `base_state`：idle、attention、listening、thinking、speaking、executing、blocked、error、offline；
- `intensity`，有界数值；
- `gaze_target`，仅为 none、user、content、task；
- `voice_activity`：silent、listening、speaking；
- `transition_ms`；
- `interrupt`；
- `source_snapshot_revision`；
- `generated_at`；
- `expires_at`；
- `explanation_code`。

L0-A 不发送面部骨骼名、BlendShape 权重、3D 模型路径或具体动画名。L0-B 自己把稳定语义映射到身体能力。

最小映射为：生命周期 `quiet` 投影为表达 `idle`；存在用户输入或有效请求时投影为 attention、listening、thinking、speaking 或 executing；后端不可达时由前端投影为 offline。生命周期与表达状态是两套用途不同的有限状态，不能共用一个枚举或互相覆盖持久事实。

## 15. 错误和恢复策略

### 15.1 身份文件缺失

仅在确认首次启动且不存在任何谱系记录时创建新身份。部分文件缺失但存在历史记录时进入恢复，不生成第二个 Javis 覆盖过去。

### 15.2 身份校验失败

进入只读恢复模式，保留当前可读副本和前一版本，不启动高风险行动，不用默认身份静默覆盖。

### 15.3 事件日志不可写

身份仍可读取；系统进入 degraded；关键审计动作提高限制或暂停；普通对话可在明确提示下继续；恢复后按队列能力补写必要事件。

### 15.4 队列过载

先合并或丢弃动画、电平和重复健康事件，保留用户授权、删除、身份、行动结果和恢复事件。过载不能阻塞 ToolRegistry 和语音打断热路径。

### 15.5 前后端版本不匹配

通过 `schema_version` 和 capability negotiation 降级。前端不理解新字段时忽略；后端不支持生命快照时继续使用现有 8 态状态。

## 16. 安全与隐私

- 身份宪章与用户身份分库存储；
- 所有写接口执行路径、权限和来源检查；
- 生命日志在落盘前脱敏；
- 原始秘密不进入 EventBus 普通 payload；
- 云模型无权读取完整宪章历史和生命日志；
- 表面 UI 只显示安全摘要；
- 恢复包不包含明文 API Key；
- 用户删除身份数据是显式破坏性操作，需要范围说明和备份选择；
- 测试使用临时数据根，不读写真实用户身份；
- 日志中的用户输入默认不做全文检索。

## 17. 验收场景

### 17.1 首次出生

在空用户数据目录启动，产生一个 Javis 身份、一条谱系和一个实例；没有虚构用户姓名、共同经历或情感关系；状态最终为 quiet。

### 17.2 正常重启

关闭并重启后，identity_id、lineage_id 和 instance_id 保持一致；检查点连续；不会创建第二个身份。

### 17.3 模型切换

Live 使用本地模型、Code 使用云 API 或二者切换时，身份宪章哈希、谱系和生命快照身份摘要不变。

### 17.4 界面切换

Live、Code、Pet 读取同一个 snapshot revision；界面切换不创建新生命实例或新关系。

### 17.5 异常关闭

模拟进程异常退出后重启，临时授权全部失效，未完成动作不自动重放，系统进入明确恢复或退化状态。

### 17.6 宪章损坏

破坏当前宪章文件后启动，系统保留前一版本并进入只读恢复，不用默认模板覆盖，也不宣称正常。

### 17.7 事件完整性

一个 request → agent run → tool → result 链能通过 correlation、causation、sequence 和 source_event_id 完整追踪；重放不重复写入。

### 17.8 隐私

含模拟 API Key、文件内容和用户私人输入的事件不会进入全文索引或只读表面快照。

### 17.9 性能

同步事件处理只完成有界映射与队列入队。SQLite 故意变慢时，工具完成和语音打断路径仍能返回，系统以 degraded 告警而非阻塞主链。

### 17.10 L0-B 联动

生命快照从 quiet → engaged → quiet 时，ExpressionIntent revision 单调增加；过期表达不会覆盖新 listening 状态；3D 不可用时前端仍显示现有 2D 状态。

## 18. 自动化测试范围

实施计划必须覆盖：

- 身份宪章创建、版本链、哈希和回滚；
- 用户身份与 Javis 身份隔离；
- 正常重启、异常关闭和损坏恢复；
- 实例复制与 fork 标记；
- 最小状态机合法与非法迁移；
- 旧 Event 到 LifeEvent 的确定性映射；
- 完整事件字段持久化；
- 幂等、游标、时钟回拨和重放；
- 隐私分类、脱敏和全文索引排除；
- 有界队列、关键事件优先级和慢 SQLite；
- runtime 子系统启动、停止和资源释放；
- 只读 API 与 WebSocket 快照；
- 前端旧协议降级；
- 本地/云端模型切换不改变身份；
- 现有语音、打断、会话和工具回归。

## 19. 实施拆分原则

本子规格适合拆成一个独立实施计划，但实施任务必须按以下依赖顺序：

1. 纯数据契约与文件迁移；
2. 身份和谱系存储；
3. 最小状态机与快照投影；
4. 事件适配与隐私分类；
5. 异步日志和恢复；
6. Runtime 子系统接线；
7. 只读 API、WebSocket 与前端降级；
8. L0-B 表达契约；
9. 回归和 D 盘真实重启验收。

每个任务都必须先有失败测试，且能够独立回滚。不得把 L1 情绪、L2 长期记忆或 L5 Agent 扩建塞进 L0-A。

## 20. 非目标

- 不实现完整功能性情绪；
- 不实现自传记忆和夜间整理；
- 不生成用户画像；
- 不实现 Computer Twin；
- 不新增 Planner、多 Agent 或主动建议；
- 不实现永久权限；
- 不实现 3D 渲染；
- 不迁移全部旧数据库；
- 不删除现有 RuntimeStateCoordinator；
- 不用提示词代替身份存储；
- 不宣称 Javis 已拥有意识或完整生命。

## 21. 出口标准

L0-A 只有同时满足以下条件才可进入 L1：

1. 身份宪章、实例谱系和版本链真实落盘并可恢复；
2. 首次启动不虚构过去，重启不产生第二身份；
3. 模型和界面切换不改变身份；
4. 生命事件保留因果、顺序、隐私、保留和来源；
5. 同步 EventBus 热路径无 SQLite 和模型调用；
6. 敏感 payload 不进入普通全文索引；
7. 异常关闭和宪章损坏进入安全恢复；
8. 前后端都能读取同一只读生命快照；
9. L0-B 能只依赖 ExpressionIntent v1 驱动身体；
10. 现有 P0/P1 语音、打断、统一会话和发布基线无回归；
11. G 盘隔离实现、G 盘集成、D 盘真实验收边界得到遵守；
12. 所有未在 D 盘真实执行的项目明确标记为未验收，不以自动测试代替。
