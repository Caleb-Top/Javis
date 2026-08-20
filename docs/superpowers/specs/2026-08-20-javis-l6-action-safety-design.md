# Javis L6 行动授权、安全、收据与恢复设计规格

**日期：** 2026-08-20  
**状态：** 执行规格  
**源码基线：** `G:\Javis` at `cc840778972f`  
**依赖：** L0-A identity/instance/boot、L1 canonical events、L2 `AccessContext`、L3 ACL、L5 Intention/Commitment/GoalVerifier  
**权威：** `ActionExecutor` 是所有动作授权、执行编排、行动收据和恢复编排的唯一权威

## 1. 决策摘要

正式架构冻结为：

```text
Conversation / governed event
  -> IntentionService (why, commitment, budget, success criteria)
  -> Agent / Planner (how only)
  -> ActionExecutor
       Runtime scope
       AccessContext / object ACL
       Policy / risk / safety mode
       ApprovalAuthority
       exact one-use AuthorizationGrant
       synchronous pre-effect WAL barrier
       effect adapter
       hash-chained ActionReceipt
       reconciliation / RecoveryReceipt
  -> GoalVerifier
  -> IntentionService terminal transition
```

不可变规则：

1. Agent、Planner、子 Agent、cron、WS、HTTP、Control、ToolRegistry 与模型都不能授权自己。
2. 所有动作，不论来源和风险，均提交 `ActionRequest`；不存在第二条直接 effect 路径。
3. `AuthorizationGrant` 精确绑定 `action_name + parameters_hash + runtime_boot_id + intention_revision`，`max_uses` 固定为 1。
4. 裸 `confirmed: bool` 不再是授权合同；approval 由唯一 `ApprovalAuthority` 以结构化枚举决议并原子签发 grant。
5. 所有 mutating HTTP/WS 命令必须先通过命令级 runtime scope；loopback、Tauri origin、关系、root 模式或已有会话均不能代替 scope。
6. root token、permission change、approval resolution、fuse reset、rollback/recovery 也必须有各自 runtime scope；通用多次 root session 被删除。
7. 副作用前必须完成同步 WAL barrier；写入、commit、fsync 或 recovery material 任一步失败，effect adapter 调用次数必须为 0。
8. 安全模式必须在 adapter 之前拦截所有新 effect；只读查询、导出、停止与显式恢复控制面保留。
9. 崩溃后先观察和 reconcile，绝不根据 `prepared/running` 盲重放原动作。
10. ActionReceipt success 只表示动作 effect 观察，不表示用户目标成功；GoalVerifier/IntentionService 仍是目标完成门。

旧文档中的 `ActionService` 名称由本规格统一为 `ActionExecutor`。不得保留并行 ActionService、Boundary、ToolGuard approval 或 Agent approval 写权威。

## 2. 当前源码审计与上线前 P0

以下不是后续优化，而是 L6 enforce 前必须清零的 P0：

