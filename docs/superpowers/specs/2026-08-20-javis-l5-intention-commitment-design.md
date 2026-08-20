# Javis L5 意图、承诺与目标验证设计规格

**日期：** 2026-08-20  
**状态：** 执行规格  
**源码基线：** `G:\Javis` at `cc840778972f`  
**依赖：** L0-A 身份与谱系、L1 规范事件与薄收据、L2 `AccessContext`、L3 主体/受众边界；L4 环境上下文是可选只读输入  
**后继：** L6 `ActionExecutor`、精确授权、行动收据与恢复

## 1. 决策摘要

L5 建立“为什么做、对谁承诺、预算多少、什么才算成功”的唯一权威。正式链路冻结为：

```text
Conversation / governed event
  -> IntentionService
       - why
       - commitment
       - execution/suggestion budget
       - success criteria
  -> Agent / Planner
       - how only
  -> ActionExecutor
       - authorize / execute / receipt / recovery
  -> GoalVerifier
       - verify user goal
  -> IntentionService
       - terminal transition only
```

以下规则不可被模型、Agent、Planner、工具、关系熟悉度或 UI 绕过：

1. `IntentionService` 是 `Intention`、`Commitment`、`SuggestionBudget` 和意图状态的唯一写入者。
2. Agent 与 Planner 只提出“怎样做”，不能授权动作、创建既成承诺或宣布目标完成。
3. `ActionExecutor` 的工具/适配器成功只证明一次动作返回成功，不证明用户目标成功。
4. 只有 `GoalVerifier` 产生的、绑定当前意图 revision 且覆盖全部必需成功标准的 `VerificationRecord`，才能使 `IntentionService` 转入 `completed`。
5. `request.completed`、Agent `done`、Planner `complete_plan()`、模型文本“完成了”和 `ActionReceipt.effect_succeeded` 均不是目标完成证据。
6. 瞬时推理、原始 chain-of-thought、`reasoning_content`、scratchpad、隐藏提示词和模型 token 轨迹不得进入 L5 持久化对象。
7. 重启可以恢复承诺证据和安全检查点，不能恢复模型瞬时思考，也不能盲目继续副作用。
8. 主动建议必须先原子消费可见预算；关系、情绪和模型热情不能提高预算。

旧文档中的 `IntentService` 名称由本规格统一为 `IntentionService`。不得保留两个服务或兼容写权威；旧名称最多作为迁移期只读别名。

## 2. 当前源码事实与缺口

当前实现可复用 `ConversationStore`、`ConversationHub`、`EventBus`、`AgentRunStore`、`Planner`、取消令牌、L1 receipt 与 Life boot ID，但它们尚未形成 L5 真值边界：

| 当前事实 | 源码位置 | L5 裁决 |
|---|---|---|
| Agent 以最后一次工具结果推导 `done.success` | `core/agent.py::_completion_event` | 删除目标完成语义；仅保留 response/run 终止语义 |
| `end_turn` 可直接调用 `planner.complete_plan()` 并输出“任务完成” | `core/agent.py` | `end_turn` 只能结束思考轮次，不能完成 Intention |
| Planner 的 `completed` 由任务节点状态自动产生 | `core/planner.py` | 仅为计划投影；不得映射为承诺或目标终态 |
| AgentRunRecorder 把 `done` 写成 completed run | `core/agent_run_recorder.py` | run completed 仅表示 Agent 流结束，不表示目标验证成功 |
| Agent 会保存 `reasoning_content` 和自由文本 delta | `core/agent.py`、`core/agent_runs.py` | 不得迁入 ThinkingCheckpoint；L5 后停止持久化原始推理 |
| Conversation 终态有 request completed/failed/cancelled | `core/conversation_hub.py` | 继续作为会话证据，但与 Intention 终态分离 |
| 旧 run `resume_run()` 可恢复到 running | `core/agent_runs.py` | 不得据此恢复承诺执行；先进入 L5/L6 reconciliation |

因此 L5 不是给现有 Planner 再加一个状态字段，而是增加独立权威并降级现有 run/plan 状态的语义。

## 3. 边界与职责

### 3.1 IntentionService

负责：

- 从已持久化的 conversation/event 证据创建候选或接受直接用户请求；
- 固化用户可见的 `why_summary`、目标、成功标准、预算、期限和取消条件；
- 创建并原子更新 Commitment；
- 原子消费执行预算和 SuggestionBudget；
- 接收 Agent/Planner 的候选计划与安全检查点，但不接收原始推理；
- 接收 ActionReceipt/RecoveryReceipt 引用并决定 waiting、blocked 或 verifying；
- 接收 GoalVerifier 的 VerificationRecord 并执行最终 CAS 状态转换；
- 启动时扫描未终态承诺，执行过期、阻塞与恢复分类。

