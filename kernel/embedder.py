"""文本嵌入器 — 将文本转换为 1536 维嵌入（供感知叶训练用）

使用 sentence-transformers + 投影层，CPU 推理，不占 VRAM。
输出维度对齐 VisualLeaf/AuditoryLeaf 的 backbone_dim=1536。
"""

import logging
from typing import Optional

import torch
import torch.nn.functional as F

logger = logging.getLogger("kernel.embedder")


class TextEmbedder:
    """文本嵌入器

    将任意文本转换为 1536 维嵌入向量。
    用于为感知叶提供文本正样本（InfoNCE 训练的 target）。
    """

    def __init__(self, device: Optional[str] = None):
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
        self._model = None
        self._projector = None
        self._dim = 1536
        logger.info(f"文本嵌入器初始化: device={self.device}")

    _no_st_warned = False

    def _lazy_init(self):
        """延迟初始化（首次使用时才加载模型）"""
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            # 用轻量模型，CPU推理
            self._model = SentenceTransformer(
                "all-MiniLM-L6-v2", device="cpu"
            )
            hidden_dim = 384  # all-MiniLM-L6-v2 的输出维度
            # 投影到 1536 维
            self._projector = torch.nn.Linear(hidden_dim, self._dim).to(self.device)
            logger.info("文本嵌入器加载完成: all-MiniLM-L6-v2 -> 1536d")
        except ImportError:
            if not self._no_st_warned:
                logger.warning("sentence-transformers 未安装，使用回退嵌入")
                self._no_st_warned = True
            self._model = None

    def embed(self, text: str) -> torch.Tensor:
        """将文本转换为 [1, 1536] 嵌入

        Args:
            text: 输入文本

        Returns:
            [1, 1536] 张量
        """
        if not text or not text.strip():
            return torch.zeros(1, self._dim, device=self.device)

        self._lazy_init()

        if self._model is None:
            # 回退：使用简单哈希嵌入
            return self._fallback_embed(text)

        # sentence-transformers embedding
        emb = self._model.encode(text, convert_to_tensor=True)  # [384]
        if emb.device != self.device:
            emb = emb.to(self.device)
        # 投影到 1536
        projected = self._projector(emb.unsqueeze(0))  # [1, 1536]
        return F.normalize(projected, dim=-1)

    def embed_batch(self, texts: list[str]) -> torch.Tensor:
        """批量文本嵌入

        Args:
            texts: 文本列表

        Returns:
            [B, 1536] 张量
        """
        if not texts:
            return torch.zeros(0, self._dim, device=self.device)

        self._lazy_init()

        if self._model is None:
            return torch.stack([self._fallback_embed(t) for t in texts])

        embs = self._model.encode(texts, convert_to_tensor=True)  # [B, 384]
        if embs.device != self.device:
            embs = embs.to(self.device)
        projected = self._projector(embs)  # [B, 1536]
        return F.normalize(projected, dim=-1)

    def _fallback_embed(self, text: str) -> torch.Tensor:
        """回退嵌入：字符级哈希，确保至少能工作"""
        emb = torch.zeros(self._dim, device=self.device)
        for i, ch in enumerate(text[:500]):
            idx = (ord(ch) * 2654435761) % self._dim
            emb[idx] += 1.0
        emb = emb / (emb.norm() + 1e-8)
        return emb.unsqueeze(0)

    def status(self) -> dict:
        return {
            "device": self.device,
            "model_loaded": self._model is not None,
            "output_dim": self._dim,
        }
