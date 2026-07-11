# Javis 架构改进计划

> 基于对 Claude Code/Fable 5、GPT-5.x/Codex CLI、Hermes AI、Open Interpreter、AutoGPT、TaskWeaver 的研究
> 最后更新: 2026-07-11

---

## 已落地改进

### ✅ 速度模式选择器 (v3.2)
输入框上方新增 4 档纯文字速度模式，实时切换模型/温度/最大 Token：

| 模式 | 温度 | max_tokens | 自动切换模型 |
|------|------|-----------|-------------|
| 极速 | 0.3 | 2048 | → flash/轻量版 |
| 均衡 | 0.7 | 8192 | 默认模型（默认） |
| 深度 | 0.5 | 16384 | → max/最强版 |
| 极致 | 0.3 | 32768 | 全力以赴 |

灵感来自 Claude Code 的 `fast/smart/ultracode` 和 GPT-5 的推理努力度分级。

### ✅ Fable 5 编程式工具调用模式 (run_code++)
Javis 的 `run_code` 工具天然实现了 Fable 5 的"编程式工具调用"模式 — 模型编写 Python 代码在沙箱内批量调用工具、过滤结果并在上下文中返回摘要。Fable 5 的 benchmarks 显示此模式比逐轮工具调用节省 **24% token** 并提升 **11% 任务成功率**。

### ✅ 异构模型路由
`InferenceEngine` 已经在云 API（主算力）和本地 Ollama（备用）之间路由。借鉴 GPT-5.5 MoE 架构思想，可通过 `config.yaml` 扩展为按任务难度路由：简单任务用轻量模型，复杂任务用强模型。

### ✅ 工作区自我管理 (v3.1)
7 个自我管理工具 + WorkspaceManager 模块，让 Javis 能自主创建、追踪、清理自己的文件 — 对应 Fable 5 的"文件持久记忆"模式（Fable 5 从此模式获得 3 倍于 Opus 4.8 的性能提升）。

### ✅ 折叠进度面板
发送消息后显示折叠式任务进度（分析→方案→执行→验证），任务完成后自动消失。

---

## 待实施改进（按优先级）

### P0 — 立即可以做的

#### 1. 三层系统提示缓存优化
**来源**: Hermes AI + Codex CLI + GPT-5.6 KV 缓存  
**当前问题**: `agent.py` 中 `SYSTEM_PROMPT` 是单块硬编码字符串，每次 LLM 请求都完整发送，无法利用缓存。  
**方案**: 将系统提示拆为 `stable`（身份、规则、行为准则）+ `context`（当前会话信息）+ `volatile`（时间戳、内存摘要）。借鉴 GPT-5.6 的 KV 前缀缓存机制。  
**效果**: 同提供商下连续请求的 API 缓存命中率提升，每次节省数千 token。

#### 2. 规划-执行-反射三阶段循环
**来源**: GPT-5.5 Planner-Executor-Reflector + Fable 5 双层验证  
**当前问题**: 当前 ReAct 循环在同一轮次中混合思考和行动，缺少结构化规划和验证阶段。  
**方案**: 在 `Agent.chat()` 中添加可选 `plan_first` 模式：
1. **规划阶段**: 要求 LLM 输出结构化多步计划后再执行工具
2. **执行阶段**: 按计划逐步执行，每步后自动验证
3. **反射阶段**: 检查结果是否符合预期，必要时重规划
Fable 5 系统卡指出自验证"可被击败"，推荐**外层确定性验证循环**（检查安全约束、复杂度边界、不变量）+ **内层模型自验证**的双层架构。

#### 3. 编程式编排模式 (run_code 形式化)
**来源**: Fable 5 Programmatic Tool Calling + GPT-5.6 可编程工具调用  
**当前问题**: `run_code` 已存在但未作为首选取代逐轮工具调用。  
**方案**: 在 System Prompt 中强化"复杂任务优先用 run_code 批量编程"的导向。Fable 5 的编程式工具调用将 20+ 工具调用合并为**一次推理回合**，消除"乒乓"往返。据统计，此模式在 Fable 5 上节省 24% 输入 token，提升 11% 任务成功率。

#### 4. 分级工具确认
**来源**: Codex CLI 三级风险模型  
**方案**: 引入三级风险：
- `low` — 自动执行（如 file_read, file_list）
- `medium` — 仅通知（如 file_write 到工作区）
- `high` — 需用户确认（如 file_delete, 系统命令）

---

### P1 — 重要改进

