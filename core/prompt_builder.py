"""Prompt Builder (P0-8) — Three-Layer Prompt Architecture

三层架构:
  Layer 1 — 基础身份层 (Base Identity): 个性、语气、核心能力，写死+极少变化
  Layer 2 — 动态记忆层 (Memory Context): 脑数据注入、经验规则、最近话题
  Layer 3 — 阶段指引层 (Phase Guidance): 当前阶段指引、护栏摘要、安全规则

缓存策略:
  - Layer 1: 静态，整个 session 不变
  - Layer 2: 每 N 步或每次请求时刷新（brain 数据可能更新）
  - Layer 3: 阶段变化时刷新

集成: Agent._build_system_prompt() → PromptBuilder.build()
"""

from __future__ import annotations

import hashlib, json, logging, time
from typing import Optional

logger = logging.getLogger("prompt_builder")


# ============================================================
# Layer 1: 基础身份层（静态，极少变化）
# ============================================================
LAYER1_BASE_IDENTITY = """你是 Javis — 一个真正能思考、能编程、能控制电脑的 AI 智能体。

## 你的说话方式（比功能更重要）

你的风格模板参考了 Claude Fable 5（你的搭档）——自然、不装、有温度。

**说人话，别念报告。**
- 不要上来就抛 "CPU:10.9% 内存:60.6%" 这种冷冰冰的数据行
- 先来一句自然的结论："还行，挺轻快的，CPU才跑了10%"
- 数据是支撑，不是开头

**交代背景，不要只扔结论。**
- 坏："✅ system_info — CPU:10.9% 内存:60.6%"
- 好："看了一眼系统，资源吃得不重，CPU 才 10%，内存占了六成，磁盘还有将近三分之二的空间。"

**你是助手，不是说明书。**
- 用"咱"、"我们"拉近距离没关系
- 复杂操作前简单说一句要干什么："我先看看你的文件结构"
- 做完后说一句结果，不用反复确认"要继续吗"

**语感上：**
- 短句优先，偶尔来点语气词（嗯、对了、不过）
- 不用每句话都感叹号
- 不用每段都加 emoji
- 数据和结论之间自然过渡，不要让用户感觉在读 log
- 遇到问题直说，不用铺垫
- 绝对不要复读工具返回的原始数据。工具返回了 "CPU:10% 内存:61%" 你看到了就行，换成你自己的话说

## 你的核心能力
当用户提出需求时, 按阶段推进:
1. 规划: 分析需求, 输出完整计划
2. 执行: 用 run_code 或工具逐步执行
3. 验证: 检查结果, 如果不对则重试

## run_code 是你最重要的工具
预置环境: user32, ctypes, os, subprocess, json, time, pyautogui, psutil, re, math, urllib

## 预置工具
open_app, find_app, focus_window, screenshot, mouse_click,
keyboard_type, keyboard_press, set_volume, list_windows,
read_ui_window, get_window_state

## 行为准则
1. 先分析再行动, 复杂任务必须输出计划
2. 能用 run_code 解决的, 就写代码
3. 执行后自己验证结果
4. 用中文回复，用上面的风格
5. 你有跨会话持久记忆。每次对话开始时，系统提示里的「经验规则」和「风格守则」
   是从你硬盘上的 brain_data/ 加载的——你的用户偏好、对话教训都写在那里。
   所以不要说自己"记不住"或"关窗就忘"。你记着呢，只是每次需要重新加载到上下文而已。
6. 如果忘记之前说过什么，用 brain_status 或 memory_status 查自己的记忆库"""


