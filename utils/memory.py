"""Javis 持久记忆系统 — 文件存储, 不依赖外部数据库

存储位置: D:\Javis\memory\
  - conversations/   → 完整对话历史
  - profiles/        → 用户偏好/习惯
  - index.json       → 索引
"""

import json, os, time, logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("memory")

MEMORY_DIR = Path(__file__).parent.parent / "memory"
CONV_DIR = MEMORY_DIR / "conversations"
PROFILE_DIR = MEMORY_DIR / "profiles"
INDEX_FILE = MEMORY_DIR / "index.json"

_MAX_CONV_DAYS = 30       # 对话保留 30 天
_MAX_CONV_COUNT = 10000   # 最多 10000 条对话
_MAX_CONV_LENGTH = 10000  # 每条对话保留最近 10000 轮


def _ensure_dirs():
    CONV_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> dict:
    _ensure_dirs()
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"conversations": [], "profiles": []}


def _save_index(idx: dict):
    INDEX_FILE.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 对话记忆 ──

def save_conversation(session_id: str, cards: list[dict], name: str = ""):
    """保存对话到文件"""
    _ensure_dirs()
    now = time.time()
    idx = _load_index()
    file_path = CONV_DIR / f"{session_id}.json"
    existing = next((c for c in idx.get("conversations", []) if c["id"] == session_id), None)
    created_at = existing.get("created_at", now) if existing else now
    saved_name = name or (existing.get("name", "") if existing else "")
    data = {
        "id": session_id, "updated_at": now,
        "created_at": created_at,
        "cards": cards[-_MAX_CONV_LENGTH:],
        "name": saved_name,
    }
    file_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # 更新索引
    idx["conversations"] = [c for c in idx["conversations"] if c["id"] != session_id]
    idx["conversations"].append({
        "id": session_id, "updated_at": now,
        "created_at": data["created_at"],
        "card_count": len(cards),
        "name": saved_name,
    })
    # 限制数量
    idx["conversations"] = sorted(idx["conversations"], key=lambda x: x["updated_at"], reverse=True)[:_MAX_CONV_COUNT]
    _save_index(idx)
    _cleanup_old()


def load_conversation(session_id: str) -> list[dict]:
    """读取对话"""
    file_path = CONV_DIR / f"{session_id}.json"
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return data.get("cards", [])
        except Exception:
            pass
    return []


def list_conversations() -> list[dict]:
    """列出所有对话摘要"""
    idx = _load_index()
    return idx.get("conversations", [])


def delete_conversation(session_id: str):
    """删除对话"""
    file_path = CONV_DIR / f"{session_id}.json"
    if file_path.exists():
        file_path.unlink()
    idx = _load_index()
    idx["conversations"] = [c for c in idx["conversations"] if c["id"] != session_id]
    _save_index(idx)


def _cleanup_old():
    """清理过期对话"""
    now = time.time()
    cutoff = now - _MAX_CONV_DAYS * 86400
    idx = _load_index()
    idx["conversations"] = [c for c in idx["conversations"] if c.get("updated_at", 0) > cutoff]
    _save_index(idx)


# ── 用户偏好记忆 ──

def save_profile(key: str, value: str):
    """保存一条用户偏好 (用户名/习惯/设置等)"""
    _ensure_dirs()
    file_path = PROFILE_DIR / f"{key}.json"
    data = {"key": key, "value": value, "updated_at": time.time()}
    file_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_profile(key: str) -> str | None:
    """读取用户偏好"""
    file_path = PROFILE_DIR / f"{key}.json"
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return data.get("value")
        except Exception:
            pass
    return None


def load_all_profiles() -> dict:
    """读取所有偏好"""
    result = {}
    if PROFILE_DIR.exists():
        for f in PROFILE_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                result[data["key"]] = data["value"]
            except Exception:
                pass
    return result


# ── 启动时清理 ──
_ensure_dirs()

# ════════════════════════════════════════════════════════════
# P0-9: SessionStore — 会话持久化存储
# ════════════════════════════════════════════════════════════

from dataclasses import dataclass, field


@dataclass
class SessionMeta:
    """会话元数据"""
    session_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    name: str = ""
    message_count: int = 0
    tool_call_count: int = 0
    last_topic: str = ""
    phase: str = "idle"

    def touch(self):
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.session_id, "created_at": self.created_at,
            "updated_at": self.updated_at, "name": self.name,
            "message_count": self.message_count,
            "tool_call_count": self.tool_call_count,
            "last_topic": self.last_topic, "phase": self.phase,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionMeta":
        return cls(
            session_id=d.get("id", ""),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            name=d.get("name", ""),
            message_count=d.get("message_count", 0),
            tool_call_count=d.get("tool_call_count", 0),
            last_topic=d.get("last_topic", ""),
            phase=d.get("phase", "idle"),
        )


