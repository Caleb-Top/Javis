"""
Javis Computer Use Vision — 多模型融合视觉推理系统
=================================================
架构:
  Multi-Model Pipeline:
    ├── YOLOv8 COCO (80类通用物体)      → tools/yolo/yolov8*.onnx
    ├── UI Detection (55类UI元素)        → 训练管道就绪(ScreenParse)
    ├── OCR (Tesseract)                 → 文字识别
    └── Layout Analysis (OpenCV)        → 区域布局

  数据流: screen → COCO+UI+OCR+Layout → 融合 → 结构化场景 → brain_data/
  训练: ScreenParse数据集 → YOLO UI训练 → 自主数据收集 → 增量训练
"""

import os, json, time, logging, subprocess, shutil
from pathlib import Path
import numpy as np

logger = logging.getLogger("cvu")

BASE = Path(__file__).parent.parent
YOLO_DIR = BASE / "tools" / "yolo"
SCREENPARSE_DIR = BASE / "tools" / "cvu_data" / "screenparse"
UI_TRAIN_DIR = BASE / "tools" / "cvu_data" / "ui_training"

# 55 ScreenParse 类别
SCREENPARSE_CLASSES = [
    "Button","Text","Heading","Link","Image","Icon","Input","TextArea",
    "Checkbox","Radio","Switch","Slider","Dropdown","List","ListItem",
    "Table","TableCell","Menu","MenuItem","NavigationBar","TabBar","Tab",
    "Toolbar","Sidebar","StatusBar","Card","Modal","Popover","Tooltip",
    "Banner","Alert","ProgressBar","Spinner","Avatar","Badge","Chip",
    "Divider","SearchField","DatePicker","TimePicker","ColorPicker",
    "Map","Video","Chart","Canvas","Advertisement","Header","Footer",
    "Section","Container","Form","Dialog","Window","AppIcon","Screenshot"
]

UI_TARGET_CLASSES = [
    "Button","Text","Input","Icon","Image","Link","Menu","Navigation","Tab",
    "Checkbox","Radio","Switch","Slider","Dropdown","Dialog","Modal",
    "Toolbar","SearchField","Card","Banner","StatusBar","Window"
]


class ScreenParseManager:
    """ScreenParse 数据集下载管理"""

    HF_DATASET = "docling-project/screenparse"

    def __init__(self):
        self.parquet_dir = SCREENPARSE_DIR / "parquet"
        self.image_dir = SCREENPARSE_DIR / "images"
        self.label_dir = SCREENPARSE_DIR / "labels"

    def list_files(self):
        """列出数据文件"""
        import requests
        r = requests.get(
            f"https://huggingface.co/api/datasets/{self.HF_DATASET}",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15
        )
        if r.status_code != 200:
            return []
        siblings = r.json().get("siblings", [])
        return [s["rfilename"] for s in siblings
                if s["rfilename"].endswith(".parquet") and s["rfilename"].startswith("train/")]

    def download_metadata(self, max_files=5):
        """下载标注数据"""
        self.parquet_dir.mkdir(parents=True, exist_ok=True)
        files = self.list_files()[:max_files]
        downloaded = 0
        import requests
        for rel_path in files:
            dest = self.parquet_dir / os.path.basename(rel_path)
            if dest.exists():
                downloaded += 1
                continue
            url = f"https://huggingface.co/datasets/{self.HF_DATASET}/resolve/main/{rel_path}"
            try:
                r = requests.get(url, stream=True, timeout=300)
                if r.status_code == 200:
                    with open(dest, 'wb') as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    downloaded += 1
            except:
                pass
        return downloaded


class YOLODataCollector:
    """自主数据收集"""

    def collect(self, samples=5):
        """自动截屏+COCO标注"""
        from tools.yolo_manager import get_yolo, COCO_LABELS
        import cv2
        from PIL import ImageGrab

        out_dir = UI_TRAIN_DIR / "images" / "train"
        out_dir.mkdir(parents=True, exist_ok=True)
        label_dir = UI_TRAIN_DIR / "labels" / "train"
        label_dir.mkdir(parents=True, exist_ok=True)

        yolo = get_yolo()
        collected = 0
        for i in range(samples):
            try:
                screen = np.array(ImageGrab.grab())[:, :, ::-1].copy()
            except:
                continue
            ts = int(time.time())
            cv2.imwrite(str(out_dir / f"auto_{ts}.jpg"), screen)
            h, w = screen.shape[:2]
            dets = yolo.detect(screen)
            lines = []
            for d in dets:
                if d["label"] in COCO_LABELS:
                    cid = COCO_LABELS.index(d["label"])
                    cx = (d["x"]+d["w"]/2)/w
                    cy = (d["y"]+d["h"]/2)/h
                    lines.append(f"{cid} {cx:.6f} {cy:.6f} {d['w']/w:.6f} {d['h']/h:.6f}")
            if lines:
                (label_dir / f"auto_{ts}.txt").write_text("\n".join(lines), encoding="utf-8")
                collected += 1
        return collected


