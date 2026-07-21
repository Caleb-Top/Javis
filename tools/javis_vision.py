"""
Javis 视觉系统 — 自进化视觉皮层
===================================
架构:
  L1 像素→特征 (OpenCV 纯计算)          ← 不依赖任何模型
  L2 特征→语义 (规则匹配 brain_data/semantic/)  ← 自我判断
  L3 经验→进化 (每帧写入经验 brain_data/experiences/) ← 自我强化
  YOLO 目标检测 (tools/yolo/ + yolo_manager.py)  ← 可热切换模型
  Brain 融合 (检测结果→facts, 规则→semantic, 观察→experiences)

三大输出:
  1. describe_screen → 自然语言描述 (OpenCV + YOLO + 规则)
  2. 经验记录 → brain_data/experiences/vis_*.json (自我进化)
  3. Brain facts → 检测到的物体作为事实 (用于聊天检索)

YOLO 热切换: describe_screen_switch_model() 可运行时换模型
"""

import os, json, time, hashlib, logging
from pathlib import Path
import numpy as np

logger = logging.getLogger("vision")

# ── 全局 kernel 引用（由 main.py 在融合模式下手动注入）──
_VISION_KERNEL = None

def set_vision_kernel(kernel):
    """注入 JavisKernel 实例，使 describe_screen 能喂给 VisualLeaf"""
    global _VISION_KERNEL
    _VISION_KERNEL = kernel

# ─────────── 配置 ───────────
BRAIN_DIR = Path(__file__).parent.parent / "brain_data"
YOLO_LABELS = [
    "person","bicycle","car","motorcycle","airplane","bus","train","truck","boat",
    "traffic light","fire hydrant","stop sign","parking meter","bench","bird","cat",
    "dog","horse","sheep","cow","elephant","bear","zebra","giraffe","backpack",
    "umbrella","handbag","tie","suitcase","frisbee","skis","snowboard","sports ball",
    "kite","baseball bat","baseball glove","skateboard","surfboard","tennis racket",
    "bottle","wine glass","cup","fork","knife","spoon","bowl","banana","apple",
    "sandwich","orange","broccoli","carrot","hot dog","pizza","donut","cake","chair",
    "couch","potted plant","bed","dining table","toilet","tv","laptop","mouse",
    "remote","keyboard","cell phone","microwave","oven","toaster","sink","refrigerator",
    "book","clock","vase","scissors","teddy bear","hair drier","toothbrush"
]


# ════════════════════════════════════════════
# L1: 像素→特征 (OpenCV 纯计算)
# ════════════════════════════════════════════

