# Javis L5 意图、承诺与目标验证实施计划

**日期：** 2026-08-20  
**状态：** 待实施  
**源码基线：** `G:\Javis` at `cc840778972f`  
**对应规格：** `docs/superpowers/specs/2026-08-20-javis-l5-intention-commitment-design.md`  
**目标：** 交付 `IntentionService` 单写、无原始 CoT、以 `GoalVerifier` 为完成门且受主动建议预算治理的生产闭环。

## 1. 实施不变量

1. 冻结链路为 `Conversation/event -> IntentionService -> Agent/Planner -> ActionExecutor -> GoalVerifier`。
2. Agent/Planner 只负责“怎样”，不得授权、写 Commitment、扩大预算或宣布 completed。
3. `request.completed`、Agent `done`、Planner completed、AgentRun completed 和 tool success 只作为过程证据。
4. 所有 L5 写命令有服务端 `AccessContext`、idempotency key、expected revision 和 evidence refs。
5. ThinkingCheckpoint 禁止原始 chain-of-thought、`reasoning_content`、messages、prompt 和 scratchpad。
6. EventBus handler 不同步访问 SQLite/模型/文件；Intention durable ack 使用有界异步 writer。
7. 未接通 L6 ActionExecutor 前，不允许通过临时 adapter 放行 mutating effect。
8. 每个任务先写失败测试，再实现最小代码；共享生产文件只由第 4 节指定的集成任务修改。

## 2. 外部依赖与启动条件

| 依赖 ID | 必需交付 | 阻塞范围 |
|---|---|---|
| `L0A` | `LifeService` identity/instance/boot、privacy/retention、canonical event | 全部 L5 |
| `L1` | canonical conversation observation、TurnExperienceReceipt、restart event | event ingest/recovery |
| `L2-A` | 服务端 `AccessContext`、owner/audience filter、deletion derivation hook | API、查询、迁移、删除 |
| `L3-A` | Subject/participant/guest 隔离与对象 ACL | 多人生产出口 |
| `L6-T1..T7` | L6 contracts、ApprovalAuthority、SafetyController、ActionJournal、ActionExecutor core | L5 生产 ActionRequest/verification 接线 |
| `L4` | `EnvironmentSnapshotV1` 只读端口 | 仅环境型 criterion；不阻塞纯会话/文件 read-back 验证 |

`L2-A` 或 `L3-A` 未合入时，可以完成纯 domain 单元测试，但不得用客户端 body 伪造 owner，也不得开启生产 API。`L6-T1..T7` 未完成时，L5 不得把旧 `ToolRegistry.execute()` 当作 ActionExecutor 替身。

## 3. 任务依赖图

```text
L5-T1 Contracts
  -> L5-T2 Store
  -> L5-T3 IntentionService state machine
       -> L5-T4 SuggestionBudget
       -> L5-T5 ThinkingCheckpoint
       -> L5-T6 GoalVerifier
       -> L5-T7 Event/Conversation intake
       -> L5-T8 Context and Agent-facing ports
       -> L5-T9 API and app projections
       -> L5-T10 Migration and boot recovery

L6-T1..T7 + L5-T3/T5/T6/T8
  -> L6-T11 shared production integration
       -> L5-T11 end-to-end and release evidence
```

并行许可：T4、T5、T6 在 T1/T2 后可并行；T7、T8 在 T3 后可并行；T9 依赖 T3/T4/T6；T10 依赖 T2/T3/T5/T6。`L6-T11` 是共享文件的唯一生产接线者。

## 4. 文件所有权

### 4.1 L5 专属

| 路径 | 所有任务 |
|---|---|
| `core/intention/__init__.py` | T1/T3 |
| `core/intention/contracts.py` | T1 |
| `core/intention/store.py` | T2 |
| `core/intention/service.py` | T3 |
| `core/intention/suggestions.py` | T4 |
| `core/intention/checkpoints.py` | T5 |
| `core/intention/verification.py` | T6 |
| `core/intention/event_adapter.py` | T7 |
| `core/intention/context.py`、`core/intention/ports.py` | T8 |
| `core/intention/api.py` | T9 |
| `core/intention/migration.py`、`core/intention/recovery.py` | T10 |
| `app/src/intention/*` | T9 |
| `tests/test_intention_*.py`、`app/tests/intention*.test.ts` | 对应 L5 任务 |
| `docs/verification/JAVIS_L5_D_DRIVE_ACCEPTANCE.md` | T11 |

### 4.2 共享文件的唯一所有者

以下文件不得由 L5 任务直接修改；统一由 L6 计划的 `L6-T11` 在 L5 模块测试通过后接线：

