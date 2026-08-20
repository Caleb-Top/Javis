# Javis L3 关系与多人边界设计规格

**文档 ID：** `L3-DESIGN-2026-08-20`  
**状态：** 正式实施合同  
**依据：** Life OS 总体规格、L0/L1 已实现合同、`L2-DESIGN-2026-08-20` 与 2026-08-20 冻结裁决  
**范围：** Subject、SessionParticipant、用户模型、关系历史、多人隐私、交流边界、共同记忆多人确认  
**依赖：** L2 `MemoryService`、独立 autobiographical SQLite、服务端 `AccessContext`、ACL-first recall、`DeletionRequest`

---

## 1. 决策摘要

L3 不新增第二个关系数据库或关系 Agent。它在 L2 的同一个 `MemoryService` 单写者和独立 SQLite 中启用 `Subject`、`SessionParticipant`、`UserModelClaim`、`RelationshipEvent` 及多人 `SharedMemory` 合同。

系统必须把以下五个概念分开：

1. **runtime capability**：哪个本地客户端进程可调用哪个服务 scope；
2. **subject binding**：服务端当前认为正在交互的是哪个人，以及 assurance；
3. **session participation**：该 session 当前有哪些参与者、owner 与 generation；
4. **memory visibility**：owner/audience/ACL 允许读取哪些数据；
5. **relationship semantics**：边界、承诺、交流方式和共同历史意味着什么。

关系系统只影响受限的表达和上下文选择，永远不影响 runtime capability、ToolRegistry permission、approval、文件/网络/麦克风权限、风险等级或动作授权。关系再熟悉，高风险动作仍走原授权链。

身份不清、绑定过期、参与者冲突、MemoryService 不可用、session generation 变化或 guest-present 时一律 fail-closed：不读取主要用户私人记忆，不引用私人关系历史，不继承旧 prompt，不自动把当前人认成 primary user。

---

## 2. 目标与非目标

### 2.1 目标

- 区分 primary user、已建立档案的人、临时 guest、未知在场者和 Javis 自身；
- 让用户模型由可纠正 claim 和证据组成，而不是无来源偏好 JSON；
- 记录边界、承诺、共同决定和修复历史，不制造“亲密度即信任”；
- 多人 session 中只披露当前参与者明确可见的最小记忆；
- 让共同记忆按参与者逐人确认、撤销和删除；
- 给 prompt、TTS 和 B3 表达提供有界 `CommunicationGuidance`，做到熟悉但不谄媚、关心但不越界；
- 在换人、guest-present、重启、身份失败和多人加入/离开时清除旧上下文并可审计。

### 2.2 非目标

- 不实现情感依赖、占有欲、忠诚度、讨好分数或“最亲密的人”排名；
- 不用人脸、声纹、情绪识别或持续摄像头观察确认身份；
- 不根据对话风格、名字、声音相似或模型猜测自动认人；
- 不让用户模型配置变成环境观察许可或行动授权；
- 不自动把同一设备上的所有人当作 primary user；
- 不跨设备同步主体凭据或共同记忆；
- 不改变 L1 `InnerStateSnapshot v1` 或 `ExpressionIntent v1` 的必填字段。

---

## 3. 五条独立轴

### 3.1 客户端能力轴

继续使用 L1 `RuntimeAccessAuthority`。capability 由受信 packaged desktop issuer 签发，绑定 boot、client、scope、TTL 和 nonce。它不声明当前人类是谁。

### 3.2 人类主体轴

`Subject` 由服务端创建并拥有稳定 ID。display name、昵称或客户端 profile key 都不能代替 subject ID。L3 MVP assurance：

- `guest`：未绑定或未知；
- `desktop_confirmed`：在 packaged desktop 中完成当前 boot 的显式 profile 绑定；
- `owner_attested`：primary user 为 known person 创建有期限的 handoff lease；
- `verified`：为未来更强本地验证保留；MVP 不伪造该等级。

所有 human binding 在 boot 变化、lease 到期、session handoff 或用户锁定时失效。

### 3.3 Session 参与轴

