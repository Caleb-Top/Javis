"""InfoNCE 对比损失 — 视觉/音频-文本对齐的核心训练方法

参考 DOCX 文档：第 5 节
对称 InfoNCE 损失，同时计算 视觉→文本 和 文本→视觉 两个方向的交叉熵。
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class InfoNCELoss(nn.Module):
    """对称 InfoNCE 对比损失

    用于将感知叶（视觉/听觉）的输出嵌入与文本嵌入对齐。
    """

    def __init__(self, temperature: float = 0.07):
        super().__init__()
        self.temperature = nn.Parameter(torch.tensor(temperature))

    def forward(self, anchor_embeds: torch.Tensor,
                positive_embeds: torch.Tensor) -> torch.Tensor:
        """计算对称 InfoNCE 损失

        Args:
            anchor_embeds:   [B, D] 锚点嵌入（如视觉输出）
            positive_embeds: [B, D] 正样本嵌入（如对应文本）

        Returns:
            loss: 标量
        """
        B = anchor_embeds.size(0)
        # L2 归一化
        a = F.normalize(anchor_embeds, dim=-1)
        p = F.normalize(positive_embeds, dim=-1)

        # 相似度矩阵 [B, B]
        logits = torch.matmul(a, p.T) / self.temperature.abs().clamp(min=0.01)
        labels = torch.arange(B, device=a.device)

        # 对称损失
        loss_a2p = F.cross_entropy(logits, labels)   # 锚点→正样本
        loss_p2a = F.cross_entropy(logits.T, labels)  # 正样本→锚点
        return (loss_a2p + loss_p2a) / 2


class TripletLoss(nn.Module):
    """三元组损失 — 辅助对比学习"""

    def __init__(self, margin: float = 0.5):
        super().__init__()
        self.margin = margin

    def forward(self, anchor: torch.Tensor, positive: torch.Tensor,
                negative: torch.Tensor) -> torch.Tensor:
        """Triplet Margin Loss"""
        return F.triplet_margin_loss(anchor, positive, negative, margin=self.margin)
