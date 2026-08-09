# Javis 数字生命体 · 核心系统与底层代码设计（细化报告）

> 版本：CORE-REPORT-2026-08-09
> 承接：《一个生命的诞生》V4 —— 把"心/记忆/身体/分寸/成长"细化到可执行粒度。
> 生成方式：5 个深度设计代理（实际读源码核验接口）+ 1 个批判代理逐行核查 + 记忆层补跑。
> 铁律：不重写现有模块，加门面对齐；每个器官可独立验证；不虚构"活着"。

---

## 〇、执行摘要

### 五器官总览
| 器官 | 位置 | 它活到什么程度 | 现有基础 | 新增 |
|---|---|---|---|---|
| 心灵层（心） | core/life/ | 基于真实事件派生认知状态+功能性情绪+人格+循环 | core/events + tool 事件 | 5 新文件，main.py 2 行 |
| 记忆层（海马体） | memory/ | 双记忆+关系+遗忘，统一门面 | controller/brain/session_db | 3 新文件，agent.py 2 行+runtime 1 行 |
| 身体层 | app/src/life/ | 认知驱动 3D/2D 存在，分级降级 | pet/ + RuntimeStateCoordinator | 前端新目录 |
| 分寸层 | core/life/boundary | 动态授权，小事做/风险问/危险绝不 | action_policy/guardrails | 批判后大幅砍 scope |
| 成长层 | core/life/habits | 习惯学习+双记忆成长 | evolution/skill_creator | 批判后改走既有 consolidation |

### 批判核心修正（已纳入）
1. boundary 砍掉 token/认知 step：现有 agent 层已处理 confirm/deny，Boundary 只保留只读 /api/boundary/status 聚合
2. 记忆层 Me Memory 走独立 json：避开 brain.learn_fact 对 prompt 指纹的污染
3. 成长层不复制 HabitLearner：直接用既有 EventMemoryConsolidator + 独立 json
4. tool.completed 无 task 字段：记忆/成长器官不依赖 task，改在 interaction 层面
5. 真实输入只剩 tool.*：心灵层事件源对齐真实，补 user.submitted 发布点

---



## core/life/boundary — 行动边界（动态授权 / 分寸层）

**目标**：让 Javis「活着的一刻」始终有分寸：把分散的三套权限系（utils/config_api 4级、tool_guardrails 5级、control._LEVEL_RANK 3级）统一成一个可裁决的边界层，无论配置怎么改都保证三层语义稳定——小事即做(allow)、风险必问(confirm)、危险绝不(deny)。具体目标：(1) 以一个 Boundary 门面对齐现有 evaluate_action + ToolGuard + 认知状态，不重写任何存量模块；(2) PermissionToken 支持临时分级授权（升级工具免 confirm，5 分钟短 TTL、单用途、可审计、hash 存储）并暴露给 UI；(3) 轻量认知状态（caution 警戒度：检测到循环/失败/危险工具被拦后升级，时间衰减回落）参与动态裁决，但只做 O(1) 纯 CPU 运算，低配置/无脑时降级为静态行为；(4) 修复现存双闸死锁——guard.pre_check 在 confirmed=True 后仍可能因 risk>permission_level 硬拦截；(5) 全链路可审计（AuditEntry），可观测（/api/boundary/status），且对既有行为零破坏。

### 模块结构

**core/boundary.py** — 
- Boundary
- BoundDecision

**core/boundary.py** — 
- BoundaryTokenStore
- PermissionToken

**core/boundary.py** — 
- CognitiveState

**core/boundary.py** — 
- BoundaryMiddleware

**core/boundary.py** — 
- get_boundary 单例函数
- register_in_manifest(reg) 函数

**core/runtime.py / main.py** — 
- 主入口挂载（改 2 行）

### 数据流（活着的一刻如何参与）

「活着的一刻」输入输出链路（从事件到动作）：  入口 A（语音/文字）：/ws（conversation_ws.serve）收到 voice / conversation.message → _submit 构造 ConversationRequest → conversation_hub.submit → agent.chat() 流式事件。 入口 B（直接工具调用）：main.py _handle_ws_tool → registry.execute(tool, params)。  裁决点：所有工具调用最终收敛到 core/tool_registry.py execute() → middleware.execute_async(invoke) → guard.pre_check(tool, safe_params, schema, confirmed)。  Boundary 注入后的链路： 1) user_input / 事件（request.accepted / USER_PROMPT_SUBMIT hook）→ boundary.on_event() 记意图 → CognitiveState 仅在有证据时更新（弱信号不改状态）。 2) 工具意图到达 registry.execute() 前，BoundaryMiddleware.before() 被 MiddlewareP

### 实施步骤

**1 — Boundary 核心裁决器（统一分级 + 底线 deny 恒真）. 核心裁决逻辑：统一风险/权限分级 + 三层语义（allow/confirm/deny）恒真**
- 做法：新建 core/boundary.py，定义 BoundDecision dataclass(action, reason, source, risk_rank, via_token)。内部定义 RISK_RANK={safe:0,low:1,medium:2,dangerous:3,critical:4}（与 TOOL_RISK_LEVELS 同键同值），PERM_RANK 映射表把 config_api 4级(level 1-4)+guard 5级+command_tasks 3级全部归一。decide() 先调 evaluate_action（复用原 deny 语义），再合并 risk 级与权限 rank；绝不把 deny 提升。纯同步、零 IO、无锁；app_root 从 config paths workspace 取，读不到时用 evaluate_action 默认 root。
- 文件：core/boundary.py, tests/test_boundary_risk.py

**2 — 接入 MiddlewarePipeline（现有扩展点）. 无侵入接入存量执行链：middleware 拦截 deny，不改 tool_registry/agent 源码**
- 做法：在 core/boundary.py 定义 BoundaryMiddleware(Middleware)：before() 里调 boundary.decide()，action==deny 时 context.reject(reason)；allow 直接放行；confirm 暂不拦截（把决策附到 context.metadata['boundary_decision']），由 agent 层既有 confirm_required 流程处理。不修改 core/middleware.py（其 fail-closed 语义本就正确）。注入点唯一选择 registry.execute 的 middleware.execute_async：new_files/middleware 追加或独立新 pipeline 两种方式，优先 registry.middleware.add()。
- 文件：core/boundary.py, core/middleware.py(只读不改，追加挂载用其 add), core/runtime.py(可选：create_runtime 内一行 add), tests/test_boundary_risk.py

**3 — PermissionToken + 令牌存储（动态提权）. PermissionToken 临时分级授权：短 TTL、单用途、hash 存储、可审计**
- 做法：在 core/boundary.py 定义 PermissionToken dataclass(token, kind, risk_rank, reason, issued_at, expires_at, consumed) 与 BoundaryTokenStore：issue(kind, risk_rank, ttl_sec=300)→secrets.token_urlsafe(32)；内部存 sha256 hash+expires_at+reason（对齐 control/command_tasks.py 的 token hash 模式，杜绝明文泄露）；consume(token, risk_rank)→bool，单用途即核销，过期自动清理。boundary.decide() 在 evaluate_action 返回 confirm 且无 action_policy deny 时，若存
- 文件：core/boundary.py, tests/test_boundary_token.py

