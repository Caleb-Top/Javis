# Javis P0–P1 权威执行计划

> 日期：2026-08-09
> 状态：执行中
> 适用范围：P0 语音可靠性收口、v3.0.1 候选构建、D 盘真实验收、P1 可信发布
> 冲突处理：本计划只覆盖近期执行顺序；当既有 `PLAN-*`、`MASTER-PLAN-*`、`CORE-REPORT-*` 的近期步骤与本计划冲突时，以本计划为准。长期产品愿景仍由原文档保留。

## 1. 输入与不可变规则

本计划综合以下输入：

- `Javis_一个生命的诞生_完整版_V4_2026-08-09.md`
- `Javis_核心系统底层代码设计_细化报告_2026-08-09.md`
- `Javis_AI_OS_完整实施计划_2026-08-09.md`
- 用户确认的开发/验收目录规则

目录边界：

| 路径 | 角色 | 规则 |
|---|---|---|
| `G:\Javis` | 唯一真源、依赖、缓存、编译、构建与最终 main | 开发期间不直接改；审查通过并得到合入许可后才合并 |
| `G:\Javis-worktrees` | 隔离开发与大项测试区 | 当前唯一允许写源码的区域 |
| `D:` | 真实用户模拟端 | 只放最终候选安装包、安装、运行、升级保留、卸载与验收证据 |
| C 盘 | 非本任务工作区 | 不写入本项目修复、构建缓存或交付物 |

附加纪律：

- 不下载或安装任何依赖、模型、运行时，除非用户明确同意。
- 用户选择 G 盘数据根目录时，运行时、配置、记忆和模型注册必须按配置落到该目录；不得把固定 `%LOCALAPPDATA%` 路径当成不可更改依赖。
- 每项行为修改先有失败测试，再实现、回归、独立审查和独立提交。
- 代码测试通过不等于真实语音问题已经完成；只有 D 盘最终二进制通过门禁才可宣布完成。
- 测试总数必须来自当次命令输出，不沿用计划中的固定旧数字。
- 不记录原始 PCM、base64 音频、API Key、访问令牌或未脱敏工具参数。

## 2. 当前真实基线

当前开发分支：

- 工作树：`G:\Javis-worktrees\voice-p0-reliability`
- 分支：`fix/p0-voice-reliability`
- 基线 main：`47683c71`
- P0 Task 4 提交：`02d3564 test: gate releases on voice reconnect reliability`

已完成的 P0 Task 1–4 能力：

- WebSocket 断开、发送失败、显式停止和取消时释放麦克风 owner。
- 释放过程单航班、可等待、可抵抗重复取消；迟到旧 owner 不覆盖新 owner。
- Live 请求具有 acknowledgement、首响应和总时限终态；迟到事件不能复活已退休请求。
- 空 STT 产生唯一 `transcript.empty`，仅带时长/RMS/peak 等脱敏指标。
- 生产 Live 界面显示“没有识别到语音，请再说一次”，保持 listening，不提交空请求。
- 安装验证脚本会真实执行重连集成测试和前端生命周期测试，而不是只匹配源码文字。

2026-08-09 当前验证证据：

| 层 | 结果 |
|---|---|
| 前端聚焦生命周期 | 21/21 PASS |
| Python 语音/运行时聚焦 | 58/58 PASS |
| App 全量 | 120/120 PASS |
| TypeScript 严格编译 (`tsc --noEmit`) | PASS |
| Python 语音/会话/发布阻断集合 | 104/104 PASS |
| 两套 agent distill 辅助测试 | 18/18 PASS |
| Tauri Rust | 6/6 PASS |
| PowerShell 解析与无 override 源码门禁 | PASS |
| Python `tests/` 全仓库 | 397 PASS / 5 worktree-resource failures；同 5 项在只读 `G:\Javis` 基线 5/5 PASS |

5 个工作树失败分别依赖未复制到隔离区的 Ollama runtime、R1 权重、v1 安装归档和 G 盘核心身份数据；它们不是本次 P0 变更引入，但暴露了 preflight/测试环境需要显式共享根或环境守卫。仍未完成：构建前可观测性/stall harness、模型中心差距闭环、最终 main 合入、v3.0.1 构建、D 盘安装验收、24 小时浸泡。因此当前状态只能称为“P0 Task 4 代码候选完成”，不能称为“P0 发布完成”。

## 3. 近期唯一依赖顺序

