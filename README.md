# JARVIS Agent

> 🤖 自进化桌面智能体 — 截图视觉 · 摄像头 · 桌面控制 · 语音 · 自学习大脑 · 本地优先

JARVIS 是一个运行在 Windows 上的本地 AI 助手。他能看屏幕、控制键鼠、调用摄像头、识别语音，并通过一个自学习的四层记忆系统持续进化。**默认使用 Ollama 本地模型，零数据泄漏，也支持 Anthropic/OpenAI 等云端 API**。

## 快速开始

```bash
# 1. 下载 Ollama → 安装 → 拉取模型
ollama pull qwen2.5:7b

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python main.py
```

浏览器打开 http://localhost:8080

## 连接 GitHub

```bash
git remote add origin https://github.com/Caleb-Top/Javis.git
git push -u origin main
```

使用 Personal Access Token 认证（Settings → Developer settings → Tokens classic）。

## 模型选择

JARVIS 默认使用 Ollama 本地模型，编辑 `config.yaml` 切换 provider：

| 提供商 | 模型示例 | 说明 |
|--------|---------|------|
| 本地 Ollama | `qwen2.5:7b` / `deepseek-r1:8b` | 零网络、零泄漏 |
| Anthropic | `claude-sonnet-4-20250514` | 最强推理 |
| 其他 OpenAI 兼容 | 任意 | 自定义 base_url |

```yaml
model:
  provider: local          # local / anthropic / openai
  name: qwen2.5:7b
  temperature: 0.7
  local:
    base_url: http://localhost:11434/v1
```

## 功能矩阵

| 功能 | 说明 | 状态 |
|------|------|------|
| 💬 多模型对话 | Ollama 本地 / Anthropic / OpenAI 兼容 | ✅ |
| 🧠 ReAct 推理 | 思考 → 行动 → 观察 → 反思循环 | ✅ |
| 🖥️ 桌面截图 | mss 高速截图 + UI Automation 控件树 | ✅ |
| 🖱️ 键鼠控制 | 点击/拖拽/滚轮/快捷键/输入 | ✅ |
| 📷 摄像头 | 多摄像头拍照 | ✅ |
| 📁 文件操作 | 读写/列表 | ✅ |
| ⚡ run_code | AI 自主编写并执行 Python/PowerShell | ✅ |
| 🔍 GitHub 搜索 | Token 认证，5000 req/h | ✅ |
| 🎤 语音输入 | Whisper + SpeechRecognition | ✅ |
| 🔊 语音输出 | Edge-TTS | ✅ |
| 🧠 自学习大脑 | 四层记忆 + 知识注入 + 论文消化 | ✅ |
| 👁️ CVU 视觉理解 | YOLO UI 检测 + 截图语义解析 | ✅ |
| 🌐 浏览器控制 | 自动化网页操作 | ✅ |
| 📄 文档转换 | 20+ 格式互转（PDF/Word/Excel/PPT/Markdown 等） | ✅ |
| 🔌 插件系统 | Codex Skills + Anthropic Plugins 兼容 | ✅ |
| 🗂️ 工作流模板 | 可组合的自动化流水线 | ✅ |
| 🖥️ 后台守护 | 系统托盘常驻 | ✅ |

## 架构

```
┌─────────────────────────────────────────────┐
│  ③ 元认知层 (Meta-Cognition)                  │
│  Planner → Reflector → 策略进化 → 经验积累   │
├─────────────────────────────────────────────┤
│  ② 知识层 (Knowledge + Memory)                │
│  四层记忆: 工作 · 情景 · 语义 · 程序          │
│  自学习大脑: brain.py + learner.py            │
│  知识注入: 论文 / 人类知识 / 跨领域            │
├─────────────────────────────────────────────┤
│  ① 执行层 (Execution)                         │
│  ReAct Agent + 30+ 工具 + 20+ 格式转换       │
│  run_code 自主编程 · 桌面控制 · CVU 视觉      │
└─────────────────────────────────────────────┘
```

## 项目结构

```
Javis/
├── core/           核心引擎 (agent, engine, llm_client, planner, reflector, tool_registry)
├── kernel/         神经内核 (embedder, leaves, cognitive fusion, sleep learning)
├── memory/         四层记忆 (episodic, semantic, procedural, consolidator, controller)
├── knowledge/      知识库 (brain, learner, papers_db, crossdomain, human_knowledge)
├── tools/          工具集 (desktop, camera, cvu, browser, code_exec, daemon, vision)
├── tools_lib/      工具库 (20+ 格式转换, superpowers, plugin_creator, loader)
├── skills/         技能模块 (全功能, 桌面控制, 文件管理, 摄像头, 语音, 超级技能)
├── voice/          语音 (Whisper STT, Edge-TTS, 实时对话)
├── web/            Web UI (HTML/CSS/JS, WebSocket 实时通信)
├── utils/          工具函数 (config_api, memory, logger)
├── docs/           文档 (架构, 多模态方案, 自进化设计, 内存层级)
├── scripts/        启动脚本
├── config.yaml     全局配置 (模型/工具/记忆参数)
└── main.py         主入口 (FastAPI + WebSocket)
```

## 技术栈

**Python 3.11 · FastAPI · WebSocket · Ollama · ReAct · Whisper · Edge-TTS · YOLO · OpenCV · UIAutomation**

## 记忆系统

JARVIS 拥有四层自进化记忆：

- **工作记忆**：当前对话上下文，实时更新
- **情景记忆**：历史对话经验，按相似度检索
- **语义记忆**：结构化知识图谱，压缩去重
- **程序记忆**：执行成功/失败模式，策略进化

记忆控制器后台循环运行（语义同步 5min / 压缩 10min / 摘要 30min），知识库会自动消化论文和跨领域知识。

## 自学习管道

```
用户交互 → 提取经验 → 知识注入到大脑 → 睡眠学习(kernel/sleep_learning)
         → 反思修正 → 策略更新         → InfoNCE 对比训练
```

## License

MIT
