"""后台训练引擎 — 在系统空闲时训练感知叶

参考 DOCX 文档：第 5、8 节
训练循环在后台线程中运行，不干扰主系统的对话/工具执行。
"""

import logging
import threading
import time
from typing import Optional

import torch
import torch.optim as optim

from kernel.leaves.base_leaf import PerceptualLeaf
from kernel.leaves.visual_leaf import VisualLeaf
from kernel.leaves.auditory_leaf import AuditoryLeaf
from kernel.training.info_nce_loss import InfoNCELoss

logger = logging.getLogger("kernel.training")


class TrainingEngine:
    """感知叶训练引擎

    在后台线程中持续收集训练数据并更新感知叶参数。
    使用梯度累积适配 8GB VRAM。
    """

    def __init__(self, learning_rate: float = 1e-4):
        self.learning_rate = learning_rate
        self._leaves: dict[str, PerceptualLeaf] = {}
        self._optimizers: dict[str, optim.Optimizer] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._train_buffer: dict[str, list] = {}  # leaf_id → [batch, ...]
        self._max_buffer = 2000                    # 每个叶最多缓存 2000 条
        self._gradient_accumulation_steps = 4      # 梯度累积步数
        self._batch_size = 8                       # 每批样本数

    def register_leaf(self, leaf: PerceptualLeaf):
        """注册一个感知叶到训练引擎"""
        leaf_id = leaf.leaf_id
        with self._lock:
            self._leaves[leaf_id] = leaf
            self._optimizers[leaf_id] = optim.AdamW(
                leaf.parameters(), lr=self.learning_rate, weight_decay=1e-5
            )
            self._train_buffer[leaf_id] = []
        logger.info(f"训练引擎: 注册感知叶 {leaf_id}")

    def add_training_sample(self, leaf_id: str, data: dict):
        """添加一条训练样本到缓冲区"""
        with self._lock:
            if leaf_id in self._train_buffer:
                buf = self._train_buffer[leaf_id]
                buf.append(data)
                if len(buf) > self._max_buffer:
                    buf.pop(0)

    def _train_step(self, leaf_id: str, batch: dict) -> dict:
        """执行一步训练"""
        leaf = self._leaves[leaf_id]
        optimizer = self._optimizers[leaf_id]
        loss_fn = InfoNCELoss()

        # 前向传播 & 损失
        if leaf_id == "visual":
            # 跳过无图片的旧数据（只有 text_embed 没有 image）
            if "image" not in batch or batch.get("from_old_episode"):
                return {"loss": 0, "alignment": 0, "skipped": True}
            image = batch["image"]
            text_embed = batch["text_embed"]
            output = leaf(image)["embedding"]
            loss = loss_fn(output, text_embed)
        elif leaf_id == "auditory":
            # 跳过无音频的旧数据
            if "audio" not in batch:
                return {"loss": 0, "alignment": 0, "skipped": True}
            audio = batch["audio"]
            text_embed = batch["text_embed"]
            output = leaf(audio)["embedding"]
            loss = loss_fn(output, text_embed)
        else:
            return {"loss": 0, "error": f"unknown leaf: {leaf_id}"}

        # 反向传播（梯度累积）
        loss = loss / self._gradient_accumulation_steps
        loss.backward()

        # 评估对齐
        with torch.no_grad():
            alignment = torch.cosine_similarity(
                output.mean(dim=0, keepdim=True),
                text_embed.mean(dim=0, keepdim=True),
            ).item()

        return {"loss": loss.item() * self._gradient_accumulation_steps,
                "alignment": alignment}

    def _training_loop(self):
        """后台训练主循环"""
        logger.info("训练引擎: 后台循环启动")
        accumulation_counter = 0

        while self._running:
            time.sleep(1)

            with self._lock:
                for leaf_id in list(self._leaves.keys()):
                    buf = self._train_buffer.get(leaf_id, [])
                    if len(buf) < self._batch_size:
                        continue

                    # 取一批样本
                    batch_data = buf[:self._batch_size]
                    buf[:self._batch_size] = []

                    # 组装 batch
                    batch = {}
                    for k in batch_data[0].keys():
                        tensors = [b[k] for b in batch_data if k in b]
                        if tensors and isinstance(tensors[0], torch.Tensor):
                            batch[k] = torch.cat(tensors, dim=0).to(
                                next(self._leaves[leaf_id].parameters()).device
                            )

                    if not batch:
                        continue

                    # 训练一步
                    try:
                        result = self._train_step(leaf_id, batch)

                        # 跳过旧数据（无 image/audio）
                        if result.get("skipped"):
                            continue

                        accumulation_counter += 1

                        # 梯度累积达到步数后更新参数
                        if accumulation_counter >= self._gradient_accumulation_steps:
                            optimizer = self._optimizers[leaf_id]
                            torch.nn.utils.clip_grad_norm_(
                                self._leaves[leaf_id].parameters(), max_norm=1.0
                            )
                            optimizer.step()
                            optimizer.zero_grad()
                            accumulation_counter = 0

                        leaf = self._leaves[leaf_id]

                        # 更新对齐质量
                        leaf.alignment_quality = 0.9 * leaf.alignment_quality + 0.1 * result["alignment"]

                        # 检查生长触发
                        trigger = leaf.grow_trigger_check()
                        if trigger == "child":
                            leaf._expand_capacity()
                            leaf.growth_stage = "child"
                            # 重新初始化优化器
                            self._optimizers[leaf_id] = optim.AdamW(
                                leaf.parameters(), lr=self.learning_rate * 0.5,
                                weight_decay=1e-5,
                            )
                            logger.info(f"训练引擎: {leaf_id} 升级到 child 阶段")
                        elif trigger == "adult":
                            leaf.growth_stage = "adult"
                            logger.info(f"训练引擎: {leaf_id} 升级到 adult 阶段")

                    except Exception as e:
                        logger.warning(f"训练引擎: {leaf_id} 训练异常: {e}")
                        accumulation_counter = 0

        logger.info("训练引擎: 后台循环结束")

    def start(self):
        """启动训练引擎（后台线程）"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._training_loop, daemon=True)
        self._thread.start()
        logger.info("训练引擎: 已启动")

    def stop(self):
        """停止训练引擎"""
        self._running = False
        logger.info("训练引擎: 已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    def status(self) -> dict:
        """训练引擎状态"""
        result = {"running": self._running}
        for leaf_id in self._leaves:
            leaf = self._leaves[leaf_id]
            result[leaf_id] = {
                "growth_stage": leaf.growth_stage,
                "alignment_quality": round(leaf.alignment_quality, 3),
                "buffer_size": len(self._train_buffer.get(leaf_id, [])),
            }
        return result
