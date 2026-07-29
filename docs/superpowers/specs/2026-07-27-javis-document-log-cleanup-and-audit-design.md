# Javis 文档、日志清理与全面检测设计

日期: 2026-07-27  
状态: 待用户最终审阅  
范围: `D:\Javis` 内文档、运行日志、文档引用、测试和一方源码检测  
硬约束: 不删除代码、模块、测试、App、配置、模型、记忆数据库或用户数据

## 1. 目标

在不影响 Javis 功能完整性和代码完整性的前提下，将分散、重复、过期的文档和运行日志收束为少量权威入口，并建立清理后的完整检测基线。

执行结束后必须同时满足:

1. Javis 的当前架构、App 要求、运行方式、当前状态和检测结果均有唯一权威文档。
2. 过期自检、重复计划、旧 HTML 图、扫描中间产物和大部分历史运行日志被永久删除。
3. 所有保留文档中的链接都指向存在的文件。
4. 不删除或移动任何代码文件、模块目录、测试、App 文件、配置、模型、数据库和工作区数据。
5. 清理前后运行同一组测试和静态检查；出现回归则立即停止清理并定位根因。
6. 只修复有明确失败证据的问题，不借清理任务做无关重构。

## 2. 已比较的方案

### 方案 A: 全部归档

把旧文档和日志移动到 `archive/`。优点是易恢复；缺点是凌乱仍存在，搜索结果继续混入过期材料，不符合用户要求的“删除没必要内容”。

### 方案 B: 提炼后精确永久删除

先建立当前架构、运行手册和最新审计报告，再按显式清单删除旧文档和日志。优点是目录清晰、当前信息唯一；缺点是删除不可逆，因此必须有精确白名单、基线测试和删除后复测。

### 方案 C: 直接清空历史资料

只留下 README 和 App 规格。优点是文件最少；缺点是会丢失后端架构、记忆、感知、安全和运维细节，风险不可接受。

采用方案 B。

## 3. 清理前基线

2026-07-27 只读检查结果:

- `docs/` 内共有 26 个文件。
- 项目根目录共有 10 份 Markdown 文档，其中 9 份为历史自检/架构报告。
- `logs/` 内共有 46 份 `.log`，约 1.68MB。
- 现有测试共 111 项，全部通过。
- 一方 Python 文件 AST 扫描 183 个，语法错误 0 个。
- Git 工作区包含用户现有修改和大量未跟踪的新模块，不能依靠 Git 自动恢复所有内容。
- 代码没有直接读取历史架构文档；文档引用主要存在于总账和旧计划之间。

## 4. 永久保护范围

以下路径不属于删除范围，不得以任何理由删除、移动或批量改名:

```text
app/
blueprint/
control/
core/
evolution/
gateway/
knowledge/
memory/
perception/
providers/
scripts/
skills/
tests/
tools/
tools_lib/
utils/
voice/
web/
agent_distill/
ClaudeAgent_Distill/
main.py
start.py
config.yaml
config.example.yaml
requirements.txt
```

额外保护:

- 所有 `.py`、`.pyi`、`.ts`、`.tsx`、`.js`、`.css`、`.rs`、`.toml` 源码。
- 所有测试文件。
- `app/preview.html`、`app/index.html` 和根目录 `javis_ui_preview.html`。
- 所有 SQLite/DB、会话、记忆、模型、训练数据、上传文件和工作区文件。
- 用户现有 Git 修改和未跟踪代码。

删除动作只能使用本规格第 7、8 节列出的绝对路径集合。禁止目录通配删除，禁止递归删除项目目录。

## 5. 清理后权威文档集合

### 5.1 项目入口

- `README.md`: 重写为当前项目入口，只保留功能、快速启动、目录、测试和权威文档链接。

### 5.2 当前架构

- 新建 `docs/JAVIS_CURRENT_ARCHITECTURE.md`。

该文件合并并保留以下有效内容:

