# Javis App 发布阻塞修复设计

## 目标

修复 Code 侧栏收起后错位、Live 状态来源冲突、Live 本地闲聊延迟、阻塞式权限确认，以及音频链路不可自检五项问题。既有五大系统蓝图、工具权限模型、旧 Web Workbench 功能和 App 导航保持不变。

## 设计

### Code 响应式布局

`toggleSidebar()` 不再只改元素的内联 `display`，而是同步维护 `body.sidebar-collapsed`。固定输入区、推理区、权限区和顶栏工具区都从同一状态计算偏移；再次展开时恢复原宽度。窄屏规则继续拥有最高优先级。

### Live 快速对话

App 的文本和语音请求携带 `interaction_mode: "live"`。Agent 只对短文本、无工具意图的日常交流启用紧凑路径：保留核心记忆和会话召回，跳过规划器、94 个工具 schema 和完整动态提示，调用本地 Ollama 紧凑对话接口。工具、文件、桌面、代码、长任务仍进入完整 Agent 路径。

本地紧凑路径使用 `/api/chat`、`/no_think`、有限上下文、有限输出和 `keep_alive`。失败时回退完整 Agent，不吞掉请求。

### 状态统一

Sidecar 只表示桌面进程所有权，WebSocket 和 HTTP 健康检查表示实际后端连接。浏览器预览不再把 Sidecar 不可用覆盖成后端离线。状态栏分别显示 Provider、WebSocket、Kernel 和 Sidecar，不使用一个布尔值推导全部状态。

### 非阻塞确认

Control Drawer 使用现有抽屉内确认卡处理 ROOT 令牌和屏幕分析。操作进入等待态，用户可确认或取消；不再调用 `window.confirm`。确认卡一次只处理一个动作，关闭抽屉或取消时不执行副作用。

### 音频自检

VoiceCapture 暴露能力探测和短录音自检：检查 `getUserMedia`、`MediaRecorder`、支持的 MIME、输入轨道和录音字节。系统音频使用 `getDisplayMedia({audio:true})` 进行显式授权探测，不常驻采集。Diagnostics 展示麦克风、系统音频、STT 和 TTS 的分项结果，并提供手动启动按钮。

## 验收

- 侧栏收起后输入区中心偏移不超过 2px。
- 浏览器预览连接后不再同时显示“后端在线”和“WebSocket 离线”。
- `仅回复：LIVE_FAST_OK` 的本地紧凑路径输出包含 `LIVE_FAST_OK`，且不携带工具 schema。
- ROOT 和屏幕分析源码中不存在 `window.confirm`。
- 音频诊断能区分不支持、未授权、无音轨、录音成功和后端不可用。
- Python 测试、TypeScript 构建、Vite 构建、Tauri `cargo check` 全部通过。
