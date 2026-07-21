"""Javis 内核 — 感知叶 + 训练引擎 + 认知空间 的统一入口

将所有内生多模态组件整合为单一可管理实例。
"""

import logging
from typing import Optional

import torch

from kernel.leaves.visual_leaf import VisualLeaf
from kernel.leaves.auditory_leaf import AuditoryLeaf
from kernel.cognitive.fusion_gate import FusionGatingNetwork
from kernel.cognitive.unified_space import UnifiedCognitiveSpace
from kernel.training.train_loop import TrainingEngine
from kernel.training.sleep_learning import SleepLearning
from kernel.embedder import TextEmbedder

logger = logging.getLogger("kernel")

logger = logging.getLogger("kernel")


class JavisKernel:
    """Javis 内核 — 统一入口

    管理所有感知叶、认知空间、训练引擎和睡眠学习。
    在 main.py 中作为一个全局实例初始化。
    """

    def __init__(self):
        # ── 设备 ──
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"内核初始化: device={self.device}")

        # ── 感知叶 ──
        self.visual_leaf = VisualLeaf(backbone_dim=1536).to(self.device)
        self.auditory_leaf = AuditoryLeaf(backbone_dim=1536).to(self.device)
        logger.info("视觉叶/听觉叶已创建 (随机初始化)")

        # ── 认知空间 ──
        self.fusion_gate = FusionGatingNetwork(backbone_dim=1536).to(self.device)
        self.cognitive_space = UnifiedCognitiveSpace(dim=1536).to(self.device)
        self.fusion_gate.register_leaf("visual")
        self.fusion_gate.register_leaf("auditory")
        logger.info("认知空间已创建")

        # ── 训练引擎 ──
        self.training_engine = TrainingEngine(learning_rate=1e-4)
        self.training_engine.register_leaf(self.visual_leaf)
        self.training_engine.register_leaf(self.auditory_leaf)
        logger.info("训练引擎已创建")

        # ── 睡眠学习 ──
        self.sleep_learning = SleepLearning(training_engine=self.training_engine)
        logger.info("睡眠学习控制器已创建")

        # ── 文本嵌入器（将文字转 1536 维，供训练配对）──
        self.text_embedder = TextEmbedder(device=str(self.device))
        logger.info("文本嵌入器已创建")

        # ── 状态追踪 ──
        self._started = False

    def start(self):
        """启动所有后台服务"""
        if self._started:
            return
        self.training_engine.start()
        self.sleep_learning.start()
        self._started = True
        logger.info("内核后台服务已启动 (训练引擎 + 睡眠学习)")

    def stop(self):
        """停止所有后台服务"""
        self.training_engine.stop()
        self.sleep_learning.stop()
        self._started = False
        logger.info("内核后台服务已停止")

    # ── 视觉 ──

    def process_visual(self, image, add_to_buffer: bool = True) -> dict:
        """处理视觉输入

        Args:
            image: 图片路径、numpy 数组或张量
            add_to_buffer: 是否加入训练缓冲

        Returns:
            dict: {'embedding': tensor, 'growth_stage': str, 'alignment': float}
        """
        result = self.visual_leaf(image)
        result["leaf_type"] = "visual"

        if add_to_buffer and self.training_engine._running:
            # 加入训练缓冲（等待睡眠学习时配对文本嵌入）
            self.training_engine.add_training_sample("visual", {
                "image": image if isinstance(image, torch.Tensor) else self.visual_leaf.preprocess_image(image),
                "has_image": True,
                "difficulty": 0.3,
            })

        return result

    def process_visual_with_text(self, image, text_embed: torch.Tensor) -> dict:
        """带文本对齐的视觉处理（直接传入嵌入向量）"""
        result = self.process_visual(image, add_to_buffer=False)
        self.training_engine.add_training_sample("visual", {
            "image": image if isinstance(image, torch.Tensor) else self.visual_leaf.preprocess_image(image),
            "text_embed": text_embed,
        })
        return result

    def feed_visual_with_description(self, image, description: str) -> dict:
        """★ 融合：将截图+文字描述喂给 VisualLeaf 训练

        自动将描述文本转为 1536 维嵌入，与图片配对加入训练缓冲。
        这是 describe_screen 调用的主要接口。
        """
        text_embed = self.text_embedder.embed(description)
        return self.process_visual_with_text(image, text_embed)

    # ── 听觉 ──

    def process_auditory(self, audio, add_to_buffer: bool = True) -> dict:
        """处理音频输入"""
        result = self.auditory_leaf(audio)
        result["leaf_type"] = "auditory"

        if add_to_buffer and self.training_engine._running:
            self.training_engine.add_training_sample("auditory", {
                "audio": audio if isinstance(audio, torch.Tensor) else self.auditory_leaf.preprocess_audio(audio),
                "difficulty": 0.3,
            })

        return result

    def process_auditory_with_text(self, audio, text_embed: torch.Tensor) -> dict:
        """带文本对齐的音频处理（直接传入嵌入向量）"""
        result = self.process_auditory(audio, add_to_buffer=False)
        self.training_engine.add_training_sample("auditory", {
            "audio": audio if isinstance(audio, torch.Tensor) else self.auditory_leaf.preprocess_audio(audio),
            "text_embed": text_embed,
        })
        return result

    def feed_auditory_with_text(self, audio, text: str) -> dict:
        """★ 融合：将语音+文字喂给 AuditoryLeaf 训练

        自动将文字转为 1536 维嵌入，与音频配对加入训练缓冲。
        这是 speech_to_text 调用的主要接口。
        """
        text_embed = self.text_embedder.embed(text)
        return self.process_auditory_with_text(audio, text_embed)

    # ── 多模态融合 ──

    def fuse_multimodal(self, text_embed: Optional[torch.Tensor] = None,
                        visual_embed: Optional[torch.Tensor] = None,
                        auditory_embed: Optional[torch.Tensor] = None) -> dict:
        """融合多模态输入到统一认知空间

        Returns:
            dict: fused_embedding, activated_concepts, weights
        """
        # 收集各叶嵌入
        leaf_outputs = {}
        if visual_embed is not None:
            leaf_outputs["visual"] = visual_embed
        if auditory_embed is not None:
            leaf_outputs["auditory"] = auditory_embed

        # 计算融合权重
        if not leaf_outputs:
            return {"fused_embedding": text_embed, "weights": {"text": 1.0}}

        # 构造 dummy backbone_output 用于门控
        backbone_dim = 1536
        device = text_embed.device if text_embed is not None else self.device
        dummy_backbone = torch.zeros(1, 1, backbone_dim, device=device)

        weights = self.fusion_gate(dummy_backbone, leaf_outputs)

        # 映射到认知空间
        cognitive = self.cognitive_space.encode_to_cognitive(
            text_embed=text_embed,
            visual_embed=visual_embed,
            auditory_embed=auditory_embed,
            fusion_weights=weights,
        )
        cognitive["weights"] = weights
        return cognitive

    # ── 旧数据摄取（融合：将旧 episodes/semantic 灌入训练）──

    def ingest_old_episodes(self, max_episodes: int = 200) -> dict:
        """从 brain_data/episodes/ 读取旧 episode，提取视觉/音频知识

        旧 episodes 不保存实际图片/音频数据，所以无法用于感知叶训练。
        但 episode 中的 user_input 会被嵌入认知空间作为概念锚点。
        实际训练数据来自 describe_screen 和 speech_to_text 的实时调用。

        Returns:
            dict: {"visual_samples": int, "auditory_samples": int, "semantic_rules": int}
        """
        import json
        from pathlib import Path

        eps_dir = Path("brain_data/episodes")
        if not eps_dir.exists():
            return {"visual_samples": 0, "auditory_samples": 0, "semantic_rules": 0}

        results = {"visual_samples": 0, "auditory_samples": 0, "semantic_rules": 0}
        episodes = sorted(eps_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:max_episodes]

        concepts_added = 0
        for ep_path in episodes:
            try:
                data = json.loads(ep_path.read_text("utf-8"))
                user_input = data.get("user_input", "")
                outcome = data.get("outcome", "")

                if user_input and len(user_input) > 5:
                    # 注入认知空间，不作为训练数据（无实际传感器数据）
                    text = f"[episode] {user_input} -> {outcome}"
                    text_embed = self.text_embedder.embed(text)
                    idx = concepts_added % self.cognitive_space.num_concept_anchors
                    with torch.no_grad():
                        self.cognitive_space.concept_anchors.data[idx] += 0.05 * (
                            text_embed.squeeze(0) - self.cognitive_space.concept_anchors.data[idx]
                        )
                    concepts_added += 1
                    results["visual_samples"] += 1  # 统计样本数

            except Exception:
                continue

        logger.info(f"旧 episodes 注入认知空间: {results['visual_samples']} 条")
        return results

    def ingest_semantic_rules(self) -> int:
        """从 brain_data/semantic/ 读取旧语义规则，嵌入认知空间

        将规则结论作为概念锚点预训练数据。
        """
        import json
        from pathlib import Path
        import torch.nn.functional as F

        sem_dir = Path("brain_data/semantic")
        if not sem_dir.exists():
            return 0

        count = 0
        for f in sorted(sem_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text("utf-8"))
                conclusion = data.get("conclusion", "")
                if conclusion and len(conclusion) > 20:
                    # 将规则结论转为嵌入，更新概念锚点
                    text_embed = self.text_embedder.embed(conclusion)
                    anchor_idx = count % self.cognitive_space.num_concept_anchors
                    with torch.no_grad():
                        self.cognitive_space.concept_anchors.data[anchor_idx] += 0.1 * (
                            text_embed.squeeze(0) - self.cognitive_space.concept_anchors.data[anchor_idx]
                        )
                    count += 1
            except Exception:
                continue

        logger.info(f"旧语义规则嵌入完成: {count} 条注入认知空间")
        return count

    # ── 状态 ──

    def status(self) -> dict:
        """内核完整状态"""
        return {
            "device": str(self.device),
            "started": self._started,
            "visual_leaf": {
                "growth_stage": self.visual_leaf.growth_stage,
                "alignment_quality": round(self.visual_leaf.alignment_quality, 3),
            },
            "auditory_leaf": {
                "growth_stage": self.auditory_leaf.growth_stage,
                "alignment_quality": round(self.auditory_leaf.alignment_quality, 3),
            },
            "training": self.training_engine.status(),
            "sleep_learning": self.sleep_learning.status(),
            "cognitive_space": {
                "dim": self.cognitive_space.dim,
                "concept_anchors": self.cognitive_space.num_concept_anchors,
            },
        }

    def status_text(self) -> str:
        """人类可读的状态"""
        s = self.status()
        lines = [
            f"Javis 内核状态",
            f"  设备: {s['device']}",
            f"  视觉叶: {s['visual_leaf']['growth_stage']} "
            f"(对齐质量={s['visual_leaf']['alignment_quality']})",
            f"  听觉叶: {s['auditory_leaf']['growth_stage']} "
            f"(对齐质量={s['auditory_leaf']['alignment_quality']})",
            f"  训练引擎: {'运行中' if s['training']['running'] else '停止'}",
            f"  睡眠学习: {'运行中' if s['sleep_learning']['monitor_running'] else '停止'}"
            f" ({s['sleep_learning']['unconsolidated_episodes']} 待巩固)",
            f"  认知空间: {s['cognitive_space']['dim']}维, "
            f"{s['cognitive_space']['concept_anchors']} 个概念锚点",
        ]
        return "\n".join(lines)
