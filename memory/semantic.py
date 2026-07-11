# -*- coding: utf-8 -*-
"""语义记忆 — 从情景记忆中自动提炼可复用规则

工作流:
  consolidate() 扫描所有未处理的 episodes
    → 按指纹分组统计失败率
    → 阈值达标则生成/更新语义规则
    → 规则存入 brain_data/semantic/

存储: brain_data/semantic/{rule_id}.json
"""
import json, os, time, logging, hashlib
from pathlib import Path
from typing import Optional

logger = logging.getLogger("memory.semantic")

SEMANTIC_DIR = Path(__file__).parent.parent / "brain_data" / "semantic"
MIN_SAMPLE_SIZE = 3        # 最少样本数才能生成规则
MIN_FAILURE_RATE = 0.6     # 失败率达到这个阈值才生成规则
MAX_CONFIDENCE = 0.95


class SemanticRule:
    """一条自动提炼的语义规则"""

    def __init__(self, fingerprint: dict, conclusion: str, risk_level: float,
                 confidence: float, evidence_ids: list[str], alternatives: list[dict]):
        now = time.time()
        self.id = f"sem_{int(now)}_{hashlib.md5(str(fingerprint).encode()).hexdigest()[:6]}"
        self.fingerprint = fingerprint
        self.conclusion = conclusion[:200]
        self.risk_level = round(risk_level, 2)
        self.confidence = round(confidence, 2)
        self.evidence_count = len(evidence_ids)
        self.evidence_ids = evidence_ids
        self.alternatives = alternatives[:5]
        self.created_at = now
        self.last_updated = now
        self.status = "active"  # active / deprecated / merged

    def save(self):
        SEMANTIC_DIR.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        (SEMANTIC_DIR / f"{self.id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


def load_all_rules() -> list[dict]:
    """加载所有活跃的语义规则"""
    rules = []
    for f in sorted(SEMANTIC_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("status", "active") == "active":
                rules.append(data)
        except:
            pass
    return rules


def find_relevant_rules(fingerprint: dict, min_confidence: float = 0.3) -> list[dict]:
    """根据当前场景指纹查找匹配的语义规则"""
    rules = load_all_rules()
    scored = []
    for rule in rules:
        rf = rule.get("fingerprint", {})
        score = 0
        for key in ["domain", "task_type"]:
            if fingerprint.get(key) and rf.get(key) == fingerprint.get(key):
                score += 1
        # 工具交集
        t_fp = set(fingerprint.get("tools_involved", []))
        t_r = set(rf.get("tools_involved", []))
        if t_fp and t_r:
            common = t_fp & t_r
            score += len(common) * 2
        # 平台匹配
        if fingerprint.get("platform") and rf.get("platform") == fingerprint.get("platform"):
            score += 1

        if score >= 2:
            scored.append((score * rule.get("confidence", 0.5), rule))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s[1] for s in scored[:5]]


def consolidate(brain=None, force: bool = False) -> int:
    """扫描所有 episodes, 自动提炼语义规则"""
    from memory.episodic import EPISODES_DIR, load_episode

    episodes = sorted(EPISODES_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    if not episodes:
        return 0

    # 按工具+领域+任务类型分组
    groups = {}
    for ep_file in episodes:
        try:
            data = json.loads(ep_file.read_text(encoding="utf-8"))
        except:
            continue
        fp = data.get("fingerprint", {})
        timeline = data.get("timeline", [])

        # 按每个工具调用分组统计
        for entry in timeline:
            tool = entry.get("tool", "unknown")
            domain = fp.get("domain", "general")
            task_type = fp.get("task_type", "general")
            platform = fp.get("platform", "")

            # 维度: domain + task_type + tool (platform 作为子维度)
            key = f"{domain}|{task_type}|{tool}"
            if key not in groups:
                groups[key] = {"count": 0, "failures": 0, "episodes": set(),
                               "success_alternatives": [], "platforms": set()}
            groups[key]["count"] += 1
            groups[key]["episodes"].add(data.get("id", ep_file.stem))
            if entry.get("result") == "failure":
                groups[key]["failures"] += 1
            else:
                # 成功的工具调用可以作为替代方案
                groups[key]["success_alternatives"].append({
                    "tool": tool, "episode": data.get("id", ""),
                })
            if platform:
                groups[key]["platforms"].add(platform)

    # 生成/更新规则
    new_count = 0
    for key, stats in groups.items():
        if stats["count"] < MIN_SAMPLE_SIZE:
            continue
        failure_rate = stats["failures"] / stats["count"]
        if failure_rate < MIN_FAILURE_RATE:
            continue

        parts = key.split("|")
        domain, task_type, tool = parts[0], parts[1], parts[2]
        confidence = min(MAX_CONFIDENCE, 1 - 1 / (stats["count"] + 1))
        evidence_ids = list(stats["episodes"])[:20]

        # 构建结论
        plat_str = f" 在 {', '.join(stats['platforms'])}" if stats["platforms"] else ""
        conclusion = f"{tool} 在 {domain}/{task_type}{plat_str} 场景下失败率 {failure_rate:.0%}（{stats['failures']}/{stats['count']}）"

        # 替代方案（除本工具外的成功工具）
        alternatives = []
        for alt in stats["success_alternatives"][:5]:
            if alt["tool"] != tool:
                alternatives.append({"tool": alt["tool"], "success_in": alt["episode"][:20]})

        # 检查是否已有类似规则（去重）
        existing = _find_similar_rule(domain, task_type, tool, stats.get("platforms", set()))
        if existing:
            # 已有规则 → 更新
            existing["evidence_ids"] = list(set(existing.get("evidence_ids", []) + evidence_ids))
            existing["evidence_count"] = len(existing["evidence_ids"])
            existing["risk_level"] = round(max(existing.get("risk_level", 0), failure_rate), 2)
            existing["confidence"] = round(min(MAX_CONFIDENCE, confidence), 2)
            existing["last_updated"] = time.time()
            (SEMANTIC_DIR / f"{existing['id']}.json").write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"📊 语义规则已更新: {existing['id']} ({conclusion[:60]})")
        else:
            # 新规则
            rule = SemanticRule(
                fingerprint={"domain": domain, "task_type": task_type, "tool": tool,
                             "platform": list(stats.get("platforms", set()))},
                conclusion=conclusion, risk_level=failure_rate,
                confidence=confidence, evidence_ids=evidence_ids,
                alternatives=alternatives,
            )
            rule.save()
            new_count += 1
            logger.info(f"📊 语义规则已生成: {rule.id} ({conclusion[:60]})")

            # 同步到大脑
            if brain:
                try:
                    brain.learn_fact(
                        f"[语义规则] {conclusion} | 替代方案: {[a['tool'] for a in alternatives[:3]]} | 置信度:{confidence}",
                        category=f"semantic.{domain}.{tool}", source="self_reflection", priority=max(2, int(confidence * 5)),
                    )
                except:
                    pass

    return new_count


def _find_similar_rule(domain: str, task_type: str, tool: str, platforms: set) -> Optional[dict]:
    """找已有类似规则"""
    for f in SEMANTIC_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            rf = data.get("fingerprint", {})
            if (rf.get("domain") == domain and rf.get("task_type") == task_type
                    and rf.get("tool") == tool and data.get("status", "active") == "active"):
                return data
        except:
            pass
    return None


def get_stats() -> dict:
    rules = load_all_rules()
    high_risk = [r for r in rules if r.get("risk_level", 0) >= 0.8]
    medium_risk = [r for r in rules if 0.6 <= r.get("risk_level", 0) < 0.8]
    return {
        "total_rules": len(rules),
        "high_risk": len(high_risk),
        "medium_risk": len(medium_risk),
        "avg_confidence": round(sum(r.get("confidence", 0) for r in rules) / max(len(rules), 1), 2),
    }
