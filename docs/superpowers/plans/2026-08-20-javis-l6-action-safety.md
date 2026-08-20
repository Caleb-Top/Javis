# Javis L6 行动授权、安全、收据与恢复实施计划

**日期：** 2026-08-20  
**状态：** 待实施  
**源码基线：** `G:\Javis` at `cc840778972f`  
**对应规格：** `docs/superpowers/specs/2026-08-20-javis-l6-action-safety-design.md`  
**目标：** 以 `ActionExecutor` 收口所有动作来源，完成精确一次授权、同步 pre-effect WAL、哈希收据链、真实安全模式和无盲重放恢复。

## 1. 全局约束

1. 冻结架构：`Conversation/event -> IntentionService -> Agent/Planner -> ActionExecutor -> GoalVerifier`。
2. `ActionExecutor` 是唯一动作执行权威；`ToolRegistry`、Control、HTTP/WS、subagent、cron、gateway 均不得直接 effect。
3. runtime scope 是客户端入口门，AuthorizationGrant 是具体动作门；两者缺一不可。
4. `AuthorizationGrant.max_uses` 固定为 1，并绑定 action、parameters hash、boot、intention revision、target。
5. 生产代码删除 `confirmed bool` 授权语义；统一使用 ApprovalAuthority + `ApprovalResolutionV1.decision`。
6. WAL barrier 未同步成功时 adapter 必须 0 调用；安全状态不可读时默认 closed。
7. 崩溃恢复只观察/reconcile；原动作需要继续时创建新 ActionRequest 和新 grant。
8. ActionReceipt success 不能完成目标；L5 GoalVerifier/IntentionService 是唯一完成门。
9. P0 scope、approval、safe mode、WAL、reconcile 和 source coverage 未全部通过前，不允许 `enforce` 或发布安全声明。
10. 每个任务先加失败测试；共享文件按第 4 节单写，不覆盖并发工作。

## 2. 任务依赖图

```text
L6-T0 P0 inventory/failing gates
  -> L6-T1 Contracts
       -> L6-T3 ApprovalAuthority
       -> L6-T4 SafetyController
       -> L6-T5 ActionJournal / receipts
       -> L6-T6 AuthorizationPolicy
            -> L6-T7 ActionExecutor core
                 -> L6-T8 Tool adapter cutover
                 -> L6-T9 Control/permission/root cutover
                 -> L6-T10 HTTP/WS scope + executor cutover

L5-T3/T5/T6/T8 + L6-T7/T8/T10
  -> L6-T11 shared Conversation/Agent/Runtime integration
       -> L6-T12 subagent/cron/gateway
       -> L6-T13 crash reconcile/recovery
       -> L6-T14 App approval/safety/receipt UI
       -> L6-T15 migration and enforce cutover
       -> L6-T16 release evidence

L6-T2 Runtime scopes
  -> L6-T3, T9, T10, T14
```

可并行：T2/T3/T4/T5 在 T1 后可并行；T8/T9/T10 在 T7 后按不重叠文件并行。T11 必须等待 L5 domain 模块稳定；T15 之前不得删除旧接口。

## 3. 外部依赖

| 依赖 ID | 必需交付 | 使用点 |
|---|---|---|
| `L0A` | identity/instance/runtime boot、privacy、canonical hash | 全部 grant/receipt/safety |
| `L1` | canonical events、restart/turn receipt | source/recovery evidence |
| `L2-A` | 服务端 AccessContext、owner filter、deletion hook | authorization/API/audit |
| `L3-A` | object ACL、participant/guest isolation | 多人动作与 approval |
| `L5-T1/T3/T6/T8` | Intention contracts/service、GoalVerifier、Agent ports | ActionRequest admission/目标完成 |
| `L4` | EnvironmentGrant/Snapshot/observe port | capture/environment action；不阻塞纯 workspace MVP |

缺 `L2-A/L3-A` 时不得从 request body 读取 owner；缺 L5 Intention 时不得执行新的 effectful action，只保留 emergency stop、cancel、read-only diagnostics 和 reconciliation。

## 4. 文件所有权

### 4.1 L6 专属新模块