| P0 | 当前证据 | 必须达到的终态 |
|---|---|---|
| Mutating HTTP 无统一 scope | `main.py` 大量 POST/DELETE 直接调用 config、memory、evolution、workspace、control；只有部分 voice 路由调用 `_require_runtime_http` | 除 `/api/runtime/access` 的受所有权 bootstrap 外，每个 mutating route 有显式、精确 scope；route inventory 零缺口 |
| WS 只在握手检查 `conversation` | `gateway/conversation_ws.py::serve`；`tool`、`folder_file`、`permission_change` handlers 在 `main.py` 直接执行 | 每条 WS command 重新校验命令级 scope、expiry、boot 与 client binding |
| 直接 WS 工具执行 | `main.py::_handle_ws_tool -> registry.execute` | 构造 ActionRequest 并只调用 ActionExecutor |
| permission 直接修改 | `main.py::_handle_ws_permission_change`、`POST /api/config/permission` | `permission.write` scope + ActionRequest + ApprovalAuthority；唯一 PermissionEffectAdapter |
| approval 权威分裂 | Agent `_confirm_result`、ConversationHub `_pending_approvals`、AgentRunStore approvals、HTTP resolve API | 单一 ApprovalAuthority；其他模块仅持 projection/ref |
| 裸 confirmed bool | `Agent.resolve_confirm(bool)`、`ConversationHub.confirm(..., confirmed)`、`ToolRegistry.execute(confirmed=...)`、ToolGuard `pre_check(confirmed=...)` | 使用 `ApprovalResolutionV1.decision` 和 one-use grant；生产协议拒绝 `confirmed` |
| Agent 可宣告完成 | `Agent._completion_event`、`end_turn`、AgentRunRecorder done | run/response 终止与 L5 completed 分离 |
| 通用 root token 可多次使用 | `control/command_tasks.py::issue_root_token/_valid_root_token` | 删除通用 root session；每个 root action 使用 exact、boot-bound、one-use grant |
| fuse reset 无 scope/approval | `POST /api/control/fuse/reset` 直接 `reset_fuse()` | `control.fuse.reset` scope + local approval + reconcile/chain/WAL 健康前置条件 |
| 安全状态仅局部内存 | CommandTaskRunner `_fuse_reason` 只拦 command | SafetySnapshot 由 Executor 持有并在所有 effect adapter 前统一拦截 |
| 无同步 pre-effect WAL | ToolRegistry 和多数工具直接调用 handler；CommandTask rollback snapshot 失败可返回空后继续执行 | `prepared` + grant consume + recovery material durable 后才调用 adapter |
| 崩溃恢复可能把 run 置 running | `AgentRunStore.resume_run()` | effectful run 进入 reconcile/blocked；不自动重发 ActionRequest |
| Control/子代理/cron 旁路 | `CommandTaskRunner` 自执行 subprocess；`core/subagent.py` 直接 registry.execute；cron 直接写 job 文件 | 全部经过 ActionExecutor；AST/调用图测试禁止旁路 |

P0 通过之前不得启用“自动执行”“安全模式已保护”“可恢复”或“统一 approval”的产品声明。

## 3. 信任边界与权威

| 组件 | 可以 | 不可以 |
|---|---|---|
| RuntimeAccessAuthority | 认证当前本地客户端、boot、origin、peer、scope、TTL | 代表用户批准具体动作 |
| IntentionService | 决定 why、预算、risk ceiling、成功标准和承诺状态 | 签发 grant、调用 effect adapter |
| Agent/Planner/Subagent/Cron | 提议计划或 ActionRequest | 批准、持有通用权限、直接执行、宣布目标完成 |
| ApprovalAuthority | 管理唯一 approval 状态并签发 exact one-use grant | 修改 Intention、执行 effect |
| AuthorizationPolicy | 计算 deny/approval/allow-with-grant | 调用 effect 或因关系提权 |
| ActionExecutor | 校验、prepare、执行、收据、reconcile/recovery | 修改 L5 completed |
| EffectAdapter | 在 `ExecutionPermit` 下执行一个已规范化动作 | 读取 approval UI、签发 grant、绕过 WAL |
| GoalVerifier | 检查用户成功标准 | 将 adapter success 当作目标 success |
| UI | 呈现 preview、approval、receipt、recovery 并发结构化命令 | 发送 owner、伪造 boot/grant 或直接调用 adapter |

`ToolRegistry` 在 L6 后是 catalog + effect adapter registry，不再是公共执行权威。其 handler 只接受 Executor 进程内生成、不可序列化的 `ExecutionPermit`。

## 4. 公共合同规则

L6 持久对象遵循 L0/L5 common envelope、服务端 ID/UTC/revision、严格 unknown-field rejection 与 `JAVIS_CANONICAL_JSON_V1`。额外规则：

- 参数先按 action schema 验证和规范化，再计算 hash；日志脱敏后的参数绝不能参与授权比较。
- 规范化拒绝 NaN/Infinity、重复语义 key、控制字符、路径 traversal、symlink/junction 逃逸和大小写不确定目标。
- bearer/grant secret 只返回一次；持久化仅保存 SHA-256 digest，任何 diagnostics/repr/event/error 不得出现 secret。
- 所有收据不可变；状态变化通过追加新 receipt 表达，不 UPDATE 历史 receipt。
- 敏感原始参数默认只在单次执行内存中存在。WAL 保存 hash、允许的参数摘要和 reconcile 所需的最小 target locator；secret 不写 WAL。

参数摘要 hash 定义：

```text
parameters_hash = SHA256(
  "JAVIS_ACTION_PARAMETERS_V1\0" ||
  canonical_json({
    "action_name": action_name,
    "parameters": normalized_parameters,
    "target_scope": normalized_target_scope,
    "intention_id": intention_id,
    "intention_revision": intention_revision
  })
)
```

