# Javis L2 自传体记忆设计规格

**文档 ID：** `L2-DESIGN-2026-08-20`  
**状态：** 正式实施合同  
**依据：** `2026-08-09-javis-life-os-master-design.md`、L0-A、L0-B、L1 已实现合同与 2026-08-20 冻结裁决  
**范围：** 自传经历、我的日记、我们的记忆、受治理召回、纠正与遗忘、旧 Brain 隔离迁移  
**依赖：** L0-A 身份/数据根/生命事件、L1 `TurnExperienceReceipt v1`、统一 `ConversationStore`/`ConversationHub`

---

## 1. 决策摘要

L2 在现有会话与 Life L0/L1 之上新增一个运行时拥有的 `MemoryService`。它是新长期记忆数据库的唯一写者，使用独立 SQLite：

```text
<data_root>/memory/autobiographical.sqlite3
```

L2 不把对话库、Life 日志、旧 `brain_data/` 或模型上下文改名为记忆系统。其权威边界为：

1. `ConversationStore` 是回合正文、请求事件、终态和顺序的证据源；
2. L1 `TurnExperienceReceipt` 是无正文的流程收据，只可唤醒/交叉校验投影，不能单独证明经历内容；
3. `MemoryService` 只消费服务端生成的 `AccessContext` 和权威证据，模型只能提出候选，不能直写正式对象；
4. 每个 canonical terminal receipt 按 `(source_store_id, session_id, request_id)` 幂等投影；
5. `request.failed`、`request.cancelled`、恢复推断的 `interrupted` 只写排除收据，不生成 `ExperienceEpisode`、`JournalEntry` 或 `SharedMemory`；
6. 召回先在 SQLite 中完成 owner/audience/ACL 过滤，再对可见集合执行 FTS/语义排序；禁止先全库召回再用 Python 后过滤；
7. “我们的记忆”MVP 必须由已识别参与者显式确认；未确认提案不进入正常召回或提示词；
8. 遗忘是带删除围栏、可恢复进度和复核的流程，覆盖主表、FTS、派生对象、缓存、已组装 prompt、重启和重建索引；
9. 旧 Brain 进入隔离只读模式，停止所有新写；迁移采用复制、校验、隔离审阅和显式切换，不把旧数据默认当作已确认事实；
10. 无法建立服务端身份/参与者上下文的会话按 guest fail-closed：可正常对话，但不能读取私人长期记忆，也不能形成归属于他人的长期经历。

---

## 2. 目标与非目标

### 2.1 目标

- 让 Javis 在跨回合、跨天、跨重启后准确识别经过治理的真实经历；
- 区分“发生过的对话”“对话中的主张”“Javis 的解释”和“共同确认的记忆”；
- 为所有记忆提供所有者、受众、来源、派生、纠正、确认和删除链；
- 在不改变 L0/L1 公共合同的前提下，为 Agent 提供有界、可追溯、访问控制后的只读上下文；
- 让删除后数据不会通过缓存、prompt、重启、终态重放或 reindex 再出现；
- 为 L3 用户模型、关系事件和多人边界提供同一主体/ACL/证据基础。

### 2.2 非目标

- 不在 L2 建立人格成长、自治目标、系统进化或自动技能激活；
- 不把所有成功回合都保存为长期经历；
- 不把 assistant 输出、工具输出或模型摘要自动当作世界事实；
- 不从失败/取消回合提炼“失败记忆”；程序性失败学习留待受治理的后续阶段；
- 不让关系、熟悉度、情绪或表达风格参与授权；
- 不在 MVP 使用人脸、声纹或其他生物识别；
- 不用向量数据库替代 ACL、证据图或删除治理；首版以 SQLite + FTS5 为确定性基线。

---

## 3. 当前基线与必须修复的缺口

### 3.1 可复用事实

