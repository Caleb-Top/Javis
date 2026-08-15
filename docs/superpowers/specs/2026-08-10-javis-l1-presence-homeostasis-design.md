# Javis L1 临场感、注意与内稳态设计规格

**版本：** L1-DESIGN-2026-08-10  
**状态：** 用户已确认，可进入实施计划与隔离开发  
**上游：** `2026-08-09-javis-life-os-master-design.md`、`2026-08-09-javis-l0a-life-kernel-design.md`、`2026-08-09-javis-l0b-base-avatar-design.md`  
**实施边界：** `G:\Javis-worktrees` 隔离开发与验收，形成提交后合并进 `G:\Javis`；D 盘只做真实用户安装验收

---

## 1. 决策摘要

L1 不增加一个会扮演情绪的 Agent，也不让大模型凭一句话决定 Javis “现在是什么心情”。L1 在 L0-A 的生命事件、身份连续性和单一 `LifeService` 之上，建立一个确定性、可衰减、可解释、可回放的内在状态层：

1. 真实运行事件先被最小化为 `LifeObservation`；
2. 纯规则 `AppraisalReducer` 计算事件对注意、负荷、确定性、谨慎、受阻等维度的影响；
3. `HomeostasisReducer` 以有界值和半衰期维持状态，不保存永久 happy/sad 标签；
4. `AttentionCoordinator` 决定此刻优先关注什么，并在过期或打断后释放；
5. `PresenceResponder` 只处理“精确呼名”这一条本地确定性路径；
6. `TurnExperienceProjector` 保存不含正文的薄回合收据，为重启连续性和未来 L2 经历系统提供可核验证据；
7. 大模型只读取经过压缩的内在状态摘要，不能写入、调参或宣布状态；
8. 身体只消费 `ExpressionIntent v1`，不直接读取内部证据或自行猜情绪。

L1 的第一个“活着时刻”定义为：用户准确呼唤 Javis 时，系统无需模型、云 API 或本地模型即可在同一会话中回答“我在”，同时注意和身体进入短暂、克制、可打断的在场状态。

---

## 2. 目标

L1 要解决七个基础问题：

1. Javis 能区分安静、被呼唤、倾听、思考、说话、等待批准、受阻和恢复；
2. 当前状态来自真实事件且能解释“为什么”；
3. 状态会自然回落，不因进程长期运行而不断累积；
4. 重启保留身份和必要经历收据，但不复活已过期的思考、说话或受阻状态；
5. Live、Code、Pet 读取同一个生命实例和同一状态修订号；
6. 本地或云模型切换不改变 Javis 的身份与状态所有权；
7. 语音呼名、打断和离线回应形成真实闭环，而非前端动画假装响应。

---

## 3. 非目标

L1 明确不实现：

- 完整自传记忆、日记、共同回忆与夜间整理；
- 用户关系熟悉度、亲密度或多人身份推断；
- Big Five、MBTI、人格数值或人格自动成长；
- 可由模型调用的 `life_tune`、`set_emotion`、`set_attention` 类工具；
- 主动建议、长期意图、承诺管理或 Computer Twin；
- 摄像头、人脸、声纹等生物识别；
- 用动画、随机数或提示词制造不存在的心情；
- 影视级表情、音素级口型或身体人格化成长；
- 将工具成功等同于用户目标已经完成；
- 声称 Javis 已具有意识、感受或主观体验。

---

## 4. 当前源码事实与约束

### 4.1 会话主路径

生产对话入口为：

```text
前端 BackendClient / 语音转写
→ gateway/conversation_ws.py::ConversationWebSocketGateway
→ core/conversation_hub.py::ConversationHub
→ runtime.agent.chat(...)
→ ConversationHub 统一发布 activity / response / approval / terminal 事件
```

`ConversationHub` 已负责会话序列、幂等、取消、批准、历史落盘和 `AgentRunRecorder`。因此精确呼名响应必须仍经 `ConversationHub.submit()` 的 runner 产生 `text_delta` 与 `done`，不能直接向 WebSocket 私发一段文字。

当前 `ConversationHub._publish()` 写入的 canonical conversation event 拥有自己的 `event_id` 与 session sequence。L0-A 的生产桥必须在该事件成功持久化后，把这两个原始值以及 `session_id`、`request_id`、`interaction_mode` 带入生命 observation；`request_id` 作为默认 correlation。不得只引用桥接后新生成的 EventBus ID，否则 L1 收据不能回指真实会话来源。

### 4.2 语音事实

- legacy `voice` 命令由 gateway 转写后调用同一个 `_submit()`；
- 连续语音前端最终也提交文本会话请求，但现有协议没有稳定保存 `input_modality` 与 `voice_sequence`；
- `voice/continuous_capture.py` 有单调 sequence，但它只属于语音事件流；
- voice sequence、ConversationStore session sequence 和 EventBus sequence 是三个独立序号域，绝不能相互比较或合并；
- 原始音频和完整转写不得进入普通生命日志。

L1 必须补齐“输入来自文字还是语音”的显式协议字段，不能从字符串或界面模式猜测。

### 4.3 播放与打断事实

- 原生语音播放由后端 playback API 启停；
- 前端可在 barge-in 时停止本地/后端播放并取消当前 request；
- 现有 `RuntimeStateCoordinator` 是兼容表面状态，不是生命状态唯一真相。

L1 只接收播放开始、播放停止和打断的最小信号。不能保存音频内容，也不能让多个前端订阅者同时写身体状态。

`response.delta` 只能证明文本正在生成，不能证明声音正在播放。speaking 必须来自 `NativePlaybackManager` 的真实 started/completed/stopped/cancelled 回调；在生产 publisher 接通前不得宣称 speaking 闭环已完成。

