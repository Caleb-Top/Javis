# Javis 参考包可借鉴性审查

> 日期：2026-08-09  
> 范围：`D:\chatbox-main.zip`、`D:\Kimi-K3-main.zip`、`D:\Kimi-K3-feature-multimodal-ai-upstream.zip`、`D:\WaveBench-master.zip`  
> 方法：只读检查 ZIP 目录与关键源码/文档；未解压、未执行、未联网、未安装依赖。

## 1. 结论

四个参考包都不应整包并入 Javis。可借鉴的是经过重新设计和测试锁定的架构原则：

| 参考包 | 可借鉴 | 不直接采用 | 对应 Javis 阶段 |
|---|---|---|---|
| Chatbox | Provider 注册表单一数据源、模型目录多源合并与明确优先级、离线快照/缓存降级、能力元数据、配置迁移 | Electron 架构、整套 OAuth/MCP/UI 源码、把远端目录当作唯一真相 | P1-M、P1.1 |
| Kimi-K3 | 原生多模态与长上下文作为模型 capability；Code/Live 按能力选择模型 | 把超大权重放入基础安装包；未经硬件探测即推荐本地部署 | P1-M、P3、P4 |
| Kimi-K3 multimodal feature | 统一的文本/图片/视频调用门面、流式结果接口 | 示例实现的 URL 下载、本地文件读取、Base64 整体载入和生成接口；其安全边界不足，不能进入生产链 | P1-M、P3 |
| WaveBench | `plan → check/dry-run → approve → execute → verify → recover`、不可变配置、失败关闭、安全上限、受管账本与恢复 | 仪器驱动、SCPI、部署方式和领域代码 | P1-M 安装器、P1.1 发布、P5 Authorizer |

## 2. Chatbox：用于模型中心的架构原则

### 采用

1. 建立声明式 Provider 注册表：ID、显示名、协议类型、默认 endpoint、凭据类型、模型目录策略和 capability adapter 在一处注册。
2. 锁定模型目录优先级：用户保存项 → 已连接供应商 `/models` → 内置策展目录 → 构建时快照；网络失败不得清空已有目录。
3. 将模型元数据与路由分离：模型的视觉、工具调用、推理、上下文长度等 capability 只影响适配与 UI，不得偷偷改变 Live/Code 选中的 provider/profile。
4. 自定义兼容 Provider ID 必须全局唯一，不能覆盖内置 ID。
5. 模型目录刷新要去重、超时、有缓存版本，并允许用户手工输入兼容模型 ID。
6. 配置要有 schema version 和逐版本迁移，迁移失败保留原文件并进入可恢复状态。

### 不采用

- 不迁入 Electron 主进程或 React 组件；Javis 保持 Tauri + TypeScript + Python 的既有边界。
- 不照搬第三方 OAuth 或 token 处理；凭据存储必须先通过 Javis 自身威胁模型与 Windows 凭据方案审查。
- 不让在线 registry 覆盖用户明确选择；它只能补充目录和能力元数据。

## 3. Kimi-K3：作为能力目标，不作为默认本地包

`Kimi-K3-main.zip` 主要是模型说明、许可证和技术报告，不是可直接融合的桌面应用实现。对 Javis 有价值的是把模型能力表达成可查询契约，例如：

- `text`、`vision`、`video`、`tool_use`、`reasoning`、`long_context`；
- 每条 Live/Code 路由根据任务需要检查 capability，不根据品牌名硬编码；
- 不满足能力时给出明确选择或降级，不静默切换到另一条付费/本地路线。

Kimi-K3 的本地权重和运行要求不能进入 Javis 基础安装包。只有完成硬件探测、许可证展示、空间预算和用户确认后，才可作为可选模型来源出现。

## 4. Kimi 多模态示例：只借接口思想

示例提供了图片描述、OCR、视频理解、流式输出等统一方法，但不能直接进入生产，原因包括：

- URL 下载缺少私网/回环地址阻断，存在 SSRF 风险；
- 图片和视频缺少最大下载量、像素/时长、重定向次数和磁盘预算；
- MIME 主要信任响应头或扩展名，缺少 magic-byte 校验；
- Base64 整体读入会造成大文件内存放大；
- 本地路径读取缺少 Javis workspace/用户授权边界；
- 生成图片/视频的 API 与模型 ID 只能视为示例假设，不能当作供应商正式契约；
- 临时文件、流和异常后的清理证据不足。

Javis 若实现多模态，应在 Provider adapter 前增加统一 `MediaInputGuard`：来源授权、大小/时长、MIME + magic、网络目的地址、重定向、流式落盘、哈希、取消与清理全部先过门。

## 5. WaveBench：借鉴安装与授权纪律

WaveBench 的领域不是 AI 助手，但其高风险操作方法适合 Javis：

1. 计划与执行分离；计划可静态检查，执行前再次核验环境。
2. 默认 dry-run；所有写操作有明确安全上限，未知状态 fail-closed。
3. 插件/载荷有受管账本、摘要、版本范围、事务恢复和卸载语义。
4. 安装不隐式改业务配置；用户必须显式选择后才激活。
5. 重试次数有界，失败留下结构化证据，不把“命令启动”当作完成。

这些原则直接映射到 Javis 模型安装器：不可变 plan token、磁盘预算、staging、hash/magic/架构验证、原子 commit、route-scoped 激活、取消/回滚和恢复账本；也映射到 P5 的统一 Authorizer。

## 6. 纳入权威路线的具体动作

### P1-M

- Provider registry 成为供应商和模型目录的单一数据源。
- 内置快照、账户 `/models`、用户自定义 ID 按固定优先级合并。
- 安装器采用不可变计划、dry-run、审批、staging、验证、原子提交、回滚和恢复账本。
- 多模态只先建立 capability 与输入安全契约，不在本阶段扩大为完整媒体工作台。

### P1.1

- 构建时生成 provider/model snapshot 与 schema version；发布 manifest 记录其摘要。
- 离线候选必须能使用快照打开设置，不因目录服务不可用而卡住。

### P3/P4

- 在共享 schema 冻结后，把 vision/video/long-context 能力用于 Planner 路由和数字生命表达；不把具体供应商品牌写入认知内核。

### P5

- 将 WaveBench 风格的 plan/check/approve/execute/verify/recover 契约纳入统一 Authorizer；任何危险工具未知即拒绝或询问。

## 7. 使用边界

- 只借鉴思想和可验证契约，不复制参考项目源码。
- 引入任何第三方代码、模型或资产前，另做许可证、来源、依赖和安全审查。
- 未经用户明确同意，不下载模型、运行第三方代码或写入 D 盘。
