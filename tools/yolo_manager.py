"""
Javis YOLO 模型管理器 — 多模型切换、推理、训练数据收集
=====================================================
功能:
  - 自动发现 tools/yolo/ 下所有 .onnx 模型
  - 运行时热切换模型 (nano ↔ large ↔ xl)
  - 标准 YOLOv8 ONNX 后处理 (NMS + 坐标缩放)
  - 收集检测数据供未来训练
  - 与 brain_data 深度绑定
"""

import os, json, time, logging, shutil
from pathlib import Path
import numpy as np

logger = logging.getLogger("yolo")

YOLO_DIR = Path(__file__).parent / "yolo"
YOLO_CONFIG = YOLO_DIR / "config.json"
TRAINING_DIR = YOLO_DIR / "training"

COCO_LABELS = [
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


class YoloManager:
    """YOLO 模型管理器"""

    def __init__(self):
        self._net = None
        self._active_model = None
        self._models = {}  # name -> path
        self._discover()
        self._load_active()

    def _discover(self):
        """自动发现 tools/yolo/ 下所有 .onnx 模型"""
        self._models = {}
        for f in sorted(YOLO_DIR.glob("*.onnx")):
            name = f.stem
            size_mb = f.stat().st_size / (1024 * 1024)
            self._models[name] = {
                "path": str(f),
                "size_mb": round(size_mb, 1),
                "description": f"YOLOv8 {name.replace('yolov8','')} detection" if "yolov8" in name else name,
            }
        logger.info(f"YOLO 模型库: {len(self._models)} 个模型 {', '.join(self._models.keys())}")

    def _load_active(self):
        """加载活跃模型（默认用最小的 yolov8n）"""
        # 优先加载最小的模型
        preferred = ["yolov8n", "yolov8s", "yolov8m", "yolov8l", "yolov8x"]
        for name in preferred:
            if name in self._models:
                self.switch_to(name)
                return
        # 退回到任何可用模型
        if self._models:
            first = list(self._models.keys())[0]
            self.switch_to(first)

    def switch_to(self, model_name):
        """热切换到指定模型"""
        if model_name not in self._models:
            available = list(self._models.keys())
            raise ValueError(f"模型 '{model_name}' 未找到. 可用: {available}")
        path = self._models[model_name]["path"]
        try:
            cv2 = __import__("cv2")
            net = cv2.dnn.readNetFromONNX(path)
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self._net = net
            self._active_model = model_name
            logger.info(f"YOLO 切换到: {model_name} ({self._models[model_name]['size_mb']}MB)")
        except Exception as e:
            logger.error(f"YOLO 加载失败 {model_name}: {e}")
            raise

    def detect(self, img, conf_threshold=0.25, iou_threshold=0.45):
        """
        对图像执行 YOLO 检测
        返回: [{"label":str, "confidence":float, "x":int, "y":int, "w":int, "h":int}, ...]
        """
        if self._net is None:
            return []

        cv2 = __import__("cv2")
        h, w = img.shape[:2]
        blob = cv2.dnn.blobFromImage(img, 1/255.0, (640, 640), swapRB=True, crop=False)
        self._net.setInput(blob)
        outputs = self._net.forward()

        # YOLOv8 ONNX output: (1, 84, 8400) -> transpose to (8400, 84)
        outputs = outputs[0].transpose()

        boxes = []
        for det in outputs:
            scores = det[4:]
            class_id = int(np.argmax(scores))
            confidence = float(scores[class_id])
            if confidence < conf_threshold:
                continue
            cx, cy, bw, bh = det[:4]
            # coords are normalized to 640x640 input
            x1 = int((cx - bw / 2) * w / 640)
            y1 = int((cy - bh / 2) * h / 640)
            bw_px = int(bw * w / 640)
            bh_px = int(bh * h / 640)
            boxes.append({
                "label": COCO_LABELS[class_id] if class_id < len(COCO_LABELS) else f"c{class_id}",
                "confidence": round(confidence, 2),
                "x": x1, "y": y1, "w": bw_px, "h": bh_px,
            })

        # NMS
        if len(boxes) > 1:
            nms_data = np.array([[b['x'], b['y'], b['x']+b['w'], b['y']+b['h']] for b in boxes])
            confs = np.array([b['confidence'] for b in boxes])
            indices = cv2.dnn.NMSBoxes(nms_data.tolist(), confs.tolist(), conf_threshold, iou_threshold)
            if len(indices) > 0:
                indices = indices.flatten()
                boxes = [boxes[i] for i in indices]

        return boxes

    def collect_training_data(self, img, boxes, source="auto"):
        """收集检测数据用于未来模型训练 (YOLO 格式)"""
        TRAINING_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        img_path = TRAINING_DIR / f"img_{ts}.jpg"
        label_path = TRAINING_DIR / f"img_{ts}.txt"

        # 保存图像
        cv2 = __import__("cv2")
        cv2.imwrite(str(img_path), img)

        # 保存 YOLO 格式标签
        h, w = img.shape[:2]
        lines = []
        for b in boxes:
            class_id = COCO_LABELS.index(b["label"]) if b["label"] in COCO_LABELS else 0
            cx = (b["x"] + b["w"] / 2) / w
            cy = (b["y"] + b["h"] / 2) / h
            nw = b["w"] / w
            nh = b["h"] / h
            lines.append(f"{class_id} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
        label_path.write_text("\n".join(lines), encoding="utf-8")

        return {"image": str(img_path), "labels": str(label_path), "timestamp": ts}

    def status(self):
        """返回模型状态"""
        return {
            "active_model": self._active_model,
            "models": {k: v["size_mb"] for k, v in self._models.items()},
            "training_samples": len(list(TRAINING_DIR.glob("*.txt"))) if TRAINING_DIR.exists() else 0,
        }


# 全局单例
_yolo = None

def get_yolo():
    global _yolo
    if _yolo is None:
        _yolo = YoloManager()
    return _yolo