- `JarvisRuntime`、EventBus、Tool Registry 和 Agent 所有权。
- Kernel、Control、Perception、Memory、Evolution 五系统边界。
- 执行层、知识层、元认知层三层映射。
- Provider、工具调用、权限、错误分类、Hooks、Cron、Gateway 和沙箱。
- 四层记忆、FTS5、事件存储、候选记忆和 Brain 激活。
- OCR、YOLO、屏幕、相机和结构化感知事件。
- 当前已完成、部分完成和后续差距。
- 数据流、核心 API 和目录职责。

### 5.3 运维与检测

- 新建 `docs/JAVIS_OPERATIONS.md`。

该文件保留:

- Windows 启动方式。
- 测试模式和完整测试命令。
- 后端健康检查、端口、WebSocket 和日志位置。
- 配置、记忆、模型、工作区和 AppData 路径。
- 安全默认值、隐私规则和故障排查顺序。
- 未经用户批准不得安装或下载依赖的规则。

- 新建 `docs/JAVIS_CURRENT_AUDIT.md`。

该文件记录本轮检测范围、命令、通过项、失败项、根因、修复、复测结果和剩余风险。旧自检报告中的有效结论只保留在该报告和当前架构中。

### 5.4 App 要求

- 保留 `docs/superpowers/specs/2026-07-27-javis-app-v1-master-design.md`，这是 App 第一版不可变要求的唯一详细规格。
- 保留本文件 `docs/superpowers/specs/2026-07-27-javis-document-log-cleanup-and-audit-design.md`，作为不可逆清理边界和复核依据。

### 5.5 项目总账

- 保留并更新 `logs/JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md`。
- 保留并更新 `logs/README.md`。

清理后核心 Markdown 文档为 8 份:

```text
README.md
docs/JAVIS_CURRENT_ARCHITECTURE.md
docs/JAVIS_OPERATIONS.md
docs/JAVIS_CURRENT_AUDIT.md
docs/superpowers/specs/2026-07-27-javis-app-v1-master-design.md
docs/superpowers/specs/2026-07-27-javis-document-log-cleanup-and-audit-design.md
logs/JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md
logs/README.md
```

## 6. 合并规则

删除原件前必须完成以下信息迁移:

| 来源 | 迁移到 |
|---|---|
| `ARCHITECTURE.md`、`JARVIS_DEEP_DESIGN.md` | `JAVIS_CURRENT_ARCHITECTURE.md` |
| `JARVIS_APP_BLUEPRINT.md`、五系统状态文档 | `JAVIS_CURRENT_ARCHITECTURE.md` 与 App V1 规格 |
| `CLAUDE_AGENT_DISTILL.md`、`COMPARISON.md` | 当前架构的能力映射与当前审计 |
| `memory_hierarchy.md`、Memory 状态文档 | 当前架构的记忆章节 |
| `MULTIMODAL_PLAN.md`、Perception 状态文档 | 当前架构的感知章节 |
| `SELF_EVOLUTION.md`、Evolution 状态文档 | 当前架构的进化章节 |
| App 打包、Sidecar、Live Cockpit、App Shell 计划 | App V1 规格、运维文档和总账 |
| 根目录全部历史自检报告 | `JAVIS_CURRENT_AUDIT.md` |
| 旧日志目录统计 | 总账和 `logs/README.md` |

迁移不是整段复制。只保留仍与当前代码一致的事实，并用实际文件、测试或 API 作为证据。

## 7. 文档永久删除清单

在合并文件写完、链接检查通过后，永久删除以下文档:

