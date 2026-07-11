"""语音识别模块 — faster-whisper 本地离线识别"""

import base64
import io
import logging
import tempfile
import os

logger = logging.getLogger("voice.stt")

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
            _model = WhisperModel("base", device="cpu", compute_type="int8")
            logger.info("Whisper 模型已加载 (base, CPU)")
        except ImportError:
            logger.error("faster-whisper 未安装: pip install faster-whisper")
            return None
        except Exception as e:
            logger.error(f"Whisper 加载失败: {e}")
            return None
    return _model


def transcribe(audio_base64: str, language: str = "zh") -> str:
    """
    语音转文字
    audio_base64: 浏览器录制的 webm/opus base64
    language: zh/en/auto
    """
    if not audio_base64:
        return ""

    model = _get_model()
    if model is None:
        return ""

    # WebM/Opus → WAV via ffmpeg or temp file
    raw = base64.b64decode(audio_base64)

    # 保存临时文件
    tmp_in = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    tmp_in.write(raw)
    tmp_in.close()

    try:
        segments, _ = model.transcribe(
            tmp_in.name,
            language=language if language != "auto" else None,
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        logger.info(f"STT: {len(raw)//1024}KB → '{text[:80]}'")
        return text
    except Exception as e:
        logger.error(f"STT 识别失败: {e}")
        return ""
    finally:
        try:
            os.unlink(tmp_in.name)
        except Exception:
            pass