| 路径 | 所有任务 |
|---|---|
| `core/action/__init__.py`、`core/action/contracts.py` | T1 |
| `core/action/route_policy.py` | T2 |
| `core/action/approval.py` | T3 |
| `core/action/safety.py` | T4 |
| `core/action/journal.py`、`core/action/receipts.py` | T5 |
| `core/action/authorization.py` | T6 |
| `core/action/executor.py`、`core/action/adapters.py` | T7 |
| `core/action/tool_adapter.py` | T8 |
| `core/action/control_adapter.py`、`core/action/permission_adapter.py` | T9 |
| `core/action/recovery.py` | T13 |
| `core/action/migration.py` | T15 |
| `tests/test_action_*.py` | 对应任务 |
| `docs/verification/JAVIS_L6_D_DRIVE_ACCEPTANCE.md` | T16 |

### 4.2 现有文件单写任务

| 文件 | 唯一修改任务 |
|---|---|
| `core/runtime_access.py`、`tests/test_runtime_access.py` | T2 |
| `core/tool_registry.py`、`core/tool_guardrails.py`、`core/action_policy.py`、`tools/manifest.py` | T8 |
| `control/command_tasks.py`、`utils/config_api.py` | T9 |
| `main.py`、`gateway/conversation_ws.py`、`core/conversation_protocol.py` | T10 |
| `core/agent.py`、`core/planner.py`、`core/agent_run_recorder.py`、`core/agent_runs.py`、`core/conversation_hub.py`、`core/prompt_builder.py`、`core/runtime.py`、`core/events.py` | T11 |
| `core/subagent.py`、`tools/cron_scheduler.py`、`gateway/gateway_manager.py`、其他真实 remote gateway adapter | T12 |
| `app/src/main.ts`、`app/src/bridge/*`、`app/src/conversation/*`、现有 Control/Settings/Task panels | T14 |

T11 是 L5/L6 共享后端文件的唯一集成任务；L5 任务只提供 `core/intention/*` 和 tests。任何并行工作若已修改上述文件，执行者必须基于最新内容适配，不能 reset/revert。

## 5. P0 route/scope 分类

T0/T2/T10 使用中央清单，至少覆盖当前 `main.py`：

| 路由/命令族 | 最小 scope | Executor |
|---|---|---|
| `/api/runtime/access` | bootstrap ownership exception | 否；只能签发 runtime capability |
| `/api/runtime/shutdown` | `runtime.shutdown` + sidecar ownership | Executor control stop |
| voice playback/capture/test POST | `playback` / `voice.capture` / `diagnostics.run` | 有 effect 的走 Executor |
| agent-run create/resume/cancel | `intent.write` / `action.execute` / `conversation.cancel` | 迁移后投影到 L5/L6 |
| approval resolve | `action.approve` | ApprovalAuthority |
| diagnostics self-test | `diagnostics.run` | 无外部 effect 子项可直跑；effect 子项走 Executor |
| perception ingest/analyze/capture | `environment.observe` + capture scope | Executor + L4 grant |
| memory writes/delete/rebuild | `memory.write` | Executor |
| evolution candidate/review mutation | `evolution.write` | Executor |
| skill activate/evaluate status mutation | `skills.write` | Executor |
| provider/key/model/path/effort config | `config.write` | Executor |
| permission change | `permission.write` | Executor + approval |
| model install/pause/resume/cancel | `models.install` | Executor |
| workspace terminal/save/project | `action.execute` / `workspace.write` | Executor |
| control start/cancel/rollback | `action.execute` / `control.cancel` / `action.recover` | Executor |
| root grant compatibility | `control.root.issue` | ApprovalAuthority exact grant |
| fuse trip/reset | `control.fuse.trip` / `control.fuse.reset` | Executor safety control |
| WS message/cancel/approval/tool/file/permission | 第 11 节命令级 scope | Executor/Authority |

`RoutePolicyRegistry` 必须枚举实际 FastAPI route，不以文档表人工保证。任何新增 POST/PUT/PATCH/DELETE 或 mutating WS command 未登记时，测试失败且生产启动自检报告 P0 unhealthy。

## 6. 任务清单

### L6-T0：冻结 P0 inventory 与失败门

**依赖：** 无  
**创建：** `tests/test_action_p0_inventory.py`、`tests/test_action_no_bypass.py`

先写当前源码必然失败的审计测试：

- 枚举 FastAPI route/WS command，列出无 scope mutation；
- 查找 `confirmed` 授权签名/字段；
- 查找 `main.py`/ConversationHub/SubAgent/Cron/Control 的直接 registry/handler/subprocess/file mutation；
- 验证 root token 可重复使用、fuse reset 无 scope、safe mode 不覆盖所有 adapter；
- 验证 Agent done/tool success 可形成 completed run/文本完成，但不能存在 L5 completed 证据。

