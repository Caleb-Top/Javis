# Javis Agent

> 🤖 本地智能助手 — 语音、摄像头视觉、桌面控制

## 快速开始

```bash
# 1. 下载并安装 Ollama (本地模型运行平台)
#    https://ollama.com → 下载 Windows 版 → 安装

# 2. 拉取模型 (推荐 qwen2.5:7b，中文能力强)
ollama pull qwen2.5:7b

# 3. 安装 Python 依赖
pip install -r requirements.txt

# 4. 启动
python main.py
```

浏览器打开 http://localhost:8080

## 本地模型

用 **Ollama** 在本地运行 AI，不依赖任何外部 API：

| 模型 | 大小 | 中文能力 | 命令 |
|------|------|---------|------|
| `qwen2.5:7b` | ~4GB | ⭐⭐⭐⭐⭐ | `ollama pull qwen2.5:7b` |
| `qwen2.5:3b` | ~2GB | ⭐⭐⭐⭐ | `ollama pull qwen2.5:3b` |
| `llama3.1:8b` | ~4.5GB | ⭐⭐⭐ | `ollama pull llama3.1:8b` |

编辑 `config.yaml` 中 `model.name` 即可切换。

## 功能

| 功能 | 状态 |
|------|------|
| 💬 文字对话 | ✅ |
| 🧠 ReAct 推理 | ✅ |
| 🖥️ 桌面截屏/鼠标/键盘 | ✅ |
| 📷 摄像头拍照 | ✅ |
| 📁 文件操作 | ✅ |
| ⚡ 系统命令 | ✅ |
| 🎤 语音输入 | 🚧 后续 |
| 🔊 语音输出 | 🚧 后续 |

## 项目结构

```
Javis/
├── core/           # 核心引擎 (agent, llm_client, tool_registry)
├── tools/          # 工具集 (桌面/摄像头/文件/代码执行)
├── skills/         # 技能模块 (全功能/语音/桌面控制...)
├── knowledge/      # 知识库系统 (自学习大脑)
├── brain_data/     # 持久化学习记忆 (经验/事实)
├── voice/          # 语音 (Whisper STT + Edge-TTS)
├── web/            # 前端页面
├── utils/          # 工具函数
├── platfw/         # 跨平台抽象层
├── data/           # 运行时数据 (日志 / TTS缓存)
├── learn/          # 学习资料
│   ├── code/       #   编程语言与算法
│   ├── platform/   #   基础设施与框架
│   ├── tools/      #   开发工具与CLI
│   └── reference/  #   参考材料
├── scripts/        # 启动脚本与工具
├── docs/           # 文档
├── memory/         # 对话存档
├── uploads/        # 上传文件
├── harness_output/ # 技能构建输出
├── ollama_models/  # 本地 Ollama 模型
├── config.yaml     # 全局配置
└── main.py         # 主入口
```

## 技术栈

Python 3.11 · FastAPI · WebSocket · Ollama · ReAct