```text
P0 Task 1–4 语音可靠性修复
        ↓
P0-O 可观测性 + 三档 stall harness
        ├──────────────┐
        ↓              ↓
P1.1 版本/发布基线   P1-M 现有模型中心差距闭环
        └──────┬───────┘
               ↓
v3.0.1 候选代码冻结
        ↓
用户批准后合入 G:\Javis main
        ↓
从最终 main 构建唯一候选二进制
        ↓
D 盘 P0 真实验收
        ↓
同一二进制 24 小时浸泡
        ↓
P1 可信发布冻结
        ↓
P2 记忆 ──→ P3 认知
      └────→ P4 身体
             P3 + P4 ──→ P5 管家/进化
```

说明：

- P0 必须验收最终将发布的 v3.0.1 二进制；不能先验收 v3.0.0，再用不同二进制升版。
- P2 完成后，P3 与 P4 可以在各自工作树并行；P5 同时依赖认知、安全和身体契约。
- “五器官”、完整记忆、3D 数字人和自主进化不进入本轮 P0 修复。

## 4. 阶段 A：P0-R 代码出口

### A1. 自动化硬门禁

必须证明：

1. 第一条 `/ws_voice_stream` 异常断开后，`manager.stop` 对该 session 精确执行一次。
2. 第二个不同 conversation/session 能取得 `audio.stream.ready`，任一时刻最多一个 owner。
3. gateway 返回后没有 receiver、sender、release 或事件轮询任务泄漏。
4. 每个 final turn 恰有一个终态：`transcript.final`、`transcript.empty` 或 `audio.error`。
5. `transcript.empty` 显示可见提示、返回 listening、保持连续流、不得调用 `client.send`。
6. 3 秒 acknowledgement、15 秒首响应、60 秒总上限均能清队列、取消请求并恢复 UI。
7. 旧请求的迟到 accepted/delta 不得改变当前活动请求和 Live caption。
8. 打断必须停止本地播放、取消生成并禁止旧增量继续追加。

注意：3/15/60 秒是“防永久卡死上限”，不是产品延迟 SLA。本地产品目标仍是停说到首响应 P95 小于 1 秒，低配目标小于 1.5 秒。

### A2. 生产链约束

- 原生连续采集只走 `/ws_voice_stream`。
- 文字/最终转写只走 `/ws → conversation_hub.submit → agent.chat()`。
- 不接入未被生产入口调用的 `voice/realtime.py::chat_quick`。
- 音量和未来口型只消费 `onLevel` 或播放电平，不把整段 `onAudio(base64)` 当实时振幅。
- 同步事件热路径只能做有界 O(1) 内存操作；磁盘、网络和聚合进入后台，并有 start/stop/join。

### A3. 构建前可观测性与故障注入（待实现）

这一项必须在候选构建前以独立工作树、TDD 和独立提交完成；不得等 D 盘验收或浸泡时才发现指标缺失。

最小 diagnostics schema：

- `running`、`session_attached`、脱敏 owner generation/identity、active gateway task 数。
- frame、turn、`transcript.final`、`transcript.empty`、`audio.error` 计数。
- transcription queue 当前/峰值深度、dropped frames。
- reconnect 总数、最近/分位恢复耗时、可恢复/不可恢复错误分类。
- 前端 accepted、first-response、overall timeout 计数和最近一次终态 phase。

实现约束：

1. 指标仅为有界计数、时长和脱敏标识，不记录原始音频、完整提示或密钥。
2. 新增真实可执行的测试 harness，分别模拟：无 ack、已 ack 无首字、已有增量后挂起。
3. stall 入口只能在显式测试环境变量/loopback 测试模式下启用，生产默认关闭，不能被远程请求任意打开。
4. harness 必须自动采样、断言终态、恢复 listening，并在 `finally` 清理 stall、socket、进程和端口。
5. 自动化测试证明 diagnostics 计数与真实 session/request 对齐，第二次运行不继承脏状态。

### A4. 出口

- Task 4 独立审查 APPROVED。
- 聚焦 App、Python、Rust 和 PowerShell 门禁通过。
- Python 全仓库测试结果已记录；任何失败按“本次回归/已有基线/环境阻断”逐项归类，不能笼统跳过。
- A3 diagnostics schema 与三档 stall harness 已完成自动化红绿验证。
- 分支 diff manifest、tip 和 merge-base 已留档。

## 5. 阶段 B：v3.0.1 发布基线与模型中心差距闭环

