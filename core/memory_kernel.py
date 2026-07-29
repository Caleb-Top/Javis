"""Deterministic core memory for identity and other non-negotiable facts.

This module is intentionally separate from semantic/vector recall. Critical
identity facts should be available even when ordinary recall is noisy, empty,
or irrelevant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_RULES_DIR = Path(__file__).resolve().parent.parent / "brain_data" / "rules"


@dataclass(frozen=True)
class CoreIdentity:
    user_name: str = ""
    source: str = ""

    @property
    def available(self) -> bool:
        return bool(self.user_name.strip())


class CoreMemoryKernel:
    """Loads and serves critical memory through deterministic paths."""

    def __init__(self, rules_dir: Path | None = None):
        self.rules_dir = rules_dir or DEFAULT_RULES_DIR

    def load_identity(self) -> CoreIdentity:
        path = self.rules_dir / "core_identity.md"
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return CoreIdentity()

        name = self._extract_user_name(text)
        if not name:
            return CoreIdentity()
        return CoreIdentity(user_name=name, source=str(path))

    def prompt_rules(self) -> list[str]:
        identity = self.load_identity()
        if not identity.available:
            return []
        name = identity.user_name
        return [
            f"当前用户叫 {name}。这是核心身份记忆，不是临时上下文。",
            f"当 {name} 问“我是谁”“你还记得我是谁吗”时，直接回答：你是 {name}。",
            f"称呼可自然使用 {name}；不要说“还没建立正式身份关系”。",
        ]

    def answer_if_core_query(self, user_input: str) -> str:
        """Return a direct answer for identity questions, otherwise empty."""
        text = (user_input or "").strip()
        if not text:
            return ""
        if not self._is_user_identity_query(text):
            return ""

        identity = self.load_identity()
        if not identity.available:
            return "我这边没有读到你的核心身份记录。"
        name = identity.user_name
        return f"记得。你是 {name}。"

    def rules_fingerprint_value(self) -> int:
        try:
            if not self.rules_dir.exists():
                return 0
            return int(max((p.stat().st_mtime for p in self.rules_dir.glob("*.md")), default=0))
        except Exception:
            return 0

    @staticmethod
    def _extract_user_name(text: str) -> str:
        patterns = [
            r"\*\*主人\*\*\s*[:：]\s*([A-Za-z][A-Za-z0-9_-]{1,40})",
            r"主人\s*[:：]\s*([A-Za-z][A-Za-z0-9_-]{1,40})",
            r"用户\s*[:：]\s*([A-Za-z][A-Za-z0-9_-]{1,40})",
            r"我叫\s*([A-Za-z][A-Za-z0-9_-]{1,40})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _is_user_identity_query(text: str) -> bool:
        normalized = re.sub(r"\s+", "", text.lower())
        if not any(token in normalized for token in ("我是谁", "我叫啥", "我叫什么", "我的名字", "记得我是谁", "知道我是谁")):
            return False
        # Avoid intercepting questions about Javis itself.
        if "你是谁" in normalized and "我是谁" not in normalized:
            return False
        return True


def get_core_memory_kernel(rules_dir: Path | None = None) -> CoreMemoryKernel:
    return CoreMemoryKernel(rules_dir=rules_dir)

