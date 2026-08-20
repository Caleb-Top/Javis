# Javis L2 自传体记忆实施计划

**计划 ID：** `L2-PLAN-2026-08-20`  
**对应规格：** `L2-DESIGN-2026-08-20`  
**状态：** 可直接执行；当前文档不表示代码已实现  
**实施原则：** 小步、测试先行、每阶段独立可回退；不得以修改 L0/L1 公共语义换取 L2 通过

---

## 1. 交付边界

本计划交付：服务端 `AccessContext`、L2/L3 共用对象合同、新单写者 `MemoryService`、独立 SQLite、ConversationStore terminal receipt 幂等投影、ACL-first recall、episode/journal/shared memory、纠正/遗忘、旧 Brain 只读迁移和有界模型上下文。

冻结不变：

- `ConversationStore` 是回合正文/事件/终态证据源；
- 新数据库是 `<data_root>/memory/autobiographical.sqlite3`；
- failed/cancelled/interrupted 回合绝不形成经历；
- 共同记忆 MVP 必须显式确认；
- relationship 数据不参与授权；
- 删除覆盖 DB/FTS/cache/prompt/restart/reindex；
- guest session 无私人召回和长期写入；
- 旧 Brain 停止新写，仅作隔离迁移输入；
- `GET /api/life/snapshot`、`GET /api/life/inner-state`、WS v1/v2、`ConversationStore` 现有方法和 `Agent.chat()` 原调用方式保持兼容。

---

## 2. 文件所有权

### 2.1 新 L2 后端文件

```text
core/life/memory/__init__.py
core/life/memory/contracts.py
core/life/memory/access.py
core/life/memory/store.py
core/life/memory/service.py
core/life/memory/projection.py
core/life/memory/extraction.py
core/life/memory/recall.py
core/life/memory/deletion.py
core/life/memory/migration.py
core/life/memory/api.py
```

### 2.2 允许修改的现有后端文件

```text
core/runtime_access.py
core/conversation_protocol.py
core/conversation_store.py
core/conversation_hub.py
gateway/conversation_ws.py
core/runtime.py
core/agent.py
core/prompt_builder.py
knowledge/brain.py
knowledge/learner.py
memory/controller.py
memory/episodic.py
memory/indexer.py
main.py
tools/memory_tools.py
```

对上述 legacy 文件只做关闭写入、兼容桥或路由切换，不做无关重构。`core/life/service.py` 只允许添加 MemoryService 生命周期/状态引用；不得把 L2 reducer 塞入 L1 单写者锁内。

### 2.3 新前端文件

```text
app/src/memory/memoryTypes.ts
app/src/memory/MemoryClient.ts
app/src/memory/MemorySurface.ts
app/src/memory/memoryState.ts
```

允许最小修改 `app/src/bridge/runtimeAccess.ts`、`app/src/bridge/backendEndpoints.ts`、`app/src/panels/DrawerManager.ts`、`app/src/styles.css`，只用于 scope、端点和记忆管理表面。

### 2.4 测试与验证文件

```text
tests/test_memory_contracts.py
tests/test_memory_access_context.py
tests/test_conversation_terminal_evidence.py
tests/test_memory_store.py
tests/test_memory_service.py
tests/test_memory_terminal_projection.py
tests/test_memory_episode_extraction.py
tests/test_memory_acl_recall.py
tests/test_shared_memory_confirmation.py
tests/test_memory_deletion.py
tests/test_memory_legacy_migration.py
tests/test_memory_prompt_integration.py
tests/test_l2_compatibility.py
tests/test_l2_release_contract.py
app/tests/memoryClient.test.ts
app/tests/memorySurface.test.ts
docs/verification/JAVIS_L2_D_DRIVE_ACCEPTANCE.md
```

每个工作包只修改其列出的文件；若实现时需要扩大边界，先更新本计划并说明依赖原因。

---

## 3. 依赖图与阶段