class L1_FeatureExtractor:
    """像素级特征提取 — 纯OpenCV, 不调任何模型"""

    def __init__(self):
        # YOLO 通过工具人管理器独立管理
        pass

    def load_image(self, source):
        """统一图像加载接口（支持中文路径）"""
        cv2 = __import__("cv2")
        import numpy as np
        if isinstance(source, str):
            # Windows: cv2.imread 不支持中文路径，改用 imdecode
            try:
                buf = np.fromfile(source, dtype=np.uint8)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            except Exception:
                img = cv2.imread(source)
            if img is None:
                raise ValueError(f"无法读取: {source}")
        elif isinstance(source, np.ndarray):
            img = source.copy()
        else:
            raise ValueError("需要路径或numpy数组")
        return img

    def color_features(self, img):
        """颜色特征"""
        cv2 = __import__("cv2")
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h_hist = cv2.calcHist([hsv], [0], None, [36], [0, 180])
        dominant_hue = int(np.argmax(h_hist)) * 5
        return {
            "brightness": round(float(np.mean(gray)), 1),
            "dominant_hue": dominant_hue,
            "saturation_mean": round(float(np.mean(hsv[:,:,1])), 1),
            "color_variance": round(float(np.std(img.reshape(-1,3), axis=0).mean()), 1),
            "is_dark": float(np.mean(gray)) < 60,
            "is_bright": float(np.mean(gray)) > 180,
            "is_colorful": float(np.std(img.reshape(-1,3), axis=0).mean()) > 50,
        }

    def edge_features(self, img):
        """边缘/结构特征"""
        cv2 = __import__("cv2")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(np.sum(edges > 0) / edges.size)
        sobel_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        he = float(np.mean(np.abs(sobel_x)))
        ve = float(np.mean(np.abs(sobel_y)))
        hv_ratio = he / ve if ve > 0.01 else 999
        return {
            "edge_density": round(edge_density, 4),
            "horizontal_energy": round(he, 2),
            "vertical_energy": round(ve, 2),
            "hv_ratio": round(hv_ratio, 2),
            "is_structured": edge_density > 0.02 and 0.5 < hv_ratio < 2.0,
            "edge_level": "high" if edge_density > 0.06 else ("medium" if edge_density > 0.02 else "low"),
        }

    def shape_features(self, img):
        """形状特征 — 轮廓/矩形检测"""
        cv2 = __import__("cv2")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rect_count = 0; areas = []; img_area = img.shape[0] * img.shape[1]
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 50: continue
            areas.append(area)
            if len(cv2.approxPolyDP(cnt, 0.02 * cv2.arcLength(cnt, True), True)) == 4:
                rect_count += 1
        return {
            "contour_count": len(areas),
            "rect_count": rect_count,
            "coverage_ratio": round(sum(areas) / img_area, 4) if areas else 0,
            "has_large_shapes": any(a > img_area * 0.1 for a in areas),
        }

    def texture_features(self, img):
        """纹理特征"""
        cv2 = __import__("cv2")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return {
            "laplacian_variance": round(lap_var, 1),
            "texture_level": "high" if lap_var > 800 else ("medium" if lap_var > 200 else "low"),
        }

    def region_features(self, img, max_regions=20):
        """区域布局 — OTSU 二值化 + 连通域"""
        cv2 = __import__("cv2")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        img_area = gray.size
        regions = []
        for i in range(1, stats.shape[0]):
            area = stats[i, cv2.CC_STAT_AREA]
            if area > img_area * 0.001:
                regions.append({
                    "area": int(area),
                    "x": int(stats[i, cv2.CC_STAT_LEFT]),
                    "y": int(stats[i, cv2.CC_STAT_TOP]),
                    "w": int(stats[i, cv2.CC_STAT_WIDTH]),
                    "h": int(stats[i, cv2.CC_STAT_HEIGHT]),
                })
        regions.sort(key=lambda r: r["area"], reverse=True)
        regions = regions[:max_regions]
        n = len(regions)
        return {
            "region_count": n,
            "layout_complexity": "complex" if n > 15 else ("medium" if n > 5 else "simple"),
        }

    def extract_all(self, img):
        """全量特征提取"""
        return {
            "shape": img.shape[:2],
            "color": self.color_features(img),
            "edge": self.edge_features(img),
            "shape_features": self.shape_features(img),
            "texture": self.texture_features(img),
            "region": self.region_features(img),
        }

    def describe(self, features, yolo_detections=None):
        """把特征转成自然语言描述"""
        c = features["color"]; e = features["edge"]
        s = features["shape_features"]; t = features["texture"]; r = features["region"]
        label_set = set()
        if yolo_detections:
            label_set = {d["label"] for d in yolo_detections[:10]}
        parts = []
        if c["is_dark"]: parts.append("画面偏暗")
        elif c["is_bright"]: parts.append("画面很亮")
        else: parts.append("亮度适中")
        parts.append("色彩丰富" if c["is_colorful"] else "色彩偏素")
        if e["is_structured"]: parts.append("有结构化边缘(UI/文档)")
        elif e["edge_level"] == "high": parts.append("线条密集")
        else: parts.append("边缘柔和")
        if s["rect_count"] > 10: parts.append(f"包含{s['rect_count']}个矩形区域")
        parts.append(f"纹理:{t['texture_level']} 布局:{r['layout_complexity']}({r['region_count']}区域)")
        if label_set:
            parts.append(f"检测到: {', '.join(sorted(label_set)[:8])}")
        return "；".join(parts)


# ════════════════════════════════════════════
# L2: 特征→语义 (规则匹配)
# ════════════════════════════════════════════

