# Javis Master Plan and Project Ledger

建立日期: 2026-07-27  
最近更新: 2026-07-27  
用途: 当前状态、App 路线、恢复入口和重大验证记录

## Current Truth

Javis 不是重新开始。Python Agent 后端和五系统 Runtime 已经运行，当前主线是把现有能力收束到稳定的 Tauri 桌面 App。

当前验证:

- 113 项 unittest 通过；清理前基线为 111 项，本轮新增 2 项缺陷回归测试。
- 183 个一方产品 Python 文件 AST 错误为 0。
- Blueprint 五系统在测试模式下均为 `running`，覆盖分数 `1.0`。
- App Phase 1 壳、Live Orb、语音桥、后端桥和启动脚本已存在。
- App 正式构建仍受 Node/Rust/Cargo 环境限制；没有自动安装依赖。

## Canonical Documents

只使用以下当前文档:

1. `README.md`: 项目入口。
2. `docs/JAVIS_CURRENT_ARCHITECTURE.md`: 当前代码与系统架构。
3. `docs/JAVIS_OPERATIONS.md`: 启动、测试、数据和故障排查。
4. `docs/JAVIS_CURRENT_AUDIT.md`: 最近一次完整检测证据。
5. `docs/superpowers/specs/2026-07-27-javis-app-v1-master-design.md`: App V1 不可变要求。
6. `docs/superpowers/specs/2026-07-27-javis-document-log-cleanup-and-audit-design.md`: 文档/日志清理边界。
7. 本文件: 总计划与恢复账本。
8. `logs/README.md`: 日志入口。

2026-07-27，34 份历史架构、自检、状态和计划材料被提炼进上述权威文档后批准删除。旧材料不再作为状态来源。

## Immutable App Requirements

- 首页必须是 Siri-like Live Orb，不是聊天网页或 Code workspace。
- 语音优先，文字输入始终可用。
- Orb 显示 `idle/listening/thinking/speaking/executing/blocked/error/offline`。
- Code Surface 默认隐藏，只在任务需要或用户主动打开时出现。
- Python FastAPI/WebSocket 后端继续作为核心能力来源。
- `web/` 保留为调试和回归入口。
- Windows 是第一平台，Tauri v2 是桌面壳。
- 大型 Python 环境、模型、数据集、Git 历史和缓存不进入安装包。
- 未经批准不安装依赖、不下载模型。
- JARVIS 深色、青紫能量、中心动态 Orb 的视觉方向不变。
- 文本、状态栏、抽屉、按钮和 Code Surface 在窗口缩放时不能重叠或溢出。

## Current Architecture

```text
Tauri App / Web / Voice / Gateway
              |
      FastAPI + WebSocket
              |
         JarvisRuntime
     ├─ Kernel
     ├─ Control
     ├─ Perception
     ├─ Memory
     └─ Evolution
```

后端已运行不等于 App 已产品化。App 必须通过结构化 API 和事件接入现有能力，不能复制或绕过 Runtime、权限、记忆和审计边界。

## App V1 Roadmap

### V1-A Stable Shell

- 单一 RuntimeStateCoordinator。
- 有界消息队列、请求 ID、去重和重连。
- Tauri Sidecar 生命周期。
- AppData 日志路径。
- 窗口、托盘和顶层错误边界。

### V1-B Live Productization

- Live 组件拆分。
- 可收起状态栏。
- `980x720`、`760x560`、`1280x800` 和 200% 缩放布局。
- 1-6 行自适应文字输入。
- 2 行短字幕和完整回答抽屉。
- 8 个状态的一致映射。

### V1-C Information and Control

- 会话历史与 FTS5 搜索。
- 状态、任务、权限和感知抽屉。
- blocked 权限确认。
- UI/后端双层限流。
- 隐私提示和审计日志。

### V1-D Code Surface

- 授权工作区文件树和编辑器。
- 后端 Audited Command Tasks 接入。
- 实时输出、取消和退出码。
- 工具日志和 Agent 计划进度。
- Live/Code 切换与后台运行。

### V1-E Release Hardening

- Tauri 构建、CSP、权限、托盘、快捷键和安装包。
- 首次启动与运行时检测。
- 单元、协议、端到端、视觉和崩溃恢复测试。
- Code Review 和完成前独立验证。
- 打包边界、数据迁移和日志脱敏验证。

## Current Gaps

P0 App 集成:

- Sidecar 正式托管。
- 状态协调器。
- 响应式 Live UI 和长文本稳定性。
- 权限确认和隐私界面。
- 可靠消息桥。
- 真实 Code Surface。

