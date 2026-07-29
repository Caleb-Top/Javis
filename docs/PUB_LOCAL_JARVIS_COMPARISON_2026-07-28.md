# Javis 对比 pub-local-jarvis 报告

日期：2026-07-28

来源：
- GitHub：LYiHub/pub-local-jarvis
- 本地 zip：`C:/Users/34247/Documents/Downloads/pub-local-jarvis-main.zip`
- 本地解压：`D:/Javis/tmp/pub-local-jarvis-main/pub-local-jarvis-main`
- Javis 项目：`D:/Javis`

## 结论

方向确实非常像。

但不是同一类产品。

pub-local-jarvis 是“本地多模态桌宠”。核心是常驻屏幕和系统音频感知，主动用气泡、弹幕、课程笔记介入。

Javis 是“执行型 AI 操作系统雏形”。核心是工具调用、桌面控制、权限、记忆、感知管线、自进化和 App Live 主入口。

最合适的路线不是照抄，而是吸收它的“常驻陪伴层”，接到 Javis 现有内核上。

## 一句话定位

| 项目 | 本质 | 最强能力 |
| --- | --- | --- |
| pub-local-jarvis | 桌面陪伴型 AI | 常驻屏幕/系统音频感知，主动气泡和弹幕 |
| Javis | 执行型 AI Agent | 工具执行、权限控制、长期记忆、桌面操控、App Live |

## 代码规模修正

旧报告里“Javis 约 88k 行”口径偏大。

我重新按业务代码口径统计，排除了运行时、模型、venv、Lib、缓存、下载物。

### pub-local-jarvis

| 部分 | 文件数 | 行数 |
| --- | ---: | ---: |
| Python 后端 `src/jarvis_backend` | 27 | 5,076 |
| Python 测试 `tests` | 12 | 2,960 |
| Electron 桌面端 `desktop/src` | 24 | 3,251 |
| Electron 测试 `desktop/test` | 18 | 1,390 |
| C++ 原生层 `native` | 20 | 3,540 |
| 第三方 runtime/vendor | 1,332 | 805,463 |

注意：80 万行主要是第三方 llama.cpp 相关 vendor，不应算成它自己的业务代码。

### Javis

| 部分 | 行数 |
| --- | ---: |
| `core` | 5,971 |
| `web` | 4,967 |
| `tools_lib` | 4,823 |
| `agent_distill` | 3,938 |
| `memory` | 3,194 |
| `app` | 3,161 |
| `tests` | 3,046 |
| `gateway` | 1,628 |
| `knowledge` | 991 |
| `control` | 691 |
| `perception` | 667 |
| `voice` | 655 |
| 其他核心目录 | 746 |

当前 Javis 筛选后的业务相关文件约 296 个，约 38,313 行。

## 架构对比

### pub-local-jarvis

```text
Electron 桌宠 UI
  -> Python FastAPI 控制平面
    -> C++ native worker
      -> DXGI 屏幕采集
      -> WASAPI 系统音频采集
      -> MiniCPM-o / llama.cpp-omni 本地推理
```

特点：
- Electron 做桌宠、气泡、弹幕、托盘、隐私模式。
- Python 做场景编排、课程、记忆、API。
- C++ 做低延迟屏幕/音频采集和本地推理。
- 模型是 MiniCPM-o 4.5 GGUF。
- 首次运行需要下载约 6.32 GiB 模型。

### Javis

```text
Tauri App Live 主界面
  -> FastAPI + WebSocket
    -> JarvisRuntime
      -> Agent / LLM Provider
      -> Tool Registry / Guardrails
      -> Control CommandTaskRunner
      -> Perception OCR / YOLO / VLM / Video
      -> Memory EventStore / FTS5 / Brain
      -> Evolution Candidate Engine
```

特点：
- App 打开默认是 Live Orb，不是 Code。
- Code Surface 只在需要编程时调出。
- 后端复用现有 Python Runtime。
- 感知层已有 OCR、YOLO、VLM、视频分段分析。
- 权限层已有 safe / trusted / root 方向。
- 记忆层已有事件库、FTS5、候选记忆、自动学习。
- 自进化已有 candidate review 方向。

## 能力差异

| 能力 | pub-local-jarvis | Javis | 判断 |
| --- | --- | --- | --- |
| 常驻桌宠 | 强 | 弱 | Javis 应吸收 |
| 常驻屏幕感知 | 强 | 中 | Javis 有接口，缺默认常驻体验 |
| 系统音频感知 | 强 | 弱 | Javis 应新增 |
| 实时主动提醒 | 强 | 弱/中 | Javis 应新增策略层 |
| 游戏弹幕 | 强 | 无 | 可作为可选场景模式 |
| 课程记录 | 强 | 无 | 很适合接 Javis 记忆系统 |
| 桌面操控 | 弱 | 强 | Javis 优势 |
| 文件/代码执行 | 弱 | 强 | Javis 优势 |
| 权限控制 | 弱 | 强 | Javis 必须保留 |
| 长期记忆 | 中 | 强 | Javis 优势 |
| 自进化 | 弱 | 中 | Javis 方向更接近总蓝图 |
| 内置推理运行时 | 强 | 弱/外置 | Javis 可后期吸收，不宜现在重写 |

## 最值得 Javis 吸收的东西

### 1. 桌宠/悬浮 Live 形态

Javis 现在有 Live Orb。

下一步应该变成两种形态：
- 主窗口：Siri-like Live Orb。
- 常驻形态：小型悬浮 Orb / 数字 IP / 桌宠。

这正好符合你之前说的“喊关键词随时出现”。

### 2. 常驻感知循环

pub-local-jarvis 的核心不是“用户问一句它答一句”。