```text
D:\Javis\docs\APP_PACKAGE_BOUNDARY_AUDIT.md
D:\Javis\docs\APP_SIDECAR_RUNTIME_PLAN.md
D:\Javis\docs\ARCHITECTURE.md
D:\Javis\docs\ARCHITECTURE_IMPROVEMENTS.md
D:\Javis\docs\BLUEPRINT_COVERAGE_STATUS.md
D:\Javis\docs\CLAUDE_AGENT_DISTILL.md
D:\Javis\docs\COMPARISON.md
D:\Javis\docs\DEFECTS.md
D:\Javis\docs\EVOLUTION_ENGINE_STATUS.md
D:\Javis\docs\JARVIS_APP_BLUEPRINT.md
D:\Javis\docs\JARVIS_Architecture.html
D:\Javis\docs\JARVIS_DEEP_DESIGN.md
D:\Javis\docs\JARVIS_WEB_FIRST.md
D:\Javis\docs\MEMORY_EVENT_STORE_STATUS.md
D:\Javis\docs\memory_flowchart.html
D:\Javis\docs\memory_hierarchy.md
D:\Javis\docs\MULTIMODAL_PLAN.md
D:\Javis\docs\PERCEPTION_PIPELINE_STATUS.md
D:\Javis\docs\scan_batch1.json
D:\Javis\docs\scan_result.txt
D:\Javis\docs\SELF_EVOLUTION.md
D:\Javis\docs\SESSION_SUMMARY.md
D:\Javis\docs\V15_PLAN.md
D:\Javis\docs\superpowers\plans\2026-07-25-javis-app-shell.md
D:\Javis\docs\superpowers\specs\2026-07-25-javis-live-cockpit-design.md
D:\Javis\Javis全面自检报告_2026-07-23.md
D:\Javis\P0最终验证.md
D:\Javis\架构三对照报告_2026-07-25.md
D:\Javis\自动自检报告_2026-07-24_09-02.md
D:\Javis\自检报告_2026-07-23_17-10.md
D:\Javis\自检报告_2026-07-23_18-09.md
D:\Javis\自检报告_2026-07-23_22-05.md
D:\Javis\自检报告_2026-07-24_09-08.md
D:\Javis\自检摘要_2026-07-23.md
```

不删除根目录 `README.md`、`requirements.txt` 或任何预览/源码文件。

## 8. 日志永久删除清单

保留:

```text
D:\Javis\logs\README.md
D:\Javis\logs\JAVIS_MASTER_PLAN_AND_LOG_INDEX_2026-07-27.md
D:\Javis\logs\javis_8080.log
D:\Javis\logs\javis_8080.err.log
```

永久删除:

- `D:\Javis\logs\server_20260713_*.log`，15 份。
- `D:\Javis\logs\server_20260717_*.log`，9 份。
- `D:\Javis\logs\server_20260718_*.log`，5 份。
- `D:\Javis\logs\server_20260719_*.log`，15 份。
- `D:\Javis\server_out.txt`，1 份。

共删除 45 份旧运行输出，保留 2 份最近的 8080 主日志和 2 份日志说明文档。

删除前必须再次解析匹配结果，确认每个目标的绝对路径都位于 `D:\Javis\logs`，且文件名以 `server_202607` 开头并以 `.log` 结尾。禁止对计算结果直接执行递归删除。

## 9. 引用更新

删除前更新:

- `README.md` 只链接清理后的权威文档。
- App V1 规格不得链接被删除的旧文档。
- 总账不再把旧文档列为当前入口，只记录“已于 2026-07-27 合并删除”的摘要。
- `logs/README.md` 的日志数量和保留策略更新为实际值。
- 使用全仓库文本搜索确认没有保留文档指向被删文件。

## 10. 全面检测范围

### 10.1 代码完整性

- 枚举清理前后顶层代码目录和一方源文件数量。
- 对所有一方 Python 文件执行 AST 解析。
- 解析所有一方 JSON。
- 解析 YAML 配置，但不输出密钥值。
- 检查重要模块是否存在: Runtime、EventBus、Registry、Agent、Control、Perception、Memory、Evolution、Gateway、Voice、App。
- 检查删除集合中不存在源代码扩展名或受保护路径。

### 10.2 自动测试

- 运行 `python -m unittest discover -s tests -p 'test_*.py'`。
- 单独记录 P0 Runtime 与 App Packaging 测试数量。
- 测试必须使用 `JAVIS_TEST_MODE` 或现有测试隔离，避免启动后台服务和写入生产数据。

