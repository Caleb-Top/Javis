"""
语音工具 — 注册已有 TTS/STT 引擎为 Agent 可调用工具
==============================================
完全复用 voice/tts.py + voice/stt.py, 不重写任何引擎代码。

speech_to_text 需要从浏览器接收 base64 音频数据后调用。
"""
import logging
logger = logging.getLogger("voice.tools")

# ── 全局 kernel 引用（由 main.py 注入）──
_VOICE_KERNEL = None

def set_voice_kernel(kernel):
    """注入 JavisKernel 实例，使 speech_to_text 喂给 AuditoryLeaf"""
    global _VOICE_KERNEL
    _VOICE_KERNEL = kernel

_engine_checked = False
_tts_available = False
_stt_available = False


def _check_engines():
    global _engine_checked, _tts_available, _stt_available
    if _engine_checked:
        return
    try:
        from voice.tts import text_to_speech as _tts_func
        _tts_available = True
    except Exception as e:
        logger.warning(f"TTS 引擎不可用: {e}")
    try:
        from voice.stt import transcribe as _stt_func
        _stt_available = True
    except Exception as e:
        logger.warning(f"STT 引擎不可用: {e}")
    _engine_checked = True
    logger.info(f"语音引擎: TTS={'OK' if _tts_available else 'NO'} STT={'OK' if _stt_available else 'NO'}")


async def text_to_speech(text: str) -> str:
    """文字转语音并返回 base64 音频"""
    _check_engines()
    if not _tts_available:
        return "TTS_ENGINE_UNAVAILABLE"
    from voice.tts import text_to_speech as _tts
    b64_audio = await _tts(text)
    if b64_audio:
        return "AUDIO:" + b64_audio
    return "TTS_FAILED"


async def speech_to_text(audio_base64: str = "") -> str:
    """语音转文字 — 传入浏览器录制的 base64 音频数据
       如果 audio_base64 为空, 尝试从临时文件读取最近录音"""
    _check_engines()
    if not _stt_available:
        return "STT 引擎未就绪"
    try:
        # 如果没有传入音频, 尝试读最近的录音文件
        if not audio_base64:
            from pathlib import Path
            import base64
            import glob
            data_dir = Path(__file__).parent.parent / "data"
            rec_dir = data_dir / "recordings"
            if rec_dir.exists():
                files = sorted(rec_dir.glob("*.webm"),
                               key=lambda f: f.stat().st_mtime, reverse=True)
                if files:
                    audio_base64 = base64.b64encode(
                        files[0].read_bytes()).decode()

        if not audio_base64:
            return ("没有音频输入。请通过浏览器录制语音后调用此工具, "
                    "并传入 audio_base64 参数。")

        from voice.stt import transcribe as _stt
        text = _stt(audio_base64)
        if text and text.strip():
            # ★ 融合：将音频+文字喂给 AuditoryLeaf 训练
            try:
                if _VOICE_KERNEL is not None:
                    import base64 as _b64
                    raw_audio = _b64.b64decode(audio_base64)
                    _VOICE_KERNEL.feed_auditory_with_text(raw_audio, text.strip())
            except Exception:
                pass
            return text.strip()
        return "未能识别到有效语音（VAD 可能过滤了全部音频）"
    except Exception as e:
        return f"语音识别失败: {e}"
