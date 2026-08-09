# 附录 C — P2 记忆系统完整设计（6 步 × 多方案）

> 本附录由专用设计代理补跑（因 P2 在设计工作流中 StructuredOutput 超限失败，批判代理确认其为最高严重度缺失）。已实际读取 `memory/`、`kernel/embedder.py`、`core/memory_kernel.py`、`core/session_recall.py`、`knowledge/brain.py`、`utils/memory.py`、`core/prompt_builder.py`、`core/agent.py` 等源码。

## 现状（真实代码）

**已具备：**
- 事件：`memory/episodic.py` `record_tool_call/finish/extract_fingerprint/find_episodes_by_fingerprint`
- 语义：`memory/semantic.py` `SemanticRule` + `consolidate`（MIN_SAMPLE_SIZE=3, MIN_FAILURE_RATE=0.6）
- 偏好：`knowledge/brain.py` `learn_fact` + `Fact`（priority/confidence/usage_count）+ `learn_style`
- 身份：`core/memory_kernel.py` `CoreMemoryKernel`（`load_identity/prompt_rules/answer_if_core_query`，读 core_identity.md）
- 策略：`memory/procedural.py` `consolidate_from_episodes` + `procedural_materializer.py`
- 检索：`memory/controller.py` 6 通道（关键词/指纹/近期对话/摘要/程序链/高优先级）+ `context_block`
- 存储：`memory/session_db.py` `SessionEventStore`（events/memory_candidates/memory_fts FTS5）+ `memory/indexer.py`（brain_data/memory.db 独立）
- 治理雏形：`consolidation.py` 事件→candidate、`activation.py` active→brain、状态机 candidate→active/rejected/archived
- Agent 集成：`agent.py` 规划时 `context_block`、行动后 `_after_learn` 写 episode/learner/reflector
- Embedder：`kernel/embedder.py` `TextEmbedder.embed` → [1,1536] torch.Tensor（sentence-transformers 或哈希回退），**无持久化无检索**

**真实缺口：**
1. 无统一 MemoryManager 门面（controller 关键词与 indexer/session_db 检索不互通）
2. 无遗忘机制（Fact 字段在，无评分/衰减/降级）
3. 无治理元数据（来源/时间/置信度/有效期/隐私）+ 查看/修改/遗忘/导出 API
4. 偏好未独立成层（混在 Fact.category）
5. 身份层未纳入统一门面
6. 检索组合残缺（embedder 无持久化/相似度 API；FTS 只搜 events 不含 facts）
7. 无反思合并（Reflector 逐条写，不合并同类经验）
8. 测试缺口（无 controller 多通道/遗忘/门面/五层召回测试）
9. Graph 未起步

---

## 步骤 1：MemoryManager 门面 + 五层统一注册

**目标**：`memory/manager.py` 统一五层（事件/语义/偏好/策略/身份）写入与召回，adapter 对齐既有模块，不修改底层。

**方案 A：纯新增门面 + adapter 类（推荐）**
- `MemoryEntry` dataclass：id/layer/content/source/confidence/priority/created_at/updated_at/usage_count/expires_at/privacy/tags/ref_id
- `LayerAdapter(ABC)`：`recall(query)/write(entry)/count()`，五层各一 adapter
- `MemoryManager`：`register_layers/recall/write/get_singleton`，写入发布 `EventType.MEMORY_WRITTEN`
- `core/runtime.py` 加 1 行装配
- 文件：`memory/manager.py`、`memory/governor.py`（骨架）、`core/runtime.py`、`tests/test_memory_manager_facade.py`
- 红线测试：五 adapter 召回/门面不改底层/写事件发布/身份去重
- 工作量：中（0.5-1 天）

**方案 B：就地扩展 MemoryController**（controller.py 膨胀，违背门面对齐）— 不推荐
**方案 C：mixin 继承**（僵尸缓存状态，语义混乱）— 不推荐

---

## 步骤 2：治理（SQLite 治理表 + 查看/修改/遗忘/导出）

