# JARVIS 智能体 —— Web-First 实施策略 (v3.0)

> 核心思路：环境 => Web 版本（FastAPI + WebSocket + 浏览器 UI） → 最后 Electron/PyQt 封装为桌面版

---

## 一、为什么要 Web-First？

```
传统桌面端开发                     vs        Web-First 策略
─────────────────────────────────       ────────────────────────
❌ PyQt/Qt 学习曲线高                     ✅ 用 HTML/CSS/JS，零门槛
❌ 每次改 UI 要重启应用                    ✅ 改前端刷新浏览器即可
❌ 调试工具弱 (print 打天下)               ✅ Chrome DevTools 全功率
❌ 打包 exe 每次 10 分钟                   ✅ 最后一步才打包
❌ 不能远程访问                            ✅ 手机/平板也能连
❌ 前端生态比 Web 差太远                    ✅ npm/React/Vue 全线可用
─────────────────────────────────       ────────────────────────
桌面版 = Web 核心 + Electron 壳 (最后一步)
```

---

## 二、Web 版整体架构

```
                    ┌───────────────────────────────────────────┐
                    │            浏览器 (Browser)                 │
                    │  ┌─────────────────────────────────────┐  │
                    │  │  Web UI (HTML/CSS/JS)               │  │
                    │  │  • 对话面板                          │  │
                    │  │  • 语音按钮 (按住说话)               │  │
                    │  │  • 截屏预览                          │  │
                    │  │  • 摄像头预览                        │  │
                    │  │  • 系统状态面板                       │  │
                    │  │  • 配置面板                          │  │
                    │  └─────────────────────────────────────┘  │
                    │          ↕ WebSocket (实时双向)            │
                    │          ↕ HTTP REST (文件/配置)           │
                    └───────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────────────┐
                    │      FastAPI Server (localhost:8080)    │
                    │                                        │
                    │  ┌─────────────────────────────────┐   │
                    │  │  API 路由层                      │   │
                    │  │  GET  /          → 静态页面      │   │
                    │  │  WS   /ws        → WebSocket     │   │
                    │  │  POST /api/voice  → 语音上传     │   │
                    │  │  POST /api/camera → 拍照         │   │
                    │  │  GET  /api/config → 配置读写     │   │
                    │  └──────────────┬──────────────────┘   │
                    │                 │                       │
                    │  ┌──────────────▼──────────────────┐   │
                    │  │  Agent 核心 (agent.py)           │   │
                    │  │  • ReAct 循环                    │   │
                    │  │  • 消息路由                      │   │
                    │  │  • 状态管理                      │   │
                    │  └──────────────┬──────────────────┘   │
                    │                 │                       │
                    │     ┌───────────┼───────────┐          │
                    │     ▼           ▼           ▼          │
                    │  ┌───────┐ ┌───────┐ ┌──────────┐    │
                    │  │ 工具层  │ │ LLM   │ │ 语音      │    │
                    │  │Wins/Desktop │Client │ │STT/TTS│    │
                    │  └───────┘ └───────┘ └──────────┘    │
                    └────────────────────────────────────────┘
```

### 核心优势：WebSocket 实时管道

```
用户通过 WebSocket 连接的完整对话流:

浏览器 → WebSocket →  FastAPI → Agent → LLM → Agent → WebSocket → 浏览器
                           ↕                  ↕
                        工具调用           流式输出
                      (截图/拍照)        (逐字显示)
```

---

## 三、Web 版项目结构