`core/agent.py`、`core/planner.py`、`core/agent_run_recorder.py`、`core/agent_runs.py`、`core/conversation_hub.py`、`core/prompt_builder.py`、`core/runtime.py`、`core/events.py`。

`main.py`、`gateway/conversation_ws.py`、`core/conversation_protocol.py` 由 `L6-T10` 所有；`app/src/main.ts`、`app/src/bridge/*`、现有 Settings/Drawer 文件由 `L6-T14` 所有。L5 只能提交新模块、契约测试和接线验收测试，不并发修改这些文件。

## 5. 任务清单

### L5-T1：冻结严格合同与 canonical hash

**依赖：** `L0A`  
**创建：** `core/intention/contracts.py`、`core/intention/__init__.py`、`tests/test_intention_contracts.py`

实现 `IntentionV1`、`SuccessCriterionV1`、`ExecutionBudgetV1`、`CommitmentV1`、`ThinkingCheckpointV1`、`VerificationRecordV1`、`CriterionResultV1`、`SuggestionBudgetV1` 和所有枚举。parser 必须严格拒绝未知字段、bool-as-int、NaN/Infinity、非规范 UTC、超限列表/文本、错误 common envelope 和 hash tamper。

实现 `JAVIS_CANONICAL_JSON_V1`，复用 L0 类型时只 import 稳定合同，不复制 privacy/identity 枚举。为每个 schema 固定 `schema_version=1`，写 golden fixture 与跨进程稳定 hash 测试。

**测试命令：**

```powershell
$env:TEMP='G:\Javis\tmp'; $env:TMP='G:\Javis\tmp'
python -m pytest tests/test_intention_contracts.py -q
```

**完成门：** 合同测试覆盖规格全部字段、大小边界和 forbidden key；无 `dict[str, Any]` 直接落库旁路。

### L5-T2：实现单写 Store、迁移与幂等事务

**依赖：** `L5-T1`  
**创建：** `core/intention/store.py`、`tests/test_intention_store.py`

在 `<data_root>/intentions/intentions.sqlite3` 建立 intentions、transitions、commitments、checkpoints、verification、suggestion budget、command idempotency 与 derivation 表。启用 WAL、foreign keys、busy timeout；写入由一个串行 writer 执行，读取使用独立连接。

实现：

- `execute(command, expected_revision, idempotency_key)` 原子返回；
- transition append + current row CAS 同事务；
- Intention/Commitment 状态一致性约束；
- verification immutable；
- suggestion consumption unique；
- integrity/hash scan、backup/rollback 与 schema migration fixture；
- bounded queue、critical reserve、shutdown drain 和 post-close rejection。

**测试：** duplicate command、同 key 不同 payload、双 writer、SQLite locked、crash transaction、foreign key、tamper、queue full、shutdown race。

### L5-T3：实现 IntentionService 与状态机

**依赖：** `L5-T2`、`L2-A` contract  
**创建：** `core/intention/service.py`、`tests/test_intention_service.py`、`tests/test_intention_state_machine.py`

实现命令：`create_candidate`、`accept_request`、`accept_candidate`、`activate`、`set_waiting`、`set_blocked`、`resume_after_evidence`、`begin_verification`、`apply_verification`、`cancel`、`expire_due`、`reserve_action_attempt`、`attach_receipt`。

每个命令校验 AccessContext owner/audience、source evidence、current boot/revision、expiry 和合法边。完成转换必须只从 `apply_verification` 进入，且强制全部 required criterion 满足。Commitment 与 Intention 同事务更新。

使用参数化测试穷举所有 state x state 边；终态重开、late event、cross-boot、cross-owner、stale revision 全部拒绝并留下无正文审计 code。

### L5-T4：实现 SuggestionBudget 原子门

**依赖：** `L5-T2`、`L5-T3`  
**创建：** `core/intention/suggestions.py`、`tests/test_suggestion_budget.py`

实现默认全局/分类窗口、timezone quiet hours、DST、cooldown、拒绝抑制、用户暂停、候选去重和呈现前原子消费。安全提示只绕过次数，不绕过安静模式的视觉/声音策略，也不产生执行授权。

测试冻结时钟下窗口边界、23/25 小时 DST 日、并发双消费、发送失败、重复 idempotency、拒绝 7 天、关系/情绪字段注入不改变预算。

### L5-T5：实现无原始 CoT 的 ThinkingCheckpoint

**依赖：** `L5-T1`、`L5-T2`  
**创建：** `core/intention/checkpoints.py`、`tests/test_thinking_checkpoints.py`、`tests/test_no_raw_cot_persistence.py`

实现 deterministic projector，只接受 plan/step/receipt/evidence ID 与有界结构化摘要。递归拒绝 forbidden keys 和同义大小写/分隔符变体；输出上限 16 KiB。

