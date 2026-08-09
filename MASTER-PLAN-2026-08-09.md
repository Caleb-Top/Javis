# Javis AI OS — 全生命周期完整计划（P0 → P5）

> 版本：MASTER-PLAN-2026-08-09 / 作者：Caleb-Top + Claude
> 定位：这不是一个迭代计划，而是**从今天到"数字生命体"的整条路线图**。每一阶段标注真实起点（基于已核验代码）、目标、验收、依赖。
> 铁律：**任何阶段以真实运行结果为唯一事实来源；不能验证的项标注"需真实验收"，不宣称通过。**

---

## 〇、执行方法论（如何落地本计划）

> 核心思想：**用 Javis 的认知循环理念来建造 Javis**——计划是"记忆层"，执行引擎是"认知层"，工具是"行动层"，每阶段结束都"反思并沉淀"。

### 0.1 通用执行循环（每阶段复用）
```
┌──────────────────────────────────────────────┐
│ 1. 建任务清单（TaskCreate 追踪每项）           │
│ 2. 勘探现状（Explore 代理读真实代码）          │
│ 3. 设计方案（Plan 代理，多方式+推荐）          │
│ 4. 红线测试 → 实现 → 绿线（TDD 红绿循环）     │
│ 5. 独立提交（逐项可回退，禁止混提交）          │
│ 6. 批判审查（竞态/吞错/遗漏/迟到事件复活）     │
│ 7. 真实验收（D 盘，如实记录，不虚构通过）      │
│ 8. 反思总结 → 更新记忆/计划                    │
│ ↺                                              │
└──────────────────────────────────────────────┘
```

### 0.2 工具映射（Claude 工作流 × Javis 阶段）
| 阶段 | 主用工作流 | 辅助 |
|---|---|---|
| P0 语音 | TDD 红绿 + 真实验收 | superpowers、auto-file-analyzer |
| P1 发布 | Explore 勘探 + Plan 代理 | auto-git-sync（授权后） |
| P2 记忆 | Plan 代理 + Workflow 并行 | consolidate-memory |
| P3 认知 | superpowers 计划/执行 | 批判代理跨模块审查 |
| P4 数字人 | Plan 代理 + frontend-design | Explore 勘探 pet/ |
| P5 管家 | Explore + 批判代理（安全审查） | claude-code-guide |

### 0.3 三层验证纪律（Codex SDD 提炼）
- **基线验证**：改动前全量测试跑通（语音 50 passed / 前端 119 passed 为当前基线）
- **聚焦验证**：每项修复跑相关测试（红线→绿线）
- **全量验证**：阶段结束跑全量，确认无回归

### 0.4 记忆沉淀（持续）
每次阶段完成，把非显而易见的东西写入 `~/.claude/projects/G--Javis/memory/`：
技术发现 / 工作流教训 / 用户偏好（如"不虚构通过"）/ 阶段成果。
用 `consolidate-memory` 定期整理。

---

## 一、项目全景定位

```
当前状态：个人 AI 桌面智能体（Tauri + Rust + Python Runtime）
目标形态：Javis AI OS —— 3D 数字生命体 + 全域电脑管家 + 自主进化操作系统
```

已核验的真实底座（2026-08-09）：
- **桌面壳**：Tauri 2.x + Rust + WebView2（exe 24MB，已验证）
- **AI 运行时**：Python 3.11 sidecar + FastAPI + faster-whisper + edge-tts
- **记忆**：`memory/`（episodic/semantic/procedural/consolidation/session_db）+ `kernel/embedder.py`
- **认知**：`core/`（agent/planner/reflector/subagent/memory_kernel/session_recall/prompt_builder）
- **进化**：`evolution/engine.py`（候选发现/验证/回滚雏形）
- **控制**：`control/command_tasks.py` + `core/tool_guardrails.py`（权限/参数/路径检查）
- **感知**：`perception/`（service/video）+ 语音（Whisper/OCR）
- **多通道**：`gateway/`（conversation_ws + telegram/wechat/slack）
- **前端**：`app/src/`（live/pet/code/settings/security/panels）
- **安装**：`installer/` + `scripts/build_javis_app_v3.ps1`

---

## 二、总路线图（六阶段）

```
P0 语音闭环 ──→ P1 可信发布 ──→ P2 记忆系统 ──→ P3 认知进化
     │               │               │               │
     └─── 交付 v3.0.1 ────┐         │               │
                          ▼           ▼               ▼
                    P4 数字生命体(3D)  P5 全域管家+自主进化 → 终极:Javis OS
```

