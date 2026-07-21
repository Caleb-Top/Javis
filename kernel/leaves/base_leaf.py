"""感知叶基类 — 所有感知模态的通用接口

参考 DOCX 文档：第 2.3 节
感知叶从完全随机初始化开始，经历三个阶段：
  infant  (婴儿期) — 对比学习，学习低级特征
  child   (儿童期) — 课程学习，扩展网络容量
  adult   (成年期) — 精确对齐到文本空间
"""

import time
import logging
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger("kernel.leaf")


class PerceptualLeaf(nn.Module):
    """感知叶基类

    所有感知模态（视觉、听觉、触觉）的通用接口。
    出生时完全随机初始化，没有任何先验知识。
    """

    def __init__(self,
                 input_shape: tuple,
                 backbone_dim: int = 1536,
                 growth_capacity: int = 50_000_000,
                 leaf_id: str = "leaf"):
        super().__init__()
        self.leaf_id = leaf_id
        self.input_shape = input_shape
        self.backbone_dim = backbone_dim
        self.growth_capacity = growth_capacity

        # === 阶段一：原始编码器（婴儿期）===
        # 从随机开始，学习低级特征
        # 子类必须实现 _init_sensory_encoder
        self.sensory_encoder = self._init_sensory_encoder()
        encoder_out = self._encoder_output_dim()

        # === 阶段二：语义压缩器（儿童期）===
        # 将低级特征压缩为语义向量
        self.semantic_compressor = nn.Sequential(
            nn.Linear(encoder_out, backbone_dim * 2),
            nn.LayerNorm(backbone_dim * 2),
            nn.GELU(),
            nn.Linear(backbone_dim * 2, backbone_dim),
        )

        # === 阶段三：对齐投影器（成年期）===
        # 精确对齐到主干文本空间（成年期才激活）
        self.alignment_projector = nn.Linear(backbone_dim, backbone_dim)

        # 生长状态
        self.growth_stage = "infant"  # infant → child → adult
        self.experience_buffer = []   # 原始经验缓存
        self.alignment_quality = 0.0  # 与文本空间的对齐质量
        self._total_grown = 0         # 累计生长步数
        self._checkpoint = None       # 回滚检查点

    def _init_sensory_encoder(self) -> nn.Module:
        """初始化感官编码器 — 完全随机，无预训练"""
        raise NotImplementedError("子类必须实现 _init_sensory_encoder")

    def _encoder_output_dim(self) -> int:
        """返回编码器输出维度"""
        raise NotImplementedError("子类必须实现 _encoder_output_dim")

    def forward(self, raw_input):
        """前向传播：原始感官数据 → 文本对齐嵌入

        Args:
            raw_input: 原始输入张量

        Returns:
            dict: {'embedding': [B, backbone_dim], 'features': [...],
                   'growth_stage': str, 'alignment_quality': float}
        """
        # 阶段一：原始特征提取
        features = self.sensory_encoder(raw_input)

        # 阶段二：语义压缩
        semantic = self.semantic_compressor(features)

        # 阶段三：精确对齐（仅在成年期激活）
        if self.growth_stage == "adult":
            aligned = self.alignment_projector(semantic)
        else:
            aligned = semantic

        return {
            "embedding": aligned,
            "features": features,
            "growth_stage": self.growth_stage,
            "alignment_quality": self.alignment_quality,
        }

    def grow(self, training_signal: dict) -> dict:
        """执行一步生长训练

        Args:
            training_signal: {'input': tensor, 'target_embedding': tensor, ...}
        """
        if self.growth_stage == "infant":
            return self._infant_learning(training_signal)
        elif self.growth_stage == "child":
            return self._child_learning(training_signal)
        else:
            return self._adult_refinement(training_signal)

    def _infant_learning(self, signal: dict) -> dict:
        """婴儿期学习：对比学习对齐到文本空间"""
        raise NotImplementedError("子类必须实现 _infant_learning")

    def _child_learning(self, signal: dict) -> dict:
        """儿童期学习：课程学习，从简单到复杂"""
        raise NotImplementedError("子类必须实现 _child_learning")

    def _adult_refinement(self, signal: dict) -> dict:
        """成年期微调：精确对齐"""
        raise NotImplementedError("子类必须实现 _adult_refinement")

    def save_checkpoint(self):
        """保存当前参数作为回滚点"""
        self._checkpoint = {k: v.clone() for k, v in self.state_dict().items()}

    def rollback(self):
        """回滚到上一个检查点"""
        if self._checkpoint:
            self.load_state_dict(self._checkpoint)
            logger.info(f"Leaf {self.leaf_id} rolled back to checkpoint")

    def _expand_capacity(self):
        """扩展网络容量（儿童期专用）"""
        pass  # 子类按需实现

    def _evaluate_alignment(self, output_embed: torch.Tensor,
                            target_embed: torch.Tensor) -> float:
        """评估嵌入对齐质量（余弦相似度）"""
        cos_sim = F.cosine_similarity(
            output_embed.mean(dim=0, keepdim=True),
            target_embed.mean(dim=0, keepdim=True),
        ).item()
        self.alignment_quality = 0.9 * self.alignment_quality + 0.1 * cos_sim
        return cos_sim

    def grow_trigger_check(self) -> Optional[str]:
        """检查是否需要升级生长阶段

        Returns:
            None: 不升级
            'child': 可以升级到儿童期
            'adult': 可以升级到成年期
        """
        if self.growth_stage == "infant" and self.alignment_quality > 0.7:
            if len(self.experience_buffer) > 100:
                return "child"
        elif self.growth_stage == "child" and self.alignment_quality > 0.9:
            if len(self.experience_buffer) > 1000:
                return "adult"
        return None
