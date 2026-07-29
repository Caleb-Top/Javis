"""YOLO adapter for the perception event bridge."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable


class YoloDetectionAdapter:
    name = "yolo"

    def __init__(self, manager_factory: Callable[[], Any] | None = None, model_dirs: list[Path] | None = None):
        self._manager_factory = manager_factory
        self._model_dirs = model_dirs

    def status(self) -> dict[str, Any]:
        models = self._discover_models()
        return {
            "state": "available",
            "detail": "lazy YOLO bridge; model loads only during detection",
            "models": list(models)[:20],
            "model_count": len(models),
        }

    def detect(
        self,
        image: Any,
        perception: Any,
        source: str = "image",
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
    ) -> Any:
        manager = self._get_manager()
        detections = manager.detect(
            image,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
        )
        summary = self._summarize(detections)
        return perception.ingest(
            source=source,
            modality="vision.object_detection",
            summary=summary,
            confidence=self._mean_confidence(detections),
            metadata={
                "detector": "yolo",
                "objects": detections,
                "thresholds": {
                    "confidence": conf_threshold,
                    "iou": iou_threshold,
                },
            },
        )

    def _get_manager(self) -> Any:
        if self._manager_factory is not None:
            manager = self._manager_factory()
        else:
            from tools.yolo_manager import get_yolo

            manager = get_yolo()
        if manager is None:
            raise RuntimeError("YOLO manager is unavailable")
        return manager

    def _discover_model_files(self) -> list[Path]:
        if self._model_dirs is not None:
            dirs = self._model_dirs
        else:
            try:
                from tools.yolo_manager import YOLO_DIR

                dirs = [Path(YOLO_DIR)]
            except Exception:
                dirs = []
        files: list[Path] = []
        for directory in dirs:
            if not directory.exists():
                continue
            for pattern in ("*.onnx", "*.pt", "*.engine", "*.tflite", "*.weights"):
                files.extend(sorted(directory.glob(pattern)))
        return files

    def _discover_models(self) -> dict[str, Any]:
        if self._model_dirs is not None:
            return {path.stem: path for path in self._discover_model_files()}
        try:
            from tools.yolo_manager import YoloManager

            return YoloManager(autoload=False).status().get("models", {})
        except Exception:
            return {path.stem: path for path in self._discover_model_files()}

    @staticmethod
    def _summarize(detections: list[dict[str, Any]]) -> str:
        if not detections:
            return "YOLO detected no objects"
        counts = Counter(str(item.get("label", "object")) for item in detections)
        return "YOLO detected " + ", ".join(f"{label} x{count}" for label, count in sorted(counts.items()))

    @staticmethod
    def _mean_confidence(detections: list[dict[str, Any]]) -> float:
        if not detections:
            return 0.0
        values = [float(item.get("confidence", 0.0)) for item in detections]
        return round(sum(values) / len(values), 4)
