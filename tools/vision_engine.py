"""
Javis 视觉自进化引擎 — 闭环核心
=================================
职责:
  1. 每次 describe_screen() → 自动收集 YOLO 格式训练数据
  2. 后台监控训练数据量 → 超阈值自动增量训练
  3. 训练完成 → 热加载新模型
  4. 检测结果 → 写入 brain_data 记忆系统
  5. 提供统一的状态查询接口

依赖:
  - tools/yolo_manager.py  (COCO + UI 模型管理)
  - tools/javis_vision.py  (L1-L4 视觉皮层)
  - knowledge/brain.py     (大脑记忆)
  - ultralytics YOLO       (训练引擎)
"""

import os, json, time, logging, threading, hashlib, shutil
from pathlib import Path
from collections import Counter

logger = logging.getLogger("vision_engine")

YOLO_DIR = Path(__file__).parent / "yolo"
UI_TRAIN_DIR = Path(__file__).parent.parent / "tools" / "cvu_data" / "ui_training"
BRAIN_DIR = Path(__file__).parent.parent / "brain_data"
PROJECT_ROOT = Path(__file__).parent.parent

UI_CLASSES = [
    'Button','Text','Heading','Link','Image','Icon','Input','TextArea',
    'Checkbox','Radio','Switch','Slider','Dropdown','NavigationBar','TabBar',
    'Tab','Toolbar','Sidebar','StatusBar','Card','Modal','Dialog','Menu',
    'MenuItem','SearchField','ProgressBar','Spinner','Banner','Alert',
    'List','ListItem','Table','Divider','Window','AppIcon','Screenshot'
]
UI_CLASS_MAP = {name: i for i, name in enumerate(UI_CLASSES)}

AUTO_TRAIN_THRESHOLD = 200      # 新增样本达到此数量自动训练
MONITOR_INTERVAL = 600           # 后台监控间隔 (秒)
TRAIN_LOCK = threading.Lock()    # 训练互斥锁


# ════════════════════════════════════════════
# Phase 1 & 2: 自动收集训练数据
# ════════════════════════════════════════════

def auto_collect_coco(img, detections, source="auto"):
    """
    每次视觉检测后自动收集 YOLO 格式训练数据
    返回: {"saved": bool, "total_count": int}
    """
    from tools.yolo_manager import COCO_LABELS
    if not detections:
        return {"saved": False, "total_count": 0}

    train_img_dir = UI_TRAIN_DIR / "images" / "train"
    train_lbl_dir = UI_TRAIN_DIR / "labels" / "train"
    train_img_dir.mkdir(parents=True, exist_ok=True)
    train_lbl_dir.mkdir(parents=True, exist_ok=True)

    import cv2
    ts = int(time.time() * 1000)
    h, w = img.shape[:2]

    yolo_lines = []
    for d in detections:
        label = d.get("label", "")
        if label in COCO_LABELS:
            cid = COCO_LABELS.index(label)
            cx = (d["x"] + d["w"] / 2) / w
            cy = (d["y"] + d["h"] / 2) / h
            yolo_lines.append(f"{cid} {cx:.6f} {cy:.6f} {d['w']/w:.6f} {d['h']/h:.6f}")

    if yolo_lines:
        cv2.imwrite(str(train_img_dir / f"chat_{ts}.jpg"), img)
        (train_lbl_dir / f"chat_{ts}.txt").write_text("\n".join(yolo_lines), encoding="utf-8")

        total = len(list(train_lbl_dir.glob("*.txt")))
        return {"saved": True, "total_count": total}

    return {"saved": False, "total_count": len(list(train_lbl_dir.glob("*.txt")))}


