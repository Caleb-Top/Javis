# Javis 外部 Agent 项目融合审计

日期：2026-08-01  
项目根目录：`G:\Javis`  
审计范围：用户提供的 9 个 Agent 源码包、2 个数据集压缩包和 1 个标注文件  
状态：仅完成分析与候选设计，尚未融合外部源码

> 本文是实施层审计，不替换、不删改 Javis 总蓝图。Live 当前版本保持不动。

## 1. 总结论

这些项目里没有任何一个适合整体替换 Javis。Javis 已经具备五大系统、Windows 控制、四层记忆、视觉感知、权限守卫、自进化候选流程和 Tauri App；整套引入外部框架会制造第二套内核、第二套记忆或第二套工具运行时。

推荐采用“原生选择性融合”：

1. 从 1MCP 移植渐进式工具发现思想，增强 Javis Tool Registry。
2. 从 AgentScope 移植事件、权限、中间件和 Workspace 接口设计，不替换 Javis 内核。
3. 从 Convex Agent 移植持久线程、流式增量、审批恢复和使用量记录的数据约束。
4. 从 Xata Agent 移植可恢复的定时 Playbook、通知分级和评测追踪。
5. 将 agency-agents、agent-skills、agents-main 建成按需检索的技能目录，不批量塞进模型上下文。
6. AgentGPT 与 agenticSeek 为 GPL-3.0，只研究机制，不直接复制源码进 Javis 安装包。
7. 两个手写数字数据集没有明确许可证，不进入源码和安装包。

## 2. 输入清单与许可结论

| 输入文件 | 实际项目/内容 | 许可证 | 结论 |
|---|---|---|---|
| `agency-agents-main.zip` | msitarzewski/agency-agents，270 个专家角色提示 | MIT | 可选取、转换、保留归属信息 |
| `AgentGPT-main.zip` | reworkd/AgentGPT | GPL-3.0 | 仅研究，不直接复制进当前 Javis |
| `agenticSeek-main.zip` | Fosowl/agenticSeek | GPL-3.0 | 仅研究，或未来做独立 GPL sidecar |
| `agent-main.zip` | Xata PostgreSQL SRE Agent | Apache-2.0 | 可移植 Playbook/调度/评测模式 |
| `agent-main (1).zip` | Convex Agent Component 0.6.4 | Apache-2.0 | 可移植持久会话与审批恢复模式 |
| `agent-main (2).zip` | 1MCP Agent 0.35.0-beta.2 | Apache-2.0 | 可移植渐进式工具目录与服务治理模式 |
| `agentscope-main.zip` | AgentScope 2.0 | Apache-2.0 | 最强架构参考，适合选择性移植协议 |
| `agent-skills-main.zip` | addyosmani/agent-skills | MIT | 适合导入 Code 技能与评测库 |
| `agents-main.zip` | wshobson/agents | MIT | 适合建立大型按需技能目录 |
| `archive (1).zip` | HWD-V1 手写数字数据集，152,138 张图 | 未发现许可 | 隔离保留，不训练、不分发 |
| `archive.zip` | 手写数字训练/测试集，280,000 张图 | 未发现许可 | 隔离保留，不训练、不分发 |
| `annotations.json` | PDF 高亮坐标，正文基本为空 | 不适用 | 与 Agent 融合无关 |

许可证边界：MIT 与 Apache-2.0 内容可在保留许可证、来源和修改记录的前提下移植；GPL 项目若直接组合进同一衍生程序会带来整体分发义务，因此当前方案禁止直接复制 GPL 源码。

## 3. 逐项目源码结论

### 3.1 agency-agents

实际规模：270 个角色提示，23 个分类。最大分类为 engineering 58、specialized 57、marketing 36、GIS 13、security 12、design 10。

可迁移内容：

- YAML frontmatter：`name`、`description`、`color`、`emoji`、`vibe`。
- 角色职责、工作流、交付物格式、质量规则和工具偏好。
- 面向 Claude、Codex、Gemini、Cursor 等环境的转换器思路。
- MCP memory 和角色安装器的目录组织方式。

不应直接做的事：

- 不把 270 个角色全部注入系统提示，会扩大上下文、降低路由准确率。
- 不让角色提示绕过 Javis 权限、工具风险和工作区边界。
- 不把装饰性字段当作真实能力声明。

建议：导入成只读角色目录；FTS 检索后每次最多激活 1 至 3 个角色，记录来源、版本和许可证。

### 3.2 AgentGPT