### 3.2 Agent / Planner

只能读取 `IntentionExecutionContextV1`，并输出：

- `PlanProposal`：步骤、依赖、预期观察和资源估算；
- `ActionRequest` 候选：交给 L6，不携带授权；
- `ThinkingCheckpointCandidate`：结构化、可解释的进度摘要；
- `VerificationRequest`：请求 GoalVerifier 检查某组 criterion。

Agent/Planner 不能调用任何 `mark_completed`、生成 grant、解析 approval 为权限、改变 Commitment、补写成功标准或扩大预算的接口。

### 3.3 ActionExecutor

L5 只消费 L6 返回的不可变收据引用。ActionExecutor 不写 Intention 状态，不声称目标成功；一次动作的成功、失败、取消或不确定只触发 IntentionService 重新判断下一状态。

### 3.4 GoalVerifier

GoalVerifier 是验证执行器，不是意图状态写者。它读取当前 Intention revision、criterion、ActionReceipt、RecoveryReceipt 和新的目标观察，生成 `VerificationRecord`；IntentionService 再校验其完整性与新鲜度。

### 3.5 UI 与模型

UI 只能发命令和读投影；模型只能读预算快照。用户可见的“已完成”徽标必须来自 Intention `state=completed`，不得从流结束、工具绿色状态或 AgentRun `completed` 推导。

## 4. 公共合同规则

所有 L5 持久对象使用严格 wire schema，拒绝未知字段。除专门说明外均包含：

`schema_version`、对象 ID、`javis_identity_id`、`instance_id`、`owner_subject_id`、`participant_ids`、`audience`、`source_event_ids`、`runtime_boot_id`、`created_at_utc`、`updated_at_utc`、`expires_at_utc`、`privacy_class`、`retention_class`、`state`、`revision`、`provenance`、`content_hash`。

公共规则：

- ID、revision、sequence 与时间由服务端生成；客户端不能声明 owner 或主体。
- 时间使用 UTC RFC3339；到期判断使用服务端时钟，排序使用 revision/sequence。
- canonical hash 使用 `JAVIS_CANONICAL_JSON_V1`：UTF-8、NFC 字符串、键排序、无多余空白、拒绝 NaN/Infinity，并排除 `content_hash` 自身。
- 每个写命令必须含 `idempotency_key` 与 `expected_revision`；重复命令返回同一结果，旧 revision fail closed。
- 任何 source event 被删除、supersede 或失去主体可见性后，派生对象必须进入重新验证或抑制流程。
- EventBus 只发布薄事件和 ID，不发布 why 正文、成功标准正文、检查点摘要或用户内容。

## 5. Intention 合同

`IntentionV1` 表达“为什么现在值得做什么”，不是执行计划。

| 字段 | 约束 |
|---|---|
| `intention_id` | 服务端 UUID/ULID；全局唯一 |
| `kind` | `request | suggestion | commitment | goal | risk_response | maintenance` |
| `trigger_kind` | `conversation | governed_event | schedule | recovery | explicit_command` |
| `source_event_ids` | 1..32 个可见、可追溯证据；不得引用原始媒体 |
| `why_summary` | 用户可见，1..1024 UTF-8 bytes；说明形成原因，不含隐藏推理 |
| `expected_user_value` | 0..512 bytes；不能用情感施压 |
| `goal_statement` | 1..2048 bytes；可由用户纠正并产生新 revision |
| `success_criteria` | 1..16 个 `SuccessCriterionV1`；创建 active 状态前冻结 |
| `execution_budget` | `max_plan_steps`、`max_action_attempts`、`max_wall_seconds`、`max_model_tokens`；范围有界 |
| `risk_ceiling` | `none | low | medium | high | irreversible`；只是上限，不是授权 |
| `capability_hints` | Planner 可考虑的 capability 名称；不是 grant |
| `urgency` | `background | normal | time_sensitive | safety` |
| `interruption_cost` | `silent | low | medium | high` |
| `valid_until_utc` | 必填；无无限期主动意图 |
| `cancellation_conditions` | 0..8 个结构化 code；不得执行自由代码 |
| `commitment_id` | 可空；承诺存在时必须指向同一 intention/owner |
| `latest_checkpoint_id` | 可空；只指向合法 ThinkingCheckpoint |
| `latest_verification_id` | 可空；不能据此绕过 revision 检查 |
| `state` | 第 10 节定义的状态机 |
| `state_reason_code` | 稳定 code；自由说明另存受限展示层 |