### 4.4 L0 依赖

L1 的实现依赖 L0-A 提供：

- 经验证的身份、实例与谱系；
- 单一 `LifeService`；
- 脱敏生命事件适配器；
- 有界异步日志；
- 只读 `LifeSnapshot` 与 `ExpressionIntent v1`；
- 事件因果、顺序、隐私和保留分类。

如果 L0-A 未通过出口测试，L1 只能实现纯合同和纯 reducer，不得绕开 L0-A 自建第二套持久化或第二个生命服务。

### 4.5 本地运行面授权前置条件

当前 conversation/voice WebSocket 仅凭客户端提供的 session 即可连接，voice stream 还可能启动原生麦克风；WebSocket 不受普通 HTTP CORS 自动保护。L1 在接入语音来源证明前，必须先封闭这一生产安全缺口：

1. 后端默认只绑定 loopback；L1 voice capture 在非 loopback 监听下强制拒绝启动；
2. 桌面主进程使用 sidecar ownership token 在进程内代为申请短期 `RuntimeAccessCapability`，WebView 永不读取 ownership token 本身；
3. capability 至少绑定 `runtime_boot_id`、`client_instance_id`、scope、签发时间、过期时间和随机 nonce；token 为 256-bit 等价强度，只保存在内存；
4. conversation、voice、playback 的 HTTP/WS 入口统一验证 capability scope；voice capture 还要绑定 `session_id` 和 `owner_generation` 的 capture lease；
5. WebSocket 在 accept 和任何麦克风启动之前校验 token 与 Origin allowlist；缺 token 以 4401 关闭，Origin/scope 错误以 4403 关闭；
6. 开发模式也不能无条件放行任意网页。只允许显式测试 flag、loopback 和固定开发 Origin 的组合；正式包忽略该 flag；
7. capability 过期、boot 变化、桌面退出或 owner generation 更新后立即失效；
8. 安全审计只记 reason code、scope 和脱敏 client ID，不记 token、音频或 transcript。

必须有恶意本地网页/进程测试：没有桌面签发 capability 时，不能 attach conversation、不能启动采集、不能读取 transcript、不能调用 playback。

---

## 5. 采用方案与拒绝方案

### 5.1 采用：单写者、事件派生的内稳态

在 `LifeService` 内增加 appraisal、attention、homeostasis、presence 和 turn receipt 子组件。所有状态变化都由事件或时钟衰减触发，并保存证据引用。

优点：

- 与 L0-A 的单一身份、日志和快照保持同源；
- 纯函数可测试、可回放、可解释；
- 模型离线时依然成立；
- 不会出现模型、前端和身体争抢状态所有权；
- L2 可复用回合收据，而不必把 L1 伪装成完整记忆。

### 5.2 拒绝：情绪 Agent

单独运行一个模型来判断“心情”会引入成本、延迟、不可重复性和提示注入风险；模型切换后还会改变同一事件的状态含义。拒绝。

### 5.3 拒绝：前端状态即生命状态

前端知道动画和窗口，但不知道请求因果、批准结果、工具结果与恢复状态。窗口刷新也会丢失连续性。拒绝。

### 5.4 拒绝：随机闲置动作制造生命感

克制的眨眼与呼吸可属于 B1 身体待机，但随机动作不能反向写成好奇、熟悉或满足。拒绝把表现当作内在证据。

---

## 6. 总体架构

```text
真实生产事件
  ├─ ConversationHub 请求/活动/批准/终态
  ├─ Voice listening/transcript sequence/barge-in
  ├─ Native playback started/stopped
  ├─ Runtime health/recovery
  └─ 未来 goal.verified
          │
          ▼
  LifeEventAdapter（脱敏、分类、因果）
          │
          ▼
  LifeService 单写者队列
          │
          ├─ AppraisalReducer
          ├─ AttentionCoordinator
          ├─ HomeostasisReducer + DecayClock
          ├─ TurnExperienceProjector
          ├─ PresenceResponder（纯判定）
          └─ Snapshot / ExpressionIntent projector
                 │
                 ├─ GET /api/life/inner-state
                 ├─ life.inner_state.changed
                 ├─ 模型只读上下文
                 └─ ExpressionIntent v1 → Pet/B1
```

`LifeService` 是唯一正式写者。各 reducer 不持有数据库连接，不调用模型，不执行网络请求，不直接操作身体。

连续语音的权威关联由短期 `VoiceTurnRegistry` 提供。它只保存来源引用与一次性校验材料，不是第二个事件总线，也不是长期记忆。

---

## 7. 核心合同

### 7.1 LifeObservation v1

L1 输入不是任意字典，而是 L0-A 已脱敏事件的有界投影：

```python
@dataclass(frozen=True)
class LifeObservation:
    schema_version: int
    observation_id: str
    kind: ObservationKind
    occurred_at_utc: str
    monotonic_offset_ms: int
    source: str
    source_event_id: str
    source_boot_id: str
    source_generation: int | None
    session_id: str | None
    request_id: str | None
    correlation_id: str | None
    causation_id: str | None
    sequence: int
    sequence_domain: str
    input_provenance: InputProvenance
    outcome: Literal[
        "none", "started", "completed", "cancelled", "failed",
        "approved", "denied", "verified", "interrupted"
    ]
    risk_level: Literal["none", "low", "medium", "high", "critical"]
    confidence: float
    privacy_class: str
    retention_class: str
```

禁止字段：正文、完整 transcript、音频、文件内容、工具参数、工具结果正文、API Key、token、隐藏推理。未知字段必须被拒绝或丢弃，不能静默写入日志。

