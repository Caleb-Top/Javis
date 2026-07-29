# Javis App Phase 2-4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改写现有 Python Runtime、不安装新依赖的前提下，完成 App V1-B、V1-C、V1-D，并用监督器限制重复失败和记录阶段证据。

**Architecture:** 保持 Tauri v2 + 原生 TypeScript + Vite。App 通过单一状态协调器和可靠 BackendClient 接入 FastAPI/WebSocket；Live、抽屉和 Code Surface 各自分层，所有工作区写入与命令执行继续经过现有后端安全边界。

**Tech Stack:** TypeScript、DOM/CSS、Tauri v2、FastAPI、WebSocket、Python unittest。

## Global Constraints

- 首页始终是 Live Orb，Code Surface 默认隐藏。
- 支持 `980x720`、`760x560`、`1280x800` 和 200% 缩放，不允许文字或控件重叠。
- 不引入前端框架或新依赖，不执行安装、下载、模型拉取。
- 所有工作区路径、文件保存和命令任务必须调用现有受控后端 API。
- 原始麦克风、屏幕和相机媒体默认不落盘。
- 相同失败指纹连续出现 3 次时监督器熔断，禁止无界重试。

---

### Task 1: Build Supervisor

**Files:**
- Create: `scripts/app_phase_supervisor.py`
- Test: `tests/test_app_phase_supervisor.py`

**Interfaces:**
- Produces: `BuildSupervisor.record(phase, step, status, detail, signature)` 和 CLI `start/checkpoint/fail/complete/status`。
- Writes: `%LOCALAPPDATA%\Javis\logs\supervisor\app-phase-build.jsonl` 与同目录状态 JSON。

- [x] 写失败测试，覆盖阶段顺序、同错误三次熔断、成功后清零和日志脱敏。
- [x] 运行测试并确认因模块缺失而失败。
- [x] 实现有界状态机、原子状态写入、错误指纹与敏感字段脱敏。
- [x] 运行监督器测试并确认通过。
- [x] 用监督器启动 `phase-2`，写入本轮基线。

### Task 2: State and Bridge Foundation

**Files:**
- Create: `app/src/state/runtimeStateTypes.ts`
- Create: `app/src/state/RuntimeStateCoordinator.ts`
- Create: `app/src/bridge/requestQueue.ts`
- Modify: `app/src/bridge/backendClient.ts`
- Test: `tests/test_app_phases.py`

**Interfaces:**
- Produces: `RuntimeStateCoordinator.signal()`、`subscribe()`、`snapshot()`。
- Produces: 有界请求队列、请求 ID、去重、取消、重连退避和事件订阅。

- [x] 写静态契约测试，要求唯一状态写入口、有界队列和 `[1,2,3,5,8,15]` 重连序列。
- [x] 运行测试并确认缺少文件/符号而失败。
- [x] 实现状态优先级、requestId 终止规则、订阅和 DOM 适配器。
- [x] 实现 BackendClient HTTP/WS API、队列上限、低风险待发与高风险不重发。
- [x] 运行阶段测试并记录监督器 checkpoint。

### Task 3: Phase 2 Live Productization

**Files:**
- Create: `app/src/app/AppPreferences.ts`
- Create: `app/src/live/LiveCaption.ts`
- Create: `app/src/live/CommandComposer.ts`
- Create: `app/src/panels/StatusRail.ts`
- Modify: `app/src/live/LiveStage.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/styles.css`
- Modify: `app/preview.html`
- Test: `tests/test_app_phases.py`

**Interfaces:**
- Produces: `renderLiveStage()`、`createCommandComposer()`、`createStatusRail()`、`createLiveCaption()`。

- [x] 写失败测试，覆盖 textarea 1-6 行、状态栏折叠持久化、两行字幕、断点、减少动画和可访问属性。
- [x] 实现 Live DOM 结构与固定轨道布局。
- [x] 接通统一状态、语音、文字发送、任务摘要和状态栏展开。
- [x] 更新无依赖预览，使其展示 Phase 2 的真实布局。
- [x] 运行测试并将 `phase-2` 标记完成。

### Task 4: Phase 3 Information and Control

**Files:**
- Create: `app/src/panels/DrawerManager.ts`
- Create: `app/src/panels/ConversationDrawer.ts`
- Create: `app/src/panels/ControlDrawer.ts`
- Create: `app/src/panels/TaskStream.ts`
- Create: `app/src/security/RateLimiter.ts`
- Modify: `app/src/bridge/backendClient.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/styles.css`
- Test: `tests/test_app_phases.py`

**Interfaces:**
- Consumes: BackendClient `get/post`、事件订阅和状态协调器。
- Produces: 250ms 可取消 FTS5 搜索、状态/任务/权限/感知标签页、确认响应和 UI 限流。

- [x] 写失败测试，覆盖搜索最短长度、取消旧请求、权限操作、隐私提示、单抽屉和 300/600ms 限流。
- [x] 实现会话搜索及加载/空/错误状态。
- [x] 实现状态、任务、权限、感知、设置标签页和确认队列。
- [x] 接通 `/api/runtime/status`、`/api/blueprint/coverage`、`/api/config/permission`、`/api/control/commands` 和感知 API。
- [x] 运行测试并将 `phase-3` 标记完成。

### Task 5: Phase 4 Code Surface

**Files:**
- Modify: `app/src/code/CodeSurface.ts`
- Create: `app/src/code/FileExplorer.ts`
- Create: `app/src/code/EditorPane.ts`
- Create: `app/src/code/TerminalPane.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/styles.css`
- Test: `tests/test_app_phases.py`

**Interfaces:**
- Consumes: `/api/workspace/explore`、`/api/workspace/read`、`/api/workspace/save`、`/api/workspace/terminal`、`/api/control/commands`。
- Produces: 文件树、文本编辑/保存、二进制元数据、命令执行/输出/退出码、任务刷新和 Live 返回。

- [x] 写失败测试，覆盖后端安全 API、未保存状态、保存错误、命令状态、紧凑布局和 Live/Code 切换。
- [x] 实现文件树和文件读取。
- [x] 实现编辑器脏状态、保存与错误反馈。
- [x] 实现审计终端、任务历史和命令结果。
- [x] 运行测试并将 `phase-4` 标记完成。

### Task 6: Verification and Ledger

**Files:**
- Modify: `logs/JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md`
- Modify: `docs/JAVIS_CURRENT_AUDIT.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: 监督器状态、测试输出、AST/静态检查和可用构建工具探针。

- [x] 在 `JAVIS_TEST_MODE=1` 下运行完整 Python 测试。
- [x] 运行 App Phase 静态契约、HTML/CSS 结构与打包边界检查。
- [x] 若本机已有可用 TypeScript/Vite 工具则执行构建；否则记录环境阻塞且不安装。
- [x] 检查受保护入口、数据文件前后哈希和 `git diff --check`。
- [x] 将每阶段结果、剩余风险和下一阶段入口写入唯一主日志与当前审计。
- [x] 监督器输出最终摘要并标记本轮完成。