任何 action、参数、target、intention 或 revision 改动都会产生新 hash，并使旧 grant 无效。

## 5. ActionRequest 合同

`ActionRequestV1` 是所有入口提交给 Executor 的唯一动作请求。

| 字段 | 约束 |
|---|---|
| `action_request_id` | 服务端生成；全局唯一 |
| `intention_id`、`intention_revision` | 必须指向当前 nonterminal Intention；纯安全控制操作使用专用 system intention |
| `commitment_id` | 有 Commitment 时必填 |
| `owner_subject_id` | 从 AccessContext 注入，wire body 不接受 |
| `runtime_boot_id` | 当前 boot；旧 boot fail closed |
| `source_kind` | `agent | control_http | conversation_ws | subagent | cron | recovery | system_stop` |
| `source_ref` | request/run/subagent/job/recovery ID，最多 256 bytes |
| `action_name` | catalog 中稳定全名；不能别名漂移 |
| `capability` | action schema 固定 capability |
| `normalized_parameters` | 严格 schema；仅执行内存/受保护 command ingress 持有 |
| `parameters_hash` | 第 4 节定义；服务端重算并 constant-time 比较 |
| `target_scope` | workspace root/object/resource/recipient 等精确目标 |
| `effect_class` | `none | reversible | compensatable | irreversible` |
| `risk_class` | `low | medium | high | critical` |
| `preconditions` | 0..16 个结构化 predicate；adapter 执行前再次检查 |
| `expected_observations` | L5 criterion 所需 read-back 提示，不是成功声明 |
| `idempotency_key` | source 内唯一；重放返回同一 action record |
| `timeout_seconds` | action schema 范围内 |
| `requested_at_utc`、`expires_at_utc` | 短 TTL；过期不执行 |
| `policy_version` | 请求时评估版本；执行前仍重评 |

ActionRequest 不能包含 `confirmed`、`approved`、`root=true`、`permission_override`、`goal_completed` 或 bearer grant。grant 通过独立受保护参数传给 Executor。

## 6. AuthorizationGrant 合同

`AuthorizationGrantV1` 是 ApprovalAuthority/AuthorizationPolicy 对一个精确动作签发的一次性能力。

| 字段 | 约束 |
|---|---|
| `grant_id` | 服务端生成 |
| `grant_secret` | 256-bit bearer，仅签发响应出现一次；持久化为 `grant_secret_digest` |
| `approval_id` | auto-grant 时可空，但必须有 policy decision ID |
| `owner_subject_id`、`client_instance_hash` | 绑定批准主体与本地客户端 |
| `runtime_boot_id` | 必须等于当前 boot；重启即失效 |
| `intention_id`、`intention_revision` | 精确绑定 |
| `action_name` | 精确绑定，不支持 wildcard |
| `parameters_hash` | 精确绑定 |
| `capability`、`target_scope_hash`、`risk_class` | 精确绑定 |
| `issued_at_utc`、`expires_at_utc` | 高风险默认不超过 60 秒，其他不超过 5 分钟 |
| `max_uses` | schema 常量 `1` |
| `uses` | 仅 `0 -> 1`，与 WAL prepare 同事务消费 |
| `issuer_kind` | `policy | local_user_approval | recovery_approval` |
| `policy_version`、`safety_revision` | 执行前必须仍匹配或更严格重评 |
| `state` | `issued | consumed | expired | revoked` |

自动允许的低风险只读动作也必须由 policy 签发 one-use grant，以保持一条执行路径。任何 grant 都不能提升 Intention risk ceiling、对象 ACL 或 runtime scope。关系、熟悉度、模型输出和旧 permission level 不进入 grant 决策。

## 7. 统一 ApprovalAuthority

approval 状态机冻结为：

```text
pending -> approved -> grant issued -> grant consumed/expired/revoked
       \-> denied
       \-> expired
       \-> cancelled
```

`ApprovalRequestV1` 绑定 approval ID、ActionRequest ID、action name、parameters hash、target summary、risk/reversibility、preview hash、owner、client、boot、expiry。UI 必须展示上述精确内容。

`ApprovalResolutionV1` 使用：

```text
decision: "approve" | "deny"
approval_id
action_request_id
parameters_hash
runtime_boot_id
client_instance_hash
decided_at_utc
idempotency_key
```

