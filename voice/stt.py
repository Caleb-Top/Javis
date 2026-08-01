"""Offline speech recognition backed by a packaged faster-whisper model."""

from __future__ import annotations

import base64
import io
import importlib.util
import logging
import os
import tempfile
import threading
import time
import wave
from pathlib import Path


logger = logging.getLogger("voice.stt")

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_MODEL_DIR = ROOT / "models" / "faster-whisper-base"
WHISPER_CACHE_DIR = (
    Path.home()
    / ".cache/huggingface/hub/models--Systran--faster-whisper-base/snapshots"
)

_model = None
_model_lock = threading.Lock()
_model_load_seconds = 0.0


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
        "loaded": _model is not None,
        "load_seconds": round(_model_load_seconds, 3),
        "hardware_tested": False,
    }


def _get_model():
    global _model, _model_load_seconds
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        started = time.monotonic()
        try:
            from faster_whisper import WhisperModel

            model_source = _resolve_model_source()
            _model = WhisperModel(
                str(model_source),
                device="cpu",
                compute_type="int8",
                cpu_threads=max(2, min(8, os.cpu_count() or 4)),
                num_workers=1,
                local_files_only=True,
            )
            logger.info("Loaded local Whisper model from %s", model_source)
        except ImportError:
            logger.error("faster-whisper is unavailable in the packaged runtime")
        except Exception as exc:
            logger.error("Unable to load local Whisper model: %s", exc)
        finally:
            _model_load_seconds = time.monotonic() - started
    return _model


def preload_model() -> bool:
    """Load the local recognizer before the first spoken turn."""
    return _get_model() is not None


def _transcribe_bytes(
    raw: bytes,
    *,
    suffix: str,
    language: str,
    beam_size: int,
    vad_filter: bool,
) -> str:
    model = _get_model()
    if model is None or not raw:
        return ""
    tmp_in = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp_in.write(raw)
        tmp_in.close()
        segments, _ = model.transcribe(
            tmp_in.name,
            language=language if language != "auto" else None,
            beam_size=beam_size,
            vad_filter=vad_filter,
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


def transcribe_pcm(
    pcm: bytes,
    *,
    sample_rate: int = 16_000,
    language: str = "zh",
    final: bool = True,
    beam_size: int | None = None,
) -> str:
    """Transcribe PCM16 mono frames using an in-memory WAV container."""
    if not pcm:
        return ""
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(int(sample_rate))
        stream.writeframes(pcm)
    return _transcribe_bytes(
        output.getvalue(),
        suffix=".wav",
        language=language,
        beam_size=max(1, min(5, int(beam_size if beam_size is not None else (5 if final else 1)))),
        vad_filter=False,
    )


def transcribe(audio_base64: str, language: str = "zh") -> str:
    """Transcribe base64 audio without network model resolution."""
    if not audio_base64:
        return ""
    try:
        raw = base64.b64decode(audio_base64, validate=True)
    except Exception:
        logger.warning("STT rejected invalid base64 audio")
        return ""
    return _transcribe_bytes(
        raw,
        suffix=".wav" if raw.startswith(b"RIFF") else ".webm",
        language=language,
        beam_size=5,
        vad_filter=True,
    )
