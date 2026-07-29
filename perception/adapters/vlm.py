"""Local VLM adapter gate for image/video perception."""

from __future__ import annotations

import base64
import json
import mimetypes
import urllib.request
from pathlib import Path
from typing import Any, Callable


class OpenAICompatibleVlmDescriber:
    """Calls a local OpenAI-compatible vision endpoint such as Ollama."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 8.0,
        confidence: float = 0.72,
    ):
        self.base_url = str(base_url or "").rstrip("/")
        self.model = str(model or "local-vlm")
        self.api_key = str(api_key or "")
        self.timeout = max(0.1, float(timeout or 8.0))
        self.confidence = max(0.0, min(1.0, float(confidence)))

    def __call__(self, image: Any, prompt: str) -> dict[str, Any]:
        return self.describe(image, prompt)

    def describe(self, image: Any, prompt: str) -> dict[str, Any]:
        if not self.base_url:
            return self._failure("local VLM base_url is not configured")
        try:
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": str(prompt or "Describe this image.")},
                            {"type": "image_url", "image_url": {"url": self._image_to_data_url(image)}},
                        ],
                    }
                ],
                "temperature": 0,
                "max_tokens": 512,
            }
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            summary = self._extract_summary(body)
            if not summary:
                return self._failure("local VLM returned an empty description")
            return {"summary": summary, "confidence": self.confidence, "model": self.model}
        except Exception as exc:
            return self._failure(str(exc)[:200])

    def _image_to_data_url(self, image: Any) -> str:
        if isinstance(image, (str, Path)):
            path = Path(image)
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            raw = path.read_bytes()
        elif isinstance(image, bytes):
            mime = "image/jpeg"
            raw = image
        else:
            cv2 = __import__("cv2")
            ok, encoded = cv2.imencode(".jpg", image)
            if not ok:
                raise ValueError("could not encode image for local VLM")
            mime = "image/jpeg"
            raw = encoded.tobytes()
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    @staticmethod
    def _extract_summary(body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            return " ".join(part.strip() for part in parts if part.strip())
        return str(content).strip()

    def _failure(self, reason: str) -> dict[str, Any]:
        return {
            "summary": f"Local VLM backend unavailable: {reason}",
            "confidence": 0.0,
            "model": self.model,
            "error": reason,
        }


class LocalVlmAdapter:
    """Wraps a local vision-language describer behind the perception contract."""

    name = "vlm"

    def __init__(
        self,
        describer: Callable[[Any, str], dict[str, Any] | str] | None = None,
        model_name: str = "local-vlm",
        upgrade_threshold: float = 0.55,
    ):
        self.describer = describer
        self.model_name = model_name
        self.upgrade_threshold = max(0.0, min(1.0, float(upgrade_threshold)))

    def status(self) -> dict[str, Any]:
        return {
            "state": "available" if self.describer is not None else "not_configured",
            "detail": "local VLM describer gate",
            "model": self.model_name,
            "upgrade_threshold": self.upgrade_threshold,
        }

    def analyze(
        self,
        image: Any,
        perception: Any,
        source: str = "image",
        prompt: str = "Describe the visual scene for the reasoning core.",
    ) -> Any:
        result = self._describe(image, prompt)
        summary = str(result.get("summary", "")).strip()
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))
        model = str(result.get("model", self.model_name))
        if not summary:
            summary = "Local VLM adapter is not configured"
        return perception.ingest(
            source=source,
            modality="vision.vlm",
            summary=summary,
            confidence=confidence,
            metadata={
                "adapter": self.name,
                "model": model,
                "prompt": prompt,
                "needs_upgrade": confidence < self.upgrade_threshold,
                "upgrade_reason": "low local VLM confidence" if confidence < self.upgrade_threshold else "",
                "error": str(result.get("error", "")),
            },
        )

    def _describe(self, image: Any, prompt: str) -> dict[str, Any]:
        if self.describer is None:
            return {"summary": "", "confidence": 0.0, "model": self.model_name}
        result = self.describer(image, prompt)
        if isinstance(result, str):
            return {"summary": result, "confidence": 0.7, "model": self.model_name}
        return dict(result or {})
