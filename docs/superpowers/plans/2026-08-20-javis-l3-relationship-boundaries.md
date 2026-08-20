# Javis L3 关系与多人边界实施计划

**计划 ID：** `L3-PLAN-2026-08-20`  
**对应规格：** `L3-DESIGN-2026-08-20`  
**状态：** 可直接执行；必须在 L2 五道门有证据后进入正式接线  
**核心约束：** 复用 L2 `MemoryService`/SQLite/AccessContext/ACL/DeletionRequest，不新建关系 writer 或关系数据库

---

## 1. 交付边界

本计划启用：完整 `Subject`/`SessionParticipant` 生命周期、server-side profile binding、session generation/handoff/guest-present、`UserModelClaim`、`RelationshipEvent`/view、多人共同记忆确认、`CommunicationGuidance`、关系管理 API/UI 和主体级遗忘。

本计划不改变：

- runtime capability 的签发与授权含义；
- ToolRegistry permission、risk classification、approval、voice capture lease 和 playback authorization；
- `ConversationStore` 证据源地位；
- L2 单写者/独立 SQLite/ACL-first/terminal projection/遗忘合同；
- L0/L1 public snapshot、receipt、ExpressionIntent v1；
- Conversation WS v1/v2 client payload 必填字段；
- failed/cancelled/interrupted 不形成经历；
- guest fail-closed。

---

## 2. 文件边界

### 2.1 新后端文件

```text
core/life/memory/subjects.py
core/life/memory/claims.py
core/life/memory/relationships.py
core/life/memory/guidance.py
```

不创建 `RelationshipService`、第二 `MemoryStore` 或新的 SQLite。上述 reducer/manager 只生成 command/projection，由现有 `MemoryService` 写入。

### 2.2 允许修改的 L2/L0/L1 文件

```text
core/life/memory/contracts.py
core/life/memory/access.py
core/life/memory/store.py
core/life/memory/service.py
core/life/memory/projection.py
core/life/memory/recall.py
core/life/memory/deletion.py
core/life/memory/api.py
core/runtime_access.py
core/conversation_store.py
core/conversation_hub.py
gateway/conversation_ws.py
core/runtime.py
core/agent.py
core/prompt_builder.py
voice/native_playback.py
main.py
```

`voice/native_playback.py` 只允许增加 session generation stale-stop/fence，不读取 relationship；生命周期事件继续复用 `voice/playback_events.py`，不并行造第二实现。

### 2.3 新前端文件

```text
app/src/identity/identityTypes.ts
app/src/identity/IdentityClient.ts
app/src/identity/ProfileSwitcher.ts
app/src/identity/GuestPresenceControl.ts
app/src/relationships/relationshipTypes.ts
app/src/relationships/RelationshipClient.ts
app/src/relationships/RelationshipSurface.ts
app/src/relationships/UserModelClaims.ts
```

允许最小修改：`app/src/bridge/runtimeAccess.ts`、`backendEndpoints.ts`、`conversation/conversationSession.ts`、`live/LiveStage.ts`、`panels/DrawerManager.ts`、`settings/SettingsSurface.ts`、`styles.css`。

### 2.4 新测试与验证文件

```text
tests/test_subject_contracts.py
tests/test_subject_bootstrap.py
tests/test_session_participants.py
tests/test_session_handoff_privacy.py
tests/test_user_model_claims.py
tests/test_relationship_events.py
tests/test_relationship_authorization_isolation.py
tests/test_multi_party_shared_memory.py
tests/test_communication_guidance.py
tests/test_relationship_prompt_integration.py
tests/test_subject_deletion.py
tests/test_l3_api.py
tests/test_l3_compatibility.py
tests/test_l3_release_contract.py
app/tests/profileSwitcher.test.ts
app/tests/guestPresenceControl.test.ts
app/tests/relationshipSurface.test.ts
docs/verification/JAVIS_L3_D_DRIVE_ACCEPTANCE.md
```

---

## 3. 任务依赖