| 阶段 | 名称 | 核心交付 | 前置 |
|---|---|---|---|
| **P0** | 语音闭环 | 语音链路稳定，真实设备验收通过 | — |
| **P1** | 可信发布 | 干净基线 + 可复现构建 + 验收报告 | P0 |
| **P2** | 记忆系统 | 五层记忆统一 + 治理 + 检索 | P1（语音已稳） |
| **P3** | 认知进化 | 规划/反思/子代理/进化闭环 | P2 |
| **P4** | 数字生命体 | 3D 数字人 + 人格 + 情绪 + 语音人格 | P3 |
| **P5** | 全域管家+自主进化 | 电脑控制/权限/数字孪生/自我学习 → Javis OS | P4 |

---

## 三、P0 阶段：语音闭环（当前所在）

> 目标：**让语音在真实 D 盘环境跑通并闭环**。代码层已在 worktree 完成，剩余是真实验收。

### 已完成的代码修复（worktree `fix/p0-voice-reliability`，已提交）
1. **麦克风会话租约**（8 个提交，从 C 盘 1:1 搬运）：断线释放/二次接管/取消安全/重复 stop 单飞
2. **前端状态机 + 看门狗**：3s 确认 / 15s 首字 / 60s 总时限，迟到事件墓碑隔离
3. **空转写终态**：`transcript.empty` → "没有识别到语音，请再说一次"
4. **SNR 诊断指标**：`signal_rms`/`snr_db` 加入 metrics
5. **麦克风设备选择**：设置面板下拉 + WS 启动负载带 `device_index`
6. **TTS 播报失败提示**：不再静默吞错，显示原因

### P0-A 真实设备验收（D 盘，最高优先级）
| # | 门槛 | 方法 | 证据 |
|---|---|---|---|
| 1 | 连续语音 30 轮无永久"思考中" | D 盘安装后逐轮对话 | 录音/日志 |
| 2 | 断线重连各 10 次无麦克风残留 | 关窗/托盘/重开/重连 | 无 `microphone stream belongs to another conversation` |
| 3 | 异常有明确提示 | 拔网/API 失败/Ollama 未启动/麦克风断开 | UI 提示截图 |
| 4 | 20 条中文语料无空识别，字错率<20% | 固定中文短句 → Whisper 转写 | 转写结果表 |
| 5 | 打断响应<500ms | 播放中打断 | 计时 |
| 6 | 终态后 1 秒内恢复聆听 | 完成/失败/取消 | 状态机日志 |

### P0-B 中文语料校准（未做，需真实模型）
- 录制 20 条固定中文短句（数字/命令/常见指令）为 wav
- 跑 Whisper 转写记录字错率，作为回归基线
- **无法在无麦克风环境伪造通过**，必须 D 盘真实验收

### P0 出口标准
- 全部 6 项验收门槛通过
- 中文语料字错率达标
- 语音闭环在真实环境稳定运行 24h 无残留

---

## 四、P1 阶段：可信发布

> 目标：**建立"改得了、能追踪、可复现、敢发布"的工程纪律**。

### P1-1 修复发布基线
- `.gitignore` 补齐：`models/`、`tools/python-runtime-3.11/`、`tools/cvu_data/`、`tools/yolo/`、`artifacts/`（**忽略而非删除**——构建依赖）
- 合并 `fix/p0-voice-reliability` → main（merge --no-ff，逐提交 review）
- 记录源码提交号 + 运行时哈希

### P1-2 一条命令构建流水线
- 从 clean main → 产物 + 版本清单（`runtime-version.json`）
- 产物附带：源码提交号 / 运行时 sha256 / 验收报告
- 打包前自动跑测试，不绿不打包

### P1-3 处理 12 个预存失败（诚实归类）
- 环境性（缺 R1 模型/安装目录）→ 修测试准备
- 真 Bug → 修代码
- 过期断言 → 更新测试
- 每类需运行日志/文件证据

### P1-4 提交纪律固化
- 独立提交（模型安装器/UI/语音/打包分开）
- G 只编译 / D 只验收
- P0 验收通过前不宣称"安装包完成"

### P1 出口标准
- main 工作树干净，可复现构建
- v3.0.1 安装包 + 完整版本清单 + 验收报告

---

## 五、P2 阶段：记忆系统

> 起点（已核验）：`memory/`（episodic/semantic/procedural/consolidation/session_db）+ `kernel/embedder.py` + `core/memory_kernel.py` + `core/session_recall.py` + `knowledge/brain.py`。
> 原则：**在已有模块上加统一门面，不重写**。

### P2-A 统一记忆入口（五层）
| 层 | 已有 | 动作 |
|---|---|---|
| L1 事件 | `memory/episodic.py`、`session_events.sqlite` | 统一事件入口 |
| L2 语义 | `memory/semantic.py`、`knowledge/brain.py` | 事实/知识带置信度 |
| L3 偏好 | `memory/profiles/`、`utils/memory.py` | 偏好带有效期 |
| L4 策略 | `memory/procedural.py` | 行为策略带触发条件 |
| L5 身份 | `knowledge/brain.py`、`prompt_builder` | 用户数字人格模型 |