测试输出只打印 route/function/code，不打印参数、token 或用户数据。将基线结果保存为 test assertions，不创建独立审计报告文件。

### L6-T1：冻结 L6 严格合同

**依赖：** `L0A`、`L5-T1`  
**创建：** `core/action/contracts.py`、`core/action/__init__.py`、`tests/test_action_contracts.py`

实现 `ActionRequestV1`、`AuthorizationGrantV1`、`ApprovalRequestV1`、`ApprovalResolutionV1`、`ActionReceiptV1`、`RecoveryReceiptV1`、`SafetySnapshotV1`、effect/risk/phase/state 枚举、`JAVIS_ACTION_PARAMETERS_V1` hash。

强制：unknown field rejection、`max_uses=1` literal、owner 不在 client wire、无 `confirmed/approved bool/root override`、canonical normalized params、bounded target/preview/receipt、secret 不参与 repr/to_dict/event。

**测试命令：**

```powershell
$env:TEMP='G:\Javis\tmp'; $env:TMP='G:\Javis\tmp'
python -m pytest tests/test_action_contracts.py -q
```

### L6-T2：扩展 runtime capability 与中央 route policy

**依赖：** `L6-T0`、`L6-T1`  
**创建：** `core/action/route_policy.py`、`tests/test_runtime_mutation_scopes.py`、`tests/test_websocket_command_scopes.py`  
**修改：** `core/runtime_access.py`、`tests/test_runtime_access.py`

增加规格 scope，不支持 wildcard。将 WebSocket context 从“已通过一个 scope”改为 process-local `RuntimeAccessSession`：持有 token digest/ref、client hash、boot、deadline；每条命令由 Authority 重新校验 required scope 和 revoked/expired 状态。

实现 `RoutePolicyRegistry` 与启动/测试 inventory。`/api/runtime/access` 是唯一 bootstrap mutation exception，要求 sidecar ownership/loopback/origin；shutdown 需要 scope + ownership。

测试错 origin、非 loopback、错 boot、过期、撤销、scope confusion、一个 socket 读权限尝试 approval/tool/permission、token 泄漏扫描和 inventory zero-gap。

### L6-T3：实现唯一 ApprovalAuthority

**依赖：** `L6-T1`、`L6-T2`、`L2-A/L3-A` contracts  
**创建：** `core/action/approval.py`、`tests/test_approval_authority.py`

在 ActionJournal 同库或同一事务域建立 approvals/grants 表。实现 pending CAS、preview hash、owner/client/boot/action/params binding、approve/deny enum、expiry/cancel、exact one-use grant issuance、digest-only storage、revoke boot/client。

禁止公开 `resolve(approved: bool)`。测试 duplicate same decision 幂等、conflict decision 拒绝、cross-session/client/owner/boot/hash、expired、ID guess、grant secret diagnostics/repr/DB/event 零泄漏。

AgentRunStore approval 暂不在本任务修改；T11 将其降级为 Authority event projection。

### L6-T4：实现 SafetyController 与持久 SafetySnapshot

**依赖：** `L6-T1`  
**创建：** `core/action/safety.py`、`tests/test_action_safety.py`

实现 normal/safe/reconciling/recovery_only/shutdown 状态机、effect admission、persistent fuse、reason set、health inputs、uncertain action IDs、revision CAS 和 current pointer。安全状态缺失/损坏默认 closed。

提供 Executor 内建 `trip/cancel/stop/reset_request` 控制接口。reset 前置：scope、local approval、journal/chain healthy、reconcile complete、uncertain empty。测试 root/permission/Agent/旧 safety revision 无法绕过；safe 下每类普通 adapter 调用为 0，recovery-only 只放行 exact recovery permit。

### L6-T5：实现 ActionJournal、同步 WAL 与收据链

**依赖：** `L6-T1`、`L6-T3` contract、`L6-T4`  
**创建：** `core/action/journal.py`、`core/action/receipts.py`、`tests/test_action_journal.py`、`tests/test_action_receipt_chain.py`、`tests/test_action_wal_barrier.py`

建立 action requests、states、approvals/grants、WAL records、ActionReceipt、RecoveryReceipt、SafetySnapshot、idempotency 表。SQLite 使用 WAL + synchronous FULL + foreign keys + single writer。