#### 5. 分层工具加载
**来源**: Hermes AI 三级加载 + GPT-5.4 Tool Search  
**当前问题**: `tools.get_schemas()` 返回全部工具到每个提示中（当前 35 个工具，随工作区工具增加还会增长）。  
**方案**: Level 0 仅返回元数据（名称+一句话描述，~3k tokens），Level 1 按需加载完整 schema。GPT-5.4 的 Tool Search 可减少约 **47% token 消耗**。

#### 6. 子智能体委托 + 编排器模式
**来源**: Claude Code `AgentTool` + Fable 5 Orchestrator Pattern  
**当前问题**: 无子智能体机制。长任务的上下文持续膨胀。  
**方案**: 新增 `spawn_explorer` 工具（只读子智能体，全新上下文，返回摘要）和可选编排器模式。Fable 5 Orchestrator Pattern 用 Fable 5 规划+Sonnet 5 并行执行，**达到 96% 性能但仅需 46% 成本**。

#### 7. 错误分类与智能恢复
**来源**: ReLoop + Metacog + Fable 5 七路恢复  
**当前问题**: 当前重试在 `agent.py` 的 179-207 行是盲循环（无学习）。Fable 5 有 7 种独立恢复路径。  
**方案**: 新增错误类型分类（参数/权限/超时/循环/API/上下文溢出/输出截断），每种类型有独立恢复路径和断路器（连续 3 次同类失败后停止）。

---

### P2 — 长期演进

#### 8. 异构模型路由（MoE 风格）
**来源**: GPT-5.5 稀疏 MoE 架构  
**方案**: 当前 `InferenceEngine` 仅在云/本地之间路由。扩展为按任务复杂度路由：
- 快速分类 → 轻量模型 (local/flash)
- 中等推理 → 默认模型 (deepseek-v4-pro)
- 复杂长任务 → 云端强模型 + 本地备用

#### 9. 嵌入向量工具选择
**来源**: TaskWeaver `PluginSelector`  
**方案**: 当工具超过 50 个时，使用 embedding 相似度（本地 `sentence-transformers`）动态选择当前请求相关的工具子集。

#### 10. Hooks 系统
**来源**: Claude Code 25+ 生命周期事件  
**方案**: 在 agent 循环中添加 `PreToolUse` / `PostToolUse` / `UserPromptSubmit` / `Stop` 钩子，支持 shell/http/prompt 三种处理程序。

#### 11. 统一提供商抽象
**来源**: Hermes 三模式归一化 + GPT-5.6 多模型家族  
**方案**: 将 `LLMClient` 的 `if provider == "anthropic"` 分支重构为三种 API 模式：`chat_completions` / `anthropic_messages` / `responses`。

---

## 研究来源

### Claude Code / Fable 5
- [Claude Fable 5 公告](https://www.anthropic.com/news/claude-fable-5-mythos-5)
- [自适应思考](https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking)
- [编程式工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)
- [Compaction 上下文压缩](https://platform.claude.com/docs/en/build-with-claude/compaction)
- [Fable 5 编码行为分析](https://github.com/New1Direction/fable-5-playbook)
- [Fable 5 编排器插件](https://github.com/czlonkowski/fables)
- [双层验证架构](https://www.sonarsource.com/jp/blog/why-fable-5-still-needs-a-second-loop/)
- [Claude Code Agent SDK](https://code.claude.com/docs/en/agent-sdk/agent-loop)

### GPT-5.x
- [GPT-5.5 基准测试和功能](https://www.thesys.dev/blogs/gpt-5-5)
- [GPT-5.6 公告](https://openai.com/index/gpt-5-6/)
- [GPT-5.6 家族详解](https://simonwillison.net/2026/Jul/9/gpt-5-6/)
- [GPT-5.5 MoE 架构深度解析](https://cloud.tencent.cn/developer/article/2671799)
- [Codex vs Claude Code](https://www.morphllm.com/comparisons/codex-vs-claude-code)

### 其他
- [OpenAI Codex Agent Loop](https://openai.com/index/unrolling-the-codex-agent-loop/)
- [Hermes Agent 架构](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)
- [Open Interpreter 架构](https://deepwiki.com/OpenInterpreter/open-interpreter/11.1-architecture-principles)
- [AutoGPT Forge 组件系统](https://agpt.co/docs/classic/forge/component-agent-introduction/)
- [TaskWeaver 插件系统](https://microsoft.github.io/TaskWeaver/blog/plugin/)
