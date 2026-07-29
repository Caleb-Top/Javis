# Javis Current Architecture

更新日期: 2026-07-27  
适用版本: 当前 `D:\Javis` 工作区  
状态依据: 当前代码、测试模式 Runtime、`/api/blueprint/coverage` 和 156 项测试

## Current Status

Javis 是一个 Windows 本地 AI 助手，当前由 Python Agent 后端、Web 调试界面和正在产品化的 Tauri 桌面 App 组成。

2026-07-27 验证状态:

- 156 项 unittest 全部通过。
- 185 个一方产品 Python 文件 AST 解析无错误。
- 测试模式下 Blueprint 五系统覆盖率为 `1.0`。
- Kernel、Control、Perception、Memory、Evolution 均已挂载并报告 `running`。
- App Phase 2、3、4 已实现：Live 产品化、信息与控制抽屉、真实 Code Surface。
- App V1-E 已实现 Sidecar 所有权、Javis 身份探测、托盘、关闭隐藏、AppData 日志、CSP、错误恢复和诊断页。
- App Phase 6 已实现安全预览启动、只读构建预检、正确产品打包边界和首次启动隐私/路径 Surface。
- `RuntimeStateCoordinator`、有界请求队列、FTS5 搜索、权限/感知面板、冲突安全编辑和可取消审计终端已经接通。
- Tauri 尚未正式构建；Node 22 与 Rust 1.97 可用，Rust 源码已由 `rustfmt` 解析，但 App `node_modules` 与离线 Tauri crate 缓存缺失，本轮没有安装或下载依赖。

## System Context

```text
用户
├─ Tauri App / Live Orb
├─ Web 调试界面
├─ 语音、屏幕、相机和文件
└─ Gateway 消息入口
        |
        v
FastAPI + WebSocket (main.py)
        |
        v
JarvisRuntime (core/runtime.py)
├─ Agent / LLM / Provider
├─ Tool Registry / Guardrails
├─ EventBus / SessionEventStore
├─ Control
├─ Perception
├─ Memory
└─ Evolution
```

前端只消费结构化 API 和事件，不直接装配 Agent 内核。模型 Provider 只是推理通道，不拥有系统状态、工具、记忆或权限。

## Runtime Kernel

核心装配点是 `core/runtime.py` 中的 `JarvisRuntime`。

Runtime 持有:

- `brain`: 长期知识接口。
- `learner`: 学习与知识吸收接口。
- `registry`: Tool Registry 与权限 Guard。
- `llm`: Provider 客户端。
- `engine`: 推理路由与主备恢复。
- `agent`: ReAct/计划执行主体。
- `event_bus`: 结构化进程内事件总线。
- `event_store`: SQLite 事件与候选记忆存储。
- `subsystems`: Control、Perception、Evolution 等生命周期对象。

关键边界:

- `create_runtime(root, startup_side_effects)` 是统一装配入口。
- `register_always_on_tools()` 保证核心工具不因技能切换消失。
- `sync_permission()` 是权限变更统一入口并发布事件。
- `register_event_store()` 将 EventBus 持久化到事件库。
- `register_subsystem()` 统一启动和登记子系统。
- `get_runtime_status()` 为 App 和诊断提供内核状态。

`main.py` 仍承载较多路由，但不再手工创建 Brain、Registry、Agent 和 LLM。

## Five-System Architecture

| 系统 | 当前后端状态 | 主要实现 | App 侧状态 |
|---|---|---|---|
| Kernel | running | `core/runtime.py`、`core/events.py`、`core/tool_registry.py` | `/api/runtime/status` 已可读，尚未形成完整状态抽屉 |
| Control | running | `control/command_tasks.py`、权限、root token、fuse、rollback | API 已有，权限确认和终端 UI 尚未完整接通 |
| Perception | running | `perception/service.py`、OCR、YOLO、VLM、视频分析 | API 已有，屏幕/相机控制和隐私 UI 尚未完整接通 |
| Memory | running | EventStore、FTS5、候选记忆、激活和程序化物化 | API 已有，App 搜索与来源解释 UI 尚未完成 |
| Evolution | running | 候选池、验证门、性能记录和回归回滚 | 后端闭环存在，V1 不在首页暴露自动激活入口 |

测试模式覆盖证据:

- Kernel: EventBus、8 个 always-on 工具、Runtime 持有 Agent。
- Control: 审计命令任务、root 会话令牌、手动熔断和持久回滚存储。
- Perception: OCR、VLM、YOLO、本地视频变化检测和结构化事件。
- Memory: SQLite 事件、FTS 召回、候选审核、Brain 同步和工作流物化。
- Evolution: staged 验证门、质量/延迟基线和回归回滚。