实现 `prepare_effect` 单事务：重验 -> grant uses CAS -> recovery ref -> prepared state -> receipt chain append -> commit。注入 connection commit、disk full、busy、fsync、snapshot write/rename/parent fsync、chain append 失败，断言 grant 未消费、adapter spy 0 调用、SafetyController 进入 safe。

收据链测试 genesis、sequence、concurrent append、duplicate idempotency、gap/reorder/delete/tamper、跨 boot 验证与脱敏导出。

### L6-T6：统一 AuthorizationPolicy 与 deny floor

**依赖：** `L6-T1`、`L6-T2`、`L6-T3`、`L6-T4`、`L5-T3` contract  
**创建：** `core/action/authorization.py`、`tests/test_action_authorization.py`

按固定顺序校验 runtime scope、AccessContext/ACL、Intention revision/state/budget/risk ceiling、action schema/target、SafetySnapshot、deny floor、risk/reversibility/preview 和 approval policy。输出结构化 `deny | request_approval | issue_auto_grant`，不执行 effect。

将 `core/action_policy.py` 的 Windows/Javis 保护规则作为 deny floor 输入；不得复制成平行规则。关系、熟悉度、情绪、模型、legacy permission/root 均不能提高结果。测试完整矩阵与 TOCTOU recheck。

### L6-T7：实现 ActionExecutor core 与 ExecutionPermit

**依赖：** `L6-T3..T6`、`L5-T3/T6` ports  
**创建：** `core/action/executor.py`、`core/action/adapters.py`、`tests/test_action_executor.py`

实现 submit/resolve approval/execute/cancel/reconcile/recover 接口与 adapter registry。ExecutionPermit 为进程内不可序列化对象，绑定 request/grant/params/safety revision/nonce，单次核销。

执行顺序严格为：admission -> policy -> grant -> pre-effect recheck -> WAL barrier -> adapter apply -> observe -> receipt -> GoalVerifier request。post-effect receipt 写失败时标记 uncertain、trip safety，不向 L5 报 success。

测试 adapter 抛错/超时/取消/返回假 success/read-back 不符、permit reuse/forge/serialize、late safety trip、L5 budget race、GoalVerifier unavailable。

### L6-T8：将 ToolRegistry 降级为 Executor adapter registry

**依赖：** `L6-T7`  
**创建：** `core/action/tool_adapter.py`、`tests/test_tool_executor_integration.py`  
**修改：** `core/tool_registry.py`、`core/tool_guardrails.py`、`core/action_policy.py`、`tools/manifest.py`

移除 `ToolRegistry.execute(... confirmed=False)` 与 ToolGuard `confirmed` 分支。公共调用变为 ActionExecutor；内部 ToolEffectAdapter 持 ExecutionPermit 调用 handler。ToolGuard 保留 schema/rate/content/telemetry，但不再是 approval authority，也不能在 receipt 后伪造成功。

调整 TOOL_STARTED：只有 WAL prepared 且 adapter 即将调用时才发薄 started 事件；参数不进入 EventBus。直接 registry invoke 无 permit 必须拒绝。

AST 测试限定 production `.execute`/handler 调用只存在于 `core/action/*adapter.py`；测试模块可使用 injected fake。

### L6-T9：迁移 Control、permission、root 与 fuse

**依赖：** `L6-T2`、`L6-T7`、`L6-T8`  
**创建：** `core/action/control_adapter.py`、`core/action/permission_adapter.py`、`tests/test_control_action_executor.py`、`tests/test_permission_action_executor.py`  
**修改：** `control/command_tasks.py`、`utils/config_api.py`

将 subprocess/Popen/rollback restore 置于 CommandEffectAdapter，要求 ExecutionPermit。CommandTaskRunner 只管理 adapter-local process/observation，不持有 approval/root authority。

删除通用 root token 生产逻辑；迁移兼容调用只可请求绑定 command hash/target/boot 的 one-use grant。permission set 由唯一 PermissionEffectAdapter 同步 config + runtime projection，并产生 receipt。fuse 持久状态迁入 SafetyController。

rollback snapshot 为空、超上限、hash/manifest/root 不完整时 prepare 失败且不执行命令。测试 command/path escape、root reuse、permission race、fuse trip/reset、cancel before/during effect、rollback 作为新 recovery action。

### L6-T10：收口所有 HTTP/WS mutation

