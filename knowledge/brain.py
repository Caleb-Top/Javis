"""大脑系统 v2 — 永久记忆 · 自动压缩 · 永不删除"""

import json, os, time, logging, hashlib, threading, atexit
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("brain")

BRAIN_DIR = Path(__file__).parent.parent / "brain_data"
FACTS_DIR = BRAIN_DIR / "facts"
EXPERIENCES_DIR = BRAIN_DIR / "experiences"
PAPERS_DIR = BRAIN_DIR / "papers"

# 无上限 — 所有知识永久保存
# 当数量过大时自动压缩摘要，绝不删除
MAX_FACTS = 100000
MAX_EXPERIENCES = 50000


@dataclass
class Fact:
    id: str = ""
    content: str = ""
    category: str = "general"
    source: str = "conversation"
    confidence: float = 0.5
    created_at: float = 0.0
    updated_at: float = 0.0
    usage_count: int = 0
    priority: int = 1          # 1-5 优先级，新增


@dataclass
class Experience:
    id: str = ""
    intent: str = ""
    action: str = ""
    result: str = ""
    error: str = ""
    lesson: str = ""
    created_at: float = 0.0
    used_count: int = 0
    priority: int = 1          # 1-5 优先级，新增
    domain: str = "general"    # 所属领域层次，新增
    error_category: str = ""   # 错误类型分类，新增