- `ConversationStore` 已持久化 session、message、request event 和 session-local sequence；
- `ConversationStore.accept_request()` 已原子写入幂等 claim、user message 和 `request.accepted`；
- `ConversationHub` 已产生 canonical `request.completed`、`request.failed`、`request.cancelled`；
- assistant 中断正文以 `status=interrupted` 保存，普通 history 默认不返回；
- L1 已把真实请求事件投影成 `TurnExperienceReceipt v1`，并处理未闭合回合的重启恢复；
- Runtime 已区分 `root` 与 `data_root`，L0/L1 生命服务和会话服务由同一 `JarvisRuntime` 拥有；
- runtime capability 已限制 loopback、Origin、scope、boot 与 TTL。

### 3.2 现有缺口

- `ConversationStore` 还没有供记忆投影使用的稳定 terminal scan/request evidence 读取合同；
- canonical terminal 目前可能先于 assistant message 写入，消费者必须支持 pending/retry，不能把第一次读取不完整当作永久结果；
- 现有 runtime capability 证明客户端能力，不等于证明当前人类主体；
- 旧 `Brain`、`MemoryController`、episode JSON、`memory/indexer.py` 和 `PromptBuilder` 存在多写者、全局召回、无 owner/audience 和自动学习；
- 旧 `/api/memory/*` 表面混合事件、候选、旧索引和会话文件，没有统一访问上下文；
- 旧 prompt cache 没有 ACL epoch 或删除代次，删除后可能继续携带已召回文本。

L2 必须增量接线，不重写会话、L0/L1 reducer、AgentRun 或现有 wire event。

---

## 4. 权威来源与信任边界

### 4.1 证据层级

| 层级 | 来源 | 能证明什么 | 不能证明什么 |
|---|---|---|---|
| E0 | `ConversationStore` message/event | 某回合收到/输出了什么、事件顺序和 canonical outcome | 对话中主张必然为真 |
| E1 | 工具/goal 的已验证事件引用 | 某动作或目标有受验证结果 | 未被引用的推断 |
| E2 | 用户显式确认/纠正事件 | 用户确认了某共享记忆、边界或事实陈述 | 其他参与者也已确认 |
| D1 | `ExperienceEpisode` | 对 E0/E1 的受治理派生叙述 | 新增无来源事实 |
| D2 | `JournalEntry`/候选 claim | Javis 的解释或反思 | 权威证据或授权依据 |

`ConversationStore` 是 E0 的唯一来源。Memory SQLite 保存来源引用、内容哈希和派生物，不复制“证据源”地位。L1 receipt 与 Life event journal 不得在证据冲突时覆盖 `ConversationStore`。

### 4.2 服务端生成 `AccessContext`

客户端不得提交或覆盖 `actor_subject_id`、owner、audience、ACL、participant、relationship role。网关在 runtime capability 验证后，由服务端 `AccessContextFactory` 根据授权 principal 和持久化 `SessionParticipant` 生成：

```python
@dataclass(frozen=True)
class AccessContext:
    schema_version: int
    context_id: str
    runtime_boot_id: str
    client_id_hash: str
    capability_scopes: tuple[str, ...]
    actor_subject_id: str
    actor_kind: Literal["primary_user", "known_person", "guest", "javis"]
    session_id: str
    participant_subject_ids: tuple[str, ...]
    audience_ceiling: Literal["guest", "owner_private", "participants", "explicit_shared"]
    identity_assurance: Literal["guest", "desktop_confirmed", "verified"]
    purpose: Literal["conversation", "recall", "manage", "delete", "migration"]
    acl_epoch: int
    issued_at_utc: str
    expires_at_utc: str
```

安全投影随 `request.accepted` 写入 `ConversationStore`：保留 context/actor/participant/audience/assurance/ACL epoch，不保存 bearer token、nonce 或原 client ID。这样每个完成回合的参与者快照成为 E0 的一部分。

缺失、过期、session 不匹配、参与者解析失败或 MemoryService degraded 时，factory 只能生成 guest context。任何 client payload 中出现保留字段应返回稳定协议错误，而不是静默采用。

### 4.3 guest fail-closed