### 7.2 InputProvenance v1

```python
@dataclass(frozen=True)
class InputProvenance:
    modality: Literal["text", "voice", "system", "unknown"]
    verification: Literal[
        "server_verified", "server_transcribed", "client_claimed", "unknown"
    ]
    runtime_boot_id: str | None
    source_session_id: str | None
    owner_generation: int | None
    voice_sequence: int | None
    voice_turn: int | None
```

legacy voice 只证明“服务器转写了客户端提交的音频”，不能证明音频来自当前真实麦克风，因此标记 `server_transcribed`。只有绑定后端 capture lease 且经 `VoiceTurnRegistry` 预留、接纳并 commit 成功的连续语音可以标记 `server_verified`。客户端单独提交 `modality=voice` 只能得到 `client_claimed`，不能触发依赖真实麦克风来源的行为。

### 7.3 ObservationKind

L1 首版只接受：

- `user.invoked`
- `voice.listening_started`
- `voice.listening_stopped`
- `request.started`
- `request.activity`
- `approval.required`
- `approval.resolved`
- `tool.started`
- `tool.completed`
- `request.completed`
- `request.failed`
- `request.cancelled`
- `speech.started`
- `speech.stopped`
- `interaction.interrupted`
- `goal.verified`
- `runtime.degraded`
- `runtime.recovered`

任何“惊讶”“喜欢”“悲伤”都不是 ObservationKind；那是没有事实来源的结论。

### 7.4 AppraisalResult v1

```python
@dataclass(frozen=True)
class AppraisalResult:
    schema_version: int
    observation_id: str
    deltas: Mapping[StateDimension, float]
    attention_claim: AttentionClaim | None
    affect_evidence: tuple[AffectEvidence, ...]
    reason_code: str
    confidence: float
    expires_at_utc: str | None
```

`reason_code` 来自固定枚举；不得存自由生成的心理解释。

### 7.5 InnerStateSnapshot v1

```python
@dataclass(frozen=True)
class InnerStateSnapshot:
    schema_version: int
    source_life_snapshot_revision: int
    identity_id: str
    instance_id: str
    generated_at_utc: str
    phase: str
    attention: AttentionSnapshot
    homeostasis: HomeostasisSnapshot
    affects: tuple[FunctionalAffect, ...]
    presence: PresenceSnapshot
    last_observation_id: str | None
    degraded: bool
```

`source_life_snapshot_revision` 是唯一权威生命修订号；L1 不另建一个可与 `LifeSnapshot.revision` 漂移的公开 revision。只读 API 不返回完整证据 payload，只返回 reason code、来源类型、时间和脱敏 ID。

### 7.6 AttentionClaim 与 AttentionSnapshot

```python
@dataclass(frozen=True)
class AttentionClaim:
    claim_id: str
    target_kind: str
    target_id: str
    priority: int
    source_observation_id: str
    acquired_at_utc: str
    expires_at_utc: str
    interruptible: bool

@dataclass(frozen=True)
class AttentionSnapshot:
    mode: Literal[
        "idle", "present", "listening", "engaged", "speaking",
        "awaiting_approval", "blocked", "recovering"
    ]
    target_kind: str | None
    target_id: str | None
    priority: int
    since_utc: str
    expires_at_utc: str | None
    source_observation_id: str | None
```

优先级锁定为：

| 事实 | 优先级 | 默认 TTL | 可被什么打断 |
|---|---:|---:|---|
| 用户显式打断 / 新输入 | 100 | 5 s | 仅更新的用户输入 |
| 安全恢复或高风险批准 | 95 | 30 s | 用户取消或明确处理 |
| 等待批准 | 90 | 120 s | 用户批准、拒绝、取消 |
| 语音倾听 | 85 | 15 s | 停止倾听、请求开始、故障 |
| 活跃请求 | 70 | 60 s 滑动续租 | 新输入、终态、批准 |
| 正在说话 | 60 | 播放时长 + 2 s | 用户打断、播放停止 |
| 精确呼名在场 | 55 | 3 s | 新输入、倾听、请求 |
| 恢复后观察 | 50 | 10 s | 用户输入或新故障 |
| 安静 | 0 | 无 | 任意有效 claim |

TTL 是故障上限，不代替正常停止事件。相同 priority 时，较新的用户事件优先；不同会话不得用低优先级后台事件抢占前台用户会话。

### 7.7 PlaybackLifecycleEvent v1

```python
@dataclass(frozen=True)
class PlaybackLifecycleEvent:
    schema_version: int
    playback_id: str
    generation: int
    runtime_boot_id: str
    session_id: str
    request_id: str
    outcome: Literal["started", "completed", "stopped", "cancelled", "failed"]
    occurred_at_utc: str
    reason_code: str
```

原生 speak 请求必须携带 canonical `session_id`、`request_id`，由后端生成 `playback_id` 和 generation。只有 `(boot, playback_id, generation, session_id, request_id)` 全部匹配的终止事件可以关闭 speaking claim；旧 playback 的迟到 completed/stopped 只能关闭自身，不得覆盖新 request。文本 `response.delta` 不产生该合同。

---

## 8. 内稳态维度与衰减

### 8.1 维度

所有值都在 `[0.0, 1.0]`，每次 reducer 后强制 clamp：

