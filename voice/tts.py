"""Javis 语音合成 — 纯动态语音"""
import io, base64, logging, os, hashlib
from pathlib import Path
logger = logging.getLogger("voice.tts")
VOICE = "zh-CN-YunxiNeural"
CACHE_DIR = Path(__file__).parent.parent / "data" / "tts_cache"
MAX_CACHE = 100

def _trim_cache():
    if not CACHE_DIR.exists(): return
    files = sorted(CACHE_DIR.glob("*.mp3"), key=os.path.getmtime)
    if len(files) <= MAX_CACHE: return
    for f in files[:len(files)-MAX_CACHE]:
        try: f.unlink()
        except OSError:
            pass  # 缓存清理失败不影响主流程

async def text_to_speech(text: str) -> str:
    if not text or not text.strip(): return ""
    text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
    cache_file = CACHE_DIR / f"{text_hash}.mp3"
    if cache_file.exists():
        with open(cache_file, "rb") as f: return base64.b64encode(f.read()).decode()
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text, VOICE)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio": buf.write(chunk["data"])
        buf.seek(0)
        audio_data = buf.read()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "wb") as f: f.write(audio_data)
        _trim_cache()
        logger.info(f"TTS: {len(text)}字 -> {len(audio_data)//1024}KB")
        return base64.b64encode(audio_data).decode()
    except Exception as e:
        logger.error(f"TTS失败: {e}")
        return ""

async def preload_phrases():
    pass
