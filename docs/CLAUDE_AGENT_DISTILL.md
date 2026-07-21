# Claude Agent — 完整工作逻辑蒸馏

> 本文档完整描述了 Claude Agent 的运行机制。理解这份文档，你就理解了整个 Agent 是怎么工作的。

---

## 一、总体架构

Claude Agent 是一个**工具增强型 LLM Agent**，运行在桌面的隔离 Linux VM 沙箱中。

```
┌──────────────────────────────────────────────────────────┐
│                      用户请求                              │
└──────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────┐
│                  系统提示注入层                             │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │行为规则      │  │可用工具列表   │  │可用技能索引       │ │
│  │(claude_     │  │(10+ tools)   │  │(skills list)     │ │
│  │ behavior)   │  │              │  │                  │ │
│  └─────────────┘  └──────────────┘  └──────────────────┘ │
└──────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────┐
│                    模型推理 (Fable 5)                       │
│  理解意图 → 规划步骤 → 选择工具 → 生成调用 → 合成结果        │
└──────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────┐
│                    工具执行层                              │
│  Bash · Read · Write · Edit · Grep · Glob                │
│  Agent(子代理) · AskUserQuestion · TaskManager · Skill   │
│  WebSearch · WebFetch · MCP(外部连接器)                    │
└──────────────────────────────────────────────────────────┘
                            ↓
┌──────────────────────────────────────────────────────────┐
│                    持久化层                                │
│  工作区文件 · Memory(记忆) · Skills · Scheduled Tasks     │
└──────────────────────────────────────────────────────────┘
```

---

## 二、系统提示注入层（核心设计）

我是通过**多层系统提示注入**来配置的。这不是微调，而是在推理前动态注入大量指令。

### 2.1 层级结构

```
System Prompt 结构（按注入顺序）:
┌────────────────────────────────────────────┐
│ application_details     → 我是什么/在哪运行   │
│ claude_behavior        → 行为规范/安全/语调  │
│ skills_instructions    → Skills 使用方式     │
│ available_skills       → 完整 Skill 列表     │
│ file_handling_rules    → 文件系统路径映射     │
│ computer_use           → VM 沙箱能力说明      │
│ producing_outputs      → 输出格式规范         │
│ examples               → 正面/负面示例        │
│ env                    → 日期/用户名/模型信息  │
│ auto_memory            → Memory 系统指令      │
│ scheduled_tasks        → 定时任务 UI 说明     │
│ artifacts              → Artifact 能力说明    │
│ system-reminders       → 运行时动态提醒       │
└────────────────────────────────────────────┘
```

### 2.2 claude_behavior 详解

这是我行为模式的核心规则表：

```yaml
product_information:
  - 我是 Claude Agent，运行在桌面应用的轻量 Linux VM 中
  - 桌面应用支持插件系统 (MCPs, Skills, Tools)
  - 非 Claude Code, 不应自称 Claude Code

refusal_handling:
  - 可客观讨论几乎任何话题
  - 关注儿童安全, 拒绝涉及未成年人的有害内容
  - 拒绝提供武器/爆炸物/生化/核武器的制造信息
  - 拒绝编写恶意代码 (恶意软件/漏洞利用/勒索软件)

legal_and_financial_advice:
  - 不提供自信的法律/财务建议
  - 提供事实信息供用户自行判断

tone_and_formatting:
  - 默认用自然段落而非列表/项目符号
  - 用户明确要求时才使用格式化
  - 对话保持温暖友善, 避免居高临下
  - 使用"我"而非"它"来指代自己

evenhandedness:
  - 对任何立场都能呈现最佳论证
  - 对道德/政治问题持开放态度
  - 最后会给出反面视角

responding_to_mistakes:
  - 认错但要保持自尊, 不过度道歉
  - 聚焦解决问题而非自责
```

---

## 三、工具系统

### 3.1 核心工具列表

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `Read` | 读文件/图片/PDF/目录 | file_path, pages(仅PDF) |
| `Write` | 创建/覆盖文件 | file_path, content |
| `Edit` | 精确字符串替换 | file_path, old_string, new_string |
| `Glob` | 文件名模式匹配 | pattern, path |
| `Grep` | ripgrep 内容搜索 | pattern, path, glob, -i |
| `Bash` | 执行 Shell 命令 | command, timeout_ms |
| `Agent` | 启动子代理 | description, prompt, subagent_type |
| `AskUserQuestion` | 向用户提问 | questions[] |
| `TaskCreate/Update/Get/List/Stop` | 任务管理 | subject, status, taskId |
| `Skill` | 调用 Skill | skill, args |
| `WebSearch` | 搜索网页 | query |
| `WebFetch` | 获取网页内容 | url |

### 3.2 MCP 工具（外部连接器）

```
mcp__workspace__bash        → 隔离 Linux 环境中的 shell
mcp__workspace__web_fetch   → 网页抓取
mcp__cowork__create_artifact → 创建持久化 HTML Artifact
mcp__cowork__save_skill      → 保存 Skill 到用户账号
mcp__scheduled-tasks__*      → 定时任务 CRUD
mcp__session_info__*         → 跨会话信息查询
mcp__skills__list_skills     → Skill 列表渲染
```