def auto_collect_ui(img, ui_elements, source="auto"):
    """
    收集 UI 检测结果为训练数据 (UI 类别)
    """
    if not ui_elements:
        return {"saved": False, "total_count": 0}

    train_img_dir = UI_TRAIN_DIR / "images" / "train"
    train_lbl_dir = UI_TRAIN_DIR / "labels" / "train"
    train_img_dir.mkdir(parents=True, exist_ok=True)
    train_lbl_dir.mkdir(parents=True, exist_ok=True)

    import cv2
    ts = int(time.time() * 1000)
    h, w = img.shape[:2]

    yolo_lines = []
    for e in ui_elements:
        cls = e.get("class", "")
        if cls in UI_CLASS_MAP:
            cid = UI_CLASS_MAP[cls]
            cx = (e["x"] + e["w"] / 2) / w
            cy = (e["y"] + e["h"] / 2) / h
            yolo_lines.append(f"{cid} {cx:.6f} {cy:.6f} {e['w']/w:.6f} {e['h']/h:.6f}")

    if yolo_lines:
        cv2.imwrite(str(train_img_dir / f"ui_{ts}.jpg"), img)
        (train_lbl_dir / f"ui_{ts}.txt").write_text("\n".join(yolo_lines), encoding="utf-8")
        return {"saved": True, "total_count": len(list(train_lbl_dir.glob("*.txt")))}

    return {"saved": False, "total_count": 0}


# ════════════════════════════════════════════
# Phase 3: 增量训练引擎
# ════════════════════════════════════════════

def get_training_data_count():
    """获取当前训练数据总量"""
    lbl_dir = UI_TRAIN_DIR / "labels" / "train"
    if not lbl_dir.exists():
        return 0
    return len(list(lbl_dir.glob("*.txt")))


def get_previous_training_count():
    """获取上次训练时的数据量"""
    meta_file = UI_TRAIN_DIR / "training_meta.json"
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text("utf-8")).get("last_count", 0)
        except:
            pass
    return 0