class Brain:
    """Javis 的大脑 v2 — 层次化记忆管理"""

    def __init__(self):
        self._ensure_dirs()
        self._facts: list[Fact] = []
        self._experiences: list[Experience] = []
        self._load()
        self._dirty = False
        self._save_timer = 0
        self._start_auto_flush()
        atexit.register(self._flush)

    def _start_auto_flush(self):
        """后台线程每30秒自动刷盘，每10分钟压缩一次"""
        def _loop():
            tick = 0
            while True:
                time.sleep(30)
                try: self._flush()
                except: pass
                tick += 1
                if tick >= 20:
                    try: self.compress()
                    except: pass
                    tick = 0
        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def _ensure_dirs(self):
        for d in [FACTS_DIR, EXPERIENCES_DIR, PAPERS_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    def _load(self):
        facts_files = sorted(FACTS_DIR.glob("*.json"))
        exp_files = sorted(EXPERIENCES_DIR.glob("*.json"))
        loaded_f, skipped_f = 0, 0
        loaded_e, skipped_e = 0, 0
        for f in facts_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                # 兼容旧版（无 priority 字段）
                if "priority" not in data:
                    data["priority"] = 1
                self._facts.append(Fact(**data))
                loaded_f += 1
            except Exception as e:
                skipped_f += 1
                logger.debug(f"跳过事实文件 {f.name}: {e}")
        for f in exp_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if "priority" not in data:
                    data["priority"] = 1
                if "domain" not in data:
                    data["domain"] = "general"
                if "error_category" not in data:
                    data["error_category"] = ""
                self._experiences.append(Experience(**data))
                loaded_e += 1
            except Exception as e:
                skipped_e += 1
                logger.debug(f"跳过经验文件 {f.name}: {e}")
        logger.info(f"🧠 大脑加载: {loaded_f}事实/{loaded_e}经验 ({skipped_f+skipped_e}跳过)")

    # ── 学习 ──

    # ── 风格信号词典（大脑原生） ──
    _STYLE_SIGNS = {
        "短句": ["好", "行", "嗯", "对", "可以", "看看", "试试"],
        "复读数据": ["CPU:", "内存:", "磁盘:", "✅ system", "❌ tool"],
        "口语": ["了", "嘛", "吧", "呢", "哦", "嗯", "啊", "哈", "咱", "我们", "对了", "不过"],
        "先结论": ["还行", "挺", "没什么", "没问题", "正常", "还好"],
        "用emoji": ["✅", "❌", "📸", "💻", "⭐", "🔧", "📁"],
        "精简": ["ok", "好", "继续", "嗯", "对"],
    }

    @classmethod
    def extract_style(cls, text: str) -> dict:
        """从文本中提取风格维度"""
        if not text:
            return {}
        dims = {}
        for dim, signs in cls._STYLE_SIGNS.items():
            n = sum(text.count(s) for s in signs)
            if n > 0:
                dims[dim] = n
        import re as _re
        sents = [s.strip() for s in _re.split(r'[。！？\n]', text) if s.strip()]
        dims["avg_len"] = round(sum(len(s) for s in sents) / max(len(sents), 1))
        return dims

    def learn_style(self, user_msg: str, assistant_msg: str = ""):
        """从一轮对话中学习风格 — 原生大脑能力"""
        if not user_msg:
            return
        try:
            u = self.extract_style(user_msg)
            if u:
                summary = "用户风格:" + ",".join(f"{k}={v}" for k, v in sorted(u.items()) if k != "avg_len") + f"|均句{u.get('avg_len',0)}字"
                self.learn_fact(summary, category="user_style.obs", source="self", priority=4)
            if assistant_msg and len(assistant_msg) > 10:
                a = self.extract_style(assistant_msg)
                if not a:
                    return
                if "复读数据" in a and "复读数据" not in u:
                    self.learn_fact("用户不喜欢回复中出现原始数据行(CPU:xxx%)，用自己的话重说",
                                    category="user_style.avoid.repeat", source="self", priority=5)
                if u.get("用emoji", 0) == 0 and a.get("用emoji", 0) > 2:
                    self.learn_fact("用户不太用emoji，助手少用",
                                    category="user_style.avoid.emoji", source="self", priority=4)
                uv = u.get("avg_len", 0)
                av = a.get("avg_len", 0)
                if uv > 0 and av > uv * 1.5:
                    self.learn_fact(f"用户短句(均{uv}字)，助手可更精简(均{av}字)",
                                    category="user_style.avoid.verbose", source="self", priority=4)
        except Exception:
            pass

    def learn_fact(self, content: str, category: str = "general",
                   source: str = "conversation", priority: int = 1):
        """学习一个新知识点，带优先级"""
        now = time.time()
        fact_id = hashlib.md5(content.encode()).hexdigest()[:12]
        existing = [f for f in self._facts if f.id == fact_id]
        if existing:
            existing[0].confidence = min(1.0, existing[0].confidence + 0.1)
            existing[0].usage_count += 1
            existing[0].priority = max(existing[0].priority, priority)
            existing[0].updated_at = now
            self._dirty = True
            logger.info(f"📖 强化: [{priority}★] {content[:40]}...")
            return
        fact = Fact(
            id=fact_id, content=content, category=category,
            source=source, confidence=0.6, priority=priority,
            created_at=now, updated_at=now
        )
        self._facts.append(fact)
        self._dirty = True
        self._trim_facts()
        logger.info(f"📖 新知识: [{priority}★][{category}] {content[:40]}...")

    def record_experience(self, intent: str, action: str, result: str,
                          error: str = "", lesson: str = "",
                          priority: int = 1, domain: str = "general",
                          error_category: str = ""):
        """记录经验，带优先级和领域"""
        now = time.time()
        exp = Experience(
            id=hashlib.md5(f"{intent}{action}{now}".encode()).hexdigest()[:12],
            intent=intent, action=action, result=result,
            error=error, lesson=lesson, priority=priority,
            domain=domain, error_category=error_category,
            created_at=now
        )
        self._experiences.append(exp)
        self._dirty = True
        self._trim_experiences()
        status = "✅" if result == "success" else "❌"
        logger.info(f"💾 经验: [{priority}★][{domain}] {action} {status}")

    # ── 检索 ──

    _recall_cache: dict = {}
    _MAX_CACHE_SIZE = 32

    def recall(self, query: str, max_results: int = 5) -> list[Fact]:
        """带优先级的知识检索 — 高优先级优先返回"""
        cache_key = f"{query}:{max_results}"
        if cache_key in self._recall_cache:
            return self._recall_cache[cache_key]

        query_lower = query.lower()
        scored = []
        for fact in self._facts:
            score = 0.0
            if query_lower in fact.content.lower():
                score += fact.confidence * 10
            for word in query_lower.split():
                if len(word) >= 2 and word in fact.content.lower():
                    score += 1
            # 优先级加权
            score *= (1 + 0.2 * fact.priority)
            score *= (1 + 0.1 * fact.usage_count)
            if score > 0:
                scored.append((score, fact))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = [f for _, f in scored[:max_results]]

        if len(self._recall_cache) >= self._MAX_CACHE_SIZE:
            self._recall_cache.pop(next(iter(self._recall_cache)))
        self._recall_cache[cache_key] = result
        return result

    def get_experiences(self, intent: str = "", max_results: int = 5,
                        domain: str = "") -> list[Experience]:
        """按领域和意图检索经验，按优先级排序"""
        if not intent and not domain:
            exps = sorted(self._experiences,
                          key=lambda x: (x.priority, x.created_at),
                          reverse=True)[:max_results]
            return exps

        il = intent.lower() if intent else ""
        scored = []
        for exp in self._experiences:
            score = 0
            if domain and exp.domain == domain:
                score += 10
            if domain and domain in exp.domain:
                score += 5
            if il:
                for w in il.split():
                    if len(w) >= 2 and w in exp.intent.lower():
                        score += 2
                    if w in exp.error.lower():
                        score += 3
            score += exp.priority * 5  # 优先级是核心权重
            if score > 0:
                scored.append((score, exp))

        if not scored:
            # 兜底：返回高优先级经验
            scored = [(exp.priority * 5, exp) for exp in self._experiences]

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:max_results]]

    def get_experiences_by_domain(self, domain: str) -> list[Experience]:
        """获取特定领域的所有经验"""
        return [e for e in self._experiences if domain in e.domain]

    def get_priority_experiences(self, min_priority: int = 3) -> list[Experience]:
        """获取高优先级经验（>= min_priority）"""
        return [e for e in self._experiences if e.priority >= min_priority]

    def _flush(self):
        """批量刷盘（不再每次 learn_fact 都写）"""
        if not self._dirty:
            return
        for fact in self._facts:
            self._save_fact(fact)
        for exp in self._experiences:
            self._save_experience(exp)
        self._dirty = False
        logger.debug(f"💾 批量刷盘完成")

    def get_stats(self) -> dict:
        """增强的大脑统计"""
        categories = {}
        for f in self._facts:
            c = f.category.split(".")[0]  # 取顶层分类
            categories[c] = categories.get(c, 0) + 1

        # 按优先级分布
        priority_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for e in self._experiences:
            p = e.priority if e.priority <= 5 else 5
            priority_dist[p] = priority_dist.get(p, 0) + 1

        # 按领域分布
        domain_dist = {}
        for e in self._experiences:
            d = e.domain.split(".")[0] if e.domain else "general"
            domain_dist[d] = domain_dist.get(d, 0) + 1

        return {
            "facts_count": len(self._facts),
            "experiences_count": len(self._experiences),
            "papers_count": len(list(PAPERS_DIR.glob("*.md"))),
            "categories": categories,
            "priority_distribution": priority_dist,
            "domain_distribution": domain_dist,
        }

    # ── 维护 ──

    def compress(self):
        """压缩低优先级知识：不删除，合并为摘要"""
        self._flush()
        groups = {}
        for f in self._facts:
            if f.priority <= 2 and f.usage_count <= 1:
                key = f.category.split(".")[0]
                groups.setdefault(key, []).append(f)
        compressed = 0
        for cat, facts in groups.items():
            if len(facts) < 5:
                continue
            latest = max(f.updated_at for f in facts)
            lines = [f.content[:40] for f in facts[:20]]
            summary = " | ".join(lines)
            if len(summary) > 300:
                summary = summary[:300] + "..."
            for f in facts[:5]:
                f.content = "[压缩] " + summary[:60]
                f.priority = max(1, f.priority)
                f.updated_at = latest
                compressed += 1
            logger.info(f"压缩: {cat} {len(facts)}条")
        if compressed:
            self._flush()
        logger.info(f"压缩完成: {compressed} 条")

    def _trim_facts(self):
        """超出上限时压缩低优先级知识，绝不删除"""
        if len(self._facts) <= MAX_FACTS:
            return
        self.compress()

    def _trim_experiences(self):
        """超出上限时压缩低优先级经验，绝不删除"""
        if len(self._experiences) <= MAX_EXPERIENCES:
            return
        self.compress()

    def _save_fact(self, fact: Fact):
        (FACTS_DIR / f"{fact.id}.json").write_text(
            json.dumps(asdict(fact), ensure_ascii=False), encoding="utf-8")

    def _save_experience(self, exp: Experience):
        (EXPERIENCES_DIR / f"{exp.id}.json").write_text(
            json.dumps(asdict(exp), ensure_ascii=False), encoding="utf-8")