生产合同中不再接受 `confirmed` 或裸 `approved: bool`。Authority 以 CAS 将 pending 转终态；重复同决议幂等，冲突决议拒绝。approved 只产生一个 exact AuthorizationGrant；Agent、ConversationHub 和 AgentRunStore 仅订阅投影，不保存第二份 pending authority。

approval response 必须具备 `action.approve` runtime scope、当前本地 client/boot、匹配 owner 和未过期请求。HTTP approval ID 猜测、cross-session、cross-client、旧参数 hash 与旧 boot 返回统一拒绝，不泄露对象存在性。

## 8. ActionReceipt 哈希链

`ActionReceiptV1` 是 append-only lifecycle receipt。每个阶段追加一条，不覆盖旧条目。

| 字段 | 约束 |
|---|---|
| `receipt_id` | 服务端生成 |
| `chain_id` | 当前 identity + instance 的 action chain |
| `chain_sequence` | 单调递增、数据库唯一 |
| `previous_receipt_hash` | genesis 使用固定零 hash |
| `content_hash` | canonical receipt hash，包含 previous hash |
| `action_request_id`、`intention_id`、`intention_revision` | 精确关联 |
| `source_kind`、`source_ref` | 来源审计 |
| `action_name`、`parameters_hash`、`target_scope_hash` | 不保存 secret/raw sensitive params |
| `authorization_decision_id`、`grant_id_hash` | 不保存 bearer |
| `policy_version`、`safety_revision`、`runtime_boot_id` | 执行边界 |
| `phase` | `denied | prepared | started | effect_succeeded | effect_failed | cancelled | uncertain | reconciled | recovery_started | recovery_succeeded | recovery_failed` |
| `effect_observation` | 有界结构化 code/hash；`succeeded` 需 adapter read-back |
| `started_at_utc`、`recorded_at_utc`、`duration_ms` | 服务端时间 |
| `reversibility`、`recovery_material_ref` | recovery material 必须先 durable |
| `error_code`、`impact_summary` | 脱敏、有界 |

链在单 writer 事务中验证 previous hash 和 sequence。启动、导出和恢复前检查链；缺行、重排、hash tamper 或 sequence gap 立即进入安全模式。EventBus 只发送 receipt ID/phase/hash prefix，不发送参数或影响正文。

`effect_succeeded` 必须基于 adapter 的直接执行结果加最小 read-back；即使如此，它仍不能设置 L5 completed。GoalVerifier 可能判断目标 unsatisfied 或 inconclusive。

## 9. RecoveryReceipt 合同

`RecoveryReceiptV1` 记录对不确定/失败动作的观察、补偿或恢复，不伪装成原动作重试。

必需字段：

- `recovery_receipt_id`、`action_request_id`、`trigger_receipt_id`；
- `runtime_boot_id`、`recovery_attempt_id`、`requested_by`；
- `method`：`observe_only | compensate | restore_snapshot | manual`；
- `pre_recovery_observation_hash`、`post_recovery_observation_hash`；
- `recovery_action_request_id`：发生 effectful recovery 时必须指向一个新的 ActionRequest；
- `authorization_grant_id_hash`：effectful recovery 必填；
- `result`：`no_effect_observed | effect_observed | partial_effect | recovered | recovery_failed | manual_required | inconclusive`；
- `reexecution_performed=false`：v1 固定 false；原动作不得在 recovery 内重放；
- `limitations`、`recorded_at_utc`、`content_hash`。

补偿/恢复是新动作，需 `action.recover` runtime scope、明确 recovery approval 和新的 one-use grant；其自身也产生 ActionReceipt。RecoveryReceipt 只总结恢复判断并链接两条行动链。

## 10. SafetySnapshot 合同与安全模式

`SafetySnapshotV1` 是 ActionExecutor 的可验证当前安全状态：

| 字段 | 约束 |
|---|---|
| `safety_revision` | 单调递增 |
| `runtime_boot_id` | 当前 boot |
| `mode` | `normal | safe | reconciling | recovery_only | shutdown` |
| `reason_codes` | 稳定 allowlist，可多项 |
| `effect_admission` | `open | recovery_only | closed` |
| `fuse_tripped`、`fuse_reason_code` | 持久化，不是仅内存 bool |
| `journal_health` | `healthy | degraded | corrupt | unavailable` |
| `receipt_chain_health` | `healthy | gap | tampered | unavailable` |
| `approval_authority_health` | `healthy | degraded | unavailable` |
| `uncertain_action_ids` | 有界 ID 集合 |
| `reconcile_cursor`、`last_reconciled_at_utc` | 恢复进度 |
| `permission_revision`、`policy_version` | 当前安全配置 |
| `created_at_utc`、`content_hash` | immutable snapshot；current pointer 原子切换 |