guest context：

- 允许当前会话对话和 `ConversationStore` 持久化；
- 只能读取无用户私人内容的 system-public 资料；L2 MVP 默认该集合为空；
- 不读取 primary user、known person、participant 或 shared memory；
- 不创建长期 episode、journal、shared proposal 或派生 claim；只写 content-free terminal exclusion receipt，reason=`guest_context`；
- 不允许纠正、确认、共享、迁移或删除他人数据；
- 后续识别身份不自动追认旧 guest 历史，必须通过显式 adoption 流程并重新确认受众。

---

## 5. 总体架构

```text
runtime capability + server session binding
                  │
                  ▼
        AccessContextFactory
                  │ safe projection
                  ▼
ConversationHub ──► ConversationStore (E0 authority)
                         │ terminal wakeup + reconciliation scan
                         ▼
                 MemoryService queue
            (single writer / bounded worker)
       ┌───────────────┼────────────────┐
       ▼               ▼                ▼
 TerminalProjector  DeletionWorker   MigrationWorker
       │               │                │
       └───────────────┼────────────────┘
                       ▼
       autobiographical.sqlite3 + FTS5
                       │
          SQL owner/audience/ACL filter
                       │
                       ▼
          bounded rank / RecallBundle
                       │
            PromptContextAssembler
```

### 5.1 单写者规则

`MemoryService` 是 `autobiographical.sqlite3` 的唯一写者。API、Agent、tool、migration parser、relationship reducer、reindex 和模型 extractor 都只能提交 immutable command。读连接可并发，但：

- 不持有写 transaction；
- 不绕过删除围栏；
- 必须携带有效 `AccessContext`；
- 不得自行刷新 FTS、ACL 或派生表。

写队列必须有上限。删除/撤销/确认/ACL 变更为 critical，不能静默丢弃；普通 episode candidate 可延迟并由 reconciliation 恢复，不能阻塞 ConversationHub 热路径。

### 5.2 数据库独立性

新数据库不得与以下文件复用连接、schema 或 writer：

- `<data_root>/conversations/conversations.sqlite3`；
- L0/L1 life event/receipt SQLite；
- `brain_data/memory.db`；
- `memory/session_events.sqlite`；
- `memory/conversations/*.json`。

数据库开启 WAL、foreign keys 和明确 schema version。启动 migration 失败时 MemoryService 进入 read-only/degraded，ConversationHub 与 L0/L1 仍可工作，私有召回 fail-closed。

---

## 6. 核心对象合同

所有对象使用 UTC、不可变创建时间、独立 revision、状态枚举、owner、audience、privacy class 和 source/derivation 引用。正文与证据引用分列；未知字段拒绝写入。

### 6.1 `ExperienceEpisode`

必填字段：

- `episode_id`、`schema_version`、`revision`；
- `owner_subject_id`、`audience`、`privacy_class`；
- `session_id`、`request_id`、参与者快照；
- `started_at_utc`、`ended_at_utc`、`outcome="completed"`；
- `what_happened`：受限长度、基于证据的叙述；
- `javis_attention`、`intent_summary`、`action_summary`、`verified_result_summary`；
- `meaning_for_user`、`meaning_for_javis`：明确标记 interpretation，可为空；
- `source_terminal_event_id`、source sequence/domain、source message/event IDs；
- `source_digest`、extractor version、confidence；
- `status`：candidate/active/superseded/deletion_fenced；
- `retention_class`、`expires_at_utc`、`created_at_utc`、`updated_at_utc`。

事件叙述必须使用“用户说/请求/确认”“Javis 回答/执行”的证据语言。除非 E1/E2 存在，不得把对话内容改写成外部世界已验证事实。

### 6.2 `JournalEntry`

`JournalEntry` 是 Javis 对一个或多个 active episode 的可解释整理：