`SuccessCriterionV1` 必须含 `criterion_id`、用户可读描述、`required`、`verifier_kind`、`evidence_requirements`、`freshness_seconds`、`subjective` 与可选 `user_confirmation_required`。成功标准不能由 Agent 在执行后改写来迎合结果；变更会增加 Intention revision，并使旧 verification 失效。

执行预算由 IntentionService 原子保留/消费。动作尝试在提交 ActionExecutor 前消费一次，失败和取消也计数，以阻断循环；仅因 Executor 在副作用前拒绝且明确 `effect_not_started` 时可按策略返还。Agent 自报 token/step 数不作为权威。

## 6. Commitment 合同

`CommitmentV1` 表达 Javis 已明确承担并需要跨回合跟踪的义务。普通请求不必自动形成长期 Commitment；模型文本“我会处理”也不能创建它。

| 字段 | 约束 |
|---|---|
| `commitment_id` | 服务端生成；一个 active Intention 最多一个当前 Commitment |
| `intention_id` | 必须指向同 owner、非终态 Intention |
| `accepted_from_event_id` | 用户请求、用户接受建议或明确预授权的证据 |
| `promise_summary` | 用户可见，1..1024 bytes；禁止夸大能力和保证不可控结果 |
| `due_at_utc` | 可空；若空则仍必须有 Intention expiry |
| `resume_policy` | `ask | reconcile_then_ask | no_effect_only` |
| `current_blockers` | 0..8 个结构化 blocker code/evidence refs |
| `next_review_at_utc` | waiting/blocked 时必填 |
| `last_checkpoint_id` | 只能引用同 Intention 当前 revision |
| `last_action_receipt_id` | 仅作证据引用，不代表完成 |
| `last_recovery_receipt_id` | 可空；不允许触发自动重放 |
| `verification_record_ids` | 有界引用列表；完整历史在独立表 |
| `state` | `active | waiting | blocked | verifying | fulfilled | failed | cancelled | expired` |
| `state_reason_code` | 必填稳定 code |

Commitment 与 Intention 在同一事务中更新：

| Intention state | Commitment state |
|---|---|
| `accepted` 且尚未承担 | 不存在 |
| `active` | `active` |
| `waiting` | `waiting` |
| `blocked` | `blocked` |
| `verifying` | `verifying` |
| `completed` | `fulfilled` |
| `failed/cancelled/expired` | 同名终态 |

不得出现 Intention completed 而 Commitment active，或 Commitment fulfilled 而 Intention 未完成。数据库约束与服务不变量测试必须同时覆盖。

## 7. ThinkingCheckpoint 合同

`ThinkingCheckpointV1` 是恢复所需的安全工作摘要，不是 chain-of-thought。

允许字段：

- `checkpoint_id`、`intention_id`、`commitment_id`、`plan_id`、`plan_revision`；
- `stage`：`planning | awaiting_action | awaiting_observation | awaiting_user | ready_to_verify | blocked`；
- `completed_step_ids`、`action_receipt_ids`、`evidence_refs`；
- `decision_summaries`：最多 8 条，每条为稳定 code + 240 bytes 用户可解释摘要；
- `assumptions`：最多 8 条，每条带来源、置信度和失效条件；
- `open_questions`：最多 8 条，不含内部提示词；
- `next_safe_step`：单个结构化建议，不是授权；
- `blocker_codes`、`budget_snapshot`、`created_at_utc`、`content_hash`。

整个对象 canonical JSON 不超过 16 KiB。解析器必须递归拒绝以下 key 或同义变体：

`chain_of_thought`、`cot`、`reasoning`、`reasoning_content`、`scratchpad`、`hidden_state`、`system_prompt`、`developer_prompt`、`messages`、`raw_model_output`、`logits`、`token_probabilities`。

检查点只由 deterministic `ThinkingCheckpointProjector` 从结构化候选、收据 ID 和已治理证据生成。不得截取模型 reasoning 再“总结后原样保存”；原始 reasoning 在回合结束后丢弃。日志、AgentRun delta、错误层和遥测也不得把原始 reasoning 作为旁路保存。

## 8. VerificationRecord 合同

`VerificationRecordV1` 是 GoalVerifier 对一组成功标准的不可变判断。