def save_training_meta(epochs, mAP50):
    """保存训练元数据"""
    meta = {
        "last_count": get_training_data_count(),
        "last_train_time": time.time(),
        "epochs": epochs,
        "mAP50": mAP50,
    }
    (UI_TRAIN_DIR / "training_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def incremental_train():
    """
    增量训练 UI 检测模型
    从已有 last.pt 恢复，只跑 10 个 epoch
    """
    with TRAIN_LOCK:
        current_count = get_training_data_count()
        prev_count = get_previous_training_count()
        new_samples = current_count - prev_count

        if new_samples < AUTO_TRAIN_THRESHOLD and prev_count > 0:
            logger.info(f"增量训练跳过: 新增{new_samples} < 阈值{AUTO_TRAIN_THRESHOLD}")
            return {"trained": False, "reason": "数据不足"}

        logger.info(f"🚀 开始增量训练: 总计{current_count}样本 (+{new_samples}新增)")

        try:
            from ultralytics import YOLO

            # 优先从上次训练权重恢复
            last_ckpt = YOLO_DIR / "ui_yolov8n_last.pt"
            trained = YOLO_DIR / "ui_yolov8n.pt"

            if last_ckpt.exists():
                model = YOLO(str(last_ckpt))
                logger.info(f"从上次检查点恢复: {last_ckpt}")
            elif trained.exists():
                model = YOLO(str(trained))
                logger.info(f"从已训练模型恢复: {trained}")
            else:
                logger.info("无已有权重，从预训练模型开始")
                model = YOLO("yolov8n.pt")

            # 增量训练 (10 epochs)
            results = model.train(
                data=str(UI_TRAIN_DIR / "ui_dataset.yaml"),
                epochs=10,
                imgsz=640,
                batch=8,
                device=0,
                project=str(PROJECT_ROOT / "train_output"),
                name="incremental",
                exist_ok=True,
                patience=5,
                workers=0,
                verbose=False,
            )

            # 获取最终 mAP
            csv_path = PROJECT_ROOT / "train_output/incremental/results.csv"
            mAP50 = 0
            if csv_path.exists():
                lines = csv_path.read_text().strip().split('\n')
                if len(lines) > 1:
                    last_line = lines[-1]
                    mAP50 = float(last_line.split(',')[7])  # metrics/mAP50(B)

            # 保存新模型并热切换
            best = Path(str(PROJECT_ROOT / "train_output/incremental/weights/best.pt"))
            if best.exists():
                shutil.copy(str(best), str(trained))
                shutil.copy(str(best), str(last_ckpt))
                logger.info(f"✅ 增量训练完成! mAP50={mAP50:.4f}")
            else:
                # 尝试其他路径
                for candidate in (PROJECT_ROOT / "train_output").rglob("best.pt"):
                    shutil.copy(str(candidate), str(trained))
                    shutil.copy(str(candidate), str(last_ckpt))
                    logger.info(f"✅ 模型已保存 (来自 {candidate})")
                    break

            # 保存元数据
            save_training_meta(10, mAP50)

            # 热加载 (通知下次推理用新模型)
            from tools.yolo_manager import get_yolo
            yolo = get_yolo()
            if trained.exists():
                # 注册 UI 模型到管理器
                yolo._models["ui_yolov8n"] = {
                    "path": str(trained),
                    "size_mb": round(trained.stat().st_size / 1024 / 1024, 1),
                    "description": "UI detection (auto-trained)"
                }

            return {"trained": True, "mAP50": mAP50, "samples": current_count}

        except Exception as e:
            logger.error(f"❌ 增量训练失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"trained": False, "error": str(e)}


# ════════════════════════════════════════════
# Phase 4: Brain 记忆融合
# ════════════════════════════════════════════

def inject_to_brain(yolo_detections, ui_elements=None):
    """
    将检测结果写入 brain_data 记忆系统
    - 检测摘要 → brain facts
    - UI 元素 → brain experiences
    """
    try:
        from knowledge.brain import get_brain
        brain = get_brain()
    except:
        return

    # YOLO 检测 → 写入 fact
    if yolo_detections:
        counts = Counter(d["label"] for d in yolo_detections)
        summary = "视觉检测: " + ", ".join(f"{k}×{v}" for k, v in counts.most_common(10))
        try:
            brain.learn_fact(summary, category="vision.detect", source="self", priority=2)
        except:
            pass

    # UI 元素 → 写入经验
    if ui_elements:
        types = set(e.get("class", "") for e in ui_elements)
        if types:
            summary = f"界面元素: {', '.join(sorted(types)[:10])}"
            try:
                brain.learn_fact(summary, category="vision.ui", source="self", priority=2)
            except:
                pass


# ════════════════════════════════════════════
# 后台监控线程
# ════════════════════════════════════════════

_monitor_running = False
_monitor_thread = None


def start_auto_train_monitor():
    """启动后台训练监控线程"""
    global _monitor_running, _monitor_thread
    if _monitor_running:
        return
    _monitor_running = True
    _monitor_thread = threading.Thread(target=_monitor_loop, daemon=True, name="vision-train-monitor")
    _monitor_thread.start()
    logger.info(f"🔍 视觉自训练监控已启动 (每{MONITOR_INTERVAL}秒)")


def _monitor_loop():
    """后台循环: 检查训练数据量 → 超阈值 → 自动训练"""
    global _monitor_running
    while _monitor_running:
        time.sleep(MONITOR_INTERVAL)
        try:
            current = get_training_data_count()
            prev = get_previous_training_count()
            new_samples = current - prev

            if prev > 0 and new_samples >= AUTO_TRAIN_THRESHOLD:
                logger.info(f"⚡ 触发增量训练: {new_samples}新样本 (阈值{AUTO_TRAIN_THRESHOLD})")
                result = incremental_train()
                if result.get("trained"):
                    logger.info(f"✅ 后台增量训练成功: mAP50={result.get('mAP50', '?')}")
                else:
                    logger.warning(f"⚠️ 后台训练未执行: {result.get('reason', '未知')}")
            elif prev == 0 and current > 100:
                pass  # 首次训练由用户手动触发
        except Exception as e:
            logger.debug(f"监控循环异常: {e}")


def stop_auto_train_monitor():
    """停止后台监控"""
    global _monitor_running
    _monitor_running = False


# ════════════════════════════════════════════
# 状态查询
# ════════════════════════════════════════════

def training_status():
    """获取训练状态"""
    current = get_training_data_count()
    prev = get_previous_training_count()
    new_samples = current - prev

    meta_file = UI_TRAIN_DIR / "training_meta.json"
    meta = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text("utf-8"))
        except:
            pass

    last_train = meta.get("last_train_time", 0)

    return {
        "total_samples": current,
        "new_since_last_train": new_samples,
        "auto_train_threshold": AUTO_TRAIN_THRESHOLD,
        "ready_to_train": new_samples >= AUTO_TRAIN_THRESHOLD if prev > 0 else current >= 100,
        "last_train": time.strftime('%Y-%m-%d %H:%M', time.localtime(last_train)) if last_train else "从未",
        "last_mAP50": meta.get("mAP50", "N/A"),
        "monitor_running": _monitor_running,
        "training_in_progress": TRAIN_LOCK.locked(),
    }