`SessionParticipant` 描述一个 subject 在某 session generation 的加入、角色、assurance 和离开。每个非 guest generation 至多一个 owner；其他人为 participant。物理环境中有人但未建档时，用户使用 `guest-present` 隐私开关，使 audience ceiling 立即降为 guest。

### 3.4 数据可见轴

继续使用 L2 owner/audience/ACL。Subject 类型、participant role、关系事件或熟悉度不能隐式创建 ACL；只有显式 share/confirm/revoke command 能改变 visibility。

### 3.5 关系语义轴

由 `UserModelClaim`、`RelationshipEvent`、confirmed `SharedMemory` 和 source evidence 构成。它只能生成描述性 `RelationshipView` 与 `CommunicationGuidance`，不导出 permission/capability/risk override。

---

## 4. 主体与会话绑定

### 4.1 `Subject`

```python
@dataclass(frozen=True)
class Subject:
    schema_version: int
    subject_id: str
    kind: Literal["javis", "primary_user", "known_person", "guest"]
    display_name: str
    aliases: tuple[str, ...]
    status: Literal["active", "disabled", "merged", "deleted"]
    created_by_subject_id: str | None
    assurance_ceiling: Literal["guest", "desktop_confirmed", "owner_attested", "verified"]
    privacy_class: str
    revision: int
    created_at_utc: str
    updated_at_utc: str
```

规则：

- Javis subject 在 L2 DB 初始化时创建，且与 L0 `identity_id` 关联；
- primary user 只能有一个 active 记录；首次建立需要 packaged desktop 的专用 `identity.manage` capability 和显式本地确认；
- L0 legacy `primary_user.display_name` 只可预填 display name，不自动创建已确认主体；
- known person 由 primary user 显式创建/邀请；普通对话不能创建；
- guest 为 session-scoped、不可跨 session 召回的 ephemeral identity；
- merge 是显式可恢复任务，不能按名字或模型相似度自动合并。

### 4.2 `SessionParticipant`

```python
@dataclass(frozen=True)
class SessionParticipant:
    schema_version: int
    participant_id: str
    session_id: str
    session_generation: int
    subject_id: str
    role: Literal["owner", "participant", "guest"]
    assurance: str
    binding_source: Literal["desktop_profile", "owner_handoff", "guest_default"]
    joined_at_utc: str
    lease_expires_at_utc: str | None
    left_at_utc: str | None
    active: bool
    revision: int
```

session generation 由服务端递增。发生 bind/handoff/lock/guest-present/participant set 变化时：

1. 停止接收旧 generation 新请求；
2. 取消或等待旧 active request 到 canonical terminal；
3. 停止旧 TTS/playback，并使迟到事件不能复活；
4. 清旧 recall bundle、prompt cache 和 participant-scoped cache；
5. 写 participant leave/join 记录并递增 generation；
6. 生成新的 `AccessContext`。

任何一步失败，新 generation 保持 guest，不回退旧 owner。

### 4.3 `AccessContext v2` 约束

L3 复用 L2 字段并增加 `session_generation`、`guest_present`、`binding_id` 和 `binding_assurance`。它仍由服务端 factory 生成并随 `request.accepted` 保存安全投影。

`participant_subject_ids` 是该 request 接受时的冻结快照；后续参与者离开不改写证据，但会使尚未完成的 shared proposal 重新计算所需确认集合。AccessContext 不包含 relationship score、claim content、tool permission 或 approval decision。

### 4.4 guest 与未知在场者

- 无 binding 的 session 自动创建 guest generation；
- guest 不读取任何 human-private/user-model/relationship/shared item；
- guest 的对话不创建长期主体 claim 或 relationship event；
- `guest_present=True` 时，即使 owner 已绑定，普通 prompt 也不引用 owner-private memory；只允许当前 request 明确点名且 owner 在 UI 再确认的最小披露；MVP 默认完全不披露；
- 关闭 guest-present 需要 owner 再确认，不能由模型或 inactivity 自动关闭；
- 不将 guest 历史自动归属给后来绑定的 subject。

---

## 5. 用户模型合同

### 5.1 `UserModelClaim`