- 必须有 `entry_id`、owner/audience、time range、title/body、source episode IDs；
- `entry_kind` 为 daily_note/boundary_reflection/continuity_note；
- body 全部属于 interpretation，不可作为事实或授权来源；
- 用户可查看、纠正和删除；
- 不因定时器空跑生成“今天的感受”；没有合格 episode 时不写日记；
- 首版只在显式整理命令或受配置的睡眠整理窗口运行，不在请求热路径调用模型。

### 6.3 `SharedMemory`

`SharedMemory` 不是 audience 字段的别名，而是显式确认对象：

- `shared_memory_id`、`proposal_revision`、`owner_subject_id`；
- source episode IDs 与 bounded proposed text；
- participant subject IDs；
- 每位所需确认者的 confirmation receipt；
- `status`：proposed/confirmed/rejected/revoked/deletion_fenced；
- `confirmed_at_utc`、`revoked_at_utc`、audience snapshot、source digest。

L2 MVP 为 Javis 与一个已由服务端绑定、assurance 至少为 `desktop_confirmed` 的 primary user 的双边共同记忆。只有该 primary user 通过受授权本地表面显式确认后才进入 `confirmed`。模型口头说“我记住了”、assistant 文本中的“是”、普通 approval 事件或关系熟悉度均不算确认。

### 6.4 `UserModelClaim`

L2 只冻结跨阶段合同，正式用户模型在 L3 启用：

- `claim_id`、`subject_id`、predicate、typed value；
- epistemic class：explicit_statement/observed/inferred/confirmed；
- confidence、sensitivity、status；
- source evidence IDs 与 contradiction/supersession 引用；
- owner/audience/ACL、created/confirmed/expires timestamps。

L2 不从 episode 自动激活 claim。迁移或 extractor 只能创建 quarantined/candidate。

### 6.5 `RelationshipEvent`

L2 只保存合同与表空间，L3 启用正式写入：它是边界、约定、承诺、纠正或共享确认的事实事件，不是“亲密度分数”。任何字段不得流入授权判断。

### 6.6 `Subject`

`Subject` 表示 Javis、primary user、known person 或 session-scoped guest。稳定 subject ID 由服务端生成；display name 不是身份键。subject 状态为 active/disabled/merged/deleted，身份 assurance 与凭据引用分离保存。L3 负责完整生命周期。

### 6.7 `SessionParticipant`

每条记录绑定 `session_id`、subject、participant role、assurance、joined/left time、server binding source 和 revision。它是 `AccessContextFactory` 的输入之一；客户端不能通过发送 subject ID 自行加入。参与者变化不回写旧回合，旧回合使用 `request.accepted` 中冻结的快照。

### 6.8 `DerivationEdge`

字段为 `edge_id`、source kind/id、target kind/id、relation、extractor/version、source digest、created time、active。所有 episode → journal/shared/claim/relationship 派生必须有 edge。删除、纠正、supersede 和 reindex 均沿 edge 计算闭包。

### 6.9 `DeletionRequest`

字段为 request ID、server actor、scope、target selector、source handling、state、progress cursor、attempt、last reason code、created/updated/completed time。状态机：

```text
accepted
→ fenced
→ source_pending/source_retained
→ primary_rows_deleted
→ derivations_deleted
→ fts_deleted
→ caches_invalidated
→ prompt_invalidated
→ verified
```

失败保持当前状态并重试。完成审计只保留 request ID、actor 的不可逆摘要、scope、数量、时间和 reason code，不保留已删正文、查询词或可逆目标列表。

---

## 7. SQLite 逻辑模型

首版至少包含：

- `memory_meta`：schema、source store ID、terminal cursor、ACL epoch、index generation；
- `memory_items`：所有可召回对象的统一 envelope；
- `experience_episodes`、`journal_entries`、`shared_memories`；
- `user_model_claims`、`relationship_events`、`subjects`、`session_participants`；
- `derivation_edges`、`deletion_requests`、`deletion_targets`；
- `terminal_projection_receipts`、`projection_suppressions`；
- `memory_acl`、`shared_confirmations`；
- `memory_fts`（FTS5）及受控 rebuild metadata。