构造带 `reasoning_content`、嵌套 `messages`、prompt、scratchpad、token probability 和恶意 key 的 fixture，扫描 L5 DB、AgentRun DB、ConversationStore、EventBus capture、日志与错误响应，证明原始 CoT 零命中。接线后 `core/agent.py` 的 reasoning 仅存在瞬时模型调用对象，不写 Agent state/delta。

### L5-T6：实现 GoalVerifier registry 与完成门

**依赖：** `L5-T2`、`L5-T3`、`L6-T1` receipts contract  
**创建：** `core/intention/verification.py`、`tests/test_goal_verifier.py`

实现 verifier registry：`deterministic`、`trusted_adapter`、`user_confirmation`、`model_assisted_extractor`。每个 verifier 接收 immutable intention snapshot 和 evidence resolver，输出 VerificationRecord，不直接写状态。

实现 freshness、criterion 完整性、冲突检测、owner 可见性、receipt/recovery 链完整性和 verifier allowlist。外部 effect、高风险、权限、删除、发送、金额与安装 criterion 禁止 model-only satisfied。

测试：tool success 但目标未变、返回码 0 但文件内容不符、旧 revision、旧 boot、stale observation、主观目标缺确认、冲突证据、双 verifier 竞争、verification 后 CAS 崩溃与幂等重放。

### L5-T7：接入 Conversation/event intake 模块

**依赖：** `L5-T3`、`L5-T4`、`L1`、`L2-A`  
**创建：** `core/intention/event_adapter.py`、`tests/test_intention_event_adapter.py`、`tests/test_intention_conversation_intake.py`

实现两类入口：

1. ConversationHub 在 canonical `request.accepted` 成功后调用的 durable intake port；
2. EventBus 薄事件进入 bounded queue 后，从权威 store 重读的 candidate projector。

直接用户请求可 accepted；模型/环境/cron/恢复只能 candidate，除非存在明确、仍有效的用户 policy。实现 source deletion、event supersede、duplicate/乱序、queue overload、session owner 变化和 event TTL 测试。

生产 ConversationHub 修改留给 `L6-T11`；本任务使用 fake hub/port 验证协议。

### L5-T8：提供 Agent/Planner 只读上下文与端口

**依赖：** `L5-T3`、`L5-T5`、`L5-T6`  
**创建：** `core/intention/context.py`、`core/intention/ports.py`、`tests/test_intention_model_context.py`、`tests/test_agent_intention_boundary.py`

生成 `JAVIS_INTENTION_CONTEXT_V1`，最多 4096 UTF-8 bytes；只含当前 revision、why/goal/criterion 摘要、剩余预算、risk ceiling、Commitment/blocker 和允许输出类型。

定义 Agent-facing protocols：`propose_plan`、`propose_action_request`、`submit_checkpoint_candidate`、`request_verification`。协议中不存在 authorize、grant、set_state、complete 或 write_commitment。通过 fake Agent 证明它不能构造服务端主体、修改 budget 或调用完成入口。

### L5-T9：实现 scoped API 与前端只读投影

**依赖：** `L5-T3`、`L5-T4`、`L5-T6`、`L2-A`、`L6-T2` runtime scopes  
**创建：** `core/intention/api.py`、`app/src/intention/intentionTypes.ts`、`app/src/intention/IntentionBridge.ts`、`app/src/intention/IntentionPanel.ts`、`tests/test_intention_api.py`、`app/tests/intentionContracts.test.ts`

Router 提供：

- `GET /api/intentions`、`GET /api/intentions/{id}`、`GET /api/commitments`；
- `POST /api/intentions/{id}/accept|cancel|resume|verify`；
- `GET/PUT /api/intention/suggestion-budget`。

所有 mutation 使用 `intent.write` runtime scope、服务端 AccessContext、expected revision 与 idempotency key。没有 `POST .../complete`。API 不返回 ThinkingCheckpoint 内容、approval/grant 或其他主体 ID 存在性。

前端展示 why、状态、期限、blocker、criterion、验证依据和预算；“完成”只读 Intention state。已有 App shell/bridge 的挂载由 `L6-T14` 完成。

### L5-T10：迁移旧 run/plan 并实现启动恢复

**依赖：** `L5-T2`、`L5-T3`、`L5-T5`、`L5-T6`、`L6-T13` recovery contract  
**创建：** `core/intention/migration.py`、`core/intention/recovery.py`、`tests/test_intention_migration.py`、`tests/test_intention_recovery.py`

迁移只读扫描 AgentRunStore/ConversationStore：

- completed run/tool success 不导入 completed；
- 有 canonical request 证据的 nonterminal run 最多导入 `candidate(legacy_unverified)`；
- pending approval 交 L6 取消/过期；
- reasoning/delta 不复制；
- 记录 counts/hash/source cursor，不记录正文。