**依赖：** `L6-T2`、`L6-T7..T9`  
**修改：** `main.py`、`gateway/conversation_ws.py`、`core/conversation_protocol.py`  
**创建：** `tests/test_main_action_scopes.py`、`tests/test_websocket_action_executor.py`、`tests/test_action_route_inventory.py`

为第 5 节全部 route 注册 policy/dependency。mutation handler 只做 strict parse -> runtime scope -> AccessContext -> ActionRequest/ApprovalResolution -> Executor/Authority；禁止直接 set/write/delete/install/execute。

WS 每 command 校验 scope：read/message/voice/cancel/approval/tool/file/permission 分离。将 `conversation.confirm {confirmed}` 替换为 `conversation.approval.resolve {decision,...binding}`；旧 payload 返回 `approval_contract_required`。`_handle_ws_tool`、folder save、permission change 改为 ActionRequest。

root token、fuse reset、permission、approval endpoint 使用专用 scope。`/api/runtime/access` 为唯一 bootstrap exception；`/api/runtime/shutdown` 增加 runtime scope。

验收：FastAPI/WS inventory 零未分类；scope 验证发生在 body 引发任何 I/O 之前；跨 session/client/boot 全拒绝。

### L6-T11：共享 Conversation/Agent/Planner/Runtime 集成

**依赖：** `L5-T3/T5/T6/T8`、`L6-T7/T8/T10`  
**修改：** `core/agent.py`、`core/planner.py`、`core/agent_run_recorder.py`、`core/agent_runs.py`、`core/conversation_hub.py`、`core/prompt_builder.py`、`core/runtime.py`、`core/events.py`  
**测试：** `tests/test_intention_action_goal_pipeline.py`、`tests/test_agent_cannot_authorize_or_complete.py`、`tests/test_unified_approval_projection.py`

这是共享文件唯一接线任务，按以下顺序一次完成：

1. Runtime 构造 IntentionService -> ApprovalAuthority/Safety/Journal -> ActionExecutor -> GoalVerifier，注入同一 boot/identity/access resolver。
2. ConversationHub 在 canonical request.accepted 后等待 durable Intention ack，再调用 Agent；失败不回退旧 Agent。
3. Agent/Planner 只读 Intention context，只输出 plan/action/checkpoint/verification candidates；删除 `_confirm_result`、`resolve_confirm`、直接 tool execute、completion authority 和“任务完成”硬编码。
4. AgentRunStore/Recorder 变为投影：run completed=stream ended；approval 只镜像 Authority ID/state；不保存 raw tool params/result/reasoning。
5. ActionOutcome 收据触发 Intention verifying/blocked/waiting；GoalVerifier record 通过后 IntentionService 才 completed。
6. PromptBuilder 只加入预算化 L5 context，不加入 grant、approval secret、raw receipt/CoT。

集成测试明确断言：Agent done、end_turn、Planner complete、tool success、ActionReceipt effect_succeeded、AgentRun completed 单独或组合都不能完成 Intention。

### L6-T12：收口 subagent、cron 与 gateway

**依赖：** `L6-T11`  
**修改：** `core/subagent.py`、`tools/cron_scheduler.py`、`gateway/gateway_manager.py` 及已启用 remote adapters  
**创建：** `tests/test_subagent_action_executor.py`、`tests/test_cron_action_executor.py`、`tests/test_gateway_action_executor.py`

SubAgent 继承 parent Intention/owner/risk ceiling/tool allowlist，只能提交 child ActionRequest；不能接收 grant secret、resolve approval 或调用 registry。需 approval 时返回 parent blocked signal。

cron add/update/remove/toggle 走 CronConfigAdapter；trigger 创建新 event/intention/action。每次 trigger 重新评估，签发新的 one-use grant；高风险/不可逆默认 awaiting local approval。job prompt 不是授权。

remote gateway 使用 server-resolved subject/ACL 和 service/runtime capability；默认不能代表本地用户 approve 高风险动作。测试 source spoof、parent escape、parallel child grant reuse、cron 重启重复、remote approval denial。

### L6-T13：实现 crash reconciliation 与 recovery

**依赖：** `L6-T5/T7/T9/T11`  
**创建：** `core/action/recovery.py`、`tests/test_action_crash_reconciliation.py`、`tests/test_action_recovery.py`