它是持续观察：
- 屏幕每秒一帧。
- 系统音频约一秒一段。
- 模型判断 LISTEN 还是 SPEAK。

Javis 应该新增：

```text
Ambient Perception Daemon
  -> screen frame sampler
  -> system audio sampler
  -> scene classifier
  -> intervention policy
  -> Live Orb / bubble / notification
```

这不要塞进 Agent 主循环。

它应该是独立子系统。

### 3. 场景模式

pub-local-jarvis 把场景分成 game / course / other。

Javis 可以扩展成：
- work：工作/开发。
- game：游戏陪伴。
- course：课程记录。
- meeting：会议摘要。
- browsing：网页阅读。
- idle：空闲观察。

每个场景有不同干预频率。

### 4. 课程记录

这是高价值功能。

Javis 已经有记忆系统，适合直接做：
- 自动识别课程画面。
- 抽取关键帧。
- OCR + VLM 出知识点。
- 写入情景记忆。
- 结束后生成 Markdown 笔记。
- 重要知识沉淀为语义记忆。

### 5. 隐私模式

pub-local-jarvis 双击桌宠暂停屏幕/音频感知。

Javis 必须做得更明确：
- 一键暂停感知。
- 屏幕上显示感知状态。
- 不保存原始图像和音频。
- root 权限下也不能绕过用户暂停。

## 不建议现在吸收的东西

### 1. 直接引入 llama.cpp-omni vendor

风险太大。

它带来：
- C++ 构建链。
- CUDA 兼容。
- 打包体积。
- Windows DLL 问题。
- 模型下载和校验。

Javis 现在的方向是先把 App + Runtime + 感知事件链跑稳。

内置推理运行时应该放到后期。

### 2. 从 Tauri 改回 Electron

不建议。

Javis 已经在 App-first Tauri 路线上。

Electron 的优势是生态快。

Tauri 的优势是体积小、系统集成更干净、适合长期做本地智能体。

### 3. 把主动感知直接接到执行权限

危险。

pub-local-jarvis 主要“看”和“提醒”。

Javis 能“执行”和“控制”。

所以 Javis 的主动感知只能默认产生建议，不应自动执行高风险操作。

## 和你贴的自检摘要对齐

你贴的 3 个 PARTIAL 需要更新判断。

### UI 搜索框

后端 `/api/memory/search` 已存在。

当前 App 侧已经出现 `ConversationDrawer` 搜索 UI，能请求 `/api/memory/search`。

所以“Web 前端缺搜索框”仍可能成立，但 App 主线已经补上。

### run_subagent 集成

`core/subagent.py` 里已有 `subagent_run` 和 `subagent_parallel` 工具定义。

真正要检查的是它是否随 runtime 默认注册，并能被 Agent 稳定调度。

旧报告说“独立存在未集成 agent.py 主循环”，这个结论需要重新跑工具注册测试后再定。

### /learn 触发器

`core/skill_creator.py` 已经有 `/learn` 语义和 `skill_create` 工具。

但“用户显式输入 /learn xxx 就触发技能创建”的路由仍需验证。

这个属于入口问题，不是能力完全缺失。

## 推荐融合蓝图

### Phase A：常驻陪伴壳

目标：让 Javis 像 Siri + 桌面 Jarvis。

实现：
- App 主窗口保持 Live Orb。
- 新增 floating companion window。
- 支持 always-on-top、拖拽、透明背景。
- 支持唤醒词/快捷键召回。
- 支持隐私暂停。

验收：
- 打开 App 就是 Live Orb。
- 最小化后仍有悬浮 Javis。
- 不需要 Code 界面也能对话。

### Phase B：Ambient Perception Daemon

目标：让 Javis 不只等你问。

实现：
- 屏幕抽帧。
- 当前活动窗口识别。
- 系统音频采样。
- OCR/YOLO/VLM 输出结构化事件。
- 事件写入工作记忆和 SessionEventStore。

验收：
- Javis 能解释“我刚才看到/听到什么”。
- 默认不保存原始画面和音频。

### Phase C：主动干预策略

目标：该说话时说，不该说时安静。

实现：
- 场景分类：work/game/course/meeting/browsing/idle。
- 冷却时间。
- 打扰等级。
- 用户可配置。
- 只生成提醒，不自动执行危险操作。

验收：
- 课程能提醒。
- 游戏能弹幕。
- 工作时不乱打断。

### Phase D：课程/会议记录

目标：把 pub-local-jarvis 的课程能力接到 Javis 记忆系统。

实现：
- 自动创建 session。
- 关键帧摘要。
- OCR/VLM 知识点。
- 结束后 Markdown 笔记。
- 重点知识写入语义记忆。

验收：
- 一节课结束后自动产出笔记。
- 可在记忆搜索里找回。

### Phase E：本地推理运行时

目标：减少 Ollama/云 API 依赖。

实现顺序：
1. 先接外部 Ollama / LM Studio / vLLM。
2. 再支持 llama.cpp server。
3. 最后考虑内嵌 C++ runtime。

验收：
- 小模型本地处理常见感知摘要。
- 复杂多模态才升级到强模型。

## 最终判断

pub-local-jarvis 对 Javis 的价值很大。

但它不是“替代方案”。

它更像 Javis 缺的外层人格和常驻感知范式。

Javis 应该保留自己的五大系统和执行内核。

然后吸收它的：
- 桌宠/悬浮交互。
- 常驻屏幕和系统音频感知。
- 场景模式。
- 课程记录。
- 隐私暂停。
- 主动提醒策略。

不要现在吸收它的：
- Electron 路线。
- 大型 C++ vendor。
- 直接内嵌推理 runtime。

一句话：

把 pub-local-jarvis 当“Javis 的感知陪伴外壳参考”，不要当“Javis 的内核参考”。
