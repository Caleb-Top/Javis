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
