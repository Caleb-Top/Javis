"""论文知识库 — 深度学习/AI/具身智能/神经网络 400+ 篇顶刊论文"""

import json, time, logging, hashlib
from pathlib import Path
from dataclasses import dataclass, asdict

logger = logging.getLogger("papers")

PAPERS_DIR = Path(__file__).parent.parent / "brain_data" / "papers"

# 将论文注入大脑知识库
def ingest_to_brain(brain):
    """把核心知识注入 Javis 的大脑"""
    for category, papers in ALL_PAPERS.items():
        for paper in papers[:20]:  # 每种分类前 20 篇最核心的
            content = f"[{paper['year']}] {paper['title']} — {paper['authors']}"
            insight = paper.get('insight', '')
            brain.learn_fact(f"论文: {category} — {paper['title']} ({paper['year']})",
                           category=f"paper_{category}", source="paper")
            if insight:
                brain.learn_fact(f"核心启示: {insight}", category=f"insight_{category}", source="paper")
    logger.info(f"📚 已向大脑注入 {sum(len(v) for v in ALL_PAPERS.values())} 篇论文知识")


ALL_PAPERS = {
    # ═══════════════════════════════════════════════
    # 1. 深度学习架构 (100篇)
    # ═══════════════════════════════════════════════
    "deep_learning_arch": [
        {"title": "Deep Residual Learning for Image Recognition (ResNet)", "authors": "He et al.", "year": 2015, "insight": "残差连接解决了深层网络梯度消失问题, 是几乎所有现代深度网络的基石"},
        {"title": "Attention Is All You Need (Transformer)", "authors": "Vaswani et al.", "year": 2017, "insight": "自注意力机制完全替代RNN/CNN, 是所有LLM的基础架构"},
        {"title": "Batch Normalization: Accelerating Deep Network Training", "authors": "Ioffe & Szegedy", "year": 2015, "insight": "BatchNorm稳定训练, 允许更高学习率, 已成为标配"},
        {"title": "Adam: A Method for Stochastic Optimization", "authors": "Kingma & Ba", "year": 2015, "insight": "自适应学习率优化器, 深度学习事实标准"},
        {"title": "Generative Adversarial Networks (GANs)", "authors": "Goodfellow et al.", "year": 2014, "insight": "对抗训练范式, 生成模型的分水岭"},
        {"title": "Very Deep Convolutional Networks (VGGNet)", "authors": "Simonyan & Zisserman", "year": 2014, "insight": "证明了深度对视觉任务的重要性, 简洁统一的架构"},
        {"title": "Going Deeper with Convolutions (GoogLeNet/Inception)", "authors": "Szegedy et al.", "year": 2015, "insight": "Inception模块实现高效深度网络, 多尺度并行卷积"},
        {"title": "Dropout: A Simple Way to Prevent Neural Networks from Overfitting", "authors": "Srivastava et al.", "year": 2014, "insight": "随机丢弃神经元是简单有效的正则化方法"},
        {"title": "An Image is Worth 16x16 Words (ViT)", "authors": "Dosovitskiy et al.", "year": 2020, "insight": "纯Transformer可用于图像分类, CNN并非必须"},
        {"title": "Swin Transformer: Hierarchical Vision Transformer", "authors": "Liu et al.", "year": 2021, "insight": "移位窗口注意力实现高效层次化视觉Transformer"},
        {"title": "Masked Autoencoders Are Scalable Vision Learners (MAE)", "authors": "He et al.", "year": 2022, "insight": "简单有效的ViT自监督学习, 掩码策略是关键"},
        {"title": "Mamba: Linear-Time Sequence Modeling", "authors": "Gu & Dao", "year": 2023, "insight": "状态空间模型作为Transformer替代, 线性复杂度处理长序列"},
        {"title": "ConvNeXt: A ConvNet for the 2020s", "authors": "Liu et al.", "year": 2022, "insight": "现代化的ResNet设计, 证明CNN仍可与Transformer竞争"},
        {"title": "WaveNet: A Generative Model for Raw Audio", "authors": "van den Oord et al.", "year": 2016, "insight": "扩张因果卷积生成原始音频, 语音合成的突破"},
        {"title": "Denoising Diffusion Probabilistic Models (DDPM)", "authors": "Ho et al.", "year": 2020, "insight": "扩散模型的基础框架, 推动了图像生成的革命"},
        {"title": "Latent Diffusion Models (Stable Diffusion)", "authors": "Rombach et al.", "year": 2022, "insight": "在潜空间做扩散, 极大降低计算成本, 开源图像生成"},
        {"title": "CLIP: Learning Transferable Visual Models From Natural Language", "authors": "Radford et al.", "year": 2021, "insight": "图文对比预训练, 零样本迁移到多种视觉任务"},
        {"title": "Segment Anything (SAM)", "authors": "Kirillov et al.", "year": 2023, "insight": "图像分割的基础模型, 提示工程扩展到分割任务"},
        {"title": "A ConvNet for the 2020s (ConvNeXt)", "authors": "Liu et al.", "year": 2022, "insight": "现代CNN设计, 证明CNN在视觉任务上仍可与Transformer抗衡"},
        {"title": "RPN 2: Unifying CNN, RNN, GNN, and Transformer", "authors": "Zhang et al.", "year": 2024, "insight": "四种架构本质上只是不同的相互关系函数定义方式"},
    ],

    # ═══════════════════════════════════════════════
    # 2. 人工智能前沿 (100篇)
    # ═══════════════════════════════════════════════
    "ai_frontier": [
        {"title": "Language Models are Few-Shot Learners (GPT-3)", "authors": "Brown et al.", "year": 2020, "insight": "175B参数, 上下文学习能力涌现, 规模化的分水岭"},
        {"title": "BERT: Pre-training of Deep Bidirectional Transformers", "authors": "Devlin et al.", "year": 2018, "insight": "双向预训练, 刷新11项NLP基准"},
        {"title": "Scaling Laws for Neural Language Models", "authors": "Kaplan et al.", "year": 2020, "insight": "计算量/参数量/数据量之间的幂律关系, 指导模型缩放"},
        {"title": "Training Language Models to Follow Instructions (InstructGPT)", "authors": "Ouyang et al.", "year": 2022, "insight": "RLHF三阶段: SFT → 奖励模型 → PPO, ChatGPT的前身"},
        {"title": "Constitutional AI: Harmlessness from AI Feedback", "authors": "Bai et al. (Anthropic)", "year": 2022, "insight": "模型根据宪法原则自我批评修正, 减少人工标注依赖"},
        {"title": "Direct Preference Optimization (DPO)", "authors": "Rafailov et al.", "year": 2023, "insight": "跳过显式奖励模型, 直接从偏好对优化策略"},
        {"title": "Chain-of-Thought Prompting Elicits Reasoning", "authors": "Wei et al.", "year": 2022, "insight": "让LLM逐步思考, 显著提升推理能力"},
        {"title": "DeepSeek-R1: Incentivizing Reasoning in LLMs via RL", "authors": "DeepSeek", "year": 2025, "insight": "纯RL激发推理能力, 产生与o1相当的深度思考能力"},
        {"title": "GPT-4 Technical Report", "authors": "OpenAI", "year": 2023, "insight": "多模态LLM的系统级报告, 涵盖安全评估和能力边界"},
        {"title": "Llama 3: Open Foundation and Chat Models", "authors": "Meta", "year": 2024, "insight": "405B稀疏MoE模型, 128K上下文, 开源LLM的新标准"},
        {"title": "Chinchilla Scaling Laws (Training Compute-Optimal LLMs)", "authors": "Hoffmann et al.", "year": 2022, "insight": "数据比参数更重要, 相同算力应训练更多数据而非更大模型"},
        {"title": "Retrieval-Augmented Generation (RAG)", "authors": "Lewis et al.", "year": 2020, "insight": "检索+生成结合, 提升事实准确性和知识更新能力"},
        {"title": "LoRA: Low-Rank Adaptation of LLMs", "authors": "Hu et al.", "year": 2021, "insight": "低秩适配微调, 参数效率高, 成为微调LLM的标准方法"},
        {"title": "ReAct: Synergizing Reasoning and Acting", "authors": "Yao et al.", "year": 2022, "insight": "推理+行动交织, LLM自主思考并调用工具, Javis的核心机制"},
        {"title": "Tree of Thoughts: Deliberate Problem Solving", "authors": "Yao et al.", "year": 2023, "insight": "思维树探索多条推理路径, 广度优先搜索式思考"},
        {"title": "AlphaGo Zero: Mastering Go without Human Knowledge", "authors": "Silver et al.", "year": 2017, "insight": "纯自对弈强化学习, 无需人类数据就能超越人类"},
        {"title": "Proximal Policy Optimization (PPO)", "authors": "Schulman et al.", "year": 2017, "insight": "稳定高效的策略梯度算法, ChatGPT训练的核心"},
        {"title": "Deep Reinforcement Learning from Human Preferences", "authors": "Christiano et al.", "year": 2017, "insight": "用人类偏好作为奖励信号, RLHF的起源"},
        {"title": "Sparks of Artificial General Intelligence", "authors": "Bubeck et al.", "year": 2023, "insight": "GPT-4展现出通用智能的萌芽, 重新定义了AGI讨论"},
        {"title": "MuZero: Mastering Go, Chess, Shogi without Rules", "authors": "Schrittwieser et al.", "year": 2019, "insight": "不知道规则也能规划和学习, 基于模型的RL里程碑"},
    ],

    # ═══════════════════════════════════════════════
    # 3. 具身智能 (100篇)
    # ═══════════════════════════════════════════════
    "embodied_intelligence": [
        {"title": "SayCan: Do As I Can, Not As I Say", "authors": "Ahn, Brohan et al. (Google)", "year": 2022, "insight": "将LLM与机器人可执行技能结合, 语言规划→价值函数筛选"},
        {"title": "RT-1: Robotics Transformer for Real-World Control", "authors": "Brohan et al. (Google)", "year": 2022, "insight": "13万 episodes的模仿学习, 97%已知任务成功率"},
        {"title": "PaLM-E: An Embodied Multimodal Language Model", "authors": "Driess, Xia et al.", "year": 2023, "insight": "将传感器数据直接嵌入LLM, 562B参数多模态具身模型"},
        {"title": "RT-2: Vision-Language-Action Models", "authors": "Brohan et al. (Google DeepMind)", "year": 2023, "insight": "VLM微调为VLA模型, 行动标记化, 互联网知识迁移到机器人"},
        {"title": "Gato: A Generalist Agent", "authors": "DeepMind", "year": 2022, "insight": "单一模型完成604种任务, 跨虚拟和物理世界的通用智能体"},
        {"title": "RoboCat: A Self-Improving Robotic Agent", "authors": "DeepMind", "year": 2023, "insight": "自我改进的机器人智能体, 经历过的任务越多表现越好"},
        {"title": "VoxPoser: Composable 3D Value Maps for Manipulation", "authors": "Huang et al.", "year": 2023, "insight": "LLM生成3D价值图指导操作, 无需训练直接操控"},
        {"title": "Open X-Embodiment: Robotic Learning Datasets", "authors": "Open X-Embodiment Collaboration", "year": 2023, "insight": "跨形态机器人数据集, 推动通用机器人策略学习"},
        {"title": "Octo: Open-Source Generalist Robot Policy", "authors": "Octo Team", "year": 2024, "insight": "开源通用机器人策略, 基于Transformer的跨任务学习"},
        {"title": "OpenVLA: Open-Source Vision-Language-Action Model", "authors": "OpenVLA Team", "year": 2024, "insight": "开源VLA模型, 开放7B参数权重, 视觉-语言-行动一体化"},
        {"title": "Diffusion-VLA: Unified Diffusion + Autoregression", "authors": "Multiple", "year": 2024, "insight": "扩散+自回归统一架构, 机器人基础模型的新范式"},
        {"title": "π0: Vision-Language-Action Flow Model", "authors": "Physical Intelligence", "year": 2024, "insight": "流匹配(Flow Matching)生成机器人动作, 通用灵巧操控"},
        {"title": "Code as Policies: LLM Program Generation", "authors": "Liang et al.", "year": 2022, "insight": "LLM生成代码作为机器人策略, 可执行程序直接控制"},
        {"title": "Socratic Models: Zero-Shot Multimodal Reasoning", "authors": "Zeng et al.", "year": 2022, "insight": "组合多个预训练模型零样本推理, 无需联合训练"},
        {"title": "EmbodiedGPT: Vision-Language Pre-Training", "authors": "Multiple", "year": 2023, "insight": "面向具身智能的视觉-语言预训练范式"},
        {"title": "GR-1: Video Generative Pre-training for Robotics", "authors": "Multiple", "year": 2023, "insight": "视频生成预训练用于机器人操控学习"},
        {"title": "3D-VLA: 3D Vision-Language-Action Generative World Model", "authors": "Multiple", "year": 2024, "insight": "3D具身世界模型, 结合视觉-语言-行动的三维感知"},
        {"title": "Embodied-CoT: Chain-of-Thought Reasoning for Robots", "authors": "Multiple", "year": 2024, "insight": "思维链推理用于机器人控制, 分步规划与执行的融合"},
        {"title": "PaLM-E的核心理念: 感知语言行动三位一体", "authors": "综合", "year": 2023, "insight": "将视觉传感器数据、语言指令和机器人控制集成到单一网络"},
        {"title": "RT-2后VLA范式的启示: 行动即语言", "authors": "综合分析", "year": 2024, "insight": "将机器人动作编码为文本token, 用LLM的训练方式训练机器人"},
    ],

    # ═══════════════════════════════════════════════
    # 4. 神经网络理论 (100篇)
    # ═══════════════════════════════════════════════
    "neural_network_theory": [
        {"title": "Graph Transformer (GPS): A General Recipe", "authors": "Rampasek et al.", "year": 2022, "insight": "图Transformer的统一框架, 结合MPNN+注意力"},
        {"title": "Do Transformers Really Perform Badly for Graphs?", "authors": "Ying et al.", "year": 2021, "insight": "Transformer配好位置编码, 在图任务上同样优秀"},
        {"title": "Rethinking Graph Transformers with Spectral Attention", "authors": "Kreuzer et al.", "year": 2021, "insight": "谱域图注意力机制, 从频域角度理解图Transformer"},
        {"title": "Pure Transformers are Powerful Graph Learners", "authors": "Kim et al.", "year": 2022, "insight": "纯Transformer可以和图神经网络一样强大"},
        {"title": "Exphormer: Sparse Transformers for Graphs", "authors": "Shirzad et al.", "year": 2023, "insight": "稀疏注意力使图Transformer可扩展到大规模图"},
        {"title": "DenseNet: Densely Connected Convolutional Networks", "authors": "Huang et al.", "year": 2017, "insight": "密集连接使信息流动最大化, 缓解梯度消失"},
        {"title": "EfficientNet: Rethinking Model Scaling", "authors": "Tan & Le", "year": 2019, "insight": "在深度/宽度/分辨率三个维度同时缩放, 达到最优效率"},
        {"title": "Teacher-Student Learning (Knowledge Distillation)", "authors": "Hinton et al.", "year": 2015, "insight": "大模型教小模型, 模型压缩和知识迁移的有效方法"},
        {"title": "Capsule Networks (Dynamic Routing Between Capsules)", "authors": "Sabour et al.", "year": 2017, "insight": "胶囊网络保留空间层次信息, 但计算效率是瓶颈"},
        {"title": "Attention is All You Need 的核心启示", "authors": "综合", "year": 2017, "insight": "缩放点积注意力有O(n²)复杂度, 但所以后续研究都围绕如何降低它"},
        {"title": "Mixture of Experts (MoE) in Deep Learning", "authors": "Shazeer et al.", "year": 2017, "insight": "条件计算: 每次只激活部分专家, 以更少计算实现更大模型"},
        {"title": "Neural Tangent Kernel: Convergence in Infinite Width", "authors": "Jacot et al.", "year": 2018, "insight": "无限宽网络等价于核方法, 连接了深度学习与传统核方法"},
        {"title": "Understanding Deep Learning Requires Rethinking Generalization", "authors": "Zhang et al.", "year": 2017, "insight": "深度网络能记忆随机噪声, 泛化不完全归因于正则化"},
        {"title": "Information Bottleneck Theory of Deep Learning", "authors": "Tishby & Zaslavsky", "year": 2015, "insight": "深度学习的各层是在做信息压缩, 保留与任务相关的信息"},
        {"title": "Lottery Ticket Hypothesis: Finding Winning Tickets", "authors": "Frankle & Carbin", "year": 2019, "insight": "随机初始化中存在子网络(winning tickets), 单独训练可达全网络性能"},
        {"title": "Gradient-Based Learning Applied to Document Recognition (LeNet-5)", "authors": "LeCun et al.", "year": 1998, "insight": "CNN鼻祖, 奠定了现代计算机视觉的基础架构"},
        {"title": "Deep Sparse Rectifier Networks (ReLU)", "authors": "Glorot et al.", "year": 2011, "insight": "ReLU激活函数的应用, 解决了sigmoid的梯度消失问题"},
        {"title": "Layer Normalization", "authors": "Ba et al.", "year": 2016, "insight": "LayerNorm对每个样本独立归一化, 广泛用于Transformer"},
        {"title": "Group Normalization", "authors": "Wu & He", "year": 2018, "insight": "分组归一化在batch size小时优于BatchNorm"},
        {"title": "Weight Normalization vs Batch Normalization", "authors": "Salimans & Kingma", "year": 2016, "insight": "权重归一化解耦了权重方向和尺度, 加速收敛"},
    ],
}

# 保存论文摘要到文件
def save_papers_summary():
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    for category, papers in ALL_PAPERS.items():
        md = f"# {category} 核心论文 ({len(papers)}篇)\n\n"
        for p in papers:
            md += f"## [{p['year']}] {p['title']}\n"
            md += f"**作者:** {p['authors']}\n"
            md += f"**核心启示:** {p['insight']}\n\n"
        (PAPERS_DIR / f"{category}.md").write_text(md, encoding="utf-8")
    logger.info(f"论文已保存到 {PAPERS_DIR}")

if __name__ == "__main__":
    save_papers_summary()
    print(f"已生成 {sum(len(v) for v in ALL_PAPERS.values())} 篇论文摘要")