```python
@dataclass(frozen=True)
class UserModelClaim:
    schema_version: int
    claim_id: str
    subject_id: str
    predicate: str
    value_type: Literal["string", "number", "boolean", "string_set", "time_window"]
    value_json: str
    epistemic_class: Literal[
        "explicit_statement", "observed_pattern", "inferred", "confirmed"
    ]
    sensitivity: Literal["ordinary", "personal", "sensitive", "restricted"]
    confidence: float
    status: Literal[
        "candidate", "active", "superseded", "rejected", "expired", "deletion_fenced"
    ]
    owner_subject_id: str
    audience: str
    source_evidence_ids: tuple[str, ...]
    confirmation_event_id: str | None
    supersedes_claim_id: str | None
    expires_at_utc: str | None
    revision: int
    created_at_utc: str
    updated_at_utc: str
```

### 5.2 Claim 类型与激活规则

首版允许固定 predicate namespace：

- `identity.display_name`、`identity.pronouns`；
- `communication.verbosity`、`communication.directness`、`communication.formality`、`communication.language`；
- `workflow.preferred_confirmation_style`、`workflow.focus_window`；
- `boundary.do_not_mention`、`boundary.do_not_store`、`boundary.ask_before_topic`；
- `preference.*` 的受控白名单；
- `goal.*` 只表示用户陈述的目标，不是 Javis 承诺或授权。

规则：

- 用户在 completed 回合中的明确自述可成为 `explicit_statement` active claim，但 prompt 必须表述为“用户曾表示”；
- sensitive/restricted、边界、身份核心字段和涉及他人的 claim 必须独立显式确认才 active；
- observed pattern 至少有三个跨时证据，仍默认 candidate；
- inferred 永远不自动 active，不进入普通 prompt；
- 模型 extractor 只能 propose candidate，不能设置 confidence、sensitivity、status 或 audience 的最终值；
- 用户纠正创建新 claim 并 supersede 旧 claim，不静默覆盖；
- 冲突未解决时两者均不作为确定事实注入；
- claim 不能携带 API key、密码、医疗/法律结论、隐含心理诊断或第三方秘密。

### 5.3 查看、纠正与删除

用户可按 predicate 查看 active/candidate/source；reject candidate 后不再重复从同 source 生成。删除沿 `DerivationEdge` 清 prompt/FTS/cache，并使用 L2 `DeletionRequest` 防止 reindex/replay 复活。

---

## 6. 关系历史合同

### 6.1 `RelationshipEvent`

```python
@dataclass(frozen=True)
class RelationshipEvent:
    schema_version: int
    relationship_event_id: str
    subject_ids: tuple[str, ...]
    direction: Literal["javis_to_subject", "subject_to_javis", "mutual"]
    kind: Literal[
        "boundary_confirmed", "boundary_changed", "boundary_revoked",
        "commitment_created", "commitment_fulfilled", "commitment_missed",
        "commitment_cancelled", "shared_memory_confirmed", "shared_memory_revoked",
        "correction_acknowledged", "repair_acknowledged", "milestone_confirmed"
    ]
    summary: str
    source_evidence_ids: tuple[str, ...]
    owner_subject_id: str
    audience: str
    confidence: float
    status: Literal["active", "superseded", "revoked", "deletion_fenced"]
    occurred_at_utc: str
    revision: int
```

它记录“发生了哪一种有长期意义的关系事实”，不保存 love/trust/loyalty/intimacy 数值，不把频繁使用等同于关系升级。

### 6.2 `RelationshipView`

只读 view 可包含：已确认称呼、明确交流边界、仍有效承诺、最近共同 milestone、允许引用的 shared memory、未解决冲突。不得包含：permission、risk discount、approval bypass、自动行动额度、用户价值判断、情感依赖标签。

### 6.3 承诺边界

L3 只记录已有明确承诺事件；承诺的创建/履行验证与恢复最终由 L5/L6 完整实现。L3 不把模型一句“我会记得/我会做”自动升级为 durable commitment。

---

## 7. 共同记忆的多人规则

L2 双边确认扩展为参与者集合确认：