### 10.3 Runtime 与 API

- 测试模式导入 `main.py`，确认无启动副作用。
- 枚举 FastAPI 路由，检查相同 HTTP 方法和路径是否重复。
- 验证 `/api/status`、`/api/runtime/status`、Blueprint、Memory、Perception、Evolution 关键接口存在。
- 验证 `/ws` 路由存在。
- 不调用云 Provider，不上传数据，不启动未知后台进程。

### 10.4 安全

- 搜索 `shell=True`、`os.system`、`eval`、动态 `exec`、裸 `except` 和硬编码凭证模式。
- 每个命中按真实上下文分类，不能只按字符串数量判定漏洞。
- 验证路径保护、工具权限、危险命令、上传文件名和 skill/tool 名称测试。
- 验证日志和新文档中不包含明文 API Key。

### 10.5 记忆与数据

- 只读检查 SQLite integrity。
- 检查记忆、会话、事件存储路径存在性和权限，不删除数据。
- 不压缩、不迁移、不重建用户记忆数据库。

### 10.6 App

- 运行现有 13 项 App Packaging 测试。
- 核对 Tauri 配置 JSON、Live Orb、8 状态、VoiceCapture、WebSocket 和启动脚本。
- Node/Rust 不可用时记录为环境阻塞，不自动安装，也不把未运行的 Tauri build 写成通过。

### 10.7 文档与日志

- 检查清理后文档总数。
- 检查 Markdown 相对链接和本地路径。
- 检查保留日志数量、名称和时间。
- 检查旧文档名称不再出现在当前入口中。

## 11. 修复规则

检测到问题后按以下流程处理:

1. 保存失败命令、错误信息、路径和复现步骤。
2. 找到根因和同类正常实现。
3. 为代码问题先增加最小失败测试。
4. 一次只修复一个根因，不捆绑无关重构。
5. 运行目标测试，再运行 111 项完整基线。
6. 若连续三次修复尝试仍失败，停止并报告架构问题，不继续叠加修改。

文档链接、过期描述和清理统计错误可以直接修正文档；代码行为问题必须遵循测试驱动修复。

## 12. 删除执行安全检查

执行删除前，脚本或命令必须逐个验证:

- `Resolve-Path` 后目标以 `D:\Javis\docs\`、`D:\Javis\logs\` 或本规格列出的根目录报告绝对路径开头。
- 目标是文件，不是目录。
- 目标不在永久保护范围。
- 目标扩展名仅为 `.md`、`.html`、`.json`、`.txt`、`.log`。
- 删除列表与本规格完全一致。

执行删除后立即验证:

- 受保护目录全部存在。
- `main.py`、App、测试和核心模块全部存在。
- 45 份旧日志不存在，2 份主运行日志存在。
- 权威文档集合全部存在。
- 完整测试和静态检查重新通过。

## 13. 停止条件

出现以下任一情况立即停止，不继续删除或修复:

- 删除解析结果包含源码扩展名、数据库、配置、模型或目录。
- 任一目标路径不在 `D:\Javis` 预期范围内。
- 合并文档尚未写完或引用检查失败。
- 清理前基线测试不能稳定复现。
- 清理后出现新的测试失败。
- 发现文件同时属于代码运行时依赖和删除清单。

## 14. 验收标准

- 根目录只保留 `README.md` 作为主说明，不再散落历史自检报告。
- `docs/` 只保留当前架构、运维、当前审计和两份有效规格。
- `logs/` 只保留总账、入口和两份最新主日志。
- 所有旧信息的有效结论已迁入权威文档。
- 所有保留文档链接有效。
- 受保护代码和模块数量不因清理减少。
- 完整测试不少于清理前 111 项，全部通过。
- 一方 Python AST 错误为 0。
- 新发现的代码问题均有根因、测试、修复和复测证据；未发现则明确写“未进行无证据修复”。
- 没有安装、下载、联网调用或模型变更。
