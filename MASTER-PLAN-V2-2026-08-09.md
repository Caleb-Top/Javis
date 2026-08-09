# Javis AI OS — 全生命周期深度实施计划 V2（P0 → P5）

> 版本：MASTER-PLAN-V2-2026-08-09
> 生成方式：6 个深度设计代理（实际读取源码）+ 1 个批判代理审查 + P2 补跑 + 执行引擎融入
> 铁律：**任何阶段以真实运行结果为唯一事实来源；不能验证的项标注"需真实验收"，不虚构通过。**

---

## 〇、执行方法论（融入本计划）

> 用 Javis 的认知循环理念来建造 Javis：计划是"记忆层"，执行引擎是"认知层"，工具是"行动层"，每阶段结束反思沉淀。

### 通用执行循环（每阶段复用）
```
1. 建任务清单（TaskCreate 追踪）
2. 勘探现状（Explore 代理读真实代码）
3. 设计方案（Plan 代理，多方式+推荐）
4. 红线测试 → 实现 → 绿线（TDD 红绿循环）
5. 独立提交（逐项可回退，禁止混提交）
6. 批判审查（竞态/吞错/遗漏/迟到事件复活）
7. 真实验收（D 盘，如实记录，不虚构通过）
8. 反思总结 → 更新记忆/计划
```

### 工具映射
| 阶段 | 主用工作流 | 辅助 |
|---|---|---|
| P0 语音 | TDD 红绿 + 真实验收 | superpowers、auto-file-analyzer |
| P1 发布 | Explore 勘探 + Plan 代理 | auto-git-sync（授权后） |
| P2 记忆 | Plan 代理 + Workflow 并行 | consolidate-memory |
| P3 认知 | superpowers 计划/执行 | 批判代理跨模块审查 |
| P4 数字人 | Plan 代理 + frontend-design | Explore 勘探 pet/ |
| P5 管家 | Explore + 批判代理（安全审查） | claude-code-guide |

### 三层验证纪律
- **基线验证**：改动前全量测试跑通（当前基线：语音 50 passed / 前端 119 passed）
- **聚焦验证**：每项修复跑相关测试
- **全量验证**：阶段结束跑全量，确认无回归

---

## 一、真实基线（六阶段设计代理核验，2026-08-09）

| 维度 | 实测 |
|---|---|
| 桌面壳 | Tauri 2.x + Rust + WebView2（exe 24MB） |
| AI 运行时 | Python 3.11 sidecar + FastAPI + faster-whisper + edge-tts |
| 记忆 | `memory/`（episodic/semantic/procedural/consolidation/session_db）+ `kernel/embedder.py` + `core/memory_kernel.py` + `core/session_recall.py` |
| 认知 | `core/`（agent/planner/reflector/subagent/memory_kernel/prompt_builder） |
| 进化 | `evolution/engine.py`（候选发现/验证/回滚雏形） |
| 控制 | `control/command_tasks.py` + `core/tool_guardrails.py` + `core/action_policy.py` |
| 多通道 | `gateway/`（conversation_ws + telegram/wechat/slack） |
| 前端 | `app/src/`（live/pet/code/settings/security/panels） |
| 测试 | main 373 passed / 前端 119 passed / 语音 50 passed；worktree 5 环境性失败 |
| 发布 | 版本号 3.0.0 硬编码 18 文件；无单一版本源；.gitignore 未覆盖 24GB 大目录 |
| D 盘 | runtime 仍为 v3.0.0（08-05），P0 修复未生效真实环境 |

---

## 二、六阶段总览与推荐方案速查

| 阶段 | 核心目标 | 步骤数 | 关键推荐方案 |
|---|---|---|---|
| **P0** | 语音闭环真实验收 | 6 | 全量构建安装；E2E 验收 harness；故障注入真实验证看门狗 |
| **P1** | 可信发布流程 | 5 | 单一版本源 version.py；merge --no-ff；release_v301.ps1 单命令 |
| **P2** | 记忆系统 | 6 | MemoryManager 门面 + adapter；SQLite 治理表；遗忘评分；hybrid 检索 |
| **P3** | 认知进化 | 6 | Planner 任务图；Reflection 闭环；Multi-Agent 编排；Evolution 闭环 |
| **P4** | 数字生命体 | 6 | Three.js + three-vrm；VRM 模型；认知状态→人格→情绪→行为→表达 |
| **P5** | 全域管家+自主进化 | 6 | Python 子系统扩展；Authorizer 统一决策；双孪生；自学习闭环 |

---

## 三、各阶段详细计划（摘要 + 推荐方案）

> 完整多方案细节见附录 A-F（.plan-build/design-*.json），或本章展开。