class L2_SemanticMatcher:
    """视觉语义规则引擎 — 规则存于 brain_data/semantic/vision_*.json"""

    def __init__(self):
        self.rules = []
        self._load()

    def _load(self):
        sem_dir = BRAIN_DIR / "semantic"
        if not sem_dir.exists(): return
        for f in sorted(sem_dir.glob("vision_*.json")):
            try:
                rule = json.loads(f.read_text("utf-8"))
                if rule.get("status") == "active":
                    self.rules.append(rule)
            except: pass

    def add_rule(self, name, conditions, conclusion, confidence=0.7):
        """添加一条视觉语义规则并持久化"""
        rule_id = f"vision_{int(time.time()*1000)}_{len(self.rules)}"
        rule = {
            "id": rule_id, "domain": "vision", "name": name,
            "conditions": conditions, "conclusion": conclusion,
            "confidence": confidence, "hit_count": 0,
            "status": "active", "created_at": time.time(),
        }
        self.rules.append(rule)
        path = BRAIN_DIR / "semantic" / f"{rule_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rule, ensure_ascii=False, indent=2), encoding="utf-8")
        return rule_id

    def match(self, features):
        """对特征进行所有规则匹配"""
        matches = []
        for rule in self.rules:
            matched = True
            for path, (lo, hi) in rule["conditions"].items():
                val = self._deep_get(features, path)
                if val is None:
                    matched = False; break
                if not (str(lo) <= str(val) <= str(hi)):
                    matched = False; break
            if matched:
                rule["hit_count"] = rule.get("hit_count", 0) + 1
                matches.append({
                    "name": rule["name"],
                    "conclusion": rule["conclusion"],
                    "confidence": rule["confidence"],
                })
        matches.sort(key=lambda m: m["confidence"], reverse=True)
        return matches

    def _deep_get(self, d, path):
        for key in path.split("."):
            if isinstance(d, dict):
                d = d.get(key)
            else: return None
            if d is None: return None
        return d

    def status(self):
        return f"规则:{len(self.rules)} 活跃:{sum(1 for r in self.rules if r['status']=='active')}"

    def init_default_rules(self):
        """写入初始规则（如尚未存在）"""
        existing = {r.get("name") for r in self.rules}
        presets = [
            ("暗色IDE", {"color.is_dark":(True,True),"edge.is_structured":(True,True),"texture.texture_level":("high","high")}, "暗色主题代码编辑器或IDE界面", 0.85),
            ("亮色UI", {"color.is_bright":(True,True),"edge.is_structured":(True,True),"texture.texture_level":("medium","high")}, "亮色主题应用界面或网页", 0.80),
            ("桌面", {"color.is_colorful":(True,True),"region.layout_complexity":("medium","complex"),"edge.edge_density":(0.02,0.10)}, "桌面环境(壁纸+图标)", 0.75),
            ("文档密集", {"texture.laplacian_variance":(500,99999),"edge.hv_ratio":(0.5,2.0),"edge.is_structured":(True,True)}, "文字密集的文档或网页", 0.78),
            ("自然图片", {"texture.laplacian_variance":(50,500),"color.is_colorful":(True,True),"edge.is_structured":(False,False)}, "自然照片或图片", 0.72),
            ("纯色简约", {"texture.laplacian_variance":(0,100),"color.color_variance":(0,30),"region.region_count":(0,5)}, "大面积纯色背景或空白页", 0.80),
            ("暗色模式", {"color.is_dark":(True,True)}, "暗色或深色主题", 0.90),
            ("亮色模式", {"color.is_bright":(True,True)}, "亮色或浅色主题", 0.90),
        ]
        added = 0
        for name, cond, conc, conf in presets:
            if name not in existing:
                self.add_rule(name, cond, conc, conf)
                added += 1
        return added


# ════════════════════════════════════════════
# L3: 经验学习与规则强化
# ════════════════════════════════════════════

class L3_Learner:
    """视觉经验记录 + 规则置信度自调整"""

    def __init__(self):
        self.exp_dir = BRAIN_DIR / "experiences"

    def record(self, features, matches, yolo_detections=None, source="screenshot"):
        """记录一次视觉观察经验"""
        c = features["color"]
        e = features["edge"]
        t = features["texture"]
        fp = f"{round(c['brightness']/20)*20}|{round(e['edge_density'],3)}|{t['texture_level']}"
        record = {
            "id": hashlib.md5(f"{fp}{time.time()}".encode()).hexdigest()[:12],
            "timestamp": time.time(), "source": source, "fingerprint": fp,
            "matches": matches,
            "features": {
                "brightness": c["brightness"], "edge_density": e["edge_density"],
                "texture_level": t["texture_level"], "rect_count": features["shape_features"]["rect_count"],
                "region_count": features["region"]["region_count"],
            },
            "yolo": yolo_detections[:5] if yolo_detections else [],
        }
        path = self.exp_dir / f"vis_{record['id']}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return record

    def reinforce(self):
        """根据历史观察调整规则置信度"""
        sem_dir = BRAIN_DIR / "semantic"
        rules = []
        if sem_dir.exists():
            for f in sem_dir.glob("vision_*.json"):
                try: rules.append(json.loads(f.read_text("utf-8")))
                except: pass
        exps = []
        if self.exp_dir.exists():
            for f in self.exp_dir.glob("vis_*.json"):
                try: exps.append(json.loads(f.read_text("utf-8")))
                except: pass
        if not exps: return "尚无经验数据"
        updated = 0
        for rule in rules:
            hits = sum(1 for exp in exps for m in exp.get("matches",[]) if m.get("name") == rule.get("name"))
            if hits > 0:
                hit_rate = hits / len(exps)
                old = rule.get("confidence", 0.5)
                new = max(0.1, min(0.99, round(old * 0.7 + hit_rate * 0.3, 3)))
                if abs(new - old) > 0.01:
                    rule["confidence"] = new
                    rule["hit_count"] = rule.get("hit_count", 0) + hits
                    (sem_dir / f"{rule['id']}.json").write_text(
                        json.dumps(rule, ensure_ascii=False, indent=2), encoding="utf-8")
                    updated += 1
        return f"强化了 {updated}/{len(rules)} 条规则 ({len(exps)} 次观察)"

    def stats(self):
        sem_dir = BRAIN_DIR / "semantic"
        exp_dir = self.exp_dir
        exps = list(exp_dir.glob("vis_*.json")) if exp_dir.exists() else []
        rules = list(sem_dir.glob("vision_*.json")) if sem_dir.exists() else []
        last_ts = 0
        for e in exps:
            try:
                ts = json.loads(e.read_text("utf-8")).get("timestamp", 0)
                if ts > last_ts: last_ts = ts
            except: pass
        return {"observations": len(exps), "rules": len(rules), "last": last_ts}