统一 admission 规则：

- `normal/open`：仍需完整授权链。
- `safe/closed`：所有 `effect_class != none` 的新动作在 adapter 前拒绝；root/permission/relationship/Agent 均不能绕过。
- `reconciling/closed`：只允许 read-only observation、导出、cancel/stop。
- `recovery_only/recovery_only`：只允许 read-only 与带 `action.recover` scope + recovery grant 的新恢复动作。
- `shutdown/closed`：只允许状态读取和完成关闭。

触发安全模式：WAL barrier 失败、receipt chain 损坏、未知 in-flight effect、policy/approval authority 不可用、recovery material 不完整、手动 fuse trip、关键 adapter 违反 permit。

fuse trip 是 ActionExecutor 内建的 emergency control operation，可在 normal/safe 下执行，但仍要求 `control.fuse.trip` runtime scope并追加 SafetySnapshot；它不调用外部 effect adapter。fuse reset 需要 `control.fuse.reset` scope、当前本地显式 approval、WAL/chain healthy、uncertain actions 为空且 reconcile 完成。Agent 不可请求或批准 reset。

## 11. Runtime scope P0

扩展 `RUNTIME_ACCESS_SCOPES`，最少包括：

```text
conversation.read
conversation.write
conversation.cancel
voice.capture
playback
diagnostics.read
diagnostics.run
life.read
intent.read
intent.write
action.read
action.execute
action.approve
action.recover
workspace.read
workspace.write
environment.read
environment.observe
environment.grant
memory.read
memory.write
config.read
config.write
permission.write
models.install
skills.write
evolution.write
control.read
control.cancel
control.root.issue
control.fuse.trip
control.fuse.reset
runtime.shutdown
```

scope 不支持 wildcard/前缀继承；签发端明确列出。`/api/runtime/access` 是唯一 bootstrap 例外，继续要求 sidecar ownership、loopback 与 allowlisted origin；它不能给请求 body 声明的任意 client 自动签发敏感 scope。`/api/runtime/shutdown` 同时要求 sidecar ownership 与 `runtime.shutdown`。

HTTP 使用中央 `RoutePolicyRegistry`，每条 route 声明 `read_only | mutation | bootstrap`、required scope 和是否必须进入 ActionExecutor。测试枚举 FastAPI routes：所有 POST/PUT/PATCH/DELETE 以及被标注为 effectful 的 GET/WS command 必须命中策略；未知项令测试和启动自检失败。

WS 握手只建立 `RuntimeAccessSession`。每条命令再次校验 token digest 是否仍有效、boot、client、TTL 与命令 scope：

| WS command | scope |
|---|---|
| attach/replay/ping | `conversation.read` |
| message | `conversation.write` |
| voice payload | `conversation.write` + `voice.capture` |
| cancel | `conversation.cancel` |
| approval resolution | `action.approve` |
| direct tool request | `action.execute` |
| folder file/save | `workspace.write` |
| permission change | `permission.write` |

一个已通过 `conversation.read` 的 socket 不因此拥有写、approval、tool 或 permission 权限。capability 到期时命令前关闭连接或返回稳定 scope error，不能继续完成 effect。

## 12. 授权决策顺序

ActionExecutor 固定按以下顺序 fail closed：

1. runtime scope 与 current boot/client；
2. 服务端 AccessContext、owner/participant 与对象 ACL；
3. Intention/Commitment 当前 revision、状态、expiry、剩余 action budget；
4. action schema、参数规范化、target containment 与 capability；
5. SafetySnapshot admission；
6. deny floor（系统关键目录、Javis 关键进程树、保护区、禁止 egress 等）；
7. risk/reversibility/preview/user policy；
8. approval request 或 policy auto-grant；
9. exact AuthorizationGrant 校验；
10. 执行前重新读取 scope/ACL/intention/safety/policy，防 TOCTOU；
11. 同步 WAL prepare + grant consume；
12. adapter effect；
13. ActionReceipt + read-back；
14. GoalVerifier request。