启动恢复执行 integrity/hash、expiry、旧 boot、uncertain action 与 verification freshness 分类。所有 effectful Commitment 进入 `blocked(action_reconciliation_required)` 或 waiting/ask；无任何自动 ActionRequest 重发。

### L5-T11：生产集成验收与发布证据

**依赖：** `L5-T1..T10`、`L6-T11`、`L6-T13`、`L6-T14`  
**创建：** `tests/test_intention_production_integration.py`、`tests/test_intention_release_contract.py`、`docs/verification/JAVIS_L5_D_DRIVE_ACCEPTANCE.md`

本任务不修改共享源码，只验收 `L6-T11` 的最终接线：

- text/voice/WS/HTTP request 先 durable Intention 再进 Agent；
- Agent emits done/end_turn/tool success 时 Intention 仍未完成；
- ActionReceipt 后进入 verifying，GoalVerifier 满足全部 criterion 后才 completed；
- cancel/interrupt 立即停止新 action，并保持已发生 effect 的 reconcile 证据；
- restart 只恢复 Commitment/checkpoint，不恢复 reasoning/grant/待执行调用；
- UI 状态与权威状态一致。

D 盘验收记录精确 commit、安装包 SHA-256、Windows/WebView2、用户主体、请求/承诺/动作/验证 ID、重启点、安静时段和 `NOT EXECUTED` 项，不记录用户正文或 secret。

## 6. 测试矩阵

| 能力 | 单元 | 状态机/属性 | 集成 | 恢复 | App/D 盘 |
|---|---|---|---|---|---|
| Contracts/hash | T1 | unknown/tamper/size | API strict parser | schema migration | wire compatibility |
| Intention/Commitment | T3 | 全边 + stale CAS | Conversation intake | old boot/expiry | 状态/来源展示 |
| Execution budget | T3 | concurrency/loop | Agent action request | crash refund rule | 剩余预算可见 |
| SuggestionBudget | T4 | quiet/cooldown/DST | event candidate | restart window | 静音/拒绝体验 |
| ThinkingCheckpoint | T5 | forbidden recursive keys | AgentRun/log scan | checkpoint restore | 不显示内部推理 |
| GoalVerifier | T6 | criterion matrix | real receipts/read-back | CAS replay | “完成”一致 |
| Subject/privacy | T3/T7/T9 | owner/ACL matrix | guessed IDs | subject change | visitor session |
| Degradation | 各模块 | queue/store failure | L0/L1 + stop | read-only mode | 可解释错误 |

## 7. 每任务验证纪律

每个任务执行：

```powershell
$env:TEMP='G:\Javis\tmp'
$env:TMP='G:\Javis\tmp'
$env:JAVIS_TEST_MODE='1'
python -m pytest <task tests> -q
git diff --check
```

集成出口执行：

```powershell
python -m pytest tests -q
npm --prefix app test
npm --prefix app run build
```

Rust/Tauri 仅在依赖已存在时执行 `cargo test`/`cargo check`；不得为完成文档或无明确批准而联网安装依赖。未运行项在验收文档写 `NOT EXECUTED`。

## 8. 迁移与切换顺序

1. 合入 L5 contracts/store/service，feature flag 默认 off，只跑 domain tests。
2. 合入 L6 contracts/executor 基础，建立 ActionReceipt/VerificationRecord 接口。
3. 运行 legacy dry-run inventory；不导入 completed，不复制 reasoning。
4. `L6-T11` 原子切换 Conversation/Agent/Planner/Runtime：新请求必须有 Intention；旧 run 只读投影。
5. 启用 L5 API/App 投影；移除任何从 AgentRun/tool result 推导“完成”的 UI 逻辑。
6. 执行 restart/reconcile 与 D 盘矩阵；稳定一个发布周期后再删除旧写路径，不删除旧审计数据。

切换后禁止 fail-open 回退到无 Intention Agent。Intention DB 不可写时，只保留 deterministic presence、只读查询、cancel/stop/recovery。

## 9. 阶段出口

- L5-T1..T10 定向测试与关联回归通过。
- `L6-T11` 生产链无旁路，Agent/Planner API 中无 authorize/complete。
- 原始 CoT 在所有持久层、日志、事件和错误响应中零命中。
- tool success/Agent done/AgentRun completed 无法完成目标；GoalVerifier 是唯一完成证据入口。
- 主动建议预算与 Commitment 跨重启真实生效。
- 全量 Python/App/build 和可用 Rust 门通过，D 盘证据可追溯。
- 若 L6 接线、多人边界或真机验证未完成，状态必须保持 `domain complete, stage NOT COMPLETE`。

