# -*- coding: utf-8 -*-
"""记忆控制器 — 统一四层记忆 + 大脑交互 + 多通道检索 + 循环压缩

架构:
  外部请求 → controller.retrieve(query)
                ├─ 通道1: 关键词检索 (brain.recall)
                ├─ 通道2: 场景指纹 (semantic rules)
                ├─ 通道3: 近期对话 (session.topic + user_msgs)
                ├─ 通道4: 摘要记忆 (长期压缩摘要)
                ├─ 通道5: 程序链 (procedural chains)
                └─ 通道6: 高优先级规则 (priority≥4 facts)
              → 合并排名 → 返回 context 块

  后台循环:
    每轮对话结束 → 写入 episode + brain facts
    每5分钟 → 语义规则提取 + 程序记忆固化
    每10分钟 → 低优先级知识压缩摘要
    每30分钟 → 长期记忆摘要生成

存储:
  所有数据最终汇入 brain_data/ (facts + experiences + episodes + semantic + procedural)
  Brain facts 是唯一的长期持久层
"""
import logging, time, json, os, glob, threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("memory.controller")

# ── 全局缓存 ──
_cache = {
    "semantic_rules": None,
    "procedural_chains": None,
    "summaries": None,
    "ts": 0,
}
_CACHE_TTL = 30  # 秒