```text
T1 contracts
 ├─► T2 ConversationStore evidence
 ├─► T3 MemoryStore schema
 └─► T4 AccessContext/session binding
          │
T2 + T3 + T4 ─► T5 MemoryService/runtime lifecycle
                       │
                 T6 terminal projection
                  ├─► T7 episode/journal
                  └─► T8 ACL-first recall/prompt
T3 + T4 + T7 ─► T9 shared confirmation
T3 + T6 + T8 + T9 ─► T10 deletion/reindex
T5 + T8 + T10 ─► T11 legacy isolation/migration
T4 + T8 + T9 + T10 ─► T12 API/UI
T1..T12 ─► T13 compatibility/release evidence
```

阶段出口：

| 阶段 | 任务 | 出口 |
|---|---|---|
| P0 合同与来源 | T1-T4 | 对象/AccessContext/证据读取稳定，guest 默认拒绝私有数据 |
| P1 存储与投影 | T5-T7 | 独立 DB 单写者工作，completed 唯一投影，失败/取消为零 episode |
| P2 召回与共同记忆 | T8-T9 | ACL-first 可证明，未确认 shared 不可见 |
| P3 遗忘与迁移 | T10-T11 | 六路径删除无复活，旧 Brain 零新写 |
| P4 产品闭环 | T12-T13 | 管理表面、全回归、真实安装验收记录齐全 |

---

## 4. 工作包

### Task 1：冻结对象与命令合同

**依赖：** L0/L1 当前主分支；无 L2 任务依赖。  
**创建：** `core/life/memory/__init__.py`、`core/life/memory/contracts.py`、`tests/test_memory_contracts.py`。  
**禁止修改：** store、runtime、gateway。

实现 immutable dataclass/enum：`AccessContext`、`ExperienceEpisode`、`JournalEntry`、`SharedMemory`、`UserModelClaim`、`RelationshipEvent`、`Subject`、`SessionParticipant`、`DerivationEdge`、`DeletionRequest`，以及 command/result：

```python
ProjectTerminal
CreateJournalEntry
ProposeSharedMemory
ConfirmSharedMemory
CorrectMemory
ForgetMemory
MigrateLegacyBatch
RecallQuery
RecallBundle
```

要求：严格字段、UTC、bounded text、canonical JSON/hash、unknown-field rejection、owner/audience/privacy/status 枚举；relationship 相关合同不得 import `ToolRegistry`、runtime permission 或 approval 类型。

**测试：** round-trip、坏枚举、超长正文、时间/ID 控制字符、canonical hash 稳定、frozen mutation、九对象覆盖扫描。  
**出口：** 合同测试全绿，后续任务只依赖这些类型，不传裸 dict 作为正式写命令。

### Task 2：给 ConversationStore 增加只读终态证据合同

**依赖：** T1。  
**修改：** `core/conversation_store.py`。  
**创建：** `tests/test_conversation_terminal_evidence.py`。  
**回归：** `tests/test_conversation_store.py`、`test_conversation_atomic_acceptance.py`、`test_conversation_hub.py`。

增加：

```python
store.source_store_id() -> str
store.scan_terminal_events(after_row_id=0, limit=...) -> TerminalEventPage
store.read_request_evidence(session_id, request_id) -> RequestEvidence
store.redact_request_evidence(session_id, request_id, deletion_id) -> RedactionReceipt
```

`source_store_id` 首次初始化写入 `conversation_meta`；scan 使用 SQLite internal row ID 作为 opaque cursor，同时返回 session sequence/domain。evidence 在单个只读 transaction 中返回 accepted event、完整/中断 messages、相关 events、terminal 冲突状态和安全 access projection。

redact 仅供 T10 调用：硬删 message 正文，敏感 event payload 改为无正文 tombstone，保留 event ID/type/sequence、request idempotency 和 deletion ID。重复调用返回同 receipt。

**测试重点：** terminal 后 assistant message 稍后到达时 `evidence_ready=False`；同 request 双 terminal 被标冲突；existing `history/events_after/accept_request` 返回不变；redact 后 sequence replay 和 duplicate acceptance 仍成立。  
**出口：** Memory projector 不需读私有表结构或猜测终态，旧会话 API 全回归。

### Task 3：实现独立 MemoryStore schema

