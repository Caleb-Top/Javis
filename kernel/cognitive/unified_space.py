"""统一认知空间 — 所有模态最终映射到的共享表征空间

参考 DOCX 文档：第 4.2 节
无论看到、听到还是读到，最终都转化为同一 1536 维空间中的向量。
核心主干 Qwen2.5-1.5B 的 hidden_state 维度就是 1536。
"""

import logging
from typing import Dict, Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger("kernel.cognitive.space")


class DynamicGraph(nn.Module):
    """动态概念关联图：概念之间的语义关系"""

    def __init__(self, dim: int = 1536):
        super().__init__()
        self.dim = dim
        # 边的邻接矩阵（学习到的概念关系）
        self.edge_weights = nn.Parameter(torch.zeros(1, 1))
        self.edge_emb = nn.Linear(dim, dim)

    def forward(self, concept_anchors: torch.Tensor) -> torch.Tensor:
        """更新概念锚点之间的连接"""
        # 简单线性变换
        return self.edge_emb(concept_anchors)

    def get_state(self) -> dict:
        return {"dim": self.dim}


class UnifiedCognitiveSpace(nn.Module):
    """统一认知空间

    所有感知模态的输出最终被加权融合到此处。
    核心主干读取此空间的嵌入进行推理。
    """

    def __init__(self, dim: int = 1536, num_concept_anchors: int = 1000):
        super().__init__()
        self.dim = dim
        self.num_concept_anchors = num_concept_anchors

        # 认知空间中的"概念锚点"
        # 这些是基础概念在嵌入空间中的位置
        # 初始化为小随机值，随使用而精化
        self.concept_anchors = nn.Parameter(
            torch.randn(num_concept_anchors, dim) * 0.01
        )

        # 概念关联图
        self.concept_graph = DynamicGraph(dim=dim)

        # 融合投影器（各模态加权融合后进此）
        self.fusion_projector = nn.Linear(dim, dim)

        # 使用计数（追踪哪些概念被激活过）
        self.register_buffer("concept_usage", torch.zeros(num_concept_anchors))

    def encode_to_cognitive(self,
                            text_embed: Optional[torch.Tensor] = None,
                            visual_embed: Optional[torch.Tensor] = None,
                            auditory_embed: Optional[torch.Tensor] = None,
                            fusion_weights: Optional[Dict[str, torch.Tensor]] = None,
                            ) -> Dict:
        """将多模态输入编码到统一认知空间

        Args:
            text_embed:     [B, D] 文本嵌入（来自主干）
            visual_embed:   [B, D] 视觉嵌入（来自 VisualLeaf）
            auditory_embed: [B, D] 听觉嵌入（来自 AuditoryLeaf）
            fusion_weights: {modality: [B]} 融合权重，默认均等

        Returns:
            dict: fused_embedding, activated_concepts, concept_graph_state
        """
        device = self.concept_anchors.device
        fusion_weights = fusion_weights or {}
        embeddings = []
        weights = []

        if text_embed is not None:
            embeddings.append(text_embed.to(device))
            w = fusion_weights.get("text", torch.ones(text_embed.size(0), device=device))
            weights.append(w)

        if visual_embed is not None:
            embeddings.append(visual_embed.to(device))
            w = fusion_weights.get("visual", torch.zeros(visual_embed.size(0), device=device))
            weights.append(w)

        if auditory_embed is not None:
            embeddings.append(auditory_embed.to(device))
            w = fusion_weights.get("auditory", torch.zeros(auditory_embed.size(0), device=device))
            weights.append(w)

        if not embeddings:
            raise ValueError("至少需要一个输入模态")

        # 归一化权重
        weights = torch.stack(weights, dim=0)  # [N_mod, B]
        weights = weights / (weights.sum(dim=0, keepdim=True) + 1e-8)

        # 加权融合
        fused = sum(
            w.view(-1, 1) * emb
            for w, emb in zip(weights, embeddings)
        )  # [B, D]

        # 通过融合投影器
        fused = self.fusion_projector(fused)
        fused = F.normalize(fused, dim=-1)

        # 在认知空间中找到最近的概念锚点
        similarities = torch.matmul(fused, self.concept_anchors.T)  # [B, N_anchor]
        top_scores, top_indices = torch.topk(similarities, k=min(5, self.num_concept_anchors), dim=-1)

        # 更新概念使用计数
        for idx in top_indices.flatten():
            self.concept_usage[idx] += 1

        # 更新概念锚点（如果接近度够高，拉近）
        with torch.no_grad():
            for i in range(fused.size(0)):
                for j in range(top_indices.size(1)):
                    anchor_idx = top_indices[i, j].item()
                    score = top_scores[i, j].item()
                    if score > 0.5:
                        # 将概念锚点向输入方向微调
                        self.concept_anchors.data[anchor_idx] += 0.01 * (
                            fused[i].detach() - self.concept_anchors.data[anchor_idx]
                        )

        return {
            "fused_embedding": fused,   # [B, D]
            "activated_concepts": {
                "indices": top_indices,  # [B, 5]
                "scores": top_scores,    # [B, 5]
            },
            "concept_graph_state": self.concept_graph.get_state(),
        }

    def lookup_concept(self, embedding: torch.Tensor, top_k: int = 3) -> list:
        """查找嵌入最匹配的概念索引"""
        sim = torch.matmul(F.normalize(embedding, dim=-1),
                           F.normalize(self.concept_anchors, dim=-1).T)
        _, indices = torch.topk(sim, top_k, dim=-1)
        return indices[0].tolist()

    def get_most_used_concepts(self, k: int = 10) -> torch.Tensor:
        """获取最常用的 k 个概念索引"""
        return torch.topk(self.concept_usage, k).indices

    def extra_repr(self) -> str:
        return f"dim={self.dim}, anchors={self.num_concept_anchors}"