class SessionStore:
    """会话持久化存储 — 跨会话存活

    存储位置: D:\Javis\memory\sessions\
    结构:
      sessions/<session_id>.json  → 完整对话
      sessions_index.json          → 快速索引
    """

    SESSIONS_DIR = MEMORY_DIR / "sessions"
    SESSIONS_INDEX = MEMORY_DIR / "sessions_index.json"
    _MAX_MESSAGES = 200
    _MAX_SESSIONS = 500

    def __init__(self):
        self.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        if not self.SESSIONS_INDEX.exists():
            self._save_index({})

    def _load_index(self) -> dict:
        try:
            if self.SESSIONS_INDEX.exists():
                return json.loads(self.SESSIONS_INDEX.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_index(self, idx: dict):
        self.SESSIONS_INDEX.write_text(
            json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")

    def save(self, session_id: str, messages: list[dict], meta: SessionMeta = None) -> None:
        now = time.time()
        file_path = self.SESSIONS_DIR / f"{session_id}.json"
        if meta is None:
            meta = self.load_meta(session_id) or SessionMeta(session_id=session_id)
        meta.touch()
        if not meta.name and messages:
            first = messages[0].get("content", "") if messages else ""
            if isinstance(first, str) and first:
                meta.name = first[:40]
        meta.message_count = len(messages)
        data = {"session_id": session_id, "meta": meta.to_dict(),
                "messages": messages[-self._MAX_MESSAGES:], "saved_at": now}
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        idx = self._load_index()
        idx[session_id] = meta.to_dict()
        if len(idx) > self._MAX_SESSIONS:
            sorted_s = sorted(idx.items(), key=lambda x: x[1].get("updated_at", 0), reverse=True)
            idx = dict(sorted_s[:self._MAX_SESSIONS])
        self._save_index(idx)

    def load(self, session_id: str) -> tuple:
        file_path = self.SESSIONS_DIR / f"{session_id}.json"
        if not file_path.exists():
            return [], None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
            meta_dict = data.get("meta")
            meta = SessionMeta.from_dict(meta_dict) if meta_dict else None
            return messages, meta
        except Exception as e:
            logger.warning(f"SessionStore.load({session_id}) failed: {e}")
            return [], None

    def load_messages(self, session_id: str) -> list[dict]:
        messages, _ = self.load(session_id)
        return messages

    def load_meta(self, session_id: str) -> "SessionMeta | None":
        idx = self._load_index()
        if session_id in idx:
            return SessionMeta.from_dict(idx[session_id])
        _, meta = self.load(session_id)
        return meta

    def delete(self, session_id: str) -> None:
        file_path = self.SESSIONS_DIR / f"{session_id}.json"
        if file_path.exists():
            file_path.unlink()
        idx = self._load_index()
        idx.pop(session_id, None)
        self._save_index(idx)

    def list_sessions(self, limit: int = 50) -> list[dict]:
        idx = self._load_index()
        return sorted(idx.values(), key=lambda x: x.get("updated_at", 0), reverse=True)[:limit]

    def find_by_topic(self, keyword: str, limit: int = 10) -> list[dict]:
        kw = keyword.lower()
        results = []
        for s in self.list_sessions(limit=200):
            if kw in s.get("name", "").lower() or kw in s.get("last_topic", "").lower():
                results.append(s)
                if len(results) >= limit:
                    break
        return results

    def recent_sessions(self, n: int = 10) -> list[dict]:
        return self.list_sessions(limit=n)

    def cleanup_old(self, max_days: int = 30) -> int:
        cutoff = time.time() - max_days * 86400
        deleted = 0
        idx = self._load_index()
        to_delete = [sid for sid, meta in idx.items() if meta.get("updated_at", 0) < cutoff]
        for sid in to_delete:
            self.delete(sid)
            deleted += 1
        return deleted

    def clear_all(self) -> int:
        idx = self._load_index()
        count = 0
        for sid in list(idx.keys()):
            self.delete(sid)
            count += 1
        return count

    @property
    def stats(self) -> dict:
        idx = self._load_index()
        total = len(idx)
        if not idx:
            return {"total_sessions": 0, "total_messages": 0, "earliest": None, "latest": None}
        messages = sum(m.get("message_count", 0) for m in idx.values())
        times = [m.get("created_at", 0) for m in idx.values()]
        return {
            "total_sessions": total, "total_messages": messages,
            "earliest": time.strftime("%Y-%m-%d", time.localtime(min(times))),
            "latest": time.strftime("%Y-%m-%d", time.localtime(max(times))),
        }