**依赖：** T1。  
**创建：** `core/life/memory/store.py`、`tests/test_memory_store.py`。  
**数据位置：** 只接受 runtime 传入的 `<data_root>/memory/autobiographical.sqlite3`。

实现 versioned migration 和设计规格第 7 节所有表/索引/唯一约束。写连接只能由注入的 writer token/thread 使用；read connection factory 强制 `query_only=ON`。FTS5 不可用时服务 degraded，不能降级成无 ACL 的 legacy search。

提供 writer-internal transaction primitives 和 public read methods；public API 不暴露任意 SQL。每次 mutation 同步更新 item envelope、ACL/FTS、ACL epoch/index generation。

**测试：** 路径独立、WAL/FK、schema upgrade、duplicate terminal、FK closure、非 writer thread write rejection、read connection write rejection、FTS consistency、损坏 migration read-only recovery。  
**出口：** 空 DB/reopen/migration 可重复，其他任务无直接 sqlite3 写入。

### Task 4：生成服务端 AccessContext 与最小主体绑定

**依赖：** T1、T3；使用现有 runtime capability。  
**创建：** `core/life/memory/access.py`、`tests/test_memory_access_context.py`。  
**修改：** `core/runtime_access.py`、`core/conversation_protocol.py`、`gateway/conversation_ws.py`、`core/conversation_hub.py`。

步骤：

1. runtime authorizer 暴露不含 token 的 immutable server principal（boot、client hash、scopes、expiry）；
2. MemoryStore 首次启动创建 Javis subject；primary user 仅通过 packaged desktop 明示绑定创建；
3. `AccessContextFactory.for_session()` 读取 active `SessionParticipant` 并生成 bounded context；
4. gateway 从 WS server scope 获取 principal，绝不从 payload 读取 subject/owner/audience；
5. `ConversationHub` 将 context 安全投影写入 `request.accepted`；
6. `ConversationRequest` 添加可选内部字段并提供 guest default，保持现有构造器兼容；
7. protocol 对 client 保留字段返回 `reserved_access_field`。

L2 只支持 Javis + primary user + per-session guest；known person 与多人生命周期由 L3 开启。未绑定、过期、store 不可用、session mismatch 全部 guest。

**测试：** 恶意 owner/subject/audience 注入、任意本地网页、过期 capability、boot rotate、跨 session context、未绑定 session、context serialization 无 token/nonce、旧直接构造器。  
**出口：** 每个新 accepted request 有服务端 safe projection；所有未知情况为 guest，绝不推测 primary user。

### Task 5：组装 MemoryService 单写者与运行时生命周期

**依赖：** T2、T3、T4。  
**创建：** `core/life/memory/service.py`、`tests/test_memory_service.py`。  
**修改：** `core/runtime.py`、`core/life/service.py`（仅状态引用）、`main.py` lifespan。

`MemoryService` 拥有 bounded priority queue、唯一 writer thread、read pool、terminal reconcile timer、deletion worker 和 cache。Runtime 创建顺序：ConversationStore → LifeService → MemoryStore/Service → Agent/Hub 接线；shutdown 先停止接收 recall/mutation，再 drain critical，最后关连接。

Memory DB failure 只令 `memory.state=degraded`；ConversationHub、presence、voice、L0/L1 保持运行。private recall 在 degraded 时返回空 + reason，不读 legacy。

**测试：** 单写者、queue pressure、critical command 不静默丢、startup migration failure、shutdown drain、receipt failure 不推进 cursor、MemoryService unavailable 会话仍完成。  
**出口：** Runtime 只有一个新 memory writer，status 可诊断且不含正文。

### Task 6：实现 terminal receipt 幂等投影

**依赖：** T5。  
**创建：** `core/life/memory/projection.py`、`tests/test_memory_terminal_projection.py`。  
**修改：** `core/life/memory/service.py`；`core/conversation_hub.py` 只增加 terminal wakeup，不等待结果。

实现 EventBus wakeup + durable scan reconciliation。每条 terminal 先写/更新 `terminal_projection_receipts`；completed 且 evidence ready 才进入 candidate，failed/cancelled/interrupted/guest/suppressed 写 excluded。pending 使用有界 backoff，transaction 成功后推进 cursor。

