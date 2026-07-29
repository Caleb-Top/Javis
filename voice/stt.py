"""Offline speech recognition backed by a packaged faster-whisper model."""

from __future__ import annotations

import base64
import importlib.util
import logging
import os
import tempfile
from pathlib import Path


logger = logging.getLogger("voice.stt")

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_MODEL_DIR = ROOT / "models" / "faster-whisper-base"
WHISPER_CACHE_DIR = (
    Path.home()
    / ".cache/huggingface/hub/models--Systran--faster-whisper-base/snapshots"
)

_model = None


def _resolve_model_source() -> Path:
    if (PACKAGED_MODEL_DIR / "model.bin").is_file():
        return PACKAGED_MODEL_DIR
    if WHISPER_CACHE_DIR.is_dir():
        candidates = sorted(
            (
                path
                for path in WHISPER_CACHE_DIR.iterdir()
                if (path / "model.bin").is_file()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]
    raise FileNotFoundError(
        "local faster-whisper base model is missing; Javis will not download it automatically"
    )


def get_diagnostics() -> dict:
    package_available = importlib.util.find_spec("faster_whisper") is not None
    try:
        model_source = _resolve_model_source()
    except FileNotFoundError:
        model_source = None
    return {
        "available": bool(package_available and model_source),
        "package_available": package_available,
        "model": "faster-whisper/base",
        "model_path": str(model_source) if model_source else "",
        "offline": True,
        "hardware_tested": False,
    }


def _get_model():
    global _model
    if _model is not None:
        return _model
    try:
        from faster_whisper import WhisperModel

        model_source = _resolve_model_source()
        _model = WhisperModel(
            str(model_source),
            device="cpu",
            compute_type="int8",
            local_files_only=True,
        )
        logger.info("Loaded local Whisper model from %s", model_source)
    except ImportError:
        logger.error("faster-whisper is unavailable in the packaged runtime")
    except Exception as exc:
        logger.error("Unable to load local Whisper model: %s", exc)
    return _model


def transcribe(audio_base64: str, language: str = "zh") -> str:
    """Transcribe browser WebM/Opus audio without network model resolution."""
    if not audio_base64:
        return ""

    model = _get_model()
    if model is None:
        return ""

    try:
        raw = base64.b64decode(audio_base64, validate=True)
    except Exception:
        logger.warning("STT rejected invalid base64 audio")
        return ""

    tmp_in = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    try:
        tmp_in.write(raw)
        tmp_in.close()
        segments, _ = model.transcribe(
            tmp_in.name,
            language=language if language != "auto" else None,
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        logger.info("STT: %s KB -> %r", len(raw) // 1024, text[:80])
        return text
    except Exception as exc:
        logger.error("STT transcription failed: %s", exc)
        return ""
    finally:
        try:
            os.unlink(tmp_in.name)
        except OSError:
            pass
