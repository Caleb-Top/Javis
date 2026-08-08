"""Build-time contracts for the standalone Javis installer payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any


COPY_BUFFER = 8 * 1024 * 1024
EXTERNAL_BUNDLE_SCHEMA = 2
MAX_WINDOWS_EXECUTABLE_BYTES = 4 * 1024**3 - 1
SETUP_FILENAME = "Javis-v3.0.0-Setup.exe"
SETUP_MANIFEST_FILENAME = "Javis-v3.0.0-Setup.manifest.json"
PAYLOAD_DIRECTORY_NAME = "payloads"
ADDON_DIRECTORY_NAME = "Javis-R1-8B-Addon"
ADDON_MANIFEST_FILENAME = "Javis-R1-8B-Addon.manifest.json"


def _manifest_path(model_root: Path, model: str) -> Path:
    name, separator, tag = str(model).strip().partition(":")
    if not name:
        raise ValueError("Ollama model name is empty")
    tag = tag if separator and tag else "latest"
    return (
        Path(model_root)
        / "manifests"
        / "registry.ollama.ai"
        / "library"
        / name
        / tag
    )


def _descriptor_blob(model_root: Path, descriptor: dict[str, Any]) -> Path:
    digest = str(descriptor.get("digest") or "").strip()
    algorithm, separator, value = digest.partition(":")
    if not separator or algorithm != "sha256" or not value:
        raise ValueError(f"unsupported Ollama blob digest: {digest}")
    blob = Path(model_root) / "blobs" / f"sha256-{value}"
    if not blob.is_file():
        raise FileNotFoundError(f"Ollama model blob is missing: {blob}")
    expected_size = descriptor.get("size")
    if isinstance(expected_size, int) and blob.stat().st_size != expected_size:
        raise ValueError(
            f"Ollama model blob size mismatch: {blob.name}; "
            f"expected {expected_size}, got {blob.stat().st_size}"
        )
    return blob


def collect_ollama_model(model_root: Path, model: str) -> dict[str, Any]:
    model_root = Path(model_root).resolve()
    manifest = _manifest_path(model_root, model)
    if not manifest.is_file():
        raise FileNotFoundError(f"Ollama model manifest is missing: {manifest}")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    descriptors = [payload.get("config"), *payload.get("layers", [])]
    blobs = {
        _descriptor_blob(model_root, descriptor)
        for descriptor in descriptors
        if isinstance(descriptor, dict)
    }
    files = sorted([*blobs, manifest], key=lambda path: path.relative_to(model_root).as_posix())
    return {
        "schema_version": 1,
        "model": model,
        "model_root": str(model_root),
        "manifest": manifest.relative_to(model_root).as_posix(),
        "files": [path.relative_to(model_root).as_posix() for path in files],
        "bytes": sum(path.stat().st_size for path in files),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(COPY_BUFFER), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_info(name: str, *, compression: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info.external_attr = 0o100644 << 16
    return info


def _write_zip_file(
    archive: zipfile.ZipFile,
    source: Path,
    target: str,
    *,
    compression: int,
) -> None:
    with source.open("rb") as source_stream:
        with archive.open(
            _zip_info(target, compression=compression),
            "w",
            force_zip64=True,
        ) as target_stream:
            shutil.copyfileobj(source_stream, target_stream, length=COPY_BUFFER)


def stage_model_payload(model_root: Path, model: str, output: Path) -> dict[str, Any]:
    model_root = Path(model_root).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    model_report = collect_ollama_model(model_root, model)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
        embedded = json.dumps(model_report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        archive.writestr(
            _zip_info("local-ai/model-pack.json", compression=zipfile.ZIP_STORED),
            embedded,
        )
        for relative in model_report["files"]:
            _write_zip_file(
                archive,
                model_root / relative,
                f"local-ai/models/{relative}",
                compression=zipfile.ZIP_STORED,
            )
    temporary.replace(output)
    return {
        **model_report,
        "archive": str(output),
        "archive_bytes": output.stat().st_size,
        "sha256": _sha256(output),
    }


def stage_ollama_payload(source_root: Path, output: Path) -> dict[str, Any]:
    source_root = Path(source_root).resolve()
    output = Path(output).resolve()
    executable = source_root / "ollama.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"portable Ollama executable is missing: {executable}")
    files = sorted(
        (path for path in source_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(source_root).as_posix(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for source in files:
            relative = source.relative_to(source_root).as_posix()
            _write_zip_file(
                archive,
                source,
                f"local-ai/ollama/{relative}",
                compression=zipfile.ZIP_DEFLATED,
            )
    temporary.replace(output)
    return {
        "schema_version": 1,
        "source": str(source_root),
        "archive": str(output),
        "files": len(files),
        "uncompressed_bytes": sum(path.stat().st_size for path in files),
        "archive_bytes": output.stat().st_size,
        "sha256": _sha256(output),
    }


def _validate_payload_name(name: str) -> str:
    value = str(name).strip()
    if not value or Path(value).name != value or "/" in value or "\\" in value:
        raise ValueError(f"installer payload name must be a file name: {name}")
    return value


def _copy_payload(source: Path, destination: Path) -> None:
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if source == destination:
        return
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    with source.open("rb") as input_stream, temporary.open("wb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream, length=COPY_BUFFER)
    temporary.replace(destination)


def build_external_bundle(
    bootstrap: Path,
    payloads: list[tuple[str, Path]],
    output_dir: Path,
) -> dict[str, Any]:
    """Build a Windows-loadable setup EXE with hash-locked external payloads.

    Windows rejects PE files once a large appended overlay crosses the loader's
    executable-size boundary. The bootstrap therefore stays small and resolves
    every payload through a relative, validated sidecar manifest.
    """

    bootstrap = Path(bootstrap).resolve()
    output_dir = Path(output_dir).resolve()
    if not bootstrap.is_file():
        raise FileNotFoundError(f"installer bootstrap is missing: {bootstrap}")
    if bootstrap.stat().st_size > MAX_WINDOWS_EXECUTABLE_BYTES:
        raise ValueError("installer bootstrap exceeds the Windows executable size limit")

    output_dir.mkdir(parents=True, exist_ok=True)
    payload_dir = output_dir / PAYLOAD_DIRECTORY_NAME
    payload_dir.mkdir(parents=True, exist_ok=True)
    setup = output_dir / SETUP_FILENAME
    _copy_payload(bootstrap, setup)

    index: dict[str, Any] = {"schema_version": EXTERNAL_BUNDLE_SCHEMA, "payloads": []}
    seen: set[str] = set()
    for raw_name, raw_source in payloads:
        name = _validate_payload_name(raw_name)
        if name.casefold() in seen:
            raise ValueError(f"duplicate installer payload name: {name}")
        seen.add(name.casefold())
        source = Path(raw_source).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"installer payload is missing: {source}")
        destination = payload_dir / name
        _copy_payload(source, destination)
        index["payloads"].append(
            {
                "name": name,
                "path": f"{PAYLOAD_DIRECTORY_NAME}/{name}",
                "size": destination.stat().st_size,
                "sha256": _sha256(destination),
            }
        )

    manifest = output_dir / SETUP_MANIFEST_FILENAME
    manifest.write_text(
        json.dumps(index, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return index


def read_external_bundle_index(setup: Path) -> dict[str, Any]:
    setup = Path(setup).resolve()
    manifest = setup.with_suffix(".manifest.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != EXTERNAL_BUNDLE_SCHEMA:
        raise ValueError("external installer manifest schema is invalid")
    for entry in payload.get("payloads", []):
        name = _validate_payload_name(entry.get("name", ""))
        expected_path = f"{PAYLOAD_DIRECTORY_NAME}/{name}"
        if entry.get("path") != expected_path:
            raise ValueError(f"external installer payload path is invalid: {entry.get('path')}")
        target = setup.parent / PAYLOAD_DIRECTORY_NAME / name
        if not target.is_file():
            raise FileNotFoundError(f"external installer payload is missing: {target}")
        if target.stat().st_size != entry.get("size"):
            raise ValueError(f"external installer payload size mismatch: {name}")
        if _sha256(target) != str(entry.get("sha256", "")).lower():
            raise ValueError(f"external installer payload checksum mismatch: {name}")
    return payload


def build_offline_release(
    *,
    model_root: Path,
    ollama_root: Path,
    bootstrap: Path,
    app_setup: Path,
    output_dir: Path,
    work_dir: Path,
    model: str = "deepseek-r1:8b",
) -> dict[str, Any]:
    output_dir = Path(output_dir).resolve()
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    model_archive = work_dir / "deepseek-r1-8b.zip"
    ollama_archive = work_dir / "ollama-runtime.zip"
    model_report = stage_model_payload(model_root, model, model_archive)
    ollama_report = stage_ollama_payload(ollama_root, ollama_archive)
    bundle_index = build_external_bundle(
        bootstrap,
        [
            ("app-setup.exe", Path(app_setup)),
            ("ollama-runtime.zip", ollama_archive),
            ("deepseek-r1-8b.zip", model_archive),
        ],
        output_dir,
    )
    setup = output_dir / SETUP_FILENAME
    report = {
        "schema_version": 1,
        "product": "Javis",
        "version": "3.0.0",
        "delivery": "external-payload-folder",
        "installer": str(setup),
        "installer_bytes": setup.stat().st_size,
        "installer_sha256": _sha256(setup),
        "installer_manifest": str(output_dir / SETUP_MANIFEST_FILENAME),
        "bundle": bundle_index,
        "model": model_report,
        "ollama": ollama_report,
        "user_model_switching": True,
    }
    (output_dir / "Javis-v3.0.0-Offline.manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def build_r1_addon(
    *,
    model_root: Path,
    ollama_root: Path,
    output_dir: Path,
    work_dir: Path,
    model: str = "deepseek-r1:8b",
) -> dict[str, Any]:
    """Build the optional, independently selectable local R1 add-on."""
    output_dir = Path(output_dir).resolve()
    addon_dir = output_dir / ADDON_DIRECTORY_NAME
    addon_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    model_archive = work_dir / "deepseek-r1-8b.zip"
    ollama_archive = work_dir / "ollama-runtime.zip"
    model_report = stage_model_payload(model_root, model, model_archive)
    ollama_report = stage_ollama_payload(ollama_root, ollama_archive)
    payloads = []
    for role, source in (
        ("ollama-runtime", ollama_archive),
        ("ollama-model", model_archive),
    ):
        destination = addon_dir / source.name
        _copy_payload(source, destination)
        payloads.append({
            "role": role,
            "path": destination.name,
            "size": destination.stat().st_size,
            "sha256": _sha256(destination),
        })
    manifest = {
        "schema": 1,
        "kind": "javis-local-model-addon",
        "title": "Javis DeepSeek R1 8B 离线附加包",
        "model": model,
        "runtime": "portable-ollama",
        "payloads": payloads,
    }
    manifest_path = addon_dir / ADDON_MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    report = {
        **manifest,
        "directory": str(addon_dir),
        "manifest": str(manifest_path),
        "model_report": model_report,
        "ollama_report": ollama_report,
    }
    (output_dir / "Javis-R1-8B-Addon.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-r1:8b")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--build-addon", action="store_true")
    parser.add_argument("--ollama-root", type=Path)
    parser.add_argument("--bootstrap", type=Path)
    parser.add_argument("--app-setup", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)
    if args.build_addon:
        required = {
            "ollama_root": args.ollama_root,
            "output_dir": args.output_dir,
            "work_dir": args.work_dir,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error(f"--build-addon requires: {', '.join(missing)}")
        report = build_r1_addon(
            model_root=args.model_root,
            ollama_root=args.ollama_root,
            output_dir=args.output_dir,
            work_dir=args.work_dir,
            model=args.model,
        )
    elif args.build:
        required = {
            "ollama_root": args.ollama_root,
            "bootstrap": args.bootstrap,
            "app_setup": args.app_setup,
            "output_dir": args.output_dir,
            "work_dir": args.work_dir,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error(f"--build requires: {', '.join(missing)}")
        report = build_offline_release(
            model_root=args.model_root,
            ollama_root=args.ollama_root,
            bootstrap=args.bootstrap,
            app_setup=args.app_setup,
            output_dir=args.output_dir,
            work_dir=args.work_dir,
            model=args.model,
        )
    else:
        report = collect_ollama_model(args.model_root, args.model)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
