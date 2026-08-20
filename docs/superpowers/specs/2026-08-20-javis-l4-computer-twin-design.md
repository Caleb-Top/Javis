# Javis L4 Computer Twin 设计规格

**日期：** 2026-08-20  
**状态：** 执行规格  
**依赖：** L0-A、L1、L2 主体合同、L3 多人边界

## 1. 目标

L4 让 Javis 在明确授权、最小采集和短时有效的前提下理解自己的运行环境。Computer Twin 是“受治理的当前环境事实投影”，不是持续录屏、用户监控、无限工作区索引或第二记忆系统。

用户体验出口：Javis 能说明当前获得了哪些环境权限、哪些事实仍新鲜、哪些已经未知；能在任务所需时理解前台应用、工作区状态和设备健康；撤销授权后立即停止观察，且不会从旧 OCR 或旧截图继续推断。

## 2. 非目标

- 不持续保存屏幕、摄像头、麦克风或窗口历史。
- 不将 OCR 全文、完整路径、进程命令行或文件正文默认写入记忆。
- 不让本地 capability token 代替用户的采集同意。
- 不让模型创建、扩大、延长或恢复环境 grant。
- 不把一般窗口变化提升为 Life 注意抢占事件。
- 不创建独立常驻 Rust 监控守护进程。
- 不把旧 `PerceptionService` 扩成权限、事实、保留和模型上下文的混合权威。

## 3. 架构

```text
Governed Source
  -> Runtime capability
  -> EnvironmentGrant
  -> Source adapter
  -> Minimizer / Privacy classifier
  -> EnvironmentObservationV1
  -> EnvironmentReducer
  -> EnvironmentSnapshotV1
       -> EnvironmentContextProjector -> PromptBuilder (read only)
       -> EnvironmentAttentionSelector -> governed Life safety cue
       -> thin audit event
```

`EnvironmentService` 是环境事实的单写者。采集适配器只能提交观察；模型、前端、MemoryService、LifeService 与工具均不能直接写快照。

## 4. 权限双门

### 4.1 Runtime capability

新增 scopes：

- `environment.read`
- `environment.observe`
- `workspace.read`
- `screen.capture`
- `camera.capture`

capability 证明请求来自当前受信本地客户端、绑定当前 runtime boot，并有短 TTL。它不证明用户同意当前来源、范围和用途。

### 4.2 EnvironmentGrant

每次观察还必须具有服务端存储的 grant：

- `grant_id`
- `owner_subject_id`
- `source_kind`
- `scope`：窗口、工作区根、设备或来源实例
- `purpose`
- `duration`：`once | session | persistent`
- `issued_at_utc`、`expires_at_utc`
- `foreground_only`
- `model_visibility`：`none | local_only | governed_cloud`
- `media_egress_allowed`
- `revoked_at_utc`

一次 grant 消费后失效；session grant 在会话或 runtime 结束时失效；persistent grant 仍需可见撤销。模型只能读取 grant 状态，不能调用写接口。

## 5. 数据合同

### 5.1 EnvironmentObservationV1

- `schema_version = 1`
- `observation_id`
- `runtime_boot_id`
- `source_event_id`
- `source_kind`
- `subject_kind`
- `subject_key`
- `attributes`：按 source allowlist 的标量与短数组
- `observed_at_utc`
- `valid_until_utc`
- `sequence`
- `confidence`，范围 `[0, 1]`
- `privacy_class`
- `retention_class`
- `grant_id_hash`
- `provenance`

未知字段、非规范时间、未来过远时间、无 grant、错误 boot、重复 ID 和倒退 sequence 均 fail closed。

### 5.2 EnvironmentSnapshotV1

- `schema_version = 1`
- `revision`
- `runtime_boot_id`
- `generated_at_utc`
- `expires_at_utc`
- `facts`
- `stale_keys`
- `source_health`
- `permission_revision`