# ============================================================
# Layer 2: 动态记忆层（随 brain 数据变化）
# ============================================================
class MemoryLayerBuilder:
    """从 brain 数据构建 Layer 2（动态记忆上下文）"""

    @staticmethod
    def build(brain=None, max_experiences: int = 5,
              max_style_rules: int = 5, max_topics: int = 3,
              max_user_msgs: int = 3) -> str:
        """构建记忆注入层。返回格式化文本或空字符串。"""
        if not brain:
            return ""

        blocks: list[str] = []

        # 1. 高优先级经验规则
        exps = MemoryLayerBuilder._get_experiences(brain, max_experiences)
        if exps:
            blocks.append("## 📋 经验规则\n" + "\n".join(
                f"{i+1}. {e}" for i, e in enumerate(exps)))

        # 2. 风格守则
        styles = MemoryLayerBuilder._get_style_rules(brain, max_style_rules)
        if styles:
            blocks.append("## 🎨 风格守则\n" + "; ".join(styles[:max_style_rules]))

        # 3. 最近话题
        topics = MemoryLayerBuilder._get_recent_topics(brain, max_topics)
        if topics:
            blocks.append("## 💬 最近话题\n" + "; ".join(topics))

        # 4. 用户最近消息
        msgs = MemoryLayerBuilder._get_user_msgs(brain, max_user_msgs)
        if msgs:
            blocks.append("## 🗣 你说过\n" + "; ".join(msgs))

        return "\n\n".join(blocks) if blocks else ""

    @staticmethod
    def _get_experiences(brain, max_n: int) -> list[str]:
        """获取高优先级经验（去重）"""
        seen: set[str] = set()
        results: list[str] = []
        try:
            for exp in brain.get_priority_experiences(min_priority=3):
                if exp.lesson and len(exp.lesson) > 10:
                    key = exp.lesson[:100]
                    if key not in seen:
                        seen.add(key)
                        results.append(exp.lesson[:120])
                if len(results) >= max_n:
                    break
        except Exception:
            pass
        return results

    @staticmethod
    def _get_style_rules(brain, max_n: int) -> list[str]:
        """获取风格守则"""
        seen: set[str] = set()
        results: list[str] = []
        try:
            for f in sorted(brain._facts, key=lambda x: -x.priority):
                if (f.category.startswith("user_style")
                        and f.priority >= 4
                        and len(f.content) > 5):
                    key = f.content[:60]
                    if key not in seen:
                        seen.add(key)
                        results.append(f.content[:100])
                if len(results) >= max_n:
                    break
        except Exception:
            pass
        return results

    @staticmethod
    def _get_recent_topics(brain, max_n: int) -> list[str]:
        """获取最近会话主题"""
        results: list[str] = []
        try:
            tops = sorted(
                [f for f in brain._facts if f.category == "session.topic"],
                key=lambda x: x.created_at, reverse=True,
            )[:max_n]
            for t in tops:
                results.append(t.content[:60])
        except Exception:
            pass
        return results

    @staticmethod
    def _get_user_msgs(brain, max_n: int) -> list[str]:
        """获取用户最近消息片段"""
        results: list[str] = []
        try:
            msgs = sorted(
                [f for f in brain._facts
                 if f.category == "conversation.user_msgs"],
                key=lambda x: x.created_at, reverse=True,
            )[:max_n]
            for m in msgs:
                results.append(m.content[:50])
        except Exception:
            pass
        return results


# ============================================================
# Layer 3: 阶段指引层（随 phase 变化）
# ============================================================
class PhaseGuidanceBuilder:
    """构建当前阶段的指引、护栏摘要、安全规则"""

    # 各阶段指引模板
    PHASE_GUIDES: dict[str, str] = {
        "planning": (
            "\n\n【📍 当前阶段: 规划】\n"
            "你正在分析用户需求。先完整理解意图，输出清晰的计划步骤，"
            "然后再调用工具。不要急于执行——好的计划是成功的一半。\n"
            "提示: 复杂的 multi-step 任务先列步骤；简单的问题直接规划+执行。"
        ),
        "executing": (
            "\n\n【⚡ 当前阶段: 执行】\n"
            "按计划逐步执行工具调用。每一步汇报结果，遇到错误立即报告。\n"
            "提示: 如果工具连续失败 2 次，停下来分析原因，不要盲目重试。"
        ),
        "verifying": (
            "\n\n【🔍 当前阶段: 验证】\n"
            "检查上一步执行结果是否正确。如果不正确，判断是参数问题还是策略问题。\n"
            "提示: 参数问题→修正重试；策略问题→回到规划阶段重新设计。"
        ),
        "learning": (
            "\n\n【🧠 当前阶段: 学习】\n"
            "从本次对话中提取可复用的知识。什么做对了？什么可以改进？\n"
            "将关键教训记录为经验规则，方便未来复用。"
        ),
    }

    # 工具安全分类摘要（来自 tool_guardrails）
    _SAFETY_SUMMARY = (
        "\n\n## 🛡 安全规则\n"
        "• 安全工具（自动批准）: screenshot, file_read/list, system_info, web_search, "
        "brain_status, memory_status, find_app, list_windows, camera_snapshot\n"
        "• 需确认工具: file_write, file_delete, execute_command, run_code\n"
        "• 操作前评估风险，危险操作需用户确认\n"
        "• 不要修改系统关键文件或注册表\n"
        "• 敏感信息（token/key/密码）不在回复中展示"
    )

    @classmethod
    def build(cls, phase: str = "planning",
              include_safety: bool = True,
              include_guardrails_summary: bool = True) -> str:
        """构建阶段指引层"""
        parts: list[str] = []

        # 阶段指引
        guide = cls.PHASE_GUIDES.get(phase, cls.PHASE_GUIDES["planning"])
        parts.append(guide)

        # 护栏摘要
        if include_guardrails_summary:
            parts.append(cls._build_guardrails_summary())

        # 安全规则
        if include_safety:
            parts.append(cls._SAFETY_SUMMARY)

        return "".join(parts)

    @classmethod
    def _build_guardrails_summary(cls) -> str:
        """构建护栏摘要（轻量版，减少 token 消耗）"""
        return (
            "\n\n## ⚙️ 执行约束\n"
            "• 工具调用频率限制已生效 — 重复调用相同工具可能被限流\n"
            "• 单步超时 30s，输出上限 100KB\n"
            "• 敏感信息检查已开启 — 输出中的 API Key/Token 会被标记\n"
            "• 循环检测: 同参数重复调用 ≥4 次自动中止"
        )


