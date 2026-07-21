"""听觉感知叶 — 从原始音频波形生长出语音理解

参考 DOCX 文档：第 3.2 节
不使用预训练的 Whisper/Wav2Vec，从随机 Conv1d 开始。
输出 1536 维嵌入，对齐到文本认知空间。
"""

import logging
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from .base_leaf import PerceptualLeaf

logger = logging.getLogger("kernel.leaf.auditory")

_AUDIO_SAMPLE_RATE = 16000
_AUDIO_INPUT_LENGTH = _AUDIO_SAMPLE_RATE  # 1 秒音频


class AuditoryLeaf(PerceptualLeaf):
    """听觉感知叶

    从麦克风原始音频波形生长出语音/声音理解。
    输出 1536 维嵌入，对齐到文本认知空间。
    """

    def __init__(self, backbone_dim: int = 1536, sample_rate: int = _AUDIO_SAMPLE_RATE):
        super().__init__(
            input_shape=(1, sample_rate),
            backbone_dim=backbone_dim,
            growth_capacity=30_000_000,  # 3000 万参数上限
            leaf_id="auditory",
        )
        self.sample_rate = sample_rate
        self.temperature = nn.Parameter(torch.tensor(0.07))

    def _init_sensory_encoder(self) -> nn.Module:
        """音频编码器：从原始波形到频谱特征

        类似耳蜗→听觉皮层通路。
        使用可学习的大卷积核模拟 STFT（短时傅里叶变换）。
        """
        return nn.Sequential(
            # 层1：波形→频谱（可学习 STFT）
            # kernel=400 @ 16kHz = 25ms 窗口, stride=160 = 10ms 步长
            nn.Conv1d(1, 64, kernel_size=400, stride=160),
            nn.BatchNorm1d(64),
            nn.GELU(),
            # 层2：频率模式
            nn.Conv1d(64, 128, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm1d(128),
            nn.GELU(),
            # 层3：时间序列
            nn.Conv1d(128, 256, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm1d(256),
            nn.GELU(),
            # 层4：全局时间聚合
            nn.AdaptiveAvgPool1d(1),
            nn.Flatten(),  # [B, 256]
        )

    def _encoder_output_dim(self) -> int:
        return 256

    # ── 音频预处理 ──

    @staticmethod
    def preprocess_audio(audio_bytes: bytes, sample_rate: int = _AUDIO_SAMPLE_RATE) -> torch.Tensor:
        """将原始音频字节转换为 [B, 1, sample_rate] 张量"""
        import numpy as np

        waveform = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        waveform = waveform / 32768.0  # 归一化到 [-1, 1]

        # 截断或填充到固定长度
        target_len = sample_rate
        if len(waveform) > target_len:
            waveform = waveform[:target_len]
        elif len(waveform) < target_len:
            waveform = np.pad(waveform, (0, target_len - len(waveform)))

        tensor = torch.from_numpy(waveform).float().unsqueeze(0).unsqueeze(0)  # [1, 1, T]
        return tensor

    @staticmethod
    def preprocess_tensor(audio_tensor: torch.Tensor) -> torch.Tensor:
        """将任意音频张量统一为 [B, 1, SAMPLE_RATE]"""
        if audio_tensor.dim() == 1:
            audio_tensor = audio_tensor.unsqueeze(0).unsqueeze(0)
        elif audio_tensor.dim() == 2:
            audio_tensor = audio_tensor.unsqueeze(1)
        # 确保长度
        if audio_tensor.size(-1) != _AUDIO_SAMPLE_RATE:
            audio_tensor = F.interpolate(
                audio_tensor, size=_AUDIO_SAMPLE_RATE, mode="linear",
                align_corners=False,
            )
        return audio_tensor

    # ── 前向传播与训练 ──

    def forward(self, raw_input):
        """前向传播"""
        if isinstance(raw_input, bytes):
            raw_input = self.preprocess_audio(raw_input)
        elif isinstance(raw_input, torch.Tensor) and raw_input.dim() < 3:
            raw_input = self.preprocess_tensor(raw_input)

        if raw_input.device != next(self.parameters()).device:
            raw_input = raw_input.to(next(self.parameters()).device)
        return super().forward(raw_input)

    def _info_nce_loss(self, audio_embeds: torch.Tensor,
                       text_embeds: torch.Tensor) -> torch.Tensor:
        """InfoNCE 对比损失：音频-文本对齐"""
        B = audio_embeds.size(0)
        a = F.normalize(audio_embeds, dim=-1)
        t = F.normalize(text_embeds, dim=-1)
        logits = torch.matmul(a, t.T) / self.temperature.abs()
        labels = torch.arange(B, device=a.device)
        loss_a2t = F.cross_entropy(logits, labels)
        loss_t2a = F.cross_entropy(logits.T, labels)
        return (loss_a2t + loss_t2a) / 2

    def _infant_learning(self, signal: dict) -> dict:
        """婴儿期学习：对齐音频嵌入到文本嵌入"""
        audio = signal["audio"]
        text_embed = signal.get("text_embed", signal.get("target_embedding"))

        audio_out = self(audio)["embedding"]
        loss = self._info_nce_loss(audio_out, text_embed)

        alignment = F.cosine_similarity(
            audio_out.mean(dim=0, keepdim=True),
            text_embed.mean(dim=0, keepdim=True),
        ).item()
        self.alignment_quality = 0.9 * self.alignment_quality + 0.1 * alignment

        return {
            "loss": loss.item(),
            "alignment": alignment,
            "growth_stage": self.growth_stage,
        }

    def _child_learning(self, signal: dict) -> dict:
        """儿童期学习（扩展网络后）"""
        audio = signal["audio"]
        text_embed = signal.get("text_embed", signal.get("target_embedding"))

        audio_out = self(audio)["embedding"]
        loss = self._info_nce_loss(audio_out, text_embed)

        alignment = F.cosine_similarity(
            audio_out.mean(dim=0, keepdim=True),
            text_embed.mean(dim=0, keepdim=True),
        ).item()
        self.alignment_quality = 0.9 * self.alignment_quality + 0.1 * alignment
        self._total_grown += 1

        return {
            "loss": loss.item(),
            "alignment": alignment,
            "growth_stage": self.growth_stage,
        }

    def _adult_refinement(self, signal: dict) -> dict:
        """成年期微调"""
        audio = signal["audio"]
        text_embed = signal.get("text_embed", signal.get("target_embedding"))

        audio_out = self(audio)["embedding"]
        aligned = self.alignment_projector(audio_out)

        loss = self._info_nce_loss(aligned, text_embed)
        loss += 0.5 * F.mse_loss(aligned, text_embed)

        alignment = F.cosine_similarity(
            aligned.mean(dim=0, keepdim=True),
            text_embed.mean(dim=0, keepdim=True),
        ).item()
        self.alignment_quality = 0.95 * self.alignment_quality + 0.05 * alignment

        return {
            "loss": loss.item(),
            "alignment": alignment,
            "growth_stage": self.growth_stage,
        }

    def _expand_capacity(self):
        """儿童期扩展：增加时间序列建模深度"""
        old_encoder = self.sensory_encoder
        children = list(old_encoder.children())
        new_encoder = nn.Sequential(
            children[0], children[1], children[2],  # Conv1d → BN → GELU
            # 新增深层
            nn.Conv1d(64, 128, kernel_size=5, stride=2, padding=2),
            nn.BatchNorm1d(128), nn.GELU(),
            nn.Conv1d(128, 256, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm1d(256), nn.GELU(),
            nn.Conv1d(256, 512, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm1d(512), nn.GELU(),
            nn.AdaptiveAvgPool1d(1), nn.Flatten(),
        )
        self.sensory_encoder = new_encoder
        self.semantic_compressor = nn.Sequential(
            nn.Linear(512, self.backbone_dim * 2),
            nn.LayerNorm(self.backbone_dim * 2), nn.GELU(),
            nn.Linear(self.backbone_dim * 2, self.backbone_dim),
        )
        logger.info("AuditoryLeaf 扩展完成")
