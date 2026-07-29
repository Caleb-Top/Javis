# Javis

Javis 是运行在 Windows 上的本地 AI 助手。它通过 FastAPI、WebSocket、语音、桌面工具、结构化感知、长期记忆和受控执行，将本地模型或云 Provider 组合成一个可观察、可审计的 Agent Runtime。

## Current Status

- Python Runtime 与 Blueprint 五系统已运行。
- 156 项测试通过，185 个产品 Python 文件无语法错误。
- Control 已有审计命令、root token、熔断和回滚。
- Perception 已有 OCR、YOLO、VLM、屏幕、相机和视频分析。
- Memory 已有 EventStore、FTS5、候选审核、Brain 激活和工作流物化。
- Evolution 已有验证门、性能记录和回归回滚。
- App Phase 2、3、4 已实现：桌面 Live Surface、统一状态、可靠消息队列、会话/控制抽屉和真实 Code Surface。
- App V1-E 发布硬化代码已实现：进程归属、Javis 身份探测、托盘、关闭隐藏、AppData 日志、严格 CSP、错误恢复和诊断面板。
- App Phase 6 已实现安全预览启动、机器可读构建预检、打包清单修复和首次启动隐私/路径确认。
- Live 是 Tauri App 打开后的直接主界面；`preview.html` 仅用于无依赖视觉测试。
- Tauri 尚未正式构建；只读预检当前报告 `frontend-dependencies-missing`、`tauri-crate-cache-missing`、`bundle-disabled`、`bundle-icon-missing`，未经批准不会安装或下载依赖。

## Architecture

```text
Tauri App / Web / Voice / Gateway
              |
      FastAPI + WebSocket
              |
         JarvisRuntime
     ├─ Kernel and Agent
     ├─ Control
     ├─ Perception
     ├─ Memory
     └─ Evolution
```

详细架构见 `docs/JAVIS_CURRENT_ARCHITECTURE.md`。

## Safe Start

```powershell
Set-Location D:\Javis
$env:PORT='8080'
& 'D:\Javis\venv\Scripts\python.exe' -u .\main.py
```

App-first 预览:

```powershell
& 'D:\Javis\venv\Scripts\python.exe' .\scripts\start_javis_app.py
```

Web 调试入口: `http://127.0.0.1:8080`
WebSocket: `ws://127.0.0.1:8080/ws`

## Tests

```powershell
$env:JAVIS_TEST_MODE='1'
$env:PYTHONPATH='D:\Javis'
& 'D:\Javis\venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

不需要 API Key 的启动与测试路径必须保持可用。

只读构建预检:

```powershell
& 'D:\Javis\venv\Scripts\python.exe' .\scripts\app_build_preflight.py
```

## Core Capabilities

- 多 Provider 对话和推理路由。
- ReAct、计划、反射和错误分类。
- 桌面、文件、浏览器、摄像头、语音和代码工具。
- 权限、审计命令、沙箱、熔断和回滚。
- 工作、情景、语义和程序四层记忆。
- OCR、YOLO、本地 VLM 和长视频变化检测。
- Gateway、Hooks、Cron、技能和子代理基础。
- Live Orb 桌面 App 路线。

## Repository

```text
app/          Tauri/Vite/TypeScript App
core/         Runtime、Agent、LLM、Registry、EventBus
control/      审计命令、权限、熔断和回滚
perception/   OCR、YOLO、VLM、屏幕、相机和视频
memory/       EventStore、FTS5、候选、语义和程序记忆
evolution/    候选、验证、性能和回滚
gateway/      外部消息适配器
tools/        核心工具
tools_lib/    扩展工具库
voice/        STT、实时语音和 TTS
web/          Web 调试和回归界面
tests/        Runtime 与 App 测试
docs/         当前架构、运维、审计和有效规格
logs/         主日志与项目总账
```

## Canonical Documents

- 当前架构: `docs/JAVIS_CURRENT_ARCHITECTURE.md`
- 运行与排障: `docs/JAVIS_OPERATIONS.md`
- 当前审计: `docs/JAVIS_CURRENT_AUDIT.md`
- App V1 规格: `docs/superpowers/specs/2026-07-27-javis-app-v1-master-design.md`
- 项目总账: `logs/JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md`

## Data Policy

- 原始麦克风、屏幕和相机媒体默认不持久化。
- API Key 不写入日志或文档。
- 模型、记忆、日志、缓存和工作区逐步迁移到 App 安装目录之外。
- 不把 `venv/`、`Lib/`、`python-embed/`、模型、数据集或 Git 历史打入 App。
- 所有下载和安装操作必须先获得用户明确批准。

## License

MIT