**方案 A：SQLite 治理表 + 回写 JSON 双写（推荐）**
- `memory_entries` 表：entry_id/layer/content/source/confidence/priority/usage_count/created_at/updated_at/expires_at/privacy/tags_json/ref_kind/ref_id/status
- `inspect/update(白名单)/forget(软删 status=forgotten+降 priority=0+摘 FTS)/export(JSON，按隐私过滤)`
- 迁移：启动扫 facts/*.json 补默认治理元数据
- 文件：`memory/governor.py`、`memory/manager.py`、`memory/session_db.py`（暴露连接）、`tests/test_memory_governor.py`
- 红线测试：元数据非空/软删可逆/更新白名单/导出尊重隐私/迁移补默认
- 工作量：中（1-1.5 天）

**方案 B：JSON sidecar**（无索引，列表慢）— 不推荐
**方案 C：内存 dict 不持久化**（重启即丢）— 否决

---

## 步骤 3：遗忘机制

**方案 A：纯函数评分 + 后台降级（推荐）**
- `score = importance × freshness × (1+usage)`，`freshness = 0.5^((now-created)/(half_life*86400))`
- `privacy=long_term` → 恒 5.0+importance（永权重）
- `run_maintenance(dry_run=True)`：弱 fact → priority 1 + dormant；score<0.05 非 long_term → archived
- 接入 controller 循环；默认 dry_run 跑 2 周再放真实降级
- 文件：`memory/forgetting.py`、`memory/manager.py`、`core/runtime.py`、`tests/test_memory_forgetting.py`
- 红线测试：评分单调性/long_term 豁免/dry_run 不写库/dormant 不进 prompt/不物理删
- 工作量：中（0.5-1 天）

**方案 B：EWMA 在线更新**（写放大）— 不推荐
**方案 C：离线批处理**（启动慢，实时性差）— 不推荐

---

## 步骤 4：检索组合（FTS + 向量双轨融合）

**方案 A：向量列入 SQLite + 双重检索 + RRF 融合（推荐）**
- `memory_vectors(entry_id/model/dim/vector BLOB)`；`ensure_embeddings()` 用 `TextEmbedder.embed_batch`
- 两段式：FTS 预筛 50 → 向量重排 20 → RRF 融合（k=60）
- 降级链：向量→FTS→controller 关键词；embed 失败/无 ST 模型→纯 FTS
- 文件：`memory/retriever.py`、`memory/manager.py`、`memory/session_db.py`、`tests/test_memory_retriever.py`
- 红线测试：双源召回/向量降级/RRF 排序/embedding 持久化/增量嵌入/中文 LIKE 兜底
- 工作量：高（1.5-2 天）

**方案 B：SQLite 降维近似**（PCA 64 维，质量损失）— 否决
**方案 C：纯 FTS**（不满足向量目标，作降级保留）— 不推荐

---

## 步骤 5：Agent 循环集成

**方案 A：既有 hook 点最小替换（推荐）**
- 规划召回：`Agent.chat` 中 `context_block` 后追加 `manager.recall_block`，合并注入
- 行动写入：`_after_learn` 追加 `record_episode/record_preference`（保留 controller/learner/reflector）
- 反思合并：`consolidate_reflections` — 同 domain+reusable_lesson 聚合，≥2 次合并为 `experience.<domain>.stabilized`，confidence 随证据上调
- 文件：`core/agent.py`（约 10 行）、`memory/manager.py`、`tests/test_agent_memory_integration.py`
- 红线测试：规划召回注入/行动写入/反思去重/跨域不合并/事件发布/既有 agent 测试不回归
- 工作量：中（1 天）

**方案 B：hook_system 事件驱动**（无对话结束事件，参数脆弱）— 不推荐
**方案 C：独立 MemorySubsystem**（要重建回合状态，成本最高）— 留 P3+

---

## 步骤 6：Graph schema 预设计 + 审计

**方案 A：schema 常量模块 + 只读审计（推荐）**
- 节点：EPISODE/FACT/PREFERENCE/RULE/TOOL/SESSION/ENTITY
- 边：HAPPENED_IN/USED/REFERENCES/PREFERS/DERIVED/SIMILAR/SUPERSEDES
- `build_edges_from_records` 纯函数；`memory/audit.py` 审计快照（各层计数/治理缺口/过期条目）
- `memory_status` 工具追加 audit 段
- 文件：`memory/graph_schema.py`、`memory/audit.py`、`tools/system.py`、`tests/test_graph_schema.py`
- 红线测试：schema 类型集合/建边映射/审计报缺口/GRAPH_ENGINE=="pending"
- 工作量：小（0.5 天）

**方案 B：networkx 内存图**（依赖未确认，无持久化）— 不推荐

---

## 依赖链
```
Step1 → Step2 → Step3
              ↘ Step4 → Step5
              ↘ Step6（收尾）
```

## 出口标准
1. 门面存在，五层可召回/写入/计数，不改既有模块（diff 0 或白名单例外）
2. 治理：每条带 source/time/confidence/expires_at/privacy；forget 软删可逆
3. 遗忘：评分单测通过；dry-run ≥2 周后启用真实降级
4. 检索：hybrid 离线可用，三种降级不崩溃
5. Agent 三段集成各有一张红线测试；既有测试零回归
6. graph_schema 就绪且 GRAPH_ENGINE=="pending"
7. 全量 pytest 在 JAVIS_TEST_MODE=1 下通过

## 需进一步确认
- `tools/python-runtime-3.11` 是否捆绑 sentence-transformers（决定向量质量）
- `memory_recall/memory_recent` 是否注册进 manifest
- `brain_data/rules/core_identity.md` 是否实际存在
- config.yaml 的 `user.name` 为空（身份记忆生产来源待确认）
