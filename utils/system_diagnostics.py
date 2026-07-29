"""Actionable local diagnostics shared by the App and Python runtime."""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit


def _check(
    check_id: str,
    label: str,
    status: str,
    message: str,
    action: str = "",
) -> dict:
    result = {
        "id": check_id,
        "label": label,
        "status": status,
        "message": message,
    }
    if action:
        result["action"] = action
    return result


def check_directory(
    check_id: str,
    label: str,
    path: str | Path,
    *,
    create: bool = False,
    writable: bool = False,
) -> dict:
    directory = Path(path).expanduser()
    try:
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        if not directory.exists():
            return _check(check_id, label, "fail", f"目录不存在：{directory}", "open_storage")
        if not directory.is_dir():
            return _check(check_id, label, "fail", f"路径不是目录：{directory}", "open_storage")
        if writable:
            probe = directory / f".javis-write-probe-{os.getpid()}-{uuid.uuid4().hex[:6]}"
            try:
                probe.write_text("javis", encoding="utf-8")
            finally:
                probe.unlink(missing_ok=True)
        free_gb = shutil.disk_usage(directory).free / (1024 ** 3)
        state = "warn" if free_gb < 2 else "pass"
        suffix = "可写" if writable else "可访问"
        message = f"{suffix}，剩余 {free_gb:.1f} GB"
        action = "open_storage" if state == "warn" else ""
        return _check(check_id, label, state, message, action)
    except Exception as error:
        return _check(check_id, label, "fail", f"无法访问：{str(error)[:120]}", "open_storage")


def check_model_directory(path: str | Path) -> dict:
    directory = Path(path).expanduser()
    if not directory.exists() or not directory.is_dir():
        return _check(
            "model_dir",
            "本地模型目录",
            "warn",
            f"目录不存在：{directory}",
            "open_storage",
        )
    model_extensions = {".gguf", ".safetensors", ".bin", ".onnx"}
    count = 0
    try:
        for root, _, files in os.walk(directory):
            for name in files:
                if Path(name).suffix.lower() in model_extensions or "sha256-" in name:
                    count += 1
                    if count >= 512:
                        break
            if count >= 512:
                break
        return _check(
            "model_dir",
            "本地模型目录",
            "pass",
            f"目录可访问，发现 {count} 个模型文件或分片",
        )
    except Exception as error:
        return _check("model_dir", "本地模型目录", "warn", str(error)[:120], "open_storage")


def get_ollama_models(base_url: str) -> list[str]:
    parsed = urlsplit(base_url or "http://127.0.0.1:11434")
    root = f"{parsed.scheme or 'http'}://{parsed.netloc or '127.0.0.1:11434'}"
    endpoint = f"{root}/api/tags"
    with urllib.request.urlopen(endpoint, timeout=2.5) as response:
        payload = json.loads(response.read(2_000_000).decode("utf-8"))
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return sorted({
        str(item.get("name") or item.get("model") or "").strip()
        for item in models
        if isinstance(item, dict) and (item.get("name") or item.get("model"))
    })


def probe_ollama(base_url: str, expected_model: str) -> dict:
    try:
        names = set(get_ollama_models(base_url))
        if expected_model and expected_model not in names:
            return _check(
                "local_model_connection",
                "本地模型连接",
                "warn",
                f"Ollama 在线，但未发现 {expected_model}",
                "open_storage",
            )
        return _check(
            "local_model_connection",
            "本地模型连接",
            "pass",
            f"Ollama 在线，已发现 {len(names)} 个模型",
        )
    except Exception as error:
        return _check(
            "local_model_connection",
            "本地模型连接",
            "fail",
            f"Ollama 无法连接：{str(error)[:100]}",
            "restart_runtime",
        )


def probe_remote_provider(
    provider: str,
    base_url: str,
    api_key: str,
    expected_model: str = "",
) -> dict:
    provider_name = str(provider or "remote").strip()
    if not api_key:
        return _check(
            "remote_model_connection",
            "远端 API 连接",
            "fail",
            f"{provider_name} 未配置 API Key",
            "open_storage",
        )

    endpoint = f"{str(base_url or '').rstrip('/')}/models"
    headers = {"Accept": "application/json", "User-Agent": "Javis-Diagnostics/3.0"}
    if provider_name == "anthropic":
        headers.update({
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        })
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=4.0) as response:
            status = int(getattr(response, "status", 200) or 200)
            response.read(2_000_000)
        if 200 <= status < 300:
            suffix = f"，当前模型 {expected_model}" if expected_model else ""
            return _check(
                "remote_model_connection",
                "远端 API 连接",
                "pass",
                f"{provider_name} 已连接{suffix}",
            )
        return _check(
            "remote_model_connection",
            "远端 API 连接",
            "warn",
            f"{provider_name} 服务返回 HTTP {status}",
            "open_storage",
        )
    except urllib.error.HTTPError as error:
        if error.code in {401, 403}:
            message = f"{provider_name} 认证失败（HTTP {error.code}）"
            status = "fail"
        elif error.code in {404, 405}:
            message = f"{provider_name} 服务可达，但不支持模型探测端点"
            status = "warn"
        elif error.code == 429:
            message = f"{provider_name} 服务可达，但当前受到限流"
            status = "warn"
        else:
            message = f"{provider_name} 返回 HTTP {error.code}"
            status = "fail"
        return _check(
            "remote_model_connection",
            "远端 API 连接",
            status,
            message,
            "open_storage",
        )
    except Exception as error:
        return _check(
            "remote_model_connection",
            "远端 API 连接",
            "fail",
            f"{provider_name} 无法连接：{str(error)[:100]}",
            "open_storage",
        )


def build_report(checks: list[dict], scope: str) -> dict:
    counts = {
        state: sum(1 for check in checks if check.get("status") == state)
        for state in ("pass", "warn", "fail")
    }
    overall = "fail" if counts["fail"] else ("warn" if counts["warn"] else "pass")
    return {
        "ok": counts["fail"] == 0,
        "scope": scope,
        "overall": overall,
        "counts": counts,
        "summary": (
            f"{counts['pass']} 项通过，"
            f"{counts['warn']} 项需留意，"
            f"{counts['fail']} 项失败"
        ),
        "checks": checks,
    }