## Agent and Provider Flow

主流程:

```text
输入 -> Agent -> LLM/Provider -> 工具调用 -> Tool Registry
     -> Guard/Control -> 执行 -> 结构化结果 -> EventBus
     -> Memory/Reflector -> 文本或语音输出
```

Provider 位于 `providers/`、`core/llm_client.py` 和 `core/engine.py`。配置支持本地模型和 OpenAI 兼容云 API。云 API 缺少 Key 时，客户端进入 not-ready 状态而不是导致进程启动崩溃。

工具协议:

- DeepSeek 等支持原生工具调用的 Provider 使用结构化 function calling。
- 不可靠的本地兼容接口可以走文本工具协议。
- 工具函数接受常见参数别名和额外参数，降低模型参数漂移造成的失败。
- 工具结果通过 WebSocket 返回 `success/data/error/image` 等结构化字段。

## Tools, Permissions, and Safety

工具来自 `tools/`、`tools_lib/`、技能和扩展子系统。

安全层包括:

- `core/tool_guardrails.py` 的权限等级、工具风险和循环防护。
- `control/command_tasks.py` 的审计命令、工作区约束、root token 和 fuse。
- 文件与工作区路径保护。
- 上传文件名净化。
- Skill/Tool 名称校验。
- 沙箱后端检测和危险命令过滤。
- 回滚点与大文件持久回滚存储。
- 工具调用、权限和生命周期事件写入 EventBus。

App 不应直接发送任意 shell 字符串。Code Surface 必须通过受控命令任务展示命令、风险、工作目录、状态、输出摘要和退出码。

## Memory Architecture

记忆分为四层:

1. 工作记忆: 当前请求、会话卡片、短期工具结果。
2. 情景记忆: 带时间线的会话、事件和工具调用。
3. 语义记忆: 用户偏好、稳定事实、规则和 Brain 知识。
4. 程序记忆: 可复用流程、工作流和技能候选。

主要实现:

- `memory/session_db.py`: EventStore、候选记忆和进化候选持久化。
- `memory/indexer.py`: FTS5 会话、事实、经验、情景和程序记忆索引。
- `memory/consolidation.py`: 原始事件转为待审核记忆候选。
- `memory/activation.py`: active 语义候选同步到 Brain。
- `memory/procedural_materializer.py`: active 程序候选生成带版本和去重哈希的工作流。
- `memory/search_api.py`: 会话与全局搜索。

关键 API:

- `/api/memory/events`
- `/api/memory/recall`
- `/api/memory/search`
- `/api/memory/candidates`
- `/api/memory/apply-active`
- `/api/memory/materialize-procedural`

## Perception Architecture

原则: 文本模型不直接接收未经处理的原始媒体；本地感知层先把输入转为结构化事件。

实现:

- `perception/service.py`: 统一感知编排和 `PerceptionEvent`。
- `perception/adapters/ocr.py`: OCR。
- `perception/adapters/yolo.py`: 对象/UI 检测。
- `perception/adapters/vlm.py`: 本地 VLM 描述门。
- `perception/video.py`: 变化检测、分段和长视频摘要。

输入可以来自图片、屏幕、相机和视频。输出包含摘要、OCR 文本、对象、置信度、来源和时间等字段，然后写入 EventBus。

关键 API:

- `/api/perception/ingest`
- `/api/perception/image/analyze`
- `/api/perception/screen/analyze`
- `/api/perception/camera/analyze`
- `/api/perception/video/analyze`

## Evolution Architecture

Evolution 只通过受控状态机提升能力:

```text
candidate -> staged -> active
    |           |         |
 rejected   validation  regression rollback
```

`evolution/engine.py` 从重复成功路径中发现候选，`evolution/service.py` 管理生命周期。候选包含证据、置信度、风险和结构化提案。

当前后端已有:

- 候选发现和持久化。
- staged 验证门。
- 静态/沙箱验证接口。
- active 性能、失败率、延迟和质量记录。
- 回归触发回滚。

V1 App 默认只展示可审核状态，不允许未经确认的能力静默激活。

## Voice, Gateway, and Scheduling

语音:

- `voice/stt.py`: 本地转写入口。
- `voice/realtime.py`: WebSocket 语音会话、分块转写和 TTS 协调。
- `voice/tts.py`: Edge-TTS 与缓存。
- App 的 `VoiceCapture.ts` 使用 MediaRecorder，将音频发送到现有 `voice` 协议。