技术栈：Next.js、FastAPI、MySQL/Prisma、LangChain、Serper、Docker。核心流程是目标输入、任务生成、任务执行、结果学习与下一轮任务生成，并设置最大循环次数。

可借鉴：

- `AgentRun`、`AgentTask`、`analyze/execute/create/summarize` 的阶段数据模型。
- 最大循环次数与重复总结保护。
- 主记忆失败时回退次级记忆的包装器。
- 工具选择输出的结构化校验。

不建议直接融合：项目较旧，依赖云数据库和旧版 LangChain；Javis 已有更完整的规划、记忆和工具执行。GPL-3.0 也不适合直接进入当前安装包。

### 3.3 agenticSeek

核心模块：BrowserAgent、CoderAgent、PlannerAgent、McpAgent、CasualAgent、AgentRouter、浏览器驱动、SearXNG、语音交互和本地模型适配。

值得研究的点：

- BART 分类器与本地 LLM Router 置信度投票。
- 简单任务路由到专用 Agent，复杂任务路由 Planner。
- 浏览器读取、表单发现、导航反馈和失败重试循环。
- 代码 Agent 按语言选择解释器并读取执行反馈。

局限与风险：

- 浏览器实现包含反指纹、验证码和 `--no-sandbox` 路径，不可直接放入高权限 Javis。
- SearXNG、Redis、Docker 和路由模型增加部署体积与常驻成本。
- GPL-3.0，当前只能借鉴算法与测试场景。

### 3.4 Xata Agent

定位：PostgreSQL SRE Agent。项目包含只读诊断工具、Playbook、监控计划、Slack 通知和评测追踪。

最有价值的机制：

- cron 与自动间隔两类计划。
- `scheduled/running` 状态和运行超时恢复，能处理进程崩溃后遗留的 running 状态。
- 最大并行任务数和延迟执行策略。
- Playbook 运行后先判断通知级别，再决定是否继续下钻其他 Playbook。
- 根因总结中保留已运行 Playbook 链路。
- 测试结果、模型轨迹、Judge 结果和 CSV 汇总。
- `safe explain` 与 `unsafe explain` 分离的能力边界。

建议：把这些模式移植到 Javis 的主动提醒、定时任务和自进化验证，不移植 PostgreSQL 专用 UI 与数据库依赖。

### 3.5 Convex Agent Component

定位：基于 Convex 的 Agent 持久化组件。源码覆盖 threads、messages、streams、files、vector search、React hooks 和 approval。

最有价值的机制：

- Thread 与 Message 分离，消息有稳定顺序和步骤归属。
- 模型流式 delta 独立保存，前端可重连和重放。
- 混合文本/向量消息搜索。
- 文件引用与消息生命周期绑定。
- 每个生成步骤记录 token usage、reasoning token 和 cached token。
- 未解决审批在进入新消息时自动拒绝，避免旧审批悬挂后被误执行。
- 工具审批响应可恢复后续生成，而不是重新跑整轮任务。

建议：只移植数据约束和状态机到 Javis SQLite/EventBus；不增加 Convex 云依赖，也不替换当前 Python 后端。

### 3.6 1MCP Agent

定位：聚合多个 MCP Server 的单一运行时，提供服务生命周期、过滤、模板实例、认证、会话和客户端接口。

最值得移植的机制：

- Agent 面向的三步工作流：`instructions -> inspect -> run`。
- 工具 Schema 不一次性全部进入上下文；先看服务摘要，再看目标工具 Schema，最后执行。
- 静态服务、模板服务、异步加载、懒加载和预热列表。
- Preset 通过 tag、AND/OR/NOT 表达式形成不同工具集合。
- 服务健康检查、失败恢复、配置热更新和版本解析。
- CLI 会话缓存、陈旧会话重试、REST/MCP 回退。
- OAuth 会话和敏感日志脱敏。

对 Javis 的直接价值：当前 `ToolRegistry.get_light_schemas()` 仍会把所有轻量工具放进模型上下文，且没有 inspect 层。1MCP 的渐进披露可明显减少工具上下文、误调用和首次响应延迟。

建议：在 Python 内原生实现 Tool Catalog，不先引入 1MCP TypeScript 常驻 sidecar。未来需要连接大量第三方 MCP Server 时，再把 1MCP 作为可选适配器。

### 3.7 AgentScope 2.0

这是与 Javis 架构层最相关的项目，但不应整体替换 Javis。

已核实的模块：