```
jarvis-web/
│
├── main.py                    # FastAPI 入口, 启动服务器
├── config.yaml                # GPT 风格参数配置
├── requirements.txt           # Python 依赖
│
├── server/                    # 后端服务
│   ├── __init__.py
│   ├── app.py                 # FastAPI 应用实例 + 路由
│   ├── websocket_handler.py   # WebSocket 连接管理 + 消息分发
│   ├── api_routes.py          # REST API 路由
│   └── static_server.py       # 静态文件服务
│
├── core/                      # 核心逻辑 (与桌面版共享)
│   ├── __init__.py
│   ├── agent.py               # Agent 主循环 (ReAct)
│   ├── llm_client.py          # LLM 调用封装
│   ├── tool_registry.py       # 工具注册中心
│   ├── tool_result.py         # 工具返回类型定义
│   └── memory.py              # 记忆系统
│
├── cognition/                 # 认知能力
│   ├── __init__.py
│   ├── intent_parser.py       # 意图识别
│   ├── planner.py             # 任务规划
│   └── react_loop.py          # ReAct 执行引擎
│
├── tools/                     # 工具实现
│   ├── __init__.py
│   ├── camera.py              # 摄像头 (OpenCV)
│   ├── desktop.py             # 桌面控制 (截图/鼠标/键盘)
│   ├── vision.py              # 视觉分析 (桌面+摄像头)
│   ├── file_ops.py            # 文件操作
│   ├── system.py              # 系统命令/状态
│   ├── web.py                 # 网络搜索
│   ├── clipboard.py           # 剪贴板
│   └── config_tool.py         # 动态配置读写
│
├── voice/                     # 语音模块
│   ├── __init__.py
│   ├── stt.py                 # 语音识别 (Whisper)
│   ├── tts.py                 # 语音合成 (Edge-TTS)
│   └── wake_word.py           # 唤醒词 (独立线程)
│
├── web/                       # 💻 前端 (浏览器 UI)
│   ├── index.html             # 主页面
│   ├── css/
│   │   └── style.css          # 样式 (深色主题)
│   ├── js/
│   │   ├── app.js             # 主应用逻辑
│   │   ├── websocket.js       # WS 连接管理
│   │   ├── voice.js           # 语音录音/播放
│   │   ├── camera.js          # 摄像头控制
│   │   └── config.js          # 配置面板
│   └── assets/
│       └── favicon.ico
│
├── utils/
│   ├── __init__.py
│   ├── logger.py
│   └── config.py
│
└── data/                      # 运行时数据
    ├── memory/                # 向量数据库
    ├── logs/                  # 日志
    └── screenshots/           # 临时截图
```

### 共享核心 (core/ tools/ voice/)

```
         ┌─────────────────┐
         │   jarvis-core    │ ← 这部分 Web 版和桌面版完全相同
         │ (core + tools +  │
         │  voice + cognition) │
         └────┬────────┬────┘
              │        │
    ┌─────────▼──┐ ┌──▼──────────┐
    │ jarvis-web  │ │ jarvis-desktop│ ← 前端不同
    │ (server/web)│ │ (PyQt/Electron)│
    └─────────────┘ └──────────────┘
```

---

## 四、Web 版核心技术选型

| 模块 | 技术 | 说明 |
|------|------|------|
| **后端** | **FastAPI** + **uvicorn** | 高性能异步 Python Web 框架 |
| **实时通信** | **WebSocket** | 双向流式对话 + 工具状态反馈 |
| **前端** | **原生 HTML/CSS/JS** | 无框架依赖，极简快 |
| **流式输出** | **Server-Sent Events** (可选) | LLM 逐字推送到浏览器 |
| **语音前端** | **MediaRecorder API** | 浏览器原生录音，无需插件 |
| **语音播放** | **Audio API** | 播放 TTS 返回的音频 |
| **摄像头** | **getUserMedia API** | 浏览器调用摄像头拍照 |
| **打包** | **PyInstaller** + **Electron** | 最终一步封装为 exe |

---

## 五、WebSocket 消息协议设计

### 双向消息格式

```json
// 客户端 → 服务器
{
    "type": "message" | "voice_start" | "voice_stop" | "camera_request" 
          | "config_update" | "interrupt" | "ping",
    "payload": { ... },
    "session_id": "uuid",
    "timestamp": 1234567890.123
}

// 服务器 → 客户端
{
    "type": "text_delta" | "text_done" | "tool_start" | "tool_result"
          | "thinking" | "error" | "audio_chunk" | "state_change"
          | "config_update" | "pong",
    "payload": { ... },
    "session_id": "uuid",
    "timestamp": 1234567891.456
}
```