### B1. P1.1 版本与发布基线

在独立工作树完成，不混入 P0 提交：

1. 建立可生成的单一版本源，至少同步 Python、Tauri、Node、NSIS 和产物名称；增加一致性测试。
2. 候选 manifest 包含 `version`、`source_commit`、`runtime_sha256`、`package_sha256`、`test_summary`、`acceptance_status: pending` 和 candidate ID。
3. 修正 preflight 对工作树、构建依赖和环境性失败的误判；不得用 skip 把真缺陷变绿。
4. `.gitignore` 只负责忽略模型、运行时、缓存和产物；它不等于释放磁盘，不得借此删除依赖。
5. 固化单命令构建入口，但最后实现编排脚本，先保证底层门禁可靠。

推荐顺序：`版本一致性 → preflight/环境守卫 → 发布纪律 → 单命令编排`。

### B2. P1-M 现有模型中心差距审计与闭环

这不是绿地重写。先核验并复用仓库已有的六类供应商、账户 `/models` 合并、统一设置和 Hugging Face 安装器，再只补真实缺口：

1. 供应商至少覆盖 DeepSeek、GLM、Kimi、Qwen、OpenAI、Anthropic；每家显示内置推荐目录、账户 `/models` 返回的完整可用目录，并允许自定义兼容 model ID。
2. Live 与 Code 从任一入口打开同一个设置中心；共享开关开启时使用同一 profile，关闭时可独立选择本地/云端、供应商、模型和 endpoint。
3. 路由隔离必须可验证：Live 本地 + Code 云端、Live 云端 + Code 本地、两者同云端、两者同本地四种组合互不覆盖。
4. 基础安装包不含大型模型权重；R1 附加包与主安装包并列，用户可改安装目录，导入前验证 manifest/hash/空间。
5. 本地向导复用现有 Ollama/GGUF/HF 能力，补齐明确同意、许可证/大小/目标目录、分阶段进度、暂停/继续/取消、断点恢复和失败回滚。
6. 默认 `trust_remote_code=false`；自动适配只支持白名单架构/格式，不执行模型仓库任意代码。
7. Ollama 未运行、重启或端口变化后，保存的模型与自定义目录可恢复检测；本地失败不得破坏云端 profile。
8. 为以上差距增加真实状态机测试和 D 盘验收，不以静态源码匹配代替行为验证。

## 6. 阶段 C：合入与 G 盘构建

前置条件：用户明确批准合入。

1. 在 `G:\Javis` 记录 main HEAD、工作树状态和未跟踪构建资产清单。
2. 逐提交审查 P0、P0-O、P1.1 与 P1-M 分支，使用可追溯的非快进合并。
3. 合入后的最终 main 重新运行全部 Python、App、Rust、release blocker 和 installer source gate。
4. 只从该 main 构建一个 v3.0.1 候选；构建后任何二进制相关代码变化都会使验收证据失效。
5. 基础安装包不携带大型本地模型权重；R1 作为独立附加包，与主安装包并列交付。
6. 产物复制到 D 盘后记录 SHA-256、大小、source commit 和候选 manifest；D 盘不得参与编译。
7. D 盘验收和浸泡完成后，不重建二进制，只生成 final release manifest：引用同一 candidate ID/package SHA，并附验收报告与浸泡报告哈希；若 package SHA 变化则全部证据作废重跑。

## 7. 阶段 D：D 盘真实验收矩阵

所有验收针对同一个最终候选二进制：