### P0 语音闭环真实验收（6 步）
1. **P0 基线锁定与 D 盘安装刷新**：`run_python_tests.ps1` 全绿 → `build_javis_app_v3.ps1` 全量构建 → D 盘安装 → 校验 runtime-version.json。方案 A 全量构建（推荐）/ 方案 B 增量运行时替换（前端有改时不成立）
2. **验收链路通探**：新 harness 脚本覆盖 30 轮对话 / 断线重连 10 次 / 故障注入。方案 A harness 脚本（推荐）/ 方案 B 手动清单
3. **固定中文语料校准**：录制 20 条 → CER 计算 → 基线存档。方案 A 录制+CER（推荐）/ 方案 B SAPI 自检
4. **异常提示验收**：拔网/API 失败/Ollama 未启动/麦克风断开各有明确提示。方案 A E2E 脚本+人工确认（推荐）/ 方案 B 手动
5. **看门狗真实验证 + 缺陷闭环**：三档超时各触发一次（**注意：需黑洞端点/进程挂起/STALL 注入，停 Ollama 是快速失败测不到超时**）。方案 A 故障注入（推荐）/ 方案 B 前端 fake-timer
6. **24 小时浸泡 + 验收报告**：后台浸泡监控，报告落盘 `docs/P0_ACCEPTANCE_REPORT_2026-08-09.md`

### P1 可信发布流程（5 步）
1. **修复发布基线**：`.gitignore` 补齐大目录 + 单一版本源 `scripts/version.py`（推荐 A1）/ 全局改 18 文件
2. **合并 P0 分支到 main**：`merge --no-ff` + 逐提交 review（推荐 A1）/ squash merge
3. **单命令构建 v3.0.1**：`release_v301.ps1` 编排全部脚本（推荐 A1）/ 扩展 build 脚本
4. **处理 5 个预存失败**：修测试环境（preflight 共享根修复 + conftest 环境守卫，推荐 A1）/ 只在 main 跑
5. **提交纪律固化**：文档 + 提交模板 + 校验脚本（推荐 A1）/ git hooks

### P2 记忆系统（6 步）
1. **MemoryManager 门面 + 五层 adapter**（推荐 A：纯新增门面，不碰底层）
2. **治理**：SQLite 治理表 + 查看/修改/遗忘（软删可逆）/导出（推荐 A）
3. **遗忘机制**：`score = importance × freshness × (1+usage)`，long_term 豁免，dry-run 先行（推荐 A）
4. **检索组合**：FTS 预筛 + 向量重排 + RRF 融合，降级链完备（推荐 A 两段式）
5. **Agent 集成**：规划召回 + 行动写入 + 反思合并（推荐 A：既有 hook 点最小替换）
6. **Graph schema 预设计 + 审计**（推荐 A：常量模块 + 只读审计，实现放后续）

### P3 认知进化（6 步）
1. **Planner 增强**：任务图 + 依赖调度 + 计划持久化（推荐 A：Agent 循环内最小闭环）
2. **Reflection 闭环**：计划后反思 + 失败经验即时写回策略层（推荐 A）
3. **Multi-Agent + Critic**：`core/orchestrator.py` 编排层，角色预测 + recorder + 自动 Critic（推荐 A）/ 工具层扩展（B）/ 研究-编码-合并流水线（C）
4. **Evolution 闭环**：真实事件收集 + 自动 review + 精选扩展（推荐 A）/ 沙箱验证（B）
5. **失败学习测试**：确定性红线（fake LLM 捕获 Agent.chat 链路，推荐 A）/ 真实模型冒烟（B）
6. **集成 + 全量回归**（推荐 A：渐进合并）

### P4 数字生命体（6 步）
1. **3D 渲染选型**：Three.js + three-vrm（推荐 A）/ WebGPU / Unity WebGL / Tauri Rust
2. **角色模型 + BlendShape**：VRM 标准模型（推荐 A）/ 自定义 GLTF / 外部模型源
3. **口型同步 LipSync**：词边界时间戳 → 前端 blendshape（推荐 A）/ 时长+文本包络（B）/ 音频分析（C）
4. **认知状态→人格→情绪→行为→表达映射**：`core/life/` 后端 + WS 事件（推荐 A）/ 前端映射 / 客户端-服务器双端
5. **语音人格 Voice Persona**：`tts.py` 扩展 `synthesize(text, persona, emotion)`（推荐 A）/ 前端仿真 / 音色切换
6. **双端整合 + 回归**：AvatarSurface 同时服务 Live 与 pet（推荐 A）

