"""Javis 实时语音引擎 — 流式 STT + VAD + TTS

架构:
  WebSocket (/ws_voice) → 音频块 → VAD检测静默 → faster-whisper转文字
  → Agent.chat_quick() 流式回复 → edge-tts 合成 → WebSocket 回传音频

依赖:
  - faster-whisper (本地 STT)
  - edge-tts (在线 TTS)
  - webrtcvad (可选的 VAD, 否则用能量检测)

注意: 浏览器 MediaRecorder 产生的 WebM 块不能直接拼接。
     合并后必须通过 ffmpeg 重混流才能被 Whisper 正确解码。
"""

import asyncio, base64, io, json, logging, os, tempfile, time, uuid, subprocess
from collections import deque
from pathlib import Path
from typing import Optional

logger = logging.getLogger("voice.realtime")

_PROJECT_ROOT = Path(__file__).parent.parent
(_PROJECT_ROOT / "tmp").mkdir(exist_ok=True)

# ── VAD 参数 ──
SILENCE_THRESHOLD = 2.0       # 静默秒数 — 调大到2秒，防止网络抖动导致过早切分
MAX_UTTERANCE_SECS = 20       # 单句最长录音秒数
CHUNK_DURATION = 0.5          # 前端每块时间 (匹配 MediaRecorder timeslice 500ms)
MIN_CHUNKS_FOR_AUTOFLUSH = 6  # VAD 自动刷新最少需要 6 块 (≈3秒)，否则等 audio_end
MIN_AUDIO_BYTES = 3000        # 最小音频字节数，低于此不识别

# ── STT 模型 (与 stt.py 共用单例) ──
_stt_model = None

