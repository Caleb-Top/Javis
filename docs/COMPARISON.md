# Javis vs Hermes Agent — 逐项对比报告

> Hermes Agent: 153,392行 · 3,139 Python文件 · 156 agent模块 · 947 TypeScript桌面端
> Javis:        6,360行  ·    13 Python文件 ·   1 agent模块 ·   3 Web文件(纯HTML)

---

## 一、架构对比

| 维度 | Hermes Agent | Javis |
|------|-------------|-------|
| 语言 | Python + TypeScript(桌面端) | Python + HTML/CSS/JS |
| 桌面端 | Electron + React (947 TS文件) | 无 (Web-First策略, 未打包) |
| 代理循环 | `agent_runtime_helpers.py` 3473行 | `agent.py` 617行 |
| 状态管理 | `hermes_state.py` 7781行 | `AgentState` dataclass 40行 |
| CLI | `cli.py` 16089行 | `启动Javis.bat` 8行 |
| 插件系统 | 19个插件目录 | 无 |
| 浏览器引擎 | browser_use + firecrawl + browserbase | 无 |

## 二、功能矩阵 (✅有 / ⚠️半成品 / ❌无)

| 功能 | Hermes | Javis |
|------|--------|-------|
| **LLM调用** | | |
| Native Function Calling | ✅ | ✅ |
| 多Provider切换 | ✅ 30+ | ✅ 7 |
| 流式输出 | ✅ | ✅ |
| **记忆系统** | | |
| 对话记忆 | ✅ memory插件 | ❌ |
| 上下文压缩 | ✅ context_compressor | ❌ |
| Session搜索 | ✅ session_search工具 | ❌ |
| **工具系统** | | |
| 工具数量 | 60+ | 17 |
| 浏览器操控 | ✅ browser_use | ❌ |
| 网页搜索 | ✅ web_search | ❌ |
| 桌面控制 | ❌ (服务端无GUI) | ✅ 截图/键鼠/窗口 |
| 文件管理 | ✅ | ✅ |
| 语音输入 | ✅ whisper | ✅ faster-whisper |
| 语音输出 | ✅ TTS providers | ✅ edge-tts |
| 摄像头 | ❌ | ✅ |
| **开发体验** | | |
| 单元测试 | ✅ 大量 | ❌ 0 |
| CLI工具 | ✅ 完整 | ❌ |
| Docker部署 | ✅ | ❌ |
| CRON定时任务 | ✅ | ❌ |
| 插件热加载 | ✅ | ❌ |
| API Key加密 | ✅ secret_sources | ❌ 明文 |
| **部署** | | |
| 云端部署 | ✅ Docker/SSH | ❌ |
| 本地部署 | ✅ | ✅ |
| 桌面应用 | ✅ Electron | ❌ |
| 自动启动 | ✅ | ✅ .bat |

## 三、你比 Hermes 强的地方

| 优势 | 说明 |
|------|------|
| 🖥️ **桌面控制** | Hermes跑服务器端, 没有桌面GUI操控。你能截图/键鼠/窗口控制 |
| 📷 **摄像头** | Hermes没有camera工具 |
| 🗂️ **open_app/find_app** | 独创, 扫描开始菜单打开应用, Hermes做不到 |
| 🎯 **定位清晰** | 你就是Windows桌面助手, 不试图做一切 |
| 📦 **体积小** | 274KB vs Hermes的数百MB, 启动快 |
| 💰 **成本低** | DeepSeek API 1元/百万token, 本地模型零成本 |

## 四、Hermes 碾压你的地方

| 劣势 | 说明 |
|------|------|
| 🧠 **记忆系统** | 完全缺失, 刷新就失忆 |
| 🧪 **测试** | 0个测试, 全靠我手工测 |
| 🔌 **插件系统** | 无, 加功能要改核心代码 |
| 🌐 **浏览器** | 无, 不能上网搜东西 |
| 💬 **多渠道** | 无, 只能网页端用 |
| 🔒 **安全** | API Key明文存config.yaml |
| 📱 **跨设备** | 无, 只能本机访问 |

## 五、优先补课清单

```
🔴 1. 对话记忆 (ChromaDB或localStorage) — 最重要
🔴 2. API Key加密 (哪怕简单base64)
🟡 3. 拆分app.js为chat/voice/settings
🟡 4. 写3-5个核心工具单测
🟡 5. 添加浏览器工具 (打开网页/搜索)
🟢 6. 插件系统基础框架
🟢 7. 打包Electron桌面版
```