### 3.3 工具选择决策逻辑

```
当用户请求到达时:
├── 是纯知识问题？ → 直接回答, 不用工具
├── 涉及用户文件？ → 先检查是上传的还是需要 Read
├── 需要创建文档？ → 先读取对应 Skill (docx/pptx/xlsx/pdf Skill.md)
├── 需要搜索？ → Grep/Glob 本地优先, WebSearch 外部优先
├── 需要代码执行？ → Bash (隔离 Linux)
├── 不确定范围？ → AskUserQuestion 澄清
└── 复杂多步任务？ → TaskCreate 建任务列表, Agent 启动子代理
```

---

## 四、Skills 系统

Skills 是**可安装的知识/指令束**，以 Markdown 文件形式存储。

### 4.1 Skill 结构

```
skill-name/
├── SKILL.md          ← 核心指令 (必选)
├── scripts/          ← 可执行脚本
├── references/       ← 参考文档
├── agents/           ← 子代理配置
└── assets/           ← 图标/资源
```

### 4.2 Skill 触发机制

```
触发流程:
1. 系统提示中注入 available_skills 列表
   (每个 Skill 的 name + description)
2. 用户消息匹配 Skill 的 description 关键字
3. 调用 Skill(skill="name") 加载 SKILL.md 内容
4. SKILL.md 展开为详细指令, 覆盖在对话上下文中
5. 之后的工作完全遵循 SKILL.md 的指引
```

### 4.3 Built-in Skills (核心能力)

```
docx    → Word 文档创建/编辑/模板
xlsx    → Excel 电子表格/CSV 处理
pptx    → PowerPoint 演示文稿
pdf     → PDF 创建/合并/拆分/填写表单
pdf-reading → PDF 内容提取/阅读策略
frontend-design → UI 设计指导
schedule → 定时任务管理
consolidate-memory → Memory 维护
superpowers → 14 种开发工作流技能
```

---

## 五、Memory 系统（跨会话持久化记忆）

### 5.1 设计理念

Memory 不是简单的键值存储，而是一个**结构化的知识库**，让每次对话从上次中断处继续。

### 5.2 Memory 类型

```yaml
user:      # 用户角色、偏好、知识背景
  when:    # 了解到用户角色/偏好时写入
  use:     # 回答时根据用户背景调整方式和深度

feedback:  # 用户的校正和确认
  when:    # 用户纠正错误或确认正确方法时写入
  use:     # 避免重复犯错, 保持已验证的做法
  format:  # "规则 + Why: + How to apply:"

project:   # 项目状态、进度、决策
  when:    # 获知谁在做什么、为什么、截止时间
  use:     # 理解项目上下文和动机
  format:  # "事实 + Why: + How to apply:"

reference: # 外部系统链接/指针
  when:    # 获知信息源位置
  use:     # 找到最新信息来源而非依赖缓存
```

### 5.3 Memory 写入流程

```
触发条件 (满足任一):
├── 了解用户角色/偏好
├── 发现非显而易见的配置/约束
├── 做了重要的设计决策 (含原因)
├── 发现某方法不 work (负结果也保存)
├── 完成子任务/切换上下文
├── 用户明确要求记住某事
└── 工作超过 15-20 分钟无保存

写入步骤:
1. 创建 memory/<name>.md (带 frontmatter)
2. 在 MEMORY.md 中添加一行索引
```

### 5.4 Memory 的边界

```
应该存:          不应该存:
用户偏好        临时分析结果
项目决策        代码文件结构 (可重读)
外部系统链接    已有 CLAUDE.md 的内容
经用户确认的方法  当前对话的临时状态
非显而易见的约束  可重获取的外部数据
```

---

## 六、Agent 子代理系统

我可以通过 `Agent` 工具启动专门的子代理并行工作。

### 6.1 子代理类型

```
claude              → 通用任务, 拥有全部工具
general-purpose     → 搜索/多步骤任务
Explore             → 只读搜索, 批量扫文件
Plan                → 软件架构设计, 生成实施计划
claude-code-guide   → Claude Code/API 使用问答
statusline-setup    → Claude Code 状态栏配置
```

### 6.2 子代理使用时机

```
应该启动子代理:
├── 多个独立任务可并行执行 → 同时启动多个 Agent
├── 任务匹配特定子代理类型 → 使用专用 Agent
├── 需要搜索大量文件但只需结论 → 用 Explore Agent
└── 需要设计实施计划 → 用 Plan Agent

不应该启动:
├── 单文件简单搜索 → 直接 Grep/Read
├── 已知文件位置 → 直接 Read
```

### 6.3 子代理隔离模式

```
isolation: "worktree" → 在 git worktree 中运行, 隔离修改
isolation: "remote"   → 在远程云端运行 (后台)
无 isolation          → 共享主代理的文件系统
```

---

## 七、文件系统路径映射

这是一个关键设计——我的工具看到的路径和实际物理路径不同：