def _get_stt_model():
    global _stt_model
    if _stt_model is None:
        # 优先复用 stt.py 的模型，避免重复加载
        try:
            from voice.stt import _model as _shared_model
            if _shared_model is not None:
                _stt_model = _shared_model
                logger.info("实时 STT: 复用 stt.py 模型")
                return _stt_model
        except Exception:
            pass
        try:
            from faster_whisper import WhisperModel
            model_name = "base"
            device = "cpu"
            compute_type = "int8"
            try:
                import yaml
                config_path = Path(__file__).parent.parent / "config.yaml"
                with open(config_path, encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                model_name = cfg.get("voice", {}).get("stt", {}).get("model", "base")
            except Exception:
                pass
            try:
                import torch
                if torch.cuda.is_available():
                    device = "cuda"
                    compute_type = "float16"
            except Exception:
                pass
            _stt_model = WhisperModel(model_name, device=device,
                                       compute_type=compute_type, num_workers=2)
            logger.info(f"Realtime STT: {model_name} on {device.upper()}")
        except ImportError:
            logger.error("faster-whisper 未安装")
            return None
        except Exception as e:
            logger.error(f"STT 模型加载失败: {e}")
            return None
    return _stt_model


def transcribe_chunks(audio_chunks: list[bytes]) -> str:
    """将累积的音频块合并后转文字

    注意：浏览器 MediaRecorder 的 WebM 块不能直接拼接。
    每个块都是独立的 WebM 片段，b"".join() 后容器结构损坏。

    修复方案（2026-07）：
      前端已改为不设 timeslice → stop() 时只发一个完整的 WebM 文件。
      如果仍有多个块，尝试用 ffmpeg 合并或用 EBML 级别的拼接。
    """
    if not audio_chunks:
        return ""
    model = _get_stt_model()
    if model is None:
        return ""

    # 合并多个块 —— 尝试用 ffmpeg 或直接 EBML 级拼接
    audio_data = _merge_webm_chunks(audio_chunks)
    if not audio_data:
        logger.debug("实时 STT 跳过: 合并失败")
        return ""

    tmp_path = str(_PROJECT_ROOT / "tmp" / f"voice_realtime_{uuid.uuid4().hex[:12]}.webm")
    try:
        with open(tmp_path, "wb") as f:
            f.write(audio_data)
    except Exception as e:
        logger.error(f"临时文件写入失败: {e}")
        return ""

    # 读取语言设置
    try:
        import yaml
        _cfg_path = _PROJECT_ROOT / "config.yaml"
        with open(_cfg_path, encoding="utf-8") as f:
            _stt_lang = yaml.safe_load(f).get("voice", {}).get("stt", {}).get("language", "zh")
    except Exception:
        _stt_lang = "zh"

    try:
        audio_duration_s = len(audio_data) / 16000 * 2  # 粗略估计秒数
        use_vad = audio_duration_s >= 3.0  # < 3s 的音频关掉 VAD，避免短音频全被滤掉
        segments, info = model.transcribe(
            tmp_path,
            language=_stt_lang if _stt_lang != "auto" else None,
            beam_size=5,
            vad_filter=use_vad,
            vad_parameters=dict(min_silence_duration_ms=500) if use_vad else None,
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        if text:
            logger.info(f"实时 STT: {len(audio_data)//1024}KB → '{text[:60]}'")
        else:
            logger.debug(f"实时 STT: 空文本 (音频 {len(audio_data)//1024}KB)")
        return text
    except Exception as e:
        logger.error(f"实时 STT 失败: {e}")
        return ""
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _merge_webm_chunks(chunks: list[bytes]) -> bytes:
    """合并多个 WebM 音频块为一个有效 WebM

    策略：
    1. 如果只有 1 个块（前端已修复为无 timeslice），直接返回（自包含的完整 WebM）
    2. 如果 ffmpeg 在 PATH 中，用 concat demuxer 合并
    3. 如果多个块且无 ffmpeg，尝试 EBML 级别的智能拼接
    """
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]

    total_size = sum(len(c) for c in chunks)
    if total_size < 3000:
        logger.debug(f"音频总长度不足 ({total_size}B)")
        return b""

    # ── 尝试 ffmpeg concat ──
    ffmpeg_path = None
    import shutil
    ffmpeg_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if ffmpeg_path:
        try:
            tmp_dir = _PROJECT_ROOT / "tmp"
            tmp_dir.mkdir(exist_ok=True)
            # 写每个块到独立文件
            chunk_paths = []
            concat_file = tmp_dir / f"concat_{uuid.uuid4().hex[:8]}.txt"
            for i, ch in enumerate(chunks):
                cp = tmp_dir / f"chunk_{uuid.uuid4().hex[:8]}_{i}.webm"
                cp.write_bytes(ch)
                chunk_paths.append(cp)
                concat_file.write_text(
                    (concat_file.read_text() if concat_file.exists() else "")
                    + f"file '{cp.name}'\n"
                )
            out_path = tmp_dir / f"merged_{uuid.uuid4().hex[:8]}.webm"
            result = subprocess.run(
                [ffmpeg_path, "-f", "concat", "-safe", "0",
                 "-i", str(concat_file),
                 "-c", "copy", str(out_path)],
                capture_output=True, timeout=30
            )
            if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                merged = out_path.read_bytes()
                # 清理临时文件
                for p in chunk_paths + [concat_file, out_path]:
                    try: p.unlink()
                    except: pass
                if merged:
                    logger.info(f"ffmpeg 合并: {len(chunks)}块 → {len(merged)//1024}KB")
                    return merged
            # 清理
            for p in chunk_paths + [concat_file, out_path]:
                try: p.unlink()
                except: pass
        except Exception as e:
            logger.debug(f"ffmpeg 合并失败: {e}")

    # ── 无 ffmpeg：尝试 EBML 级智能拼接 ──
    # 第一个块有完整 EBML header + Segment header
    # 后续块开头有重复的 EBML header，需要跳过
    logger.debug(f"EBML 级拼接: {len(chunks)}个块, {total_size//1024}KB")
    result = bytearray(chunks[0])
    # Matroska Cluster ID: 0x1F43B675
    CLUSTER_ID = b'\x1f\x43\xb6\x75'
    for chunk in chunks[1:]:
        # 在后续块中查找第一个 Cluster，从那里开始追加
        pos = chunk.find(CLUSTER_ID)
        if pos >= 0:
            result.extend(chunk[pos:])
        else:
            # 找不到 Cluster 标记，追加整个块（兜底）
            result.extend(chunk)
    return bytes(result)


async def tts_stream(text: str) -> str:
    """流式合成语音, 返回 base64 编码的 mp3"""
    if not text or not text.strip():
        return ""
    try:
        import edge_tts
        from voice.tts import VOICE
        communicate = edge_tts.Communicate(text, VOICE)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        logger.info(f"TTS: {len(text)}字 → {len(b64)//1024}KB")
        return b64
    except Exception as e:
        logger.error(f"TTS 失败: {e}")
        return ""


async def _safe_send(ws, data) -> bool:
    """安全发送 JSON，客户端已断开时返回 False 不抛异常"""
    try:
        await ws.send_json(data)
        return True
    except Exception:
        return False


async def handle_voice_ws(ws, agent) -> None:
    """处理实时语音 WebSocket 连接

    协议:
      客户端 → 服务端:
        {"type": "audio_start", "mime": "audio/webm;codecs=opus"}
        {"type": "audio_chunk", "data": "<base64>"}
        {"type": "audio_end"}
        {"type": "ping"}

      服务端 → 客户端:
        {"type": "status", "state": "listening"|"thinking"|"speaking"}
        {"type": "transcript", "text": "识别结果（中间）"}
        {"type": "audio", "data": "<base64 mp3>"}
        {"type": "text", "content": "文字回复"}
        {"type": "done"}
        {"type": "pong"}
    """
    await ws.accept()
    logger.info("🎤 实时语音 WebSocket 已连接")

    audio_chunks: list[bytes] = []
    last_chunk_time = 0
    utterance_active = False
    current_mime = "audio/webm;codecs=opus"
    _processing = False  # 防止同一段语音被重复处理

    async def flush_utterance() -> Optional[str]:
        nonlocal audio_chunks, utterance_active
        if not audio_chunks:
            return None
        text = transcribe_chunks(list(audio_chunks))
        audio_chunks.clear()
        utterance_active = False
        return text

    async def safe_process(text: str):
        """安全处理语音文本：防重复 + 防崩溃"""
        nonlocal _processing
        if not text:
            await _safe_send(ws, {"type": "done"})
            return
        if _processing:
            logger.debug("safe_process 跳过: 正在处理中")
            return
        _processing = True
        try:
            logger.debug(f"处理语音: '{text[:50]}'")
            await _process_utterance(ws, agent, text)
        except Exception as e:
            logger.debug(f"_process_utterance 异常: {e}")
        finally:
            _processing = False

    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=0.5)
            except asyncio.TimeoutError:
                if utterance_active and audio_chunks and (time.time() - last_chunk_time) > SILENCE_THRESHOLD:
                    if len(audio_chunks) >= MIN_CHUNKS_FOR_AUTOFLUSH:
                        text = await flush_utterance()
                        await safe_process(text)
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = msg.get("type", "")

            if t == "audio_start":
                current_mime = msg.get("mime", current_mime)
                audio_chunks.clear()
                utterance_active = True
                last_chunk_time = time.time()
                await _safe_send(ws, {"type": "status", "state": "listening"})

            elif t == "audio_chunk":
                data_b64 = msg.get("data", "")
                if data_b64:
                    try:
                        chunk = base64.b64decode(data_b64)
                        audio_chunks.append(chunk)
                        last_chunk_time = time.time()
                        utterance_active = True
                    except Exception:
                        pass

                if len(audio_chunks) * CHUNK_DURATION > MAX_UTTERANCE_SECS:
                    text = await flush_utterance()
                    await safe_process(text)

            elif t == "audio_end":
                text = await flush_utterance()
                await safe_process(text)

            elif t == "ping":
                await _safe_send(ws, {"type": "pong"})

    except Exception as e:
        logger.debug(f"语音 WS 断开: {e}")
    finally:
        try:
            if audio_chunks:
                text = await flush_utterance()
                await safe_process(text)
        except Exception:
            pass