**4 — 轻量认知状态（caution 警戒度）. 轻量认知状态：caution 警戒度参与动态裁决，O(1) 纯 CPU、可降级**
- 做法：在 core/boundary.py 定义 CognitiveState：纯 O(1) 时间衰减（decay = max(0, base - elapsed_sec * RATE)，RATE 如 0.1/秒），caution∈[0,2]，无锁（单线程事件回调）。signal() 由 boundary.on_event 在以下证据触发：guard 记录 blocked（含 denied）、evaluate_action 连续 confirm、agent 层循环检测、用户显式取消；弱信号（普通对话/无动作）不触发，绝不虚构警戒。catch 信号阈值到 2 时，decide() 把中等风险动作的 confirm 保持为 confirm（不额外放行），对 allow 低风险无影响。tick 只在事件驱动时调用，禁止后台轮询；brain 可选写 learn_fact('...', category=
- 文件：core/boundary.py, tests/test_boundary_cognitive.py

**5 — 状态/审计出口与运行时挂载. 可观测出口 + 挂载：/api/boundary/status、register_in_manifest、单例注入**
- 做法：在 core/boundary.py 定义 register_in_manifest(reg) 导出边界工具（boundary_status、boundary_issue_token、boundary_revoke_token），全部走 ToolDef，风险级标 safe；并在 core/runtime.py 的 _register_extension_tools 列表追加 ('core.boundary', 'Boundary registry registered')（一行，仿既有 7 项）。在 main.py 新增 @app.get('/api/boundary/status')，仅只读聚合 boundary 单例状态；不触碰现有 /api/config/permission 与 /api/status。boundary 单例以 get_boundary() 全局函数提供，注入点：c
- 文件：core/boundary.py, core/runtime.py(追加 1 行 register + 1 行 middleware.add), main.py(新增 1 个只读路由), tests/test_boundary_status_api.py

**6 — 端到端集成与回归验证. 端到端验证 + 全量回归：live 链路裁决、sync_permission 联动、零破坏**
- 做法：集成收尾：使用 create_runtime(root, startup_side_effects=False)（runtime.py 已支持，禁止写入 config/brain_data）构造运行时，注入 BoundaryMiddleware 后，用 conversation_hub 直接 submit 一条 live 文本请求验证端到端裁决；验证 sync_permission 联动；最后跑全量测试套件确认零回归。不修改 core/action_policy.py、core/tool_guardrails.py、core/tool_registry.py、core/agent.py 的行为，仅新增注入。
- 文件：tests/test_boundary_integration.py

### 红线测试

- 红线1：deny 底线恒真 — decide() 在 evaluate_action 返回 deny 时，confirmed=True / permission=root / token=super 三种增强均不改变 deny 结果（test_boundary_risk.py::test_botto
- 红线2：统一分级映射完整 — 映射表覆盖全部已知工具名（TOOL_RISK_LEVELS 键全集）与 4+5+3 全部权限级，未知工具回落到默认行为（test_boundary_risk.py::test_unified_risk_map / test_unknown_tool_defaults）。
- 红线3：middleware 拦截链路 — deny→MiddlewareRejected→registry.execute 返回 ToolResult.failure；allow 原样通过且 guard.post_check 仍执行；confirm 未确认前 handler 不被调用（test_b
- 红线4：token 生命周期 — 签发→消费单用途失效；过期拒绝并自动清理；token 足够时 confirm→allow（deny 不被覆写）；audit 无明文 token（test_boundary_token.py）。
- 红线5：认知状态性能与降级 — caution 达标后动作更严格、60s 衰减回基线；无 brain 时 tick/on_event no-op；10k 次 tick<1s（单次<0.1ms）（test_boundary_cognitive.py）。
- 红线6：观测与回归 — /api/boundary/status 契约成立；/api/config/permission 响应逐字不变；register_in_manifest 可装载；live 文本经 conversation_hub→agent→工具→boundary 有审计记录；runtime

### 验收

- 验收1（红线守卫恒真）：单元测试覆盖 evaluate_action 的 deny 场景（删除 Windows 底层系统文件、修改 Javis 自身代码/应用文件），断言 Boundary 永不把 action=deny 提升或放行，即使 confirmed=True / permission=ro
- 验收2（权限映射全通过）：新增 Boundary 把 utils/config_api.PERMISSION_LEVELS 4 级、tool_guardrails.PERMISSION_LEVELS 5 级、control._LEVEL_RANK 3 级映射到统一 rank 0-4，提供映射表可测；
- 验收3（PermissionToken 端到端）：BoundaryTokenStore 可签发/核销短时 token（TTL 5 分钟、单用途、hash 存储、支持层级），token 生效期间对应风险级动作免 confirm 直接 allow，过期待后恢复 confirm；全程有 audit 记录（
- 验收4（认知状态轻量）：CognitiveState 只做 O(1) 时间衰减与整数 clamp（纯 CPU，无锁），单次 tick <0.1ms；仅在 catch/repeat 等明确信号下提升 caution，默认不虚构警戒，低配置/无 brain 时全链路退化到静态权限行为（不降速）。
- 验收5（可观测出口）：新增 /api/boundary/status 返回 permission 生效级、caution、token 数、audit 最近条目；main.py 原 /api/config/permission 与 /api/status 的 permission 字段保持原样不变，无
- 验收6（不重写存量）：对 core/action_policy.py、core/tool_guardrails.py、core/tool_registry.py、core/agent.py、control/command_tasks.py、main.py 的既有行为零破坏——全部测试套件通过；新逻辑

### 风险

- 风险1（双闸死锁/权限系割裂）：现有三套权限系（config_api 4级、guard 5级、control 3级）各自为政；guard.pre_check 在 confirmed=True 时仍会因 risk>permission_level 硬拦截（tool_guardrails.py:215），导致『用户已确认却被第二道闸挡死』。应对：Boundary 统一映射到唯一 rank，decide
- 风险2（权限升级绕过 deny）：若 token 逻辑把 evaluate_action 的 deny 也放行，将摧毁底线安全。应对：decide() 先跑 evaluate_action，action==deny 直接返回且所有增强（confirmed/token/root）都不可覆写；红线测试 test_bottom_line_deny_never_overridden 显式锁死。
- 风险3（token 明文泄露）：token 若以明文存内存或写 audit，会泄露权限提升面。应对：沿用 control/command_tasks.py 已验证的 sha256 hash 存储模式，audit 只写 hash；红线测试 test_token_not_leaked_to_audit。
- 风险4（认知状态拖慢实时语音）：若 cognitive 更新引入 IO/锁/后台轮询，会破坏低延迟语音路径（P0-6 的优化成果）。应对：CognitiveState 纯 O(1) 无锁整数运算，tick 只在事件驱动时调用，红线断言单次 tick<0.1ms、无 brain 时 no-op 不降速。
- 风险5（Middleware 注入影响既有工具）：middleware 是 fail-closed 嵌套管线，before() 抛异常可能误伤所有工具调用。应对：BoundaryMiddleware.before() 内部 try/except，任何异常按原始 allow 放行并记 logger（fail-open 于此层，底线仍由 guard/evaluate_action 兜底），且仅对 de
- 风险6（配置缺失/低配置降级）：无 config.yaml 或 brain 缺失时若 hard-fail，会影响离线单机安装。应对：所有外部依赖用 try/except 包裹，默认 rank=config_api 默认 full_access(level1) 行为，缺失即退化为静态权限行为，可独立验证不虚构。
- 风险7（audit 内存膨胀/泄露敏感参数）：audit 若记录完整 params 或无限增长，会泄漏敏感信息并拖慢。应对：params 一律先经 tool_guardrails.sanitize_params 脱敏，队列上限 500 条滚动裁剪，与 guard.audit_log 同规模（≤1000）。
- 风险8（chat_quick 死路径诱饵）：voice/realtime.py:379 引用未定义的 agent.chat_quick（遗留死代码，未接入 main.py），若被误当作本器官入口会引入假链路。应对：明确不纳入，只走 /ws 网关 + agent.chat 主线，集成测试以 conversation_hub.submit 为真入口。


## core/life 心灵层（心：认知状态/情绪/人格/循环）——在 Javis-worktrees/voice-p0-reliability 工作树内落地

**目标**：让 Javis 有心：基于真实事件派生可验证的认知状态（LifeState 向量）与功能性情绪（EmotionVector），叠加持久化的六大人格（BigSix）与认知循环状态机（LifeCycle），由 LifeManager 统一聚合并在 EventBus 上节流广播。目标不是'宣称活着'，而是让状态可查询、可随时间演化、可被上层（web/voice/未来器官）与 LLM 自身（life_status 工具）读取——活到什么程度：每个真实事件的成败都被反映进状态，且所有数值有界、可衰减、可持久化、可降级，零侵入现有模块。

### 模块结构

**core/life/life_state.py** — 
- LifeState

**core/life/emotion.py** — 
- EmotionVector
- EmotionUpdateRule

**core/life/personality.py** — 
- PersonalityProfile
- PersonalityStore

**core/life/life_cycle.py** — 
- LifePhase
- LifeCycle

**core/life/life_manager.py** — 
- LifeManager
- LifeService

### 数据流（活着的一刻如何参与）

在"活着的一刻"，LifeManager 是一个纯事件驱动的派生状态机，输入全部来自真实发生的、可验证的事件，不产生任何 LLM 调用： (1) 输入：EventBus 上 ToolRegistry.execute 发布的 tool.started / tool.completed / tool.failed（payload: tool, category, success, duration_ms, error），以及 AgentRunStore 发布的 agent_run.created/completed/failed/cancelled（payload: run_id, status, objective/error）；可选补充 HookEvent.USER_PROMPT_SUBMIT（agent.py:555 已触发，标记'用户在互动'）；(2) 处理：LifeManager._on_event(event) 把事件类型映射为 (signal, delta) → 同步更新四层：LifeState.apply_signal 更新认知状态向量（energy/arousal/focus/mastery/connectedness，含指数衰减向 baseline 回摆）；EmotionVector.update 按 EmotionUpdateRule 更新功能性情绪（frustr

### 实施步骤

**1. 数据模型：LifeState 认知状态向量 + EmotionVector 功能性情绪（纯 dataclass，零外部依赖）**
- 做法：新建 core/life/life_state.py：@dataclass LifeState，字段 energy/arousal/focus/mastery/connectedness(0-1 float) + _baseline dict + updated_at；方法 apply_signal(name, delta) 用 clamp 保证 [0,1]，decay(dt) 按指数回摆 baseline，snapshot()/to_dict()。新建 core/life/emotion.py：@dataclass EmotionVector，维度 glad/calm/focused/frustrated/tired/alert，方法 update(deltas) clamp、decay(dt)、dominant()->(name,value)、valence_arousal()、to_
- 文件：core/life/life_state.py, core/life/emotion.py, tests/test_life.py

**2. 状态机：LifeCycle 认知循环（LifePhase 枚举 + 事件/计时驱动迁移）**
- 做法：新建 core/life/life_cycle.py：class LifePhase(str, Enum): idle/thinking/acting/reflecting/resting；class LifeCycle 持 current_phase、phase_entered_at、idle_timeout_s(默认 60)、heartbeat_s(默认 30)；方法 on_event(event_type:str, now)->str 按事件迁移（tool.started→thinking/acting，tool.completed/failed→reflecting，agent_run.completed→reflecting，agent_run.failed→resting），tick(now) 处理空闲超时回 idle、resting 恢复 energy 回调，enter(ph
- 文件：core/life/life_cycle.py, tests/test_life.py

**3. 人格：六大人格 BigSix 数据模型 + 独立 json 持久化**
- 做法：新建 core/life/personality.py：@dataclass PersonalityProfile，字段 conscientiousness/openness/agreeableness/stability/extraversion/curiosity（各 0-1）+ confidence + version；方法 trait(name)->float、baseline(name)->float、to_dict()。class PersonalityStore：save(profile, path=None) 写 brain_data/life/personality.json（新目录，不碰 facts/），load()->PersonalityProfile 容错回默认；缺省用代码内 DEFAULT_BIGSIX。文件写入带 makedirs(exist_ok=True)
- 文件：core/life/personality.py, tests/test_life.py

**4. 引擎与广播：LifeManager 订阅 EventBus→四层联动→节流广播 life.state；LifeService subsystem 包装 + life_status/life_tune 工具**
- 做法：新建 core/life/life_manager.py：class LifeManager 持 self._state/_emotion/_personality/_cycle、event_bus、throttle_ms(默认 1000)、last_broadcast_at、_lock(RLock)。start(runtime)：订阅 EventType.TOOL_STARTED/TOOL_COMPLETED/TOOL_FAILED 与 'agent_run.*' 到 self._on_event，可选 get_hook_manager().register(HookEvent.USER_PROMPT_SUBMIT, cb)；若配置开心跳则起 daemon 线程每 heartbeat_s 调 _tick（JAVIS_TEST_MODE 不启动）。_on_event(event) 同步无阻
- 文件：core/life/life_manager.py, tests/test_life.py

**5. 接线与硬化：main.py 加 2 行挂载（唯一现有文件改动，additive），降级/持久化/不虚构承诺收口**
- 做法：main.py 在 perception/evolution 挂载处附近加 `from core.life.life_manager import LifeService` 与 `runtime.register_subsystem(LifeService())`（additive，2 行，不触碰其他行）。硬化：LifeService.stop() 触发 PersonalityStore.save（异步安全：RLock + 只在 stop/每 10 分钟）；_on_event 全 try/except，订阅回调抛错被 EventBus.publish 捕获（core/events.py 已对 handler 异常 try/except）；snapshot 始终带 simulated=True/model='derived'，life_status 工具描述注明'派生自工具/运行事件的模拟状
- 文件：main.py, core/life/life_manager.py, tests/test_life.py

### 红线测试

- 红线1 core.life 各模块 import 无副作用：subprocess `python -c "import core.life.life_manager; print('ok')"` returncode==0，不启动线程、不写文件（对齐 test_runtime_module_impo
- 红线2 LifeState 越界与衰减：apply_signal(+1000)→clamp 到 1.0，apply_signal(-1000)→clamp 到 0.0；decay(dt) 单调向 baseline 收敛
- 红线3 EmotionVector：对 tool.failed 事件应用 EmotionUpdateRule 后 dominant()==('frustrated',_) 且全部维度在 [0,1]
- 红线4 人格持久化：tempdir 下 PersonalityStore save→load 往返六维一致；缺失文件 load() 返回默认且不抛错；产物落在 brain_data/life/ 不污染 facts/
- 红线5 LifeCycle 迁移：on_event('tool.started')→acting；on_event('tool.completed')→reflecting；tick 超 idle_timeout_s→idle；resting 态能量恢复
- 红线6 事件→状态闭环：EventBus+publish tool.failed → snapshot()['emotion']['frustrated']>baseline 且 bounds 合法；publish tool.completed(success)→focus 上升
- 红线7 广播节流：订阅 life.state 计数，节流窗口内 3 次工具事件 ≤1 次广播；注入时间推进后可再广播
- 红线8 Subsystem 挂载：register_subsystem(LifeService()) 后 get_runtime_status()['subsystem_status']['life']['state']=='running'，'life' in subsystems
- 红线9 工具契约：ToolRegistry+register_in_manifest 后 asyncio.run(registry.execute('life_status',{})) success 且 data 含 ok+snapshot
- 红线10 降级：LifeManager() 无 event_bus / 无 brain 不崩溃，status()/snapshot() 返回有效默认值，广播 no-op
- 红线11 不虚构活着：snapshot()['simulated'] is True 且 persona/工具描述不含'真正意识/主观体验/活着'断言
- 红线12 回归门：新增挂载后 tests/test_p0_runtime.py 全绿（尤其 test_main_import_test_mode_suppresses_startup_side_effects 不出现新增启动输出）

### 验收

- 新增 core/life/ 下 5 个文件 + tests/test_life.py；除 main.py 加 2 行挂载外，零改动 core/、knowledge/、memory/、tools/、utils/ 现有文件
- JAVIS_TEST_MODE 下 `python -m unittest tests/test_life.py` 全绿；`python -c "import core.life.life_manager"` 无副作用（不启动线程、不写文件、不加载 brain）
- 真实事件闭环可独立验证：向 EventBus publish 一条 tool.failed 事件后，LifeManager.snapshot() 中 frustration>baseline 且全部维度在 [0,1]；publish 一条 tool.completed(success) 后 focu
- 广播节流生效：throttle 窗口内多次工具事件产生 ≤1 次 life.state 事件，窗口过后可再次广播（测试用可注入时间断言）
- 生命周期可见：get_runtime_status()["subsystem_status"]["life"]["state"]=="running"；life_status 工具可执行并返回 {ok, snapshot}
- 人格持久化：PersonalityStore 在 brain_data/life/personality.json 往返一致；重启实例后 trait 值不变
- 降级安全：event_bus=None 或 brain=None 时 LifeManager 不崩溃，snapshot() 仍返回有效默认值；广播为 no-op
- 不虚构活着：snapshot() 含 simulated=True / model="derived" 字段，persona 文本不含'真正意识/主观体验'宣称（红线测试断言）
- 低配置降级：后台心跳线程在 JAVIS_TEST_MODE 或配置关闭时不起动；配置走代码默认值 + 环境变量覆盖，不强依赖 config.yaml

### 风险

- 虚构'活着'的承诺风险：状态是派生自真实事件的模拟值，若被 UI/文案误读为'真正意识'会损害可信度。应对：snapshot() 强制 simulated=True / model='derived'；life_status 工具描述注明模拟性质；红线测试断言 persona 文本不含意识宣称
- 性能/延迟：EventBus handler 在 publish 线程同步执行（core/events.py），若 _on_event 做 IO 或阻塞会卡工具执行。应对：热路径纯 O(1) 内存更新、无 await、无磁盘；持久化只在 stop/10 分钟低频；广播节流 throttle_ms；快照读用 RLock
- 事件源稀疏导致状态不动：Agent 主循环自身不发 THINKING 事件，若只靠 tool.* 事件，纯对话回合无状态变化。应对：订阅 USER_PROMPT_SUBMIT（agent.py:555 已触发）+ agent_run.* 事件 + 心跳 tick 空闲回摆，保证状态持续演化但永不越界
- 与现有记忆/反思重复或污染：若把情绪写入 brain._facts 会干扰 recall/压缩优先级。应对：PersonalityStore 用独立 brain_data/life/ 目录；只读复用 Reflector/Episode 结果做校准，不新增 facts 类别
- 低配置/无 brain/无 event_bus 环境：启动顺序中 ToolRegistry 可能先于 LifeManager 注册。应对：全部订阅/回调用 try/except + getattr 守卫；LifeManager() 无 bus 时广播为 no-op、snapshot 返回默认值；测试覆盖降级路径
- 线程安全与生命周期：_tick 心跳线程与事件回调并发访问状态。应对：单 RLock 包裹所有状态读写；stop() 置 running=False 并 join；atexit 不注册（避免与 Brain 的 _flush 冲突，对齐 test_create_runtime_without_startup_side_effects_does_not_register_brain_exit_flus
- 与 voice 层 tone 对接未知：life.state 携带 speech_hint，但 voice_tools.py 的 TTS 参数签名未验证（需进一步确认），v1 只输出字段不强制消费，避免跨系统耦合
- config.yaml 尚无 life 节：不强依赖 config_api，用代码内置默认值 + 环境变量（如 JAVIS_LIFE_ENABLED=0 关闭）覆盖；需进一步确认是否要在 config_api 暴露
- 状态饱和/死循环信号：某类信号持续出现会让维度卡在 1.0 或 0.0 而失去区分度。应对：delta 按 (1 - current) 缩放 + 指数衰减回摆，广播仅在 |delta|>epsilon 时触发


## 成长层 core/life/habits + evolution（灵魂：自主进化 + 习惯学习）

**目标**：让 Javis 会成长：从真实相处中自动沉淀习惯（HabitLearner：高频成功工具路径 → 可复习/可采纳的习惯），积累双记忆 Me Memory（成长轨迹：成为什么样、我们一起做过什么），并接管已有 evolution 闭环（候选→验证→staged→active）+ SkillCreator，最终表现为"主动为你"：会话开始时注入已激活习惯到 prompt 上下文，主动频道（主动建议）由已激活 evolution 候选驱动。目标状态 = 无需人工喂养的自动成长：每次真实交互都留下可验证的成长痕迹（habit/Me Memory/evolution candidate 三者之一），且全部写入走事件→候选→review→active 的可治理管线，不绕过 action_policy 与既有权重。核心判断"活着的一刻"：agent._after_learn 结束后，若本 session 内产生了 ≥1 条新的 memory_candidate/evolution_candidate 或 habit 复用事件，则该时刻成立。

### 模块结构

**core/life/habit_learner.py** — 
- HabitLearner
- Habit

**core/life/me_memory.py** — 
- MeMemory

**core/life/growth_service.py** — 
- GrowthService

**core/life/skill_promoter.py** — 
- SkillPromoter

**core/life/__init__.py** — 
- GrowthService 聚合导出

### 数据流（活着的一刻如何参与）

事件源：core/tool_registry.py 每次 execute 自动 publish TOOL_COMPLETED(payload={tool, success, duration_ms}) 与 TOOL_FAILED；core/agent.py._after_learn 每轮对话结束调 get_controller().memorize + reflector.reflect（失败经验→brain facts）+ learner.learn_from_conversation；SessionEventStore.attach 已订阅 EventBus("*") 把所有事件落库（memory/session_db.py events 表）。成长链路（读到即用）：runtime.event_bus 事件 → SessionEventStore 落库 → 后台 GrowthService.tick 调用 HabitLearner.learn(store) 对 tool.completed 分组计数 → upsert_memory_candidate(kind=habit)（复用 session_db.upsert_memory_candidate 既有签名）→ 用户经前端 /api/memory/candidates 或 evolution 治理 review 置 activ

### 实施步骤

**1/6 红线测试先行：HabitLearner 从 tool.completed 事件固话习惯. 证明 tool.completed 成功事件可被固话为 kind=habit 的 memory_candidate，且轻量、不阻塞**
- 做法：新建 core/life/habit_learner.py，HabitLearner.learn(store) 仿照 memory/consolidation.py::EventMemoryConsolidator._consolidate_procedural（真实接口 upsert_memory_candidate(kind, content, evidence_ids, confidence)）实现，content 格式与 procedural 对齐为 Successful tool path: {task} -> {tool}；分组 key=(task, tool)。先写 tests/test_growth_layer.py。
- 文件：core/life/habit_learner.py, tests/test_growth_layer.py

**2/6 Me Memory 成长轨迹（双记忆）. 从真实对话沉淀成长轨迹，注入 prompt，形成第二类记忆**
- 做法：新建 core/life/me_memory.py，MeMemory.observe(brain, user_input) 只做关键词短路（我习惯/我希望/以后/帮我养成/你记得/别总是），命中则 brain.learn_fact(content=成长轨迹: {text[:60]}, category=me.memory, source=self_reflection, priority=3或4)；timeline(brain) 供注入；再在 core/prompt_builder.py::MemoryLayerBuilder.build 中新增一个 me.memory 块（复用 _facts 按 category 前缀过滤的既有模式，不重写现有块）。
- 文件：core/life/me_memory.py, core/prompt_builder.py

**3/6 GrowthService：注册 runtime 子系统 + 后台 tick 调度. 把成长动作挂到 runtime，可独立 tick 验证，降级安全**
- 做法：新建 core/life/growth_service.py，GrowthService 实现 SubsystemStatus 协议（start/status/stop），tick() 顺序调 HabitLearner.learn 与 MeMemory.observe；在 core/runtime.py 的 create_runtime 末尾（main.py 里 runtime.register_subsystem 之前已有 evolution_service 先例）增加 growth 子系统注册；status() 返回 metrics。tick 由外部/后台触发，不在 subscribe 回调内做重活（EventBus 回调为同步阻塞，避免拖慢 execute）。
- 文件：core/life/growth_service.py, core/runtime.py

**4/6 接通 evolution 闭环 + SkillCreator（主动进化的出口）. 让已激活 habit 能长成真实技能，复用既有治理，不重写**
- 做法：新建 core/life/skill_promoter.py，SkillPromoter.promote_habit(runtime, candidate_id)：取 kind=habit/active 候选解析 task/tool → 校验名称安全（复用 skill_creator._validate_name 规则）→ get_creator().create(name, description, prompt)（真实签名 create(name, description, prompt, category, tags, author)）→ 发布 skill.learned 事件；publish skill.learned 到 event_bus。evolution 侧直接复用 EvolutionCandidateEngine.review + 既有 /api/evolution/* 
- 文件：core/life/skill_promoter.py

**5/6 主动频道接线：会话开始注入习惯 + proactive 建议接口. 让成长可见可主动：习惯进入每轮上下文，主动建议可被前端/voice 拉取**
- 做法：在 prompt_builder MemoryLayerBuilder.build 新增 habit 块（读 HabitLearner.active(runtime.event_store) 前 N 条，格式 Habit: {task} via {tool}）；main.py 增加 /api/growth/status 与 /api/growth/proactive 两个 GET 路由（模式照抄已有 /api/evolution/candidates）；前端后续可轮询。所有写入仍经既有工具，不新增动作。
- 文件：core/prompt_builder.py, main.py

**6/6 跨系统兼容 + 性能/降级收口. 保证在低配置、无 LLM、跨平台下仍可独立验证与安全**
- 做法：所有新模块仅在 getattr(runtime, 'event_store') 可用时工作（与 evolution/service.py 同款防御）；HabitLearner 扫描限制 recent_events(limit=500) 与 memory_candidates(limit=50)；不在 EventBus 同步回调里做聚合（改由后台 tick 触发）；对 skills 目录写入仅通过 SkillCreator，不直接 shutil（跨平台路径一致）。
- 文件：core/life/habit_learner.py, core/life/growth_service.py

### 红线测试

- 红线1: test_habit_learner_creates_habit_candidate_from_3_successes — 构造 EventBus+SessionEventStore，publish 3 次 tool.completed success → HabitLearner.lea
- 红线2: test_habit_learner_ignores_failure_events — tool.failed/非 tool.completed 不产生 habit 候选
- 红线3: test_me_memory_observe_writes_me_fact — observe(含'我习惯'/'希望你') → brain._facts 出现 category=me.memory
- 红线4: test_prompt_builder_injects_me_memory_and_habits — MemoryLayerBuilder.build 输出含 成长轨迹 与 Habit: 行，且既有 user_style/session.topic 块原样保留
- 红线5: test_skill_promoter_creates_skill_from_active_habit — promote_habit 生成技能文件并进入 SkillCreator 审查队列（status=active/或待改进），list_skills 可见
- 红线6: test_growth_tick_no_event_store_returns_zero — event_store=None 时 tick/status 不抛异常，返回0计数，其他子系统正常
- 红线7: test_evolution_pipeline_reused — 复用既有测试 test_evolution_engine_creates_reviewable_workflow_candidate（candidate→validate→staged→active→record_evolu
- 红线8: test_growth_api_contract — GET /api/growth/status 与 /api/growth/proactive 返回200及合法结构

### 验收

- 测试出口：pytest tests/test_growth_layer.py 全绿；tests/test_p0_runtime.py 既有 evolution/memory 相关用例不回归（红线7 复用通过）
- 行为出口：在真实 main.py 启动（D 盘安装端）后，制造 3+ 次同 (task,tool) 的成功工具调用 → /api/memory/candidates?kind=habit 出现候选；经 /api/memory/candidates/status 置 active → /api/gro
- 机制出口：每轮真实对话结束后，brain._facts 出现 category=me.memory 成长轨迹；prompt 上下文可见
- 闭环出口：对 active habit 执行 promote_habit 后 skills/ 出现新技能文件，且 SkillCreator 审查状态可见（active/needs_improvement）
- 降级出口：无 event_store/brain 或低配置（无 LLM）下启动正常，growth status=degraded，不影响语音/桌面/记忆其余器官
- 性能出口：单次 growth tick <100ms（限 recent_events 500 + memory_candidates 50 + 常量 brain 遍历），不在 EventBus 同步回调聚合
- 主动出口：主动频道（/api/growth/proactive 或 skill.learned 事件）能产出至少一条真实数据驱动的建议，非硬编码
- 合规出口：全程未重写 core/action_policy.py、core/tool_guardrails.py、evolution/engine.py、memory/session_db.py、core/skill_creator.py；新模块以门面对齐方式叠加

### 风险

- 习惯误学：基于 (task,tool) 的重复计数可能把一次性的同任务重复误判为习惯。缓解：min_successes 默认3可配，confidence 渐进封顶0.95，且固化产物只进 candidate 需显式 review 置 active，激活前不影响任何行为。
- prompt 膨胀/上下文毒性：注入 habit 与 me.memory 可能挤占上下文。缓解：各自 ≤5 条、单条 ≤80 字符，仅注入 active 状态，通过 MemoryLayerBuilder 既有 category 前缀过滤与去重。
- 重写风险（铁律）：新模块若不克制会碰既有模块。缓解：所有实现只调用已核验的公开接口（learn_fact/upsert_memory_candidate/evaluate_action/create），零改动 evolution/engine 与 session_db；prompt_builder 仅追加一个块，不重写现有块。
- EventBus 同步回调性能：若把聚合挂到 subscribe 会阻塞工具 execute。缓解：聚合只在后台 tick/外部触发，事件回调保持纯落库（现状）。
- 技能自产风险：promote_habit 生成技能若失控会污染 skills 目录。缓解：走 SkillCreator 既有审查（SkillReviewer.review score>=70 才 ACTIVE），名称校验复用 _validate_name，且 create 只生成模板文件不执行代码（已核验 create 的 register() 为 pass）。
- Me Memory 关键词误触发隐私：成长信号关键词命中即写入 facts 落盘。缓解：content 截断 60 字符、category 独立便于治理与删除，后续可加负面词表（需进一步确认是否需要）。
- 多端并发 tick：后台线程与手动 API 同时 tick 可能重复 upsert（session_db 幂等：同 content 哈希合并 evidence，已核验）。缓解：依赖既有幂等 upsert，tick 不删候选。
- 跨平台路径：skills 与 brain_data 路径在 Windows/Linux 的兼容。缓解：全部通过 SkillCreator/Brain 既有 Path 逻辑，不手写 shutil，遵循既有 FACT 刷盘模式。
- 行为未真正改变（不虚构活着）：若用户不 review，习惯永远停在 candidate，主动性不可见。缓解：acceptance 中主动出口明确只认数据驱动的 proactive 建议，前端/voice 主动频道作为后续接线点（需进一步确认主动频道的 UI 承载）。


## 身体层 body（app/src/life）：认知驱动存在，分级降级（3D VRM → WebGL glow → 2D sprite）

**目标**：让 Javis 拥有可感知、可回应的"身体"：前端 body 层将认知（life.state）物化为 3D/2D 存在——单条 event→动作 链路延迟 ≤30ms，渲染帧 ≤30fps（glow 目标，sprite 不依赖 rAF 亦可运转），并按渲染能力分级降级（3D VRM → WebGL glow → CSS/sprite 兜底），在 pet 与 live 两个窗口共享同一驱动，不修改任何现有模块。

### 模块结构

**app/src/life/lifeState.ts** — 

**app/src/life/capability.ts** — 

**app/src/life/EmotionMapper.ts** — 

**app/src/life/FaceController.ts** — 

**app/src/life/EyeController.ts** — 

**app/src/life/MotionController.ts** — 

### 数据流（活着的一刻如何参与）

「活着的一刻」= 后端完成一次语音转写 → 身体做出表情/动作/口型。链路：① 用户说话 → createVoiceCapture 的 ws_voice_stream → onTranscript(final) → client.send(text)（sendVoice 同链）；② client.send 先 runtimeStateCoordinator.signal({source:'ui', state:'thinking', detail:'正在理解'}) → applyRuntimeEvent 的 WS 事件（activity.tool_started→executing / response.delta→speaking / request.completed→terminal idle）→ runtimeStateCoordinator.signal(...)；③ 本器官 LifeDriver 只读订阅 runtimeStateCoordinator.subscribe(snapshot)，不再修改主状态；④ LifeDriver 将 RuntimeSnapshot 归一化为 life 输入：state→bodyState + emotionHint（detail 文本/JSON 启发式），30ms 内投递；⑤ 输入（快照）→ 决策（EmotionMapper：sta

### 实施步骤

**Step 1 — 身体态归一化（life.state 契约）. 定义 body 层的输入契约：把现有 RuntimeSnapshot + 可选 emotionHint 归一为认知可驱动的 life 输入，并为后端 core/life 预留升级位**
- 做法：新建 app/src/life/lifeState.ts（纯类型+纯函数，无 DOM）。导出 type LifeTier='vrm'|'glow'|'sprite'；type BodyState=LiveState；type LifeInput={state:BodyState; emotionHint?:string; audioLevel?:number; updatedAt:number}；export function normalizeLife(snapshot: Pick<RuntimeSnapshot,'state'|'detail'|'updatedAt'>): LifeInput（复用 runtimeStateTypes 的 detail 清洗语义：取 detail 去空/去 'Javis' 前缀→emotionHint，供 EmotionMapper 启发式）。不触碰 
- 文件：app/src/life/lifeState.ts

**Step 2 — 渲染能力分级（lifeTier 检测）. 启动时探测环境，稳定给出渲染档位，保证低配/无 WebGL 总能运行**
- 做法：新建 app/src/life/capability.ts：export interface CapabilityProbe { tier(): LifeTier; flags(): LifeCapabilityFlags }；export interface LifeCapabilityFlags { webgl:boolean; vrmAssets:boolean; lipSupported:boolean }。实现参考 LiveOrbRenderer 的 canvas.getContext('webgl') 检测（不 import 该模块），vrmAssets 用 AssetRegistry 查询（见 Step 6）或不存在即 false，lipSupported 由 islip 标志与本地音频判定。tier() 规则：vrmAssets&&webgl→'vrm'；webgl→'gl
- 文件：app/src/life/capability.ts, app/src/life/AssetRegistry.ts

**Step 3 — 认知→动作的轻量决策（EmotionMapper）. 把 LifeInput 映射为 FaceController/EyeController/MotionController/LipSync 的动作参数；决策必须是纯函数、轻量（无 rAF、无定时器）**
- 做法：新建 app/src/life/EmotionMapper.ts（纯函数）：export interface LifeAction { emotion: string; blink: boolean; talk: boolean; pulse: number; }；export function mapLife(input: LifeInput): LifeAction。映射表：BLINK 在 idle/thinking；TALK 在 speaking/listening；PULSE 依据 state（speaking>listening>executing>thinking>idle）；emotion 由 state+emotionHint 启发（如 'executing' 与 detail 含『工具』→'tool'；speaking→'speak'；error→'distress'；其
- 文件：app/src/life/EmotionMapper.ts

**Step 4 — 动作执行面（FaceController/EyeController/MotionController/LipSync）. 将 LifeAction 落地为可观测的动作输出，且全部通过接口注入（life 不直接写 document），保证可测试与可降级**
- 做法：新建 app/src/life/LifeCanvas.ts：export interface LifeCanvas { isSupported(): boolean; attach(root: HTMLElement): void; setScene(preset: {state:BodyState; accent?:string}): void; setAudioLevel(level:number): void; destroy(): void }。新建 app/src/life/FaceController.ts：export interface FaceController { apply(action: LifeAction): void; mount(target: HTMLElement): void }；EyeController、MotionController、LipS
- 文件：app/src/life/LifeCanvas.ts, app/src/life/FaceController.ts, app/src/life/EyeController.ts, app/src/life/MotionController.ts, app/src/life/LipSync.ts

**Step 5 — 身体组装与驱动（LifeDriver + AvatarRenderer + sprite2d）. 把 Face/Eye/Motion/LipSync/Canvas 组装成一个渲染出口，并实现 2D 降级渲染面（sprite2d），确保无 3D 时仍有『身体』**
- 做法：新建 app/src/life/LifeDriver.ts：export interface LifeDriver { start(): void; stop(): void; setMode(mode: DesktopMode): void; setInput(input: LifeInput): void }。export function createLifeDriver(options: LifeDriverOptions): LifeDriver。LifeDriver 持有 lifecycle：start() 时若 tier==='glow' 且 LifeCanvas.isSupported() 则 canvas 挂到 pet-anchor（查 '#pet-surface-root .pet-anchor'，读不到则使用容器的 querySelector('[data-life-
- 文件：app/src/life/LifeDriver.ts, app/src/life/AvatarRenderer.ts, app/src/life/sprite2d.ts

**Step 6 — 集成与 3D 预留（main.ts 挂载 + sprite2vrm 空运行时）. 把 body 层接入真实 app（不改现有订阅），并为 3D VRM 路线保留明确空运行时，避免重复初始化**
- 做法：新建 app/src/life/sprite2vrm.ts：export function sprite2vrm(): { supported: boolean } 恒 { supported:false }（空运行时，明确标注『需 three/@pixiv/three-vrm 依赖，未安装，后续接入』）。main.ts 仅在 bootRuntime 成功且 !previewOrbState 时挂载：在 document.getElementById('pet-surface-root') 存在时 createLifeDriver({ coordinator: runtimeStateCoordinator, voice: voiceCapture, root: document.getElementById('pet-surface-root')!, tier: probeTier() 
- 文件：app/src/life/sprite2vrm.ts, app/src/main.ts

### 红线测试

- 红线先行（源扫描 + 纯函数双轨，与仓库 node --experimental-strip-types 约定一致）：main.ts 必须仍含 runtimeStateCoordinator.subscribe 的 4 处订阅（statusRail/liveSurfaceStatus/petSurf
- app/tests/lifeState.test.ts：normalizeLife({state:'thinking', detail:'正在分析桌面布局'}) → LifeInput.state==='thinking' 且 emotionHint 非空；detail 为 JSON 时取 text
- app/tests/capability.test.ts：注入 fake getContext 返回 null → tier()==='sprite'；返回伪 WebGL → 'glow'；无 vrmAssets → 永不为 'vrm'；flags 布尔且不抛异常
- app/tests/emotionMapper.test.ts：mapLife({state:'idle'}) → {blink:true, talk:false}；speaking → talk:true；executing+『工具』detail → emotion==='tool'；同输入输出稳
- app/tests/faceController.test.ts / lifeCanvas.test.ts：注入 fake target 后 apply 幂等（同 action 两次不重复写 dataset）；WebGL null 时 isSupported()===false 且 destroy 
- app/tests/lipSync.test.ts：level() 恒在 [0,1]；isEnabled()===false 时恒 0（无音频路径静音）
- app/tests/lifeDriver.test.ts：注入 fake coordinator 后 start()，signal({state:'listening'}) → renderer 收到 LifeAction{talk:true}；同 state 重复 signal 不重复 rende
- app/tests/mainLifeIntegration.test.ts：readFileSync(main.ts) 断言 createLifeDriver 挂载于 pet-surface-root 且调 .start()，并断言现有 4 处 subscribe 与 voiceCapture 未变

### 验收

- `life/` 目录下各模块通过 `tsc && vite build` 严格编译（tsconfig strict），不修改任何现有 src 文件（git diff 仅新增）
- `lifeTier` 检测在 WebGL 可用时返回 'vrm' 或 'glow'、不可用时返回 'sprite'；低配/失败不抛错，总是有兜底渲染层
- LifeDriver 订阅 runtimeStateCoordinator 后，liver 输出（dataset 或 canvas）与 runtimeStateCoordinator.snapshot().state 一致；同一 snapshot 重复投递不会产生抖动
- `onAudio(audioBase64)` 进入时 LipSync.level() 为 0..1 标量，结束后回落到静音基线；无 onAudio 的浏览器预览路径自动静音（islip=false）
- 2D 降级渲染（motion + avatar 组合）与 3D VRM（sprite2vrm 空实现）共享同一 emotion 驱动：渲染层故障时通过 runtimeStateCoordinator.signal({source:'ui', state:'error'}) 暴露，不静默
- 集成面（main.ts）在 pet 与 live 模式下都驱动 body 层：每 60s 状态校验、请求完成回到聆听后 body 回到 idle/待命

### 风险

- VRM 依赖未安装（three / @pixiv/three-vrm 不在 package.json）：按铁律不预装，vrm 档以 sprite2vrm 空运行时占位，能力检测 vrmAssets 恒 false → 自动落 glow/sprite；MASTER-PLAN P4 的 3D 落地须另开一次依赖变更（风险消除：能力分级保证无依赖也可上线）
- existing 现有 LiveOrb（WebGL）与新增 LifeCanvas 共用同一 canvas 上下文资源：不触碰 LiveOrbRenderer，glow 档只在 pet 模式低配启用，live 模式仍由 LiveOrb 独占 orb canvas，LifeCanvas 仅在 #pet-surface-root 内独立 canvas（隔离，避免 GL 上下文冲突）
- RuntimeStateCoordinator 是单例，body 加入新订阅会增加 notify 广播成本：LifeDriver 只订阅一次、setInput 内做幂等 diff（同值不写 DOM），目标单次回调微任务内完成，避免 60fps 抖动
- sprite 档的 .life-body 挂载若与 PetSurface 的 pet-anchor 尺寸冲突：仅新增节点、不动 PetSurface 构造，尺寸用现有 surfaceDimensions 的 getLiveSurfaceSize(petScale) 约束
- performance/延迟：mapLife 为纯函数，Face/Eye/Motion 仅变化时写 DOM，Motion 用 WAAPI transition 而非 rAF 循环，LipSync 以注入音频电平取值，避免主线程高频计算
- 兼容/低配置：浏览器预览（isLocalPreview）无 onAudio 时 lipSupported=false 自动静音；WebGL 不可用时 sprite 兜底仍输出 dataset/CSS，验收在 node 测试与真实 WebView 双轨
- cross：body 感知 pet/live 用 document.body.dataset.desktopMode（现有字段），不直接 import setDesktopMode，避免循环依赖 windowMode
- 后端 core/life 不存在：本阶段用本地启发式（state+detail 推断），经 client.get('/api/life') 探测；返回 absent 不阻塞（风险应对：升级位保留，失败降级为本地推断，需进一步确认 future 推送协议）


---

## 记忆层（海马体）—— 补跑完整设计

> 此器官在初次工作流中 StructuredOutput 超限失败，本补跑由专用代理完成，回应了批判的关键问题。

### 设计目标
让 Javis 拥有类人双记忆：**Me Memory（它的日记）** + **We Memory（你们的回忆）** + relation_score + 遗忘机制，通过 MemoryManager 门面包装现有模块，不重写。

### 关键决策（回应批判）
1. **Me Memory 不用 learn_fact**：已核实 brain.learn_fact 按 md5(content) 进 _facts 并改变 _compute_brain_fingerprint → 每次写入重建 layer2 且被 recall 污染。改用 brain_data/hippocampus/me/entries.jsonl（append-only）。
2. **不复制 HabitLearner**：MemoryManager 只调用既有 get_controller 方法，新增部分全是自有存储。
3. **接线真实最小**：agent.py:530（规划 context_block）+ agent.py:936（回合后 memorize），各 1 行；runtime.py:400 装配 1 行。

### 模块结构
**memory/hippocampus.py** — 双记忆独立存储层（纯新增，零依赖 brain/controller）
- HippocampusEntry（dataclass）：id/kind/content/category/importance/tags/source/created_at/usage_count/privacy/status
- WePreference（dataclass）：id/key/value/importance/repeat_count/evidence_ids/privacy/status
- Hippocampus：ME_DIR=brain_data/hippocampus/me、WE_DIR=we、RELATION_FILE=we/relation.json
  - note_interaction(user_input, assistant_msg, feedback)：轻量写入，检测"记住/以后都这样"→long_term
  - append_me(entry)：entries.jsonl 追加一行（O(1)）
  - upsert_we(pref)：按 id 幂等合并，repeat_count+1，≥3 次升 long_term
  - recall_me/recall_we：按 importance 排序，跳过 dormant
  - relation_score()：内存衰减缓存，无 IO，[0,1]
  - to_lifestate()：产出 relation 契约 dict 给心灵层

**memory/forgetting.py** — 遗忘机制（纯函数）
- ForgettingScorer：score = importance × 0.5^((now-updated)/HALF_LIFE) × (1+log1p(usage))；long_term 恒 5.0+importance
- run_maintenance(dry_run=True)：score<阈值→archived（软删）；低重要度→dormant

**memory/manager.py** — 统一门面（真包装）
- MemoryManager.recall_block(query)：合并 controller.context_block + We 偏好 + Me 日记 + relation，≤300 字
- MemoryManager.after_turn(user_input, assistant_msg, actions, feedback)：保留 controller.memorize + 写 hippocampus + 更新 relation + publish MEMORY_WRITTEN
- get_memory_manager(brain)：全局单例

### 数据流（活着的一刻）
- 回合开始：Agent.chat 既有 context_block → 追加 manager.recall_block（We+Me+relation）
- 回合结束：_after_learn → manager.after_turn（保留 memorize + 写双记忆 + relation + 事件）
- 后台：runtime.start_cycles 旁 → get_memory_manager().start()（dry-run 维护）

### 实施步骤
1. 红线先行：test_hippocampus.py + test_memory_forgetting.py 先失败 → 实现 forgetting.py + hippocampus.py（纯 stdlib）
2. MemoryManager 门面：真包装既有 controller，不复制
3. 接线：agent.py 2 行 + runtime.py 1 行（白名单）
4. relation 出口 + 事件：relation_score + to_lifestate + MEMORY_WRITTEN 发布

### 红线测试（关键）
- test_layer2_fingerprint_untouched_by_hippocampus：只写 hippocampus 时 PromptBuilder 指纹不变（证明避开污染）
- test_after_turn_preserves_existing_memorize：brain._facts 仍含 conversation.user_msgs（controller 行为未丢）
- test_we_dedup_repeat_promotes：同一偏好 3 次 → repeat_count=3，importance≥4，触发 long_term
- test_long_term_exempt_from_forgetting：long_term 条目 score 恒 ≥5.0，永不被 archive
- test_no_rewrite_guard：git diff 仅含白名单文件

### 验收
- pytest tests/ 在 JAVIS_TEST_MODE=1 下全绿
- 双记忆可独立验证：Me 日记 append-only、We 偏好幂等、relation ∈[0,1]
- 遗忘 dry-run 2 周后启用真实降级；archive 软删可逆
- 改动清单固定：3 新模块 + 2 行 agent.py + 1 行 runtime.py
- brain/event_bus=None 时所有方法降级不抛异常


---

## 附录：批判架构师审查全文

I have completed a source-level verification of every load-bearing claim in the four organ plans. Here is my review.

---

# Javis 五器官可执行细化设计 — 首席架构师审查

结论先行：这是四份（core/life 心 + app/src/life 身体 + boundary 分寸 + habits/evolution 成长）质量高、红线意识强的设计，整体可执行。但存在 **1 个方向性误判（boundary）**、**3 个"假设已有输入"的悬空依赖（core/life、growth、body）**，以及 **若干个未验证即断言成立的技术假设**。按严重度排序如下。

## 最重要的 5 个问题

### P0-1（方向性错误）boundary 的核心问题"双闸死锁"在现状下不存在，器官在重复已有能力

- 计划宣称现有 guard `pre_check` 在 `confirmed=True` 时仍会因 risk>permission_level 硬拦截（"tool_guardrails.py:215"），导致"用户已确认却被第二道闸挡死"。**已核实这是错的**：`tool_registry.py:103` 的 `pre_check` 在 handler 之前调用；而 `agent.py:732` 调用 `self.tools.execute(tn, tp, confirmed=action_confirmed)` 时，只有 `evaluate_action != allow` 才进入确认流程（`agent.py:309 _should_auto_approve` 第一条就 `if evaluate_action(...).action != "allow": return False`）。因此**拒绝删除系统文件在 agent 层就终止，根本到不了 guard**；而 reach guard 的 confirm 类动作（普通删除/写 Javis 自身）在用户确认后 `confirmed=True` 会**放行**（`tool_guardrails.py:209`：`if decision.action == "confirm" and not confirmed: return`）。所谓双闸死锁场景实际不成立。
- **更严重的是重复能力**：`PERMISSION_LEVELS` 4 级（config_api）+ `TOOL_RISK_LEVELS` 5 级（guard）+ `_LEVEL_RANK` 3 级（control）**已经是同一套 `"safe→low→medium→dangerous→critical"` 0-4 数值映射**（guard `:24` 与 `_LEVEL_RANK` `:23` 完全同构），guard 已经通过 `PERMISSION_ALIASES`（tool_registry.py:21，`full_access→critical` 等）把 config 4 级接进 risk 5 级。计划中的"统一 rank 映射表"（step 1）在 60 行内重做这三套已有东西，验收红线 1/2 验证的恰恰是**已被现有代码满足的属性**。
- **先决问题**：Boundary 的 deny 底线（红线 1）要求"evaluate_action 返回 deny 时绝不放行"——但 evaluate_action 已经在 agent 层以 deny 拦截（`agent.py:689-697`），middleware 层根本收不到这个 deny；`decide()` 里再调一次 evaluate_action 只是重复。Token 提权（confirm→allow 免确认）**也发生在 agent 确认层**（`_should_auto_approve` + `wait_for_confirm`），Boundary 在 registry.execute 内无法介入；等你在 `create_runtime` 一行 add 进 middleware 时，permission 的单一事实源（config_api）与 agent 的确认链路（`agent.py:284 wait_for_confirm`）都不会把 token 纳入。
- **建议**：把 boundary 器官的 scope 砍掉 step 3/4（token/cognitive），只保留"映射表单测"与"audit 导出"（step 5 的只读 `/api/boundary/status` 是唯一有价值的新增），且不再声称修复双闸。若一定要 token 免确认，必须把注入点改到 `agent.py` 确认决策处——这违反"不重写现有模块"，需先澄清这条铁律是否允许。红线 2（"guard 双闸死锁被修复"）在当前架构下**不可验证**，因为修复对象不存在。

### P0-2（悬空输入）core/life 的真实事件输入中，agent_run.* 与 USER_PROMPT_SUBMIT 均未接入运行主线，纯对话回合没有事件驱动

- **tool.\* 已确认真实**（tool_registry.py:90,159；但 payload 是 `{"tool","category","success","duration_ms","error"}`，**没有 `task` 字段**，见 P0-4）。
- **AGENT_RUN_* 无消费方**：`AgentRunStore` 各 `_publish`（agent_runs.py:167,355,500）已在核心代码中，但生产代码中**没有任何模块 publish 它**——`create_run` 只在被外部显式调用的 `api_agent_runs`（main.py:509）和 `AgentRunRecorder.__init__`（conversation_hub.py:265 构造，**未用**）里触发；对话主流程（`conversation_hub.submit`，hub.py:94）走的是自定义 `activity.*` 事件（hub.py:281-311）而不是 `agent_run.*`。**当前运行的 Javis 主循环中 agent_run.created/completed 事件实际上不存在。**
- **USER_PROMPT_SUBMIT 无发布方**：`HookEvent.USER_PROMPT_SUBMIT`（hook_system.py:24）只定义了枚举，agent.py 中 `hook_manager.trigger(HookEvent.PRE_TOOL_USE/POST_TOOL_USE)`（:718,742）都有，但**没有任何地方 trigger USER_PROMPT_SUBMIT**。计划依赖的"agent.py:555 已触发"是虚构的。
- 而 `_after_learn` 是**在每个 message（含 text_delta）都调**（agent.py:505,583,612,637,673,682,811,823），且真正的主循环 prompt 由 agent 层 event 驱动。若 core/life 只订阅 tool.*，那么纯对话回合（P0-7 语音回合：无工具调用）状态下 LifeManager 完全不动——与风险条目"事件源稀疏导致状态不动"自相矛盾，且该风险的缓解方案（USER_PROMPT_SUBMIT + agent_run.\*）两个都是假的。
- **可验证的替代输入**：① `thinking.started`（事件已定义，`_completion_event` 若发出则真实）；② `MEMORY_RECALLED/MEMORY_WRITTEN`；③ 若需要"用户开始互动"信号，**必须新增一行发布**（在 conversation_hub.submit 或 main.py _submit 触发 `bus.publish("user.submitted",...)`），并把它写进"唯一现有文件改动"清单——这不是 2 行能覆盖的。**红线 6"publish tool.failed 后 frustration>baseline"可验证，但"状态随时间演化"的验收在真实运行下没有输入，需重新定义。**

### P0-3（悬空输入）body 层的 RuntimeSnapshot 是"全量复算的单例信号"，不是事件流；且 onAudio 不可用作 LipSync 直接输入

- RuntimeStateCoordinator（RuntimeStateCoordinator.ts:26）每次 signal 是**合并一次快照**（当前 state、detail、updatedAt），`subscribe` 回调立即回放当前快照（:52）。body 层订阅它没问题，但**没有 requestId 维度**；同一快照重复投递本来就是等幂快照，`LifeDriver` 的"幂等 diff"只是重复验证已有语义。真正的问题：
- **`onTranscript` 触发点在 `client.send(text)` 之前，后端 activity.\* 事件触发在之后**——`main.ts:319` `onTranscript: (text) => { client.send(text) }`。前后端"状态"是两个时钟：UI 立即 signal listening，后端返回 activity.tool_started 时前端才变 executing。body 层的 30ms 目标与"状态真实性"依赖这条**既有同步时序**，计划未验证它会不会在 listening→executing 之间出现明显空白（P0-6 低延迟路径就是为此优化的，需确认）。
- **onAudio 误用**：`onAudio(audioBase64)` 是 base64 的 PCM/WAV 整段（VoiceCapture.ts:14），不是逐帧电平流。LipSync 从它取 0..1 标量需要**解码整段音频**——这与"≤30ms、无 rAF、热路径轻量"自相矛盾。更合理的是用现成的 `onLevel: (level) => liveOrb.setAudioLevel(level)`（main.ts:316）或后端 TTS 播放回调。计划把"onAudio→LipSync"作为验收（`onAudio 进入时 LipSync.level() 0..1`），实现需要额外的解码/降采样模块，未写进 modules。
- **sprite2vrm 空运行时 + AssetRegistry 假名**：`capability.ts` 引用 `AssetRegistry`（Step 2 新建）但 `app/src/life/AssetRegistry.ts` 在 files 里只出现一次且没有测试/模块定义，`vrmAssets` 的判定来源不明；`PetSurface` 的 pet-anchor 是**模板字符串构造**（PetSurface.ts:31），`.pet-anchor` 选择器依赖这个 DOM 存在，body 层"新增 .life-body 节点不动 PetSurface 构造"在 pet 模式有具体 DOM 冲突风险（该节点会挂在谁下面、尺寸用 getLiveSurfaceSize 但 PetSurface 自身也在用）。

### P0-4（核心事件契约错位）tool.completed 无 `task` 字段，"习惯/进化候选"的数据源是空集

- 计划（growth 器官 data_flow 与红线 1/2）宣称 `HabitLearner` 按 `(task, tool)` 分组 `tool.completed`，`upsert_memory_candidate(kind=habit, content="Successful tool path: {task} -> {tool}", ...)`。**已核实 `tool_registry.py:159-168` 的 TOOL_COMPLETED payload 只有 `tool/category/success/duration_ms/error`，没有 `task` 字段**。
- 我搜遍整个代码库，**没有任何模块往 `payload["task"]` 写入**——consolidation.py:51 的 `payload.get("task","general")`、evolution/engine.py:38 的 `payload.get("task","general")` 都会静默落到 "general"。所以**现有 consolidation 的 procedural 候选和 evolution 的 workflow 候选已经全部退化为 (general, tool) 的单一分组**，每条 tool.completed 都会混在一起。growth 器官照抄同一个 payload 键，产出的习惯永远是 `general -> screenshot` 这类无区分度内容。
- **根因**：任务名（task）在 agent 层可见（`agent.py:660-675` 有 `resp.tool_calls` 循环、`user_input` 上下文），但从不传给 registry。若要让"习惯/进化"真正工作，**必须在 `agent.py` 的工具调用点把 `task=...`（如 user_input 摘要或步骤名）塞进 execute 参数再随 event 广播**——这是对 agent.py 的实质改动，与"零改动"铁律冲突，且不在任何计划的 files/改动清单里。**不做这个改动，growth 器官的"主动出口/闭环出口"验收在真实运行下永远产生空内容候选。**
- 附带：growth 器官 `HabitLearner` 与既有 `EventMemoryConsolidator._consolidate_procedural`（consolidation.py:43-70）**逐字重复**——后者已做 (task,tool) 分组、content 格式、confidence 渐进封顶、`kind="procedural"`。新模块的唯一差异是把 kind 改成 "habit"。这是纯粹的重写。

### P0-5（性能/延迟分析流于断言，未验证真实量级；两条关键约定未核对即写入计划）

- 三条 `≤Xms` 断言均无基准支撑：core/life "热路径 O(1) 无 IO"（可接受，因为是纯内存），**但 growth 的"单次 tick ≤100ms"**与 body 的"setInput ≤1ms"、"≤30fps"都是未测即写的目标，建议作为验收指标而非承诺。
- **关键误判：evolution 引擎与 EventMemoryConsolidator 的 `review` 目前没有任何生产调用者**。main.py 只 `register_subsystem(evolution_service)`（:98-99），从未调 `evolution_service.review()`；只有手动 `POST /api/evolution/review` 才触发。growth 器官"接管已有 evolution 闭环"建立在"闭环自动运行"的假设上——**它目前是手动/API 驱动的，不存在自动闭环**。验收红线 7"复用既有 evolution 测试不回归"能过，但"自动成长"在现状下不会发生。这也让 core/life 的 `agent_run.*` 与 growth 的 `evolution.review` 在"成长事实"上彼此为对方虚构数据源。
- **单机回归门不充分**：计划全部以 `tests/test_p0_runtime.py` 为回归基准。该文件已是 2192 行、60+ 用例、覆盖 command_tasks 根 token/回滚。body 层挂载若把 `lifeDriver` 引到 `main.ts` 而编译 tsconfig strict 通过，但 `test_app_release_blockers / test_app_voice_runtime_integration` 这类构建/WebView 测试是否仍绿**未纳入回归门**。

## 跨器官一致性（问题 1）逐项结论

- 数据流主链（心广播→身体订阅→记忆召回→分寸决策→成长学习）**不成立**：心与身体之间没有"life.state"事件契约，两器官各自内生（后端 EventBus 字符串事件 / 前端 RuntimeSnapshot 快照），无共享 schema。body 层用 `client.get('/api/life')` 主动探测再"未来升级"，这条升级位**没有任何后端实现或协议**，计划里两处"需进一步确认推送协议"自证未知。
- 事件类型：后端 EventType 枚举（events.py:18-50）很齐全，但**前端 `activity.*`（hub.py:287-311）与后端 `tool.*`/`thinking.*` 是两套并行事件**，无桥接。若"心广播→身体订阅"要成立，需要一个**统一事件映射层**（如 hub 的 `activity.tool_started` ↔ `tool.started`），计划未提供。
- 记忆召回：`SessionEventStore.recall`（session_db.py:410）走 FTS，`upsert_memory_candidate` 用 `sha256(f"{kind}:{content}")` 幂等（session_db.py:84），**幂等合并证据已在**——growth 的多端并发 tick 风险（risk 条目）其实已被缓解，计划对该点过度担心。但 prompt 注入链：`MemoryLayerBuilder`（prompt_builder.py:94）按 `category` 前缀过滤 + `_compute_brain_fingerprint`（:443-450）**用 `latest created_at` 与 fact/experience 计数做哈希**——**新增 me.memory/habit 块会自动进入 layer2 指纹，写入即重建，prompt_builder 的缓存策略会把"每次 observe 写入"变成"每次对话重建 layer2"**。growth 器官的注入改动（memory_builder 加块）比"只读观察"更侵入，未在风险中评估。
- 分寸决策在事件链中**根本不在链上**（见 P0-1）：boundary 挂在 registry.execute 的 middleware，而 confirm 决策在 agent 层。chain 的"裁决点"描述与实际调用栈不符。

## 是否违反"不重写现有模块"铁律（问题 2）

- core/life：新增 5 文件 + main.py 2 行——**合规**。但 main.py 的 2 行只覆盖挂载；`LifeService.stop()` 写 personality 到 `brain_data/life/`，该目录 git 忽略（.gitignore:28）无污染。OK。
- body：新增文件 + main.ts 挂载。main.ts 已核实 4 处 subscribe（:411-414）、onAudio/onTranscript（:319-325）真实存在，源扫描基线可行。**合规但依赖两个虚构对象**（AssetRegistry、`sprite2vrm` 空实现——后者可接受为占位）。`client.get('/api/life')` 若在 `backendClient` 未暴露 get 而只有 post（计划自己写 get/post）则需新增 bridge 代码。
- boundary：**计划自身改写动作最大**——step 5 要求 `core/runtime.py` 追加 1 行 register + 1 行 middleware.add（`_register_extension_tools` 列表确实存在，runtime.py:362-371，可追加）；main.py 新增路由。但**"新增 1 个只读路由"仍需 import/路由注册，是 main.py 实质改动**，且 scope 几乎等于重写三套权限逻辑，合规性最差。
- growth：**最违反铁律**。`HabitLearner.learn` 是 `EventMemoryConsolidator._consolidate_procedural` 的复制改写；`MeMemory.observe` 写 `brain.learn_fact(category="me.memory")` 会进 `_facts`（MAX_FACTS=100000 的 trimming，brain.py:364）并改 prompt 指纹；`prompt_builder.py` 加块 + `runtime.py` 加子系统 + `main.py` 加路由 = 3 处现有文件改动，而计划的 acceptance 只字未提这些（除 `MeMemory` 的注入）。红线 4/5（注入生效）本身就要改 prompt_builder，与"零改动"自相矛盾。
- **更优路径**：growth 直接调用**已有** `EventMemoryConsolidator`（kind=procedural）产候选，不加 habit 新 kind、不写 brain facts（MeMemory 用 `session_store` 或独立 json 而非 learn_fact）；proactive 只聚合既有 evolution/procedural active 候选。这样成长器官可以做到"纯新文件 + 只读现有 API"，与 core/life 的克制风格一致。**改 prompt_builder 前必须先用 `tests/test_p0_runtime.py` 验证 layer2 缓存/指纹不受破坏（其 330/338 测试已锁定 layer2 内容），否则一次回归门就红。**

## 性能/延迟/低配置降级（问题 3）

- 已证实的技术事实：EventBus 是**同步内联 handler**（events.py:109-118，每个 handler try/except），`tool_registry.execute` 的 `_publish`（:160）在 handler 里同步执行。core/life 的 `_on_event` 若做任何重活都会卡住工具执行——**计划对此有清醒认知**（"热路径无 IO 无 await"）且红线 10 覆盖降级，这点做得最好。
- 但 core/life 引入了一个**新的后台 daemon 心跳线程**（heartbeat_s），与 memory/controller.py:199-226 的既有后台线程模式一致；若 `LifeService.stop()` 不 join 会与 `Brain._flush`/exit flush（brain.py test 1725/1750 锁定）抢资源。计划已对齐该风险（atexit 不注册），OK。
- **低配置降级最弱在 body 层**：无 WebGL 时 sprite 档仍要写 DOM；`LifeDriver.start()` 若在 `document.getElementById('pet-surface-root')` 为空时抛错会破坏 main.ts 既有 boot——计划 try/catch 包裹正确，但红线只在 node 测试（fake DOM），**没有真实 WebView/桌面降级回归测试**（可用 `test_app_orb_only_shell` 模式补）。此外 `isLocalPreview` 无 onAudio 时 `lipSupported=false` 自动静音——需要确认 `VoiceCaptureOptions` 在预览路径是否真的不触发 onAudio（未验证）。

## 红线测试可验证性（问题 4）

逐条判定（红线均可运行，但其中若干"恒真/空集"）：

- **core/life**：红线 1/2/3/4/5/6/7/9 全部可测且有效（import 无副作用、clamp、roundtrip、事件→状态闭环、节流注入时间、subsystem 挂载、工具契约）。红线 8（挂载）对齐 `test_p0_runtime.py:825` 模式可行。红线 10 降级、红线 11 不虚构、红线 12 回归门均有效。**但红线 6 与"状态随时间演化"在真实运行下的输入（P0-2）为空；红线 8 的 `LifeService.start` 会触发 main.py 挂载，但挂载是 additive 不会破坏现有 4 订阅。**
- **body**：红线"4 处 subscribe 源扫描"有效（main.ts 已核实）；lifeState/emotionMapper/capability/lifeDriver 纯函数测试可测。但"LipSync from onAudio"、"≤30ms 内投递"、`AssetRegistry.vrmAssets`、`client.get('/api/life')` 升级协议四项为**不可验证或虚构**。
- **boundary**：红线 1（deny 恒真）**在 middleware 层不可达**（agent 已先拦）；红线 2"双闸修复"对象不存在；红线 4 token 端到端需要前端 UI 契约（未给）；红线 6 全量 `pytest tests/` 是唯一可靠的红线。**核心验收（token 免 confirm）与现有 agent 确认链路冲突，未验证可接线。**
- **growth**：红线 1/2 可测，但**只验证了"假设 (task,tool) 分组存在"的算法，不验证真实事件携带 task**（P0-4 空集问题）。红线 7"复用既有 evolution 测试"通过但不能证明自动闭环存在（P0-5 无调用者）。红线 4/5 依赖 prompt_builder/skill_creator 改动，测试本身可写，但**未把"既有 `test_p0_runtime.py` 的 layer2 相关用例不回归"纳入**。

## 五器官之外的三个共性问题

1. **"活着的一刻"定义空洞化**：四器官都声称"基于真实事件派生"，但核心事件源（task 字段、agent_run.*、USER_PROMPT_SUBMIT、evolution.review 自动调度）要么不存在、要么无调用者。建议成立一个 **事件契约清单**（哪个事件、谁发布、payload 字段、在真实运行中是否实际出现），任何器官的红线必须先挂靠清单上的**存在事件**。
2. **生命周期依赖未标注**：`LifeService`/`GrowthService`/`LifeDriver` 各自 start/stop 的时序（在 runtime.close 时 stop？在 main 退出时？），以及 `register_subsystem` 在 create_runtime 内被 `start_cycles` 并发调度的关系未定义；`stop()` 的持久化/join 与 Brain._flush 冲突风险只有 core/life 提到。
3. **前端测试基建未确认**：body 层所有测试都写"node --experimental-strip-types"与"fake HTMLElement/dataset"——需先确认仓库 test runner 是否已配置此模式（`tests/` 下未见 `app/tests/` 的 node 测试基建），否则红线在 CI 里跑不起来。

## 汇总（改动面审计）

- core/life（心）：**唯一一个铁律合规、输入真实（tool.*）、降级完整**的器官——但输入只剩 tool.* 一项，且生命周期事件（agent_run/phase）需要补一个发布点。
- app/src/life（身体）：**结构最清晰**（纯函数→能力分级→驱动→渲染），但 30ms/1ms/fps 断言与 onAudio 解码两处需要重新定义输入源，且 main.ts 挂载必须验证不破坏现有 4 订阅与 `test_app_*` 构建类用例。
- boundary（分寸）：**建议大幅砍 scope 或废弃重来**——"双闸修复"是虚构目标，token/cognitive 与现有 agent 确认链路冲突，最有价值的是只读 `/api/boundary/status` 聚合。若铁律允许改 agent.py 确认点，再谈 token；否则删掉 step 3/4。
- growth（成长）：**要么砍掉 HabitLearner（直接用既有 EventMemoryConsolidator），要么先给 agent 补 task 字段**——二者至少一个必须做，否则整个器官产出空内容候选。prompt_builder 改动需以既有 layer2 测试为回归门。

关键文件（绝对路径）：`G:\Javis\core\tool_registry.py`（:90-172 TOOL_COMPLETED 无 task 字段）、`G:\Javis\core\agent.py`（:284-323 确认链路、:660-732 工具调用、:933-976 _after_learn 无 USER_PROMPT_SUBMIT）、`G:\Javis\core\conversation_hub.py`（:94-311 主流程走 activity.* 非 agent_run.*）、`G:\Javis\core\tool_guardrails.py`（:23-25, :203-219 pre_check 语义）、`G:\Javis\core\middleware.py`（:66-118 fail-closed 管线）、`G:\Javis\memory\consolidation.py`（:43-70 与 HabitLearner 重复）、`G:\Javis\core\agent_runs.py`（:167-500 事件发布但无生产消费）、`G:\Javis\core\hook_system.py`（:24 USER_PROMPT_SUBMIT 无发布方）、`G:\Javis\voice\realtime.py`（:379 chat_quick 死代码确认）、`G:\Javis\core\prompt_builder.py`（:360-375 layer2 缓存指纹受新块影响）、`G:\Javis\core\runtime.py`（:362-378 extension tools 列表可追加）、`G:\Javis\app\src\main.ts`（:411-414 4 订阅、:319-325 onTranscript/onAudio）。
