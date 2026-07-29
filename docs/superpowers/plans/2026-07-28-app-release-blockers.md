# Javis App Release Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复五项已确认的 App 发布阻塞问题，同时保留现有蓝图和全部功能。

**Architecture:** 旧 Web 用单一布局状态驱动响应式偏移；App 将 Sidecar、HTTP 和 WebSocket 状态分离；Agent 为 Live 短对话增加紧凑推理路径；Control Drawer 与 Diagnostics 承担确认和音频自检。

**Tech Stack:** Python、FastAPI、Ollama、TypeScript、Tauri 2、WebSocket、MediaRecorder。

## Global Constraints

- 不改变五大系统蓝图。
- 不删除旧 Web Workbench 或现有工具入口。
- 高风险权限仍需明确本地确认。
- 音频采集必须由用户动作触发并立即释放轨道。

---

### Task 1: Code 侧栏布局

**Files:**
- Modify: `web/js/app.js`
- Modify: `web/css/style.css`
- Test: `tests/test_app_v1_release.py`

- [ ] 写入断言：侧栏函数维护 `sidebar-collapsed`，CSS 为该状态覆盖固定控件偏移。
- [ ] 运行测试并确认因缺少状态规则而失败。
- [ ] 实现状态类和桌面/窄屏覆盖。
- [ ] 运行测试并确认通过。

### Task 2: 状态来源统一

**Files:**
- Modify: `app/src/bridge/backendClient.ts`
- Modify: `app/src/main.ts`
- Test: `tests/test_app_release_hardening.py`

- [ ] 写入断言：连接回调携带独立 HTTP/WebSocket 状态，浏览器预览不由 Sidecar 覆盖。
- [ ] 运行测试并确认失败。
- [ ] 实现独立状态快照和状态栏合并。
- [ ] 运行测试并确认通过。

### Task 3: Live 紧凑推理

**Files:**
- Modify: `app/src/bridge/backendClient.ts`
- Modify: `main.py`
- Modify: `core/agent.py`
- Modify: `core/engine.py`
- Modify: `core/llm_client.py`
- Test: `tests/test_p0_runtime.py`

- [ ] 写入短对话分类、Live 协议和本地紧凑请求测试。
- [ ] 运行测试并确认失败。
- [ ] 实现 `interaction_mode`、紧凑路径和失败回退。
- [ ] 运行测试并确认通过。

### Task 4: 非阻塞确认

**Files:**
- Modify: `app/src/panels/ControlDrawer.ts`
- Modify: `app/src/styles.css`
- Test: `tests/test_app_phases.py`

- [ ] 写入断言：Control Drawer 不含 `window.confirm`，并有待确认动作状态。
- [ ] 运行测试并确认失败。
- [ ] 复用抽屉确认卡实现 ROOT、屏幕分析确认和取消。
- [ ] 运行测试并确认通过。

### Task 5: 音频诊断

**Files:**
- Modify: `app/src/live/VoiceCapture.ts`
- Modify: `app/src/panels/DiagnosticsPanel.ts`
- Modify: `app/src/main.ts`
- Modify: `app/src/styles.css`
- Test: `tests/test_app_packaging.py`

- [ ] 写入能力探测、麦克风自检、系统音频显式探测和诊断入口断言。
- [ ] 运行测试并确认失败。
- [ ] 实现分项自检、轨道释放和诊断输出。
- [ ] 运行测试并确认通过。

### Task 6: 全链路回归

**Files:**
- Verify only.

- [ ] 运行 173 项 Python 测试。
- [ ] 运行 TypeScript 和 Vite 生产构建。
- [ ] 运行 Tauri GNU `cargo check`。
- [ ] 在浏览器复测侧栏中心、状态栏、确认卡、记忆、感知和 Live。
- [ ] 在 Tauri 中打开音频诊断并记录仍需用户完成的硬件授权步骤。
