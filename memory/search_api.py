"""
跨类型统一搜索 API — search_sessions / search_all
"""
import logging
from typing import Optional
from .indexer import search_facts, search_experiences, search_episodes, search_rules, search_procedural

logger = logging.getLogger("memory.search")

def search_sessions(query: str, limit: int = 20) -> list[dict]:
    """搜索所有会话 — 合并 episodes + facts"""
    results = []
    try:
        episodes = search_episodes(query, limit=limit)
        for e in episodes:
            e["_source"] = "episode"
            results.append(e)
        facts = search_facts(query, limit=limit)
        for f in facts:
            f["_source"] = "fact"
            results.append(f)
        results.sort(key=lambda x: x.get("priority", 0), reverse=True)
    except Exception as e:
        logger.error(f"search_sessions 失败: {e}")
    return results[:limit]

def search_all(query: str, limit: int = 30) -> dict[str, list[dict]]:
    """跨类型统一搜索 — 返回所有类型的搜索结果"""
    return {
        "episodes": search_episodes(query, limit=max(1, limit//5)) or [],
        "facts": search_facts(query, limit=max(1, limit//5)) or [],
        "experiences": search_experiences(query, limit=max(1, limit//5)) or [],
        "rules": search_rules(query, limit=max(1, limit//5)) or [],
        "procedural": search_procedural(query, limit=max(1, limit//5)) or [],
    }

# CLI 测试入口
if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "git"
    print(f"### search_sessions: {q}")
    for r in search_sessions(q)[:5]:
        print(f"  [{r.get('_source','?')}] {r.get('content','')[:80]}")
    print(f"### search_all: {q}")
    all_r = search_all(q)
    for k, v in all_r.items():
        print(f"  {k}: {len(v)} results")
