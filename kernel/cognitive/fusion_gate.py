"""融合门控网络 — 动态决定哪些感知叶参与当前推理

参考 DOCX 文档：第 4.1 节
类似大脑注意力机制——"现在我在听，视觉可以弱一些"
"""

import logging
from typing import Dict

import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger("kernel.cognitive.fusion")


class FusionGatingNetwork(nn.Module):
    """融合突触：动态决定各感知叶的参与权重"""

    def __init__(self, backbone_dim: int = 1536, max_leaves: int = 16):
        super().__init__()
        self.backbone_dim = backbone_dim
        self.max_leaves = max_leaves

        # 跨模态注意力：不同叶之间的交互
        self.cross_modal_attention = nn.MultiheadAttention(
            embed_dim=backbone_dim,
            num_heads=8,
            batch_first=True,
        )

        # 动态权重生成器
        # 输入是 [text_query(1D) + leaf_mean(1D) + attended(1D)] = 3D
        self.weight_generator = nn.Sequential(
            nn.Linear(backbone_dim * 3, 512),
            nn.LayerNorm(512),
            nn.GELU(),
            nn.Linear(512, max_leaves),
            nn.Softmax(dim=-1),
        )

        # 存在检测器（每个叶一个，懒加载）
        self.presence_detectors = nn.ModuleDict()
        self._leaf_ids_registered = set()

    def register_leaf(self, leaf_id: str):
        """注册新感知叶"""
        if leaf_id not in self._leaf_ids_registered:
            self.presence_detectors[leaf_id] = nn.Sequential(
                nn.Linear(self.backbone_dim, 64),
                nn.GELU(),
                nn.Linear(64, 1),
                nn.Sigmoid(),
            )
            self._leaf_ids_registered.add(leaf_id)
            logger.info(f"FusionGate: 注册感知叶 {leaf_id}")

    def forward(self, backbone_output: torch.Tensor,
                leaf_outputs: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """计算各感知叶的融合权重

        Args:
            backbone_output: [B, seq_len, D] 主干输出
            leaf_outputs: {leaf_id: [B, D]} 各叶的嵌入

        Returns:
            {leaf_id: [B]} 各叶的融合权重
        """
        batch_size = backbone_output.size(0)

        if not leaf_outputs:
            # 没有感知输入，纯文本推理
            return {"_text_only": torch.ones(batch_size, device=backbone_output.device)}

        # 堆叠所有叶嵌入
        leaf_ids = sorted(leaf_outputs.keys())
        embeddings = [leaf_outputs[leaf_id] for leaf_id in leaf_ids]
        leaf_stack = torch.stack(embeddings, dim=1)  # [B, N_leaf, D]

        # 使用文本 [CLS] token 作为查询
        text_query = backbone_output[:, 0:1, :]  # [B, 1, D]

        # 跨模态注意力
        attended, attn_weights = self.cross_modal_attention(
            query=text_query,
            key=leaf_stack,
            value=leaf_stack,
        )  # attended: [B, 1, D]

        # 生成融合权重
        combined = torch.cat([
            text_query.squeeze(1),                     # [B, D]
            leaf_stack.mean(dim=1),                     # [B, D]
            attended.squeeze(1),                         # [B, D]
        ], dim=-1)  # [B, 3D]

        raw_weights = self.weight_generator(combined)   # [B, max_leaves]
        active_weights = raw_weights[:, :len(leaf_ids)]  # [B, N_leaf]
        active_weights = active_weights / (active_weights.sum(dim=-1, keepdim=True) + 1e-8)

        return {
            leaf_id: active_weights[:, i]
            for i, leaf_id in enumerate(leaf_ids)
        }

    def extra_repr(self) -> str:
        return f"max_leaves={self.max_leaves}, registered={list(self._leaf_ids_registered)}"
