# Javis Life OS 执行台账

更新：2026-10-22。本台账调整执行顺序与验收粒度，不替代已批准的 L0-L8 规格。

## 进度与证据

| 阶段 | 状态 | 剩余工作 / 证据 |
| --- | --- | --- |
| L0-A / L1 | 已有实现 | 后续改动按影响范围回归 |
| L0-B | 已有代码，待验收交付 | 已定位 AvatarSurface/Scene/Controller、Lightform、Mixer、SpeakingEnvelope、性能与回退、预览及对应测试；仍需逐项检查实际行为、视觉和实机证据。D 盘验收文档为 NOT EXECUTED |
| L2 T1-T13 | 已交付基线 | 后续主体和删除变更需保持 ACL 与防复活语义 |
| L3 T1-T3 | 已提交 | f70ae63a：显式主体绑定 |
| L3 T4 | 已提交，仍需集成审计 | 3d7a006a：代际、围栏、交接；已跑自动测试不等于所有竞态及硬件验收完成 |
| L3 T5 | 进行中 | 准入与召回门 → 持久决策 → 来源提取和删除抑制 |
| L3 T6-T8 | 待完成 | 关系事件、共同确认、交流指导 |
| L3 T9-T12 | 待完成 | 授权隔离门、主体删除、API/UI、发布证据 |
| L4 | 部分实现 | T1-T5 历史完成记录；剩余项需逐条核验 |
| L5 | 部分实现 | T1-T5 历史完成记录；剩余项需逐条核验 |
| L6 | 部分实现 | T1-T4 历史完成记录；剩余项需逐条核验 |
| L7 / L8 | 部分实现 | 按各自正式计划核验；当前工作区有既存 L8 修改 |
| 发布 | 未完成 | 全量集成、安装升级、真实硬件验收与 GitHub 同步 |

## 执行优化

1. 每个切片交付可审查代码、对应行为测试和明确未完成项。模块存在、单测通过、产品闭环分别记录。
2. 主线程负责共享 MemoryService/store 集成；子智能体负责独立审查、测试设计或独立文件。共享文件只设一个写入者。
3. L3-T5 分三片：A 固定 predicate/typed value/召回资格；B 原子确认、拒绝、纠正、过期及幂等收据；C completed evidence 提取、跨时证据、冲突、删除 suppression。
4. T6 可在 T5 合同稳定后并行；T7 依赖 T4/T6；T8 依赖 T4/T5/T6；T9 必须先于 T10/T11。L0-B 可独立推进资产与渲染，不阻塞关系内核。
5. 每片先跑直接受影响测试。共享合同/数据库/生命周期变化再跑邻接矩阵；发布节点跑全量 Python 与 App。没有新改动或新风险不重复同组测试。
6. 测试失败先定位具体用例，避免反复运行整套。硬件、安装包、压力竞态未执行时明确标记 NOT EXECUTED。
7. 源码区 G:/Javis 当前允许集成；已有未提交改动保留。新探索性并行实现使用隔离工作区；逐文件提交，不将其他阶段未验收修改混入提交。

## 当前切片

- [x] T5-A 固定提议字段和值域；数据库搜索和缓存召回均检查普通 prompt 资格，按 session/generation/binding 划分缓存。
- [ ] T5 写入边界统一治理：通用 put_item 仍需接入来源真实性和决策收据验证；T5-A 只封住普通搜索与 prompt，不代表数据写入已完整治理。
- [x] T5-B1 确认/拒绝单写者事务、修订 CAS、幂等决策收据、拒绝来源抑制；当前绑定和 relationship.manage 校验。
- [x] T5-B2a 过期转换：启动及单写者后台批量清理、FTS 退出、保留决策历史；禁止终态复活与原地改值/主体/来源。
- [x] T5-B2b 纠正事务：新候选、旧记录 superseded、派生边和幂等收据原子提交。
- [ ] 完整通用写入治理与原始证据验证。当前来源检查只验证 active completed episode；原始用户自述证据验证仍属 C，确认/纠正入口尚未对外开放。
- [ ] T5-C 验证真实来源并接入单写者；删除/replay 不复活。
- [x] T5-C 首批原文提取接入：明确记住 + 第一人称受控偏好，中英文全句匹配；与 episode/receipt/cursor 原子提交，跨回合矛盾退出普通 prompt。
- [x] T5-C 删除 claim 来源抑制：围栏事务写入无内容抑制记录，清理后换 ID 写入与重启重放被拒绝；不等于完整来源提取已交付。
- [ ] T4 集成审计：重启时未完成围栏恢复、旧 WS replay、播放请求归属与迟到音频。

## 验收原则

