# -*- coding: utf-8 -*-
"""程序记忆 — 高频成功的操作链固化为可执行模板

当某个工具组合在相似场景下成功多次后，
自动固化为程序记忆，下次可直接调用。

存储: brain_data/procedural/{proc_id}.json
"""
import json, os, time, logging, hashlib
from pathlib import Path
from typing import Optional

logger = logging.getLogger("memory.procedural")

PROCEDURAL_DIR = Path(__file__).parent.parent / "brain_data" / "procedural"
MIN_EXECUTIONS = 5      # 最少执行次数才能固化
MIN_SUCCESS_RATE = 0.8  # 最低成功率


def consolidate_from_episodes():
    """从 episodes 中提取高频成功序列"""
    from memory.episodic import EPISODES_DIR
    episodes = sorted(EPISODES_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)

    # 按 fingerprint + 工具序列 分组
    chains = {}  # key → {"count": N, "success": N, "tools": [...], "episodes": []}
    for ep_file in episodes:
        try:
            data = json.loads(ep_file.read_text(encoding="utf-8"))
        except:
            continue
        if data.get("outcome") == "failure":
            continue  # 只从成功/部分成功的 episode 提取

        fp = data.get("fingerprint", {})
        timeline = data.get("timeline", [])
        if len(timeline) < 2:
            continue

        # 提取工具序列（去重连续重复的工具）
        tool_sequence = []
        for entry in timeline:
            t = entry.get("tool", "")
            if not tool_sequence or tool_sequence[-1] != t:
                tool_sequence.append(t)

        if len(tool_sequence) < 2:
            continue

        key = f"{fp.get('domain', 'general')}|{fp.get('task_type', 'general')}|{'→'.join(tool_sequence[:5])}"
        if key not in chains:
            chains[key] = {"count": 0, "success": 0, "tools": tool_sequence,
                           "episodes": [], "domain": fp.get("domain", ""), "task_type": fp.get("task_type", "")}
        chains[key]["count"] += 1
        chains[key]["episodes"].append(data.get("id"))
        if data.get("outcome") == "success":
            chains[key]["success"] += 1

    # 生成程序记忆
    new_count = 0
    for key, chain in chains.items():
        success_rate = chain["success"] / chain["count"]
        if chain["count"] < MIN_EXECUTIONS or success_rate < MIN_SUCCESS_RATE:
            continue

        proc_id = f"proc_{int(time.time())}_{hashlib.md5(key.encode()).hexdigest()[:6]}"
        data = {
            "id": proc_id, "domain": chain["domain"], "task_type": chain["task_type"],
            "tool_sequence": chain["tools"],
            "success_rate": round(success_rate, 2), "execution_count": chain["count"],
            "source_episodes": chain["episodes"][:10],
            "created_at": time.time(), "last_triggered": 0,
            "avg_latency_ms": 0,  # 可后续更新
        }
        (PROCEDURAL_DIR / f"{proc_id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        new_count += 1
        logger.info(f"🧠 程序记忆已固化: {proc_id} ({'→'.join(chain['tools'][:4])}) [{success_rate:.0%}]")

    return new_count


def find_procedural(domain: str, task_type: str) -> Optional[dict]:
    """按领域+任务类型查找程序记忆"""
    best = None
    best_score = 0
    for f in PROCEDURAL_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            score = 0
            if data.get("domain") == domain: score += 2
            if data.get("task_type") == task_type: score += 3
            score += int(data.get("success_rate", 0) * 10)
            if score > best_score:
                best_score = score
                best = data
        except:
            pass
    return best


def get_stats() -> dict:
    procs = []
    for f in PROCEDURAL_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            procs.append(data)
        except:
            pass
    return {"total": len(procs), "avg_success_rate": round(sum(p.get("success_rate", 0) for p in procs) / max(len(procs), 1), 2)}