fixture 必须覆盖：duplicate wakeup、process crash、late assistant message、session sequence 相同但 domain 不同、terminal conflict、queue full 后 reconcile、重启 scan、删除 suppression。

**硬断言：** 对所有 `request.failed`、`request.cancelled` 和 L1 `interrupted_by_restart` fixture，以下 count 恒为 0：episode、journal、shared、claim、relationship。  
**测试：** `tests/test_memory_terminal_projection.py` 运行上述全部 fixture，并断言重复 wakeup/restart scan 不增加 receipt 或 episode count。  
**出口：** 一百万次同 terminal 重放语义上仍只有一条 receipt 和至多一条 episode candidate。

### Task 7：实现受治理 episode 与 journal

**依赖：** T6。  
**创建：** `core/life/memory/extraction.py`、`tests/test_memory_episode_extraction.py`。  
**修改：** `core/life/memory/service.py`、`core/life/memory/store.py`。

先实现 deterministic selector：explicit remember、confirmed decision/boundary、verified goal/milestone。模型 extractor 为可选 adapter，只产结构化 candidate；测试默认 fake extractor。校验每个 summary 的 source refs、epistemic label、privacy、length 和 no-new-fact policy。

Journal 只从 active episode 构建，空日不写；每条必须有 derivation edge。首版不自动生成 `UserModelClaim` 或 `RelationshipEvent`。

**测试：** 普通问答/not selected、exact invocation/not selected、明确记忆请求、事实/主张区分、工具结果无验证不升级、模型幻觉字段拒绝、journal 无 source 拒绝。  
**出口：** 可跨重启读取真实 completed episode，citation 可回到 ConversationStore evidence。

### Task 8：实现 ACL-first recall 与 request-scoped prompt

**依赖：** T5、T7。  
**创建：** `core/life/memory/recall.py`、`tests/test_memory_acl_recall.py`、`tests/test_memory_prompt_integration.py`。  
**修改：** `gateway/conversation_ws.py`、`core/agent.py`、`core/prompt_builder.py`、`tools/memory_tools.py`。

实现 temp visible ID table → FTS/rank 的两阶段 read transaction；所有 query 都必须传 `AccessContext`。`RecallBundle` 限制 8 items/4 KiB。cache key 含 actor、participant hash、purpose、ACL epoch、index generation。

gateway 在调用 `Agent.chat()` 前用 server context 召回；Agent 新参数均可选。删除旧 `MemoryController.context_block()` 正式调用和全局 Brain Layer2 注入。`PromptBuilder` 接收 request-scoped bundle，不跨请求/subject 缓存。

**测试：** 使用 sqlite trace/spy 证明不可见 item 未进入 rank 输入；两个 owner 查询同关键词互不泄漏；guest 空结果；revoked ACL/cache epoch 立即失效；错误时不回退旧 Brain；旧 `Agent.chat(user_input, session_id=...)` 能运行且 memory 为空；Live fast path/精确呼名不增加模型或 recall 延迟。  
**出口：** 任何正式 prompt 内长期文本都能对应当前 AccessContext 下的 RecallBundle。

### Task 9：实现共同记忆显式确认

**依赖：** T3、T4、T7、T8。  
**创建：** `tests/test_shared_memory_confirmation.py`。  
**修改：** `core/life/memory/contracts.py`、`core/life/memory/service.py`、`core/life/memory/store.py`。

实现 propose/confirm/reject/revoke commands；proposal 默认不建 active FTS row、不进正常 recall。confirm 校验 primary user actor、participant、proposal revision、source live、`memory.manage` scope 和 idempotency key，在单 transaction 写 confirmation/ACL/FTS。

**测试：** assistant 文本“确认”、普通 approval、重复 confirm、stale revision、guest、nonparticipant、source deleted、reject/revoke、确认后跨重启 recall。  
**出口：** 只有独立 confirmation receipt 能让 shared item active；relationship/熟悉度字段不存在于判定调用图。

### Task 10：实现纠正、删除、反复活与 reindex

