# -*- coding: utf-8 -*-
"""记忆整合器 — 后台自动运行的模式提取与知识固化

工作流:
  每 N 分钟扫描新 episodes
  → 提取语义规则 (group by 指纹, calc failure rate)
  → 固化为程序记忆 (high-frequency success chains)
  → 将高置信度规则同步到 Brain facts
"""
import logging, time, threading
from typing import Optional

logger = logging.getLogger("memory.consolidator")

INTERVAL_SECONDS = 300  # 每 5 分钟跑一次


class Consolidator:
    """后台记忆整合器"""

    def __init__(self, brain=None):
        self._brain = brain
        self._last_consolidation = 0
        self._running = False

    def set_brain(self, brain):
        self._brain = brain

    def start(self):
        if self._running:
            return
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True, name="memory-consolidator")
        t.start()
        logger.info("记忆整合器已启动 (每5分钟)")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self.consolidate()
            except Exception as e:
                logger.debug(f"整合异常: {e}")
            time.sleep(INTERVAL_SECONDS)

    def consolidate(self) -> dict:
        """执行一次完整的记忆整合"""
        result = {"semantic": 0, "procedural": 0, "brain_facts": 0}

        # 1. 语义记忆: 从 episodes 提取模式
        try:
            from memory.semantic import consolidate as semantic_consolidate
            n = semantic_consolidate(brain=self._brain, force=False)
            result["semantic"] = n
        except Exception as e:
            logger.warning(f"语义整合失败: {e}")

        # 2. 程序记忆: 从 episodes 固化操作链
        try:
            from memory.procedural import consolidate_from_episodes
            n = consolidate_from_episodes()
            result["procedural"] = n
        except Exception as e:
            logger.warning(f"程序记忆固化失败: {e}")

        # 3. 将高置信度语义规则同步到 Brain facts
        if self._brain:
            try:
                from memory.semantic import load_all_rules
                for rule in load_all_rules():
                    if rule.get("confidence", 0) >= 0.7 and rule.get("status", "active") == "active":
                        cid = f"semantic.{rule.get('fingerprint', {}).get('domain', 'general')}"
                        existing = [f for f in self._brain._facts if cid in f.category and rule.get("conclusion", "")[:30] in f.content]
                        if not existing:
                            self._brain.learn_fact(
                                f"[自动规则] {rule['conclusion'][:120]} 置信度:{rule.get('confidence', 0):.0%}",
                                category=cid, source="self_reflection",
                                priority=max(2, int(rule.get('confidence', 0.5) * 5)),
                            )
                            result["brain_facts"] += 1
            except Exception as e:
                logger.warning(f"同步 brain 失败: {e}")

        if any(result.values()):
            logger.info(f"记忆整合完成: 语义{result['semantic']}条, 程序{result['procedural']}条, 脑事实{result['brain_facts']}条")
        return result


_consolidator = Consolidator()


def get_consolidator(brain=None) -> Consolidator:
    global _consolidator
    if brain:
        _consolidator.set_brain(brain)
    return _consolidator