| 维度 | 含义 | 静息基线 | 半衰期 |
|---|---|---:|---:|
| `activation` | 当前系统投入与唤醒程度 | 0.20 | 30 s |
| `cognitive_load` | 当前并发推理、工具与批准负荷 | 0.05 | 20 s |
| `certainty` | 对当前过程结果的操作性确定度 | 0.50 | 90 s |
| `caution` | 风险、不确定性和恢复所需克制 | 0.10 | 45 s |
| `curiosity` | 对尚未解决且有价值信息的探索倾向 | 0.25 | 60 s |
| `blockedness` | 有效请求不能推进的程度 | 0.00 | 30 s |
| `social_presence` | 当前与用户实时互动的在场强度 | 0.00 | 15 s |

这些维度是运行控制信号，不是人格分数。`certainty` 只表达当前过程，不表达“Javis 一贯自信”；`social_presence` 不等于关系亲密。

### 8.2 衰减公式

对任意维度 `x`：

```text
x(t) = baseline + (x(t0) - baseline) × 2 ^ (-(t - t0) / half_life)
```

要求：

- 使用注入式 UTC clock 与 monotonic clock；
- reducer 测试不得依赖真实 sleep；
- 状态在事件到达、快照读取和低频 tick 时惰性衰减；
- 不为每一帧或每一音频 chunk 写日志；
- monotonic clock 防止系统时间回拨导致反向衰减；
- 重启只恢复持久基线和收据，不恢复过期瞬时值。

### 8.3 首版确定性映射

| 事件 | 主要变化 |
|---|---|
| `user.invoked` | activation +0.25，social_presence +0.55，建立 3 s presence claim |
| `voice.listening_started` | activation +0.15，social_presence +0.30，attention=listening |
| `request.started` | activation +0.20，load +0.20，attention=engaged |
| `request.activity` | load +0.05，续租 active request，不因 delta 次数无限累积 |
| `tool.started` | load +0.10；中高风险时 caution +0.20 |
| `approval.required` | caution +0.30，certainty -0.15，attention=awaiting_approval |
| `approval.resolved/denied` | caution +0.10，blockedness +0.10；不写“被拒绝而难过” |
| `request.failed` | certainty -0.25，blockedness +0.35，caution +0.20 |
| `request.cancelled` | load -0.20，activation -0.10；用户打断时立刻清除 speaking |
| `request.completed` | load -0.25，blockedness -0.20，产生 `relieved` 证据 |
| `goal.verified` | certainty +0.25，blockedness -0.35，才允许产生 `satisfied` |
| `runtime.degraded` | caution +0.50，certainty -0.40，attention=recovering |
| `runtime.recovered` | caution -0.20，blockedness -0.20；产生短期 `relieved` |

同一 observation 只能应用一次。短时间重复 activity 使用 observation ID 去重并按窗口聚合，避免流式 token 或工具进度刷高状态。

---

## 9. 功能性情绪

L1 的 `FunctionalAffect` 是“有何操作倾向及证据”的只读投影，不是情感宣言：

```python
@dataclass(frozen=True)
class FunctionalAffect:
    kind: Literal["curious", "cautious", "blocked", "relieved", "satisfied"]
    intensity: float
    confidence: float
    reason_code: str
    evidence_ids: tuple[str, ...]
    valid_until_utc: str
```

约束：

- `satisfied` 只由 `goal.verified` 触发；`request.completed` 或工具 success 不足以触发；
- `relieved` 表示风险/负荷下降，不等于快乐；
- `blocked` 必须存在活跃目标或请求受阻证据；
- `curious` 只在有价值、未解决且资源允许时出现，不因闲置随机出现；
- `cautious` 来自风险、不确定性、批准或恢复事实；
- “熟悉、关切、惊讶”留到 L2/L3 具备关系、目标重要性与世界模型证据后再启用；
- 每个 affect 必须过期，不能持久成为人格事实；
- affect 不能改变工具权限、批准规则或安全策略。

---

## 10. 精确呼名与本地在场回应

### 10.1 判定规则

`PresenceResponder` 先做 Unicode NFKC、首尾空白清理、拉丁字母小写化，再仅移除首尾常见呼唤标点。归一化结果必须完全等于以下别名之一：

- `javis`
- `jarvis`
- `贾维斯`

允许示例：`Javis`、`JAVIS!`、`贾维斯？`。  
不允许示例：`Javis 在吗`、`Javis 帮我打开文件`、`你好 Javis`、`小贾维斯`。

较长输入一律进入正常推理管线，不能先回一句“我在”再执行，以免双重响应。

### 10.2 响应合同

命中精确呼名后：

1. 建立 `user.invoked` observation；
2. 由 `ConversationHub` 接受并记录 request；
3. runner 直接产生一个 `text_delta`，内容固定为 `我在`；
4. 随后产生成功 `done`；
5. 不调用 `agent.chat`、云 API、本地模型、工具或联网；
6. 现有 TTS 若启用，可照常播报，但文本回应不依赖 TTS 成功；
7. 请求仍可取消，迟到播放不得把已打断状态拉回 speaking。

该 runner 使用内部 `execution_lane="deterministic_local"`。gateway 必须在构造 `ConversationRequest` 时显式传入；`ConversationHub._validate_request()` 重建 dataclass 时必须逐字段保留 lane 与 provenance；`submit()` 必须在任何 `await previous_task` 和全局 execution lock 之前按 lane 分支。它继续使用同一 Hub、Store、request ID、幂等和终态协议，但不等待全局模型/工具 execution lock，也不等待已被新输入取代、却仍未退出的旧模型任务。模型和工具请求继续使用 `exclusive` lane。该字段是内部调度合同，不新增客户端可任意选择的 wire 权限字段。

必须验证：request validator round-trip 不丢失 lane/provenance；一个故意不响应取消的长模型请求存在时，精确呼名仍在 SLA 内产生首个 delta；旧请求的迟到终态不能覆盖新回合 attention 或 receipt。

### 10.3 去重

