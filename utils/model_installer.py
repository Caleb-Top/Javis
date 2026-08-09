"""Consent-gated local model installation and Hugging Face discovery.

The application may inspect an offline add-on or public model metadata without
changing the machine. Any extraction or network download requires an explicit
approval marker from the settings UI.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

import requests


ADDON_MANIFEST = "Javis-R1-8B-Addon.manifest.json"
APPROVAL_MARKER = "install-local-model"
HF_API = "https://huggingface.co/api"
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
PLAN_TOKEN_TTL_SECONDS = 30 * 60
MAX_ZIP_MEMBER_BYTES = 64 * 1024 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 128 * 1024 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 250
MAX_ZIP_MEMBERS = 100_000
MAX_HF_DOWNLOAD_BYTES = 64 * 1024 * 1024 * 1024
MAX_HF_REDIRECTS = 5
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_PLAN_TOKEN_SECRET = secrets.token_bytes(32)
_SUPPORTED_GGUF_ARCHITECTURES = {
    "deepseek",
    "deepseek2",
    "gemma",
    "gemma2",
    "llama",
    "mistral",
    "phi2",
    "phi3",
    "qwen2",
    "qwen2moe",
    "stablelm",
    "starcoder2",
}
_SUPPORTED_GGUF_FILE_TYPES = {
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    10: "Q2_K",
    11: "Q3_K_S",
    12: "Q3_K_M",
    13: "Q3_K_L",
    14: "Q4_K_S",
    15: "Q4_K_M",
    16: "Q5_K_S",
    17: "Q5_K_M",
    18: "Q6_K",
}
_GGML_TENSOR_LAYOUTS = {
    0: (1, 4),       # F32
    1: (1, 2),       # F16
    2: (32, 18),     # Q4_0
    3: (32, 20),     # Q4_1
    6: (32, 22),     # Q5_0
    7: (32, 24),     # Q5_1
    8: (32, 34),     # Q8_0
    9: (32, 40),     # Q8_1
    10: (256, 84),   # Q2_K
    11: (256, 110),  # Q3_K
    12: (256, 144),  # Q4_K
    13: (256, 176),  # Q5_K
    14: (256, 210),  # Q6_K
    15: (256, 292),  # Q8_K
}
_INSTALL_LOCK = threading.Lock()
_PROGRESS_LOCK = threading.Lock()
_INSTALL_CONTROL = threading.Condition(threading.RLock())
_ACTIVE_INSTALL_JOB_ID = ""
_INSTALL_JOB_ACTIVE = False
_INSTALL_PAUSE_REQUESTED = False
_INSTALL_CANCEL_REQUESTED = False
_INSTALL_PROGRESS: dict[str, Any] = {
    "state": "idle",
    "phase": "等待安装",
    "percent": 0,
    "completed_bytes": 0,
    "total_bytes": 0,
    "job_id": "",
    "cancellable": False,
    "pausable": False,
    "paused": False,
    "cancelled": False,
}


class ModelInstallCancelled(RuntimeError):
    """Raised at a cooperative checkpoint after the user cancels an install."""


def _begin_install_job() -> str:
    global _ACTIVE_INSTALL_JOB_ID, _INSTALL_JOB_ACTIVE
    global _INSTALL_PAUSE_REQUESTED, _INSTALL_CANCEL_REQUESTED
    with _INSTALL_CONTROL:
        _ACTIVE_INSTALL_JOB_ID = uuid.uuid4().hex
        _INSTALL_JOB_ACTIVE = True
        _INSTALL_PAUSE_REQUESTED = False
        _INSTALL_CANCEL_REQUESTED = False
        return _ACTIVE_INSTALL_JOB_ID


def _finish_install_job() -> None:
    global _INSTALL_JOB_ACTIVE, _INSTALL_PAUSE_REQUESTED, _INSTALL_CANCEL_REQUESTED
    with _INSTALL_CONTROL:
        _INSTALL_JOB_ACTIVE = False
        _INSTALL_PAUSE_REQUESTED = False
        _INSTALL_CANCEL_REQUESTED = False
        _INSTALL_CONTROL.notify_all()


def _install_control_result(job_id: str) -> tuple[bool, dict[str, Any]]:
    normalized = str(job_id or "").strip().lower()
    if not _INSTALL_JOB_ACTIVE or not normalized or normalized != _ACTIVE_INSTALL_JOB_ID:
        return False, {"ok": False, "error": "install_job_not_active"}
    progress = get_model_install_progress()
    if not progress.get("cancellable", False):
        return False, {"ok": False, "error": "install_job_not_controllable", "job_id": normalized}
    return True, {"ok": True, "job_id": normalized}


def pause_model_install(job_id: str) -> dict[str, Any]:
    global _INSTALL_PAUSE_REQUESTED
    with _INSTALL_CONTROL:
        accepted, result = _install_control_result(job_id)
        if not accepted:
            return result
        if not get_model_install_progress().get("pausable", False):
            return {"ok": False, "error": "install_job_not_pausable", "job_id": result["job_id"]}
        _INSTALL_PAUSE_REQUESTED = True
        current = get_model_install_progress()
        _set_progress(
            "pausing",
            "正在安全暂停本地模型安装",
            current.get("percent", 0),
            current.get("completed_bytes", 0),
            current.get("total_bytes", 0),
        )
        return result


def resume_model_install(job_id: str) -> dict[str, Any]:
    global _INSTALL_PAUSE_REQUESTED
    with _INSTALL_CONTROL:
        accepted, result = _install_control_result(job_id)
        if not accepted:
            return result
        _INSTALL_PAUSE_REQUESTED = False
        current = get_model_install_progress()
        _set_progress(
            "resuming",
            "正在继续本地模型安装",
            current.get("percent", 0),
            current.get("completed_bytes", 0),
            current.get("total_bytes", 0),
        )
        _INSTALL_CONTROL.notify_all()
        return result


def cancel_model_install(job_id: str) -> dict[str, Any]:
    global _INSTALL_CANCEL_REQUESTED
    with _INSTALL_CONTROL:
        accepted, result = _install_control_result(job_id)
        if not accepted:
            return result
        _INSTALL_CANCEL_REQUESTED = True
        current = get_model_install_progress()
        _set_progress(
            "cancelling",
            "正在取消并回滚本地模型安装",
            current.get("percent", 0),
            current.get("completed_bytes", 0),
            current.get("total_bytes", 0),
        )
        _INSTALL_CONTROL.notify_all()
        return result


def _install_control_checkpoint() -> None:
    """Pause or cancel only at a boundary where rollback remains safe."""
    with _INSTALL_CONTROL:
        if _INSTALL_CANCEL_REQUESTED:
            raise ModelInstallCancelled("用户已取消本地模型安装")
        while _INSTALL_PAUSE_REQUESTED:
            current = get_model_install_progress()
            _set_progress(
                "paused",
                "本地模型安装已暂停",
                current.get("percent", 0),
                current.get("completed_bytes", 0),
                current.get("total_bytes", 0),
            )
            _INSTALL_CONTROL.wait()
            if _INSTALL_CANCEL_REQUESTED:
                raise ModelInstallCancelled("用户已取消本地模型安装")


def _set_progress(
    state: str,
    phase: str,
    percent: float,
    completed_bytes: int = 0,
    total_bytes: int = 0,
    *,
    pausable: bool | None = None,
) -> None:
    with _INSTALL_CONTROL:
        active = _INSTALL_JOB_ACTIVE
        job_id = _ACTIVE_INSTALL_JOB_ID if active else ""
    control_locked_states = {
        "cancelling",
        "committing",
        "rolling_back",
        "completed",
        "failed",
        "cancelled",
    }
    with _PROGRESS_LOCK:
        _INSTALL_PROGRESS.update({
            "state": state,
            "phase": phase,
            "percent": max(0, min(100, round(float(percent), 1))),
            "completed_bytes": max(0, int(completed_bytes)),
            "total_bytes": max(0, int(total_bytes)),
            "job_id": job_id,
            "cancellable": active and state not in control_locked_states,
            "pausable": (
                active and state not in control_locked_states
                if pausable is None else bool(pausable)
            ),
            "paused": state == "paused",
            "cancelled": state == "cancelled",
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
            _install_control_checkpoint()
            digest.update(chunk)
            if on_chunk:
                on_chunk(len(chunk))
    return digest.hexdigest()


def _read_exact(stream: Any, size: int, label: str) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise ValueError(f"GGUF {label} 不完整")
    return value


def _skip_exact(stream: Any, size: int, label: str) -> None:
    remaining = int(size)
    while remaining:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise ValueError(f"GGUF {label} 不完整")
        remaining -= len(chunk)


def _read_gguf_string(stream: Any, *, capture: bool) -> str:
    length = struct.unpack("<Q", _read_exact(stream, 8, "字符串长度"))[0]
    if length > 16 * 1024 * 1024:
        raise ValueError("GGUF 元数据字符串过大")
    if capture:
        try:
            return _read_exact(stream, length, "字符串").decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("GGUF 元数据不是有效 UTF-8") from error
    _skip_exact(stream, length, "字符串")
    return ""


def _read_gguf_value(stream: Any, value_type: int, *, capture: bool) -> Any:
    fixed_types = {
        0: ("<B", 1),
        1: ("<b", 1),
        2: ("<H", 2),
        3: ("<h", 2),
        4: ("<I", 4),
        5: ("<i", 4),
        6: ("<f", 4),
        7: ("<?", 1),
        10: ("<Q", 8),
        11: ("<q", 8),
        12: ("<d", 8),
    }
    if value_type in fixed_types:
        fmt, size = fixed_types[value_type]
        raw = _read_exact(stream, size, "元数据值")
        return struct.unpack(fmt, raw)[0] if capture else None
    if value_type == 8:
        return _read_gguf_string(stream, capture=capture)
    if value_type == 9:
        item_type = struct.unpack("<I", _read_exact(stream, 4, "数组类型"))[0]
        count = struct.unpack("<Q", _read_exact(stream, 8, "数组长度"))[0]
        if count > 10_000_000:
            raise ValueError("GGUF 元数据数组过大")
        fixed = fixed_types.get(item_type)
        if fixed:
            _skip_exact(stream, fixed[1] * count, "元数据数组")
            return None
        if item_type == 8:
            for _ in range(count):
                _read_gguf_string(stream, capture=False)
            return None
        raise ValueError("GGUF 包含不受支持的嵌套元数据数组")
    raise ValueError(f"GGUF 元数据类型不受支持：{value_type}")


def _inspect_gguf(path: Path) -> dict[str, Any]:
    """Validate bounded metadata, tensor descriptors, and tensor data extents."""
    file_size = path.stat().st_size
    with path.open("rb") as stream:
        if _read_exact(stream, 4, "magic") != b"GGUF":
            raise ValueError("文件不是有效 GGUF（magic 不匹配）")
        version, tensor_count, metadata_count = struct.unpack(
            "<IQQ",
            _read_exact(stream, 20, "头部"),
        )
        if version not in {2, 3}:
            raise ValueError(f"GGUF 版本不受支持：{version}")
        if tensor_count <= 0 or tensor_count > 1_000_000:
            raise ValueError("GGUF 张量数量无效")
        if metadata_count > 100_000:
            raise ValueError("GGUF 元数据条目过多")
        metadata: dict[str, Any] = {}
        wanted = {"general.architecture", "general.file_type", "general.alignment"}
        for _ in range(metadata_count):
            key = _read_gguf_string(stream, capture=True)
            value_type = struct.unpack("<I", _read_exact(stream, 4, "元数据类型"))[0]
            value = _read_gguf_value(stream, value_type, capture=key in wanted)
            if key in wanted:
                metadata[key] = value
        try:
            alignment = int(metadata.get("general.alignment", 32))
        except (TypeError, ValueError) as error:
            raise ValueError("GGUF 张量对齐值无效") from error
        if alignment < 1 or alignment > 4096 or alignment & (alignment - 1):
            raise ValueError("GGUF 张量对齐值必须是 1 到 4096 的二次幂")

        tensors: list[tuple[int, int]] = []
        names: set[str] = set()
        for _ in range(tensor_count):
            name = _read_gguf_string(stream, capture=True)
            if not name or name in names:
                raise ValueError("GGUF 张量名称为空或重复")
            names.add(name)
            dimensions_count = struct.unpack("<I", _read_exact(stream, 4, "张量维度数"))[0]
            if dimensions_count < 1 or dimensions_count > 4:
                raise ValueError("GGUF 张量维度数无效")
            elements = 1
            for _dimension in range(dimensions_count):
                dimension = struct.unpack("<Q", _read_exact(stream, 8, "张量维度"))[0]
                if dimension <= 0 or elements > (2**63 - 1) // dimension:
                    raise ValueError("GGUF 张量维度无效或过大")
                elements *= dimension
            tensor_type = struct.unpack("<I", _read_exact(stream, 4, "张量类型"))[0]
            layout = _GGML_TENSOR_LAYOUTS.get(tensor_type)
            if layout is None:
                raise ValueError(f"GGUF 张量类型不受支持：{tensor_type}")
            offset = struct.unpack("<Q", _read_exact(stream, 8, "张量偏移"))[0]
            if offset % alignment:
                raise ValueError("GGUF 张量偏移未按声明边界对齐")
            block_elements, block_bytes = layout
            tensor_bytes = ((elements + block_elements - 1) // block_elements) * block_bytes
            tensors.append((offset, tensor_bytes))

        descriptor_end = stream.tell()
        data_start = (descriptor_end + alignment - 1) // alignment * alignment
        if data_start > file_size:
            raise ValueError("GGUF 张量区不完整")
        intervals: list[tuple[int, int]] = []
        for offset, tensor_bytes in tensors:
            if offset > file_size - data_start:
                raise ValueError("GGUF 张量偏移越过文件末尾")
            tensor_end = data_start + offset + tensor_bytes
            if tensor_end > file_size:
                raise ValueError("GGUF 张量数据不完整")
            intervals.append((data_start + offset, tensor_end))
        previous_end = data_start
        for tensor_start, tensor_end in sorted(intervals):
            if tensor_start < previous_end:
                raise ValueError("GGUF 张量数据区相互重叠")
            previous_end = tensor_end
    architecture = str(metadata.get("general.architecture") or "").strip().lower()
    if architecture not in _SUPPORTED_GGUF_ARCHITECTURES:
        raise ValueError(f"GGUF 模型架构不受支持：{architecture or '未声明'}")
    try:
        file_type = int(metadata["general.file_type"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("GGUF 未声明有效量化类型") from error
    quantization = _SUPPORTED_GGUF_FILE_TYPES.get(file_type)
    if not quantization:
        raise ValueError(f"GGUF 量化类型不受支持：{file_type}")
    return {
        "gguf_version": version,
        "architecture": architecture,
        "file_type": file_type,
        "quantization": quantization,
    }


def _normalize_targets(value: Any) -> list[str]:
    if value is None:
        return ["live", "code"]
    if not isinstance(value, list):
        raise ValueError("模型应用目标必须是 live/code 数组")
    invalid = [item for item in value if item not in {"live", "code"}]
    if invalid:
        raise ValueError("模型应用目标只能包含 live 或 code")
    return [name for name in ("live", "code") if name in value]


def _plan_core(plan: dict[str, Any]) -> dict[str, Any]:
    common = {
        "schema": 1,
        "source": plan["source"],
        "install_dir": plan["install_dir"],
        "targets": list(plan["targets"]),
        "model": plan.get("model", ""),
        "base_url": plan.get("base_url", ""),
        "license": plan.get("license", ""),
    }
    if plan["source"] == "offline":
        common.update({
            "addon_path": plan["addon_path"],
            "payloads": plan["payloads"],
        })
    elif plan["source"] == "local_gguf":
        common.update({
            "gguf_path": plan["gguf_path"],
            "filename": plan["filename"],
            "size": plan["required_bytes"],
            "sha256": plan["sha256"],
            "architecture": plan["architecture"],
            "file_type": plan["file_type"],
            "quantization": plan["quantization"],
        })
    elif plan["source"] == "huggingface":
        common.update({
            "repo_id": plan["repo_id"],
            "repo_revision": plan["repo_revision"],
            "filename": plan["filename"],
            "size": plan["required_bytes"],
            "sha256": plan["sha256"],
            "gated": plan["gated"],
            "trust_remote_code": False,
        })
    return common


def _token_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _token_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except Exception as error:
        raise PermissionError("安装计划令牌格式无效") from error


def _issue_plan_token(plan: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "issued_at": int(time.time()),
            "plan": _plan_core(plan),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_PLAN_TOKEN_SECRET, payload, hashlib.sha256).digest()
    return f"{_token_encode(payload)}.{_token_encode(signature)}"


def _read_plan_token(value: Any) -> dict[str, Any]:
    token = str(value or "").strip()
    if not token or token.count(".") != 1:
        raise PermissionError("请先生成并确认安装计划")
    payload_value, signature_value = token.split(".", 1)
    payload = _token_decode(payload_value)
    supplied_signature = _token_decode(signature_value)
    expected_signature = hmac.new(_PLAN_TOKEN_SECRET, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise PermissionError("安装计划令牌签名无效")
    try:
        decoded = json.loads(payload.decode("utf-8"))
        issued_at = int(decoded["issued_at"])
        plan = decoded["plan"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PermissionError("安装计划令牌内容无效") from error
    if int(time.time()) - issued_at > PLAN_TOKEN_TTL_SECONDS or issued_at > int(time.time()) + 60:
        raise PermissionError("安装计划已过期，请重新生成")
    if not isinstance(plan, dict):
        raise PermissionError("安装计划令牌内容无效")
    return plan


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


def _validated_zip_members(archive: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], int]:
    members = archive.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise ValueError("ZIP 条目总数超过安全上限")
    total = 0
    names: set[str] = set()
    for member in members:
        normalized_name = member.filename.replace("\\", "/")
        if not normalized_name or "\x00" in normalized_name:
            raise ValueError("ZIP 条目名称无效")
        parts = [part for part in normalized_name.split("/") if part not in {"", "."}]
        if normalized_name.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", normalized_name):
            raise ValueError(f"ZIP 条目越界：{member.filename}")
        for part in parts:
            if ":" in part or part != part.rstrip(" ."):
                raise ValueError(f"ZIP 条目含 Windows 不安全名称：{member.filename}")
            if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
                raise ValueError(f"ZIP 条目含 Windows 保留名称：{member.filename}")
        canonical = "/".join(parts).casefold()
        if canonical in names and not member.is_dir():
            raise ValueError(f"ZIP 包含重复条目：{member.filename}")
        names.add(canonical)
        unix_mode = (member.external_attr >> 16) & 0xFFFF
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise ValueError(f"ZIP 不允许符号链接：{member.filename}")
        if member.flag_bits & 0x1:
            raise ValueError(f"ZIP 不允许加密条目：{member.filename}")
        if member.is_dir():
            continue
        if member.file_size < 0 or member.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError(f"ZIP 单文件解压大小超过安全上限：{member.filename}")
        total += member.file_size
        if total > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("ZIP 解压总大小超过安全上限")
        if member.file_size:
            if member.compress_size <= 0:
                raise ValueError(f"ZIP 条目压缩比超过安全上限：{member.filename}")
            if member.file_size / member.compress_size > MAX_ZIP_COMPRESSION_RATIO:
                raise ValueError(f"ZIP 条目压缩比超过安全上限：{member.filename}")
    return members, total


def _offline_extract_budget(payloads: list[dict[str, Any]]) -> int:
    total = 0
    for item in payloads:
        try:
            with zipfile.ZipFile(Path(item["resolved_path"])) as archive:
                _members, archive_total = _validated_zip_members(archive)
        except zipfile.BadZipFile as error:
            raise ValueError(f"附加包载荷不是有效 ZIP：{Path(item['resolved_path']).name}") from error
        total += archive_total
        if total > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("附加包解压总大小超过安全上限")
    return total


def _safe_extract(
    archive_path: Path,
    destination: Path,
    on_chunk: Callable[[int], None] | None = None,
) -> None:
    _install_control_checkpoint()
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        members, _expected_total = _validated_zip_members(archive)
        for member in members:
            member_path = (root / member.filename).resolve()
            if member_path != root and root not in member_path.parents:
                raise ValueError(f"ZIP 条目越界：{member.filename}")
        destination.mkdir(parents=True, exist_ok=True)
        extracted_total = 0
        for member in members:
            _install_control_checkpoint()
            member_path = (root / member.filename).resolve()
            if member.is_dir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            member_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, member_path.open("wb") as output:
                extracted_member = 0
                for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                    _install_control_checkpoint()
                    extracted_member += len(chunk)
                    extracted_total += len(chunk)
                    if extracted_member > member.file_size or extracted_member > MAX_ZIP_MEMBER_BYTES:
                        raise ValueError(f"ZIP 单文件实际解压大小超过安全上限：{member.filename}")
                    if extracted_total > MAX_ZIP_TOTAL_BYTES:
                        raise ValueError("ZIP 实际解压总大小超过安全上限")
                    output.write(chunk)
                    if on_chunk:
                        on_chunk(len(chunk))
                if extracted_member != member.file_size:
                    raise ValueError(f"ZIP 条目实际解压大小不匹配：{member.filename}")


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


def _finalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    plan["plan_id"] = uuid.uuid4().hex
    plan["requires_approval"] = True
    plan["plan_token"] = _issue_plan_token(plan)
    _set_progress("planned", "安装计划已生成，等待用户确认", 0)
    return plan


def plan_model_install(values: dict[str, Any]) -> dict[str, Any]:
    if values.get("trust_remote_code") is True:
        raise ValueError("Javis 不允许 Hugging Face remote code；trust_remote_code 必须为 false")
    source = str(values.get("source") or "offline").strip().lower()
    target = _normalize_target(str(values.get("install_dir") or ""))
    targets = _normalize_targets(values.get("targets"))
    if source == "offline":
        manifest, data, payloads = _load_addon(str(values.get("addon_path") or ""))
        _reject_addon_as_target(manifest, target)
        required = _offline_extract_budget(payloads)
        free = shutil.disk_usage(target.parent if target.parent.exists() else target.anchor).free
        return _finalize_plan({
            "ok": True,
            "source": "offline",
            "title": str(data.get("title") or "Javis R1 离线附加包"),
            "install_dir": str(target),
            "targets": targets,
            "addon_path": str(manifest),
            "model": str(data.get("model") or "deepseek-r1:8b"),
            "base_url": "http://127.0.0.1:11435/v1",
            "license": str(data.get("license") or "Javis 离线附加包许可"),
            "download_bytes": 0,
            "required_bytes": required,
            "free_bytes": free,
            "payloads": [{key: item.get(key) for key in ("role", "path", "size", "sha256")} for item in payloads],
        })
    if source == "local_gguf":
        gguf = Path(str(values.get("gguf_path") or "").strip()).expanduser().resolve()
        if gguf.suffix.lower() != ".gguf" or not gguf.is_file():
            raise ValueError("请选择有效的本地 GGUF 模型文件")
        metadata = _inspect_gguf(gguf)
        runtime = target / "local-ai" / "ollama" / "ollama.exe"
        required = gguf.stat().st_size
        free = shutil.disk_usage(target.parent if target.parent.exists() else target.anchor).free
        return _finalize_plan({
            "ok": True,
            "source": "local_gguf",
            "title": gguf.name,
            "filename": gguf.name,
            "install_dir": str(target),
            "targets": targets,
            "gguf_path": str(gguf),
            "model": _normalize_model_name(str(values.get("model_name") or gguf.stem)),
            "base_url": "http://127.0.0.1:11435/v1",
            "license": str(values.get("license") or "用户提供的本地文件；许可由用户确认"),
            "download_bytes": 0,
            "required_bytes": required,
            "free_bytes": free,
            "sha256": _sha256(gguf),
            **metadata,
            "runtime_ready": runtime.is_file(),
        })
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
                "sha256": str(lfs.get("sha256") or "").lower(),
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
    if selected is None:
        raise ValueError("仓库中没有可安装的 GGUF 文件")
    if not re.fullmatch(r"[0-9a-f]{64}", str(selected.get("sha256") or "")):
        raise ValueError("所选 Hugging Face 文件缺少可信 LFS SHA-256")
    if int(selected.get("size") or 0) <= 0:
        raise ValueError("所选 Hugging Face 文件缺少可信大小")
    if int(selected["size"]) > MAX_HF_DOWNLOAD_BYTES:
        raise ValueError("所选 Hugging Face 文件超过安全下载大小上限")
    card = info.get("cardData") if isinstance(info.get("cardData"), dict) else {}
    bundled_runtime = target / "local-ai" / "ollama" / "ollama.exe"
    return _finalize_plan({
        "ok": True,
        "source": "huggingface",
        "title": repo_id,
        "repo_id": repo_id,
        "repo_revision": str(info.get("sha") or "main"),
        "filename": selected["filename"],
        "available_files": gguf_files,
        "install_dir": str(target),
        "targets": targets,
        "model": _normalize_model_name(str(values.get("model_name") or Path(selected["filename"]).stem)),
        "base_url": "http://127.0.0.1:11435/v1",
        "download_bytes": selected["size"],
        "required_bytes": selected["size"],
        "free_bytes": shutil.disk_usage(target.parent if target.parent.exists() else target.anchor).free,
        "sha256": selected["sha256"],
        "license": str(card.get("license") or "未声明"),
        "gated": bool(info.get("gated", False)),
        "trust_remote_code": False,
        "runtime_ready": bundled_runtime.is_file(),
    })


class _OverlayTransaction:
    """Commit staged files with a reversible per-file journal."""

    def __init__(self, staging_root: Path, staged_tree: Path, destination: Path):
        self.staging_root = staging_root
        self.staged_tree = staged_tree
        self.destination = destination
        self.backup_root = Path(tempfile.mkdtemp(
            prefix=".javis-model-rollback-",
            dir=destination.parent.parent,
        ))
        self.actions: list[tuple[Path, Path | None]] = []
        self.created_directories: list[Path] = []
        self.pointer = _pointer_path()
        self.pointer_existed = self.pointer.is_file()
        self.pointer_bytes = self.pointer.read_bytes() if self.pointer_existed else b""
        self.committed = False
        self.closed = False

    @staticmethod
    def _remove(path: Path) -> None:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    def _ensure_directory(self, path: Path) -> None:
        missing: list[Path] = []
        cursor = path
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        path.mkdir(parents=True, exist_ok=True)
        self.created_directories.extend(reversed(missing))

    def commit(self, selected_root: Path) -> None:
        self.committed = True
        try:
            self._ensure_directory(self.destination)
            for staged in sorted(path for path in self.staged_tree.rglob("*") if path.is_file()):
                relative = staged.relative_to(self.staged_tree)
                final = self.destination / relative
                self._ensure_directory(final.parent)
                backup: Path | None = None
                if final.exists():
                    backup = self.backup_root / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(final, backup)
                self.actions.append((final, backup))
                os.replace(staged, final)

            self.pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer_temp = self.pointer.with_suffix(".txt.tmp")
            pointer_temp.write_text(str(selected_root), encoding="utf-8")
            os.replace(pointer_temp, self.pointer)
        except Exception:
            self.rollback()
            raise

    def rollback(self) -> None:
        if self.closed:
            return
        if self.committed:
            for final, backup in reversed(self.actions):
                self._remove(final)
                if backup is not None and backup.exists():
                    final.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup, final)
            if self.pointer_existed:
                self.pointer.parent.mkdir(parents=True, exist_ok=True)
                pointer_temp = self.pointer.with_suffix(".txt.rollback")
                pointer_temp.write_bytes(self.pointer_bytes)
                os.replace(pointer_temp, self.pointer)
            else:
                self.pointer.unlink(missing_ok=True)
            for directory in reversed(self.created_directories):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        self.committed = False
        self.closed = True
        self._cleanup()

    def finalize(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._cleanup()

    def _cleanup(self) -> None:
        shutil.rmtree(self.staging_root, ignore_errors=True)
        shutil.rmtree(self.backup_root, ignore_errors=True)


def _install_offline(values: dict[str, Any], target: Path) -> dict[str, Any]:
    manifest, data, payloads = _load_addon(str(values.get("addon_path") or ""))
    _reject_addon_as_target(manifest, target)
    hash_total = sum(int(item["size"]) for item in payloads)
    hashed = 0

    def hash_progress(chunk: int) -> None:
        nonlocal hashed
        hashed += chunk
        _set_progress("verifying", "正在校验附加包完整性", 5 + (hashed / max(1, hash_total)) * 25, hashed, hash_total)

    for item in payloads:
        payload = Path(item["resolved_path"])
        if _sha256(payload, on_chunk=hash_progress) != str(item["sha256"]).lower():
            raise ValueError(f"附加包 SHA-256 校验失败：{payload.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".javis-model-staging-", dir=target.parent))
    transaction: _OverlayTransaction | None = None
    try:
        extract_total = _offline_extract_budget(payloads)
        extracted_bytes = 0

        def extract_progress(chunk: int) -> None:
            nonlocal extracted_bytes
            extracted_bytes += chunk
            _set_progress("staging", "正在解压本地模型与 Ollama", 30 + (extracted_bytes / max(1, extract_total)) * 55, extracted_bytes, extract_total)

        for item in payloads:
            _safe_extract(Path(item["resolved_path"]), staging, extract_progress)
        extracted = staging / "local-ai"
        if not extracted.is_dir():
            raise ValueError("附加包未生成 local-ai 目录")
        if not (extracted / "ollama" / "ollama.exe").is_file():
            raise ValueError("附加包暂存区缺少 Ollama 运行时")
        _set_progress("verifying", "暂存内容校验通过", 90)
        _install_control_checkpoint()
        transaction = _OverlayTransaction(staging, extracted, target / "local-ai")
        _set_progress("committing", "正在提交已校验的本地模型文件", 94)
        transaction.commit(target)
        return {
            "ok": True,
            "source": "offline",
            "model": str(data.get("model") or "deepseek-r1:8b"),
            "base_url": "http://127.0.0.1:11435/v1",
            "install_dir": str(target),
            "pointer": str(_pointer_path()),
            "restart_required": True,
            "message": "R1 与便携 Ollama 已安装；重启 Javis 后启用",
            "_transaction": transaction,
        }
    except Exception:
        if transaction is not None:
            transaction.rollback()
        else:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _normalize_model_name(value: str) -> str:
    name = str(value or "").strip()[:120]
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", name).strip("-.") or "javis-local-model"


def _persist_installed_model_configuration(
    model: str,
    base_url: str,
    targets: list[str],
) -> None:
    """Update only explicitly approved Live/Code local profiles."""
    from utils.config_api import get_model_connection_settings, set_model_connection_settings

    normalized_targets = _normalize_targets(targets)
    if not normalized_targets:
        return
    current = get_model_connection_settings()
    if current.get("share_live_code", True) and set(normalized_targets) != {"live", "code"}:
        raise ValueError("Live 与 Code 当前共享配置；应用本地模型时必须同时选择两者")
    routes: dict[str, Any] = {}
    for route_name in normalized_targets:
        existing = current.get("routes", {}).get(route_name, {})
        routes[route_name] = {
            "source": existing.get("source", "local"),
            "local": {"model": model, "base_url": base_url},
            "remote": dict(existing.get("remote", {})),
        }
    result = set_model_connection_settings({
        "share_live_code": bool(current.get("share_live_code", True)),
        "routes": routes,
    })
    if not result.get("applied"):
        raise RuntimeError(str(result.get("error") or "本地模型配置保存失败"))


def _revalidate_authorized_token(plan_token: Any, authorized: dict[str, Any]) -> None:
    current = _read_plan_token(plan_token)
    expected_bytes = json.dumps(
        authorized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    current_bytes = json.dumps(
        current,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not hmac.compare_digest(current_bytes, expected_bytes):
        raise PermissionError("安装计划令牌在提交前已改变")


def _copy_authorized_gguf(source: Path, destination: Path, authorized: dict[str, Any]) -> str:
    expected_size = int(authorized.get("size") or 0)
    expected_hash = str(authorized.get("sha256") or "").lower()
    if expected_size <= 0 or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise PermissionError("安装计划缺少不可变 GGUF 大小或 SHA-256")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    with source.open("rb") as input_stream, destination.open("xb") as output_stream:
        for chunk in iter(lambda: input_stream.read(8 * 1024 * 1024), b""):
            _install_control_checkpoint()
            copied += len(chunk)
            if copied > expected_size:
                raise PermissionError("GGUF 来源在授权后发生改变（大小超出计划）")
            digest.update(chunk)
            output_stream.write(chunk)
            _set_progress(
                "staging",
                "正在暂存并校验 GGUF",
                5 + (copied / max(1, expected_size)) * 65,
                copied,
                expected_size,
            )
    actual_hash = digest.hexdigest()
    if copied != expected_size or not hmac.compare_digest(actual_hash, expected_hash):
        raise PermissionError("GGUF 来源在授权后发生改变（大小或 SHA-256 不一致）")
    return actual_hash


def _validate_staged_metadata(metadata: dict[str, Any], authorized: dict[str, Any]) -> None:
    for key in ("architecture", "file_type", "quantization"):
        if key in authorized and metadata.get(key) != authorized.get(key):
            raise PermissionError(f"暂存 GGUF 的 {key} 与授权计划不一致")


def _install_local_gguf(
    authorized: dict[str, Any],
    target: Path,
    plan_token: Any,
) -> dict[str, Any]:
    source = Path(str(authorized.get("gguf_path") or "")).resolve()
    if source.suffix.lower() != ".gguf" or not source.is_file():
        raise PermissionError("授权的本地 GGUF 来源已不存在")
    runtime = target / "local-ai" / "ollama" / "ollama.exe"
    if not runtime.is_file():
        raise RuntimeError("所选位置尚未安装 Javis Ollama；请先用 R1 附加包完成运行时安装")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".javis-model-staging-", dir=target.parent))
    transaction: _OverlayTransaction | None = None
    try:
        staged_tree = staging / "local-ai"
        staged_model_root = staged_tree / "imported"
        filename = Path(str(authorized.get("filename") or source.name)).name
        staged_gguf = staged_model_root / filename
        actual_hash = _copy_authorized_gguf(source, staged_gguf, authorized)
        metadata = _inspect_gguf(staged_gguf)
        _validate_staged_metadata(metadata, authorized)
        model_name = _normalize_model_name(str(authorized.get("model") or source.stem))
        adapter = staged_model_root / "Javis-Modelfile"
        adapter.write_text(f'FROM "{staged_gguf}"\n', encoding="utf-8")
        _revalidate_authorized_token(plan_token, authorized)
        _set_progress("running", "正在隔离暂存区适配模型到 Ollama", 82)
        _adapt_gguf_with_ollama(runtime, staged_tree / "models", adapter, model_name)
        final_gguf = target / "local-ai" / "imported" / filename
        adapter.write_text(f'FROM "{final_gguf}"\n', encoding="utf-8")
        _revalidate_authorized_token(plan_token, authorized)
        _install_control_checkpoint()
        transaction = _OverlayTransaction(staging, staged_tree, target / "local-ai")
        _set_progress("committing", "正在提交已验证的本地 GGUF", 94)
        transaction.commit(target)
        return {
            "ok": True,
            "source": "local_gguf",
            "model": model_name,
            "base_url": "http://127.0.0.1:11435/v1",
            "gguf_path": str(final_gguf),
            "modelfile": str(target / "local-ai" / "imported" / "Javis-Modelfile"),
            "sha256": actual_hash,
            "license": str(authorized.get("license") or "用户提供的本地文件；许可由用户确认"),
            **metadata,
            "install_dir": str(target),
            "restart_required": True,
            "message": "本地 GGUF 已导入并自动适配；重启 Javis 后启用",
            "_transaction": transaction,
        }
    except Exception:
        if transaction is not None:
            transaction.rollback()
        else:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _assert_public_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Hugging Face 下载及重定向必须使用无凭据 HTTPS URL")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Hugging Face 下载地址不能指向本机或私有网络")
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise ValueError("Hugging Face 下载端口无效") from error
    try:
        addresses = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError("Hugging Face 下载地址无法安全解析") from error
    if not addresses:
        raise ValueError("Hugging Face 下载地址无法安全解析")
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address[4][0].split("%", 1)[0])
        except (IndexError, ValueError) as error:
            raise ValueError("Hugging Face 下载地址解析结果无效") from error
        if not resolved.is_global:
            raise ValueError("Hugging Face 下载地址不能指向本机、私有或保留网络")


def _download_hf_file(
    url: str,
    headers: dict[str, str],
    destination: Path,
    expected_size: int,
) -> None:
    _install_control_checkpoint()
    if expected_size <= 0 or expected_size > MAX_HF_DOWNLOAD_BYTES:
        raise ValueError("Hugging Face 文件大小超过安全下载上限")
    current_url = url
    current_headers = dict(headers)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        for redirect_count in range(MAX_HF_REDIRECTS + 1):
            _install_control_checkpoint()
            _assert_public_https_url(current_url)
            response = requests.get(
                current_url,
                headers=current_headers,
                stream=True,
                timeout=(20, 120),
                allow_redirects=False,
            )
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = str(response.headers.get("Location") or "").strip()
                    if not location:
                        raise RuntimeError("Hugging Face 重定向缺少 Location")
                    if redirect_count >= MAX_HF_REDIRECTS:
                        raise RuntimeError("Hugging Face 下载重定向次数超过上限")
                    next_url = urljoin(current_url, location)
                    _assert_public_https_url(next_url)
                    if urlsplit(next_url).hostname != urlsplit(current_url).hostname:
                        current_headers.pop("Authorization", None)
                    current_url = next_url
                    continue
                if response.status_code != 200:
                    response.raise_for_status()
                    raise RuntimeError(f"Hugging Face 下载返回意外状态：{response.status_code}")
                content_length = str(response.headers.get("Content-Length") or "").strip()
                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError as error:
                        raise RuntimeError("Hugging Face Content-Length 无效") from error
                    if declared_size > expected_size or declared_size > MAX_HF_DOWNLOAD_BYTES:
                        raise RuntimeError("Hugging Face 下载大小超过已确认计划或安全上限")
                downloaded = 0
                with destination.open("xb") as stream:
                    for chunk in response.iter_content(8 * 1024 * 1024):
                        _install_control_checkpoint()
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > expected_size or downloaded > MAX_HF_DOWNLOAD_BYTES:
                            raise RuntimeError("Hugging Face 下载大小超过已确认计划或安全上限")
                        stream.write(chunk)
                        _set_progress(
                            "running",
                            "正在从 Hugging Face 下载 GGUF",
                            5 + (downloaded / max(1, expected_size)) * 65,
                            downloaded,
                            expected_size,
                        )
                if downloaded != expected_size:
                    raise RuntimeError("Hugging Face 下载大小校验失败")
                return
            finally:
                response.close()
        raise RuntimeError("Hugging Face 下载重定向次数超过上限")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _download_huggingface(
    authorized: dict[str, Any],
    target: Path,
    plan_token: Any,
    access_token: str = "",
) -> dict[str, Any]:
    repo_id = str(authorized.get("repo_id") or "").strip()
    filename = str(authorized.get("filename") or "").strip()
    revision = str(authorized.get("repo_revision") or "").strip()
    expected_size = int(authorized.get("size") or 0)
    expected_hash = str(authorized.get("sha256") or "").lower()
    if (
        not REPO_PATTERN.fullmatch(repo_id)
        or not filename.lower().endswith(".gguf")
        or ".." in filename.replace("\\", "/").split("/")
        or not revision
        or expected_size <= 0
        or expected_size > MAX_HF_DOWNLOAD_BYTES
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
    ):
        raise PermissionError("授权的 Hugging Face 安装对象无效")
    runtime = target / "local-ai" / "ollama" / "ollama.exe"
    if not runtime.is_file():
        raise RuntimeError("所选位置尚未安装 Javis Ollama；请先用 R1 附加包完成运行时安装")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".javis-model-staging-", dir=target.parent))
    transaction: _OverlayTransaction | None = None
    try:
        staged_tree = staging / "local-ai"
        relative_root = Path("huggingface") / repo_id.replace("/", "--")
        staged_model_root = staged_tree / relative_root
        staged_gguf = staged_model_root / Path(filename).name
        headers = {"Authorization": f"Bearer {access_token.strip()}"} if access_token.strip() else {}
        url = (
            f"https://huggingface.co/{quote(repo_id, safe='/')}/resolve/"
            f"{quote(revision, safe='')}/{quote(filename, safe='/')}"
        )
        _download_hf_file(url, headers, staged_gguf, expected_size)
        actual_hash = _sha256(staged_gguf)
        if not hmac.compare_digest(actual_hash, expected_hash):
            raise RuntimeError("Hugging Face 下载 SHA-256 校验失败")
        metadata = _inspect_gguf(staged_gguf)
        model_name = _normalize_model_name(str(authorized.get("model") or Path(filename).stem))
        adapter = staged_model_root / "Javis-Modelfile"
        adapter.write_text(f'FROM "{staged_gguf}"\n', encoding="utf-8")
        _revalidate_authorized_token(plan_token, authorized)
        _set_progress("running", "正在隔离暂存区适配 Hugging Face 模型", 82)
        _adapt_gguf_with_ollama(runtime, staged_tree / "models", adapter, model_name)
        final_model_root = target / "local-ai" / relative_root
        final_gguf = final_model_root / Path(filename).name
        adapter.write_text(f'FROM "{final_gguf}"\n', encoding="utf-8")
        _revalidate_authorized_token(plan_token, authorized)
        _install_control_checkpoint()
        transaction = _OverlayTransaction(staging, staged_tree, target / "local-ai")
        _set_progress("committing", "正在提交已验证的 Hugging Face 模型", 94)
        transaction.commit(target)
        return {
            "ok": True,
            "source": "huggingface",
            "model": model_name,
            "base_url": "http://127.0.0.1:11435/v1",
            "gguf_path": str(final_gguf),
            "modelfile": str(final_model_root / "Javis-Modelfile"),
            "sha256": expected_hash,
            "license": str(authorized.get("license") or "未声明"),
            "repo_revision": revision,
            "trust_remote_code": False,
            **metadata,
            "install_dir": str(target),
            "restart_required": True,
            "adaptation_pending": False,
            "message": "GGUF 已下载并自动适配到 Ollama；重启 Javis 后启用",
            "_transaction": transaction,
        }
    except Exception:
        if transaction is not None:
            transaction.rollback()
        else:
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _adapt_gguf_with_ollama(runtime: Path, models: Path, modelfile: Path, model_name: str) -> None:
    _install_control_checkpoint()
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
            _install_control_checkpoint()
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
        _set_progress(
            "running",
            "正在由 Ollama 创建本地模型",
            get_model_install_progress().get("percent", 82),
            pausable=False,
        )
        creator = subprocess.Popen(
            [str(runtime), "create", model_name, "-f", str(modelfile)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        output = ""
        try:
            deadline = time.monotonic() + 1800
            while True:
                _install_control_checkpoint()
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(creator.args, 1800)
                try:
                    output, _ = creator.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            if creator.poll() is None:
                creator.terminate()
                try:
                    creator.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    creator.kill()
                    creator.wait(timeout=10)
            raise
        if creator.returncode != 0:
            raise RuntimeError(f"Ollama 自动适配失败：{output[-300:]}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)


def _authorize_install_plan(values: dict[str, Any]) -> dict[str, Any]:
    authorized = _read_plan_token(values.get("plan_token"))
    replanned_values = {
        key: value
        for key, value in values.items()
        if key not in {"approved", "confirmation", "plan_token"}
    }
    try:
        current = plan_model_install(replanned_values)
    except Exception as error:
        raise PermissionError(f"安装计划已失效，请重新生成：{error}") from error
    authorized_bytes = json.dumps(
        authorized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    current_bytes = json.dumps(
        _plan_core(current),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not hmac.compare_digest(authorized_bytes, current_bytes):
        raise PermissionError("安装计划内容已改变，请重新生成并确认")
    if int(current.get("free_bytes") or 0) < int(current.get("required_bytes") or 0):
        raise RuntimeError("所选安装磁盘空间不足")
    return authorized


class _PathSnapshot:
    def __init__(self, path: Path):
        self.path = path
        self.existed = path.is_file()
        self.value = path.read_bytes() if self.existed else b""

    def restore(self) -> None:
        if self.existed:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.rollback")
            try:
                temporary.write_bytes(self.value)
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            self.path.unlink(missing_ok=True)


def _install_record_path(result: dict[str, Any], plan: dict[str, Any]) -> Path:
    target = Path(str(result["install_dir"])).resolve()
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return target / "local-ai" / "install-records" / f"{record_id}.json"


def _write_install_record(result: dict[str, Any], plan: dict[str, Any]) -> str:
    target = Path(str(result["install_dir"])).resolve()
    path = _install_record_path(result, plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    record = {
        "schema": 1,
        "installed_at": int(time.time()),
        "source": result.get("source"),
        "model": result.get("model"),
        "base_url": result.get("base_url"),
        "install_dir": str(target),
        "targets": list(plan.get("targets") or []),
        "license": plan.get("license", ""),
        "sha256": plan.get("sha256", ""),
        "size": plan.get("size", 0),
        "architecture": result.get("architecture", plan.get("architecture", "")),
        "quantization": result.get("quantization", plan.get("quantization", "")),
        "repo_id": plan.get("repo_id", ""),
        "repo_revision": plan.get("repo_revision", ""),
        "filename": plan.get("filename", ""),
        "trust_remote_code": False,
    }
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return str(path)


def install_model(values: dict[str, Any]) -> dict[str, Any]:
    if values.get("approved") is not True or values.get("confirmation") != APPROVAL_MARKER:
        raise PermissionError("必须由用户在安装计划中明确确认后才能安装或下载")
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise RuntimeError("已有本地模型安装任务正在进行，请勿重复提交")
    _begin_install_job()
    transaction: _OverlayTransaction | None = None
    config_snapshot: _PathSnapshot | None = None
    record_snapshot: _PathSnapshot | None = None
    record_path: Path | None = None
    record_directory_existed = False
    try:
        _set_progress("approved", "用户已确认；正在验证不可变安装计划", 1)
        authorized = _authorize_install_plan(values)
        _set_progress("verifying", "安装计划与来源校验通过", 3)
        target = _normalize_target(str(values.get("install_dir") or ""))
        source = str(values.get("source") or "offline").strip().lower()
        if source == "offline":
            result = _install_offline(values, target)
        elif source == "local_gguf":
            result = _install_local_gguf(authorized, target, values.get("plan_token"))
        elif source == "huggingface":
            result = _download_huggingface(
                authorized,
                target,
                values.get("plan_token"),
                str(values.get("token") or ""),
            )
        else:
            raise ValueError("安装来源只能是 offline、local_gguf 或 huggingface")
        transaction = result.pop("_transaction", None)
        if source in {"local_gguf", "huggingface"}:
            expected_hash = str(authorized.get("sha256") or "")
            if not hmac.compare_digest(str(result.get("sha256") or ""), expected_hash):
                raise RuntimeError("安装结果与已确认计划的 SHA-256 不一致")
        _install_control_checkpoint()
        _set_progress("committing", "正在保存所选 Live/Code 本地模型配置", 98)
        approved_targets = list(authorized.get("targets") or [])
        from utils import config_api as config_module

        config_snapshot = _PathSnapshot(Path(config_module.CONFIG_PATH))
        _persist_installed_model_configuration(
            str(result.get("model") or "deepseek-r1:8b"),
            str(result.get("base_url") or "http://127.0.0.1:11435/v1"),
            approved_targets,
        )
        record_path = _install_record_path(result, authorized)
        record_directory_existed = record_path.parent.exists()
        record_snapshot = _PathSnapshot(record_path)
        result["install_record"] = _write_install_record(result, authorized)
        result["targets"] = approved_targets
        result["configuration_applied"] = bool(approved_targets)
        if transaction is not None:
            transaction.finalize()
            transaction = None
        _set_progress("completed", "安装与自动适配已完成", 100)
        return result
    except Exception as error:
        rollback_errors: list[str] = []
        if record_path is not None:
            try:
                record_path.with_suffix(".json.tmp").unlink(missing_ok=True)
                if record_snapshot is not None:
                    record_snapshot.restore()
                if not record_directory_existed:
                    try:
                        record_path.parent.rmdir()
                    except OSError:
                        pass
            except Exception as rollback_error:
                rollback_errors.append(f"安装记录恢复失败：{rollback_error}")
        if config_snapshot is not None:
            try:
                config_snapshot.restore()
            except Exception as rollback_error:
                rollback_errors.append(f"模型配置恢复失败：{rollback_error}")
        if transaction is not None:
            _set_progress("rolling_back", "安装失败；正在恢复原文件与目录指针", 99)
            try:
                transaction.rollback()
            except Exception as rollback_error:
                rollback_errors.append(f"模型文件恢复失败：{rollback_error}")
        terminal_state = "cancelled" if isinstance(error, ModelInstallCancelled) else "failed"
        _set_progress(terminal_state, str(error)[:200], get_model_install_progress().get("percent", 0))
        if rollback_errors:
            raise RuntimeError(f"{error}；{'；'.join(rollback_errors)}") from error
        raise
    finally:
        _finish_install_job()
        _INSTALL_LOCK.release()
