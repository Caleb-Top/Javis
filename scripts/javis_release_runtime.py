"""Build and validate the packaged Javis release Python runtime."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator


VERSION = "3.0.0"
ARCHIVE_NAME = "javis-runtime.zip"
SYSTEMS = ("core", "memory", "perception", "control", "evolution")
EXTERNAL_COMPONENTS = (
    "deepseek_api",
    "ollama_models",
    "cuda_training_stack",
)
BACKEND_INCLUDE = (
    "main.py",
    "config.example.yaml",
    "blueprint",
    "brain_data",
    "core",
    "control",
    "evolution",
    "gateway",
    "knowledge",
    "memory",
    "perception",
    "providers",
    "skills",
    "tools",
    "tools_lib",
    "utils",
    "voice",
    "web",
)

PERSISTENT_PATHS = (
    "app/brain_data",
    "app/data",
    "app/memory",
    "app/workspace",
    "app/uploads",
    "app/logs",
    "app/output",
    "app/config.yaml",
    "app/skills/generated",
)

REQUIRED_MEMBERS = (
    "python/python.exe",
    "python/Lib/site-packages/av/__init__.py",
    "python/Lib/site-packages/ctranslate2/__init__.py",
    "python/Lib/site-packages/edge_tts/__init__.py",
    "python/Lib/site-packages/faster_whisper/__init__.py",
    "python/Lib/site-packages/pytesseract/__init__.py",
    "python/Lib/site-packages/pyaudio/__init__.py",
    "python/Lib/site-packages/pyaudio/_portaudio.cp311-win_amd64.pyd",
    "python/Lib/site-packages/win32com/__init__.py",
    "python/Lib/site-packages/pywin32_system32/pythoncom311.dll",
    "app/main.py",
    "app/models/faster-whisper-base/model.bin",
    "app/core/agent.py",
    "app/core/llm_client.py",
    "app/core/tool_guardrails.py",
    "app/blueprint/audit.py",
    "app/control/command_tasks.py",
    "app/evolution/service.py",
    "app/memory/session_db.py",
    "app/perception/service.py",
    "app/tools/Tesseract-OCR/tesseract.exe",
    "app/voice/stt.py",
    "app/voice/native_capture.py",
    "app/voice/native_capture_worker.py",
    "app/voice/streaming_pipeline.py",
    "app/voice/continuous_capture.py",
    "app/voice/streaming_ws.py",
    "app/voice/native_playback.py",
    "app/voice/native/javis-wasapi-loopback.exe",
    "app/web/index.html",
)

PYTHON_OPTIONAL_EXCLUDES = {
    "torch",
    "torchgen",
    "torchvision",
    "torchaudio",
    "torio",
    "ultralytics",
}

APP_TOOLCHAIN_EXCLUDES = {
    "app/tools/rust",
    "app/tools/mingw32",
    "app/tools/nodejs",
    "app/tools/imagemagick",
    "app/tools/gh",
    "app/tools/cvu_data",
}


@dataclass(frozen=True)
class RuntimeEntry:
    source: Path
    archive_name: str
    is_python: bool


def _parts(path: Path | PurePosixPath) -> tuple[str, ...]:
    return tuple(part.lower() for part in PurePosixPath(path.as_posix()).parts)


def should_exclude(path: Path | PurePosixPath, *, is_python: bool) -> bool:
    """Return true for generated, secret, state or development-only files."""
    posix = PurePosixPath(path.as_posix())
    parts = _parts(posix)
    joined = "/".join(parts)
    name = parts[-1] if parts else ""

    if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache", ".git"} for part in parts):
        return True
    if name.endswith((".pyc", ".pyo", ".tmp", ".log")):
        return True
    if name in {"config.yaml", ".env"} or name.startswith(".env."):
        return True
    if name.endswith((".sqlite", ".sqlite3", ".db", ".db-wal", ".db-shm")):
        return True
    if any(joined == prefix or joined.startswith(prefix + "/") for prefix in APP_TOOLCHAIN_EXCLUDES):
        return True
    if joined.startswith(("app/tmp/", "app/uploads/", "app/logs/", "app/train_output/", "app/harness_output/")):
        return True
    if is_python:
        if joined.startswith(("python/doc/", "python/scripts/__pycache__/")):
            return True
        marker = "python/lib/site-packages/"
        if joined.startswith(marker):
            package = joined[len(marker):].split("/", 1)[0]
            normalized = package.split("-", 1)[0].split(".", 1)[0]
            if normalized in PYTHON_OPTIONAL_EXCLUDES or normalized.lstrip("~") in PYTHON_OPTIONAL_EXCLUDES:
                return True
    return False


def _assert_inside(path: Path, root: Path) -> None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if os.path.commonpath((str(resolved), str(root_resolved))) != str(root_resolved):
        raise ValueError(f"runtime entry escapes root: {path}")


def _walk(source: Path, archive_prefix: str, *, is_python: bool) -> Iterator[RuntimeEntry]:
    root = source if source.is_dir() else source.parent
    candidates = source.rglob("*") if source.is_dir() else (source,)
    for file_path in candidates:
        if not file_path.is_file():
            continue
        _assert_inside(file_path, root)
        relative = file_path.relative_to(source) if source.is_dir() else Path(source.name)
        archive_name = PurePosixPath(archive_prefix, relative.as_posix()).as_posix()
        if should_exclude(Path(archive_name), is_python=is_python):
            continue
        yield RuntimeEntry(file_path, archive_name, is_python)


def runtime_entries(
    root: Path,
    python_root: Path,
    *,
    site_packages_root: Path | None = None,
    stt_model_root: Path | None = None,
    allow_incomplete: bool = False,
) -> Iterator[RuntimeEntry]:
    root = Path(root)
    python_root = Path(python_root)
    for relative in BACKEND_INCLUDE:
        source = root / relative
        if not source.exists():
            if allow_incomplete:
                continue
            raise FileNotFoundError(f"missing backend runtime path: {source}")
        prefix = "app" if source.is_file() else f"app/{relative}"
        yield from _walk(source, prefix, is_python=False)
    if not python_root.is_dir():
        raise FileNotFoundError(f"missing portable Python runtime: {python_root}")
    for entry in _walk(python_root, "python", is_python=True):
        if site_packages_root is not None and entry.archive_name.lower().startswith(
            "python/lib/site-packages/"
        ):
            continue
        yield entry

    if site_packages_root is None:
        if not allow_incomplete:
            raise FileNotFoundError("site-packages overlay was not provided")
    elif not Path(site_packages_root).is_dir():
        raise FileNotFoundError(f"missing site-packages overlay: {site_packages_root}")
    else:
        yield from _walk(
            Path(site_packages_root),
            "python/Lib/site-packages",
            is_python=True,
        )

    if stt_model_root is None:
        if not allow_incomplete:
            raise FileNotFoundError("local faster-whisper model was not provided")
    elif not Path(stt_model_root).is_dir():
        raise FileNotFoundError(f"missing local faster-whisper model: {stt_model_root}")
    else:
        yield from _walk(
            Path(stt_model_root),
            "app/models/faster-whisper-base",
            is_python=False,
        )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    return info


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_runtime_archive(
    root: Path,
    output: Path,
    python_root: Path,
    *,
    site_packages_root: Path | None = None,
    stt_model_root: Path | None = None,
    allow_incomplete: bool = False,
) -> dict:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()

    entries = sorted(
        runtime_entries(
            root,
            python_root,
            site_packages_root=site_packages_root,
            stt_model_root=stt_model_root,
            allow_incomplete=allow_incomplete,
        ),
        key=lambda item: item.archive_name,
    )
    uncompressed = 0
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for entry in entries:
            uncompressed += entry.source.stat().st_size
            with entry.source.open("rb") as source_stream:
                with archive.open(_zip_info(entry.archive_name), "w", force_zip64=True) as target_stream:
                    shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
    temporary.replace(output)

    manifest = {
        "schema_version": 1,
        "product": "Javis",
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "archive": {
            "name": ARCHIVE_NAME,
            "sha256": _sha256(output),
            "files": len(entries),
            "uncompressed_bytes": uncompressed,
            "compressed_bytes": output.stat().st_size,
        },
        "backend": {
            "complete_source": True,
            "include": list(BACKEND_INCLUDE),
            "systems": list(SYSTEMS),
        },
        "python": {
            "source": str(Path(python_root)),
            "site_packages": str(Path(site_packages_root)) if site_packages_root else "",
            "optional_excludes": sorted(PYTHON_OPTIONAL_EXCLUDES),
        },
        "models": {
            "faster_whisper_base": str(Path(stt_model_root)) if stt_model_root else "",
            "network_downloads": False,
        },
        "systems": list(SYSTEMS),
        "external_components": list(EXTERNAL_COMPONENTS),
        "persistent_paths": list(PERSISTENT_PATHS),
        "required_members": list(REQUIRED_MEMBERS),
    }
    manifest_path = output.parent / "javis-runtime-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output.parent / "javis-runtime.sha256").write_text(
        f"{manifest['archive']['sha256']}  {ARCHIVE_NAME}\n",
        encoding="ascii",
    )
    return manifest


def validate_runtime_archive(archive: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    archive = Path(archive)
    if not archive.is_file():
        return [f"runtime archive missing: {archive}"]
    expected_hash = str(manifest.get("archive", {}).get("sha256", ""))
    if expected_hash and _sha256(archive) != expected_hash:
        errors.append("runtime archive checksum mismatch")
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                errors.append(f"unsafe runtime member: {name}")
        for required in REQUIRED_MEMBERS:
            if required not in names:
                errors.append(f"missing required runtime member: {required}")
    return errors