class ComputerUseAnalyzer:
    """多模型融合推理 + 自进化数据收集"""

    def __init__(self):
        self._ui_loaded = False
        self._ui_model = None
        self._ui_classes = UI_TARGET_CLASSES
        self._try_load_ui()
        # 启动后台训练监控
        try:
            from tools.vision_engine import start_auto_train_monitor
            start_auto_train_monitor()
        except:
            pass

    def _try_load_ui(self):
        """尝试加载UI检测模型"""
        for name in ["ui_yolov8n", "ui_yolov8l", "ui_yolov8x"]:
            p = YOLO_DIR / f"{name}.pt"
            if p.exists():
                try:
                    from ultralytics import YOLO
                    self._ui_model = YOLO(str(p))
                    self._ui_loaded = True
                    self._ui_classes = self._ui_model.names if hasattr(self._ui_model, 'names') else UI_TARGET_CLASSES
                    logger.info(f"UI model loaded: {name}")
                    return
                except Exception as e:
                    logger.debug(f"UI model load failed {name}: {e}")

    def analyze(self, img):
        """完整分析一帧画面 + 自动收集训练数据 + 写入Brain"""
        import cv2
        from tools.yolo_manager import get_yolo

        h, w = img.shape[:2]
        result = {
            "timestamp": time.time(),
            "resolution": f"{w}x{h}",
            "objects": [], "ui_elements": [], "text": [],
            "layout": {}, "scene": ""
        }

        # COCO物体
        try:
            result["objects"] = get_yolo().detect(img)[:15]
        except:
            pass

        # UI元素
        if self._ui_loaded and self._ui_model:
            try:
                r = self._ui_model(img)
                if r[0].boxes is not None:
                    for box in r[0].boxes:
                        x1,y1,x2,y2 = map(int, box.xyxy[0].tolist())
                        cid = int(box.cls[0])
                        conf = float(box.conf[0])
                        if conf > 0.15:
                            cls_name = self._ui_classes[cid] if isinstance(self._ui_classes, dict) and cid in self._ui_classes else (self._ui_classes[cid] if cid < len(self._ui_classes) else f"c{cid}")
                            result["ui_elements"].append({
                                "class": cls_name,
                                "conf": round(conf,2), "x":x1, "y":y1, "w":x2-x1, "h":y2-y1
                            })
            except:
                pass

        # OCR
        try:
            import pytesseract
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            d = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
            for i, t in enumerate(d.get("text",[])):
                if t.strip() and int(d.get("conf",[0])[i] or 0) > 30:
                    result["text"].append({
                        "text": t.strip(), "x": d["left"][i], "y": d["top"][i],
                        "w": d["width"][i], "h": d["height"][i]
                    })
            result["text"] = result["text"][:15]
        except:
            pass

        # 布局
        try:
            _, binary = cv2.threshold(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
            _, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
            n = sum(1 for i in range(1,stats.shape[0]) if stats[i,cv2.CC_STAT_AREA] > h*w*0.001)
            result["layout"] = {"regions": n, "complexity": "complex" if n>15 else "medium" if n>5 else "simple"}
        except:
            pass

        # 融合描述
        parts = []
        ol = list({d["label"] for d in result["objects"][:5]})
        if ol: parts.append(f"物体: {', '.join(ol)}")
        ul = list(set(e["class"] for e in result["ui_elements"][:8]))
        if ul: parts.append(f"界面: {', '.join(ul)}")
        ts = [t["text"][:15] for t in result["text"][:3]]
        if ts: parts.append(f"文字: {', '.join(ts)}")
        result["scene"] = " | ".join(parts)

        # 自动收集训练数据 + 写入Brain
        try:
            from tools.vision_engine import auto_collect_coco, auto_collect_ui, inject_to_brain
            auto_collect_coco(img, result["objects"])
            if result["ui_elements"]:
                auto_collect_ui(img, result["ui_elements"])
            inject_to_brain(result["objects"], result["ui_elements"])
        except:
            pass

        return result

    def status(self):
        return {
            "ui_loaded": self._ui_loaded,
            "coco_models": [f.stem for f in YOLO_DIR.glob("yolov8*.onnx")],
            "training_dir": str(SCREENPARSE_DIR),
        }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "download":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        m = ScreenParseManager()
        c = m.download_metadata(max_files=n)
        print(f"下载: {c} 个 parquet 文件")

    elif cmd == "collect":
        c = YOLODataCollector()
        n = c.collect(samples=5)
        print(f"收集: {n} 个训练样本")

    elif cmd == "analyze":
        from PIL import ImageGrab
        a = ComputerUseAnalyzer()
        screen = np.array(ImageGrab.grab())[:, :, ::-1].copy()
        r = a.analyze(screen)
        print(f"场景: {r['scene']}")
        print(f"UI元素: {len(r['ui_elements'])}")
        print(f"文字: {len(r['text'])}")
        print(f"物体: {len(r['objects'])}")

    elif cmd == "status":
        a = ComputerUseAnalyzer()
        s = a.status()
        print("=== Computer Use Vision ===")
        print(f"  UI模型: {'加载' if s['ui_loaded'] else '未加载'}")
        for m in s['coco_models']:
            print(f"  COCO: {m}")
        print(f"\n  训练管道就绪:")
        print(f"    download  — 下载 ScreenParse 55类UI数据")
        print(f"    collect   — 自主截图收集训练数据")
        print(f"    analyze   — 实时画面分析")
        print(f"\n  训练流程:")
        print(f"    pip install ultralytics pyarrow")
        print(f"    python tools/cvu.py download 50")
        print(f"    python tools/cvu.py analyze")
