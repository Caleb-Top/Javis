"""OCR adapter for local image-to-text perception events."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable


class OcrAdapter:
    name = "ocr"

    def __init__(self, reader: Callable[[Any], list[dict[str, Any]]] | None = None):
        self._reader = reader

    def status(self) -> dict[str, Any]:
        try:
            self._load_pytesseract()
            return {"state": "available", "detail": "lazy OCR bridge; engine runs only during analysis"}
        except Exception as exc:
            return {"state": "degraded", "detail": str(exc)[:200]}

    def analyze(
        self,
        image: Any,
        perception: Any,
        source: str = "image",
        min_confidence: float = 0.3,
        max_tokens: int = 80,
    ) -> Any:
        raw_tokens = self._read(image)
        tokens = [
            {
                "text": str(item.get("text", "")).strip(),
                "confidence": float(item.get("confidence", 0.0)),
                "box": item.get("box"),
            }
            for item in raw_tokens
            if str(item.get("text", "")).strip() and float(item.get("confidence", 0.0)) >= min_confidence
        ][:max_tokens]
        text = " ".join(item["text"] for item in tokens).strip()
        confidence = self._mean_confidence(tokens)
        summary = f"OCR read: {text}" if text else "OCR read no text"
        return perception.ingest(
            source=source,
            modality="vision.ocr",
            summary=summary,
            confidence=confidence,
            metadata={
                "adapter": "ocr",
                "text": text,
                "tokens": tokens,
                "min_confidence": min_confidence,
            },
        )

    def _read(self, image: Any) -> list[dict[str, Any]]:
        if self._reader is not None:
            return self._reader(image)
        pytesseract = self._load_pytesseract()
        try:
            output_type = pytesseract.Output.DICT
        except AttributeError:
            from pytesseract import Output

            output_type = Output.DICT
        data = pytesseract.image_to_data(image, output_type=output_type)
        tokens: list[dict[str, Any]] = []
        texts = data.get("text", [])
        confs = data.get("conf", [])
        lefts = data.get("left", [])
        tops = data.get("top", [])
        widths = data.get("width", [])
        heights = data.get("height", [])
        for idx, text in enumerate(texts):
            try:
                confidence = float(confs[idx]) / 100.0
            except Exception:
                confidence = 0.0
            tokens.append(
                {
                    "text": text,
                    "confidence": confidence,
                    "box": [
                        int(lefts[idx]) if idx < len(lefts) else 0,
                        int(tops[idx]) if idx < len(tops) else 0,
                        int(widths[idx]) if idx < len(widths) else 0,
                        int(heights[idx]) if idx < len(heights) else 0,
                    ],
                }
            )
        return tokens

    @staticmethod
    def _load_pytesseract():
        import pytesseract

        try:
            from tools.setup import TESSERACT_EXE

            executable = Path(TESSERACT_EXE)
            if not executable.is_file():
                raise FileNotFoundError(f"Tesseract engine not found: {executable}")
            pytesseract.pytesseract.tesseract_cmd = str(executable)
            os.environ["TESSDATA_PREFIX"] = str(executable.parent / "tessdata")
        except ImportError:
            raise
        return pytesseract

    @staticmethod
    def _mean_confidence(tokens: list[dict[str, Any]]) -> float:
        if not tokens:
            return 0.0
        return round(sum(float(item.get("confidence", 0.0)) for item in tokens) / len(tokens), 4)