| 场景 | 次数/指标 | 通过条件 |
|---|---:|---|
| 正常可懂真人语音闭环 | 连续 30 个成功轮次 | 每轮必须依次达到 `transcript.final → request.completed → 可见完整文本 → 可听 TTS`；若用户关闭 TTS，需记录为明确配置降级。任一空识别、取消、错误或超时均使本轮验收失败，修复后从连续计数重新开始 |
| 关闭/重开 Live | 10 次 | 每次可重新 listening，无旧 owner |
| 强制语音 WS 断线重连 | 10 次 | 新会话收到 ready；owner ≤1；无残留任务 |
| 静音/空白/不可识别 | 各至少 3 次 | 显示重试提示，不进入 thinking，不关闭连续流 |
| 无 ack | 1 个可控 stall | 约 3 秒可见失败并恢复 |
| 已 ack 无首字 | 1 个可控 stall | 约 15 秒可见失败并恢复 |
| 已有增量后永久挂起 | 1 个可控 stall | 60 秒上限内可见终态并恢复 |
| 云 API 失败/断网 | 各 1 次 | 错误可理解，Live 不永久卡死 |
| Ollama 未启动/本地模型缺失 | 各 1 次 | 仅影响本地路由，云端路由不被破坏 |
| 麦克风断开或设备变化 | 各 1 次 | 可见错误，可重新选择设备并恢复 |
| TTS 播放中打断 | 至少 10 次 | 停止输出 P95 小于 500ms，旧增量不再追加 |
| 终态后恢复 listening | 全部回合 | 1 秒内恢复 |
| 本地停说到首响应 | 记录 P50/P95 | 目标 P95 小于 1 秒；低配目标小于 1.5 秒 |
| 中文真人语料 | 20 条 × 3 次 | 安静近讲 empty rate 为 0，平均 CER 小于 20%；同时报告 P95 与最差项，不挑样本 |
| 六供应商模型目录 | 每家至少 1 次 | 无密钥也显示内置推荐；已配置凭据的供应商合并账户 `/models` 全量返回；目录可搜索，未知兼容模型可自定义输入，错误不覆盖已保存 profile |
| Live/Code 路由组合 | 共享 1 组 + 独立 4 组 | 共享配置一致；独立配置互不覆盖，重启后保持 |
| R1 离线附加包 | 改目录导入 1 次 | manifest/hash/空间校验通过，模型注册到用户选定目录；主安装包不含权重 |
| Hugging Face 安装向导 | 下载/暂停/恢复/取消/失败各 1 次 | 仅在用户明确批准网络下载验收后执行；进度真实可见，可断点恢复；取消/失败不留下错误注册，不执行远程任意代码。未批准前只运行本地 fixture/离线附加包状态机测试，不虚构真实下载 PASS |
| Ollama 重启与离线 | 各至少 1 次 | 重启后模型/目录恢复；离线只使本地 route 可见失败，云端 route 不受影响 |
| 重启持久化 | 3 次 | 对话 identity、模型设置和用户路径按契约保留 |
| 自定义数据根目录 | 1 次完整安装/重启 | 选定 G 盘目录后，配置、记忆和运行数据不静默回落到 D 盘或固定 AppData 数据目录 |
| 安装/重装/升级/卸载 | 各 1 次 | 主程序可运行；升级保留；卸载边界符合报告 |

看门狗必须由黑洞服务、可控 SUSPEND 或 `JAVIS_TEST_STALL` 触发。错误端口和关闭 Ollama 会快速失败，不能证明 3/15/60 秒超时逻辑。

外部下载属于 consent-gated 验收：若用户明确拒绝 Hugging Face 真下载，该行记录 `USER_DECLINED/NOT_RUN`，不能写 PASS，也不阻断发布；但本地 fixture、离线附加包、暂停/恢复/取消/回滚状态机仍必须 PASS。其余矩阵行均为强制门禁。

## 8. 阶段 E：24 小时浸泡与发布冻结

浸泡只消费 A3 已在候选构建前完成并冻结的 diagnostics schema：

- 当前 owner/session、running/session_attached
- 语音 frame/turn/terminal 计数
- transcription queue 深度和 dropped frames
- reconnect 次数、恢复耗时、可恢复/不可恢复错误分类
- 请求 accepted/first-response/overall timeout 计数

24 小时结束后必须确认：

- 没有永久 thinking、owner 泄漏或不断增长的任务/队列。
- 可恢复瞬态错误在预算内且全部回到 listening。
- `running=False`、`session_attached=False`，无 8080/11435 测试进程残留。
- 浸泡二进制的哈希与最终交付清单完全一致。

## 9. 统一模型中心的产品与安全契约

该节约束 B2 的实现，不得另起一套重复模型中心：

1. Live 与 Code 从任一入口打开同一个设置中心。
2. 两者可勾选共享配置，也可分别选择本地/云端；例如 Live 本地、Code 云端互不影响。
3. 基础安装包不含模型权重；R1 附加包可选择安装目录并做 manifest/hash 校验。
4. 安装向导支持：检测现有 Ollama/GGUF、导入本地附加包、经用户确认后从 Hugging Face 下载兼容模型。
5. 下载前显示模型、来源、许可证、大小、预计磁盘占用和目标目录；过程中显示分阶段进度、速度、剩余量、暂停/继续/取消和失败恢复。
6. 默认禁止执行仓库自带任意代码，`trust_remote_code` 默认为 false；自动适配只允许已验证格式/架构白名单。
7. 模型安装失败必须回滚未完成注册，不破坏另一条 Live/Code 路由。
8. Ollama 未运行时向导负责检测和引导启动；未经许可不得静默下载或安装。