### 每种消息详细定义

```
┌──── 客户端 → 服务器 ────────────────────────────────────────────┐
│                                                                  │
│ message:         发送文字消息                                     │
│ { type: "message", payload: { text: "帮我整理桌面" } }           │
│                                                                  │
│ voice_start:     开始语音录制                                     │
│ { type: "voice_start", payload: { format: "webm" } }            │
│                                                                  │
│ voice_stop:      停止录制并上传                                    │
│ { type: "voice_stop", payload: { audio: "base64..." } }         │
│                                                                  │
│ camera_request:  请求拍照                                         │
│ { type: "camera_request", payload: { image: "base64..." } }     │
│                                                                  │
│ interrupt:       打断当前 TTS/操作                                │
│ { type: "interrupt" }                                           │
│                                                                  │
│ config_update:   更新配置                                         │
│ { type: "config_update", payload: { key: "temperature", v: 0.9 }}│
└──────────────────────────────────────────────────────────────────┘

┌──── 服务器 → 客户端 ────────────────────────────────────────────┐
│                                                                  │
│ text_delta:      LLM 流式输出 (逐字推送)                          │
│ { type: "text_delta", payload: { text: "桌" } }                 │
│                                                                  │
│ text_done:       本轮回复完成                                      │
│ { type: "text_done", payload: { full_text: "桌面已整理好" } }    │
│                                                                  │
│ thinking:        LLM 正在思考 (显示动画)                           │
│ { type: "thinking", payload: { content: "正在分析桌面..." } }    │
│                                                                  │
│ tool_start:      开始执行工具                                      │
│ { type: "tool_start", payload: { tool: "截图", step: "1/3" } }  │
│                                                                  │
│ tool_result:     工具执行结果                                      │
│ { type: "tool_result", payload: { tool: "截图", success: true } }│
│                                                                  │
│ audio_chunk:     TTS 音频流                                       │
│ { type: "audio_chunk", payload: { audio: "base64..." } }        │
│                                                                  │
│ state_change:    Agent 状态变化                                    │
│ { type: "state_change", payload: { state: "thinking" } }        │
│                                                                  │
│ error:           错误信息                                          │
│ { type: "error", payload: { message: "...", code: "E001" } }    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 六、前后端交互时序图

### 场景 1: 文字输入

```
浏览器                    FastAPI                    Agent                    LLM
  │                         │                         │                        │
  │── WS: message("整理桌面")─▶│                         │                        │
  │                         │── WS: thinking ─────────▶│                        │
  │◀── WS: {state: "thinking"}│                         │── LLM 调用 ──────────▶│
  │                         │                         │◀── 流式 tokens ───────│
  │◀── WS: "正在分析..."─────│◀── WS: text_delta ──────│                        │
  │                         │                         │                        │
  │                         │                         │── tool: screenshot() ──▶│
  │◀── WS: {tool: "截图"}───│◀── WS: tool_start ──────│                        │
  │                         │                         │◀── 截图 base64 ───────│
  │◀── WS: {result: "done"}─│◀── WS: tool_result ────│── 继续 LLM ───────────▶│
  │                         │                         │◀── "桌面已整理好" ────│
  │◀── WS: "桌面已整理好"────│◀── WS: text_delta ──────│                        │
  │◀── WS: {type: "done"}───│◀── WS: text_done ──────│                        │
```

### 场景 2: 语音输入

```
浏览器                    FastAPI                    Agent                    Whisper
  │                         │                         │                        │
  │── WS: voice_start ─────▶│                         │                        │
  │... 用户说完后停止 ...      │                         │                        │
  │── WS: voice_stop(audio)─▶│                         │                        │
  │                         │──────────────────────── ASR ─────────────────────▶│
  │                         │◀──────────────────────── "帮我整理桌面" ──────────│
  │                         │── 后续同场景 1 ────────▶│                        │