- 事件：Reply、ModelCall、Text/Data/Thinking Block、ToolCall、ToolResult、UserConfirm、Interrupt、ExternalExecution 等完整类型。
- 权限：PermissionContext、Rule、Decision、Mode、Behavior、Engine。
- 中间件：Base、Budget、RAG、TTS、Tracing、Mem0、ReMe、Agentic Memory。
- Workspace：Local、Docker、E2B、Daytona、K8s、OpenSandbox、Bubblewrap、Apple Container、MCP Gateway。
- 服务：多会话、多租户、消息总线、Scheduler、知识库索引、Agent 中断和团队工具。
- Model：DeepSeek、Ollama、OpenAI、Anthropic、Gemini、Moonshot、DashScope 等。

Javis 可迁移的部分：

- 将字符串事件升级为有 Schema 的事件族。
- 为模型调用、工具调用、记忆检索、审批和输出建立统一 Middleware 生命周期。
- 将本地工作区、受控工作区和未来沙箱统一到 Workspace Backend 协议。
- 权限决策返回结构化 reason、rule、behavior，而不是只返回错误字符串。
- 使用事件投影器给 Live 状态文字和 Code 过程面板提供同一状态来源。

不应迁移：整套 AgentScope App Service、全部第三方 Workspace 和全部模型依赖。它们会复制 Javis 五大系统并大幅增加安装包。

### 3.8 agent-skills

规模：24 个工程技能、4 个 Agent、82 个测试/评测/fixture 文件。

高价值技能：API 设计、浏览器测试、CI/CD、代码审查、代码简化、上下文工程、系统调试、迁移、ADR、前端工程、Git、可观测性、性能、安全、发布、规格驱动和 TDD。

建议：作为第一批 Code 技能导入候选。每个技能先经过 Javis Skill Candidate 校验，禁止脚本默认执行，保留来源与版本，并把原项目评测案例转换成 Javis 技能验收。

### 3.9 agents-main

实际规模：91 个插件目录、204 个 Agent Markdown、180 个 Skill、109 个 Command Markdown。

高相关插件：agent-orchestration、agent-teams、context-management、llm-application-dev、llm-finetuning、debugging-toolkit、tdd-workflows、comprehensive-review、observability-monitoring、security-scanning、ui-design、multi-platform-apps。

建议：只导入索引和用户选择的技能包。整库注入会产生提示冲突、重复技能和不可控工具声明。需要去重、可信度、适用语言、输入输出 Schema 和测试状态字段。

### 3.10 数据集与 annotations

两个 `archive*.zip` 是手写数字图像，不是 Agent 源码。它们理论上可用于 Perception/OCR 的离线训练或回归测试，但目前无明确许可，也与 Javis 主视觉目标不完全匹配。

`annotations.json` 仅含 PDF 高亮坐标，正文为空，无法形成可用知识或训练样本。

结论：三者均不进入 Javis 源码、记忆、训练成果或安装包。

## 4. 与 Javis 五大系统的差距对照

| Javis 系统 | 已有能力 | 外部项目能补的缺口 | 建议来源 |
|---|---|---|---|
| Kernel | Runtime、EventBus、Agent、Provider、Tool Registry | 类型化事件、Middleware、渐进工具目录、健康状态 | AgentScope、1MCP |
| Control | Tool Guard、动作策略、Workspace Manager、Windows 控制 | 结构化权限决策、审批恢复、Workspace Backend 协议 | AgentScope、Convex |
| Perception | OCR、YOLO、VLM、视频分段 | 当前外部 Agent 包没有明显更强实现 | 暂不融合 |
| Memory | SQLite 事件、FTS5、四层记忆、巩固、程序记忆 | Agent Run/Task/Step 持久化、流重放、使用量记录 | Convex、AgentGPT |
| Evolution | candidate/staged/active、验证、回滚 | 技能来源、许可证、评测包、热度和退化指标 | agent-skills、agents-main、Xata |
| Code | 工具执行、子代理、文件/代码/桌面能力 | 工具按需发现、技能目录、持久任务、团队编排、评测 | 1MCP、AgentScope、技能库 |
| Live | 原生球体、状态、语音入口、设置 | 本轮不改视觉与交互；仅未来消费统一事件状态 | AgentScope 事件思想 |

## 5. 推荐融合架构

### 方案 A：原生选择性融合，推荐

保持 Python Kernel、SQLite Memory、Tauri App 和五大系统不变，在现有边界内增加：