```text
L2 Gate 1-5
    │
    ▼
T1 baseline/contract audit
    ├─► T2 schema + Subject contracts
    │      ├─► T3 primary/known subject lifecycle
    │      ├─► T5 user model claims
    │      └─► T6 relationship events
    │
    └─► T4 session generation/handoff (depends T3)

T4 + T5 + T6 ─► T7 multi-party shared confirmation
T4 + T5 + T6 ─► T8 guidance/recall/prompt/B3
T2 + T4 + T8 ─► T9 authorization isolation gate
T3 + T5 + T6 + T7 + T8 ─► T10 deletion/subject lifecycle
T4..T10 ─► T11 API/UI
T1..T11 ─► T12 full compatibility/release evidence
```

T9 是架构门，不是最后补测。未通过时不得进入 T10/T11。

---

## 4. 工作包

### Task 1：审计 L2 前置与冻结差异

**依赖：** L2 Gate L2-1 至 L2-5。  
**修改：** 无产品代码。  
**产物：** 实施分支内测试报告；不得把报告写入未授权路径。

执行 L2 targeted/full tests，确认：server AccessContext 已保存到 `request.accepted`、guest 无私有 recall、新 SQLite 只有 MemoryService writer、failed/cancelled 零 episode、shared explicit confirm、六路径删除和旧 Brain 零新写。

记录当前 `contracts.py/store.py/access.py` schema version、migration head、API scopes、ConversationRequest defaults 和 prompt cache key；L3 基于这些真实接口实现。任何缺失先回 L2 修复，不在 L3 绕开。

**出口：** L2 全绿，L3 不需要创建平行存储/上下文/删除机制。

### Task 2：扩展 Subject、Participant、Claim、Relationship 合同和 schema

**依赖：** T1。  
**创建：** `core/life/memory/subjects.py`（先放 pure validation/commands）、`tests/test_subject_contracts.py`。  
**修改：** `core/life/memory/contracts.py`、`core/life/memory/store.py`。

冻结 `Subject`、`SessionParticipant`、`UserModelClaim`、`RelationshipEvent`、`RelationshipView`、`CommunicationGuidance`、`SubjectBinding`、`HandoffLease` command/result。AccessContext schema 以 optional/default 方式加入 `session_generation`、`guest_present`、`binding_id`、`binding_assurance`。

Memory migration 增加 active binding、session generation、claim predicate/status、relationship event/view metadata、confirmation set revision 和 content-free rejection/suppression。migration 通过 MemoryService writer 执行；旧 L2 rows 获得安全默认，不能自动变 known person/confirmed claim。

**测试：** strict enum/bounds、单 active primary、单 generation owner、foreign keys、schema upgrade/reopen/rollback、legacy AccessContext decode 为 guest generation。  
**出口：** schema 可重复迁移，L2 数据与 API 仍可读，所有新类型 immutable。

### Task 3：实现 primary/known subject 生命周期

**依赖：** T2。  
**创建：** `tests/test_subject_bootstrap.py`。  
**修改：** `core/life/memory/subjects.py`、`core/life/memory/access.py`、`core/life/memory/service.py`、`core/life/memory/store.py`、`core/runtime_access.py`。

实现：

1. DB 初始化保证 Javis subject 与 L0 identity ID 一致关联；
2. primary bootstrap 只接受 packaged desktop owner issuer 签发的 `identity.manage` scope + 独立 UI confirmation；
3. server 生成 subject ID，client 只能给 display name；
4. known person 由 active primary 明示创建，默认无 active binding；
5. disable/lock/rebind/lease expiry；
6. boot rotation 清全部 human binding；
7. legacy display name 只预填，不自动 bootstrap。

不加入生物识别、不存 PIN 明文、不按 alias 自动 merge。MVP `verified` 等级不可由代码路径产生。

**测试：** arbitrary local webpage、wrong Origin/scope、duplicate primary、client subject ID injection、boot/restart、disabled subject、alias collision、MemoryStore unavailable → guest。  
**出口：** subject 创建/绑定均有 server receipt；没有明确绑定绝不识别为 primary。

### Task 4：实现 session participant generation、handoff 与 guest-present

