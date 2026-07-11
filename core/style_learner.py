"""风格学习器 — 从对话中持续学习用户的说话方式

三层架构:
  ① 感知层: 每次对话后提取用户语言特征
  ② 记忆层: 高优先级存入大脑，成为长期风格知识
  ③ 表达层: 动态组装风格指导注入 system prompt

自进化机制:
  - 每次用户对话后，分析并提炼风格信号
  - 将风格信号与已有知识对比，强化或修正
  - 如果用户连续 3+ 次使用同一风格特征，提升其优先级
"""
import logging, time, re
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger("style_learner")

_brains = {}   # 连接的大脑实例


# ── 风格特征定义 ──

STYLE_FEATURES = {
    "short_sentence": {
        "desc": "倾向短句, 一句话不超过20字",
        "signals": ["好", "行", "嗯", "对", "可以", "看看", "试试"],
    },
    "natural_tone": {
        "desc": "自然口语化, 像跟朋友说话",
        "signals": ["咱", "我们", "对了", "不过", "其实", "感觉"],
    },
    "minimal_emoji": {
        "desc": "少用或不用emoji",
        "signals": [],
    },
    "emoji_user": {
        "desc": "喜欢用emoji表达",
        "signals": ["✅", "❌", "📸", "💻", "⭐"],
    },
    "data_first": {
        "desc": "先抛数据再说话",
        "signals": ["CPU:", "内存:", "磁盘:", "结果是", "输出为"],
    },
    "data_wrapped": {
        "desc": "先结论再数据",
        "signals": ["还行", "挺", "没什么", "没问题", "正常"],
    },
    "precise": {
        "desc": "精确表达, 不模糊",
        "signals": ["%", "秒", "个", "次", "行"],
    },
    "minimal_response": {
        "desc": "回复简短, 不罗嗦",
        "signals": ["ok", "好", "继续", "嗯"],
    },
}

# ── 风格信号提取 ──

def extract_features(text: str) -> dict:
    """从一段文本中提取风格特征"""
    features = {}
    if not text:
        return features

    # 句式长度
    sentences = [s.strip() for s in re.split(r'[。！？\n]', text) if s.strip()]
    avg_len = sum(len(s) for s in sentences) / max(len(sentences), 1)
    features["avg_sentence_length"] = round(avg_len, 1)
    features["short_avg"] = avg_len < 20

    # 语气词频率
    tone_words = ["了", "嘛", "吧", "呢", "哦", "嗯", "啊", "哈", "呀"]
    tone_count = sum(text.count(w) for w in tone_words)
    features["tone_word_count"] = tone_count

    # emoji / 特殊符号
    emoji_pattern = re.compile(r'[\U0001F300-\U0001FFFF✅❌📸💻⭐🔧📁🖥️🌐🐙🏗️⚙️🧩📝🔗]')
    emojis = emoji_pattern.findall(text)
    features["emoji_count"] = len(emojis)

    # 特征匹配
    matched = []
    for feat_name, feat_def in STYLE_FEATURES.items():
        for signal in feat_def["signals"]:
            if signal in text:
                matched.append(feat_name)
                break
    features["matched_features"] = matched[:5]
    features["signal_count"] = len(matched)

    return features


def compare_style(user_msg: str, assistant_msg: str) -> List[str]:
    """比较用户和助手的风格差异, 返回改进建议"""
    user_feat = extract_features(user_msg)
    asst_feat = extract_features(assistant_msg)

    suggestions = []

    # 如果用户短句多而助手长句多
    if user_feat.get("short_avg") and not asst_feat.get("short_avg"):
        suggestions.append("用户倾向短句，回复尽量精简")

    # 如果用户用 emoji 而助手没用
    ue = user_feat.get("emoji_count", 0)
    ae = asst_feat.get("emoji_count", 0)
    if ue > 2 and ae == 0:
        suggestions.append("用户用了 emoji，回复可以适当配合")
    if ue == 0 and ae > 2:
        suggestions.append("用户不怎么用 emoji，回复也少用")

    # 语气词匹配
    ut = user_feat.get("tone_word_count", 0)
    at = asst_feat.get("tone_word_count", 0)
    if ut > 3 and at == 0:
        suggestions.append("用户用语气词较多，可以跟近")

    return suggestions


# ── 核心: 风格学习 ──

