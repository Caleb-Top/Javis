"""PortAudio worker process.

This module is intentionally executed as a child process so a native audio
driver crash cannot terminate the Javis API and memory services.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import wave

import pyaudio


CHUNK_FRAMES = 1024


def _devices(audio: pyaudio.PyAudio) -> list[dict]:
    rows = []
    for index in range(audio.get_device_count()):
        try:
            info = dict(audio.get_device_info_by_index(index))
        except Exception:
            continue
        if int(info.get("maxInputChannels", 0) or 0) <= 0:
            continue
        rows.append(
            {
                "index": index,
                "name": str(info.get("name", "") or ""),
                "max_input_channels": int(info.get("maxInputChannels", 0) or 0),
                "default_rate": int(float(info.get("defaultSampleRate", 16_000) or 16_000)),
                "host_api": int(info.get("hostApi", -1) or -1),
            }
        )
    return rows


def _ordered_microphones(audio: pyaudio.PyAudio, device_index: int | None) -> list[dict]:
    devices = _devices(audio)
    if device_index is not None:
        matches = [device for device in devices if device["index"] == device_index]
        if not matches:
            raise RuntimeError(f"音频输入设备不可用: {device_index}")
        return matches
    if not devices:
        raise RuntimeError("未找到可用麦克风")
    try:
        default_index = int(audio.get_default_input_device_info()["index"])
    except Exception:
        default_index = -1
    return sorted(devices, key=lambda device: device["index"] != default_index)


def _write_status(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def list_devices() -> int:
    audio = pyaudio.PyAudio()
    try:
        print(json.dumps({"ok": True, "input_devices": _devices(audio)}, ensure_ascii=False))
        return 0
    finally:
        audio.terminate()


def capture(arguments: list[str]) -> int:
    if len(arguments) < 4:
        raise ValueError("capture requires output, stop, status and max duration")
    output_path = Path(arguments[0])
    stop_path = Path(arguments[1])
    status_path = Path(arguments[2])
    max_seconds = max(1.0, min(float(arguments[3]), 60.0))
    device_index = int(arguments[4]) if len(arguments) > 4 else None
    audio = pyaudio.PyAudio()
    stream = None
    selected = None
    rate = 16_000
    channels = 1
    frames: list[bytes] = []
    try:
        last_error: Exception | None = None
        for device in _ordered_microphones(audio, device_index):
            rate = max(8_000, min(192_000, int(device["default_rate"])))
            try:
                stream = audio.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=rate,
                    input=True,
                    input_device_index=device["index"],
                    frames_per_buffer=CHUNK_FRAMES,
                )
                selected = device
                break
            except Exception as error:
                last_error = error
        if stream is None or selected is None:
            raise RuntimeError(f"无法打开麦克风: {last_error or '未知错误'}")
        _write_status(
            status_path,
            {
                "ok": True,
                "trackLabel": selected["name"],
                "deviceIndex": selected["index"],
                "rate": rate,
                "channels": channels,
            },
        )
        deadline = time.monotonic() + max_seconds
        while not stop_path.exists() and time.monotonic() < deadline:
            frames.append(stream.read(CHUNK_FRAMES, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
        stream = None
        with wave.open(str(output_path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(audio.get_sample_size(pyaudio.paInt16))
            output.setframerate(rate)
            output.writeframes(b"".join(frames))
        return 0
    except Exception as error:
        _write_status(status_path, {"ok": False, "error": str(error)})
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            stream.close()
        audio.terminate()


def main() -> int:
    if len(sys.argv) < 2:
        raise ValueError("mode is required")
    if sys.argv[1] == "devices":
        return list_devices()
    if sys.argv[1] == "capture":
        return capture(sys.argv[2:])
    raise ValueError(f"unknown mode: {sys.argv[1]}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