事实主键为 `(subject_kind, subject_key, attribute)`。新序列替换旧值；TTL 到期后删除有效值并将 key 标记 stale/unknown。整个瞬时快照不跨重启恢复。

## 6. 来源与保留

| 来源 | 默认触发 | TTL | 保留 |
|---|---|---:|---|
| 前台应用类别 | 窗口变化，最多 2 秒一次 | 5 秒 | session |
| 进程与 CPU/内存等级 | 请求前或 30 秒低频 | 30 秒 | operational |
| 磁盘与设备健康等级 | 启动和 5 分钟 reconciliation | 5 分钟 | operational |
| 工作区元数据 | 自有 API 事件和 30 秒局部校验 | 60 秒 | session |
| 屏幕 OCR/对象事实 | 用户单次或 session grant | 10 秒 | never_persist |
| 原始屏幕/摄像头帧 | 采集管线内部 | 0 秒 | never_persist |
| 用户定义设备/空间别名 | 用户显式修改 | 直到撤销 | continuity |

原始媒体不能进入 EventBus、LifeJournal、SessionEventStore、MemoryService 或错误日志。OCR 只提交最小任务事实，不提交全文。

## 7. 模型上下文

`EnvironmentContextProjector` 每回合从未过期事实中最多选择 8 项，生成 `JAVIS_ENVIRONMENT_CONTEXT_V1`，UTF-8 总预算不超过 2048 字节。

允许：应用类别、项目别名、设备健康等级、可用性、变更数量、freshness 与权限摘要。

禁止：文件正文、OCR 全文、完整路径、窗口完整标题、进程命令行、原始媒体、token、grant ID、其他主体私有事实。

云端上下文还需 `model_visibility=governed_cloud`；媒体外发需要单独 `media_egress_allowed`，不能从文本上下文权限推导。

## 8. Life 与记忆边界

一般环境事实只进入当前模型上下文，不改变内稳态和关系。只有确定性、allowlist 的 safety cue 可以经 `EnvironmentAttentionSelector` 投射到 L1，例如授权突然撤销、设备资源进入危险等级或正在执行的工作区消失。

MemoryService 只能保存用户确认的环境别名、共同项目决定和经 L2/L3 治理的经历引用；不得保存瞬时快照、OCR 或进程列表。

## 9. 现有能力迁移

- 复用 EventBus、有界序列、Life 隐私类型和 PromptBuilder 只读投影模式。
- 复用 Win32 截图、窗口枚举、OCR、YOLO 与 workspace 底层实现，但必须置于 capability + grant + minimizer 之后。
- 现有截图按钮不得继续直接绕过授权；Tauri 命令需接收服务端签发的短期 capture permit。
- 现有 VLM 远端 `base_url` 默认只允许 loopback；非 loopback 需要独立 media egress grant。
- 现有 SessionEventStore 不再持久化 OCR 全文；旧记录进入迁移审计，不自动转为 L4 事实。

## 10. 故障与恢复

- 来源故障更新 `source_health`，不伪造空结果为事实。
- grant 撤销立即清除该 grant 派生的 facts，停止采集并增加 permission revision。
- runtime 重启后所有 session/once grant 失效，瞬时事实为空；persistent grant 需重新激活来源。
- 时钟漂移不决定新旧，服务端 sequence/revision 决定顺序。
- reducer、投影或模型失败不能阻断会话、Life L0/L1 或用户撤销权限。

## 11. 验收

1. 无 capability 或无 grant 的每个环境入口均拒绝。
2. once/session/persistent、前台限制、到期与撤销矩阵通过。
3. 原始媒体与 OCR 全文在数据库、事件、日志、提示词和崩溃恢复中零命中。
4. stale 事实变 unknown，旧 sequence 和旧 boot 不能复活。
5. 工作区路径限制在授权根，符号链接和遍历逃逸被拒绝。
6. 云模型没有 media egress grant 时不能收到图片。
7. 环境服务关闭时，身份、对话、语音和 L1 仍正常工作。