P1 发布质量:

- 会话/记忆搜索 UI。
- 状态与感知抽屉。
- Tauri 构建、签名和安装包。
- 日志轮换、诊断包和崩溃恢复。
- Code Review 工作流。

后续版本:

- 子代理更深主循环协调。
- 显式 `/learn` 入口。
- 数字人和悬浮投影形态。

## Logs

保留的原始主日志:

```text
logs/javis_8080.log
logs/javis_8080.err.log
```

旧时间戳 server logs 和根目录 server 输出已批准删除。未来 App 日志外置到 `%APPDATA%\Javis\logs`，按 `app/runtime/audit/crash` 分类。

## Codex Task Recovery

Codex 侧边栏为空不等于任务数据被删除。2026-07-27 已确认以下本地恢复源存在:

```text
C:\Users\34247\.codex\sessions
C:\Users\34247\.codex\archived_sessions
C:\Users\34247\.codex\session_index.jsonl
C:\Users\34247\.codex\sqlite\codex-dev.db
```

项目决策优先从本仓库权威文档恢复；Codex 会话作为补充证据。

## Update Protocol

每次重要开发结束更新本账本，必须记录:

1. 实际改动和文件。
2. 验证命令和真实结果。
3. 阶段状态变化。
4. 新增风险和剩余工作。
5. 安装、模型、数据或权限变化。

状态词只使用 `完成`、`部分完成`、`未开始`、`阻塞`。文件存在不等于功能完成。

## 2026-07-27