**核心**：`MemoryManager` 门面收敛读写，统一 schema（来源/时间/置信度/有效期/隐私）。

### P2-B 记忆治理
- 每条记忆：来源 / 时间 / 置信度 / 有效期 / 隐私级别
- 能力：查看 / 修改 / 遗忘 / 导出（JSON）

### P2-C 检索组合
- 第一阶段：SQLite/FTS（已有 session_db）+ 向量（kernel/embedder）
- 第二阶段：关系复杂后引入 Memory Graph
- **不提前上 Graph**，证据驱动升级

### P2-D 认知内核
- `core/agent.py` 已有 live_fast 分流 → 补"行动后记忆写入 + 反思合并"
- 规划→行动→观察→反思 统一循环

### P2-E Critic/Verifier
- `core/tool_guardrails.py` 有执行前检查 → 补"执行后结果校验"
- 重要操作失败自动回滚或提示

### P2 出口标准
- 记忆统一读写 + 治理 + 检索可用
- 语音稳定不受影响（回归全绿）

---

## 六、P3 阶段：认知进化

> 起点（已核验）：`core/planner.py`、`core/reflector.py`（错误分类/反思）、`core/subagent.py`（SubAgentRunner）、`evolution/engine.py`（候选发现）、`core/agent_run_recorder.py`、`core/session_recall.py`。
> 目标：**从"记忆"到"会用记忆思考和成长"**。

### P3-A Planner 增强
- 现状：`core/planner.py` 已有
- 目标：任务拆解 → 依赖图 → 执行调度（接入记忆召回）

### P3-B Reflection Loop
- 现状：`core/reflector.py` 已有错误分类/反思
- 目标：任务后自动反思 → 提炼经验 → 写回记忆（策略层）

### P3-C Multi-Agent / SubAgent 编排
- 现状：`core/subagent.py` 已有 SubAgentRunner + AgentDefinition
- 目标：主 Agent 派生子 Agent（research/coding/security/design），结果校验合并
- Critic 模式：生成 → 审查 → 验证 → 合并

### P3-D Evolution 闭环
- 现状：`evolution/engine.py` 已有候选发现/验证/回滚雏形
- 目标：Observe → Analyze → Hypothesis → Experiment → Measure → Adopt/Reject
- 接入：`core/agent_run_recorder.py` 记录 → evolution 分析 → 改进策略

### P3 出口标准
- 规划/反思/子代理/进化 四闭环打通
- 系统能"从一次失败中学习"（有测试证明）

---

## 七、P4 阶段：数字生命体（3D 数字人）

> 起点（已核验）：前端 `app/src/pet/`（桌宠：PetSurface/PetSkinRegistry/petPreferences/petWindowLayout），**无 3D/avatar 代码**——这是真正的从零开始。
> 方向：不是"状态灯 + 动画人偶"，而是**数字生命交互层**。

### P4-A 3D 引擎选型（需决策）
- **方案 A：Three.js + WebGL（前端内嵌）**——轻量，与 Tauri/WebView2 契合，LOD 可控
- **方案 B：Unity/Unreal WebGL 导出**——逼真度高，但体积大、与 Tauri 集成复杂
- **方案 C：Tauri 内嵌原生 3D（wgpu/bevy via Rust）**——性能最佳，但开发成本最高
- **建议**：P4 先用 Three.js（方案 A）做原型，验证交互闭环；必要时再评估 C

### P4-B 数字人分层
```
数字生命体
├── Conscious Layer（认知状态向量 → 驱动表现）
├── Personality Layer（人格：冷静/专注/守护/进化 状态）
├── Emotion Simulation（功能性情绪：Confidence/Curiosity/Caution/Satisfaction）
├── Memory Layer（接 P2 记忆）
├── Behavior Layer（眼神/姿态/手势/动作 由认知驱动）
└── Expression Layer（表情/嘴型/语音同步）
```

### P4-C 技术组件
- **模型**：Character 模型（GLTF/VRM），可先引入现成基础模型
- **表情**：BlendShape + LipSync（接 TTS 输出）
- **动作**：程序化动画（眼神追踪、呼吸、姿态）+ 关键帧
- **语音人格**：`voice/tts.py` 扩展 Voice Identity（音色/语速/停顿/情绪）
- **状态映射**：`core/` 认知状态 → 前端行为（参考 P0 的状态机但升级为"生命状态"）

### P4 出口标准
- 3D 数字人可在 Live 界面呈现
- 认知状态驱动眼神/表情/动作（非状态灯）
- 语音人格与口型同步

---

## 八、P5 阶段：全域电脑管家 + 自主进化

