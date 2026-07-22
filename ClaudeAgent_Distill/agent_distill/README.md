# Claude Agent — 完整蒸馏

一个工具增强型 LLM Agent 的完整实现。包含核心循环、工具系统、Skills、Memory、Tasks、Artifacts、子代理等所有子系统。

## 项目结构

```
agent_distill/
├── core/
│   ├── types.py           # 核心数据类型: Message, ToolCall, ToolDef, MemoryEntry, Task, SkillDef
│   ├── llm_client.py      # LLM 客户端: Anthropic / OpenAI / Ollama 统一接口
│   ├── tool_registry.py   # 工具注册表: 注册/查找/执行, 含 6 个内置工具实现
│   ├── system_prompt.py   # 系统提示构建器: 拼接各注入层
│   ├── agent.py           # ★ 核心执行循环: 推理→工具→推理 的完整流程
│   ├── memory_manager.py  # Memory 系统: 四类记忆的读写和注入
│   ├── skill_manager.py   # Skills 管理器: 扫描/加载/关键词匹配
│   ├── subagent.py        # 子代理系统: 并行执行, 工具白名单
│   └── artifacts.py       # Artifact 系统: 持久化 HTML 页面
├── tests/
│   └── test_core.py       # 9 项核心测试 (全部通过)
├── main.py                # 入口: create_agent() + CLI 交互模式
└── README.md              # 本文件
```

## 快速开始

```bash
# 安装依赖
pip install anthropic openai pyyaml pillow

# 运行测试 (无需真实 API)
cd agent_distill
PYTHONPATH=.. python tests/test_core.py

# CLI 交互模式 (需要 Ollama 或其他 API 密钥)
python main.py /path/to/workspace
```

## 核心架构

```
用户输入
    ↓
SystemPromptBuilder (注入 Memory + Skills + 行为规范)
    ↓
LLMClient.chat() → 推理
    ↓
    ├── 纯文本 → 返回结果
    └── tool_calls → ToolRegistry.execute()
            ↓
        结果追加到对话历史 → 回到 LLMClient.chat()
            ↓
        ...循环直到 LLM 返回纯文本...
```

## 子系统说明

### LLM 客户端 (llm_client.py)
统一 Anthropic/OpenAI/Ollama 的调用接口。自动检测可用 provider，支持 tool_use 多轮对话。

### 工具注册表 (tool_registry.py)
所有工具的中央仓库。内置 6 个工具: read_file, write_file, edit_file, bash, grep, glob。

### 系统提示 (system_prompt.py)
组装注入给 LLM 的完整系统提示，包含: 行为规范、环境信息、Memory 上下文、Skills 列表、产出规范。

### Memory 系统 (memory_manager.py)
四类跨会话记忆: user/feedback/project/reference。以 Markdown + frontmatter 存储，自动管理 MEMORY.md 索引。

### Skills 系统 (skill_manager.py)
可安装的知识束。从文件系统扫描 SKILL.md，支持关键词匹配自动触发。

### 子代理 (subagent.py)
并行启动专用子代理，各类型有不同的工具白名单。Explore(只读)、Plan(设计)、general-purpose(全部)。

### Artifacts (artifacts.py)
持久化交互式 HTML 页面。支持 Chart.js/Grid.js/Mermaid，通过 window.cowork API 调用 MCP 工具。

## 运行测试

```
cd agent_distill && PYTHONPATH=.. python tests/test_core.py
```

9 项测试覆盖: 核心类型、工具注册、系统提示、Memory、Skills、Task、Artifact HTML、路径映射、Agent 基本流程。