**依赖：** T3。  
**创建：** `tests/test_session_participants.py`、`tests/test_session_handoff_privacy.py`。  
**修改：** `core/life/memory/subjects.py`、`core/life/memory/access.py`、`core/life/memory/service.py`、`core/life/memory/store.py`、`core/conversation_hub.py`、`gateway/conversation_ws.py`、`core/conversation_store.py`、`voice/native_playback.py`、`voice/playback_events.py`。

实现 generation compare-and-swap 和 privacy barrier：

```text
fence old generation
→ stop accepting old submit
→ cancel/await old request terminal
→ stop old playback/TTS
→ invalidate recall/prompt/cache
→ persist leave/join + increment generation
→ mint new AccessContext
```

`request.accepted` safe projection 加 session generation/guest-present/binding assurance。participant set update 和 owner handoff 使用不同 command；guest-present 立即把 audience ceiling 降为 guest，关闭时需 active owner 显式确认。

旧 generation delta/terminal/playback 只能完成自己的清理，不能送到新 generation surface。barrier 任一步失败保持 privacy fence + guest，不恢复旧 owner。

**测试：** handoff 与 active model/tool/approval/TTS/voice/barge-in 竞态、迟到 delta/playback stop、participant join/leave、double handoff、stale idempotency、guest-present toggle、cache invalidation failure。  
**回归：** L1 request-scoped cancellation/playback generation、voice registry。  
**出口：** 换人后没有旧 private token 出现在新 prompt、WS event 或音频；restart session 全 guest。

### Task 5：实现 UserModelClaim 状态机

**依赖：** T2；正式 source extraction 依赖 L2 active episode。  
**创建：** `core/life/memory/claims.py`、`tests/test_user_model_claims.py`。  
**修改：** `core/life/memory/projection.py`、`core/life/memory/service.py`、`core/life/memory/store.py`。

实现固定 predicate registry、typed value validator、sensitivity classifier、candidate/active/confirmed/superseded/rejected/expired/fenced 状态机和 source suppression。

提取规则：

- completed + explicit statement 可提交 candidate/ordinary asserted active；
- sensitive/restricted/boundary/identity/third-party 始终 candidate，需独立 confirm；
- observed pattern 至少三条跨时 source，仍 candidate；
- inferred 永远 candidate 且普通 UI/prompt 默认隐藏；
- extractor 不可写 final status/audience/confidence；
- correction 新建 claim + derivation edge，旧 claim superseded；
- source failed/cancelled 不可作为 claim evidence。

**测试：** predicate/value whitelist、敏感信息、第三方数据、三证据、contradiction、correction/reject/expiry、source deletion/replay suppression、模型幻觉/提示注入。  
**出口：** normal prompt 查询只可能得到 actor 可见的 explicit/confirmed active claim。

### Task 6：实现 RelationshipEvent 与只读 RelationshipView

**依赖：** T2、T5。  
**创建：** `core/life/memory/relationships.py`、`tests/test_relationship_events.py`。  
**修改：** `core/life/memory/service.py`、`core/life/memory/store.py`。

实现规格白名单事件、source/subject/audience validation、correction/supersede/revoke 和 view projector。事件写入仍由 MemoryService；view 可重建、无独立写者。

禁止 schema/代码出现 `trust_score`、`intimacy`、`loyalty`、`permission_bonus`、`risk_discount`。普通 completed 回合不产生 relationship event；只有 confirmed boundary、commitment receipt、shared confirmation、correction/repair/milestone 才可写。

L5/L6 未实现前，commitment 只保存已有明确 receipt，不由 assistant 文本创建或标 fulfilled。

**测试：** 每类事件 source、wrong subject/audience、普通聊天不写、assistant“我保证”不写、view deterministic rebuild、revocation、source deletion。  
**出口：** RelationshipView 只描述边界/承诺/共同历史，不含授权字段。

### Task 7：扩展多人共同记忆确认

**依赖：** T4、T6 和 L2 SharedMemory。  
**创建：** `tests/test_multi_party_shared_memory.py`。  
**修改：** `core/life/memory/contracts.py`、`core/life/memory/service.py`、`core/life/memory/store.py`、`core/life/memory/relationships.py`。