# ════════════════════════════════════════════
# L4: 统一视觉引擎
# ════════════════════════════════════════════

class JavisVision:
    """Javis 视觉系统 — 统一入口"""

    def __init__(self):
        self.l1 = L1_FeatureExtractor()
        self.l2 = L2_SemanticMatcher()
        self.l3 = L3_Learner()
        # 初始化默认规则
        self.l2.init_default_rules()
        self._last_result = None

    def look(self, source=None):
        """
        看一次世界（L1+L2+L3+YOLO+Brain 全链路）
        source=None → 自动截屏
        source=路径 → 读文件
        source=numpy数组 → 直接分析
        """
        if source is None:
            try:
                from PIL import ImageGrab
                screen = ImageGrab.grab()
                img = np.array(screen)[:, :, ::-1].copy()
            except:
                import subprocess, tempfile
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
                try:
                    subprocess.run(["powershell", "-Command",
                        f"Add-Type -AssemblyName System.Windows.Forms; "
                        f"[System.Windows.Forms.Screen]::PrimaryScreen.Bounds | "
                        f"ForEach-Object {{$b=$_; "
                        f"$s=[Drawing.Graphics]::FromImage($img=[Drawing.Bitmap]::new($b.Width,$b.Height)); "
                        f"$s.CopyFromScreen($b.X,$b.Y,0,0,$b.Size); $img.Save('{tmp}')}}"
                    ], capture_output=True, timeout=10)
                    img = self.l1.load_image(tmp)
                finally:
                    try: os.unlink(tmp)
                    except: pass
        elif isinstance(source, str):
            img = self.l1.load_image(source)
        elif isinstance(source, np.ndarray):
            img = source.copy()
        else:
            raise ValueError("source 需要是 None/路径/numpy数组")

        # L1: 提取特征
        features = self.l1.extract_all(img)

        # YOLO 目标检测（通过 YoloManager）
        try:
            from tools.yolo_manager import get_yolo
            yolo_detections = get_yolo().detect(img)
        except Exception as e:
            logger.debug(f"YOLO 检测跳过: {e}")
            yolo_detections = []

        # L2: 语义匹配
        matches = self.l2.match(features)

        # L3: 记录经验
        self.l3.record(features, matches, yolo_detections)

        # 写入 brain facts（检测结果融入大脑）
        try:
            if yolo_detections:
                label_counts = {}
                for d in yolo_detections:
                    label_counts[d["label"]] = label_counts.get(d["label"], 0) + 1
                summary = "视觉检测: " + ", ".join(f"{k}×{v}" for k, v in sorted(label_counts.items())[:8])
                # 通过 brain 学习事实（如果不是工具调用环境，静默忽略）
                try:
                    from knowledge.brain import get_brain
                    brain = get_brain()
                    brain.learn_fact(summary, category="vision.detect", source="self", priority=2)
                except:
                    pass
        except:
            pass

        # 生成描述
        description = self.l1.describe(features, yolo_detections)

        self._last_result = {
            "description": description,
            "matches": matches,
            "yolo": yolo_detections,
        }
        return self._last_result

    def reinforce(self):
        """从视觉经验中学习，同时做 YOLO 训练数据收集"""
        result = self.l3.reinforce()
        # 返回包含 YOLO 状态
        try:
            from tools.yolo_manager import get_yolo
            yolo_status = get_yolo().status()
            result += f" | YOLO: {yolo_status['active_model']} ({yolo_status['training_samples']}训练样本)"
        except:
            pass
        return result

    def status(self):
        s = self.l3.stats()
        try:
            from tools.yolo_manager import get_yolo
            yolo_s = get_yolo().status()
            yolo_str = f"YOLO: {yolo_s['active_model']} ({yolo_s['training_samples']}训练样本)"
        except:
            yolo_str = "YOLO: 未加载"
        return (
            f"👁 Javis 视觉系统\n"
            f"  看过: {s['observations']} 次\n"
            f"  规则: {s['rules']} 条\n"
            f"  {yolo_str}\n"
            f"  最近: {time.strftime('%H:%M', time.localtime(s['last'])) if s['last'] else '无'}"
        )


