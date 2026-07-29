"""Javis text-to-speech with an offline Windows SAPI primary backend."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import io
import logging
import os
import tempfile
from pathlib import Path


logger = logging.getLogger("voice.tts")

VOICE = "zh-CN-YunxiNeural"
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "tts_cache"
MAX_CACHE = 100


def _trim_cache() -> None:
    if not CACHE_DIR.exists():
        return
    files = sorted(
        (
            path
            for path in CACHE_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in {".wav", ".mp3"}
        ),
        key=os.path.getmtime,
    )
    for path in files[:-MAX_CACHE]:
        try:
            path.unlink()
        except OSError:
            pass


def _select_sapi_voice(voice) -> None:
    preferred = os.environ.get("JAVIS_SAPI_VOICE", "").strip().lower()
    if not preferred:
        return
    for token in voice.GetVoices():
        if preferred in str(token.GetDescription()).lower():
            voice.Voice = token
            return


def _synthesize_sapi_bytes(text: str) -> bytes:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    temporary = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    path = Path(temporary.name)
    temporary.close()
    stream = None
    try:
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        _select_sapi_voice(voice)
        stream.Open(str(path), 3, False)
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
        stream = None
        return path.read_bytes()
    finally:
        if stream is not None:
            try:
                stream.Close()
            except Exception:
                pass
        try:
            path.unlink()
        except OSError:
            pass
        pythoncom.CoUninitialize()


async def _synthesize_edge_bytes(text: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(text, VOICE)
    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    return buffer.getvalue()


def get_diagnostics() -> dict:
    sapi_available = (
        os.name == "nt"
        and importlib.util.find_spec("win32com.client") is not None
    )
    edge_available = importlib.util.find_spec("edge_tts") is not None
    return {
        "available": bool(sapi_available or edge_available),
        "backend": "windows-sapi" if sapi_available else "edge-tts" if edge_available else "",
        "voice": "Windows SAPI" if sapi_available else VOICE,
        "offline": sapi_available,
        "network_required": not sapi_available and edge_available,
        "edge_tts_fallback": edge_available,
        "hardware_tested": False,
    }


async def synthesize(text: str) -> tuple[str, str]:
    """Return base64 audio and its MIME type, preferring local SAPI."""
    text = str(text or "").strip()
    if not text:
        return "", ""

    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    wav_cache = CACHE_DIR / f"{text_hash}.wav"
    mp3_cache = CACHE_DIR / f"{text_hash}.mp3"
    for cache_file, mime in ((wav_cache, "audio/wav"), (mp3_cache, "audio/mpeg")):
        if cache_file.is_file():
            return base64.b64encode(cache_file.read_bytes()).decode("ascii"), mime

    audio_data = b""
    mime = ""
    if os.name == "nt" and importlib.util.find_spec("win32com.client") is not None:
        try:
            audio_data = await asyncio.to_thread(_synthesize_sapi_bytes, text)
            mime = "audio/wav"
        except Exception as exc:
            logger.warning("Windows SAPI synthesis failed, trying fallback: %s", exc)

    if not audio_data and importlib.util.find_spec("edge_tts") is not None:
        try:
            audio_data = await _synthesize_edge_bytes(text)
            mime = "audio/mpeg"
        except Exception as exc:
            logger.error("edge-tts synthesis failed: %s", exc)

    if not audio_data:
        return "", ""

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = wav_cache if mime == "audio/wav" else mp3_cache
    cache_file.write_bytes(audio_data)
    _trim_cache()
    logger.info("TTS: %s chars -> %s KB via %s", len(text), len(audio_data) // 1024, mime)
    return base64.b64encode(audio_data).decode("ascii"), mime


async def text_to_speech(text: str) -> str:
    """Compatibility wrapper for existing callers that only consume base64."""
    audio, _ = await synthesize(text)
    return audio


async def preload_phrases() -> None:
    return None