class MemoryController:
    """统一记忆控制器"""

    def __init__(self, brain=None):
        self._brain = brain
        self._cycle_count = 0
        self._running = False

    def set_brain(self, brain):
        self._brain = brain

    # ════════════════════════════════════════════
    # 多通道检索（外部唯一入口）
    # ════════════════════════════════════════════

    def retrieve(self, query: str = "", max_results: int = 8) -> list:
        """多通道记忆检索 — 返回排名后的 context 条目"""
        channels = []
        if not self._brain:
            return channels

        now = time.time()

        # 刷新缓存
        if now - _cache["ts"] > _CACHE_TTL:
            self._refresh_cache()

        # ── 通道1: 关键词检索 (brain.recall) ──
        if query:
            facts = self._brain.recall(query, max_results=4)
            for f in facts:
                channels.append((f.priority * 3, f"知道: {f.content[:80]}"))

        # ── 通道2: 场景指纹 (semantic rules, 跳过短噪点) ──
        if query and _cache["semantic_rules"]:
            from memory.episodic import extract_fingerprint
            fp = extract_fingerprint(query)
            for rule in _cache["semantic_rules"]:
                conclusion = rule.get("conclusion", "")
                if len(conclusion) < 20:
                    continue
                rf = rule.get("fingerprint", {})
                score = 0
                if fp.get("domain") and rf.get("domain") == fp["domain"]:
                    score += 2
                if fp.get("task_type") and rf.get("task_type") == fp["task_type"]:
                    score += 1
                if score >= 2 and rule.get("risk_level", 0) > 0.5:
                    channels.append((int(score * rule.get("confidence", 0.5) * 10),
                                     f"规则: {conclusion[:80]}"))

        # ── 通道3: 近期对话 ──
        if self._brain:
            topics = sorted([f for f in self._brain._facts if f.category == "session.topic"],
                            key=lambda x: x.created_at, reverse=True)[:3]
            for t in topics:
                channels.append((3, f"上次: {t.content[:70]}"))

            msgs = sorted([f for f in self._brain._facts if f.category == "conversation.user_msgs"],
                          key=lambda x: x.created_at, reverse=True)[:3]
            for m in msgs:
                channels.append((2, f"你说: {m.content[:60]}"))

        # ── 通道4: 摘要记忆 ──
        if _cache["summaries"]:
            for s in _cache["summaries"]:
                channels.append((2, f"摘要: {s[:80]}"))

        # ── 通道5: 程序链 ──
        if _cache["procedural_chains"] and query:
            from memory.episodic import extract_fingerprint
            fp = extract_fingerprint(query)
            for chain in _cache["procedural_chains"]:
                if chain.get("domain") == fp.get("domain") and chain.get("success_rate", 0) > 0.8:
                    seq = " -> ".join(chain.get("tool_sequence", [])[:4])
                    channels.append((4, f"方案: {seq}({chain.get('success_rate',0):.0%})"))
                    break

        # ── 通道6: 高优先级规则 (priority≥5) ──
        if self._brain:
            for f in self._brain._facts:
                if f.priority >= 5 and f.category == "user_profile":
                    channels.append((5, f"偏好: {f.content[:80]}"))
                    break

        # 合并排名去重
        seen = set()
        result = []
        for score, text in sorted(channels, key=lambda x: -x[0]):
            key = text[:40]
            if key not in seen:
                seen.add(key)
                result.append(text)
                if len(result) >= max_results:
                    break

        return result

    def _refresh_cache(self):
        """刷新缓存（每30秒）"""
        try:
            from memory.semantic import load_all_rules
            _cache["semantic_rules"] = load_all_rules()
        except: pass
        try:
            from memory.procedural import PROCEDURAL_DIR
            chains = []
            for f in sorted(PROCEDURAL_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
                try: chains.append(json.loads(f.read_text(encoding="utf-8")))
                except: pass
            _cache["procedural_chains"] = chains
        except: pass
        try:
            _cache["summaries"] = self._load_summaries()
        except: pass
        _cache["ts"] = time.time()

    def _load_summaries(self) -> list:
        """加载长期摘要"""
        if not self._brain:
            return []
        return [f.content[:80] for f in self._brain._facts
                if f.category == "memory.summary" and f.priority >= 3]

    def context_block(self, query: str = "") -> str:
        """生成给 agent 的完整 context 块"""
        items = self.retrieve(query, max_results=8)
        if not items:
            return ""
        parts = []
        # 去重前缀
        seen_cats = set()
        for item in items:
            cat = item.split(":")[0] if ":" in item else "info"
            if cat not in seen_cats:
                seen_cats.add(cat)
                parts.append(item)
        return " | ".join(parts[:6])

    # ════════════════════════════════════════════
    # 写入
    # ════════════════════════════════════════════

    def memorize(self, user_msg: str, assistant_msg: str = ""):
        """记一条对话到大脑"""
        if not self._brain or not user_msg:
            return
        try:
            clean = user_msg.strip().replace("\n", " ")[:80]
            self._brain.learn_fact(f"你说: {clean}",
                category="conversation.user_msgs", source="self", priority=2)
            self._brain.learn_fact(f"话题: {clean[:60]}",
                category="session.topic", source="self", priority=3)
        except: pass

    # ════════════════════════════════════════════
    # 后台循环
    # ════════════════════════════════════════════

    def start_cycles(self):
        """启动所有后台循环"""
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._cycle_loop, daemon=True, name="memory-controller")
        t.start()
        logger.info("记忆控制器后台循环已启动")

    def _cycle_loop(self):
        """主循环：5分钟语义提取 → 10分钟压缩 → 30分钟摘要"""
        tick = 0
        while self._running:
            time.sleep(60)
            tick += 1
            try:
                # 每5分钟: 语义规则 + 程序记忆
                if tick % 5 == 0:
                    self._cycle_semantic()
                    self._cycle_procedural()
                # 每10分钟: 压缩
                if tick % 10 == 0:
                    self._cycle_compress()
                # 每30分钟: 长期摘要
                if tick % 30 == 0:
                    self._cycle_summarize()
            except Exception as e:
                logger.debug(f"循环异常: {e}")

    def _cycle_semantic(self):
        from memory.semantic import consolidate
        n = consolidate(brain=self._brain)
        if n:
            logger.info(f"语义提取: {n} 新规则")

    def _cycle_procedural(self):
        from memory.procedural import consolidate_from_episodes
        n = consolidate_from_episodes()
        if n:
            logger.info(f"程序固化: {n} 新链")

    def _cycle_compress(self):
        if self._brain:
            self._brain.compress()

    def _cycle_summarize(self):
        """生成/更新长期摘要"""
        if not self._brain:
            return
        try:
            # 按分类生成摘要
            cats = {}
            for f in self._brain._facts:
                if f.priority >= 3:
                    c = f.category.split(".")[0]
                    cats.setdefault(c, []).append(f.content[:50])
            summary_parts = []
            for cat, contents in sorted(cats.items(), key=lambda x: -len(x[1]))[:8]:
                combined = " | ".join(contents[:5])
                summary_parts.append(f"{cat}({len(contents)}条)")
            summary = f"记忆摘要: {', '.join(summary_parts)}"
            # 覆盖写入，不重复
            existing = [f for f in self._brain._facts if f.category == "memory.summary"]
            for e in existing:
                self._brain._facts.remove(e)
            self._brain.learn_fact(summary, category="memory.summary",
                                   source="self", priority=3)
            logger.info(f"摘要已更新")
        except: pass


# ── 全局单例 ──
_controller = MemoryController()


def get_controller(brain=None) -> MemoryController:
    global _controller
    if brain:
        _controller.set_brain(brain)
    return _controller
