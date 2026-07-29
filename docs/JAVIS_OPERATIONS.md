# Javis Operations

更新日期: 2026-07-27  
目标平台: Windows  
原则: 稳定优先；不静默安装、不静默下载、不输出密钥

## Safe Start

使用现有虚拟环境启动后端:

```powershell
Set-Location D:\Javis
$env:PORT='8080'
& 'D:\Javis\venv\Scripts\python.exe' -u .\main.py
```

App-first 预览入口:

```powershell
Set-Location D:\Javis
& 'D:\Javis\venv\Scripts\python.exe' .\scripts\start_javis_app.py
```

只打开 App 预览、不启动后端:

```powershell
& 'D:\Javis\venv\Scripts\python.exe' .\scripts\start_javis_app.py --no-backend
```

Web 调试入口保留在 `http://127.0.0.1:8080`。它不是 App 最终产品入口。

## Health Checks

后端存活:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/status
```

Runtime 状态:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/runtime/status
```

五系统覆盖:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/blueprint/coverage
```

主 WebSocket:

```text
ws://127.0.0.1:8080/ws
```

## Test Mode

测试模式禁止正常启动副作用:

```powershell
$env:JAVIS_TEST_MODE='1'
$env:PYTHONPATH='D:\Javis'
& 'D:\Javis\venv\Scripts\python.exe' -c "import main; print(main.runtime.get_runtime_status())"
```

完整测试:

```powershell
$env:JAVIS_TEST_MODE='1'
$env:PYTHONPATH='D:\Javis'
& 'D:\Javis\venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

App 包装测试:

```powershell
$env:JAVIS_TEST_MODE='1'
$env:PYTHONPATH='D:\Javis'
& 'D:\Javis\venv\Scripts\python.exe' .\tests\test_app_packaging.py
```

P0 Runtime 测试:

```powershell
$env:JAVIS_TEST_MODE='1'
$env:PYTHONPATH='D:\Javis'
& 'D:\Javis\venv\Scripts\python.exe' .\tests\test_p0_runtime.py
```

## App Environment

构建就绪预检只读取文件与缓存，不安装依赖:

```powershell
& 'D:\Javis\venv\Scripts\python.exe' .\scripts\app_build_preflight.py
```

基础环境探针:

```powershell
$env:PYTHONPATH='D:\Javis'
& 'D:\Javis\venv\Scripts\python.exe' -c "from pathlib import Path; from scripts.app_env_probe import probe_app_environment; print(probe_app_environment(Path('D:/Javis')))"
```

2026-07-27 Phase 6 检测结果:

- `app/` 存在。
- `pnpm 11.9.0` 在 PATH。
- 仓库内 Node 22、Rust/Cargo 1.97 可用，但不在统一 PATH。
- 当前 blocker 为 `frontend-dependencies-missing`、`tauri-crate-cache-missing`、`bundle-disabled`、`bundle-icon-missing`。
- 没有执行 Tauri build，也没有自动安装或下载依赖。

任何 `pip install`、`npm/pnpm install`、`cargo install`、`winget install`、`ollama pull` 和模型下载都必须先获得用户明确批准。

## Port 8080

查看占用:

```powershell
Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess
```

查看进程:

```powershell
Get-Process -Id <PID>
```

不要为了释放端口直接结束全部 Python 进程。先确认占用者是否为 Javis，再决定停止方式。App Sidecar 完成后，只停止 App 自己启动的后端。

## Configuration

当前兼容配置位于项目 `config.yaml`，示例位于 `config.example.yaml`。App 版本目标路径是 `%APPDATA%\Javis\config`。

规则:

- API Key 优先从环境变量读取。
- 不在日志、诊断命令或文档中打印 Key。
- Provider 缺少 Key 时进入 not-ready/降级状态，不应导致启动崩溃。
- 模型路径必须可配置，不绑定项目根目录。

## Data Paths

目标运行数据:

```text
%APPDATA%\Javis\config
%APPDATA%\Javis\memory
%APPDATA%\Javis\logs
%APPDATA%\Javis\cache
%APPDATA%\Javis\skills
%APPDATA%\Javis\sessions
D:\JavisModels
D:\JavisWorkspace
```

保护规则:

- 不删除 SQLite、会话、记忆、上传、模型或工作区数据。
- 不在 App 安装目录写运行数据。
- 不把 `venv/`、`Lib/`、`python-embed/`、模型和数据集打入 App。

## Privacy Defaults

- 原始麦克风音频默认不落盘。
- 屏幕截图和相机帧默认不落盘。
- 默认只保存结构化 OCR、对象、摘要、置信度和必要元数据。
- 用户主动上传或明确选择保留时，才保存原始媒体。
- 诊断日志不得包含 API Key、密码、令牌、原始媒体和未脱敏敏感输出。

## Logs

当前保留主日志:

```text
D:\Javis\logs\javis_8080.log
D:\Javis\logs\javis_8080.err.log
```

项目总账:

```text
D:\Javis\logs\JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md
```

App 完成运行数据外置后使用:

```text
%APPDATA%\Javis\logs\app
%APPDATA%\Javis\logs\runtime
%APPDATA%\Javis\logs\audit
%APPDATA%\Javis\logs\crash
```

## Troubleshooting Order

1. 运行环境探针，确认 Python/App/Node/Rust 状态。
2. 检查 8080 端口占用。
3. 请求 `/api/status`。
4. 请求 `/api/runtime/status` 和 `/api/blueprint/coverage`。
5. 查看 `javis_8080.err.log` 最近错误。
6. 在 `JAVIS_TEST_MODE=1` 下运行完整测试。
7. 只对可复现失败做根因修复。

常见情况:

- DeepSeek API Key 缺失: 云 Provider not-ready，检查环境变量或切换已配置 Provider。
- App 只能预览: Node/Rust/Cargo 未配置；未经批准不要安装。
- WebSocket 离线: 先确认后端健康和 8080 端口，再检查 `/ws`。
- OCR/YOLO/VLM 不可用: 查看 `/api/runtime/status` 中 perception adapters，文字交流应继续可用。
- PowerShell 中文日志乱码: 设置 `$env:PYTHONIOENCODING='utf-8'` 后重新运行诊断命令。

## Documentation

- 当前架构: `docs/JAVIS_CURRENT_ARCHITECTURE.md`
- 当前审计: `docs/JAVIS_CURRENT_AUDIT.md`
- App V1 规格: `docs/superpowers/specs/2026-07-27-javis-app-v1-master-design.md`
- 项目总账: `logs/JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md`