实现 startup gate：integrity/chain/safety -> revoke old grants -> classify nonterminal -> safe/reconciling -> adapter observe -> ActionReceipt/RecoveryReceipt -> recovery options。任何 prepared/executing/uncertain 都不调用原 adapter apply。

为每个崩溃点建立 subprocess/fault fixture：grant 前、WAL transaction、prepared 后、adapter 中、effect 后 terminal receipt 前、GoalVerifier 前、L5 CAS 前。使用外部状态 probe 证明动作调用次数与真实 effect；原动作自动 replay 次数恒为 0。

effectful compensation/restore 生成新 ActionRequest，需要 `action.recover` scope、approval、one-use grant，并产生自己的 ActionReceipt。链/WAL 损坏进入 recovery_only。

### L6-T14：App approval、安全模式、收据与恢复 UI

**依赖：** `L6-T2/T10/T11/T13`、L5 app 新模块  
**修改：** `app/src/main.ts`、`app/src/bridge/backendClient.ts`、`app/src/bridge/runtimeAccess.ts`、conversation/control/settings/task panels  
**创建：** `app/src/action/actionTypes.ts`、`app/src/action/ActionBridge.ts`、`app/src/action/ApprovalPanel.ts`、`app/src/action/RecoveryPanel.ts`、`app/tests/action*.test.ts`

App 申请最小 runtime scopes，不默认申请 root/fuse reset/action.recover。approval panel 展示 action、target summary、params hash prefix、risk、reversibility、preview、expiry；提交 decision enum 和完整 binding，不发送 confirmed bool。

安全模式界面显示 mode/reasons/journal/chain/uncertain count，保留 stop/cancel/read/export/recovery；普通 effect controls disabled 且后端仍硬拦。receipt timeline 区分 action success 与 goal verification。恢复 UI 不提供“自动重试原动作”，只提供观察、显式新恢复动作或人工处理。

测试错 boot/reconnect、approval expiry、double click、hash change、safe mode direct request、长文本/窄窗口/200% 字体无重叠。

### L6-T15：迁移、shadow 与 enforce 原子切换

**依赖：** `L6-T0..T14`  
**创建：** `core/action/migration.py`、`tests/test_action_migration.py`、`tests/test_action_enforce_cutover.py`

迁移步骤：

1. 只读 inventory legacy AgentRun approvals、ToolGuard audit、CommandTask/rollback、root token presence；记录 counts/hash，不复制 secret/params/content。
2. 建新 schema 与 genesis SafetySnapshot/receipt chain；若有可能 in-flight 项，初始 mode=safe/reconciling。
3. shadow policy 只比较决策和 scope，不调用 adapter、不写 effect receipt，避免双执行。
4. 切换窗口取消全部 old pending approval，revoke root token，停止 legacy writers。
5. 原子启用 `enforce`：所有 source 只到 Executor；旧 confirmed/direct execute 返回稳定 migration error。
6. restart 后验证 no fallback；rollback 只能回退整个版本/权威指针，不能同时双写。

enforce 启动自检要求 route inventory zero-gap、journal/chain healthy、single approval writer、adapter bypass scan clean。任一失败进入 safe 或拒绝启动 effect subsystem。

### L6-T16：全量回归、发布合同与 D 盘验收

**依赖：** `L6-T15`  
**创建：** `tests/test_action_release_contract.py`、`docs/verification/JAVIS_L6_D_DRIVE_ACCEPTANCE.md`

执行全量 Python/App/TypeScript/Vite 和可用 Rust/Tauri 门。发布合同静态/动态证明：

- route/WS mutation scope 零缺口；
- 生产 `confirmed` 授权语义零命中；
- direct effect bypass 零命中；
- exact grant one-use；
- WAL fail adapter 0；
- safe mode adapter 0；
- crash reconcile no replay；
- receipt chain tamper detection；
- tool success != goal success。

D 盘场景至少覆盖文件写入/回滚、长命令取消、permission change、fuse trip/reset、approval expiry、应用崩溃于 prepared/effect/receipt 三点、磁盘不可写、安全模式、重启 reconcile、receipt 导出。记录精确 commit、安装包 SHA-256、Windows/WebView2、数据根、动作/收据 ID 和结果 code，不记录命令正文、路径 secret、token 或用户内容。

## 7. 测试矩阵