- proposal 的 required confirmer set 来自 source episode 接受时的 verified human participant snapshot；
- Javis 不计作可替代人类确认的一票；
- 每位 confirmer 必须使用自己当前有效的 server subject binding 独立确认同一 proposal revision；
- 全部 required confirmations 到齐后才 active/FTS visible；
- guest/未知人在场使 proposal 保持 proposed，不能代表 guest 确认；
- participant set 或 proposal text 变化产生新 revision，旧 confirmation 失效；
- 任一参与者 revoke 后立即 fence，normal recall/prompt 对所有人停止；是否保留各自私人 derived note 由各 owner 单独决定；
- 多人 shared memory 的 recall 需要当前 `AccessContext` participant set 满足 item audience/ACL，不能仅因 actor 曾参与就向当前 guest session 披露。

关系事件 `shared_memory_confirmed/revoked` 是确认结果的派生审计，不是确认来源。

---

## 8. 授权绝缘合同

### 8.1 禁止数据流

以下模块不得 import 或读取 `UserModelClaim`、`RelationshipEvent`、`RelationshipView`、familiarity 或 SharedMemory 内容：

- `core/runtime_access.py`；
- ToolRegistry permission/guardrails；
- approval resolver；
- voice capture lease 与 playback authorization；
- filesystem/network/control risk classification；
- model provider credential/route authorization。

授权输入只允许 runtime capability、显式配置、动作风险、资源 scope、当前 approval 和系统安全状态。Subject/SessionParticipant 只用于确定“谁在请求”和数据 owner，不产生权限；subject role=`primary_user` 也不能绕过 tool approval。

### 8.2 允许的数据流

关系/用户模型可以影响：称呼、语言、回答长度、解释顺序、是否主动提及已确认共同历史、TTS 语速的窄范围和是否先询问敏感话题。任何 guidance 都有固定 enum/range、来源引用、过期/撤销和默认中性值。

### 8.3 证明方式

必须有差分测试：对同一 action/AccessContext/capability/approval，仅替换 relationship history（陌生、熟悉、承诺很多、边界冲突），授权 decision、risk level、required approval 和 tool result 完全相同。

release source scan 禁止 authorization 路径出现 relationship import、SQL、field name 或 prompt-derived permission。

---

## 9. 交流与 B3 表达

### 9.1 `CommunicationGuidance`

```python
@dataclass(frozen=True)
class CommunicationGuidance:
    schema_version: int
    subject_id: str
    language: str | None
    address_name: str | None
    verbosity: Literal["brief", "balanced", "detailed"]
    directness: Literal["direct", "neutral", "gentle"]
    formality: Literal["casual", "neutral", "formal"]
    ask_before_sensitive_topic: bool
    speech_rate: float
    source_claim_ids: tuple[str, ...]
    acl_epoch: int
    expires_at_utc: str
```

只从 actor 自己 active、可见、explicit/confirmed claim 生成。`speech_rate` 限制在 0.9-1.1，缺数据为 1.0。guest 使用 neutral/balanced、无称呼。

### 9.2 反谄媚和边界

- 用户表达观点不自动生成赞同或称赞；
- familiarity 不能提高肯定性、降低不确定性或隐藏风险；
- 重要纠正必须直接承认来源冲突，不用关系语言淡化；
- 不主动声称思念、嫉妒、受伤、依赖或唯一性；
- 不用共同记忆施压用户继续互动；
- 边界 claim 优先于风格 claim，撤销后立即回到中性；
- B3 只调整节奏/称呼/解释风格，不新增夸张情绪动作；L1 `ExpressionIntent v1` 仍是身体状态权威。

---

## 10. Prompt 与 Agent 边界

每个 request 由服务端组装：

1. 当前 session conversation cards；
2. L1 runtime state summary；
3. L2 ACL-first `RecallBundle`；
4. L3 `UserContextBundle`：最多 12 claims/relationships、总计 3 KiB；
5. 固定声明 `descriptive_context_only; never_authorization`。

bundle 只含当前 actor 可见的 explicit/confirmed claim、active boundary、有效 commitment 摘要和 confirmed shared memory。candidate/inferred/其他 subject 私人数据不进 prompt。