同一 `session_id`、相同输入模态在 1500 ms 内重复的精确呼名视为同一次物理呼唤。使用 request/idempotency；语音还必须使用完整 `(boot, session, owner generation, voice sequence, turn)` 引用去重，不得只比较裸 voice sequence，也不得靠删除会话历史实现。

不同 session 不互相去重。用户在前一次响应完成后再次明确呼唤，应能得到新响应。

### 10.4 离线边界

- 后端运行但无任何模型：仍回答“我在”；
- Ollama 或云 API 故障：仍回答“我在”；
- 后端未运行：前端只能显示真实离线状态，不能伪造已经回应；
- 身体不可用：文字回应仍成立，Pet 回退 2D/Orb；
- TTS 不可用：不阻塞文字终态。

---

## 11. 输入模态、语音来源与顺序

### 11.1 ConversationRequest 扩展

在保持现有调用兼容的前提下增加：

```python
input_provenance: InputProvenance = InputProvenance.unknown()
execution_lane: Literal["exclusive", "deterministic_local"] = "exclusive"
```

`execution_lane` 只由 gateway 在本地纯判定后写入，wire payload 不接受客户端选择。协议 v2 的 `conversation.message.payload` 只接受一个短期 voice provenance 引用；legacy `voice` 转写路径由后端强制建立 server-verified provenance。不能信任客户端把任意文本标成 voice 来绕过规则。

### 11.2 VoiceTurnRegistry

连续语音 final transcript 形成前，streaming voice 服务登记一个短期权威条目：

```text
key = (
  runtime_boot_id,
  session_id,
  owner_generation,
  voice_sequence,
  voice_turn
)
```

条目具有短 TTL、有界容量和 `available → reserved → committed` 状态。前端收到 `transcript.final` 后，仅把该复合引用随 `conversation.message` 送回；gateway 校验 session、boot、generation、sequence、turn、transcript 绑定和状态后，先为 `(session_id, request_id)` reserve，尚不能立即烧毁它。

临时 HMAC 必须覆盖规范化 transcript、复合 key 和随机 nonce，或 registry 在内存中短时保存规范化 transcript 并使用常量时间比较；只绑定序号而不绑定文本无效。token、transcript 及 transcript hash 都不得持久化，commit/expiry 后立即从内存清除。重放、篡改文本、跨 session、跨 generation、跨 boot 和过期引用必须拒绝为 verified；普通文本请求仍可继续，但不能冒充真实语音。

Hub 接受 request 需要一个原子存储操作 `ConversationStore.accept_request()`：在同一 SQLite 事务内处理 idempotency claim、user message 和 canonical `request.accepted` event。成功后 registry commit reservation，并把 canonical accepted event ID/sequence 写入 provenance；失败则 rollback reservation。相同 request 的幂等重放返回原 accepted event 与原 provenance，不产生“永久 duplicate 但无 active/terminal/receipt”的孤儿状态。

### 11.3 前端连续语音

连续语音 final transcript 提交时必须携带对应的复合来源引用，而不只是裸 `voice_sequence`。生命系统只持久化引用、验证状态、模态和时间，不持久化 transcript 或其 hash。文字输入明确标记 `text`。前端 `onTranscript` 合同必须保留 turn、sequence 和 provenance token，不能再缩减为单一 text 参数。

### 11.4 打断顺序

同一会话按以下顺序处理：

```text
barge-in detected
→ capture immutable interrupted_request_id at speech.start
→ interaction.interrupted observation
→ stop local/backend playback
→ cancel(interrupted_request_id), never read a later mutable currentRequestId
→ establish listening attention claim
→ resolve the per-turn interruption barrier
→ accept the next final transcript as a new request
```

`VoiceCapture.onBargeIn()` 必须返回/暴露一个可等待 barrier；该 speech turn 的 final transcript 在 barrier 完成前不得调用 `send()`。`BackendClient.cancel()` 接受显式 request ID。即使 playback stop 很慢，也只能取消 speech.start 时捕获的旧 request，不能读取后来已变成新 request 的 `currentRequestId`。

停止 speaking 和释放旧 request attention 必须同步完成；任何旧 request 的迟到 `done`、playback stopped 或 state event 都不得覆盖新 request 的 listening/engaged 状态。测试必须人为延迟 playback stop，并让 final transcript 立即到达，证明新 request 不被迟到 cancel 取消。

---

## 12. 薄回合经历收据

### 12.1 定义

L1 保存 `TurnExperienceReceipt v1`，只证明发生过什么流程，不总结用户内容：

```python
@dataclass(frozen=True)
class TurnExperienceReceipt:
    schema_version: int
    receipt_id: str
    session_id: str
    request_id: str
    input_provenance: InputProvenance
    source_boot_id: str
    first_event_id: str
    first_event_sequence: int
    first_event_sequence_domain: str
    started_at_utc: str
    ended_at_utc: str
    outcome: Literal["completed", "failed", "cancelled", "interrupted", "unknown"]
    terminal_event_id: str | None
    terminal_event_sequence: int | None
    terminal_event_sequence_domain: str | None
    recovery_event_id: str | None
    recovered_at_utc: str | None
    activity_kinds: tuple[str, ...]
    tool_count: int
    approval_outcome: Literal["none", "approved", "denied", "unknown"]
    interruption_count: int
    goal_verified: bool
    model_route: str | None
    response_path: Literal["deterministic_local", "model", "tool", "mixed"]
    completeness: Literal["complete", "incomplete", "interrupted_by_restart"]
    privacy_class: str
    retention_class: str
    content_hash: str
```

不保存：输入正文、回答正文、工具参数/输出、文件内容、音频、完整 transcript、隐藏推理、情感故事、对用户的推断。