async def _process_utterance(ws, agent, text: str):
    """处理一句完整语音: STT → Agent → TTS → 回传"""
    text = text.strip()
    if not text:
        await _safe_send(ws, {"type": "done"})
        return

    logger.info(f"🎤 识别: {text[:60]}")
    if not await _safe_send(ws, {"type": "voice_transcript", "text": text}):
        return
    await _safe_send(ws, {"type": "status", "state": "thinking"})

    full_reply = ""
    chat_gen = agent.chat_quick(text)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(chat_gen.__anext__(), timeout=120)
            except asyncio.TimeoutError:
                logger.warning(f"chat_quick 超时: {text[:40]}")
                try:
                    await chat_gen.aclose()
                except Exception:
                    pass
                break
            except StopAsyncIteration:
                break

            t = msg.get("type", "")

            if t == "thinking":
                await _safe_send(ws, {"type": "status", "state": "thinking"})

            elif t == "voice_text_delta":
                full_reply += msg.get("text", "")
                await _safe_send(ws, {"type": "text", "content": msg.get("text", "")})

            elif t == "text_delta":
                full_reply += msg.get("text", "")
                await _safe_send(ws, {"type": "text", "content": msg.get("text", "")})

            elif t == "tool_start":
                tn = msg.get("tool", "")
                await _safe_send(ws, {"type": "status", "state": "executing", "tool": tn})
                await _safe_send(ws, {"type": "tool_start", "tool": tn})

            elif t == "tool_result":
                tn = msg.get("tool", "")
                ok = msg.get("success", False)
                await _safe_send(ws, {"type": "status", "state": "executing", "tool": tn, "ok": ok})
                await _safe_send(ws, {"type": "tool_result", "tool": tn, "success": ok})

            elif t == "confirm_required":
                agent.resolve_confirm(True)
                tn = msg.get("tool", "")
                await _safe_send(ws, {"type": "status", "state": "executing", "tool": tn})

            elif t == "error":
                await _safe_send(ws, {"type": "error", "message": msg.get("message", "")})

            elif t == "done":
                break
    except Exception as e:
        logger.debug(f"chat_quick process 异常: {e}")

    if full_reply:
        await _safe_send(ws, {"type": "status", "state": "speaking"})
        audio_b64 = await tts_stream(full_reply)
        if audio_b64:
            await _safe_send(ws, {"type": "audio", "data": audio_b64})

    await _safe_send(ws, {"type": "done"})
