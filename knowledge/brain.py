"""大脑系统 v2 — 层次化记忆 · 优先级 · 自动过期 · 语义分组"""

import json, os, time, logging, hashlib, threading, atexit
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger("brain")

BRAIN_DIR = Path(__file__).parent.parent / "brain_data"
FACTS_DIR = BRAIN_DIR / "facts"
EXPERIENCES_DIR = BRAIN_DIR / "experiences"
PAPERS_DIR = BRAIN_DIR / "papers"

MAX_FACTS = 1000          # 事实上限
MAX_EXPERIENCES = 200     # 经验上限
EXPIRE_DAYS = 90          # 超过90天未使用的低优先级知识自动过期


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
        """后台线程每30秒自动刷盘"""
        def _loop():
            while True:
                time.sleep(30)
                try: self._flush()
                except: pass
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

    def cleanup(self):
        self._flush()  # 先刷盘再清理
        """自动过期清理：删除低优先级且长期未使用的知识"""
        now = time.time()
        cutoff = now - EXPIRE_DAYS * 86400
        old_facts = [f for f in self._facts
                     if f.updated_at < cutoff and f.priority <= 1
                     and f.usage_count == 0]
        old_exps = [e for e in self._experiences
                    if e.created_at < cutoff and e.priority <= 1
                    and e.used_count == 0]
        for f in old_facts:
            self._facts.remove(f)
            (FACTS_DIR / f"{f.id}.json").unlink(missing_ok=True)
        for e in old_exps:
            self._experiences.remove(e)
            (EXPERIENCES_DIR / f"{e.id}.json").unlink(missing_ok=True)
        if old_facts or old_exps:
            logger.info(f"🧹 过期清理: {len(old_facts)}事实, {len(old_exps)}经验")

    def _trim_facts(self):
        """超出上限时移除最低优先级的事实"""
        self._flush()
        if len(self._facts) <= MAX_FACTS:
            return
        self._facts.sort(key=lambda f: (f.priority, f.usage_count))
        removed = self._facts[:-MAX_FACTS]
        self._facts = self._facts[-MAX_FACTS:]
        for f in removed:
            (FACTS_DIR / f"{f.id}.json").unlink(missing_ok=True)
        logger.info(f"✂️ 事实裁剪: 移除 {len(removed)} 条低优先级知识")

    def _trim_experiences(self):
        """超出上限时移除最低优先级的经验"""
        self._flush()
        if len(self._experiences) <= MAX_EXPERIENCES:
            return
        self._experiences.sort(key=lambda e: (e.priority, e.created_at))
        removed = self._experiences[:-MAX_EXPERIENCES]
        self._experiences = self._experiences[-MAX_EXPERIENCES:]
        for e in removed:
            (EXPERIENCES_DIR / f"{e.id}.json").unlink(missing_ok=True)
        logger.info(f"✂️ 经验裁剪: 移除 {len(removed)} 条低优先级经验")

    def _save_fact(self, fact: Fact):
        (FACTS_DIR / f"{fact.id}.json").write_text(
            json.dumps(asdict(fact), ensure_ascii=False), encoding="utf-8")

    def _save_experience(self, exp: Experience):
        (EXPERIENCES_DIR / f"{exp.id}.json").write_text(
            json.dumps(asdict(exp), ensure_ascii=False), encoding="utf-8")