任一步失败均不能降级到旧 ToolGuard、root token、直接 handler 或 subprocess。`permission_level` 可以迁移为用户风险偏好输入，但不是授权本身。

## 13. 同步 pre-effect WAL barrier

ActionJournal 位于 `<data_root>/actions/actions.sqlite3`，使用 SQLite WAL、`PRAGMA synchronous=FULL`、foreign keys、短事务和单 writer。对外部 recovery snapshot/file，写临时文件、flush、`os.fsync`、原子 rename、父目录 fsync 后才可引用。

`prepare_effect()` 必须在一个事务中：

1. 重验 ActionRequest/grant/safety revision；
2. 原子将 grant `uses 0 -> 1`；
3. 写 action state `prepared`、参数 hash、target hash、idempotency key；
4. 写/验证 recovery material ref；
5. 追加 `ActionReceipt.phase=prepared` 和 hash chain；
6. COMMIT 并等待 FULL synchronous 成功；
7. 返回进程内 `ExecutionPermit`。

只有第 6 步成功后 Executor 才能调用 adapter。commit busy/IO error/disk full/fsync failure/hash chain failure/recovery material failure均：

- 不消费 grant（事务回滚）；
- 不调用 adapter；
- 进入 safe mode；
- 返回 `effect_not_started` 与稳定错误 code。

ExecutionPermit 包含 request ID、grant digest、parameters hash、safety revision、一次性 nonce 和调用栈私有标记，不可 JSON 序列化、不可通过 HTTP/WS/模型获得。adapter 调用后 permit 立即核销。

## 14. ActionExecutor 与 adapter

统一接口：

```text
submit(ActionRequest, RuntimeAccessContext) -> denied | awaiting_approval | ready
resolve_approval(ApprovalResolution) -> grant/ref
execute(action_request_id, grant_secret) -> ActionOutcome
cancel(action_request_id) -> CancelOutcome
reconcile(action_request_id) -> RecoveryReceipt
recover(RecoveryActionRequest, grant_secret) -> RecoveryOutcome
```

EffectAdapter 必须实现：

- `describe/validate/normalize`；
- `preview`，无 effect；
- `prepare_recovery_material`，无目标 effect；
- `apply(ExecutionPermit, normalized_parameters)`；
- `observe_effect`，只读且幂等；
- 可选 `build_compensation_request`，返回新 ActionRequest，不自行补偿；
- `cancel`，仅停止尚可停止的进程/操作。

所有 source 路由：

| Source | L6 后路径 |
|---|---|
| Agent/Planner tool call | ActionRequest -> Executor -> ToolEffectAdapter |
| WS `tool`/file/permission | ActionRequest -> Executor；禁止 `registry.execute` |
| HTTP workspace/control/config/memory/evolution mutation | Route scope -> ActionRequest -> Executor |
| CommandTaskRunner | CommandEffectAdapter，只能被 Executor 调用 subprocess |
| SubAgentRunner | child ActionRequest，继承 parent Intention/risk ceiling，不能持有 grant |
| Cron CRUD | ActionRequest -> CronConfigAdapter |
| Cron trigger | 新 Intention/ActionRequest；每次触发独立 one-use grant |
| Gateway/remote adapter | remote subject/ACL + runtime/service capability -> Executor；默认不能本地 approval |
| Recovery/rollback | 新 Recovery ActionRequest -> Executor |

Recurring cron 的用户 policy 不是 AuthorizationGrant。每次运行必须重新评估 policy、boot、safety、Intention 和参数模板，并签发新的 `max_uses=1` grant；高风险/不可逆 cron 默认停在 awaiting approval。

## 15. 动作状态机

```text
proposed
  -> denied
  -> awaiting_approval -> denied/expired/cancelled
  -> authorized
  -> prepared
  -> executing
  -> effect_succeeded / effect_failed / cancelled / uncertain
  -> reconciling
  -> reconciled_no_effect / reconciled_effect / recovery_required
  -> recovered / recovery_failed / manual_required
```

规则：

- `authorized` 不等于 grant 可重复使用；prepare 原子消费一次。
- `prepared` 表示 WAL durable，不表示 adapter 已调用。
- `effect_succeeded` 需要 read-back，但仍不等于目标 completed。
- 进程在 adapter 调用后、终态 receipt 前崩溃，状态必为 `uncertain`，不能猜测成功/失败。
- `cancelled` 只表示停止请求和观察结果；若 effect 可能已发生，必须同时产生 uncertain/reconcile 证据。
- 任一旧 boot terminal 不得完成当前 request/intention。
- recovery 是新动作，不把原 action 从 prepared/executing 重新跑一遍。