- 确认 App Phase 1 壳存在。
- 建立 App V1 逐点规格。
- 重新运行 111 项测试和 183 文件 AST 检查。
- 通过当前 Runtime 证据确认五系统为 running，纠正旧报告中的 0.82 结论。
- 建立当前架构、运维和当前审计文档。
- 完成永久删除 34 份旧文档/报告、44 份时间戳日志和 `server_out.txt`，目标残留为 0。
- 保护入口 23/23 存在，产品 Python 文件保持 183 个。
- 修复会话重命名失败仍返回成功的问题，并新增回归测试。
- 修复 GitHub token 文件句柄未及时关闭的问题，并新增资源测试。
- 修复后 113 项测试、98 个 JSON、3 个 YAML、65 条路由和 2 个 SQLite 库全部通过检查。
- 一次未设置 `JAVIS_TEST_MODE=1` 的定向测试误触发完整 Runtime 启动；受版本控制的 `memory/session_events.sqlite` 已按 `HEAD` 哈希精确恢复。
- 同次启动改写了被 Git 忽略且没有可验证检测前备份的 `brain_data/`；为避免破坏用户记忆，未做猜测性回滚并在当前审计中保留完整说明。
- 运维文档中的完整测试、App 包装测试和 P0 Runtime 测试均改为显式设置 `JAVIS_TEST_MODE=1`。
- 最终隔离复测 113/113 通过；`memory/session_events.sqlite` SHA-256 和 `brain_data` 最新写入时间在测试前后均保持不变。
- 用户授权无需再次确认，自动执行 App V1 Phase 2、3、4；执行计划为 `docs/superpowers/plans/2026-07-27-javis-app-phases-2-4.md`。
- 本轮先建立防循环监督器，再依次完成 Live 产品化、信息与控制抽屉、Code Surface；相同错误连续三次必须熔断并记录，不允许无界重试。
- 建立 `scripts/app_phase_supervisor.py`：阶段顺序、错误指纹、连续 3 次熔断、原子状态、凭据脱敏和 AppData JSONL 事件日志；监督器测试 5/5 通过。
- Phase 2 完成：Tauri 直接 Live Surface、`RuntimeStateCoordinator`、32 条有界队列、有限重连、可收起状态栏、两行字幕、1-6 行输入和响应式布局。
- Phase 3 完成：可取消 FTS5 搜索、单抽屉管理、Runtime/Blueprint/任务/权限/感知面板、隐私提示和 300/600ms UI 限流。
- Phase 4 完成：授权文件树、文本/二进制查看、脏状态、外部修改冲突保护、文件保存、异步审计命令、轮询输出、退出码和进程树取消。
- 视觉验收覆盖 `980x720`、`760x560`、`1280x800`；紧凑窗口状态栏展开并输入 6 行时，关键元素重叠和页面溢出均为 0。
- 完整测试 138/138 通过；23 个 TypeScript 文件语法错误 0，184 个一方 Python 文件 AST 错误 0，70 条 FastAPI method/path 重复 0，2 个 SQLite 库 integrity check 为 `ok`。
- 最终测试前后 `memory/session_events.sqlite` SHA-256 与 `brain_data` 最新写入时间均保持不变。
- 未运行 Tauri 正式构建：App 项目依赖与 Rust/Cargo 工具链未就绪；本轮未安装、未下载任何依赖或模型。
- 用户要求继续后进入 App V1-E 发布硬化；计划为 `docs/superpowers/plans/2026-07-27-javis-app-phase-5-release-hardening.md`，继续遵守不自动安装和不伪报构建成功的边界。
- Phase 5 监督器已扩展 `phase-5` 与 `release-verification`，监督器和发布契约共 14/14 通过，失败计数为 0。
- 新增 Rust `SidecarManager`：先确认 `/api/status` 的 `service=javis` 身份，再直接启动 `python -u main.py` 并持有实际 PID；不调用带按端口杀进程逻辑的 `start.py`，不结束未知进程。
- 新增 Tauri 托盘显示/暂停/受限重启/诊断/退出、关闭窗口隐藏、明确退出、自有 Sidecar 停止、AppData 10MB 轮换脱敏日志和 Runtime stdout/stderr。
- 新增本机专用 CSP、前端 SidecarClient、错误恢复边界、诊断面板及诊断测试夹具；未提供自动安装或下载入口。
- 视觉复测覆盖 `760x560`、`980x720`、`1280x800`，页面溢出与控件裁切均为 0；宽屏状态栏已修为满宽，紧凑诊断面板无溢出。
- 最终完整测试 146/146 通过；27 个 TypeScript 文件语法错误 0，184 个一方 Python 文件 AST 错误 0，66 条 HTTP method/path 重复 0，2 个 SQLite 库 integrity check 为 `ok`。
- 最终隔离复测前后 `memory/session_events.sqlite` SHA-256 均为 `5E74DB9897D72D8CB109474C175A1D7908756EDD6C627359CF1E19A41085296E`，`brain_data` 最新写入时间不变。
- 仓库内 Rust/Cargo 1.97 可用，三个 Rust 文件通过 `rustfmt --check`；`cargo check --offline` 因未缓存 `tauri` crate 而停止，App `node_modules` 也不存在，因此没有伪报正式编译成功。
- 本阶段没有安装、下载、删除模型、迁移数据库、提交 Git 或回退用户既有改动。
- 用户要求继续后进入 App Phase 6；计划为 `docs/superpowers/plans/2026-07-27-javis-app-phase-6-build-readiness.md`。
- 监督器新增 `phase-6` 与 `build-readiness-verification`，旧 completed 状态可继续推进且仍受顺序门与三次同错熔断保护。
- 修复 `scripts/start_javis_app.py`：不再调用会按端口清理进程的 `start.py`，改为直接启动 `python -u main.py`；只有 `/api/status` 返回 `service=javis` 才报告 ready。
- 修复 `main.py` 端口优先级：显式 `PORT` 环境变量现在覆盖配置端口，Tauri 和预览启动器可稳定指定 8080。
- 新增只读 `scripts/app_build_preflight.py`，输出稳定 JSON；当前 blocker 精确为前端依赖、Tauri crate 缓存、bundle 开关和应用图标四项，不执行安装、下载或构建。
- 修复产品打包清单：加入 Runtime 必需的 `blueprint/` 与 `tools/`，移除旧 `start.py`，大型工具链、模型、工作区、数据库和用户数据继续外置。
- 新增首次启动本地数据与隐私 Surface：说明 `%APPDATA%\Javis`、`D:\JavisModels`、`D:\JavisWorkspace`、麦克风/屏幕/相机默认不落盘和无自动安装；确认状态本地持久化，设置页可重新打开。
- 首次启动 Surface 在 `760x560` 与 200% 文字缩放下无横向溢出，长内容内部滚动；真实 Windows 200% DPI 留待编译后 WebView 验收。
- Phase 6 最终完整测试 156/156 通过；28 个 TypeScript 文件语法错误 0，185 个一方 Python 文件 AST 错误 0，3 个 Rust 文件解析/格式检查通过，2 个 SQLite 库 integrity check 为 `ok`。
- Phase 6 隔离复测前后事件库 SHA-256 均为 `5E74DB9897D72D8CB109474C175A1D7908756EDD6C627359CF1E19A41085296E`，`brain_data` 最新写入时间不变；没有安装、下载、迁移或删除用户数据。