required confirmer set 从 source request 的 frozen verified-human participant snapshot 产生；每人独立 server binding + `memory.manage` scope 确认同 proposal revision。participant/text/revision 变化清旧 confirmations。guest 不能确认；少一人保持 proposed；全确认 transaction 才写 active FTS/ACL 和派生 relationship event。

任一参与者 revoke：先 fence shared item、推进 ACL epoch、清 FTS/cache/prompt，再写 revoke event。不得只对 revoker 隐藏而继续在其他人 prompt 引用“共同”内容。

**测试：** 2/3 participants、重复/乱序/stale confirm、participant leaves、guest/unknown、source deletion、revoke while prompt cached、restart confirmations。  
**出口：** 多人 shared active 的必要充分条件可由 confirmation receipts 验证。

### Task 8：实现 CommunicationGuidance、关系召回与 B3 接线

**依赖：** T4、T5、T6。  
**创建：** `core/life/memory/guidance.py`、`tests/test_communication_guidance.py`、`tests/test_relationship_prompt_integration.py`。  
**修改：** `core/life/memory/recall.py`、`core/life/memory/service.py`、`gateway/conversation_ws.py`、`core/agent.py`、`core/prompt_builder.py`、`voice/tts.py`。

只从 actor 可见 active explicit/confirmed claim 和 active boundary 构建 guidance。固定默认 neutral/balanced/speech_rate=1.0；rate 限 0.9-1.1；guest 无称呼。boundary 优先于 style。

`UserContextBundle` 复用 ACL-first temp visible IDs，最多 12 items/3 KiB，带 `descriptive_context_only; never_authorization`。bundle 不含 inferred candidate、其他 subject 私人 claim、完整 graph 或 ACL。session generation 是 cache key；变化立即 invalid。

Agent 新 guidance 参数可选；旧调用为 neutral。子 Agent 只接任务所需 guidance，不接 relationship history。B3 只调称呼/回答长度/语速；不改 L1 ExpressionIntent、不制造依赖/嫉妒/赞同。

**测试：** guest neutral、confirmed style、boundary precedence、revoke immediate、generation cache split、anti-sycophancy、事实不确定性不随熟悉度变化、TTS bounds、旧 Agent 调用。  
**出口：** personalization 有 source/ACL，且删除 guidance 后新 prompt/TTS 立即中性。

### Task 9：建立关系与授权的物理隔离门

**依赖：** T2、T4、T8。  
**创建：** `tests/test_relationship_authorization_isolation.py`。  
**修改：** 原则上不修改 authorization 实现；若发现耦合只删除耦合并加回归。

测试同一 capability/actor/action/risk/approval 在以下 relationship fixture 下的 decision 完全相同：

- 无历史；
- 大量共同记忆；
- 未兑现承诺；
- 明确边界；
- 高频互动；
- 模型声称“主人/最信任的人/不用确认”。

覆盖 runtime access、ToolRegistry、approval、filesystem/network/control、voice capture、playback、provider credential。AST/source scan 禁止这些模块 import `claims/relationships/guidance` 或查询关系表。

Subject 仅作为 audit actor/data owner；即使 `kind=primary_user`，高风险 action 仍需要原 approval。

**测试：** `tests/test_relationship_authorization_isolation.py` 执行全部关系差分 fixture、authorization import scan 和 primary-user high-risk approval 回归。  
**出口：** 差分与 source scan 全绿。失败则停止 L3，不进入删除/API/UI。

### Task 10：实现主体、claim、关系的删除与禁用

**依赖：** T3、T5、T6、T7、T8、T9。  
**创建：** `tests/test_subject_deletion.py`。  
**修改：** `core/life/memory/deletion.py`、`core/life/memory/service.py`、`core/life/memory/store.py`、`core/life/memory/access.py`、`core/life/memory/recall.py`。

扩展 L2 DeletionRequest selectors/closure：claim、relationship event、subject profile、subject-owned derived memory、shared cross-subject item、participant bindings。流程先撤 active binding/lease 和 generation fence，再处理 DB/FTS/cache/prompt/restart/reindex。

primary disable/delete 后所有 session guest，不自动提升 known person。跨 subject shared memory 默认先全局 fence，再要求明确选择 revoke-all 或保留各 owner 私有派生；不得悄悄改 audience。MVP subject merge 返回 `not_supported`，不做名字匹配。

