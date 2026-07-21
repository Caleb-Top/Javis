"""视觉感知叶 — 从随机卷积生长出原生视觉理解

参考 DOCX 文档：第 3.1 节
完全不使用预训练的 ViT/CNN，从随机卷积开始。
通过 InfoNCE 对比损失将视觉输出对齐到文本嵌入空间。

三阶段生长：
  infant  — 4 层随机卷积，学习边缘/纹理/形状特征
  child   — 扩展为 6 层，学习语义概念
  adult   — 加入对齐投影器，精确匹配文本嵌入
"""

import logging
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from .base_leaf import PerceptualLeaf

logger = logging.getLogger("kernel.leaf.visual")

# 常见长宽比下的最佳 224x224 缩放后保留有效信息
_VISUAL_INPUT_SHAPE = (3, 224, 224)


class VisualLeaf(PerceptualLeaf):
    """视觉感知叶

    从摄像头/截图原始像素生长出视觉理解能力。
    输出 1536 维嵌入，对齐到文本认知空间。
    """

    def __init__(self, backbone_dim: int = 1536):
        super().__init__(
            input_shape=_VISUAL_INPUT_SHAPE,
            backbone_dim=backbone_dim,
            growth_capacity=50_000_000,  # 5000 万参数上限
            leaf_id="visual",
        )
        # 温度参数（InfoNCE 对比损失用）
        self.temperature = nn.Parameter(torch.tensor(0.07))
        self._expansion_depth = 0

    def _init_sensory_encoder(self) -> nn.Module:
        """婴儿期视觉编码器：极简卷积，随机初始化

        类似新生儿模糊的视觉皮层——只能感知边缘和粗略形状。
        """
        return nn.Sequential(
            # 层1：边缘检测
            nn.Conv2d(3, 32, kernel_size=7, stride=2, padding=3),
            nn.BatchNorm2d(32),
            nn.GELU(),
            # 层2：纹理/模式
            nn.Conv2d(32, 64, kernel_size=5, stride=2, padding=2),
            nn.BatchNorm2d(64),
            nn.GELU(),
            # 层3：局部结构
            nn.Conv2d(64, 128, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm2d(128),
            nn.GELU(),
            # 层4：全局特征
            nn.Conv2d(128, 256, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm2d(256),
            nn.GELU(),
            # 全局池化
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),  # [B, 256]
        )

    def _encoder_output_dim(self) -> int:
        return 256

    # ── 图像预处理 ──

    @staticmethod
    def preprocess_image(image, target_size: int = 224):
        """将不同来源的图片统一为 [B, 3, 224, 224] 张量"""
        import numpy as np
        if isinstance(image, str):
            # 文件路径
            from PIL import Image
            pil = Image.open(image).convert("RGB")
            image = np.array(pil)
        elif isinstance(image, np.ndarray):
            pass  # 已经是 numpy
        elif isinstance(image, torch.Tensor):
            if image.dim() == 3:
                image = image.unsqueeze(0)
            if image.size(1) != 3:  # [B, H, W, C] → [B, C, H, W]
                image = image.permute(0, 3, 1, 2)
            return image.float() / 255.0

        # numpy → tensor
        if image.ndim == 3:
            image = image.transpose(2, 0, 1)  # HWC → CHW
            image = image[np.newaxis, ...]     # → BCHW
        elif image.ndim == 4:
            image = image.transpose(0, 3, 1, 2)

        tensor = torch.from_numpy(image).float() / 255.0
        # 缩放到 target_size
        if tensor.size(-1) != target_size:
            tensor = F.interpolate(tensor, size=(target_size, target_size),
                                   mode="bilinear", align_corners=False)
        return tensor

    # ── 前向传播与训练 ──

    def _info_nce_loss(self, visual_embeds: torch.Tensor,
                       text_embeds: torch.Tensor) -> torch.Tensor:
        """InfoNCE 对比损失：视觉-文本对齐的核心

        Args:
            visual_embeds: [B, D] 视觉嵌入
            text_embeds:   [B, D] 文本嵌入

        Returns:
            loss: 标量张量
        """
        B = visual_embeds.size(0)
        # 归一化
        v = F.normalize(visual_embeds, dim=-1)
        t = F.normalize(text_embeds, dim=-1)

        # 相似度矩阵
        logits = torch.matmul(v, t.T) / self.temperature.abs()  # [B, B]
        labels = torch.arange(B, device=v.device)

        # 对称 InfoNCE
        loss_v2t = F.cross_entropy(logits, labels)
        loss_t2v = F.cross_entropy(logits.T, labels)
        return (loss_v2t + loss_t2v) / 2

    def forward(self, raw_input):
        """前向传播

        Args:
            raw_input: [B, 3, H, W] 图像张量，或文件路径/numpy数组

        Returns:
            同 base PerceptualLeaf
        """
        if isinstance(raw_input, (str, list)):
            raw_input = self.preprocess_image(raw_input)
        elif not isinstance(raw_input, torch.Tensor):
            raw_input = self.preprocess_image(raw_input)
        # 保证在正确设备上
        if raw_input.device != next(self.parameters()).device:
            raw_input = raw_input.to(next(self.parameters()).device)
        return super().forward(raw_input)

    def _infant_learning(self, signal: dict) -> dict:
        """婴儿期学习：学习"看到什么"

        训练信号格式：{
            'image': [B, 3, 224, 224],
            'text_embed': [B, 1536],  # 对应文本的嵌入
        }
        目标：让视觉输出与对应文本概念在嵌入空间对齐
        """
        image = signal["image"]
        text_embed = signal.get("text_embed", signal.get("target_embedding"))

        # 前向传播
        visual_out = self(image)["embedding"]  # [B, 1536]

        # InfoNCE 对比损失
        loss = self._info_nce_loss(visual_out, text_embed)

        # 评估对齐质量
        alignment = F.cosine_similarity(
            visual_out.mean(dim=0, keepdim=True),
            text_embed.mean(dim=0, keepdim=True),
        ).item()
        self.alignment_quality = 0.9 * self.alignment_quality + 0.1 * alignment

        return {
            "loss": loss.item(),
            "alignment": alignment,
            "growth_stage": self.growth_stage,
        }

    def _child_learning(self, signal: dict) -> dict:
        """儿童期学习：课程学习（按难度递增）

        婴儿期对齐质量 > 0.7 后自动进入此阶段。
        在此阶段扩展网络容量，训练更复杂的语义特征。
        """
        image = signal["image"]
        text_embed = signal.get("text_embed", signal.get("target_embedding"))
        difficulty = signal.get("difficulty", 0.5)

        visual_out = self(image)["embedding"]
        loss = self._info_nce_loss(visual_out, text_embed)

        # 课程学习掩码：只训练难度低于阈值的样本
        threshold = min(0.5 + self._total_grown * 0.001, 1.0)
        if difficulty <= threshold:
            mask = signal.get("mask", torch.ones(len(image), dtype=torch.bool))
            if mask.any():
                loss = self._info_nce_loss(
                    visual_out[mask], text_embed[mask]
                )
            else:
                loss = loss * 0  # 跳过超出当前能力的样本

        alignment = F.cosine_similarity(
            visual_out.mean(dim=0, keepdim=True),
            text_embed.mean(dim=0, keepdim=True),
        ).item()
        self.alignment_quality = 0.9 * self.alignment_quality + 0.1 * alignment
        self._total_grown += 1

        return {
            "loss": loss.item() if isinstance(loss, torch.Tensor) else 0,
            "alignment": alignment,
            "difficulty_threshold": threshold,
            "growth_stage": self.growth_stage,
        }

    def _adult_refinement(self, signal: dict) -> dict:
        """成年期微调：预测学习，对齐投影器激活"""
        image = signal["image"]
        text_embed = signal.get("text_embed", signal.get("target_embedding"))

        visual_out = self(image)["embedding"]
        aligned = self.alignment_projector(visual_out)

        # 精确对齐损失（MSE + InfoNCE 混合）
        mse_loss = F.mse_loss(aligned, text_embed)
        nce_loss = self._info_nce_loss(aligned, text_embed)
        loss = mse_loss + nce_loss

        alignment = F.cosine_similarity(
            aligned.mean(dim=0, keepdim=True),
            text_embed.mean(dim=0, keepdim=True),
        ).item()
        self.alignment_quality = 0.95 * self.alignment_quality + 0.05 * alignment

        return {
            "loss": loss.item(),
            "alignment": alignment,
            "mse": mse_loss.item(),
            "growth_stage": self.growth_stage,
        }

    def _expand_capacity(self):
        """儿童期扩展：增加网络深度和宽度

        类似大脑突触增生期——从 4 层扩展到 6 层。
        """
        old_encoder = self.sensory_encoder
        # 保留前 3 层，替换后 2 层为更宽的结构
        children = list(old_encoder.children())
        new_encoder = nn.Sequential(
            # 保留婴儿期学到的前两层
            children[0],   # Conv2d(3, 32)
            children[1],   # BN
            children[2],   # GELU
            children[3],   # Conv2d(32, 64)
            children[4],   # BN
            children[5],   # GELU
            # 新的深层：64 → 128 → 256 → 512
            nn.Conv2d(64, 128, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm2d(128),
            nn.GELU(),
            nn.Conv2d(128, 256, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm2d(256),
            nn.GELU(),
            nn.Conv2d(256, 512, kernel_size=3, padding=1),
            nn.BatchNorm2d(512),
            nn.GELU(),
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
        )
        self.sensory_encoder = new_encoder

        # 更新语义压缩器
        self.semantic_compressor = nn.Sequential(
            nn.Linear(512, self.backbone_dim * 2),
            nn.LayerNorm(self.backbone_dim * 2),
            nn.GELU(),
            nn.Linear(self.backbone_dim * 2, self.backbone_dim),
        )
        self._expansion_depth += 1
        logger.info(f"VisualLeaf 扩展完成 (depth={self._expansion_depth})")