# ════════════════════════════════════════════
# 工具接口 (供 manifest.py 注册) — 带自进化闭环
# ════════════════════════════════════════════

_vision = None

def describe_screen(path=None, source=None):
    """
    描述屏幕画面或指定图片的内容。
    自动收集训练数据 + 写入 Brain 记忆。

    Args:
        path: 图片文件路径，留空则自动截屏
        source: 兼容旧接口，直接传图片数据
    """
    global _vision
    if _vision is None:
        _vision = JavisVision()
        try:
            from tools.vision_engine import start_auto_train_monitor
            start_auto_train_monitor()
        except:
            pass

    # 统一参数：path 优先于 source
    input_source = path if path is not None else source
    result = _vision.look(input_source)

    # ★ 融合：将视觉数据+文字描述喂给 VisualLeaf 训练
    try:
        if _VISION_KERNEL is not None:
            description = result.get("description", "屏幕画面")
            if input_source is None:
                from PIL import ImageGrab
                visual_input = np.array(ImageGrab.grab())[:, :, ::-1].copy()
            else:
                visual_input = input_source
            _VISION_KERNEL.feed_visual_with_description(visual_input, description)
    except Exception:
        pass

    # 自动收集 YOLO 训练数据
    yolo_dets = result.get("yolo", [])
    if yolo_dets:
        try:
            from tools.vision_engine import auto_collect_coco, inject_to_brain
            if input_source is None:
                from PIL import ImageGrab
                import numpy as np
                img = np.array(ImageGrab.grab())[:, :, ::-1].copy()
            elif isinstance(input_source, str):
                import cv2, numpy as np
                buf = np.fromfile(input_source, dtype=np.uint8)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            else:
                img = input_source
            if isinstance(img, np.ndarray):
                auto_collect_coco(img, yolo_dets)
            inject_to_brain(yolo_dets)
        except Exception as e:
            logger.debug(f"自进化闭环异常: {e}")

    return result

def vision_status():
    """查看视觉系统状态"""
    global _vision
    if _vision is None:
        _vision = JavisVision()
    return _vision.status()

def vision_reinforce():
    """从视觉经验中学习强化规则"""
    global _vision
    if _vision is None:
        _vision = JavisVision()
    return _vision.reinforce()

def vision_switch_model(model_name="yolov8n"):
    """运行时切换 YOLO 检测模型"""
    from tools.yolo_manager import get_yolo
    get_yolo().switch_to(model_name)
    return f"已切换到 {model_name}"


def vision_auto_train():
    """手动触发增量训练"""
    from tools.vision_engine import incremental_train, training_status
    status = training_status()
    if status["training_in_progress"]:
        return "训练正在进行中，请等待完成"
    result = incremental_train()
    if result.get("trained"):
        return f"✅ 训练完成! mAP50={result['mAP50']:.4f} 总样本={result['samples']}"
    else:
        return f"⚠️ 训练未执行: {result.get('reason', result.get('error', '未知'))}"


def vision_training_status():
    """查看训练状态"""
    from tools.vision_engine import training_status
    s = training_status()
    lines = [
        f"📊 训练系统状态",
        f"  总样本: {s['total_samples']}",
        f"  上次训练后新增: {s['new_since_last_train']}",
        f"  自动训练阈值: {s['auto_train_threshold']}",
        f"  上次训练: {s['last_train']}",
        f"  上次 mAP50: {s['last_mAP50']}",
        f"  后台监控: {'运行中' if s['monitor_running'] else '未启动'}",
        f"  训练中: {'是' if s['training_in_progress'] else '否'}",
        f"  可训练: {'是（可手动触发）' if s['ready_to_train'] else '需更多数据' if s['new_since_last_train'] > 0 else '暂无新数据'}",
    ]
    return "\n".join(lines)