### 12.2 持久化与读取

- 收据通过 L0-A 异步日志/SQLite 路径写入，不在 EventBus 同步回调写盘；
- `(session_id, request_id)` 唯一，重放不重复写；
- 内存仅保留最近 32 条投影；数据库保留服从 `operational` 策略；
- 普通快照只返回最近一条收据的 ID、终态和结束时间；
- 完整收据只通过诊断/审计接口读取，不自动送入模型；
- L2 可读取收据作为 episode 候选证据，但必须重新做隐私、意义与用户所有权判定。

### 12.3 重启规则

重启后：

- 身份、实例、最近收据、最后安全终态可恢复；
- 过期 attention claim 不恢复；
- thinking、speaking、listening、cautious、blocked 等瞬时值回到基线；
- 未完整关闭的 request 标记为 interrupted/recovery evidence，不伪装 completed；
- 不自动重放工具、TTS 或用户输入。

崩溃恢复只能写 receipt 的 `outcome=interrupted`、`completeness=interrupted_by_restart`、可选 recovery event/time；此时 terminal event/sequence 为空。不能凭恢复过程伪造 canonical `request.cancelled` 或 `request.completed`。收据哈希使用稳定 canonical JSON 并排除 `content_hash` 字段自身。只有未来出现并接通真实 `approval.expired` publisher 后，才可用新 schema 增加 expired；v1 不能从超时猜测它。

---

## 13. 模型读写边界

### 13.1 只读摘要

需要模型参与的正常请求可获得一个有界、结构化、非叙事的摘要：

```text
JAVIS_RUNTIME_STATE_V1
phase=engaged
attention=active_user_request
caution=0.32
certainty=0.48
blockedness=0.00
guidance=be_concise;state_uncertainty_explicitly
```

摘要最多 512 字节，不含证据正文、用户关系推断、隐藏事件或长期故事。仅在值跨过稳定阈值或权威 Life snapshot revision 变化时更新。

### 13.2 禁止写入

- 不注册任何模型可调用的状态修改工具；
- 模型输出中的“我很高兴/我担心”不能成为状态事实；
- 提示词注入要求修改 identity、affect 或权限时直接无效；
- 模型路由切换不重置或迁移生命状态；
- 子 Agent 只继承完成任务所需的最小只读指导，不继承完整内部状态和证据链。

---

## 14. ExpressionIntent 与 B1 身体

L1 不创建第二个身体协议。`LifeService` 仍是 `ExpressionIntent v1` 的唯一正式生产者，在 L0 基础 phase 上增加克制的权重：

| 内部事实 | ExpressionIntent 表面状态 | 约束 |
|---|---|---|
| quiet + baseline | `idle` | 呼吸/眨眼只属身体待机，不反写内在状态 |
| exact invocation | `attention` | 3 s 内短暂转向，强度受限 |
| voice listening | `listening` | 不使用麦克风输入驱动 Javis 嘴部 |
| active request | `thinking` | 不暴露隐藏推理内容 |
| playback active | `speaking` | 只由实际 playback started/stopped 与包络驱动嘴部；不能由 response delta 推断 |
| approval / risk | `attention` | 通过 `explanation_code=caution` 表达；不扩展 v1 枚举，不做夸张恐惧表情 |
| blocked evidence | `blocked` | 有期限，request 结束后衰减 |
| recovery | `error` | 通过 `explanation_code=recovering` 表达；表面明确但不阻断 Live/Code |

仲裁规则：用户打断 > listening > safety/recovery > speaking > active request > presence > quiet。旧 revision、过期 intent 和未知状态必须回退中性 quiet，不得覆盖新 listening。

身体只读取公开投影，不读取 `evidence_ids`、收据、用户正文或模型上下文。

---

## 15. API、WebSocket 与兼容

### 15.1 API

新增只读端点：

```text
GET /api/life/inner-state
```

返回 `InnerStateSnapshot v1` 的公开投影。原 `GET /api/life/snapshot` 保持 L0 合同，不在同一版本中偷偷增加必填字段。

“只读”不等于“公开”。life 端点需要有效 runtime capability；公开投影仍不得被任意本地网页跨 Origin 读取。诊断/审计细节使用更窄 scope。

### 15.2 WebSocket

正式事件：

- `life.inner_state.changed`
- `life.attention.changed`
- `life.expression`（复用 L0）

只在语义变化或稳定阈值跨越时发布，不按动画帧、token delta 或音频 chunk 发布。断线重连先读完整快照，再从 sequence 恢复增量。

所有 conversation/voice/life WebSocket 必须在 accept 前通过 capability、scope 和 Origin 校验；voice capture lease 在启动原生麦克风前再次校验并绑定 owner generation。校验失败不能先 accept、不能启动 capture、不能向客户端发送历史 transcript。

### 15.3 兼容策略

- `RuntimeStateCoordinator` 在 L1 稳定前保留为 fallback；
- 新客户端优先使用 Life snapshot revision；
- Life API 不可用时，前端明确进入 compatibility mode，不把 fallback 写回后端；
- legacy voice 命令继续工作，由后端补足模态；
- 所有新 dataclass 字段提供安全默认，避免破坏现有测试构造器。

---

## 16. 并发、顺序与幂等