B2 必须产出模型路由契约、安装状态机、持久化 schema、安全审查和 D 盘验收证据，才可进入 v3.0.1 候选冻结。

## 10. P2–P5 的进入条件

### P2 记忆

- 先定义独立 MemoryManager/adapter 契约，不污染现有 Brain facts 和 PromptBuilder 指纹。
- Me Memory 独立 append-only 存储；We Memory 幂等合并；遗忘是可逆软降级。
- 用户必须能查看、删除、要求遗忘和导出；记忆属于用户。
- 真实遗忘策略至少 dry-run 两周后再启用，不把一周开发称为完成。

### P3 认知与 P4 身体

- 先登记生产事件契约：发布者、真实入口、payload、消费者、生命周期。
- 不依赖当前生产主线没有发布的 `agent_run.*` 或 `USER_PROMPT_SUBMIT`。
- 心、人格、身体共享一个状态 schema；不能让前后端各维护一套互相漂移的“生命状态”。
- P4 只用真实音量/播放电平驱动口型，必须支持 2D/无 WebGL 降级。

### P5 管家与进化

- 复用现有 action_policy/guard/agent 裁决源，不复制第二套权限系统。
- deny 永不可被 token、确认或成功次数覆盖。
- “成功若干次自动升级权限”禁止越过静态风险上限。
- 自生成 skill 必须经过静态检查、沙箱、测试、人工批准和可回滚发布；提示壳不算闭环。

## 11. 文档冲突的统一裁决

1. 以批判审查后的摘要/附录结论覆盖正文中已被否决的 Boundary token、双闸死锁和重复 HabitLearner 方案。
2. 用可控 stall 验证 watchdog，不采用“错误端口/关闭 Ollama 测超时”的旧方案。
3. `hardware_tested` 必须绑定 build hash、设备 ID、模型、时间戳；版本或设备变化后自动失效。
4. 版本一致性必须由生成/校验工具证明，不能只把一个 Python 常量命名为“单一版本源”。
5. 包管理器以仓库实际 `package-lock.json` 为准，不额外引入 pnpm 锁文件。
6. 任何测试、提交和缺陷数量都动态采集，不写死在长期计划里。
7. 产品可以呈现陪伴和生命感，但不得宣称 Javis 具有真实意识或灵魂。

## 12. 近期 2–4 周执行节奏

### 第 1 周

- 完成 P0-R Task 4、独立审查和全仓库测试归类。
- 在独立工作树完成 P0-O diagnostics/stall harness。
- 并行审计现有模型中心，完成六供应商目录、独立路由、附加包和 HF 状态机差距测试。
- 在独立工作树完成 P1.1 版本一致性、preflight 与发布纪律。
- 得到用户批准后，形成最终 v3.0.1 候选 main。

### 第 2 周

- 从 G 盘最终 main 构建唯一候选。
- D 盘完成 30 轮、重连、错误、三档 stall、打断、语料和持久化验收。
- 失败项只回工作树，以红线测试修复并重建同一候选链。

### 第 3 周

- 完成缺陷闭环后，对最终二进制执行 24 小时浸泡。
- 生成 P0 验收报告、指标 JSON、缺陷表、语料报告和 provenance 清单。

### 第 4 周

- 冻结通过浸泡的二进制，完成重装、升级、卸载复验。
- P0/M1 与 P1/M2 均通过后，再启动 P2；模型中心 B2 已纳入本轮 v3.0.1 候选，候选冻结后新增模型能力另立后续工作树，不插入已冻结二进制。

## 13. 完成定义

只有同时满足以下条件，才可向用户报告“语音问题已修复并可用”：

- 代码门禁与全量测试完成并审查通过。
- 构建前 diagnostics/stall harness 与模型中心 B2 门禁完成。
- 合入后的最终 source commit 可追溯。
- 安装包、runtime、manifest 和 SHA-256 一一对应。
- D 盘所有强制验收矩阵通过；consent-gated 外部下载仅可记录真实 PASS 或 `USER_DECLINED/NOT_RUN`，本地状态机仍须通过。
- 同一二进制完成 24 小时浸泡。
- 无残留测试进程/端口，报告中没有把未执行项写成 PASS。