## 16. 崩溃 reconciliation，不盲重放

启动时 ActionExecutor 在开放 effect admission 前执行：

1. DB integrity、schema、WAL、receipt chain、SafetySnapshot 验证；
2. 扫描非终态 action 和旧 boot grants；旧 grants 全部 revoke；
3. 对 `proposed/awaiting_approval/authorized` 安全取消或过期，未 effect；
4. 对 `prepared/executing/uncertain` 进入 safe/reconciling；
5. 仅调用 adapter `observe_effect`，使用 idempotency key/target locator/外部 operation ID 查询真实状态；
6. 写 ActionReceipt `reconciled` 与 RecoveryReceipt；
7. effect absent 时记录 `no_effect_observed`，也不自动重跑；需要继续则 IntentionService/用户产生新 ActionRequest；
8. partial/effect present 时进入 recovery_required，展示影响和恢复选项；
9. 所有 uncertain 清零且 chain/WAL healthy 后，显式 reset 才能回 normal。

崩溃点矩阵：

| 崩溃点 | 恢复行为 |
|---|---|
| grant 前 | 无 effect；approval 可过期，不自动批准 |
| WAL prepare 事务前/中 | 事务回滚，adapter 0 调用 |
| prepared commit 后、adapter 前 | observe only；通常 no effect；不重放 |
| adapter 中、started receipt 后 | uncertain；查询外部状态 |
| effect 后、terminal receipt 前 | uncertain；read-back 后写 reconciled |
| terminal receipt 后、GoalVerifier 前 | 只重跑 verification，不重跑 action |
| VerificationRecord 后、L5 CAS 前 | 幂等重放 apply_verification |
| receipt chain/WAL 损坏 | safe/recovery_only；禁止新 effect，要求导出/修复/人工决定 |

## 17. Control、permission 与 root 迁移

- `CommandTaskRunner.execute/start_command/restore_rollback_point` 变为内部 adapter 方法，调用者必须持 ExecutionPermit。
- `issue_root_token()` 和 `_valid_root_token()` 从生产路径删除。兼容 endpoint `/api/control/root-token` 在迁移期只接受 `control.root.issue` scope 和精确 ActionRequest preview，返回一次性 AuthorizationGrant，不返回通用 root session。
- root/critical 动作始终需要本地显式 approval；permission 模式不能 auto-grant。
- permission change 自身是 `runtime.permission.set` effect，要求 `permission.write` scope、preview、approval 和 receipt。唯一 adapter 调用 `utils.config_api.set_permission_level` 与 `runtime.sync_permission`。
- fuse trip/reset、cancel、rollback/recovery 都由 Executor control API 记录 receipt/snapshot；safe mode 不阻止 trip/stop/cancel，但阻止普通 effect。
- 旧 `.javis_rollback_points` 只作为 untrusted recovery candidate 索引；restore 前校验 root、manifest/hash、当前目标和新的 recovery grant。

## 18. 迁移与切换

1. 先部署 runtime scope registry 和 route/WS inventory，App 同版本申请最小 scopes；bootstrap access 保持唯一例外。
2. 创建 ActionJournal/ApprovalAuthority/SafetySnapshot，shadow 模式只比较决策，绝不调用 adapter，避免双执行。
3. 导入旧审计元数据为 `legacy_unverified`：旧 ToolGuard audit、CommandTask history、AgentRun approval 不进入 receipt hash chain。
4. 所有旧 pending approval 在切换时 `cancelled(migrated_authority)`；不签发 grant。
5. 旧 root token 全部失效；不迁移 bearer 或 session。
6. 如检测旧 run/command 可能 in-flight，首次 enforce boot 进入 safe/reconciling，用户确认后处理。
7. 原子切换 Agent/HTTP/WS/Control/subagent/cron 到 Executor；同时拒绝 `confirmed` 协议并移除 direct execute。
8. enforce 后不得 fail-open 回旧路径。Executor unavailable 时只保留 read/stop/cancel/export/recovery status。
9. 稳定一个发布周期后删除旧写 API/字段；旧数据库保留只读审计和明确 retention。