> 目标：**Javis 从"窗口里的助手"变成"操作系统级管家"**。

### P5-A 电脑控制层（全域管家）
- **文件系统**：`tools/file_ops.py` 已有 → 扩为全盘管理（搜索/整理/备份）
- **进程/软件**：启动/关闭/安装/更新/配置
- **系统优化**：启动项/缓存/服务/性能监控
- **Windows API Bridge**：`control/command_tasks.py` + Rust 层（Tauri）提供权限化系统调用
- **权限模型**：已有 Level 0-3（`tool_guardrails`）→ 扩为动态授权（高风险操作人工确认，低风险自动）

### P5-B 电脑数字孪生（Computer Twin）
- 硬件/软件/环境/工作流 建模
- 持续监控（CPU/内存/磁盘/温度/安全）
- 主动建议（"C 盘剩余 12%，建议清理"）

### P5-C 用户数字孪生（User Twin）
- 接 P2 记忆 → 行为模型（决策习惯/学习方式/偏好）
- 预测性建议（"你通常 9 点开发，已准备好环境"）

### P5-D 自主进化（自学习）
- 观察用户工作流 → 生成 Skill（`core/skill_creator.py` 已有基础）
- 行为模式学习 → 自动化建议
- Evolution 从"候选发现"升级为"主动学习"

### P5 出口标准
- 全域电脑操作（文件/软件/系统）在授权模型内可执行
- 双数字孪生（电脑+用户）建立
- 系统能自主学习用户习惯并主动建议

---

## 九、终极形态：Javis AI OS

```
                 Javis 数字生命体
                        │
              ┌─────────┼─────────┐
       记忆脑        认知脑       进化脑
   (P2 五层记忆)  (P3 规划/反思)  (P5 自学习)
        │            │            │
        └────────┬────┴────┬──────┘
             控制内核 (P5 权限/管家)
                  │
         ┌────────┴────────┐
     桌面/浏览器/代码     数字世界
    (P1 已交付)          (P4 数字人)
```

**一句话**：一个拥有数字人格、理解用户、管理电脑、控制数字世界，并通过长期学习形成个人认知模型的 AI 生命操作系统。

---

## 十、跨阶段依赖与关键路径

```
P0 ──→ P1 ──→ P2 ──→ P3 ──→ P4 ──→ P5
 │       │       │       │       │       │
 语音闭环  可信发布  记忆系统  认知进化  数字生命  全域管家
 需D盘验收  需合并main 需语音稳  需记忆全  需认知稳  需权限全
```

**关键路径**：P0 验收 → P1 合入构建 → P2 记忆 → P3 进化 → P4 数字人 → P5 管家
**不可跳过**：P0（语音是交互底座）、P1（发布纪律是地基）、P2（记忆是护城河）

---

## 十一、风险与决策记录

| 风险 | 应对 |
|---|---|
| D 盘验收暴露真实语音缺陷 | 回 worktree 修复 → 重建 → 复验 |
| 12 个预存失败含真 Bug | 逐条证据归类，不混修 |
| 24GB 未跟踪物 | `.gitignore` 忽略而非删除 |
| 3D 引擎选型不确定 | 先用 Three.js 原型，证据驱动升级 |
| 电脑控制有安全风险 | 权限模型先行，高风险人工确认 |
| C 盘 Codex 隔离区 | 只读 git 读取，改动经 worktree |
| 远程 token | 不 push |

---

## 十二、里程碑与验收汇总

| 里程碑 | 交付 | 验收 |
|---|---|---|
| M1 (P0) | 语音闭环 | 6 项门槛 D 盘全过 |
| M2 (P1) | v3.0.1 可信发布 | 干净基线+构建+报告 |
| M3 (P2) | 记忆系统 | 五层统一+治理+检索 |
| M4 (P3) | 认知进化 | 规划/反思/子代理/进化闭环 |
| M5 (P4) | 3D 数字人 | 认知驱动生命表现 |
| M6 (P5) | 全域管家+自主进化 | 电脑控制+双孪生+自学习 |
| M7 (终极) | Javis AI OS | 数字生命体+全域管家+自主进化 |

---

## 十三、立即执行清单（下一步，待批准）

1. **P0 出口**：合并 fix/p0-voice-reliability → main → 构建 v3.0.1 → D 盘验收 6 门槛
2. **P1 基线**：改 `.gitignore` → 独立提交纪律 → 处理 12 个预存失败
3. **P2 记忆**：MemoryManager 门面 + 治理 schema（语音验收通过后启动）
4. **P3-P5**：依次在 P2 之后展开，每阶段以上一阶段出口为前置

> 本计划基于真实代码核验，非网摘模板。每一阶段都有明确起点、动作、出口与验收证据。执行时以真实运行结果为准，不虚构"通过"。
