# Javis App Phase 6 Build Readiness Implementation Plan

**Goal:** 在不下载或安装依赖的条件下，完成 App V1 的安全启动收口、构建预检、首次启动隐私确认和高缩放验收，并把真实安装包阻塞项变成机器可读证据。

**Architecture:** Python 预检层只读取工具链、依赖缓存、Tauri 配置和打包清单；Tauri/TypeScript 产品层继续保持 Live-first，通过本地偏好记录首次启动确认。缺少图标、`node_modules` 或 Tauri crate 时只报告 blocker，不自动安装、不生成伪安装包。

**Constraints:**

- 不调用会按端口终止进程的 `start.py`。
- 8080 现有服务必须返回 `service=javis` 才能复用。
- 首次启动只说明数据路径、模型路径、工作区与媒体默认隐私，不收集遥测。
- `preview.html` 仍只用于无依赖验证，正式入口仍是 Tauri Live。
- 不执行 `pnpm install`、`cargo fetch`、联网 `cargo check` 或资源下载。

## Task 1: Phase 6 Supervision

- [x] 为 `phase-6` 与 `build-readiness-verification` 写失败测试。
- [x] 扩展监督器并启动 Phase 6，保持旧状态文件兼容。

## Task 2: Safe Preview Launcher

- [x] 写失败测试，禁止预览启动器引用 `start.py` 或接受非 Javis 服务。
- [x] 直接启动 `python -u main.py`，通过 `PORT` 传递端口并保留实际 PID。
- [x] 只在 `/api/status` 返回 `service=javis` 时报告 ready。

## Task 3: Read-only Build Preflight

- [x] 写预检结果契约测试，覆盖 Node、Rust/Cargo、App 依赖、Tauri crate、图标、bundle 和产品打包边界。
- [x] 实现 `scripts/app_build_preflight.py`，输出稳定 JSON 和 blocker 列表。
- [x] 预检不得执行安装、下载、构建或文件迁移。

## Task 4: First-run Privacy and Paths

- [x] 写首次启动面板契约测试，覆盖一次性偏好、数据路径、隐私默认值和重新打开入口。
- [x] 实现无卡片嵌套的全高首次启动 Surface，并接入设置页。
- [x] 支持 Escape、焦点恢复、长路径换行和紧凑窗口滚动。

## Task 5: Accessibility and Verification

- [x] 验证 `760x560`、`980x720`、`1280x800` 和 200% 文字缩放；真实 Windows DPI 保留为编译后验收。
- [x] 验证键盘焦点、Escape、错误恢复、诊断和首次启动 Surface。
- [x] 运行完整 Python、TypeScript 语法、Rust 解析、JSON、SQLite 和数据不变检查。
- [x] 更新 README、当前架构、当前审计和主总账，监督器完成 build-readiness verification。