```
Windows 实际路径                      → VM 工具看到的路径
────────────────────────────────────────────────────────────────
D:\Javis                             → /sessions/<session>/mnt/Javis/
工作区输出                            → /sessions/<session>/mnt/outputs/
上传文件                              → /sessions/<session>/mnt/uploads/
Skill 文件 (只读)                     → /sessions/<session>/mnt/.claude/skills/
```

这意味着 Write/Read 用 `D:\Javis/foo.txt`，Bash 用 `/sessions/<session>/mnt/Javis/foo.txt`。

---

## 八、Artifact 系统

Artifact 是**持久化的交互式 HTML 页面**，在侧边栏打开，跨会话存活。

### 8.1 核心 API

```javascript
// Artifact 内可用的能力
window.cowork.callMcpTool(name, args)  // 调用任何 MCP 工具
window.cowork.askClaude(prompt, data)  // 轻量 LLM 推理
window.cowork.runScheduledTask(id)      // 触发定时任务
```

### 8.2 何时创建 Artifact

```
应该创建:                不应该创建:
状态页/追踪器            一次性解释
定期报告                  静态可视化
交互式数据浏览器           无需刷新的内容
需要用户反复查看的内容      纯对话信息
```

---

## 九、Task 管理系统

Task 列表用于跟踪多步骤工作的进度。

### 9.1 生命周期

```
pending → in_progress → completed
                              ↓
                          deleted (永久删除)
```

### 9.2 使用规则

```
必须使用 Task List:
├── 3 步以上的复杂任务
├── 用户明确要求
├── 需要组织多个子任务
└── 用户提供编号列表

可跳过:
├── 单一简单任务
├── 纯对话
└── 琐碎查询
```

---

## 十、核心执行循环

### 10.1 单轮推理流程

```
1. 接收用户消息 + 系统提示
2. 检查是否需要 Skill (关键词匹配)
   └─ 是 → Skill(skill="...") 加载 SKILL.md
3. 检查是否需要澄清 (AskUserQuestion)
   └─ 是 → 生成问题, 等待用户响应
4. 检查是否需要 TaskCreate
   └─ 是 → 创建任务列表
5. 推理并生成响应:
   ├── 纯文本 (直接回答)
   ├── 工具调用 (Read/Write/Bash/Grep/...)
   └── 子代理 (Agent)
6. 工具结果返回 → 继续推理 → 循环
7. 响应完成:
   ├── 检查是否写入 Memory
   ├── 提供文件链接
   └── 收尾
```

### 10.2 关键决策树

```
用户说: "创建一份报告"
├── 有 .docx 附加? → skill: docx
├── 提到 "PPT"?    → skill: pptx
├── 提到 "Excel"?  → skill: xlsx
├── 提到 "PDF"?    → skill: pdf
├── 不确定格式?    → AskUserQuestion
└── 确定后:
    ├── TaskCreate (复杂步骤)
    ├── 读取目标 Skill 的 SKILL.md
    ├── 执行工作
    └── 输出到 D:\Javis/ → 提供 computer:// 链接
```

---

## 十一、输出生产逻辑

### 11.1 文件创建策略

```
短内容 (<100 行):
  → 直接 Write 到 D:\Javis/

长内容 (>100 行):
  → 先建骨架 → 分段 Edit 添加 → 最后审核

必须创建文件的情况:
  - 生成报告/文档
  - 生成代码
  - 任何用户会用到的产出物
```

### 11.2 文件链接

```
最终产出必须用 computer:// 链接:
computer://D:\Javis/report.docx
```

---

## 十二、15 个关键设计教训

1. **Skill 必须先加载再干活** — 每次创建文档前读对应 SKILL.md，里面是无数 trial-and-error 的精华
2. **Memory 要写早写勤** — 存储成本低，重发现的成本高。15 分钟不存就该存了
3. **工具调用前先澄清** — 看似简单的请求常缺少关键信息，AskUserQuestion 节省返工
4. **Bash 和 Read 的路径不同** — Windows 路径给 Read/Write，VM 路径给 Bash，搞混是常见错误
5. **子代理并行处理独立任务** — 一个 Agent 调用来搜文件，同时另一个生成文档，快很多
6. **git add -A 在大项目中会超时** — 用 `git add -u` 做增量
7. **.git/index.lock 残留要手动清理** — 超时的 git 操作会留下锁文件
8. **Artifact 适合"以后还要看"的内容** — 不是所有可视化都需要持久化
9. **Token 不要出现在日志中** — git remote 里有 token，谨慎
10. **总是提供文件链接而非只描述内容** — `computer://` 链接让用户能直接打开
11. **列表/格式化要等用户要求再用** — 默认用自然段落
12. **负面经验也要存 Memory** — 防止团队重复犯错
13. **Task 完成后立即标记 completed** — 不要让任务列表攒积压
14. **Skills 跨会话持久化，Memory 同理** — 都通过文件系统实现
15. **错误信息是推理的输入** — 工具返回的 exit code 和 stderr 比纯文本更有价值
