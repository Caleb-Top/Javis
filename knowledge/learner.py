"""自学习引擎 — 每次对话后自我进化"""

import json, time, logging, traceback
from pathlib import Path
from knowledge.brain import Brain

logger = logging.getLogger("learner")


class Learner:
    """自学习引擎 — 从每次对话中提取知识并自我完善"""

    def __init__(self):
        self.brain = Brain()

    def learn_from_conversation(self, user_input: str, reply: str, actions: list[dict]):
        """从一次完整对话中学习"""
        # 1. 提取用户偏好
        prefs = self._extract_preferences(user_input)
        for pref in prefs:
            self.brain.learn_fact(pref, category="user_pref", source="conversation")

        # 2. 记录执行结果
        for action in actions:
            result = action.get("result", "unknown")
            if result == "failure":
                self.brain.record_experience(
                    intent=user_input,
                    action=action.get("tool", ""),
                    result="failure",
                    error=action.get("error", ""),
                    lesson=f"{action.get('tool','')} 失败: {action.get('error','')}。下次尝试其他方法。"
                )

        # 3. 提取技术知识点
        knowledge = self._extract_knowledge(user_input, reply)
        for k in knowledge:
            self.brain.learn_fact(k, category="technical", source="conversation")

    def learn_from_error(self, error: str, context: dict):
        """从错误中学习"""
        lesson = f"[错误教训] {error}"
        if "参数" in error:
            lesson += " → 需要检查参数类型和格式"
        elif "权限" in error:
            lesson += " → 需要提升权限或换方式"
        elif "模块" in error:
            lesson += " → 需要先安装依赖或换内置方案"
        self.brain.learn_fact(lesson, category="error_pattern", source="self_reflection")

    def get_cross_platform_hint(self, platform: str = "windows") -> str:
        """获取跨平台适配提示"""
        hints = {
            "windows": "Win32 API (user32/kernel32), PowerShell, COM",
            "linux": "X11/Wayland, dbus, /proc, shell commands",
            "darwin": "Quartz/CoreGraphics, osascript, defaults",
        }
        return hints.get(platform, hints["windows"])

    def _extract_preferences(self, text: str) -> list[str]:
        """提取用户偏好"""
        prefs = []
        indicators = ["我喜欢", "我习惯", "帮我", "不要", "请用", "我通常"]
        for ind in indicators:
            if ind in text:
                idx = text.find(ind)
                prefs.append(f"用户偏好: {text[idx:idx+60]}")
        return prefs

    def _extract_knowledge(self, user_input: str, reply: str) -> list[str]:
        """提取知识点"""
        knowledge = []
        # 记录用户提到的技术概念
        tech_keywords = ["API", "Python", "Windows", "Linux", "命令", "代码", "文件", "网络", "进程", "服务"]
        for kw in tech_keywords:
            if kw in user_input or kw in reply:
                knowledge.append(f"用户涉及技术概念: {kw}")
        return knowledge