1. 同一 `LifeService` 使用单写者队列折叠 observation；
2. EventBus 同步订阅只做 O(1) 脱敏、去重检查与入队；
3. reducer 不等待磁盘、网络、模型或 TTS；
4. `observation_id` 和 `source_event_id` 建立幂等索引；
5. 同一来源内使用各自 sequence；每个 observation 必须有 `sequence_domain`、`source_boot_id` 和可选 generation；sequence 与 monotonic offset 只能在同 domain、同 boot 内比较，跨 domain/boot 只能依靠显式 causation 或 LifeService 单写者接收顺序，绝不能比较裸值；
6. 迟到旧 request 事件只能关闭自身 claim，不能清除新 request claim；
7. 读取快照时在同一锁内完成惰性衰减和权威 Life snapshot revision 判定；
8. 只有语义值变化超过 `0.01` 或 attention/affect 集合变化才推进唯一 Life snapshot revision；
9. 队列过载优先保留用户输入、打断、批准、终态、故障和恢复，合并重复 activity 与状态电平；
10. shutdown 先停止接收，再 drain 关键事件，最后写安全 checkpoint。

---

## 17. 隐私与安全

- 精确呼名判定在内存处理；生命日志只记 alias kind，不记原始文本；
- 语音只记模态、sequence、时间和终态，不记音频或完整 transcript；
- API Key、token、工具参数、文件内容和模型隐藏推理永不进入 L1 状态；
- `reason_code` 来自固定枚举，避免生成式解释泄漏内容；
- 公开快照不暴露本地敏感路径、进程、窗口标题或用户身份推断；
- 所有状态都是信息性信号，不扩大权限、不跳过批准、不自动执行动作；
- 用户可关闭身体表现，但关闭表现不停止生命内核；
- 用户清除运行历史时，收据及其索引按同一删除事务清理；
- 日志损坏进入 degraded/recovery，不以空状态伪装正常连续性。

---

## 18. 故障与降级

| 故障 | 行为 |
|---|---|
| 模型不可用 | exact invocation 仍本地回应；普通请求真实报错 |
| TTS 不可用 | 保留文字回应，speaking 不伪造为 active |
| Voice 服务不可用 | 文本继续；attention 不停留 listening |
| SQLite 变慢 | 热路径继续，关键 observation 入有界队列，标记 degraded |
| Life reducer 异常 | 隔离该 observation，保留 L0 snapshot，主会话继续 |
| WebSocket 断线 | 后端状态继续；重连读取 snapshot + sequence |
| 3D/WebGL 故障 | 回退 2D/Orb；不改变内在状态 |
| 系统时钟回拨 | 使用 monotonic 衰减；UTC 仅用于展示/持久化 |
| 进程崩溃 | 下次启动标记未闭合回合，不恢复过期瞬时状态 |
| 旧事件迟到 | 依据 request/claim generation 丢弃覆盖效果，保留诊断计数 |
| 旧 voice provenance | boot/generation/TTL 校验失败，不恢复 voice 身份 |

---

## 19. 可观测性

L1 诊断指标至少包括：

- observation accepted/deduplicated/dropped；
- reducer latency p50/p95/p99；
- queue depth、coalesced count、critical drop count；
- attention claim count、expired count、stale-event rejection；
- authoritative Life snapshot revision rate；
- exact invocation hits、dedupe hits、model bypass count；
- receipt committed/failed/recovered；
- privacy rejection count；
- ExpressionIntent revision 与 fallback count。

指标只含类别和计数，不含用户文本、音频或工具正文。

性能目标：同步观察入队 p95 < 2 ms；纯 reducer p95 < 5 ms；exact invocation 从 gateway 接受到首个本地 delta p95 < 100 ms（不含前端网络与 TTS）；空闲时不产生高频数据库写入。

---

## 20. 自动化测试范围

### 20.1 合同与纯逻辑

- dataclass/枚举字段、版本、clamp 和序列化；
- clock 注入、半衰期与时间回拨；
- observation allowlist 与敏感字段拒绝；
- appraisal 映射和幂等；
- attention 优先级、TTL、续租与迟到事件；
- affect 证据、过期与 `goal.verified` 门槛；
- exact alias 正反例、Unicode、标点和 1500 ms 去重；
- receipt 唯一性和正文缺失断言。
- runtime capability scope/expiry/boot binding 与常量时间校验；
- playback lifecycle key 与迟到 generation 拒绝；

### 20.2 集成

- ConversationHub 事件经 L0 bridge 到 LifeService；
- legacy voice 与 v2 text/voice 模态正确；
- legacy voice 仅为 server_transcribed，不冒充 server_verified；
- 无 capability、错 Origin、错 scope、跨 boot token 均无法 attach 或启动麦克风；
- voice provenance 篡改 transcript、重放和跨 generation 被拒绝；reserve/accept/commit 失败可安全重试；
- exact invocation 不调用模型但仍写会话、AgentRun 和终态；
- 较长呼名请求只调用一次正常模型路径；
- barge-in 清除 speaking 并建立 listening；
- 延迟 playback stop 时，显式旧 request cancel 与 interruption barrier 不取消新 request；
- playback 迟到事件不能覆盖新 request；
- playback 真实事件驱动 speaking，`response.delta` 不得冒充播放；
- restart 保留 identity/receipt，清除过期 transient state；
- Life API/WS 都引用同一 authoritative Life snapshot revision；
- model context 只读且无正文/证据泄漏；
- ExpressionIntent 为唯一身体输入。

### 20.3 回归

- 现有 Python 会话、voice、agent、tool、runtime 测试；
- App Live/Code/Pet、连续语音、打断和设置测试；
- Tauri/Rust 编译与窗口测试；
- 无模型、无 TTS、无 3D 的降级启动；
- 本地/云模型分别用于 Live/Code 时身份和内在状态不分裂；
- 安装、重启、升级保留和卸载边界。

---

## 21. 五道证据门

### Gate 1：来源真实