```

---

## 七、前端 UI 布局设计

```
┌─────────────────────────────────────────────────────────────────────┐
│  JARVIS                              [⚙️ 设置] [📷 摄像头] [🎤 语音]  │
├──────────────────────────────────┬──────────────────────────────────┤
│                                  │                                  │
│         对话区域                   │        监控面板                   │
│                                  │                                  │
│   ┌────────────────────────┐     │   📊 系统状态                     │
│   │ 🟢 Javis:              │     │   CPU: ████░░░░ 40%              │
│   │ 你好, 有什么需要帮忙的？   │     │   RAM: ██████░░ 60%            │
│   └────────────────────────┘     │                                  │
│                                  │   🛠️ 当前工具                     │
│   ┌────────────────────────┐     │   截图 ✗ | 鼠标 ✓ | 摄像头 ✓     │
│   │ 👤 你:                  │     │                                  │
│   │ 帮我打开 Chrome 搜索天气  │     │   🤖 Agent 状态                 │
│   └────────────────────────┘     │   ● THINKING                     │
│                                  │   第 2 步 / 共 4 步               │
│   ┌────────────────────────┐     │                                  │
│   │ 🟢 Javis:              │     │   📋 最近的工具调用                │
│   │ 正在打开 Chrome...       │     │   1. 截图 → ✓ 成功                │
│   │ [工具: 键盘Win+R ████████]│    │   2. 分析 → 进行中...             │
│   └────────────────────────┘     │                                  │
│                                  │                                  │
└──────────────────────────────────┴──────────────────────────────────┘
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  💬 输入你想说/命令...                  [📎 传文件] [🎤 语音] [▶ 发送]│  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 八、打包成桌面版的路径

```
Phase 1 (现在): Web 版
  ├── FastAPI 后端 + 浏览器前端
  ├── 功能完整 (语音/视觉/桌面控制)
  └── 通过浏览器访问 localhost:8080

Phase 2 (成熟后): 桌面封装
  方案 A: Electron 包裹 (推荐)
    ├── Electron 嵌入 Web 界面
    ├── + Node.js 调用系统 API
    ├── + 系统托盘 / 开机自启
    └── electron-builder 打包为 .exe
      
  方案 B: PyQt6 WebView (简单)
    ├── PyQt6 + QWebEngineView
    ├── 加载 Web 界面
    └── PyInstaller 打包为 .exe

  方案 C: 纯 PyQt6 重写 (重)
    ├── 完全重写 UI
    └── 不推荐，迭代成本高
```

### Electron 封装关键点

```javascript
// 桌面版 main.js (Electron)
const { app, BrowserWindow, Tray } = require('electron');

// 1. 启动 Python 后端
const { spawn } = require('child_process');
const server = spawn('python', ['main.py']);

// 2. 创建浏览器窗口加载 Web UI
const win = new BrowserWindow({
    width: 1000, height: 700,
    webPreferences: { nodeIntegration: true }
});
win.loadURL('http://localhost:8080');

// 3. 系统托盘
const tray = new Tray('icon.png');
tray.setToolTip('JARVIS');
```

---

## 九、实施路线 (Web-First)

```
Week 1: Web 基础设施
├── FastAPI 服务器搭建
├── WebSocket 实时通信
├── 基础 Web UI (HTML/CSS/JS)
├── Agent 核心 ReAct 循环
└── LLM 客户端对接

Week 2: 工具集成
├── 桌面控制 (截图/鼠标/键盘)
├── 摄像头 (OpenCV 调用)
├── 语音 (录音/ASR/TTS)
├── 文件/网络/系统工具
└── 工具注册中心

Week 3: 体验优化
├── 流式 LLM 输出
├── 语音打断
├── 记忆系统
├── 配置面板
└── 错误恢复

Week 4: 打包 + 优化
├── Electron 外壳
├── PyInstaller 打包 Python
├── 系统托盘/开机自启
├── 安装程序制作
└── 性能优化
```