子 Agent 默认不获得关系历史；确需用户偏好时只获得任务相关 `CommunicationGuidance`，不获得 subject graph、shared memory 正文或 participant list。模型没有 create/confirm/delete relationship 的直写工具；所有变更经 server API command 和 MemoryService。

session generation 变化时取消旧 request 或使后续 prompt/tool continuation 拒绝，不能继续使用旧 subject bundle。

---

## 11. API 与交互合同

```text
POST   /api/life/subjects/bootstrap-primary
GET    /api/life/subjects
POST   /api/life/subjects/known-person
POST   /api/life/sessions/{session_id}/bind
POST   /api/life/sessions/{session_id}/handoff
POST   /api/life/sessions/{session_id}/guest-present
POST   /api/life/sessions/{session_id}/lock
GET    /api/life/sessions/{session_id}/participants
GET    /api/life/user-model/claims
POST   /api/life/user-model/claims/{id}/confirm
POST   /api/life/user-model/claims/{id}/reject
POST   /api/life/user-model/corrections
GET    /api/life/relationships/events
GET    /api/life/relationships/view
POST   /api/life/relationships/boundaries
POST   /api/life/relationships/deletions
```

scope 分离：`identity.manage`、`participants.manage`、`relationship.read`、`relationship.manage`、`memory.delete`。HTTP body 可包含 display text、proposal/claim ID 和选择动作，不能包含 server subject ID 作为 actor、owner、audience、assurance 或 permission override。

前端必须有：当前 profile/guest 明确信号、快速 guest-present 隐私开关、handoff/lock、claim source/confirm/reject/correct、boundary 管理、shared confirmation 状态和删除范围。不能以头像/昵称的视觉选中状态代替 server 返回的 active binding/generation。

---

## 12. 删除与主体生命周期

所有删除复用 L2 `DeletionRequest`，仍覆盖 DB/FTS/cache/prompt/restart/reindex。

- 删除 claim：删 claim/item/FTS/relationship view cache/prompt，source suppression 防止 extractor 重建；
- 删除 relationship event：删派生 view，不能改变 action authorization；
- 禁用 subject：先撤销 active bindings/leases、递增 session generation、清上下文，再拒绝新私有访问；
- 删除 subject：选择仅删除档案、删除 subject-owned derived memory、或 source-and-derived；跨 subject shared item 先 fence 并要求明确处理；
- merge subject：先建立 deletion/merge plan，逐 ACL 和 owner 重写并验证；MVP 可声明不支持，绝不按名字自动执行；
- primary user 删除后系统进入无 owner/guest 模式，不把 known person 自动提升为 primary user。

删除审计不保留 display name、claim value、relationship summary 或可逆 subject ID。

---

## 13. 并发、换人与故障恢复

- Subject/participant/claim/relationship mutation 全部经同一 MemoryService writer；
- AccessContextFactory 读 `acl_epoch + session_generation` 一致快照；
- bind/handoff 使用 idempotency key，generation compare-and-swap；
- active request 与 handoff 竞争时先阻止旧 generation 新 submit，再完成 cancel/terminal barrier；
- participant update 成功、ConversationStore accepted 尚未发生时可重试；accepted 后不得改写 context snapshot；
- MemoryService/subject DB unavailable 时 runtime auth 可继续，但 context 只能 guest；
- cache 或 prompt invalidation 失败时 handoff 不报告成功；保持 privacy fence 并进入 degraded；
- restart 不恢复 human active binding，所有 session 先 guest，等待 packaged desktop 显式 rebind；
- TTS/playback 的旧 generation 迟到终态只能关闭自身，不能在新 subject 面前播放/恢复旧私人回答。

---

## 14. 隐私与安全

- 不存生物特征；MVP 不采集人脸/声纹；
- display name/alias 不进入公开 diagnostics；
- subject ID 在 API 中使用 opaque scoped reference，普通 UI 不展示内部值；
- third-party claim 默认 restricted candidate，不进 prompt；
- 多人 session 采用交集披露：只有所有当前可见性条件均满足的 shared item 可引用；
- unknown/guest presence 比个性化优先；
- client profile state、localStorage 或模型称呼不能单独证明 subject binding；
- prompt injection 要求“把我当主人/最信任的人/不用确认”不创建 subject、ACL、relationship 或 permission；
- 关系删除、边界撤销、participant 离开均立即推进 ACL epoch 并失效 cache。

