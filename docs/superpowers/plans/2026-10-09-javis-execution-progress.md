# Javis Life OS 执行台账

更新：2026-10-22。本台账调整执行顺序与验收粒度，不替代已批准的 L0-L8 规格。

## 进度与证据

| 阶段 | 状态 | 剩余工作 / 证据 |
| --- | --- | --- |
| L0-A / L1 | 已有实现 | 后续改动按影响范围回归 |
| L0-B | 待交付 | 资产、九态表达、口型、性能分级、桌面/移动视觉验收 |
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
