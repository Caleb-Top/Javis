# Javis Memory Hierarchy — 分层记忆架构

## 概述

Javis 的记忆不再是一张大平层，而是按照 **项目 → 对话 → 分支** 三层级结构组织。每层有独立记忆作用域，同时支持向上聚合和跨项目互联。

---

## 一、三级记忆作用域

```
项目级 (Project Scope)
  scope_type = "project"
  ├── 自身记忆：项目简介、目标、技术栈、全局规则、共享工具
  ├── 聚合摘要 ↓ 所有下属对话的关键结论自动提升
  │
  ├── 对话级 1 (Conversation Scope)
  │   scope_type = "conv"
  │   ├── 自身记忆：任务上下文、用户偏好、中间结果
  │   ├── 聚合摘要 ↓ 所有下属分支的工具调用和结论
  │   │
  │   └── 分支级 1.1 (Branch Scope)
  │       scope_type = "branch"
  │       └── 自身记忆：当前对话流、临时变量、单步工具结果
  │   └── 分支级 1.2 ...
  │
  └── 对话级 2 ...
```

### 1.1 分支级 (Branch)
- **粒度**：一次连续对话流（从用户发消息到工具执行完成）
- **保存**：完整的 tool_calls + 用户消息 + 助手回复
- **提升**：本轮对话中 priority≥3 的关键事实 → 自动复制到所属对话级
- **生命周期**：由用户主动创建（⑂ 创建分支），或系统在对话过长时自动分叉
- **检索范围**：仅该分支内的事实 + 所属对话摘要

### 1.2 对话级 (Conversation)
- **粒度**：一个主题下的多次对话
- **保存**：该对话下所有分支提升上来的摘要 + 用户设置的持久事实
- **提升**：对话中 priority≥4 的关键事实 → 自动复制到所属项目级
- **默认分支**：创建对话时自动生成「主线」分支，未显式创建分支前所有消息在主线进行
- **检索范围**：该对话内事实 + 下属所有分支摘要 + 所属项目摘要

### 1.3 项目级 (Project)
- **粒度**：一个完整项目或工作域
- **保存**：项目目标、全局规则、跨对话共享的关键结论
- **提升**：由对话级自动提升，或用户手动固定 (pin)
- **共享工具**：项目中注册的工具可跨对话使用
- **检索范围**：项目内事实 + 下属所有对话摘要 + 已关联的外部项目摘要

---

## 二、记忆检索规则

### 2.1 注入优先级

```
分支级提问:
  1. 分支级自身事实（高优，limit=20）
  2. 所属对话级的摘要和 priority≥4 事实（中优，limit=10）
  3. 所属项目级的基本信息（低优，limit=5）
  4. 已互联的外部项目摘要（低优，limit=3）

对话级提问（无活跃分支时）:
  1. 对话级自身事实（高优，limit=20）
  2. 下属所有分支的摘要（中优，limit=10）
  3. 下属分支中 priority≥4 的关键事实（中优，limit=8）
  4. 所属项目级的事实（中优，limit=10）
  5. 已互联的外部项目摘要（低优，limit=5）

项目级提问（无活跃对话时）:
  1. 项目级自身事实（高优，limit=25）
  2. 下属所有对话的摘要（中优，limit=15）
  3. 下属对话中 priority≥4 的关键事实（低优，limit=10）
  4. 已互联的外部项目共享事实（中优，limit=10）
```

### 2.2 记忆提升机制

```
branch → conv 提升条件:
  - 工具调用成功返回非空结果
  - 用户明确说「记住这个」
  - LLM 标记的知识点（priority≥3）
  └→ 写入 conv 级，标记 source_branch_id

conv → project 提升条件:
  - priority≥4 的事实
  - 用户在对话级固定 (pin) 的内容
  - 跨对话被重复引用 2 次以上的知识点
  └→ 写入 project 级，标记 source_conv_id
```

### 2.3 跨作用域搜索

当用户提问跨层级时：

```
search("数据库设计", scope_id="project_A")
→ 返回 project_A 下所有层级中匹配的结果
→ 按 project > conv > branch 优先级排序
→ 如果 project_A 有互联项目，也搜索互联项目的提升摘要
```

---

## 三、跨项目互联

### 3.1 互联类型

| 类型 | 符号 | 说明 |
|------|------|------|
| **记忆共享** | 🧠 | 只共享提升到项目级的摘要和关键事实 |
| **工具共享** | 🔧 | 共享该项目下注册的工具清单 |
| **完全互联** | 🔗 | 同时共享记忆和工具，且允许当前项目调用互联项目的工具执行 |

### 3.2 互联配置

```json
{
  "project_id": "proj_A",
  "connections": [
    {
      "target_id": "proj_B",
      "type": "full",         // "memory" | "tool" | "full"
      "direction": "bidirectional" | "outbound" | "inbound",
      "created_at": "2026-07-13T00:00:00Z",
      "label": "后端数据库项目"
    }
  ]
}
```

### 3.3 互联对检索的影响

```
项目A 提问时，如果项目A 与 项目B 是「完全互联」:
  1. 项目A 自身记忆（高优）
  2. 项目A 下属对话摘要（中优）
  3. 项目B 的提升摘要（中优）← 来自互联
  4. 项目B 的共享工具（可用）← 来自互联

项目A 对话级提问:
  1. 本对话记忆（高优）
  2. 本对话分支摘要（中优）
  3. 所属项目A 事实（中优）
  4. 互联项目B的提升摘要（低优）
```

### 3.4 互联管理界面

侧栏底部「工作台」区域新增互联管理：
- 🔗 互联项目列表（显示已关联的项目名和互联类型）
- ➕ 添加互联（弹出搜索/选择项目对话框）
- 每个互联项可设置类型（🧠/🔧/🔗）和方向
- 互联后在项目名前显示互联标记

---

## 四、数据结构

### 4.1 记忆事实（扩展）

```python
@dataclass
class Fact:
    content: str
    category: str
    priority: int      # 1-5
    confidence: float
    created_at: float
    scope_id: str      # 所属作用域ID
    scope_type: str    # project / conv / branch
    source_id: str | None   # 提升来源的原始 scope_id
    tags: list[str]    # 搜索标签
```

### 4.2 Scope 模型

```python
@dataclass
class Scope:
    scope_id: str
    scope_type: str           # "project" | "conv" | "branch"
    parent_id: str | None     # 上级 scope_id
    name: str
    summary: str              # 由周期性压缩生成
    summary_updated_at: float
    connections: list[Connection]  # 跨项目互联（仅 project 级有）
    created_at: float
    archived: bool = False

@dataclass
class Connection:
    target_id: str
    conn_type: str            # "memory" | "tool" | "full"
    direction: str            # "bidirectional" | "outbound" | "inbound"
    label: str
    created_at: float
```

---

## 五、实现计划

| 阶段 | 内容 | 文件 |
|------|------|------|
| P0 | Scope 模型 + Fact 扩展 scope 字段 | `memory/scope.py`, `knowledge/brain.py` |
| P1 | 检索逻辑增加层级过滤和提升规则 | `memory/controller.py`, `memory/retriever.py` |
| P2 | 摘要压缩（按 scope 周期执行） | `memory/indexer.py` |
| P3 | 跨项目互联模型和检索 | `memory/connections.py` |
| P4 | 前端侧栏 scope 创建/切换/互联管理 UI | `web/` |
| P5 | System Prompt 注入规则适配 | `core/agent.py` |