**依赖：** T2、T3、T6、T8、T9。  
**创建：** `core/life/memory/deletion.py`、`tests/test_memory_deletion.py`。  
**修改：** `core/life/memory/service.py`、`core/life/memory/store.py`、`core/conversation_store.py`、`core/agent.py`、`core/prompt_builder.py`。

实现 deletion state machine、立即 fence、derivation closure、source suppression、derived-only/source-and-derived、cache/prompt invalidation、startup resume 和 live-only reindex generation swap。

**测试：** `tests/test_memory_deletion.py` 的矩阵必须逐一断言目标 token 不存在于：

1. Memory DB live row；
2. FTS query；
3. recall cache；
4. 新/续接 prompt 与 subagent context；
5. 进程重启后的 recall；
6. full reindex 后 recall。

额外测试每个 state crash、terminal replay、shared/claim/journal derivation、WAL checkpoint、source redaction 与 duplicate request。物理介质擦除不作为自动化承诺。

**出口：** 六路径 + replay 全绿；任一 worker 失败时 fence 保持，API 不返回假 `completed`。

### Task 11：隔离旧 Brain 并执行可审阅迁移

**依赖：** T5、T8、T10。  
**创建：** `core/life/memory/migration.py`、`tests/test_memory_legacy_migration.py`。  
**修改：** `knowledge/brain.py`、`knowledge/learner.py`、`memory/controller.py`、`memory/episodic.py`、`memory/indexer.py`、`core/agent.py`、`core/runtime.py`、`main.py`、`tools/memory_tools.py`。

首先加入 hard read-only mode，并通过 source scan 禁止正式 runtime 调用 `learn_fact`、`record_experience`、`learn_style`、episode finish、memorize/consolidate、旧 mutation API。startup knowledge injection 不再写用户数据根。

迁移实现 manifest → parse → quarantine/candidate → batch copy → hash/count verify。默认不导入 prompt，不自动 active，不删除 legacy。重复批次幂等。对旧权限语句、无 owner、自动总结、来源丢失内容强制 quarantine。

**测试：** monkeypatch 所有旧写口计数为 0；正式运行一轮 completed/failed/cancelled 后 `brain_data` tree hash 不变；migration 重跑不重复；损坏 JSON 单项隔离；失败回滚不删旧文件。  
**出口：** 新对话只写 ConversationStore + MemoryService；旧 Brain 是明确标识的只读归档。

### Task 12：增加受控 API 与记忆管理表面

**依赖：** T4、T8、T9、T10。  
**创建：** `core/life/memory/api.py`、`app/src/memory/memoryTypes.ts`、`app/src/memory/MemoryClient.ts`、`app/src/memory/MemorySurface.ts`、`app/src/memory/memoryState.ts`、`app/tests/memoryClient.test.ts`、`app/tests/memorySurface.test.ts`。  
**修改：** `main.py`、`core/runtime_access.py`、`app/src/bridge/runtimeAccess.ts`、`app/src/bridge/backendEndpoints.ts`、`app/src/panels/DrawerManager.ts`、`app/src/main.ts`、`app/src/styles.css`。

API 严格按规格第 13 节。router dependency 先验证 runtime capability，再从 request principal/session 生成 AccessContext。body 中禁止 owner/audience/subject。mutation 返回 command ID/status，不等待模型 extraction。

管理表面提供：按来源查看 episode/journal、共同记忆 proposal confirm/reject/revoke、纠正、遗忘 scope 与进度、legacy quarantine 状态。删除确认必须明确 `derived_only` 与 `source_and_derived` 差异；不展示内部 subject IDs、token 或 ACL 实现细节。

**测试：** 401/403/guest、scope separation、double click idempotency、stale revision、删除进度、文本溢出/键盘可用性；无空白营销页。  
**出口：** 用户能完成“查看来源 → 确认共同记忆 → 召回 → 遗忘 → 验证消失”的完整闭环。

### Task 13：兼容、发布合同与真实验收

**依赖：** T1-T12。  
**创建：** `tests/test_l2_compatibility.py`、`tests/test_l2_release_contract.py`、`docs/verification/JAVIS_L2_D_DRIVE_ACCEPTANCE.md`。  
**修改：** `docs/JAVIS_OPERATIONS.md`；不得把未执行手工项写成 PASS。

