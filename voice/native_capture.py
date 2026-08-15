"""Crash-isolated native Windows audio capture for the desktop app."""

from __future__ import annotations

import base64
from array import array
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from typing import Any


MAX_CAPTURE_SECONDS = 30
NATIVE_ROOT = Path(__file__).resolve().parent
MICROPHONE_WORKER = NATIVE_ROOT / "native_capture_worker.py"
WASAPI_HELPER = NATIVE_ROOT / "native" / "javis-wasapi-loopback.exe"


def frames_to_wav_base64(
    frames: list[bytes],
    *,
    sample_width: int,
    channels: int,
    rate: int,
) -> str:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(sample_width)
        stream.setframerate(rate)
        stream.writeframes(b"".join(frames))
    return base64.b64encode(output.getvalue()).decode("ascii")


def wav_signal_metrics(payload: bytes) -> dict[str, int | float | bool]:
    """Return privacy-safe level metadata for a PCM16 WAV capture."""
    empty = {
        "rms": 0.0,
        "peak": 0.0,
        "duration_ms": 0,
        "sample_rate": 0,
        "channels": 0,
        "signal_detected": False,
    }
    try:
        with wave.open(io.BytesIO(payload), "rb") as stream:
            channels = int(stream.getnchannels())
            sample_width = int(stream.getsampwidth())
            sample_rate = int(stream.getframerate())
            frame_count = int(stream.getnframes())
            frames = stream.readframes(frame_count)
        if sample_width != 2 or sample_rate <= 0 or not frames:
            return empty
        samples = array("h")
        samples.frombytes(frames)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return empty
        full_scale = 32768.0
        peak = min(1.0, max(abs(value) for value in samples) / full_scale)
        rms = min(
            1.0,
            math.sqrt(sum(float(value) * float(value) for value in samples) / len(samples))
            / full_scale,
        )
        duration_ms = max(0, round(frame_count * 1000 / sample_rate))
        return {
            "rms": round(rms, 6),
            "peak": round(peak, 6),
            "duration_ms": duration_ms,
            "sample_rate": sample_rate,
            "channels": channels,
            "signal_detected": bool(
                duration_ms >= 250 and peak >= 0.006 and rms >= 0.001
            ),
        }
    except (EOFError, OSError, ValueError, wave.Error):
        return empty


@dataclass
class _CaptureProcessState:
    process: subprocess.Popen
    output_path: Path
    stop_path: Path
    status_path: Path
    source: str
    track_label: str
    backend: str