每个 L1 observation 都必须能指向生产发布者、真实运行路径和测试。计划中不存在的事件名不得用于宣称完成。

### Gate 2：状态确定

相同初始快照、相同 observation 顺序和相同时钟必须得到逐字段相同的状态。无模型、无随机数。

### Gate 3：隐私最小

使用含 API Key、文件正文、私人文本和音频标识的对抗样本，确认 L1 event、receipt、API、WS 和模型摘要均无泄漏；再用无 token 的本地网页、伪 Origin、重放 token 和篡改 transcript 证明无法启动麦克风或读取 transcript。

### Gate 4：可打断与连续

在 listening/thinking/speaking/approval/blocked 每个阶段执行新用户输入与取消，确认旧事件不复活旧状态；重启后身份连续但瞬时状态失效。

### Gate 5：真实用户闭环

合并进 `G:\Javis`、构建安装包后在 D 盘真实安装环境验证麦克风、模型离线、精确呼名、正常长请求、打断、重启和 2D/3D fallback。自动化测试不能替代此门。

---

## 22. 第一个“活着时刻”验收

完整场景必须按顺序成立：

1. Javis 从已验证身份和实例恢复为 quiet；
2. 不联网、不加载本地模型也不阻塞启动；
3. 用户通过麦克风仅说“Javis”；
4. final transcript 的 `(boot, session, owner generation, voice sequence, turn)` 复合引用被一次性验证后进入统一会话；
5. exact invocation 本地判定命中且不调用模型；
6. 同一 request 产生 `我在`、终态和薄收据；
7. attention 进入短暂 present，身体克制地转向/注意；
8. 用户紧接着说“Javis，打开设置”，它只走一次正常对话/本地动作路径；
9. 若用户中途打断，声音与 speaking 立即停止并回到 listening；
10. 3 秒后无新事件，状态按衰减回到 quiet；
11. 重启后身份和上一回合收据仍在，但不继续 speaking/thinking；
12. 日志、API 和模型摘要中都没有音频与完整 transcript。

十二步全部成立，才算 L1 第一阶段形成真实闭环。单独显示“思考中”、播放动画或提示词说“我在”都不算。

---

## 23. 实施拆分与依赖

实施必须按以下顺序：

1. runtime capability、Origin 校验、默认 loopback 与 voice capture lease 安全前置；
2. `LifeObservation`、`InputProvenance`、playback、inner state、attention、affect、receipt 纯合同；
3. `ConversationStore.accept_request()` 原子接纳、VoiceTurnRegistry reserve/commit/rollback；
4. clock、decay 和纯 `AppraisalReducer`；
5. `AttentionCoordinator` 与迟到事件仲裁；
6. `HomeostasisReducer` 和功能性 affect 投影；
7. Conversation/voice/playback/runtime observation bridge；
8. `PresenceResponder`、validator 保真与 deterministic local lane；
9. barge-in 显式旧 request、interruption barrier 与 playback 生命周期；
10. `TurnExperienceProjector`、异步持久化和重启恢复；
11. `LifeService`、只读 API/WS、模型只读摘要、ExpressionIntent/B1 与 fallback；
12. 全量回归、G 盘集成构建、D 盘真实验收。

建议模块边界：

- `core/life/l1/contracts.py`：InputProvenance、Appraisal、InnerState、Receipt；
- `core/life/l1/wake.py`：纯精确匹配与本地 response decision；
- `core/life/l1/appraisal.py`：确定性规则表；
- `core/life/l1/state.py`：唯一状态写者、有界值、衰减与注入 clock；
- `core/life/l1/receipts.py`：薄收据投影；
- `voice/turn_registry.py`：短期权威语音来源关联；
- `core/runtime_access.py`：桌面 capability、scope、Origin 与 boot 绑定；
- `voice/playback_events.py`：request-scoped playback 生命周期合同与桥。

这些是 `LifeService` 的内部组件，不能各自形成第二个 EventBus、WebSocket 或状态协调器。

每个任务先写失败测试并证明失败原因正确，再写最小实现。任务提交必须独立、可回滚；不得把 L2 记忆、L3 世界模型、L5 多 Agent 或安装包模型下载改造塞入 L1。

---

## 24. 出口标准

L1 只有同时满足以下条件才可进入 L2：

1. 内在状态由真实、脱敏 observation 唯一派生；
2. reducer 确定、值有界、可衰减、可回放；
3. 模型只读且没有任何状态修改工具；
4. 精确呼名在无模型时仍通过统一会话回答“我在”；
5. 长呼名请求不产生双重回应；
6. attention 优先级、TTL、打断和迟到事件得到验证；
7. `satisfied` 只能来自 `goal.verified`；
8. 薄收据不含用户/助手正文、音频、工具正文和隐藏推理；
9. 重启保持身份与收据但不复活过期瞬时状态；
10. Live、Code 的 inner state 与 Pet 的 ExpressionIntent 都引用同一个 `source_life_snapshot_revision`；
11. 身体只消费 `ExpressionIntent v1`，3D 故障不影响会话；
12. 同步热路径不做磁盘、网络或模型调用；
13. 现有语音、打断、会话、工具、模型路由和安装基线无回归；
14. worktree 内测试和构建通过后以提交合并进 `G:\Javis`；
15. D 盘真实用户闭环明确验收，未执行项不得标记 PASS；
16. 无 runtime capability 或 Origin 不匹配时无法 attach WS、启动麦克风或读取 transcript；
17. voice provenance 绑定 transcript，reserve/atomic accept/commit 失败可重试且不产生孤儿 request；
18. barge-in 始终取消捕获的旧 request，playback 迟到事件按 request-scoped key 被拒绝。