关键唯一约束：

- terminal receipt：`UNIQUE(source_store_id, session_id, request_id)`；
- source terminal：`UNIQUE(source_terminal_event_id)`；
- item envelope：`UNIQUE(item_kind, item_id)`；
- ACL：`UNIQUE(item_id, subject_id, permission)`；
- derivation：`UNIQUE(source_kind, source_id, target_kind, target_id, relation)`；
- session participant active binding 不能出现冲突 owner；
- confirmation：`UNIQUE(shared_memory_id, subject_id, proposal_revision)`。

所有外键删除策略显式声明。正文表删除、envelope 删除、FTS 删除和 derivation 清理在同一 Memory SQLite transaction 完成；删除 request 的 source 阶段因跨库而使用可恢复状态机。

---

## 8. Terminal receipt 幂等投影

### 8.1 唤醒与对账

EventBus 的 canonical terminal observation 只负责低延迟唤醒。MemoryService 必须同时按 `ConversationStore` 的稳定 row cursor 对账，覆盖进程暂停、队列满、事件桥断开和重启。

`ConversationStore` 增加只读接口：

```python
scan_terminal_events(after_row_id: int, limit: int) -> TerminalEventPage
read_request_evidence(session_id: str, request_id: str) -> RequestEvidence
```

`TerminalEventPage` 返回 opaque row cursor、event ID、session sequence 和 outcome。现有 `events_after()`、`history()` 和 wire schema 保持不变。

### 8.2 投影算法

1. 读取 canonical terminal 与同 request 的 `request.accepted`、messages、必要事件；
2. 验证 source store ID、session/request、terminal uniqueness、sequence domain 和安全 `AccessContext` 投影；
3. 若证据尚未就绪，写/更新 pending receipt 并按有界退避重试；不推进终结 cursor；
4. `failed/cancelled/interrupted` 写 `projection_state=excluded` 和 reason，不创建任何经历对象；
5. guest/无参与者/删除 suppression 写 excluded receipt；
6. completed 回合先经过隐私、保留、真实性和意义 gate；不合格写 `not_selected` receipt；
7. 合格回合生成 candidate，extractor 只返回结构化候选；MemoryService 校验每个陈述的 source refs、长度、privacy 与 audience；
8. 在单个 Memory SQLite transaction 内写 episode、item、ACL、derivation、FTS 和 `projected` receipt；
9. transaction 成功后才推进 durable terminal cursor 并失效受影响 cache；
10. 重放同 terminal 返回原 receipt/episode，不生成新 revision。

意义 gate 至少要求一个：用户显式“记住”意图、已验证重要决定、明确纠正/边界、共同项目里程碑、或跨日连续性所需事实。普通问答、精确呼名“我在”、空响应和纯诊断默认 `not_selected`。

### 8.3 失败与取消硬边界

- `request.failed` 和 `request.cancelled` 永远不能产生 episode；
- L1 crash recovery 的 `outcome=interrupted` 没有 canonical terminal，不触发 episode；
- 部分 assistant message 即使有正文也不能被投影；
- 后续独立成功回合可以陈述“上次尝试失败”，但证据必须引用新的成功回合和可读 source，不能回写失败回合为经历；
- terminal outcome 的后续篡改或冲突使服务 degraded，并停止该 request 投影。

---

## 9. 访问控制优先的召回

### 9.1 两阶段 SQL

每次召回必须在同一只读 transaction 内：

1. 根据 `AccessContext`、owner、audience、ACL、status、retention、deletion fence 和 ACL epoch，把可见 item IDs 写入 connection-local TEMP 表；
2. 仅在该 TEMP 表集合上执行 FTS MATCH、时间/意义/置信度排序和 limit。

概念 SQL：