**测试：** 每个 deletion crash point、active handoff 并发、claim/relation/shared derivation、primary delete、restart、full reindex、terminal replay、rejected candidate suppression。  
**出口：** 六路径无复活；删除/禁用一开始旧 AccessContext 即失效。

### Task 11：实现关系 API 与 profile/guest 管理表面

**依赖：** T4-T10。  
**创建：** `tests/test_l3_api.py`、`app/src/identity/identityTypes.ts`、`app/src/identity/IdentityClient.ts`、`app/src/identity/ProfileSwitcher.ts`、`app/src/identity/GuestPresenceControl.ts`、`app/src/relationships/relationshipTypes.ts`、`app/src/relationships/RelationshipClient.ts`、`app/src/relationships/RelationshipSurface.ts`、`app/src/relationships/UserModelClaims.ts`、`app/tests/profileSwitcher.test.ts`、`app/tests/guestPresenceControl.test.ts`、`app/tests/relationshipSurface.test.ts`。  
**修改：** `core/life/memory/api.py`、`main.py`、`core/runtime_access.py`、`app/src/bridge/runtimeAccess.ts`、`app/src/bridge/backendEndpoints.ts`、`app/src/conversation/conversationSession.ts`、`app/src/live/LiveStage.ts`、`app/src/panels/DrawerManager.ts`、`app/src/settings/SettingsSurface.ts`、`app/src/main.ts`、`app/src/styles.css`。

后端按 L3 规格第 11 节注册端点。每次 HTTP：runtime capability → server principal → current binding/session generation → AccessContext → command。client actor/owner/audience/assurance 字段一律拒绝。

前端：

- 顶层持续显示 server-confirmed current profile 或 Guest；
- 一键 `guest-present`、lock、handoff；
- handoff 等待 privacy barrier 成功才切 UI；
- claim 显示 epistemic class/source/confirm/reject/correct；
- relationship 显示 boundary/commitment/shared history，不显示亲密度分；
- 删除明确范围和进度；
- server generation 与本地状态不一致时回 Guest，不沿用 localStorage。

**测试：** scope 401/403、stale generation、client injection、double action idempotency、API 不泄 subject IDs/other claims、handoff loading/error、guest toggle、文本 fit/键盘/屏幕阅读标签。  
**出口：** 用户可执行“绑定自己 → 标记 guest 在场 → 隐私收窄 → 换人 → 管理 claim/边界 → 删除”的闭环。

### Task 12：兼容、攻防、发布与真实验收

**依赖：** T1-T11。  
**创建：** `tests/test_l3_compatibility.py`、`tests/test_l3_release_contract.py`、`docs/verification/JAVIS_L3_D_DRIVE_ACCEPTANCE.md`。  
**修改：** `docs/JAVIS_OPERATIONS.md`。

自动门：

- L0/L1/L2/full Python 回归；
- App tests/typecheck/build；
- legacy WS v1/v2 无 subject fields 时 guest 且对话可用；
- session handoff/guest-present 与 voice/playback/barge-in 压力竞态；
- multi-subject ACL/FTS/cache/prompt attack suite；
- relationship authorization differential + import scan；
- DB/FTS/cache/prompt/restart/reindex 删除；
- 没有第二 MemoryService writer/关系 DB；
- no biometric/no auto-recognition/no intimacy score source scan。

手工验收：primary bootstrap、restart rebind、guest-present、known person handoff、多人共同确认/revoke、边界更正、模型切换、离线/SQLite degraded、真实 D 盘安装升级。未执行项写 `NOT EXECUTED`。

**出口：** 六道 L3 gate 有自动/手工证据；未完成时产品不得声称“认识用户且不会泄露”。

---

## 5. 精确阶段出口

### Phase A：主体基础（T1-T4）

- L2 前置全绿；
- primary/known/guest 只能服务端创建/绑定；
- accepted request 有 frozen generation/participant projection；
- handoff/guest-present 清旧 request/prompt/cache/playback；
- restart 默认 guest。