class NativeCaptureManager:
    """Owns one native capture while keeping driver DLLs outside the API process."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: _CaptureProcessState | None = None

    @staticmethod
    def _capture_paths() -> tuple[Path, Path, Path]:
        capture_root = Path(tempfile.gettempdir()) / "javis-native-audio"
        capture_root.mkdir(parents=True, exist_ok=True)
        capture_id = uuid.uuid4().hex
        return (
            capture_root / f"{capture_id}.wav",
            capture_root / f"{capture_id}.stop",
            capture_root / f"{capture_id}.json",
        )

    @staticmethod
    def _creation_flags() -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

    @staticmethod
    def _read_status(path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError):
            return {}

    @staticmethod
    def _wait_until_ready(
        process: subprocess.Popen,
        status_path: Path,
        *,
        timeout: float = 6.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = NativeCaptureManager._read_status(status_path)
            if status:
                if status.get("ok"):
                    return status
                raise RuntimeError(str(status.get("error") or "音频采集器启动失败"))
            return_code = process.poll()
            if return_code is not None:
                error = (process.stderr.read() if process.stderr else "").strip()
                raise RuntimeError(error or f"音频采集器异常退出: {return_code}")
            time.sleep(0.04)
        process.kill()
        process.wait(timeout=3)
        raise RuntimeError("音频采集器启动超时")

    def start(
        self,
        source: str = "microphone",
        *,
        device_index: int | None = None,
    ) -> dict[str, Any]:
        source = str(source or "microphone").strip().lower()
        if source not in {"microphone", "system"}:
            raise ValueError("source must be microphone or system")
        with self._lock:
            if self._state is not None:
                raise RuntimeError("已有音频采集任务正在运行")
            if source == "system":
                if not WASAPI_HELPER.is_file():
                    raise RuntimeError("内置 Windows WASAPI 采集器缺失")
                return self._start_wasapi()
            if not MICROPHONE_WORKER.is_file():
                raise RuntimeError("内置麦克风采集器缺失")
            return self._start_microphone(device_index)

    def _start_microphone(self, device_index: int | None) -> dict[str, Any]:
        output_path, stop_path, status_path = self._capture_paths()
        command = [
            sys.executable,
            str(MICROPHONE_WORKER),
            "capture",
            str(output_path),
            str(stop_path),
            str(status_path),
            str(MAX_CAPTURE_SECONDS),
        ]
        if device_index is not None:
            command.append(str(int(device_index)))
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=self._creation_flags(),
        )
        try:
            status = self._wait_until_ready(process, status_path)
        except Exception:
            self._cleanup_paths(output_path, stop_path, status_path)
            raise
        track_label = str(status.get("trackLabel") or "Windows 默认麦克风")
        self._state = _CaptureProcessState(
            process=process,
            output_path=output_path,
            stop_path=stop_path,
            status_path=status_path,
            source="microphone",
            track_label=track_label,
            backend="isolated-pyaudio-portaudio",
        )
        return {
            "ok": True,
            "recording": True,
            "source": "microphone",
            "status": "ready",
            "message": "正在通过本机设备采集麦克风",
            "trackLabel": track_label,
            "deviceIndex": status.get("deviceIndex"),
            "rate": status.get("rate"),
            "channels": status.get("channels"),
            "backend": "isolated-pyaudio-portaudio",
            "crashIsolated": True,
        }

    def _start_wasapi(self) -> dict[str, Any]:
        output_path, stop_path, status_path = self._capture_paths()
        process = subprocess.Popen(
            [str(WASAPI_HELPER), str(output_path), str(stop_path), str(MAX_CAPTURE_SECONDS)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=self._creation_flags(),
        )
        time.sleep(0.08)
        if process.poll() is not None:
            error = (process.stderr.read() if process.stderr else "").strip()
            self._cleanup_paths(output_path, stop_path, status_path)
            raise RuntimeError(error or "WASAPI 系统音频采集器启动失败")
        self._state = _CaptureProcessState(
            process=process,
            output_path=output_path,
            stop_path=stop_path,
            status_path=status_path,
            source="system",
            track_label="Windows 默认播放设备（WASAPI 回环）",
            backend="windows-wasapi-loopback",
        )
        return {
            "ok": True,
            "recording": True,
            "source": "system",
            "status": "ready",
            "message": "正在通过 Windows WASAPI 采集系统音频",
            "trackLabel": self._state.track_label,
            "backend": self._state.backend,
            "crashIsolated": True,
        }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            if state is None:
                return {
                    "ok": False,
                    "status": "error",
                    "source": "microphone",
                    "message": "当前没有音频采集任务",
                }
            self._state = None
        state.stop_path.touch(exist_ok=True)
        try:
            _, stderr = state.process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            state.process.kill()
            _, stderr = state.process.communicate(timeout=3)
        try:
            status = self._read_status(state.status_path)
            if state.process.returncode != 0:
                message = str(status.get("error") or stderr or "原生音频采集失败").strip()
                return {
                    "ok": False,
                    "status": "error",
                    "source": state.source,
                    "message": message[:500],
                    "backend": state.backend,
                    "backendAlive": True,
                }
            if not state.output_path.is_file() or state.output_path.stat().st_size <= 44:
                return {
                    "ok": False,
                    "status": "empty",
                    "source": state.source,
                    "message": f"{_source_label(state.source)}设备已连接，但没有采集到音频数据",
                    "backend": state.backend,
                }
            payload = state.output_path.read_bytes()
            signal = wav_signal_metrics(payload)
            signal_detected = bool(signal["signal_detected"])
            return {
                "ok": True,
                "recording": False,
                "source": state.source,
                "status": "ready",
                "message": (
                    f"已采集 {max(1, round(len(payload) / 1024))} KB {_source_label(state.source)}"
                    if signal_detected
                    else f"{_source_label(state.source)}已连接，但没有检测到有效声音"
                ),
                "mimeType": "audio/wav",
                "bytes": len(payload),
                "trackLabel": state.track_label,
                "deviceIndex": status.get("deviceIndex"),
                "rate": signal["sample_rate"] or status.get("rate"),
                "channels": signal["channels"] or status.get("channels"),
                "durationMs": signal["duration_ms"],
                "rms": signal["rms"],
                "peak": signal["peak"],
                "signalDetected": signal_detected,
                "backend": state.backend,
                "crashIsolated": True,
                "audioBase64": base64.b64encode(payload).decode("ascii"),
            }
        finally:
            self._cleanup_paths(state.output_path, state.stop_path, state.status_path)

    @staticmethod
    def _cleanup_paths(*paths: Path) -> None:
        for path in paths:
            path.unlink(missing_ok=True)

    def probe(
        self,
        source: str = "microphone",
        duration: float = 1.2,
        *,
        device_index: int | None = None,
    ) -> dict[str, Any]:
        duration = max(0.25, min(float(duration), 5.0))
        self.start(source, device_index=device_index)
        time.sleep(duration)
        return self.stop()

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            if self._state is not None:
                return {
                    "available": True,
                    "backend": self._state.backend,
                    "native": True,
                    "crash_isolated": True,
                    "webview_permission_required": False,
                    "wasapi_loopback": WASAPI_HELPER.is_file(),
                    "input_devices": [],
                    "system_audio_devices": [],
                    "recording": True,
                }
            if not MICROPHONE_WORKER.is_file():
                return self._diagnostic_error("内置麦克风采集器缺失")
            try:
                completed = subprocess.run(
                    [sys.executable, str(MICROPHONE_WORKER), "devices"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=8,
                    creationflags=self._creation_flags(),
                )
                if completed.returncode != 0:
                    raise RuntimeError(completed.stderr.strip() or "音频设备诊断失败")
                payload = json.loads(completed.stdout)
                devices = payload.get("input_devices", [])
                return {
                    "available": bool(payload.get("ok")),
                    "backend": "isolated-pyaudio-portaudio",
                    "native": True,
                    "crash_isolated": True,
                    "webview_permission_required": False,
                    "wasapi_loopback": WASAPI_HELPER.is_file(),
                    "input_devices": devices,
                    "system_audio_devices": [],
                    "recording": False,
                }
            except Exception as error:
                return self._diagnostic_error(str(error))

    @staticmethod
    def _diagnostic_error(error: str) -> dict[str, Any]:
        return {
            "available": False,
            "backend": "isolated-pyaudio-portaudio",
            "native": True,
            "crash_isolated": True,
            "webview_permission_required": False,
            "wasapi_loopback": WASAPI_HELPER.is_file(),
            "error": error[:500],
        }


def _source_label(source: str) -> str:
    return "系统音频" if source == "system" else "麦克风"


capture_manager = NativeCaptureManager()


def start_capture(source: str = "microphone", device_index: int | None = None) -> dict[str, Any]:
    return capture_manager.start(source, device_index=device_index)


def stop_capture() -> dict[str, Any]:
    return capture_manager.stop()


def probe_capture(
    source: str = "microphone",
    duration: float = 1.2,
    device_index: int | None = None,
) -> dict[str, Any]:
    return capture_manager.probe(source, duration, device_index=device_index)


def get_diagnostics() -> dict[str, Any]:
    return capture_manager.diagnostics()