Gateway:

- `gateway/` 提供 Telegram、WeChat、Slack 等适配器、消息中继和统一状态。
- Gateway 是附加入口，不替代 Runtime 权限和工具边界。

调度:

- `tools/cron_scheduler.py` 提供用户可配置任务、历史和文件锁。
- 调度任务仍必须经过工具权限和审计边界。

## App Boundary

App 使用 Tauri v2 + TypeScript + Vite，目录为 `app/`。

已完成:

- App 工程骨架与 Tauri 配置。
- Live Orb 首屏和 8 个视觉状态。
- 无依赖静态预览。
- `/ws` 和 `/api/status` 桥。
- MediaRecorder 语音桥。
- App-first Python 启动脚本。
- 打包包含/排除/外置清单和 13 项测试。
- 单一 `RuntimeStateCoordinator` 与 request-scoped 终止规则。
- 有界、去重请求队列和有限 WebSocket 重连退避。
- 可收起状态栏、两行字幕、1-6 行输入和 `760x560` 到 `1280x800` 响应式布局。
- 会话/记忆 FTS5 搜索抽屉与状态、任务、权限、感知控制抽屉。
- 授权工作区文件树、编辑器脏状态、外部修改冲突保护和保存错误状态。
- 异步审计命令任务的启动、轮询、输出、退出码和进程树取消。
- Phase 2/3/4 防循环监督器与 AppData JSONL 日志。
- `SidecarManager` 直接持有 `python -u main.py` 的实际 PID，不调用会按端口清理进程的 `start.py`。
- `/api/status` 返回 `service=javis`；8080 上的非 Javis 服务只报告冲突，不终止未知进程。
- 托盘显示、暂停、受限重启、诊断和退出；窗口关闭默认隐藏，明确退出只停止 App 自有 Sidecar。
- AppData 10MB 轮换脱敏日志、Sidecar stdout/stderr、严格本地 CSP、全局错误恢复和诊断面板。
- Phase 5 与 release-verification 防循环监督阶段。
- Phase 6 与 build-readiness-verification 防循环监督阶段。
- 预览启动器与 Tauri Sidecar 均直接启动 `python -u main.py`，并只复用带 `service=javis` 身份的后端。
- 打包清单包含 `blueprint/`、`tools/` 等 Runtime 必需模块，排除旧 `start.py` 和大型/用户数据目录。
- 首次启动 Surface 说明 AppData、模型、工作区和原始媒体默认隐私，并可从设置重新打开。

待完成:

- TypeScript 正式类型检查与 Tauri 构建；当前完成 28/28 Node 语法检查、Rust 解析和浏览器视觉验收。
- 编译后的真实托盘/Sidecar 端到端验证、快捷键、签名和安装包。

App 的不可变体验要求见 `docs/superpowers/specs/2026-07-27-javis-app-v1-master-design.md`。

## Data and Runtime Paths

当前项目根目录是 `D:\Javis`。运行数据逐步外置到:

```text
%APPDATA%\Javis\config
%APPDATA%\Javis\memory
%APPDATA%\Javis\logs
%APPDATA%\Javis\cache
%APPDATA%\Javis\skills
%APPDATA%\Javis\sessions
D:\JavisModels
D:\JavisWorkspace
```

App 安装包不包含 `venv/`、`Lib/`、`python-embed/`、模型、数据集、`.git/`、缓存、日志和用户记忆。

## Implemented, Partial, and Deferred Work

后端已实现:

- Runtime、五系统、Provider、工具、权限、记忆、感知、进化、语音、Gateway、Cron 和自动更新基础。
- 关键 API、WebSocket 和测试隔离。

产品集成中:

- App 正式编译、状态协调、权限确认、搜索、Code Surface 和发布流程。
- `main.py` 路由进一步模块化。
- 运行数据全面迁移到 AppData。

后续版本:

- 子代理更深的主循环协调。
- 显式 `/learn` 用户入口。
- 数字人、悬浮投影和更完整的本地多模态体验。

## Source-of-Truth Rules

当前信息读取顺序:

1. `README.md`: 项目入口。
2. 本文件: 当前后端与系统架构。
3. `docs/JAVIS_OPERATIONS.md`: 启动、测试和故障排查。
4. `docs/JAVIS_CURRENT_AUDIT.md`: 最近一次检测证据。
5. App V1 规格: 桌面产品不可变要求。
6. `logs/JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md`: 当前路线与恢复总账。

代码、测试和运行态证据优先于历史描述。状态只能在实现存在且验证通过后写为完成。