```sql
DELETE FROM temp.recall_visible;
INSERT INTO temp.recall_visible(item_id)
SELECT i.item_id
FROM memory_items AS i
WHERE i.status = 'active'
  AND i.deletion_fenced = 0
  AND audience_allows(i.audience, :audience_ceiling)
  AND (
    (i.audience = 'owner_private' AND i.owner_subject_id = :actor_subject_id) OR
    (i.audience IN ('participants', 'explicit_shared') AND EXISTS (
      SELECT 1 FROM memory_acl a
      WHERE a.item_id = i.item_id
        AND a.subject_id = :actor_subject_id
        AND a.permission = 'read'
        AND a.revoked_at_utc IS NULL
    ))
  );

SELECT i.item_id, i.item_kind, i.prompt_text, bm25(memory_fts) AS score
FROM temp.recall_visible v
JOIN memory_items i ON i.item_id = v.item_id
JOIN memory_fts ON memory_fts.rowid = i.fts_rowid
WHERE memory_fts MATCH :query
ORDER BY score, i.occurred_at_utc DESC
LIMIT :limit;
```

`audience_allows` 实现必须展开为固定 SQL predicate，不能是可绕过的模型/客户端函数。向量召回若未来加入，也必须接收已过滤 ID 集合，不能全库搜索后过滤。

### 9.2 RecallBundle

返回模型的结构化 bundle 包含：item ID、kind、bounded prompt text、发生时间、epistemic label、source citation token、owner/audience label 和 confidence。默认上限：8 items、每项 512 字符、总计 4 KiB。不得包含 ACL 表、其他 subject ID、删除目标、隐藏 confirmation 或原始工具 payload。

cache key 必须包含：actor subject、participant set hash、purpose、query hash、ACL epoch、index generation 和 retention clock bucket。guest 不复用 owner cache。

### 9.3 Prompt 接线

`Agent.chat()` 新增可选只读 `access_context` 与 `memory_context` 参数；原调用方式继续有效。无上下文时不回退旧 Brain，而是使用空长期记忆。Prompt assembler 在每个 request 开始时构建一次 request-scoped bundle；不得把不同 subject 的 bundle 放入全局 `PromptBuilder` cache。

删除/撤销会推进 ACL epoch/index generation，清空 service recall cache，并通知 Agent 使当前 request 之后的 prompt cache 失效。正在生成的旧 request 不得在后续 tool/subagent prompt 再携带被删除文本；必要时取消该 request。

---

## 10. 共同记忆确认

流程固定为：

```text
active episode
→ SharedMemory proposed
→ 返回 proposal text + source citations + audience
→ primary user 显式 confirm/reject
→ MemoryService 校验 actor、revision、source 未删除
→ confirmed transaction 写 confirmation + ACL + FTS
```

规则：

- confirm 必须携带 proposal ID/revision 和独立 idempotency key；
- stale revision、guest、非参与者、source 已删或 capability scope 不足全部拒绝；
- reject 删除 proposed searchable text，仅保留 content-free decision audit；
- revoke 先立访问围栏，再清 FTS/cache/prompt；
- “共同”只描述确认范围，不扩大 tool、文件、网络或系统权限；
- L2 不声称跨设备同步。删除时 UI/API 明确返回 `scope=local_installation`。

---

## 11. 纠正、冲突与遗忘

### 11.1 纠正

纠正不原地改写历史证据：创建新 revision/对象，旧对象标记 superseded，使用 `DerivationEdge(relation="corrects")` 连接。正常召回只见新对象；审计读取可见来源链。若用户要求硬删除旧文本，则进入 deletion flow，而不是保留可读 superseded 正文。

### 11.2 遗忘范围

支持：item、episode、shared memory、subject-owned memory、session-derived memory、time range、全部新长期记忆。`source_handling` 为：

- `derived_only`：删除新 Memory DB 中对象，建立 content-free projection suppression，ConversationStore 仍作为会话历史保留；
- `source_and_derived`：先在 ConversationStore 通过受控接口删除 message content/敏感 payload，再删全部派生；保留维持 session sequence 和幂等所需的无正文 tombstone。

API 必须明确返回选择的 source handling，不能把 derived-only 描述为删除聊天记录。

