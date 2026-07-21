"""睡眠学习 — 离线时将经验蒸馏为感知叶参数知识

参考 DOCX 文档：第 6.2 节
类似人类睡眠时海马体→皮层的记忆巩固。
后台线程周期性触发，仅当积累足够新经验时。
"""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

import torch
import torch.nn.functional as F

from kernel.leaves.visual_leaf import VisualLeaf
from kernel.leaves.auditory_leaf import AuditoryLeaf
from kernel.training.train_loop import TrainingEngine

logger = logging.getLogger("kernel.sleep")

# 睡眠调度默认配置
_DEFAULT_SCHEDULE = {
    "min_interval_seconds": 3600,        # 最少间隔 1 小时
    "min_experiences": 50,               # 最少经验数
    "max_duration_seconds": 600,         # 最长睡眠 10 分钟
    "check_interval_seconds": 120,       # 每 2 分钟检查一次
}

_EPISODES_DIR = Path("brain_data/episodes")
_FACTS_DIR = Path("brain_data/facts")
_SLEEP_META_FILE = Path("brain_data/sleep_meta.json")


class SleepLearning:
    """睡眠学习引擎 — 离线记忆巩固

    在后台周期性运行，将积累的 episodic 记忆蒸馏到感知叶参数中。
    使用进化质量控制防止退化。
    """

    def __init__(self, training_engine: TrainingEngine):
        self.training_engine = training_engine
        self.is_sleeping = False
        self.schedule = dict(_DEFAULT_SCHEDULE)
        self._last_sleep_time = 0
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._load_meta()

    def _load_meta(self):
        """加载睡眠学习元数据"""
        if _SLEEP_META_FILE.exists():
            try:
                meta = json.loads(_SLEEP_META_FILE.read_text("utf-8"))
                self._last_sleep_time = meta.get("last_sleep_time", 0)
                logger.info(f"睡眠学习: 上次睡眠 {self._last_sleep_time}")
            except Exception:
                pass

    def _save_meta(self):
        """保存睡眠学习元数据"""
        _SLEEP_META_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SLEEP_META_FILE.write_text(json.dumps(
            {"last_sleep_time": self._last_sleep_time}, ensure_ascii=False
        ), encoding="utf-8")

    def should_sleep(self) -> bool:
        """判断是否应该进入睡眠学习"""
        if self.is_sleeping:
            return False
        elapsed = time.time() - self._last_sleep_time
        if elapsed < self.schedule["min_interval_seconds"]:
            return False
        # 检查是否有新经验
        unconsolidated = self._count_unconsolidated()
        return unconsolidated >= self.schedule["min_experiences"]

    def _count_unconsolidated(self) -> int:
        """统计未巩固的 episode 数量"""
        if not _EPISODES_DIR.exists():
            return 0
        count = 0
        for f in sorted(_EPISODES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
            try:
                data = json.loads(f.read_text("utf-8"))
                # 检查是否包含视觉/音频数据
                timeline = data.get("timeline", [])
                for entry in timeline:
                    tool = entry.get("tool", "")
                    if tool in ("describe_screen", "camera_snapshot", "screenshot"):
                        count += 1
                        break
            except Exception:
                pass
        return count

    def _collect_training_data(self) -> dict:
        """从近期经验中收集训练数据

        Returns:
            {"visual": [{"image": tensor, "text_embed": tensor, "difficulty": float}, ...],
             "auditory": [...],
             "meta": {"total": int, "visual_samples": int, "auditory_samples": int}}
        """
        visual_data = []
        meta = {"total": 0, "visual_samples": 0, "auditory_samples": 0}

        if not _EPISODES_DIR.exists():
            return {"visual": [], "auditory": [], "meta": meta}

        # 获取最近 100 个未巩固的 episode
        episodes = sorted(_EPISODES_DIR.glob("*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True)[:100]

        for ep_path in episodes:
            try:
                data = json.loads(ep_path.read_text("utf-8"))
                timeline = data.get("timeline", [])
                user_input = data.get("user_input", "")

                for entry in timeline:
                    tool = entry.get("tool", "")
                    result = entry.get("result", "")

                    # 截图/拍照 → 视觉训练数据
                    if tool in ("describe_screen", "screenshot", "camera_snapshot") and result == "success":
                        meta["visual_samples"] += 1
                        # 尽量收集，后续由训练引擎处理
                        visual_data.append({
                            "has_image": True,
                            "description": user_input or "视觉场景",
                            "difficulty": 0.3,
                        })

            except Exception:
                continue

        meta["total"] = len(episodes)
        return {"visual": visual_data, "auditory": [], "meta": meta}

    def enter_sleep(self) -> dict:
        """进入睡眠状态：执行记忆巩固

        Returns:
            dict: 梦境日志（学习进度报告）
        """
        self.is_sleeping = True
        start_time = time.time()
        log = {"started": start_time, "phases": [], "errors": [], "samples_processed": 0}

        try:
            # 1. 收集待巩固的经验
            data = self._collect_training_data()
            log["samples_processed"] = data["meta"]["total"]
            log["phases"].append({"name": "collect", "samples": data["meta"]})

            # 2. 如果有视觉数据，添加到训练引擎
            vis_count = len(data.get("visual", []))
            if vis_count > 0 and "visual" in self.training_engine._leaves:
                for sample in data["visual"]:
                    self.training_engine.add_training_sample(
                        "visual", sample
                    )
                log["phases"].append({
                    "name": "visual_replay",
                    "samples": vis_count,
                })

            # 3. 更新记忆元数据（标记已巩固）
            self._mark_consolidated()

            # 4. 睡眠时长
            duration = time.time() - start_time
            log["duration_seconds"] = round(duration, 1)

        except Exception as e:
            logger.warning(f"睡眠学习异常: {e}")
            log["errors"].append(str(e))
        finally:
            self.is_sleeping = False
            self._last_sleep_time = time.time()
            self._save_meta()

        logger.info(f"睡眠学习完成: {log.get('samples_processed', 0)} 样本, "
                    f"{log.get('duration_seconds', 0)}s")
        return log

    def _mark_consolidated(self):
        """标记最近的 episode 为已巩固（写个标记文件）"""
        marker = _EPISODES_DIR / ".consolidated"
        marker.write_text(str(time.time()), encoding="utf-8")

    def _background_loop(self):
        """后台监控循环"""
        logger.info("睡眠学习: 后台监控启动")
        while self._running:
            time.sleep(self.schedule["check_interval_seconds"])
            try:
                if self.should_sleep():
                    logger.info("睡眠学习: 条件满足，进入睡眠")
                    result = self.enter_sleep()
                    if result.get("errors"):
                        logger.warning(f"睡眠学习: 部分失败 {result['errors']}")
            except Exception as e:
                logger.debug(f"睡眠学习: 检查异常 {e}")

    def start(self):
        """启动睡眠学习监控"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._background_loop, daemon=True)
        self._thread.start()
        logger.info("睡眠学习: 已启动")

    def stop(self):
        """停止监控"""
        self._running = False

    def status(self) -> dict:
        """睡眠学习状态"""
        elapsed = time.time() - self._last_sleep_time
        unconsolidated = self._count_unconsolidated()
        ready = self.should_sleep()
        return {
            "is_sleeping": self.is_sleeping,
            "last_sleep": f"{elapsed / 3600:.1f}h ago" if self._last_sleep_time else "never",
            "unconsolidated_episodes": unconsolidated,
            "ready_to_sleep": ready,
            "monitor_running": self._running,
        }