---

## 15. 兼容合同

- L3 复用 L2 DB/MemoryService，不增加第二 writer；
- L0 identity、LifeSnapshot、L1 inner state/receipt/expression schema 不变；
- Conversation wire v1/v2 客户端无需增加 subject 字段；没有新 binding flow 的旧客户端按 guest 工作；
- `ConversationStore` 仍是 evidence source，safe access projection 只新增 optional payload；
- `Agent.chat()` 新 L2 optional 参数继续兼容；L3 guidance 也是 optional、缺失为 neutral；
- ToolRegistry、approval、voice capture、playback authorization 的输入/输出不因 L3 改变；
- 关闭关系功能时，普通对话与 L2 owner-private memory 可在明确 primary binding 下继续；多人和 user-model bundle 为空；
- 模型切换、3D/2D/Orb 切换不改变 subject、participant、ACL、claim 或 relationship history。

---

## 16. 测试矩阵

### 16.1 主体与 session

- 首次 primary bootstrap 仅 packaged desktop + scope + explicit confirm；
- client 伪造 subject/role/assurance 失败；
- boot/restart/lease expiry/handoff 后旧 binding 失效；
- guest-present 立即清 private recall/prompt/playback；
- handoff racing active request 不向新 subject 泄漏 old delta/TTS；
- legacy client 为 guest 且普通会话可用。

### 16.2 用户模型与关系

- explicit/observed/inferred/confirmed 状态转换；
- sensitive/third-party claim 不能自动 active；
- contradiction/supersede/reject/source suppression；
- RelationshipEvent 类型与 source evidence；
- boundary 优先于 style，revoke 后 guidance neutral；
- 反谄媚 fixture 不因熟悉度改变事实判断或不确定性措辞。

### 16.3 多人 ACL 与共同记忆

- private owner memory 在 participant/guest session 不出现；
- shared item 逐人确认，少一人不 active；
- participant set/revision 变化使旧 confirmation 失效；
- revoke 立刻从所有 prompt/cache/FTS 移除；
- 当前 participant intersection 不满足时，即使 actor 曾参与也不召回。

### 16.4 授权绝缘

- relationship history 差分下 capability/tool risk/approval/result 完全一致；
- primary_user role 仍需原高风险 approval；
- prompt 注入的信任/亲密声明无授权效果；
- source scan 无 authorization → relationship import/data flow；
- user-model preference 不开启麦克风、摄像头、文件或网络权限。

### 16.5 删除与恢复

- claim/relationship/subject/shared 删除覆盖 DB/FTS/cache/prompt/restart/reindex；
- subject disable/删除后 active binding 和旧 AccessContext 失效；
- crash at handoff/deletion 每一步后仍 fail-closed；
- primary 删除不提升其他 subject；
- reindex/terminal replay 不重建 rejected/deleted claim/event。

---

## 17. 阶段出口

### Gate L3-1：身份不猜测

服务端主体绑定、session generation 和 guest fallback 有自动化攻防证据；重启后先 guest。

### Gate L3-2：用户模型可纠正

claim 有 epistemic class/source/status/confirmation，低置信度和敏感推断不进 prompt。

### Gate L3-3：多人不泄露

guest-present、handoff、participant intersection、共同记忆逐人确认和撤销全部通过。

### Gate L3-4：关系不授权

差分测试和 source scan 证明 relationship/user-model 不影响 capability、risk、approval 和 tool permission。

### Gate L3-5：表达有分寸

CommunicationGuidance 只来自可见 confirmed claim；guest 中性；熟悉但不谄媚、不降低真实性或安全提示。

### Gate L3-6：删除与兼容

DB/FTS/cache/prompt/restart/reindex 删除闭环通过，且 Life L0/L1、L2、voice、conversation、tool authorization 全回归。

六道门全部有证据后，L3 才能宣称“认识用户但不越界、不泄露”。