| 风险面 | 单元 | 集成 | 故障注入/恢复 | 生产/D 盘 |
|---|---|---|---|---|
| Runtime scopes | T2 | T10/T14 | expiry/revoke/boot | 所有真实 Surface |
| Approval/grant | T3 | T10/T11 | crash/concurrent consume | 本地 approve/deny/timeout |
| Policy/ACL | T6 | T7/T12 | TOCTOU/revision | guest/remote/cron |
| WAL barrier | T5 | T7/T9 | disk full/commit/fsync/snapshot | 数据盘只读/空间故障 |
| Safety mode | T4 | T7/T10/T14 | corrupt chain/uncertain | fuse/reset/recovery-only |
| Receipt chain | T5 | T11/T13 | gap/reorder/tamper | export/restart |
| Source coverage | T8-T12 | T11 | source crash | Agent/WS/HTTP/Control/subagent/cron |
| Recovery | T13 | T11/T14 | 全 crash-point | 真进程/文件状态 |
| Goal truth | L5-T6 | T11 | verifier/CAS crash | UI 完成状态 |
| Privacy | 各合同 | API/event/log | error path | diagnostics/export |

## 8. 静态旁路守卫

`tests/test_action_no_bypass.py` 对一方生产源码建立 allowlist：

- `ToolRegistry` handler 调用只允许在 `core/action/tool_adapter.py`；
- `CommandTaskRunner` subprocess/Popen 只允许在 `core/action/control_adapter.py` 或被 permit 封装的内部方法；
- `main.py` mutating handlers 不得直接调用 `set_*`、save/delete/install/restore/execute；
- `core/subagent.py`、`tools/cron_scheduler.py`、gateway 不得调用 registry.execute 或 effect handler；
- Agent/ConversationHub/AgentRunStore 不得签发 grant、resolve bool approval、写 Intention completed；
- production approval wire/parser 不得接受 key `confirmed`。

该测试只检查明确调用边界，不做易误报的全仓库字符串禁用；合法的 L2 shared-memory `confirmed` 等非授权语义不受影响。

## 9. 验证命令与纪律

每任务：

```powershell
$env:TEMP='G:\Javis\tmp'
$env:TMP='G:\Javis\tmp'
$env:JAVIS_TEST_MODE='1'
python -m pytest <task tests> -q
git diff --check
```

P0/集成门：

```powershell
python -m pytest tests/test_action_p0_inventory.py tests/test_action_no_bypass.py tests/test_action_route_inventory.py -q
python -m pytest tests/test_intention_action_goal_pipeline.py tests/test_action_crash_reconciliation.py -q
```

阶段出口：

```powershell
python -m pytest tests -q
npm --prefix app test
npm --prefix app run build
```

Rust/Tauri 依赖存在时执行 `cargo test`、`cargo check` 和 App bundle 验证；不得未经批准下载依赖。未执行项在验收文档标记 `NOT EXECUTED`，不得用静态测试冒充真机。

## 10. 切换回滚原则

- schema/feature flag/权威指针变更分开；切换前创建完整备份和 hash。
- shadow 永不调用 effect adapter；不能让 legacy 与新 Executor 同时执行同一请求。
- enforce 后不按单请求 fail-open 回 legacy。需要回滚时停止 effect admission、完成 reconcile、回退整个已签名版本和数据库指针。
- 已消费 grant 不因版本回滚复活；旧 boot grant 一律无效。
- 已发生但未收据的 effect 必须保留 uncertain/recovery 证据，不能靠回滚数据库抹除。

## 11. 阶段出口

- T0 的所有 P0 失败门已转绿，route/WS inventory 零缺口。
- 所有 mutating HTTP/WS、root grant、fuse reset、permission、approval 都有精确 runtime scope。
- ApprovalAuthority 单写，旧 confirmed bool 授权路径已拒绝/删除。
- one-use grant 绑定完整且并发安全；root/cron/subagent 不可复用或扩大。
- WAL/commit/fsync/recovery material 任一失败均为 adapter 0 调用。
- safe/reconciling 对全部 source 真正阻断 effect，恢复动作使用新请求/新 grant。
- 所有 crash point observe-first、零盲重放，ActionReceipt/RecoveryReceipt/hash chain 可验证。
- Agent/HTTP/WS/Control/subagent/cron/gateway 无 direct effect 旁路。
- ActionReceipt success 与 L5 goal completed 在代码、事件、API 和 UI 中完全分离。
- 全量回归、构建、安装包哈希与 D 盘证据齐全；否则状态保持 `NOT COMPLETE`。