| 字段 | 约束 |
|---|---|
| `verification_id` | 服务端生成 |
| `intention_id`、`intention_revision` | 必须精确绑定当前 revision |
| `commitment_id` | 有 Commitment 时必填 |
| `requested_by` | `intention_service | user | recovery_reconcile`；Agent 只能提出候选请求 |
| `verifier_kind` | `deterministic | trusted_adapter | user_confirmation | model_assisted` |
| `verifier_version` | 固定实现/规则版本 |
| `criterion_results` | 每个 criterion 恰好一项，禁止漏项和重复 |
| `evidence_refs` | 只引用可见 ActionReceipt、RecoveryReceipt、环境事实或用户确认事件 |
| `observed_at_utc` | 目标观察时间，不等于工具结束时间 |
| `result` | `satisfied | unsatisfied | inconclusive` |
| `limitations` | 最多 8 个稳定 code/短摘要 |
| `conflicting_evidence_refs` | 有冲突时不得返回 satisfied |
| `content_hash` | 不可变 canonical hash |

每个 `CriterionResultV1` 含 `criterion_id`、`outcome`、`evidence_refs`、`fresh_until_utc`、`method` 和 `explanation_code`。

完成门：

```text
record.intention_revision == current.revision
AND every required criterion == satisfied
AND every evidence ref exists and is visible to owner
AND evidence is fresh and not superseded/deleted
AND no newer conflicting ActionReceipt/RecoveryReceipt exists
AND subjective criterion has matching explicit user confirmation
AND verifier kind is allowed by that criterion
```

`model_assisted` 只能提取或分类证据，不能单独满足外部副作用、高风险、金额、删除、安装、发送、权限或主观满意度标准。工具的 `success=true` 只能作为动作执行证据之一。

## 9. SuggestionBudget 合同

`SuggestionBudgetV1` 由 IntentionService 按 `(owner_subject_id, category)` 单写，防止主动性变成持续打扰。

| 字段 | 约束 |
|---|---|
| `budget_id` | 服务端生成 |
| `category` | 稳定 allowlist，如 `safety | reminder | workflow | wellbeing | maintenance` |
| `window_started_at_utc`、`window_ends_at_utc` | 固定窗口 |
| `max_presentations`、`presentations_used` | 原子计数，`used <= max` |
| `global_budget_revision` | 与用户全局主动设置绑定 |
| `cooldown_until_utc` | 同类建议在此之前禁止呈现 |
| `quiet_hours` | timezone ID + 本地起止时间；DST 由时区库处理 |
| `suppressed_until_utc` | 拒绝、关闭或用户暂停后的抑制 |
| `last_candidate_id`、`last_presented_intention_id` | 防重复 |
| `last_outcome` | `none | accepted | dismissed | rejected | expired` |
| `state` | `available | cooling_down | quiet | exhausted | suppressed` |

首个生产默认值应保守且可见：全局最多 2 次非安全主动呈现/24h，同类最多 1 次/24h，最短冷却 4h，明确拒绝后同类抑制 7 天，默认安静时段 22:00-08:00。安全风险可绕过“次数”但不能绕过权限、不能自动执行，并应合并去重。

候选生成不消费预算；真正向用户发送/发声前必须在事务中消费。发送失败仍计数，避免故障循环刷屏；只有在 UI 明确证明从未展示且同一 idempotency key 重试时返回同一消费结果。

## 10. Intention 状态机

状态集合冻结为：

```text
candidate -> accepted -> active -> waiting / blocked -> verifying
                                             |            |
                                             +------------+
                                                   |
                                      completed / failed / cancelled / expired
```

合法转换：

| From | To | 必需证据与规则 |
|---|---|---|
| none | `candidate` | governed event/model proposal；必须有 source 和 expiry |
| none/candidate | `accepted` | 直接用户请求、用户接受建议或明确 policy；主体必须已验证 |
| `candidate` | `cancelled/expired` | 用户拒绝/取消或 TTL 到期 |
| `accepted` | `active` | 成功标准、预算、风险上限已冻结；需要承诺时同事务创建 Commitment |
| `active` | `waiting` | 等待时间、外部状态或用户输入；必须有 `next_review_at_utc` |
| `active/waiting` | `blocked` | 权限、预算、安全、依赖或能力阻塞；必须有 blocker evidence |
| `waiting/blocked` | `active` | 阻塞证据已消失，revision 未变，且不触发旧动作重放 |
| `active/waiting/blocked` | `verifying` | 已有足够行动/观察证据，或纯对话目标可直接验证 |
| `verifying` | `active/waiting/blocked` | verification unsatisfied/inconclusive，仍可继续且预算有效 |
| `verifying` | `completed` | 第 8 节完成门全部成立；仅 IntentionService 可写 |
| 任一非终态 | `failed` | 不可恢复失败或预算耗尽且目标未满足；不能用单次工具失败直接推导 |
| 任一非终态 | `cancelled` | 用户取消、上游请求撤销或策略撤销；幂等且级联停止新 ActionRequest |
| 任一非终态 | `expired` | 服务端 expiry 到期；旧事件不能复活 |