只有带来源、actor 可见、未过期、无未解决冲突的 explicit/confirmed claim 可供普通 prompt 使用。模型提议不能指定最终 status、audience、confidence 或授权。独立确认不替代来源真实性检查。

## 2026-10-10 验证记录

- 独立子智能体完成只读审查，确认写入治理、来源抑制和持久状态机仍是 T5 剩余工作。
- 定向自动测试：`tests/test_user_model_claims.py tests/test_memory_acl_recall.py tests/test_memory_store.py`，46 passed。
- 覆盖提议越权字段、predicate/值域、推断/观察/敏感/冲突/第三方 Claim、缓存到期、代际缓存隔离、搜索 limit 前过滤。
- 第一批支持固定交流风格、语言、确认风格、时间窗、受控话题边界、回答格式和单位。自由文本偏好、goal 与身份类 predicate 尚未开放，不视为完整 predicate 交付。
- T5-B/C、API/UI、真实硬件与安装升级：NOT EXECUTED / 未完成。

### T5-B1 增量

- `DecideClaim` 经 MemoryService writer 调用单一数据库事务；确认生成 confirmed 记录和 decision receipt，拒绝持久化 source suppression。
- 同一决策精确重放不再增加 revision；变更决策载荷冲突；旧 generation、无 scope、guest-present 拒绝。
- 通用 put_item 对被拒绝来源的新 claim ID 也拒绝，数据库重新打开后仍有效。
- 存储/服务邻接验证 57 passed；新增重开数据库和收据故障注入后 Claim 专项 30 passed。
- 故障注入证明 receipt INSERT 失败时 claim revision、status、FTS 同事务回滚。完整删除 suppression、纠正、原始证据提取仍未交付。

### T5-B2a 增量

- 过期扫描复用同一 writer：启动清理一批，后台每 5 秒最多 128 条；普通召回的即时 expiry 检查继续生效。
- confirmed 转 expired 清当前确认时间，保留 confirmation_event_id 和独立 claim_decisions 历史；重复扫描不增加 revision。
- 通用写入拒绝 rejected/expired/superseded/deletion_fenced 回到可用状态；修改 value、主体或来源必须新建纠正记录。
- 存储/服务/Claim 邻接 61 passed；追加原地改写用例后 Claim 专项 36 passed。
- 纠正事务、原始证据验证与完整删除抑制仍为下一步；L0-B 与 L3-T6..L8 的剩余范围不变。

## 2026-10-22 执行调整与增量

- 用户要求缩短耗时、减少测试但保持交付范围：后续按改动运行最小行为测试，不重复此前已通过的大矩阵；全量验证集中在阶段集成和发布门。
- 纠正复用 correction_receipts，当前绑定校验与决策路径共用；成功创建新 candidate 并 supersede 旧记录，建立旧 claim/来源 episode 到新记录的边。
- 精确重放返回同一新记录；同 key 改值冲突；收据写入失败完整回滚新旧记录和派生边。
- 已执行纠正与共享授权路径 4 项定向测试，3.72 秒；另补旧 generation 拒绝测试。完整 T5 仍需来源提取、写入治理、删除/replay suppression。

### 删除来源抑制增量

- claim 删除围栏复用拒绝来源抑制表及单写者事务；仅保留 subject/predicate/source ID 和原因，不保留被删值。
- derived_only 删除 claim 保留来源 episode；即使更换 claim ID，也不能由该来源重新生成同 predicate 的可用记录。清理目标记录和重开数据库后仍有效。
- 定向验证 3 passed / 3.54 秒，补充 suppression INSERT 故障回滚 1 passed / 1.48 秒。未运行全量测试。
- 尚未证明完整 T5：原始用户内容核验、通用写入治理、跨时证据与矛盾规则仍待完成；此改动也不替代 T10 主体删除完整验收。
- 并行证据审查完成：提取接入 `_reconcile_terminal_page` 的已验证 completed evidence；episode、claim、派生边、receipt 与 cursor 必须在 `project_episode` 同一事务提交，禁止提交 receipt 后单独写 claim 导致崩溃漏提取。来源使用 complete user message，不能以 assistant 摘要证明用户自述。

### 原始会话提取接入增量

