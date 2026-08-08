"""Consent-gated local model installation and Hugging Face discovery.

The application may inspect an offline add-on or public model metadata without
changing the machine. Any extraction or network download requires an explicit
approval marker from the settings UI.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable

import requests


ADDON_MANIFEST = "Javis-R1-8B-Addon.manifest.json"
APPROVAL_MARKER = "install-local-model"
HF_API = "https://huggingface.co/api"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_INSTALL_LOCK = threading.Lock()
_PROGRESS_LOCK = threading.Lock()
_INSTALL_PROGRESS: dict[str, Any] = {
    "state": "idle",
    "phase": "等待安装",
    "percent": 0,
    "completed_bytes": 0,
    "total_bytes": 0,
}


def _set_progress(
    state: str,
    phase: str,
    percent: float,
    completed_bytes: int = 0,
    total_bytes: int = 0,
) -> None:
    with _PROGRESS_LOCK:
        _INSTALL_PROGRESS.update({
            "state": state,
            "phase": phase,
            "percent": max(0, min(100, round(float(percent), 1))),
            "completed_bytes": max(0, int(completed_bytes)),
            "total_bytes": max(0, int(total_bytes)),
        })


def get_model_install_progress() -> dict[str, Any]:
    with _PROGRESS_LOCK:
        return dict(_INSTALL_PROGRESS)


def _sha256(
    path: Path,
    chunk_size: int = 8 * 1024 * 1024,
    on_chunk: Callable[[int], None] | None = None,
) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
            if on_chunk:
                on_chunk(len(chunk))
    return digest.hexdigest()


def _data_root() -> Path:
    runtime_root = Path(os.environ.get("JAVIS_DATA_ROOT") or Path(__file__).resolve().parent.parent)
    if runtime_root.name == "app" and runtime_root.parent.name == "runtime":
        return runtime_root.parent.parent
    return runtime_root


def _pointer_path() -> Path:
    return _data_root() / "local-ai-root.txt"


def _normalize_target(value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("请选择本地模型安装目录")
    target = Path(raw).expanduser().resolve()
    anchor = Path(target.anchor).resolve()
    if target == anchor:
        raise ValueError("不能把磁盘根目录直接作为模型安装目录")
    return target


def _resolve_addon_manifest(value: str) -> Path:
    source = Path(str(value or "").strip()).expanduser().resolve()
    manifest = source / ADDON_MANIFEST if source.is_dir() else source
    if manifest.name != ADDON_MANIFEST or not manifest.is_file():
        raise ValueError(f"请选择 {ADDON_MANIFEST} 或其所在目录")
    return manifest


def detect_model_addon(value: str) -> dict[str, Any]:
    """Recognize an add-on source without hashing or changing any files."""
    try:
        manifest = _resolve_addon_manifest(value)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("kind") != "javis-local-model-addon":
            return {"ok": True, "detected": False}
        suggested = Path(manifest.anchor) / "Javis-Local-Models"
        return {
            "ok": True,
            "detected": True,
            "manifest_path": str(manifest),
            "source_dir": str(manifest.parent),
            "suggested_install_dir": str(suggested.resolve()),
            "title": str(data.get("title") or "Javis R1 离线附加包"),
            "model": str(data.get("model") or "deepseek-r1:8b"),
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return {"ok": True, "detected": False}


def get_local_runtime_state() -> dict[str, Any]:
    """Report whether the Javis-managed portable runtime is actually installed."""
    root = _data_root()
    pointer = _pointer_path()
    try:
        selected = pointer.read_text(encoding="utf-8").strip() if pointer.is_file() else ""
        if selected:
            root = Path(selected).expanduser().resolve()
    except OSError:
        pass
    runtime = root / "local-ai" / "ollama" / "ollama.exe"
    models = root / "local-ai" / "models"
    return {
        "installed": runtime.is_file() and models.is_dir(),
        "root": str(root),
        "runtime_path": str(runtime),
        "models_path": str(models),
    }


def _reject_addon_as_target(manifest: Path, target: Path) -> None:
    source = manifest.parent.resolve()
    resolved_target = target.resolve()
    if resolved_target == source:
        raise ValueError("安装位置不能是 R1 附加包来源目录；请另选模型安装位置")


def _load_addon(value: str) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    manifest_path = _resolve_addon_manifest(value)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("kind") != "javis-local-model-addon":
        raise ValueError("附加包清单格式不受支持")
    payloads = data.get("payloads")
    if not isinstance(payloads, list) or not payloads:
        raise ValueError("附加包没有可安装载荷")
    normalized: list[dict[str, Any]] = []
    for item in payloads:
        if not isinstance(item, dict):
            raise ValueError("附加包载荷条目无效")
        relative = Path(str(item.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("附加包载荷路径越界")
        payload = (manifest_path.parent / relative).resolve()
        if payload.parent != manifest_path.parent or not payload.is_file():
            raise ValueError(f"附加包缺少载荷：{relative.name}")
        expected_size = int(item.get("size") or -1)
        if expected_size != payload.stat().st_size:
            raise ValueError(f"附加包载荷大小不匹配：{relative.name}")
        expected_hash = str(item.get("sha256") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise ValueError(f"附加包载荷缺少有效 SHA-256：{relative.name}")
        normalized.append({**item, "resolved_path": str(payload)})
    return manifest_path, data, normalized


def _safe_extract(
    archive_path: Path,
    destination: Path,
    on_chunk: Callable[[int], None] | None = None,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        for member in members:
            member_path = (root / member.filename).resolve()
            if member_path != root and root not in member_path.parents:
                raise ValueError(f"ZIP 条目越界：{member.filename}")
        for member in members:
            member_path = (root / member.filename).resolve()
            if member.is_dir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, member_path.open("wb") as output:
                for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                    output.write(chunk)
                    if on_chunk:
                        on_chunk(len(chunk))


def search_huggingface_models(query: str, limit: int = 12) -> dict[str, Any]:
    text = str(query or "").strip()[:120]
    response = requests.get(
        f"{HF_API}/models",
        params={"search": text, "filter": "gguf", "sort": "downloads", "direction": -1, "limit": max(1, min(int(limit), 30))},
        timeout=15,
    )
    response.raise_for_status()
    results = []
    for item in response.json():
        if not isinstance(item, dict):
            continue
        results.append({
            "repo_id": str(item.get("modelId") or item.get("id") or ""),
            "downloads": int(item.get("downloads") or 0),
            "likes": int(item.get("likes") or 0),
            "pipeline_tag": str(item.get("pipeline_tag") or ""),
            "gated": bool(item.get("gated", False)),
        })
    return {"ok": True, "models": results}


def _huggingface_model_info(repo_id: str, token: str = "") -> dict[str, Any]:
    if not REPO_PATTERN.fullmatch(repo_id):
        raise ValueError("Hugging Face 仓库名应为 owner/model")
    headers = {"Authorization": f"Bearer {token.strip()}"} if token.strip() else {}
    response = requests.get(
        f"{HF_API}/models/{repo_id}",
        params={"blobs": "true"},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def plan_model_install(values: dict[str, Any]) -> dict[str, Any]:
    source = str(values.get("source") or "offline").strip().lower()
    target = _normalize_target(str(values.get("install_dir") or ""))
    if source == "offline":
        manifest, data, payloads = _load_addon(str(values.get("addon_path") or ""))
        _reject_addon_as_target(manifest, target)
        required = sum(int(item["size"]) for item in payloads)
        free = shutil.disk_usage(target.parent if target.parent.exists() else target.anchor).free
        return {
            "ok": True,
            "plan_id": uuid.uuid4().hex,
            "source": "offline",
            "title": str(data.get("title") or "Javis R1 离线附加包"),
            "install_dir": str(target),
            "addon_path": str(manifest),
            "model": str(data.get("model") or "deepseek-r1:8b"),
            "base_url": "http://127.0.0.1:11435/v1",
            "download_bytes": 0,
            "required_bytes": required,
            "free_bytes": free,
            "payloads": [{key: item.get(key) for key in ("role", "path", "size", "sha256")} for item in payloads],
            "requires_approval": True,
        }
    if source == "local_gguf":
        gguf = Path(str(values.get("gguf_path") or "").strip()).expanduser().resolve()
        if gguf.suffix.lower() != ".gguf" or not gguf.is_file():
            raise ValueError("请选择有效的本地 GGUF 模型文件")
        runtime = target / "local-ai" / "ollama" / "ollama.exe"
        required = gguf.stat().st_size
        free = shutil.disk_usage(target.parent if target.parent.exists() else target.anchor).free
        return {
            "ok": True,
            "plan_id": uuid.uuid4().hex,
            "source": "local_gguf",
            "title": gguf.name,
            "filename": gguf.name,
            "install_dir": str(target),
            "gguf_path": str(gguf),
            "model": _normalize_model_name(str(values.get("model_name") or gguf.stem)),
            "base_url": "http://127.0.0.1:11435/v1",
            "download_bytes": 0,
            "required_bytes": required,
            "free_bytes": free,
            "runtime_ready": runtime.is_file(),
            "requires_approval": True,
        }
    if source != "huggingface":
        raise ValueError("安装来源只能是 offline、local_gguf 或 huggingface")
    repo_id = str(values.get("repo_id") or "").strip()
    info = _huggingface_model_info(repo_id, str(values.get("token") or ""))
    siblings = info.get("siblings") if isinstance(info.get("siblings"), list) else []
    gguf_files = []
    for sibling in siblings:
        name = str((sibling or {}).get("rfilename") or "")
        if name.lower().endswith(".gguf") and ".." not in Path(name).parts:
            lfs = (sibling or {}).get("lfs") if isinstance((sibling or {}).get("lfs"), dict) else {}
            gguf_files.append({
                "filename": name,
                "size": int((sibling or {}).get("size") or lfs.get("size") or 0),
                "sha256": str(lfs.get("sha256") or ""),
            })
    requested_file = str(values.get("filename") or "").strip()
    if requested_file:
        matches = [item for item in gguf_files if item["filename"] == requested_file]
        if not matches:
            raise ValueError("所选 GGUF 文件不在该仓库中")
        selected = matches[0]
    elif gguf_files:
        def recommendation(item: dict[str, Any]) -> tuple[int, int, str]:
            name = item["filename"].lower()
            preferences = ("q4_k_m", "q5_k_m", "q4_k_s", "q5_k_s", "q4_", "q5_", "q3_", "q6_", "q8_", "fp16")
            rank = next((index for index, token in enumerate(preferences) if token in name), len(preferences))
            return rank, item["size"] if item["size"] > 0 else 2**63, name

        selected = sorted(gguf_files, key=recommendation)[0]
    else:
        selected = None
    card = info.get("cardData") if isinstance(info.get("cardData"), dict) else {}
    bundled_runtime = target / "local-ai" / "ollama" / "ollama.exe"
    return {
        "ok": True,
        "plan_id": uuid.uuid4().hex,
        "source": "huggingface",
        "title": repo_id,
        "repo_id": repo_id,
        "filename": selected["filename"] if selected else "",
        "available_files": gguf_files,
        "install_dir": str(target),
        "download_bytes": selected["size"] if selected else 0,
        "required_bytes": selected["size"] if selected else 0,
        "license": str(card.get("license") or "未声明"),
        "gated": bool(info.get("gated", False)),
        "runtime_ready": bundled_runtime.is_file(),
        "requires_approval": True,
    }


def _install_offline(values: dict[str, Any], target: Path) -> dict[str, Any]:
    manifest, data, payloads = _load_addon(str(values.get("addon_path") or ""))
    _reject_addon_as_target(manifest, target)
    hash_total = sum(int(item["size"]) for item in payloads)
    hashed = 0

    def hash_progress(chunk: int) -> None:
        nonlocal hashed
        hashed += chunk
        _set_progress("running", "正在校验附加包完整性", 5 + (hashed / max(1, hash_total)) * 25, hashed, hash_total)

    for item in payloads:
        payload = Path(item["resolved_path"])
        if _sha256(payload, on_chunk=hash_progress) != str(item["sha256"]).lower():
            raise ValueError(f"附加包 SHA-256 校验失败：{payload.name}")
    target.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".javis-model-install-", dir=target))
    try:
        extract_total = 0
        for item in payloads:
            with zipfile.ZipFile(Path(item["resolved_path"])) as archive:
                extract_total += sum(member.file_size for member in archive.infolist() if not member.is_dir())
        extracted_bytes = 0

        def extract_progress(chunk: int) -> None:
            nonlocal extracted_bytes
            extracted_bytes += chunk
            _set_progress("running", "正在解压本地模型与 Ollama", 30 + (extracted_bytes / max(1, extract_total)) * 60, extracted_bytes, extract_total)

        for item in payloads:
            _safe_extract(Path(item["resolved_path"]), staging, extract_progress)
        extracted = staging / "local-ai"
        if not extracted.is_dir():
            raise ValueError("附加包未生成 local-ai 目录")
        destination = target / "local-ai"
        if destination.exists():
            shutil.copytree(extracted, destination, dirs_exist_ok=True)
        else:
            os.replace(extracted, destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    pointer = _pointer_path()
    _set_progress("running", "正在写入 Javis 本地模型配置", 95)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(target), encoding="utf-8")
    return {
        "ok": True,
        "source": "offline",
        "model": str(data.get("model") or "deepseek-r1:8b"),
        "base_url": "http://127.0.0.1:11435/v1",
        "install_dir": str(target),
        "pointer": str(pointer),
        "restart_required": True,
        "message": "R1 与便携 Ollama 已安装；重启 Javis 后启用",
    }


def _normalize_model_name(value: str) -> str:
    name = str(value or "").strip()[:120]
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", name).strip("-.") or "javis-local-model"


def _persist_installed_model_configuration(model: str, base_url: str) -> None:
    """Persist the installed local profile before reporting 100% completion."""
    from utils.config_api import get_model_connection_settings, set_model_connection_settings

    current = get_model_connection_settings()
    routes: dict[str, Any] = {}
    for route_name in ("live", "code"):
        existing = current.get("routes", {}).get(route_name, {})
        routes[route_name] = {
            "source": existing.get("source", "local"),
            "local": {"model": model, "base_url": base_url},
            "remote": dict(existing.get("remote", {})),
        }
    result = set_model_connection_settings({
        "source": current.get("source", "local"),
        "local": {"model": model, "base_url": base_url},
        "share_live_code": bool(current.get("share_live_code", True)),
        "routes": routes,
    })
    if not result.get("applied"):
        raise RuntimeError(str(result.get("error") or "本地模型配置保存失败"))


def _install_local_gguf(values: dict[str, Any], target: Path) -> dict[str, Any]:
    source = Path(str(values.get("gguf_path") or "").strip()).expanduser().resolve()
    if source.suffix.lower() != ".gguf" or not source.is_file():
        raise ValueError("请选择有效的本地 GGUF 模型文件")
    runtime = target / "local-ai" / "ollama" / "ollama.exe"
    if not runtime.is_file():
        raise RuntimeError("所选位置尚未安装 Javis Ollama；请先用 R1 附加包完成运行时安装")
    model_root = target / "local-ai" / "imported"
    model_root.mkdir(parents=True, exist_ok=True)
    destination = (model_root / source.name).resolve()
    total = source.stat().st_size
    copied = 0
    digest = hashlib.sha256()
    if source != destination:
        partial = destination.with_suffix(destination.suffix + ".part")
        with source.open("rb") as input_stream, partial.open("wb") as output_stream:
            for chunk in iter(lambda: input_stream.read(8 * 1024 * 1024), b""):
                output_stream.write(chunk)
                digest.update(chunk)
                copied += len(chunk)
                _set_progress("running", "正在导入本地 GGUF", 5 + (copied / max(1, total)) * 75, copied, total)
        os.replace(partial, destination)
    else:
        _set_progress("running", "本地 GGUF 已在目标位置", 80, total, total)
    model_name = _normalize_model_name(str(values.get("model_name") or source.stem))
    adapter = model_root / "Javis-Modelfile"
    adapter.write_text(f'FROM "{destination}"\n', encoding="utf-8")
    _set_progress("running", "正在适配模型到 Ollama", 85)
    _adapt_gguf_with_ollama(runtime, target / "local-ai" / "models", adapter, model_name)
    pointer = _pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(target), encoding="utf-8")
    return {
        "ok": True,
        "source": "local_gguf",
        "model": model_name,
        "base_url": "http://127.0.0.1:11435/v1",
        "gguf_path": str(destination),
        "sha256": digest.hexdigest() if source != destination else _sha256(destination),
        "install_dir": str(target),
        "restart_required": True,
        "message": "本地 GGUF 已导入并自动适配；重启 Javis 后启用",
    }


def _download_huggingface(values: dict[str, Any], target: Path) -> dict[str, Any]:
    repo_id = str(values.get("repo_id") or "").strip()
    filename = str(values.get("filename") or "").strip()
    if not REPO_PATTERN.fullmatch(repo_id) or not filename.lower().endswith(".gguf") or ".." in Path(filename).parts:
        raise ValueError("Hugging Face 仓库或 GGUF 文件名无效")
    token = str(values.get("token") or "").strip()
    info = _huggingface_model_info(repo_id, token)
    selected_metadata: dict[str, Any] = {}
    for sibling in info.get("siblings", []):
        if isinstance(sibling, dict) and sibling.get("rfilename") == filename:
            selected_metadata = sibling
            break
    if not selected_metadata:
        raise ValueError("所选 GGUF 文件不在该仓库中")
    lfs = selected_metadata.get("lfs") if isinstance(selected_metadata.get("lfs"), dict) else {}
    expected_size = int(selected_metadata.get("size") or lfs.get("size") or 0)
    expected_hash = str(lfs.get("sha256") or "").lower()
    runtime = target / "local-ai" / "ollama" / "ollama.exe"
    if not runtime.is_file():
        raise RuntimeError("所选位置尚未安装 Javis Ollama；请先用 R1 附加包完成运行时安装")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    model_root = target / "local-ai" / "huggingface" / repo_id.replace("/", "--")
    destination = (model_root / Path(filename).name).resolve()
    model_root.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    downloaded = partial.stat().st_size if partial.exists() else 0
    if downloaded:
        headers["Range"] = f"bytes={downloaded}-"
    url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
    with requests.get(url, headers=headers, stream=True, timeout=(20, 120), allow_redirects=True) as response:
        if response.status_code == 200 and downloaded:
            downloaded = 0
        if response.status_code not in {200, 206}:
            response.raise_for_status()
        mode = "ab" if downloaded and response.status_code == 206 else "wb"
        with partial.open(mode) as stream:
            for chunk in response.iter_content(8 * 1024 * 1024):
                if chunk:
                    stream.write(chunk)
                    downloaded += len(chunk)
                    _set_progress("running", "正在从 Hugging Face 下载 GGUF", 5 + (downloaded / max(1, expected_size)) * 70, downloaded, expected_size)
    os.replace(partial, destination)
    if expected_size and destination.stat().st_size != expected_size:
        raise RuntimeError("Hugging Face 下载大小校验失败")
    if expected_hash and _sha256(destination) != expected_hash:
        raise RuntimeError("Hugging Face 下载 SHA-256 校验失败")
    pointer = _pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(target), encoding="utf-8")
    model_name = _normalize_model_name(str(values.get("model_name") or Path(filename).stem))
    adapter = model_root / "Javis-Modelfile"
    adapter.write_text(f'FROM "{destination}"\n', encoding="utf-8")
    _set_progress("running", "正在校验并适配 Hugging Face 模型", 85)
    _adapt_gguf_with_ollama(runtime, target / "local-ai" / "models", adapter, model_name)
    return {
        "ok": True,
        "source": "huggingface",
        "model": model_name,
        "base_url": "http://127.0.0.1:11435/v1",
        "gguf_path": str(destination),
        "modelfile": str(adapter),
        "install_dir": str(target),
        "restart_required": True,
        "adaptation_pending": False,
        "message": "GGUF 已下载并自动适配到 Ollama；重启 Javis 后启用",
    }


def _adapt_gguf_with_ollama(runtime: Path, models: Path, modelfile: Path, model_name: str) -> None:
    models.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    host = f"127.0.0.1:{port}"
    env = os.environ.copy()
    env["OLLAMA_HOST"] = host
    env["OLLAMA_MODELS"] = str(models)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    server = subprocess.Popen(
        [str(runtime), "serve"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    try:
        ready = False
        for _ in range(60):
            if server.poll() is not None:
                raise RuntimeError("Ollama 适配服务启动失败")
            try:
                response = requests.get(f"http://{host}/api/tags", timeout=1)
                if response.status_code == 200:
                    ready = True
                    break
            except requests.RequestException:
                pass
            time.sleep(0.25)
        if not ready:
            raise RuntimeError("Ollama 适配服务启动超时")
        result = subprocess.run(
            [str(runtime), "create", model_name, "-f", str(modelfile)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            creationflags=creationflags,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Ollama 自动适配失败：{result.stdout[-300:]}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)


def install_model(values: dict[str, Any]) -> dict[str, Any]:
    if values.get("approved") is not True or values.get("confirmation") != APPROVAL_MARKER:
        raise PermissionError("必须由用户在安装计划中明确确认后才能安装或下载")
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise RuntimeError("已有本地模型安装任务正在进行，请勿重复提交")
    try:
        _set_progress("running", "正在验证安装计划", 1)
        target = _normalize_target(str(values.get("install_dir") or ""))
        source = str(values.get("source") or "offline").strip().lower()
        if source == "offline":
            result = _install_offline(values, target)
        elif source == "local_gguf":
            result = _install_local_gguf(values, target)
        elif source == "huggingface":
            result = _download_huggingface(values, target)
        else:
            raise ValueError("安装来源只能是 offline、local_gguf 或 huggingface")
        _set_progress("running", "正在保存 Live 与 Code 本地模型配置", 98)
        _persist_installed_model_configuration(
            str(result.get("model") or "deepseek-r1:8b"),
            str(result.get("base_url") or "http://127.0.0.1:11435/v1"),
        )
        result["configuration_applied"] = True
        _set_progress("completed", "安装与自动适配已完成", 100)
        return result
    except Exception as error:
        _set_progress("failed", str(error)[:200], get_model_install_progress().get("percent", 0))
        raise
    finally:
        _INSTALL_LOCK.release()