# ============================================================
# PromptBuilder: 三层组装器
# ============================================================
class PromptBuilder:
    """组装三层 Prompt，带分层缓存"""

    def __init__(self, brain=None):
        self.brain = brain

        # 分层缓存
        self._layer1: str = LAYER1_BASE_IDENTITY  # 静态
        self._layer2: str = ""
        self._layer2_hash: str = ""  # brain 数据指纹
        self._layer3: str = ""
        self._layer3_phase: str = ""  # 上次构建时的 phase

        # 整体缓存
        self._cached_full: str = ""
        self._cached_step: int = -1
        self._rebuild_every_n_steps: int = 5

        self._build_count: int = 0

    # ── 各层独立构建 ──

    def build_layer1(self) -> str:
        """Layer 1: 基础身份（始终返回缓存）"""
        return self._layer1

    def build_layer2(self, force: bool = False) -> str:
        """Layer 2: 动态记忆（有变化时重建）"""
        if not self.brain:
            self._layer2 = ""
            return ""

        # 计算 brain 数据指纹
        new_hash = self._compute_brain_fingerprint()
        if not force and new_hash == self._layer2_hash and self._layer2:
            return self._layer2

        self._layer2 = MemoryLayerBuilder.build(brain=self.brain)
        self._layer2_hash = new_hash
        if self._layer2:
            logger.debug(f"🧠 Layer2 重建 ({len(self._layer2)} chars)")
        return self._layer2

    def build_layer3(self, phase: str, force: bool = False) -> str:
        """Layer 3: 阶段指引（阶段变化时重建）"""
        if not force and phase == self._layer3_phase and self._layer3:
            return self._layer3

        self._layer3 = PhaseGuidanceBuilder.build(phase=phase)
        self._layer3_phase = phase
        logger.debug(f"📍 Layer3 重建 phase={phase} ({len(self._layer3)} chars)")
        return self._layer3

    # ── 完整组装 ──

    def build(self, phase: str = "planning", step: int = 0,
              force_rebuild: bool = False) -> str:
        """组装完整的三层 System Prompt。

        Args:
            phase: 当前阶段 (planning/executing/verifying/learning)
            step: 当前步数（用于缓存策略）
            force_rebuild: 强制重建所有层
        """
        # 缓存判断: 非强制 + 同一步数窗口内
        if (not force_rebuild
                and self._cached_full
                and step - self._cached_step < self._rebuild_every_n_steps):
            return self._cached_full

        layer1 = self.build_layer1()
        layer2 = self.build_layer2(force=force_rebuild)
        layer3 = self.build_layer3(phase, force=force_rebuild)

        parts = [layer1]
        if layer2:
            parts.append(layer2)
        parts.append(layer3)

        full = "\n".join(parts)
        self._cached_full = full
        self._cached_step = step
        self._build_count += 1

        total_chars = len(full)
        logger.debug(
            f"📝 Prompt assembled: L1={len(layer1)} + "
            f"L2={len(layer2)} + L3={len(layer3)} = {total_chars} chars "
            f"(build #{self._build_count})"
        )
        return full

    def invalidate_cache(self):
        """使所有缓存失效（brain 数据大更新后调用）"""
        self._layer2_hash = ""
        self._layer3_phase = ""
        self._cached_full = ""
        self._cached_step = -1
        logger.debug("🗑 Prompt 缓存已清除")

    # ── 内部方法 ──

    def _compute_brain_fingerprint(self) -> str:
        """计算 brain 数据指纹（快速判断是否需要重建 Layer2）"""
        if not self.brain:
            return ""
        try:
            # 经验数 + 最近 3 个 topic + 最近事实时间戳
            exp_count = len(self.brain._experiences) if hasattr(self.brain, '_experiences') else 0
            fact_count = len(self.brain._facts) if hasattr(self.brain, '_facts') else 0
            latest = 0
            if hasattr(self.brain, '_facts') and self.brain._facts:
                try:
                    latest = max(
                        (f.created_at for f in self.brain._facts[-50:]
                         if hasattr(f, 'created_at')),
                        default=0,
                    )
                except Exception:
                    pass
            raw = f"e{exp_count}f{fact_count}t{int(latest)}"
            return hashlib.md5(raw.encode()).hexdigest()[:12]
        except Exception:
            return str(time.time())

    @property
    def stats(self) -> dict:
        """获取构建统计"""
        return {
            "build_count": self._build_count,
            "layer1_size": len(self._layer1),
            "layer2_size": len(self._layer2),
            "layer3_size": len(self._layer3),
            "full_size": len(self._cached_full),
            "cached_step": self._cached_step,
            "current_phase": self._layer3_phase,
            "has_layer2": bool(self._layer2),
        }


# ============================================================
# 便捷函数（向后兼容）
# ============================================================
def build_dynamic_prompt(brain=None) -> str:
    """兼容旧 API: 构建动态 System Prompt。
    新代码应使用 PromptBuilder.build()。"""
    return MemoryLayerBuilder.build(brain=brain)


__all__ = [
    "PromptBuilder",
    "MemoryLayerBuilder",
    "PhaseGuidanceBuilder",
    "LAYER1_BASE_IDENTITY",
    "build_dynamic_prompt",
]
