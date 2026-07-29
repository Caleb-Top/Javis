# Javis App Phase 5 Release Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不安装新依赖的条件下，完成 App V1-E 可静态验证的发布硬化，并明确隔离正式构建阻塞项。

**Architecture:** Tauri Rust 壳负责窗口、托盘、AppData 日志和自己启动的 Python Sidecar；前端通过 Tauri invoke 获取 Sidecar 状态并保留 HTTP/WebSocket 数据通道。所有进程操作带 ownership token，只停止当前 App 创建的进程。

**Tech Stack:** Tauri v2、Rust 标准库、TypeScript、FastAPI、Python unittest。

## Global Constraints

- Live 仍是 Tauri 窗口打开后的直接主界面，`preview.html` 只用于测试。
- 不执行 `pnpm install`、`cargo install`、`winget install` 或任何下载。
- 端口被非 Javis 进程占用时不得结束未知进程。
- App 只停止自己启动并持有 ownership token 的 Sidecar。
- CSP 只允许本地资源以及 `127.0.0.1:8080` 的 HTTP/WebSocket。
- 无 Rust/Cargo 或 App 依赖时记录构建阻塞，不把静态检查称为正式编译。

---

### Task 1: Phase 5 Supervision

**Files:**
- Modify: `scripts/app_phase_supervisor.py`
- Modify: `tests/test_app_phase_supervisor.py`

**Interfaces:**
- Produces: `phase-5` 和 `release-verification` 阶段，旧状态文件自动升级。

- [x] 写失败测试，要求已完成旧流程后可以进入 `phase-5`。
- [x] 实现向后兼容阶段升级和最终发布完成状态。
- [x] 运行监督器测试并启动 Phase 5。

### Task 2: Sidecar Ownership and Lifecycle

**Files:**
- Create: `app/src-tauri/src/sidecar.rs`
- Modify: `app/src-tauri/src/main.rs`
- Create: `app/src/bridge/sidecarClient.ts`
- Modify: `app/src/bridge/sidecarStatus.ts`
- Modify: `app/src/main.ts`
- Test: `tests/test_app_release_hardening.py`

**Interfaces:**
- Produces: Tauri commands `sidecar_status`、`sidecar_start`、`sidecar_stop`、`sidecar_restart`。
- Produces: `createSidecarClient().ensureStarted()` 和状态订阅。

- [x] 写失败契约测试，覆盖探测、启动超时、ownership token、有限重启和只停止自有 PID。
- [x] 实现 Rust SidecarManager 和本地 `/api/status` TCP 探测。
- [x] 实现 Python 路径发现、隐藏进程、AppData stdout/stderr 和停止所有权。
- [x] 接通前端 SidecarClient 和 RuntimeStateCoordinator。

### Task 3: Tray, Window and AppData Logs

**Files:**
- Create: `app/src-tauri/src/app_log.rs`
- Modify: `app/src-tauri/src/main.rs`
- Create: `app/src/app/AppLogger.ts`
- Test: `tests/test_app_release_hardening.py`

**Interfaces:**
- Produces: tray `show/pause/restart/diagnostics/quit` 菜单。
- Produces: 关闭窗口隐藏、显式退出停止自有 Sidecar、`write_app_log` 脱敏日志命令。

- [x] 写失败契约测试，覆盖 `CloseRequested.prevent_close()`、托盘命令和 AppData 日志路径。
- [x] 实现托盘和显式退出标志。
- [x] 实现 10MB 轮换、敏感字段脱敏和结构化 App 日志。

### Task 4: CSP, Error Boundary and Diagnostics

**Files:**
- Modify: `app/src-tauri/tauri.conf.json`
- Modify: `app/src-tauri/capabilities/default.json`
- Create: `app/src/app/AppErrorBoundary.ts`
- Create: `app/src/panels/DiagnosticsPanel.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/styles.css`
- Test: `tests/test_app_release_hardening.py`

**Interfaces:**
- Produces: 严格 CSP、顶层错误覆盖层、运行环境诊断摘要。

- [x] 写失败测试，禁止 `csp: null`，要求本地 connect-src、错误边界和诊断入口。
- [x] 实现 CSP、全局 error/unhandledrejection 捕获和恢复到 Live。
- [x] 实现 Python/Node/Rust/Sidecar/数据路径诊断显示，不提供自动安装按钮。

### Task 5: Verification and Ledger

**Files:**
- Modify: `README.md`
- Modify: `docs/JAVIS_CURRENT_ARCHITECTURE.md`
- Modify: `docs/JAVIS_CURRENT_AUDIT.md`
- Modify: `logs/JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md`

**Interfaces:**
- Consumes: 监督器状态、完整测试、Rust 工具探针、路由与数据哈希。

- [x] 运行完整 Python 测试与 App 发布契约测试。
- [x] 运行 23+ TypeScript 文件 Node 语法检查和 Rust 源码结构检查。
- [x] 探测 `cargo/rustc/tsc/vite`，仅在现有工具完整时构建。
- [x] 验证数据库、Brain 写入时间、CSP、受保护入口和 `git diff --check`。
- [x] 更新主日志、审计和架构，监督器标记 `release-verification` 完成。