### P5 全域管家+自主进化（6 步）
1. **系统控制扩展**：Python 子系统 + psutil + ctypes 扩 tools（推荐 A）/ 全走 CommandTaskRunner
2. **动态权限模型**：`core/authorizer.py` 统一决策 + Token 授权 + 学习降权（推荐 A）/ 中间件
3. **电脑数字孪生 Computer Twin**：`twin/` 包建模 + 监控线程 + 建议规则（推荐 A）/ 轻量版
4. **用户数字孪生 User Twin**：事件聚合习惯画像（推荐 A）/ LLM 生成
5. **自学习闭环**：workflow 实执行 + auto_skill 生成（推荐 A）/ evolution 候选
6. **主动建议推送 + 多通道接入**：proactive 服务 + gateway 真实接 ConversationHub（推荐 A）/ 仅 WS

---

## 四、批判审查结论与修正（已纳入）

### 高严重度（5 条）
1. **P2 缺失** → 已补跑完整 6 步设计（本章 P2 节）✅
2. **P0 验收绑定 v3.0.0 被 P1 升版作废** → 修正：P0 直接在 v3.0.1 分支上做，或 P1 S3 后补"v3.0.1 重跑 6 门槛+复浸泡"
3. **看门狗三档验证用错故障注入** → 修正：黑洞端点 / 进程 SUSPEND / JAVIS_TEST_STALL 注入
4. **计数口径矛盾（12 vs 5；50 vs 47）** → 修正：以实测统一"main 0 失败 / worktree 5 项环境性"，语音计数注明范围
5. **P1 步骤顺序致 S3 未在最终 main 验证** → 修正：重排 S1→S2→S4→S5→S3 或 S5 后加"最终 main 全量复跑"

### 次要（6 条，已纳入各阶段）
- P4 blend shape 双写入仲裁（AvatarSurface 内加合成器+单测）
- P0 hardware_tested 改为真实链路自动推导置真
- P0 CER 门槛加备选路径（beam/参数调整）
- P5-1 kill_process 排除 Javis 自身进程树
- P3 出口措辞对齐（P4 不依赖 P3，真正消费是 P5）
- P5-6 多通道并发 session 映射单列测试

---

## 五、跨阶段依赖与关键路径

```
P0 ─→ P1 ─→ P2 ─→ P3 ─→ P4 ─→ P5
语音验收  可信发布  记忆系统  认知进化  数字生命  全域管家
```

**修正后依赖**：
- P0 → P1：P0 在 v3.0.1 分支上做（避免升版作废）
- P1 → P2：P2 依赖 P1 的干净 main
- P2 → P3/P4/P5：三阶段均依赖 P2 记忆，但各有降级（P3 直接调 MemoryController / P4 落 persona.json / P5 用 utils.memory.save_profile）
- P3 → P5：P5 消费 P3 四闭环（P4 不依赖 P3）

---

## 六、执行顺序建议（关键路径）

```
第 1 步：P1-1 版本源 + .gitignore（快，先做）
第 2 步：P0 全量构建 v3.0.1 → D 盘验收 6 门槛（关键路径）
第 3 步：P1-2 合并 P0 → main + 单命令构建 + 预存失败处理
第 4 步：P2 记忆（门面→治理→遗忘→检索→集成→Graph）
第 5 步：P3 认知（Planner→Reflection→Multi-Agent→Evolution）
第 6 步：P4 数字人（Three.js→VRM→LipSync→状态映射→语音人格→整合）
第 7 步：P5 管家（控制→权限→双孪生→自学习→推送）
```

---

## 七、附录索引

| 附录 | 内容 | 位置 |
|---|---|---|
| A | P0 完整多方案 JSON | `.plan-build/design-P0语音闭环真实验收.json` |
| B | P1 完整多方案 JSON | `.plan-build/design-P1可信发布流程.json` |
| C | P2 完整多方案（本会话补跑） | 见上文 P2 节（6 步全展开） |
| D | P3 完整多方案 JSON | `.plan-build/design-P3认知进化.json` |
| E | P4 完整多方案 JSON | `.plan-build/design-P4.json` |
| F | P5 完整多方案 JSON | `.plan-build/design-P5.json` |
| G | 批判审查全文 | `.plan-build/critique.txt` |

---

## 八、铁律复核

- ✅ 各阶段"需 D 盘真实验收"项均明确标注（识别率/麦克风占用/SAPI 音色/打断听感）
- ✅ 不可测方案被排除（P3 LLM 反思 C、P4 前端 Analyser C、P5 方案 B 伪通过面）
- ✅ 计数口径统一为实测（main 373 / worktree 5 环境性）
- ✅ 伪通过面消除（P0 hardware_tested 改为自动推导）
- ✅ 每步红线测试先行，每阶段独立提交，不虚构"通过"