所有终态不可重新打开。重试、用户重新提出或旧目标变化必须创建新 Intention，并以 `supersedes_intention_id` 建立关系。晚到的旧 boot、旧 request、旧 revision、旧 receipt 或旧 verification 只能记为审计拒绝。

## 11. Conversation 与事件接入

### 11.1 直接用户请求

`ConversationStore` 先原子写 `request.accepted`，再由 ConversationHub 调用 IntentionService 的有确认异步写入接口。Intention durable ack 成功后才允许进入 Agent/Planner。若 IntentionService 不可用：

- 确定性“我在”、本地取消、停止与只读恢复仍可用；
- 需要模型/计划/动作的新请求返回稳定 `intention_service_unavailable`；
- 不回退到无意图 Agent 路径。

### 11.2 Governed event 与主动建议

EventBus 同步 handler 只能把 ID 和分类后的薄元数据放入有界队列，不能同步访问 SQLite 或调用模型。后台 worker 从权威源重读事件、校验主体/隐私/TTL，再创建 candidate。队列满时合并低价值候选；安全事件保留容量并可触发一次去重提醒。

### 11.3 会话终态

`request.completed` 表示响应流完整终止；它可触发检查点或验证，但不能把 Intention 设为 completed。`request.failed/cancelled` 使 IntentionService 根据是否存在 Commitment、动作不确定性和恢复策略转为 waiting、blocked、failed 或 cancelled。

## 12. 模型上下文与承诺表达

`IntentionContextProjector` 每轮生成 `JAVIS_INTENTION_CONTEXT_V1`，只含：ID/revision、why 短摘要、目标、成功标准、剩余预算、风险上限、Commitment 状态、blocker code 和允许的下一类输出。总预算不超过 4096 UTF-8 bytes。

不包含：approval/grant、敏感工具参数、原始事件正文、原始推理、隐藏提示词、其他主体承诺或已删除证据。

模型可以提出“我可以继续做 X”，但用户可见的既成承诺语言必须由 UI/response projector 基于已存在 Commitment 渲染。模型自由文本不能创建、延长或兑现 Commitment。

## 13. 存储、并发与事件

权威数据库：`<data_root>/intentions/intentions.sqlite3`，至少包含：

- `intentions`、`intention_transitions`；
- `commitments`；
- `thinking_checkpoints`；
- `verification_records`、`verification_criteria`；
- `suggestion_budgets`、`suggestion_consumptions`；
- `intention_commands` 幂等结果表；
- `intention_derivations` 证据/删除闭包。

SQLite 使用 WAL、foreign keys、busy timeout 和短事务。写入经单 writer 或串行命令队列；API/Conversation async 路径不得在 event loop 同步执行 SQLite。每次状态转换追加不可变 transition row，并以 `expected_revision` CAS 更新当前行。

薄事件 allowlist：

- `intention.created/accepted/state_changed/expired`；
- `commitment.created/state_changed`；
- `intention.checkpoint.created`；
- `goal.verification.recorded`；
- `suggestion.budget.changed`。

payload 只含对象 ID、owner hash、revision、state、reason code、source event ID；不含目标正文、checkpoint 内容或 verification 证据详情。

## 14. 重启与恢复

启动扫描按以下顺序执行：

1. 验证数据库 integrity、schema、identity/instance 和对象 hash；失败时 L5 进入只读降级。
2. 终结已过期 candidate/intention/commitment。
3. 对上个 boot 的 `active/waiting/blocked/verifying` 读取最新 ThinkingCheckpoint 和 L6 收据。
4. 存在未终态或不确定 ActionReceipt 时转 `blocked(action_reconciliation_required)`。
5. 无副作用、证据完整且 `resume_policy=no_effect_only` 的工作可转 `waiting(recovery_review)`，由新 boot 重新计划。
6. `ask` 与 `reconcile_then_ask` 均向用户呈现恢复选择；不得自动重发 ActionRequest。
7. 旧 approval/grant 一律不可恢复；旧 boot verification 只有在 evidence freshness 和 revision 仍成立时可重放状态 CAS，不可重放动作。

