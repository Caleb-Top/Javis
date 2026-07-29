# Javis Current Audit

审计日期: 2026-07-27  
状态: 清理、修复、App V1-E 与 Phase 6 构建就绪复测已完成  
范围: 一方代码、测试、Runtime、五系统、App、文档、日志、配置和本地数据库

## Original Cleanup Baseline

| 检查 | 结果 |
|---|---|
| 受保护入口 | 23/23 存在 |
| unittest | 111/111 通过 |
| 一方产品 Python AST | 183/183 通过 |
| AST 错误 | 0 |
| Blueprint 五系统 | 5/5 running，score 1.0 |
| App 包装测试 | 包含在 111 项基线内，最近单独验证 13/13 |
| App 环境 | app 存在；pnpm 可用；Node/Rust/Cargo 不在 PATH |

完整测试命令:

```powershell
$env:JAVIS_TEST_MODE='1'
$env:PYTHONPATH='D:\Javis'
& 'D:\Javis\venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

输出结论: `Ran 111 tests`，`OK`。

## Protected Inventory

受保护目录:

```text
app blueprint control core evolution gateway knowledge memory perception
providers scripts skills tests tools tools_lib utils voice web
agent_distill ClaudeAgent_Distill
```

受保护入口文件:

```text
main.py
start.py
requirements.txt
```

产品 Python 文件基线:

| 路径 | 数量 |
|---|---:|
| `main.py` | 1 |
| `core` | 23 |
| `control` | 2 |
| `evolution` | 2 |
| `gateway` | 9 |
| `knowledge` | 6 |
| `memory` | 11 |
| `perception` | 5 |
| `providers` | 1 |
| `skills` | 12 |
| `tools` | 30 |
| `tools_lib` | 40 |
| `utils` | 5 |
| `voice` | 4 |
| `blueprint` | 1 |
| `scripts` | 5 |
| `agent_distill` | 14 |
| `ClaudeAgent_Distill/agent_distill` | 12 |
| 合计 | 183 |

## Runtime and Blueprint

测试模式 Runtime:

- `startup_side_effects=false`。
- 模型配置为 `deepseek-v4-flash`，缺少 Key 时 LLM 进入 not-ready。
- always-on 工具 8 个。
- 子系统: `control`、`evolution`、`perception`。
- 测试事件库位于系统临时目录，不写生产事件库。

`/api/blueprint/coverage` 结果:

| 系统 | 状态 | 关键证据 |
|---|---|---|
| Kernel | running | EventBus、Runtime-owned Agent、always-on 工具 |
| Control | running | 审计命令、root token、fuse、持久回滚 |
| Perception | running | OCR、YOLO、VLM、视频分析 |
| Memory | running | SQLite、FTS、候选审核、Brain 同步、工作流物化 |
| Evolution | running | 候选池、验证门、质量/延迟基线、回归回滚 |

旧报告中 Control 0.55、Evolution 0.55 和总分 0.82 的结论已经过时，不再作为当前状态。

## App Baseline

已存在:

- Tauri/Vite/TypeScript 工程骨架。
- Live Orb 首屏。
- 8 个 Live 状态。
- WebSocket/HTTP 后端桥。
- MediaRecorder 语音桥。
- App-first 启动脚本。
- 包装边界和 13 项静态测试。

环境探针:

```text
app_dir_exists=True
node=''
npm=''
pnpm='11.9.0'
rustc=''
cargo=''
```

没有运行 Tauri build，也没有安装依赖。

## Document and Log Cleanup

清理批准范围:

- 合并后删除 34 份旧文档/根目录报告。
- 删除 44 份时间戳 server log。
- 删除 `server_out.txt`。
- 保留两份 8080 主日志。
- 不删除任何代码、模块、测试、配置、模型、数据库或用户数据。

精确边界见 `docs/superpowers/specs/2026-07-27-javis-document-log-cleanup-and-audit-design.md`。

## Diagnostic Notes

- 首次覆盖率诊断错误地读取了不存在的 `main.blueprint_auditor` 属性。
- 追踪 `/api/blueprint/coverage` 后确认真实路径是 `await main.api_blueprint_coverage()` 或 `BlueprintAuditor(runtime).coverage()`。
- 这是诊断命令假设错误，不是生产代码缺陷，因此未修改生产代码。
- PowerShell 捕获部分测试日志时出现中文乱码；测试退出码和 unittest 结果正常。运维文档已给出 UTF-8 环境变量处理方式。

## Cleanup Result

永久删除:

- 34 份旧架构、状态、计划和根目录自检报告。
- 44 份时间戳 `server_*.log`。
- 根目录 `server_out.txt`。
- 合计 79 个批准文件。

删除验证:

| 检查 | 结果 |
|---|---|
| 删除目标残留 | 0 |
| 受保护入口缺失 | 0 |
| 根目录历史 Markdown 报告 | 0 |
| 时间戳 server logs | 0 |
| 保留原始主日志 | 2 |
| 清理后 `docs/` 文件 | 5，临时实施计划删除后 |
| 最终核心 Markdown | 8 |

保留原始日志:

```text
logs/javis_8080.log
logs/javis_8080.err.log
```

没有删除或移动任何代码、模块、测试、App、配置、模型、数据库、记忆、上传或工作区数据。

## Repaired Defects

### 1. Conversation Rename False Success

症状: `api_mem_rename` 读取或写入 `memory/index.json` 失败时，裸 `except` 吞掉异常并返回 `ok=True`。

根因: 异常分支没有返回失败，函数落入无条件成功结果。

TDD 证据:

- 新增 `test_memory_rename_reports_index_read_failure`。
- 修复前: 预期 `ok=False`，实际为 `True`，测试失败。
- 修复后: API 返回 `ok=False` 和受限错误摘要，目标测试通过。

修改:

- `main.py`
- `tests/test_p0_runtime.py`

### 2. GitHub Token File Handle Leak

症状: 单独运行 Runtime 测试时，`tools/setup.py` 报告未关闭 `.token` 文件的 `ResourceWarning`。

根因: 使用 `open(...).read()`，文件句柄依赖垃圾回收关闭。

TDD 证据:

- 新增 `test_tool_setup_closes_github_token_file`，使用真实临时 token 文件。
- 修复前: 捕获 1 个 `ResourceWarning`，测试失败。
- 修复后: 使用上下文管理器，资源警告为 0，目标测试通过。

修改:

- `tools/setup.py`
- `tests/test_p0_runtime.py`

## Post-Cleanup Verification

| 检查 | 结果 |
|---|---|
| unittest | 113/113 通过，`OK` |
| 产品 Python AST | 183/183 通过，错误 0 |
| JSON | 98 个解析通过 |
| YAML | 3 个解析通过 |
| 配置解析错误 | 0 |
| FastAPI 路由 | 65 条 |
| 重复 HTTP method/path | 0 |
| 必需路由缺失 | 0 |
| Blueprint | score 1.0，五系统 running |
| SQLite | 2/2 integrity check 为 `ok` |
| App Packaging | 13/13 通过 |
| 受保护入口 | 23/23 存在 |
| 隔离复测 | 事件库 SHA-256 前后一致，`brain_data` 最新写入时间前后一致 |

关键路由已确认:

```text
/api/status
/api/runtime/status
/api/blueprint/coverage
/api/memory/search
/api/perception/ingest
/api/evolution/review
/ws
```

## Security Review

一方代码扫描:

| 模式 | 数量 | 结论 |
|---|---:|---|
| `shell=True` | 0 | 未发现 |
| `os.system` | 0 | 未发现 |
| `eval` | 0 | 未发现 |
| credential-like literals | 0 | 未发现硬编码长凭证 |
| dynamic `exec` | 4 | 代码执行工具和本地 Provider 扩展的设计能力 |
| bare `except` | 104 | 主要用于可选工具、媒体、索引和降级路径；保留为治理风险 |

4 处 `exec` 位于 `tools/code_exec.py` 和 `tools/provider_loader.py`。它们属于明确的代码执行/插件能力，外围已有工具权限、沙箱或本地 Provider 边界；本轮没有无证据移除功能。

104 处裸 `except` 没有批量替换。批量修改会改变可选依赖和降级行为；本轮只修复了可稳定复现的会话重命名伪成功。后续应按组件逐个增加错误日志和回归测试。

## Data Integrity

只读 SQLite 检查:

```text
brain_data/memory.db: ok
memory/session_events.sqlite: ok
```

检测期间曾有一次定向测试未预先设置 `JAVIS_TEST_MODE=1`，从而误触发完整 Runtime 启动。该启动执行了 Brain 的常规加载、强化和落盘路径，并改写了被 Git 忽略的 `brain_data/` 内容；这些文件没有可验证的检测前备份，因此没有进行猜测性删除或回滚，现状按原样保留。

同次启动改动了受版本控制的 `memory/session_events.sqlite`。该文件已从 `HEAD` 精确恢复，恢复后工作区哈希与 `HEAD` 均为 `6dc3fd67323c0072ff4c83b184c05d9406ab5b37`，且 Git 状态无差异。没有主动运行数据迁移、VACUUM 或数据删除。新增定向测试及本文件中的测试命令均已显式启用 `JAVIS_TEST_MODE=1`，防止该路径再次产生启动副作用。

## App Verification

- 完整 Python 套件 156/156 通过；包含监督器、Phase 2/3/4、发布硬化、构建预检、首次启动、文件冲突与异步命令取消测试。
- 28 个 TypeScript 文件通过 Node 22 `--check`；185 个一方 Python 文件 AST 错误为 0。
- FastAPI 当前 68 条路由，其中 66 条 HTTP method/path、1 条 WebSocket，重复 HTTP method/path 为 0。
- `980x720`、`760x560`、`1280x800` 视觉测试均无页面溢出。
- `760x560 + 状态栏展开 + 6 行输入` 下 Orb/字幕、字幕/输入、输入/Dock 重叠面积均为 0。
- 宽屏状态栏覆盖 `0..1280`；诊断面板在 `760x560` 下页面溢出与控件裁切均为 0。
- 首次启动 Surface 在 `760x560` 下无溢出；200% 文字缩放无横向溢出，长内容在 Surface 内部滚动，初始焦点和 Escape 边界成立。
- 紧凑 Code Surface 的文件树抽屉可通过按钮打开，宽度 240px，页面溢出为 0。
- Live 是 Tauri 桌面 App 的直接主 Surface；`preview.html` 只作为无依赖测试夹具。
- Rust 1.97 与 Cargo 1.97 可用，三个 Rust 文件通过 `rustfmt --check`；离线 `cargo check` 因缓存中没有 `tauri` crate 而停止，没有联网重试。
- 未运行正式 Tauri build 或 `tsc`：App `node_modules` 不存在；未安装或下载依赖。
- `scripts/app_build_preflight.py` 为只读检查，当前四个 blocker 是前端依赖缺失、Tauri crate 缓存缺失、bundle 未启用、应用图标缺失；包边界检查通过。
- 安全预览启动器不再调用 `start.py`；自定义 `PORT` 优先于配置文件，并要求后端返回 `service=javis`。
- 监督器状态与事件写入 `%LOCALAPPDATA%\Javis\logs\supervisor`，相同错误连续 3 次熔断。
- 最终隔离复测前后，`memory/session_events.sqlite` SHA-256 均为 `5E74DB9897D72D8CB109474C175A1D7908756EDD6C627359CF1E19A41085296E`，`brain_data` 最新写入时间保持不变。

## Residual Risks

- 104 处裸 `except` 需要后续按组件治理，不能一次性机械替换。
- PowerShell 捕获 Python 输出时部分中文日志可能乱码；可设置 `PYTHONIOENCODING=utf-8`。
- Tauri build、安装包、签名以及编译后托盘和真实 Sidecar 端到端行为尚未验证。
- 真实 Windows 200% 系统 DPI 仍需在编译后的 Tauri WebView 中验收；当前只完成 200% 文字缩放压力测试。
- TypeScript 尚未经过正式 `tsc` 类型检查；当前环境没有 App 的本地 `node_modules/.bin/tsc`，本轮没有执行安装。
- 当前 Git 工作区包含大量用户既有修改和未跟踪模块，本轮未回退、暂存或提交它们。
- 一次误触发真实 Runtime 启动改写了被忽略的 `brain_data/`；因缺少可验证的检测前备份，已保留现状并在 Data Integrity 中完整记录。

## Final Conclusion

文档和日志已按批准白名单完成精简，App Phase 2、3、4、V1-E 与 Phase 6 构建就绪工作已完成当前环境可执行的实现和验证。完整套件为 156/156，事件库哈希与 `brain_data` 写入时间在最终测试前后均不变。下一阶段只处理预检列出的四个构建 blocker，然后执行正式类型检查、Tauri 编译、真实桌面端到端测试、签名和安装包验证。