release source scans：

- Memory DB writer 只有 `MemoryService` worker；
- 正式 Agent 路径不 import `memory.controller`/legacy indexer；
- authorization 模块不 import relationship/user-model；
- 无 SQL `FTS MATCH` 路径缺失 visible ID 前置；
- 无 client owner/audience/subject 信任；
- failed/cancelled projector 没有 episode insert 分支；
- 删除 invalidation 覆盖 prompt/reindex/restart fixture；
- legacy runtime 无写调用。

真实验收记录：跨天/重启 recall、source citation、shared explicit confirmation、guest privacy、模型切换、SQLite 故障降级、D 盘安装/升级/卸载数据保留。未执行项保持 `NOT EXECUTED`。

**出口：** P4 全部自动门通过，手工证据真实记录；没有证据不得宣称 L2 完成。

---

## 5. 精确测试命令

在仓库根目录执行：

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest `
  tests/test_memory_contracts.py `
  tests/test_memory_access_context.py `
  tests/test_conversation_terminal_evidence.py `
  tests/test_memory_store.py `
  tests/test_memory_service.py `
  tests/test_memory_terminal_projection.py `
  tests/test_memory_episode_extraction.py `
  tests/test_memory_acl_recall.py `
  tests/test_shared_memory_confirmation.py `
  tests/test_memory_deletion.py `
  tests/test_memory_legacy_migration.py `
  tests/test_memory_prompt_integration.py `
  tests/test_l2_compatibility.py `
  tests/test_l2_release_contract.py -q

& 'G:\Javis\venv\Scripts\python.exe' -m pytest `
  tests/test_conversation_store.py `
  tests/test_conversation_atomic_acceptance.py `
  tests/test_conversation_hub.py `
  tests/test_conversation_gateway.py `
  tests/test_unified_conversation_integration.py `
  tests/test_continuous_voice_gateway.py `
  tests/test_runtime_access.py `
  tests/test_life_service.py `
  tests/test_l1_life_service.py `
  tests/test_turn_experience_receipts.py `
  tests/test_l1_release_contract.py -q

& 'G:\Javis\venv\Scripts\python.exe' -m pytest -q
pnpm --dir app test
pnpm --dir app exec tsc --noEmit
pnpm --dir app build
```

PowerShell 不展开 pytest glob；需要 glob 时先用 `Get-ChildItem` 生成数组。不得为了通过测试覆盖正式 `data_root` 或读写真实 `brain_data`；fixture 全部使用 temp root/copy。

---

## 6. 阶段停止条件

出现以下任一项立即停止当前阶段并修复，不继续叠加功能：

- canonical terminal 与 ConversationStore evidence 冲突；
- guest 或另一个 subject 能命中私人 FTS/缓存；
- failed/cancelled/interrupted 产生任意长期对象；
- shared proposal 未确认即进入 prompt；
- relationship/user-model 改变 capability、tool permission 或 approval；
- 删除后任一 DB/FTS/cache/prompt/restart/reindex 路径复活；
- runtime 正式路径仍向旧 Brain 写数据；
- MemoryService 故障阻塞会话、语音打断或 L0/L1；
- 为通过门禁破坏现有 wire/public API 必填字段。

---

## 7. 完成定义

L2 完成必须同时满足：

1. 服务端 AccessContext 与 guest fail-closed 有攻防测试；
2. ConversationStore completed terminal 可幂等投影为有来源的 episode；
3. 所有失败/取消/重启中断 fixture 为零经历；
4. SQL owner/audience/ACL 先于召回排序；
5. 共同记忆只能经显式 confirmation active；
6. 遗忘通过 DB/FTS/cache/prompt/restart/reindex 和 terminal replay；
7. 旧 Brain 零新写且迁移可审阅、可重跑、可回退；
8. L0/L1、ConversationHub、voice、presence、legacy WS/API 回归通过；
9. 跨日/跨重启真实用户场景有来源 citation；
10. 所有未执行的安装/真实设备项诚实标记 `NOT EXECUTED`。