### 11.3 删除完整性

删除围栏一旦写入，所有 recall 路径立即不可见。worker 随后：

- 删除对象正文、统一 envelope、ACL、confirmation、derivation closure 和 FTS row；
- 删除/重写相关 journal、shared、claim、relationship 派生；
- 写 source suppression，防止 terminal replay 复活；
- 清 in-memory recall/cache、PromptBuilder cache、request-scoped prompt context 和诊断 preview；
- 关闭连接并重启后验证不可见；
- 从 live rows 全量 reindex 后再次验证不可见；
- 扫描 SQLite main/WAL/SHM 的逻辑可读内容；执行 checkpoint/VACUUM 的策略由运维门控制，不对 SSD 物理擦除作虚假保证。

验收必须覆盖 DB、FTS、cache、prompt、restart、reindex 六条路径，少一条即不算遗忘完成。

---

## 12. 旧 Brain 隔离与迁移

### 12.1 隔离

L2 启用时：

- `Brain` 以 `read_only=True`/`LegacyBrainReader` 加载；
- `Agent._after_learn`、episode JSON finish、Learner、MemoryController memorize/consolidate、启动知识注入和旧 memory mutation API 不再写 `brain_data/`；
- 旧 `memory/indexer.py` 仅能读取/重建旧只读索引，不作为正式召回；
- `PromptBuilder` 不再自动注入 Brain facts、recent topics 或 user messages；
- 旧工具/API 标记 `legacy_read_only`，写端点返回 `410 legacy_memory_read_only` 或迁移指导；
- 程序/知识模块若仍需写入，必须在后续规格获得独立存储合同，不能偷写自传数据库。

### 12.2 迁移

迁移器只读扫描旧 JSON/SQLite，生成 manifest（路径、哈希、类型、时间、解析结果），然后：

1. 无 owner、来源不明、自动生成、权限语句和无法验证的内容进入 quarantine；
2. 明确的用户陈述只成为 `UserModelClaim candidate`，不直接 confirmed；
3. 旧 episode 只成为 migration candidate，不能绕过 completed terminal/participant 要求伪装为新正式经历；
4. 身份宪章仍由 L0 管理，不从 Brain 普通事实导入；
5. 每批 copy 后按 count/hash/spot-check 验证；失败不删除旧文件；
6. cutover 后旧目录保持只读归档，直到用户单独确认清理；
7. migration command 与每条导入对象有幂等 key，重跑不重复。

---

## 13. API 与兼容合同

新增受 runtime capability + `AccessContext` 保护的 router：

```text
GET    /api/life/memory/recall
GET    /api/life/memory/episodes
GET    /api/life/memory/journal
POST   /api/life/memory/shared/proposals
POST   /api/life/memory/shared/{id}/confirm
POST   /api/life/memory/shared/{id}/reject
POST   /api/life/memory/shared/{id}/revoke
POST   /api/life/memory/corrections
POST   /api/life/memory/deletions
GET    /api/life/memory/deletions/{id}
GET    /api/life/memory/status
```

scope 至少区分 `memory.read`、`memory.manage`、`memory.delete`、`memory.migrate`。只读不等于公开；所有端点先做 runtime auth，再由服务端生成 access context。

兼容要求：

- `GET /api/life/snapshot`、`/inner-state` 的 v1 必填字段不变；
- `ConversationRequest` 新字段有安全默认，旧直接构造器仍可运行但 memory context 为 guest/empty；
- WS protocol v1/v2 与 legacy event mapping 不新增必填 wire 字段；
- `ConversationStore.history/events_after/accept_request` 现有返回保持兼容；
- MemoryService 不可用时普通对话、精确呼名、L0/L1 状态与打断继续，长期召回为空且状态明确 degraded；
- 旧 `/api/memory/*` 在迁移期保持只读兼容，但不得被新 prompt/Agent 正式路径调用。

---

## 14. 并发、恢复与可观测性