class StyleLearner:
    """风格学习器 — 持续进化"""

    def __init__(self, brain=None):
        self._brain = brain
        self._history = []       # [(user_msg, assistant_msg), ...]
        self._max_history = 100
        self._style_profile = {}  # 当前风格画像
        self._convergence_count = 0  # 连续一致计数

    def set_brain(self, brain):
        self._brain = brain

    def learn(self, user_msg: str, assistant_msg: str) -> List[str]:
        """从一轮对话中学习, 返回改进建议"""
        if not user_msg:
            return []

        self._history.append((user_msg, assistant_msg))
        if len(self._history) > self._max_history:
            self._history.pop(0)

        suggestions = []
        user_feat = extract_features(user_msg)

        # ── 1. 更新风格画像 ──
        for key, val in user_feat.items():
            if key not in self._style_profile:
                self._style_profile[key] = val
            elif isinstance(val, (int, float)) and isinstance(self._style_profile[key], (int, float)):
                # 滑动平均
                self._style_profile[key] = self._style_profile[key] * 0.7 + val * 0.3
            elif isinstance(val, list):
                # 合并特征标签
                existing = set(self._style_profile.get(key, []))
                existing.update(val)
                self._style_profile[key] = list(existing)

        # ── 2. 对比助手回复 ──
        if assistant_msg:
            s = compare_style(user_msg, assistant_msg)
            suggestions.extend(s)

        # ── 3. 累积一致信号, 判断收敛 ──
        if not suggestions:
            self._convergence_count += 1
        else:
            self._convergence_count = max(0, self._convergence_count - 1)

        # ── 4. 持久化到大脑 ──
        if self._brain and user_msg:
            self._persist_style(user_feat, suggestions)

        return suggestions

    def _persist_style(self, features: dict, suggestions: List[str]):
        """将风格特征写入大脑"""
        try:
            # 写入用户风格画像 (高优先级)
            profile_summary = self.get_profile_summary()
            existing_id = hashlib.md5(f"style_profile".encode()).hexdigest()[:12]

            self._brain.learn_fact(
                f"用户说话风格: {profile_summary[:200]}",
                category="style.profile",
                source="style_learner",
                priority=4,
            )

            # 写入具体改进建议（如果连续一致 >3 次, 提升到 priority=5）
            priority = 5 if self._convergence_count >= 3 else 4
            if suggestions:
                for s in suggestions[:3]:
                    self._brain.learn_fact(
                        f"风格适配: {s}",
                        category="style.suggestion",
                        source="style_learner",
                        priority=priority,
                    )
        except Exception:
            pass

    def get_profile_summary(self) -> str:
        """生成当前风格画像摘要"""
        p = self._style_profile
        parts = []
        avg = round(p.get('avg_sentence_length', 0))
        if p.get("short_avg"):
            parts.append(f"短句({avg}字)")
        else:
            parts.append(f"中长句({avg}字)")
        tc = int(p.get("tone_word_count", 0))
        parts.append(f"语气词{tc}次/句")
        ec = p.get("emoji_count", 0)
        parts.append("用emoji" if ec > 0 else "不用emoji")
        feats = p.get("matched_features", [])
        if feats:
            parts.append("特征:" + ",".join(list(feats)[:3]))
        return " · ".join(parts)

    def get_style_guide(self) -> str:
        """生成给 system prompt 用的动态风格指导"""
        if not self._brain:
            return ""

        style_facts = [f for f in self._brain._facts
                       if f.category.startswith("style.") and f.priority >= 4]
        if not style_facts:
            return ""

        seen = set()
        unique = []
        for f in reversed(style_facts):
            key = f.content[:80]
            if key not in seen:
                seen.add(key)
                unique.append(f)
        unique.reverse()

        lines = ["", "## 风格提醒（从对话中学习）"]
        for f in unique[-5:]:
            tag = "!" if f.priority >= 5 else "-"
            lines.append(f"{tag} {f.content[:80]}")

        if self._convergence_count >= 3:
            lines.append(f"~ 风格已趋稳: {self.get_profile_summary()}")

        return "\n".join(lines)

    def get_stats(self) -> dict:
        return {
            "total_exchanges": len(self._history),
            "convergence": self._convergence_count,
            "profile": self.get_profile_summary(),
            "style_facts": len([f for f in (self._brain._facts if self._brain else [])
                               if f.category.startswith("style.")]),
        }


# ── 独立实例（全局单例） ──
_learner = StyleLearner()


def get_learner(brain=None) -> StyleLearner:
    global _learner
    if brain:
        _learner.set_brain(brain)
    return _learner


def learn_from_exchange(user_msg: str, assistant_msg: str = "") -> List[str]:
    """便捷接口: 学习一轮对话"""
    return get_learner().learn(user_msg, assistant_msg)


def style_guide_for_prompt() -> str:
    """便捷接口: 获取当前风格指导"""
    return get_learner().get_style_guide()


import hashlib  # 用于 _persist_style
