"""
Memory 系统 — 跨会话持久化记忆

四类记忆:
- user      → 用户角色/偏好/知识背景
- feedback  → 用户校正和确认
- project   → 项目状态/进度/决策
- reference → 外部系统链接/指针

存储结构:
memory/
├── MEMORY.md          ← 索引文件 (每行一条)
├── user_role.md       ← 具体记忆条目
├── feedback_testing.md
└── ...
"""
from __future__ import annotations
import os, yaml, logging, time
from typing import Optional
from dataclasses import dataclass, field

from .types import MemoryEntry, MemoryType

logger = logging.getLogger("agent.memory")


class MemoryManager:
    """
    记忆系统管理器。

    职责:
    1. 读写 MEMORY.md 索引
    2. 读写每一条记忆的 Markdown 文件
    3. 决定何时写入 (15 分钟阈值)
    4. 检索相关记忆注入到系统提示
    """

    def __init__(self, memory_dir: str):
        self.memory_dir = memory_dir
        os.makedirs(memory_dir, exist_ok=True)
        self._index_path = os.path.join(memory_dir, "MEMORY.md")
        self._last_write_time: float = 0.0
        self._write_interval: float = 900.0  # 15 分钟

    # ═══════════════════════════════════════════════
    # 读
    # ═══════════════════════════════════════════════

    def load_all(self) -> list[MemoryEntry]:
        """加载所有记忆条目"""
        entries = []
        index_entries = self._read_index()

        for line in index_entries:
            name = self._extract_name_from_index(line)
            if not name:
                continue

            file_path = os.path.join(self.memory_dir, f"{name}.md")
            if not os.path.exists(file_path):
                continue

            entry = self._read_entry(name, file_path)
            if entry:
                entries.append(entry)

        logger.info(f"Memory: 已加载 {len(entries)} 条记忆")
        return entries

    def load_for_prompt(self, max_entries: int = 10) -> list[MemoryEntry]:
        """加载用于注入系统提示的记忆 (优先 user/feedback/project)"""
        all_entries = self.load_all()
        # 优先级排序: user > feedback > project > reference
        priority_order = {
            MemoryType.USER: 0,
            MemoryType.FEEDBACK: 1,
            MemoryType.PROJECT: 2,
            MemoryType.REFERENCE: 3,
        }
        sorted_entries = sorted(
            all_entries,
            key=lambda e: priority_order.get(e.type, 99),
        )
        return sorted_entries[:max_entries]

    # ═══════════════════════════════════════════════
    # 写
    # ═══════════════════════════════════════════════

    def save(self, entry: MemoryEntry) -> bool:
        """
        保存一条记忆。

        步骤:
        1. 写入 Markdown 文件 (带 frontmatter)
        2. 更新 MEMORY.md 索引
        """
        # Step 1: 写入文件
        file_path = os.path.join(self.memory_dir, f"{entry.name}.md")
        content = self._format_entry(entry)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Memory 写入失败 [{entry.name}]: {e}")
            return False

        # Step 2: 更新索引
        self._update_index(entry)

        self._last_write_time = time.time()
        logger.info(f"Memory 已保存: [{entry.type.value}] {entry.name}")
        return True

    def should_write(self) -> bool:
        """检查是否超过写入间隔 (15 分钟)"""
        return (time.time() - self._last_write_time) > self._write_interval

    # ═══════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════

    def _read_index(self) -> list[str]:
        """读取 MEMORY.md 索引"""
        if not os.path.exists(self._index_path):
            return []
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith("#")]
            return lines
        except Exception:
            return []

    def _update_index(self, entry: MemoryEntry) -> None:
        """更新索引文件"""
        lines = self._read_index()
        # 去掉已有的同名条目
        lines = [l for l in lines if self._extract_name_from_index(l) != entry.name]
        # 添加新条目
        line = f"- [{entry.name}]({entry.name}.md) — {entry.description[:100]}"
        lines.append(line)
        # 写回 (保持排序)
        lines.sort()
        with open(self._index_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    @staticmethod
    def _extract_name_from_index(line: str) -> Optional[str]:
        """从索引行提取 name"""
        # 格式: - [name](name.md) — description
        if "](" not in line:
            return None
        try:
            start = line.index("[") + 1
            end = line.index("]")
            return line[start:end]
        except ValueError:
            return None

    def _read_entry(self, name: str, file_path: str) -> Optional[MemoryEntry]:
        """读取单条记忆文件"""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            return None

        # 解析 frontmatter
        frontmatter = {}
        body = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1]) or {}
                except Exception:
                    pass
                body = parts[2].strip()

        mem_type = MemoryType(frontmatter.get("type", "reference"))

        return MemoryEntry(
            name=name,
            description=frontmatter.get("description", ""),
            type=mem_type,
            content=body,
            file_path=file_path,
        )

    @staticmethod
    def _format_entry(entry: MemoryEntry) -> str:
        """格式化记忆为 Markdown"""
        return (
            f"---\n"
            f"name: {entry.name}\n"
            f"description: {entry.description}\n"
            f"type: {entry.type.value}\n"
            f"---\n\n"
            f"{entry.content}\n"
        )

    # ═══════════════════════════════════════════════
    # Memory 写入触发逻辑 (蒸馏自系统提示)
    # ═══════════════════════════════════════════════

    TRIGGERS = {
        "user": [
            "了解到用户的角色/职责/偏好",
            "了解到用户的知识背景/技能水平",
            "用户明确表达了工作方式偏好",
        ],
        "feedback": [
            "用户纠正了 Agent 的错误",
            "用户确认了一个非显而易见的做法是正确的",
            "用户要求 Agent 停止某种行为",
        ],
        "project": [
            "获知谁在做什么、为什么、截止时间",
            "项目方向或重要决策变更",
            "发现配置/约束/依赖的非显而易见信息",
        ],
        "reference": [
            "获知信息在外部系统中的位置",
            "用户指定了某个系统的 source of truth",
        ],
    }

    NOT_TRIGGERS = [
        "可从代码/文件系统重新获取的信息",
        "CLAUD.md 中已有记录的内容",
        "当前对话临时状态",
        "一次性的分析结果",
    ]