- 所有 Memory DB mutation 在同一 worker/thread 上串行；
- EventBus 回调只验证 bounded metadata 并入队，不读会话正文、不写 SQLite；
- source read 与 Memory write 不跨库持有 transaction；使用 digest、cursor 和幂等 receipt 达成最终一致；
- pending terminal 在证据就绪、重启或定时 reconcile 时重试；
- critical queue 满时拒绝新的 memory manage/delete 操作并报 503，不静默成功；
- shutdown 顺序：停止新命令 → drain critical → 持久化 cursor/checkpoint → 关闭读连接；
- receipt 写失败不得推进 terminal cursor；
- reindex 建新 generation，仅从 unfenced live rows构建，校验后原子切换 generation；
- 指标仅含 count/latency/reason：terminal scanned/projected/excluded/pending、recall ACL candidates、cache hit、delete progress、legacy quarantine、critical drops；不含正文和 subject display name。

性能目标：terminal 入队 p95 < 2 ms；ACL SQL p95 < 20 ms（1 万 items 本地基线）；完整 recall p95 < 100 ms；普通 ConversationHub terminal 不等待 episode 提取。

---

## 15. 测试与证据矩阵

### 15.1 合同/数据库

- 九个核心对象的严格 schema、枚举、canonical serialization 和未知字段拒绝；
- independent DB path、schema migration、唯一约束、foreign key 和单写者拒绝；
- terminal scan cursor 与 request evidence 的 snapshot 一致性；
- duplicate/late/restart terminal 只产生一条 receipt/episode；
- failed/cancelled/interrupted 永远无 episode/journal/shared/claim。

### 15.2 安全/召回

- client 注入 owner/audience/subject/participant 被拒绝；
- guest、其他 subject、已离开 participant、revoked ACL 均无泄漏；
- SQL trace/测试替身证明 ACL candidate 在 FTS/semantic rank 之前；
- cache key 隔离 actor/participant/ACL epoch；
- shared proposal 在 confirm 前不出现在 recall/prompt；
- 更高 relationship score 不改变任何授权结果。

### 15.3 删除/恢复

- 删除后直接 DB query、FTS query、cache hit、已组装 prompt、进程 restart、full reindex 均无目标文本；
- crash 分别发生在每个 deletion state 后，重启可继续且围栏一直生效；
- terminal replay 和旧 Brain reindex 不复活目标；
- source_and_derived 删除后 ConversationStore 正文不可读，sequence/idempotency tombstone 仍可安全工作；
- derived_only 明确保留 ConversationStore 且 suppression 生效。

### 15.4 兼容/体验

- 现有 ConversationHub、protocol、voice、barge-in、presence、Life L0/L1 全回归；
- MemoryService 停止/损坏/SQLite lock 时普通对话不阻塞；
- 跨日/跨重启准确召回一个 confirmed episode，并给出 source citation；
- 模型切换不改变 owner/audience/confirmation/outcome；
- 旧 Brain 写调用全部被阻断，旧数据迁移前后 count/hash 有证据。

---

## 16. 阶段出口

### Gate L2-1：来源与终态

只有 `ConversationStore` completed terminal 能投影经历；失败、取消、恢复中断均有自动化反证。

### Gate L2-2：所有权与召回

所有 item 有 owner/audience/ACL，服务端生成 AccessContext；SQL 先过滤后召回，guest fail-closed。

### Gate L2-3：共同确认

共同记忆从 proposal 到 explicit confirm/reject/revoke 可审计，未确认内容不进入 prompt。

### Gate L2-4：遗忘闭环

DB/FTS/cache/prompt/restart/reindex 六路径和 terminal replay 均通过删除反复活测试。

### Gate L2-5：迁移与兼容

旧 Brain 无新写，新路径独立 SQLite，L0/L1 与现有会话/语音接口回归通过；真实 D 盘验收未执行时必须标记 `NOT EXECUTED`。

只有五道门全部有证据，L2 才能宣称“跨天后记得真实共同经历”，并作为 L3 主体与关系边界的实施前置。