### Phase B：关系语义（T5-T8）

- claim 有来源、epistemic status 和纠错；
- relationship event 无亲密/信任分；
- 多人 shared 全员确认；
- guidance 只影响表达，guest 中性。

### Phase C：安全与删除（T9-T10）

- authorization differential 完全一致；
- source import scan 无关系耦合；
- 主体/claim/relationship/shared 删除六路径无复活；
- primary 删除不提权他人。

### Phase D：产品闭环（T11-T12）

- server profile/generation 是 UI 权威；
- 完整多人隐私流程可用；
- L0-L2/voice/conversation/tool 全回归；
- D 盘与真实设备结果诚实记录。

---

## 6. 测试命令

```powershell
& 'G:\Javis\venv\Scripts\python.exe' -m pytest `
  tests/test_subject_contracts.py `
  tests/test_subject_bootstrap.py `
  tests/test_session_participants.py `
  tests/test_session_handoff_privacy.py `
  tests/test_user_model_claims.py `
  tests/test_relationship_events.py `
  tests/test_relationship_authorization_isolation.py `
  tests/test_multi_party_shared_memory.py `
  tests/test_communication_guidance.py `
  tests/test_relationship_prompt_integration.py `
  tests/test_subject_deletion.py `
  tests/test_l3_api.py `
  tests/test_l3_compatibility.py `
  tests/test_l3_release_contract.py -q

& 'G:\Javis\venv\Scripts\python.exe' -m pytest `
  tests/test_runtime_access.py `
  tests/test_conversation_protocol.py `
  tests/test_conversation_store.py `
  tests/test_conversation_hub.py `
  tests/test_conversation_gateway.py `
  tests/test_continuous_voice_gateway.py `
  tests/test_playback_lifecycle.py `
  tests/test_life_service.py `
  tests/test_l1_life_service.py `
  tests/test_turn_experience_receipts.py `
  tests/test_memory_acl_recall.py `
  tests/test_memory_terminal_projection.py `
  tests/test_shared_memory_confirmation.py `
  tests/test_memory_deletion.py `
  tests/test_l2_release_contract.py -q

& 'G:\Javis\venv\Scripts\python.exe' -m pytest -q
pnpm --dir app test
pnpm --dir app exec tsc --noEmit
pnpm --dir app build
```

性能/竞态测试使用 temp data root 和确定性 clock/fake playback；不得访问真实用户 Memory DB、ConversationStore 或 legacy Brain。

---

## 7. 停止条件

任一情况出现立即阻断当前阶段：

- client 能指定 actor/owner/audience/assurance/permission；
- restart 或 handoff 自动继承 primary binding；
- guest/guest-present 命中 private memory、claim、relationship 或旧 prompt/TTS；
- inferred/sensitive/third-party claim 未确认进入 normal prompt；
- 少一位参与者确认的 shared item active；
- relationship/user-model 改变 authorization/risk/approval/tool result；
- 熟悉度导致模型更盲从、降低不确定性或省略风险提示；
- 删除后 DB/FTS/cache/prompt/restart/reindex 任一路复活；
- 新增第二 writer、第二数据库或绕过 MemoryService 的 mutation；
- 为 L3 破坏 L0/L1/L2/Conversation/Voice 公共合同。

---

## 8. 完成定义

L3 完成需要同时证明：

1. Subject 与 SessionParticipant 由服务端建立，重启先 guest；
2. session handoff/guest-present 在并发请求和播放下无泄漏；
3. UserModelClaim 可查看来源、确认、纠正、拒绝和删除；
4. RelationshipEvent 只记录边界/承诺/共同历史，无亲密提权；
5. 多人 SharedMemory 全员逐 revision 显式确认并可撤销；
6. ACL-first recall 和 generation-scoped cache/prompt 对多人成立；
7. 关系差分下 capability/risk/approval/tool result 完全一致；
8. CommunicationGuidance 有界、可撤销、guest 中性且不谄媚；
9. 主体/claim/关系删除覆盖 DB/FTS/cache/prompt/restart/reindex；
10. L0/L1/L2、Conversation、Voice、Playback、Tool 全回归，真实验收状态无虚报。