ThinkingCheckpoint 只恢复已知事实、完成步骤引用、blocker 与下一安全步骤。不得恢复 `Agent.state.messages` 中的 reasoning、模型 scratchpad 或待执行工具调用。

## 15. 迁移

- 现有 `AgentRunStore` 保留为历史执行图和 UI 投影，不再是 Commitment/approval/完成权威。
- 旧 `runs.status=completed`、`Planner.status=completed`、tool success 和 conversation `request.completed` 不批量转换为 completed Intention。
- 有 canonical `request.accepted` 且主体可确认的旧 active run 可导入 `candidate`，附 `provenance=legacy_unverified`；用户接受后才产生 Commitment。
- 旧 pending approval 在 L6 迁移中取消/过期；不得据其恢复 action。
- 旧 Agent reasoning/delta 不导入 ThinkingCheckpoint；迁移扫描报告数量与位置，不复制正文。
- 迁移可重复、幂等、可回滚；旧库切为只读后保留一个发布周期，禁止双写。

## 16. 隐私、删除与降级

- Intention/Commitment 的 audience 与 owner 在查询前过滤；ID 猜测不得返回存在性差异。
- 删除 source event 时先抑制召回与执行，再重新验证派生对象；不能继续陈述已删除承诺内容。
- ThinkingCheckpoint、why、目标与标准不进入通用日志、错误消息、EventBus 或云模型，除非当前 intent 的模型可见策略明确允许最小投影。
- IntentionService 故障不能破坏 L0/L1 身份、会话 attach、打断、停止、关闭和 L6 恢复查询。
- GoalVerifier 故障时保持 `verifying` 或 `blocked(verifier_unavailable)`，绝不乐观完成。
- SuggestionBudget 故障时默认不主动建议；安全事件只允许一次去重的非执行提醒。

## 17. 验收矩阵

| 维度 | 必测场景 | 预期 |
|---|---|---|
| 合同 | unknown field、超长、非法 UTC、hash tamper、错误 owner | fail closed |
| 状态机 | 全合法边、全非法边、终态重开、stale revision | 仅合法 CAS 成功 |
| 真值 | tool success、Agent done、run completed、模型“完成” | 均不能完成 Intention |
| 验证 | 漏 criterion、旧 revision、过期证据、冲突证据、主观目标 | 不得 completed |
| 承诺 | 文本承诺、用户接受、取消、过期、blocked、跨重启 | 仅服务记录有效 |
| 检查点 | reasoning key、嵌套别名、16 KiB、日志扫描 | 原始 CoT 零持久化 |
| 建议预算 | quiet hours、cooldown、拒绝、并发消费、发送失败 | 不超预算、不重复施压 |
| 并发 | duplicate request、乱序 terminal、旧 boot receipt、双 verifier | 单事实、幂等 |
| 恢复 | 崩溃于 planning/action/verifying/CAS 前后 | 不重放副作用，状态可解释 |
| 主体 | guest、nonparticipant、跨 session ID 猜测 | 零泄漏 |
| 降级 | Store/Verifier/L6/L4 分别关闭 | 核心会话和停止仍可用 |
| 生产接线 | WS/HTTP/voice/cron event 到真实 IntentionService | 无旁路 Agent |

## 18. 阶段出口

L5 只有在以下条件全部满足时完成：

1. 每个进入 Agent/Planner 的生产请求都绑定一个 durable、当前 revision 的 Intention。
2. IntentionService 是 Intention/Commitment/SuggestionBudget 的唯一写者，Agent 无完成/授权入口。
3. 工具成功、Agent done、run completed 和流终态均无法绕过 GoalVerifier。
4. ThinkingCheckpoint 可跨重启恢复工作上下文，且数据库、日志、EventBus、AgentRun 与错误层中原始 CoT 零命中。
5. 主动建议预算、冷却、安静时段、拒绝抑制和并发消费全部生效。
6. 未完成承诺在重启后只进入可解释 waiting/blocked/reconcile，不盲重放动作。
7. L6 集成门证明所有 ActionRequest 经过 ActionExecutor；未完成 L6 接线时，L5 只能标记“domain complete”，不能宣称阶段出口完成。
8. 自动化、生产路径、恢复测试和 D 盘真实体验证据均可追溯；未执行项明确标记 `NOT EXECUTED`。

