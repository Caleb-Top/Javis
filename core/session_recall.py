"""Deterministic recall for the currently selected conversation.

Long-term memory is useful for stable facts. Current-session questions need a
different path: read the visible conversation cards first, then summarize them
without asking the model to guess.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable


DEFAULT_CONVERSATIONS_DIR = Path(__file__).resolve().parent.parent / "memory" / "conversations"

SESSION_RECALL_MARKERS = (
    "这轮对话",
    "本轮对话",
    "当前对话",
    "这次对话",
    "整轮对话",
    "这一轮",
    "你看看这轮",
    "看看这轮",
    "我们刚才聊",
    "刚才讨论",
    "前面讨论",
)


def is_session_recall_query(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    if not normalized:
        return False
    if any(marker in normalized for marker in SESSION_RECALL_MARKERS):
        return True
    return bool(re.search(r"我们(之前|前面|刚才).{0,8}(聊|讨论|说|做)", normalized))


def build_session_recall_answer(
    user_input: str,
    *,
    state_messages: list[dict] | None = None,
    conversation_cards: list[dict] | None = None,
    session_id: str = "",
    conversations_dir: Path | None = None,
    max_turns: int = 40,
) -> str:
    """Return a concise current-session answer, or empty when not applicable."""

    if not is_session_recall_query(user_input):
        return ""

    visible_turns = _cards_to_turns(conversation_cards or [])
    state_turns = _messages_to_turns(state_messages or [])
    # The UI cards are the authoritative current session. Do not let an
    # unrelated "most recent" file evict them from the bounded recall window.
    persisted_turns = (
        _load_session_turns(session_id, conversations_dir)
        if session_id or not (visible_turns or state_turns)
        else []
    )
    turns = _merge_turns(persisted_turns, state_turns, visible_turns)
    turns = _without_current_query(turns, user_input)[-max_turns:]
    if not turns:
        return "不需要重新训练。是当前会话记录没有被稳定注入，我这边没读到这轮对话内容。"

    topics = _detect_topics(turns)
    if not topics:
        snippets = _fallback_user_snippets(turns)
        if snippets:
            topics = [f"你前面主要提到：{'；'.join(snippets[:3])}"]

    if not topics:
        return "不用重新训练。问题在当前会话召回链路，不在模型权重。"

    body = "；".join(topics[:6])
    return f"不用重新训练。问题是当前会话召回没接稳。\n\n这轮对话不是只聊风格。主要是：{body}。\n\n我会按当前任务记录来回答，不再只拿旧的长期记忆硬猜。"


def _cards_to_turns(cards: Iterable[dict]) -> list[dict]:
    turns = []
    for card in cards:
        role = str(card.get("role", "")).strip()
        text = str(card.get("text", "")).strip()
        if role in {"user", "assistant"} and text:
            turns.append({"role": role, "text": _clean_text(text)})
    return turns


def _messages_to_turns(messages: Iterable[dict]) -> list[dict]:
    turns = []
    for msg in messages:
        role = str(msg.get("role", "")).strip()
        content = msg.get("content", "")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        text = content.strip()
        if text and not text.startswith("["):
            turns.append({"role": role, "text": _clean_text(text)})
    return turns


def _load_session_turns(session_id: str, conversations_dir: Path | None) -> list[dict]:
    root = conversations_dir or DEFAULT_CONVERSATIONS_DIR
    if not root.exists():
        return []

    paths: list[Path] = []
    if session_id:
        candidate = root / f"{Path(session_id).name}.json"
        if candidate.exists():
            paths.append(candidate)
    if not paths:
        try:
            paths = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]
        except Exception:
            paths = []

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            turns = _cards_to_turns(data.get("cards", []))
            if turns:
                return turns
        except Exception:
            continue
    return []


def _merge_turns(*groups: list[dict]) -> list[dict]:
    merged = []
    seen = set()
    for group in groups:
        for turn in group:
            key = (turn.get("role"), turn.get("text", "")[:180])
            if key in seen:
                continue
            seen.add(key)
            merged.append(turn)
    return merged


def _without_current_query(turns: list[dict], user_input: str) -> list[dict]:
    current = _clean_text(user_input)
    if turns and turns[-1].get("role") == "user" and turns[-1].get("text") == current:
        return turns[:-1]
    return turns


def _detect_topics(turns: list[dict]) -> list[str]:
    text = "\n".join(t["text"] for t in turns)
    topics = []
    patterns = [
        (("训练", "EMNIST", "CNN", "准确率", "进度条", "epoch"), "训练脚本和可视化进度"),
        (("终端", "跑完", "展示", "过程"), "终端过程验证"),
        (("自我检测", "全局检测", "退化", "模块", "报告"), "自检、退化判断和模块报告"),
        (("先结论", "短句", "原始数据", "风格"), "你的回复风格规则"),
        (("Eric", "我是谁", "名字", "身份"), "Eric 身份记忆"),
        (("这轮对话", "本轮对话", "当前对话"), "当前会话召回缺陷"),
        (("Javis", "记忆", "持久", "brain_data", "memory_status"), "长期记忆系统"),
    ]
    for keys, label in patterns:
        if any(k in text for k in keys):
            topics.append(label)
    return topics


def _fallback_user_snippets(turns: list[dict]) -> list[str]:
    snippets = []
    for turn in turns:
        if turn.get("role") != "user":
            continue
        text = turn.get("text", "").strip()
        if not text or len(text) < 4:
            continue
        text = re.sub(r"\s+", " ", text)
        snippets.append(text[:32])
    return snippets[-4:]


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:2000]