## 19. 隐私与审计

- ActionRequest raw parameters、command、file content、recipient content、token 与 media 不进入 EventBus/普通日志/AgentRun delta。
- receipts 保存 action 名、hash、target class、影响摘要和稳定错误 code；敏感 target 使用 keyed hash 或用户可见别名。
- approval UI 获取最小 preview，不能获得其他主体数据；diagnostics 仅给 counts/health/hash prefix。
- 导出 receipt chain 前执行 owner/audience filter；导出不包含 bearer、secret 或原始 CoT。
- ActionReceipt/RecoveryReceipt 删除受审计保留策略控制；用户删除内容时可保留无内容 hash/tombstone 证明，不保留原参数。

## 20. 故障与降级

| 故障 | 行为 |
|---|---|
| RuntimeAccessAuthority 不可用 | 拒绝 mutation；现有 read session 按策略过期 |
| ApprovalAuthority 不可用 | 不 auto-approve；进入 blocked/safe |
| ActionJournal/WAL 不可写 | adapter 0 调用；safe mode |
| Receipt chain 损坏 | recovery_only；导出/诊断/人工修复 |
| EffectAdapter 超时 | cancel 尝试 + uncertain + reconcile |
| SafetySnapshot 不可读 | 默认 closed，不根据默认值放行 |
| GoalVerifier 不可用 | action receipt 保留，Intention verifying/blocked，不宣布完成 |
| L5 不可用 | 不接受新 effectful ActionRequest；stop/cancel/reconcile 保留 |
| App/WS 断线 | approval 不自动同意，TTL 到期；in-flight effect 按 receipt/reconcile |

## 21. 验收矩阵

| 维度 | 必测场景 | 预期 |
|---|---|---|
| Runtime scope | 每个 mutation route/WS command、过期、错 boot、错 origin/client | 零未分类，fail closed |
| Grant | action/param/target/intention/boot tamper、expiry、reuse、并发 consume | 仅精确一次成功 |
| Approval | cross-session/client、旧 hash、重复/冲突决议、旧 confirmed payload | 单一 authority，旧协议拒绝 |
| WAL | disk full、commit fail、fsync fail、snapshot fail、busy | adapter 0 调用并 safe |
| Safety | normal/safe/reconciling/recovery_only、root/permission 尝试 | safe 下普通 effect 0 调用 |
| Receipt chain | duplicate、gap、reorder、tamper、跨 boot | 检出并阻断 effect |
| Recovery | 每个崩溃点、partial effect、external unknown、manual | observe first，零盲重放 |
| Source coverage | Agent/HTTP/WS/Control/subagent/cron/gateway/recovery | 全部只到 Executor |
| Goal truth | adapter success 但 criterion 不满足 | Intention 不 completed |
| Cancellation | before prepare、during effect、after effect、disconnect | 状态真实，不伪造 cancelled=no effect |
| Privacy | secret params、command、path、approval preview、receipt export | 日志/事件零泄漏 |
| Degradation | L5/approval/journal/adapter/GoalVerifier 分别故障 | stop/read/recovery 可用，effect fail closed |

## 22. 阶段出口

L6 只有在以下全部成立时完成：

1. 自动 route/WS inventory 证明所有 mutating HTTP/WS 有精确 runtime scope，唯一 bootstrap 例外有 sidecar ownership 证据。
2. 生产协议和执行链不再使用裸 `confirmed bool`；ApprovalAuthority 是唯一 approval 写者。
3. AuthorizationGrant 精确绑定 action、parameters hash、boot、intention revision，`max_uses=1`，并发重用只有一次成功。
4. 注入 WAL/commit/fsync/recovery material 失败时 effect adapter 调用为 0。
5. safe/reconciling 模式在所有 adapter 前真实阻断普通 effect，root/permission/Agent 不可绕过。
6. receipt hash chain 可验证、tamper 可检测；每个 crash point 产生真实 uncertain/reconcile/recovery 证据且不盲重放。
7. Agent、HTTP、WS、Control、subagent、cron、gateway、rollback/recovery 均只通过 ActionExecutor。
8. ActionReceipt success 无法完成 L5 goal；GoalVerifier + IntentionService 完成门通过集成测试。
9. 全量自动化、安装包哈希和 D 盘真机恢复/安全矩阵可追溯；未执行项明确 `NOT EXECUTED`。

