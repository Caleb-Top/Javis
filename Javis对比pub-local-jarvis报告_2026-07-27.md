# Javis vs pub-local-jarvis (AI 贾维斯) 全面对比报告

**日期**: 2026-07-27  
**来源**: GitHub [LYiHub/pub-local-jarvis](https://github.com/LYiHub/pub-local-jarvis) (dd8fbf9, 2026-07-24)  
**Javis 基线**: 185 产品 Python 文件, 156 unittest, Blueprint 五系统 score 1.0

---

## 一、项目定位差异

这是两个方向"像"但本质不同的项目。

| 维度 | pub-local-jarvis (AI 贾维斯) | Javis |
|------|----------------------------|-------|
| **本质** | **桌面桌宠** (Desktop Pet) — 陪伴助手 | **桌面 Agent** (Desktop Agent) — 操控助手 |
| **交互模式** | 被动感知 → 主动气泡/弹幕提醒 | 语音指令 → 工具调用 → 执行/操控 |
| **启动方式** | 系统自启后台感知，用户无需主动指令 | 唤醒词/语音/文字输入 → 执行任务 |
| **UI 形态** | 桌面 GIF 桌宠 + 气泡 + 弹幕覆盖层 | Siri-like Live Orb + 抽屉 + Code Surface |
| **桌面框架** | **Electron** | **Tauri 2** |
| **后端语言** | Python + **C++** (原生模块) | Python 纯代码 |
| **AI Runtime** | **内置 llama.cpp 分支** (onnx + GGUF) | 外部依赖 Ollama / 云 API |
| **方向** | "能看懂屏幕、听懂声音" 的陪伴 | "能操控电脑、执行任务" 的助手 |

---

## 二、代码规模对比

### pub-local-jarvis (自有代码)

| 层 | 文件数 | 代码行 | 说明 |
|---|--------|--------|------|
| Python 后端 | ~17 文件 | **5,076** | FastAPI orchestration + memory |
| C++ 原生 | ~12 文件 | **3,540** | 屏幕/音频捕获 + omni 推理 |
| Electron 前端 | ~14 JS | **2,362** | 桌宠/弹幕/启动器/设置 |
| Python 测试 | ~12 文件 | **2,960** | pytest 套件 |
| **自有合计** | **~55** | **~8,000** | |

### Javis (自有代码)

| 层 | 文件数 | 代码行 | 说明 |
|---|--------|--------|------|
| Python 产品 | **185** | **~80,000** | core/tools/memory/knowledge/…所有模块 |
| Python 测试 | ~8 文件 | **~3,500** | unittest |
| TypeScript App | ~18 文件 | **~4,500** | Live/Code/抽屉/Sidecar |
| **自有合计** | **~211** | **~88,000** | |

> **Javis 的 Python 产品代码量是 pub-local-jarvis 的 ~17 倍。**

### 差异本质

pub-local-jarvis 把推理和传感器逻辑塞进 **C++** (llama.cpp fork + DXGI/WASAPI 采集)，Python 只做轻量编排。  
Javis 全部在 Python 层实现——包括工具系统、4 层记忆、知识库、反射器、子代理、权限模型。这是两个完全不同的架构取舍。

---

## 三、架构对比

### pub-local-jarvis 架构

```text
Electron App ←→ Python FastAPI (5K) ←→ C++ Native Worker (3.5K)
                                         ├─ DXGI 屏幕捕获
                                         ├─ WASAPI 音频捕获
                                         ├─ omni_runtime (多模态推理, 77K)
                                         └─ llama.cpp vendor (300K+)
```

- **核心**: `orchestrator/service.py` 77K 字节（单文件巨大）—— 场景感知 + 记忆/弹幕/课程全在里面
- **推理**: 自编译 llama.cpp 分支，支持 MiniCPM-o 多模态 GGUF 模型
- **前端**: Electron + 原生 GIF 桌宠 + CSS 弹幕覆盖层
- **配置**: TOML 格式 (`config/default.toml`)

### Javis 架构

```text
Tauri App ←→ FastAPI + WebSocket ←→ JarvisRuntime
                                        ├─ Agent / LLM Client / Provider
                                        ├─ Tool Registry (30 tools + 40 lib)
                                        ├─ Guardrails / Permission
                                        ├─ Control (audited terminal / fuse)
                                        ├─ Perception (OCR/YOLO/VLM/Video)
                                        ├─ Memory (EventStore/FTS5/Brain)
                                        └─ Evolution (candidate lifecycle)
```

- **核心**: `core/runtime.py` → `JarvisRuntime` 统一装配 → 5 个子系统
- **推理**: 外部 Ollama 或云 API (DeepSeek/GLM/Claude/OpenAI)
- **前端**: Tauri 2 + TypeScript + Live Orb + 可收起状态栏 + 抽屉 + Code Surface
- **配置**: YAML 格式 (`config.yaml`)

---

## 四、功能矩阵

| # | 能力 | pub-local-jarvis | Javis |
|---|------|------------------|-------|
| 1 | **实时屏幕感知** | ✅ C++ DXGI 捕获 + 场景分类 | ✅ YOLO + OCR + VLM via Perception 子系统 |
| 2 | **实时音频感知** | ✅ WASAPI 捕获 + 系统音频分析 | ❌ 仅麦克风输入，不分析系统音频 |
| 3 | **语音对话** | 🟡 全双工多模态，但无流式 TTS | ✅ STT + TTS + 实时语音 WebSocket |
| 4 | **桌面操控** | ❌ 无 (只能看不能点) | ✅ 鼠标/键盘/窗口/音量/应用启动 |
| 5 | **代码执行** | ❌ | ✅ 沙箱 Python/C/C++/Go/Rust/Node |
| 6 | **文件管理** | ❌ 仅课程笔记导出 | ✅ 完整文件读写/搜索/工作区管理 |
| 7 | **网络搜索** | ❌ | ✅ DuckDuckGo |
| 8 | **Git 工具** | ❌ | ✅ git_status/push/pull/clone/branch |
| 9 | **记忆系统** | 🟡 活动时间线 + 每日总结 | ✅ 4 层 (语义/情景/程序/工作) + FTS5 + Brain 知识库 + 知识学习者 |
| 10 | **权限模型** | ❌ 无 | ✅ Role/Guardrail/Risk/Blocked 确认闭环 |
| 11 | **子代理** | ❌ | ✅ SubAgentRunner + run_parallel |
| 12 | **跨平台** | ❌ 仅 Windows | 🟡 核心 Python 跨平台，桌面工具仅 Windows |
| 13 | **定时任务** | ❌ | ✅ cron_scheduler |
| 14 | **自动更新** | ❌ | ✅ GitHub API 检查 + zipfile 备份 + git pull |
| 15 | **消息网关** | ❌ | 🟡 gateway/ 后端 9 文件（Telegram/WeChat/Slack） |
| 16 | **桌面整合** | 🟡 仅桌宠 + 弹窗 | ✅ 截图/键鼠/YOLO/摄像头/OCRVLM/视频分析 |
| 17 | **桌面 Pet** | ✅ GIF 桌宠 + 弹幕 + 气泡 | ❌ 无 |
| 18 | **游戏陪伴** | ✅ 游戏模式 + 弹幕提示 | ❌ 无 |
| 19 | **课程记录** | ✅ 网课自动笔记 + 关键帧 | ❌ 无 |
| 20 | **可视化日程** | ✅ 日程图生成 | ❌ 无 |
| 21 | **内置推理引擎** | ✅ llama.cpp fork (C++) | ❌ 依赖 Ollama/云 API |
| 22 | **主动感知→推送** | ✅ 持续场景分析 + 自动气泡 | 🟡 被动响应指令 |
| 23 | **本地全双工** | ✅ 屏+音实时推理 | 🟡 语音半双工 |

---

## 五、两者最像的地方

你们在以下方面确实有相同的设计思路：

1. **Python FastAPI + WebSocket 后端** — 都是 Python HTTP/WS 后端架构
2. **本地优先** — 都强调隐私和本地运行
3. **Windows 第一平台** — 都面向 Windows 桌面
4. **叫 Jarvis** — 都叫 JARVIS / AI 贾维斯
5. **多模态感知** — 都在做屏幕/视觉内容理解
6. **学习/记忆** — 都有本地记忆系统（尽管实现深度不同）
7. **最近活跃** — 两个仓库在 2026-07-24/25 都有提交，都处于活跃开发

---

## 六、Javis 可以借鉴 pub-local-jarvis 的地方

### 🔥 高价值借鉴

| 能力 | 说明 | 实现难度 |
|------|------|---------|
| **桌宠交互模式** | GIF 桌宠 + 气泡提示是非常轻量但受用户喜爱的交互方式。Javis 的 Live Orb 可以用类似的"桌宠态"补充 | 🟢 前端 CSS/Canvas |
| **游戏陪伴** | pub-local-jarvis 的覆盖层弹幕 + 场景检测对游戏玩家很有吸引力 | 🟡 需要 YOLO + SceneClassifier |
| **全双工感知** | 持续屏幕/音频分析 + 主动推送，"不说也能做"。Javis 目前只响应不主动 | 🔴 需要 C++ 原生模块或重构 |
| **课程记录** | 自动关键帧+笔记是差异化场景，Javis 目前没有教育场景 | 🟡 Python 即可实现 |
| **内置推理引擎** | 自有 llama.cpp 分支，不依赖 Ollama。Javis 依赖外部 Ollama | 🔴 C++ 重工程度 |

### 🟢 小代价借鉴

| 能力 | 说明 |
|------|------|
| 日程可视化 | 已有 image generation API, 只需要一个画面拼装模块 |
| 隐私模式双击切换 | 比 Javis 的"点击按钮触达隐私设置"更自然 |
| 透明弹幕覆盖层 | 现有 Web 前端可用 CSS 实现 |
| 课程 session 管理 | 基于 Javis 现有的 EventStore 可以扩展 |

---

## 七、pub-local-jarvis 可以借鉴 Javis 的地方

| 能力 | 说明 |
|------|------|
| **工具系统** | Javis 有 70 个工具 + 权限模型，pub-local-jarvis 需要吗？取决于它的路线是否从"陪伴"扩展到"操控" |
| **桌面控制** | 如果 pub-local-jarvis 想从"看屏幕"升级到"操作电脑"，Javis 的 desktopp/系统工具是参考 |
| **记忆系统** | Javis 的 4 层记忆 + FTS5 搜索比 pub-local-jarvis 的活动时间线更完整 |
| **权限模型** | pub-local-jarvis 目前没有权限概念——全双工感知+屏幕访问需要权限管理 |
| **Gateway** | 如果要做 Telegram/微信远程交互，Javis 的 gateway 模块可参考 |

---

## 八、战略定位对比建议

```
pub-local-jarvis                    Javis
    │                                   │
    ▼                                   ▼
"陪伴型 AI"                         "执行型 AI"
    │                                   │
    ├─ 场景感知 → 主动推送              ├─ 语音指令 → 工具执行
    ├─ 桌宠交互 → 游戏陪伴              ├─ 桌面控制 → 任务完成
    ├─ 课程记录 → 日程总结              ├─ 文件/代码 → 开发辅助
    └─ 隐私安静 → 不打扰                └─ 全功能 Agent → 复杂任务
    │                                   │
    ▼                                   ▼
更适合：日常陪伴、游戏、学习        更适合：工作自动化、开发、运维
受众：泛用户、玩家、学生            受众：开发者、效率工具用户
```

**两者不是竞争关系，而是互补关系。**

如果用户既想要陪伴又想要操控，理论上两者可以共存：
- pub-local-jarvis 跑桌宠 + 主动感知气泡
- Javis 跑后台 Agent + 语音/工具执行
- 共享同一个 `JarvisRuntime` 内核

---

## 九、综合结论

| 维度 | pub-local-jarvis | Javis |
|------|------------------|-------|
| 自有代码量 | ~8,000 行 | ~88,000 行 |
| 核心架构 | Electron + Python + C++ | Tauri + Python 纯代码 |
| C++/第三方嵌入 | llama.cpp fork, DXGI, WASAPI | 无（依赖外部服务） |
| 前端复杂性 | 中等（桌宠 + 弹幕 + 设置） | 高（Live Orb + 8 状态 + 抽屉 + Code Surface + Sidecar） |
| 后端复杂性 | 低（5K Python 编排 + 场景引擎） | 极高（5 子系统 + 30 工具 + 4 层记忆 + 权限 + Gateway + Evolution） |
| 测试 | 2,960 行 pytest | 156 unittest + 28 TS check + 13 包装测试 |
| GitHub Stars | 新仓库公共 | 私有项目 |

**最大差异**: pub-local-jarvis 走的是一条"小而美"的陪伴路线——用 C++ 做重推理和采集，Python 只做轻量编排。Javis 走的是"大而全"的 Agent 路线——全部用 Python 实现复杂的工具系统、记忆系统、权限系统和桌面控制。

**说方向像，是因为都在做"本地桌面 AI 助手"，但一个像你家猫（默默陪伴），一个像你家管家（帮你做事）。**

### 数据来源

- [LYiHub/pub-local-jarvis — GitHub](https://github.com/LYiHub/pub-local-jarvis)
- 本地解压 `pub-local-jarvis-main.zip` 源码分析
- Javis `D:\Javis` 代码基线 (185 文件, 156 测试, Blueprint 1.0)