- 已接入 completed evidence 原文到 ClaimProposal，再由单写者分配 owner-private、状态和敏感级别；支持首批 verbosity/directness/formality/units/response_format/language 的中英文明确记忆句式。
- 只匹配 episode 已引用的完整 user message；拒绝引号、第三方、助手内容、额外指令、guest-present 及缺失显式无 guest 声明。历史弱身份数据不自动升级。
- episode、claim、派生边、receipt、cursor 同事务写入；故障注入后全部回滚，恢复重试生成一次。已有抑制来源跳过 claim，不阻塞 episode 处理。
- 同主体同 predicate 的不同值互相标记 contradiction，新记录 candidate，双方普通 prompt 不可用；不自动覆盖已有偏好。
- 并行智能体只新增提取模块与专项测试，74 passed；主线程端到端原文提取/冲突/回滚/重试 2 passed，最终运行 2.31 秒。未重复全量测试。
- 剩余范围不变：自由表达与其余 predicate、敏感/边界候选、三条跨时 observed、inferred、矛盾解决与通用写入治理尚未完整交付；T5 不标记完成。

### 工作习惯与边界提取增量

- 补齐当前 registry 中的确认风格、专注时段、do_not_mention/do_not_store/ask_before_topic 三类边界提取；中英文全句匹配，受控六类话题，不接受私人自由文本。
- 非法时间窗返回无提议，不阻塞会话处理；跨午夜合法。多条原文的同类边界话题可合并，同一工作习惯的矛盾值不猜测取舍。
- 边界仅落库为 sensitive candidate，未确认前不能进入普通 prompt；这不是隐私策略执行或删除历史内容，后者仍需 T8/T10 的实际控制链路。
- 并行智能体写入后中断，主线程接手审查并完成验证：新增提取用例 318 passed / 0.63 秒，旧 74 项未重复；五项持久化策略检查 5 passed / 4.74 秒。未运行全量测试。
- L0-B 状态依据当前文件校正为已有代码待验收：渲染、表达、口型、性能、回退与预览模块存在，但不能仅凭文件存在判定交付，D 盘实机仍 NOT EXECUTED。
- T5 余项仍包括完整写入治理、observed/inferred、矛盾解决、自由表达与更广的 predicate；本轮不缩减其范围。

### 矛盾确认与持久收据增量

- 独立确认逐一检查 contradiction 引用：只有同主体、同 predicate、已 rejected 且具有本人 reject 收据的冲突项可解除；活跃、缺失、越主体或无收据项继续阻止确认。
- 已确认记录遇到后续冲突后，也可在拒绝冲突项后重新独立确认；不因后台状态变化自动恢复 prompt 资格。
- schema v8 为 claim_decisions 增加 resolved_claim_ids_json，保存本次解除的引用；清引用、更新记录和写收据同事务。过期/删除冲突的其他解决策略尚未交付。
- 三项确认/重确认/回滚定向检查 3 passed / 2.90 秒；v7 旧收据及四条既有升级路径 5 passed / 3.92 秒。未运行全量测试。
- T5 完整通用写入治理、observed/inferred 和其他矛盾解决仍未完成；L0-B/L4-L8 范围不变。

### L0-B 实际浏览器预览增量

- 检查真实预览而非仅用 DOM fake：已有媒体查询保证默认小屏布局，未重复修改 CSS。
- 修复 speaking 状态点击 Interrupt 后 applyIntent 再次启动 speaking envelope：现在同步转 listening、gaze=user、关闭 speaking 控件并刷新状态。
- 新增可复用 scripts/verify_avatar_preview.cjs，通过 Playwright + Edge 截图与像素差检查 1280x900 / 390x844：彩色像素各 3383、状态变化像素各 1243，interrupt=true、overflow=false、pageerror=[]。
- 本地截图与结果：G:/Javis/tmp/avatar-preview-evidence/preview-1280.png、preview-390.png、results.json。浏览器验证约 4.65 秒，预览模型四项测试通过约 0.32 秒。
- 预览运行于 http://127.0.0.1:5181/?avatar-preview=1。此次只是默认 procedural3d smoke 与打断修复，不代表所有九态/资产/档位/背景/尺寸/真实 GPU 性能或 D 盘安装包验收完成。

### L0-B WebGL 故障预览增量

- 预览接入 AvatarSurface 的真实 context-lost/restored 回调，与模拟开关分别维护；真实丢失时进入可见兜底，恢复后重新展示渲染，模拟开关不能解除真实故障。
- 回调和加载失败分支均检查 dispose/重建代次，旧异步结果不再覆盖当前预览状态。
- 使用 WEBGL_lose_context 扩展真实触发丢失/恢复，两尺寸恢复后彩色像素各 3394，无页面异常、无页面横向溢出；截图已查看。扩展模拟不等价于真实 GPU 驱动故障或 Windows WebView2 验收。
- 浏览器定向验证 4.67 秒；TypeScript noEmit 通过。未跑全量测试。证据仍在 tmp/avatar-preview-evidence，包括新增 context-lost-390.png 和 context-lost-1280.png。
