"""
记忆检索工具 — Agent 按需调用的记忆接口
LLM 需要回忆时主动调用这些工具, 不预注入。

注意: 不持有自己的 Brain 实例, 通过 memory.controller 获取活跃的 brain。
"""
import time, logging
logger = logging.getLogger("memory.tools")

__all__ = ["memory_recall", "memory_recent"]


def _get_live_brain():
    """从 memory controller 获取活跃的 brain 实例（避免与主 brain 隔离）"""
    try:
        from memory.controller import _controller
        if _controller._brain is not None:
            return _controller._brain
    except Exception:
        pass
    logger.warning("memory_tools: 无法从 controller 获取 brain, 返回 None")
    return None


async def memory_recall(query: str) -> str:
    """搜索记忆库 — 从 brain._facts 关键词匹配"""
    try:
        from memory.indexer import search_episodes, ensure_index
        ensure_index()
    except Exception as e:
        return f"error: {e}"

    b = _get_live_brain()
    if b is None:
        return "error: 记忆系统未就绪"

    parts = []
    q = query.lower()

    # Facts (关键词匹配 brain._facts)
    try:
        matches = [(f.priority, f.content[:80]) for f in b._facts if q in f.content.lower()]
        matches.sort(key=lambda x: -x[0])
        if matches:
            lines = [c for _, c in matches[:8]]
            parts.append("知识:\n" + "\n".join(lines))
    except:
        pass

    # Episodes
    try:
        eps = search_episodes(limit=3)
        for ep in eps:
            inp = (ep.get("user_input") or "")[:40]
            if inp:
                parts.append(f"[{_fmt_age(ep.get('start_time',0))}] {inp}")
    except:
        pass

    if not parts:
        return f"未找到与 '{query}' 相关的记忆"
    return "\n\n".join(parts)


async def memory_recent() -> str:
    """最近对话上下文 — 直接从 brain._facts 读取"""
    b = _get_live_brain()
    if b is None:
        return "记忆系统未就绪"

    parts = []

    try:
        topics = sorted([f for f in b._facts if f.category == "session.topic"],
                        key=lambda x: x.created_at, reverse=True)[:20]
        if topics:
            lines = [t.content[:80] for t in topics]
            parts.append("最近话题:\n" + "\n".join(lines))
    except:
        pass

    try:
        msgs = sorted([f for f in b._facts if f.category == "conversation.user_msgs"],
                      key=lambda x: x.created_at, reverse=True)[:15]
        if msgs:
            lines = [m.content[:60] for m in msgs]
            parts.append("你说过:\n" + "\n".join(lines))
    except:
        pass

    return "\n\n".join(parts) if parts else "暂无最近对话记录"


def _fmt_age(timestamp):
    if not timestamp:
        return ""
    diff = time.time() - timestamp
    if diff < 60: return "刚刚"
    if diff < 3600: return f"{int(diff/60)}分钟前"
    if diff < 86400: return f"{int(diff/3600)}小时前"
    return f"{int(diff/86400)}天前"