1. Tool Catalog：目录、检索、inspect、run、Preset、健康状态和懒加载。
2. Typed Event + Middleware：统一模型、工具、审批、记忆、子代理和 UI 状态。
3. Durable Agent Run：Run/Task/Step/Delta/Approval/Checkpoint，支持恢复、取消和重放。
4. Skill Catalog：MIT/Apache 技能导入、FTS 检索、候选验证、按需激活。
5. Playbook Scheduler：可恢复调度、并发上限、通知级别、评测轨迹。

优点：最符合当前蓝图；本地优先；可逐步上线；不增加第二套前后端。  
代价：需要把外部机制翻译成 Javis 原生接口，不能简单复制整仓库。

### 方案 B：可选 Sidecar

保留 Javis 主内核，再提供可选 1MCP 或 AgentScope 服务，用于大量 MCP Server 或远程沙箱。

优点：接入快，第三方生态完整。  
缺点：多进程、更多端口、更多依赖、更高启动延迟和安装体积；状态一致性更难。

适用条件：用户明确启用“高级开发服务”，不作为默认 App 路径。

### 方案 C：替换为外部框架，不推荐

以 AgentScope、agenticSeek 或 AgentGPT 重建内核。

问题：会丢失或重写当前五大系统、Windows 全权限控制、Live、记忆数据、自进化和安装器；风险与收益不匹配。

## 6. 推荐候选包

### P0：建议首批融合

**A1. 渐进式工具智能**

- 来源：1MCP、AgentScope。
- 目标：`instructions/list -> inspect -> execute`，工具按类别和任务检索。
- 收益：减少上下文、误调用、工具 Schema 延迟。
- Live：不改。

**A2. Code 技能目录**

- 来源：agent-skills 全部 24 个；agency-agents 与 agents-main 先只建索引。
- 目标：按任务检索技能，最多激活少量内容；保留来源、许可证、版本、测试状态。
- 收益：提高 Code 的规划、调试、评测和工程质量。
- Live：不改。

**A3. 持久 Agent Run**

- 来源：Convex Agent、AgentGPT。
- 目标：任务可恢复、步骤可重放、审批可继续、流式状态可重连。
- 收益：解决 App 重启、模型超时或用户中断后整轮丢失的问题。
- Live：只消费状态，不改外观。

**A4. Typed Event + Middleware**

- 来源：AgentScope。
- 目标：模型、工具、思考、审批、记忆和子代理使用统一事件模型。
- 收益：Code/Live/诊断使用同一真实状态源，避免“后端在线但 Kernel 离线”一类状态冲突。

### P1：第二批融合

**B1. Playbook Scheduler**

- 来源：Xata Agent。
- 内容：cron、自动间隔、并发上限、崩溃恢复、通知分级、下钻流程。

**B2. Agent Team 编排**

- 来源：AgentScope、agents-main。
- 内容：角色路由、并发任务、结果聚合、取消、预算和失败策略。

**B3. Eval 与 Trace**

- 来源：Xata Agent、agent-skills。
- 内容：工具选择测试、LLM Judge、轨迹归档、回归门槛和退化报告。

### P2：可选研究

- agenticSeek 双分类器路由：仅重写思想，不复制 GPL 代码。
- 1MCP sidecar：仅大量外部 MCP 服务时启用。
- 手写数字数据集：取得明确许可并证明有 OCR 收益后再考虑。

## 7. 明确禁止项

1. 不替换 Javis 总蓝图或五大系统。
2. 不修改当前 Live 视觉和交互。
3. 不直接复制 AgentGPT、agenticSeek 的 GPL 源码进安装包。
4. 不批量加载 270 个角色、180 个技能到一次模型请求。
5. 不引入 Convex 云作为 Javis 记忆依赖。
6. 不默认常驻第二套 Node/Agent 服务。
7. 不把无许可证数据集放入仓库或安装包。
8. 不让导入技能绕过 Tool Guard、权限等级、审计日志和 Evolution 验证。

## 8. 建议决策

推荐批准：`A1 + A2 + A3 + A4`，随后做完整回归，再决定是否进入 `B1 + B2 + B3`。

这个顺序先解决 Code 当前最实际的问题：工具太多但发现不够聪明、技能没有统一目录、任务中断后不可可靠恢复、各界面状态来源不统一。它不会碰已经优化好的 Live，也不会引入 GPL 或云端强依赖。

用户确认融合范围前，不执行外部源码合并、不清理源 ZIP、不生成新的 v3.0 安装包。
