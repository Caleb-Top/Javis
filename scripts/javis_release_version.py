"""Generate and verify every Javis release version surface."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import tempfile
import tomllib
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

JSON_VERSION_TARGETS = (
    "app/package.json",
    "app/package-lock.json",
    "app/src-tauri/tauri.conf.json",
    "app/release.manifest.json",
    "app/src-tauri/resources/javis-runtime-manifest.json",
)
TOML_VERSION_TARGETS = (
    "app/src-tauri/Cargo.toml",
    "installer/bootstrap/Cargo.toml",
)
CARGO_LOCK_TARGETS = (
    "app/src-tauri/Cargo.lock",
    "installer/bootstrap/Cargo.lock",
)
TEXT_VERSION_TARGETS = (
    "scripts/javis_release_runtime.py",
    "scripts/javis_release_layout.py",
    "scripts/javis_full_installer.py",
    "scripts/finalize_javis_release.py",
    "scripts/build_javis_app_v3.ps1",
    "scripts/verify_javis_v3_installer.ps1",
)
VERSION_TARGETS = (
    *JSON_VERSION_TARGETS,
    *TOML_VERSION_TARGETS,
    *CARGO_LOCK_TARGETS,
    *TEXT_VERSION_TARGETS,
)
LOCK_NAME = ".version-sync.lock"
JOURNAL_NAME = ".version-sync-journal.json"


def _validate_version(version: str) -> str:
    normalized = str(version).strip()
    if not SEMVER.fullmatch(normalized):
        raise ValueError(f"release version must be strict MAJOR.MINOR.PATCH semver: {version!r}")
    return normalized


def load_version_contract(root: Path = ROOT) -> dict[str, Any]:
    path = Path(root) / "release" / "version.json"
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"invalid release version source: {path}: {error}") from error
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        raise ValueError(f"unsupported release version source schema: {path}")
    if contract.get("product") != "Javis":
        raise ValueError(f"release version source product must be Javis: {path}")
    contract["version"] = _validate_version(str(contract.get("version") or ""))
    return contract


def release_artifact_names(version: str) -> dict[str, str]:
    version = _validate_version(version)
    return {
        "artifact_directory": f"Javis-v{version}",
        "delivery_directory": f"Javis-v{version}-User-Test",
        "tauri_nsis": f"Javis_{version}_x64-setup.exe",
        "package": f"Javis-v{version}-Setup.exe",
        "setup_manifest": f"Javis-v{version}-Setup.manifest.json",
        "offline_manifest": f"Javis-v{version}-Offline.manifest.json",
        "candidate_manifest": f"Javis-v{version}-Candidate.manifest.json",
        "release_manifest": f"Javis-v{version}-Release.manifest.json",
    }


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _cargo_lock_version(path: Path, package_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(?ms)^\[\[package\]\]\s*\n(?:(?!^\[\[package\]\]).)*?"
        + rf'^name = "{re.escape(package_name)}"\s*$'
        + r"(?:(?!^\[\[package\]\]).)*?^version = \"([^\"]+)\"\s*$"
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(f"package {package_name!r} missing from {path}")
    return match.group(1)


def _append_drift(errors: list[str], surface: str, actual: Any, expected: str) -> None:
    if actual != expected:
        errors.append(f"{surface}: expected {expected!r}, got {actual!r}")


def _check_text_assignment(
    errors: list[str],
    *,
    surface: str,
    source: str,
    pattern: str,
    expected: str,
) -> None:
    matches = re.findall(pattern, source, flags=re.MULTILINE)
    if len(matches) != 1:
        errors.append(f"{surface}: expected one effective assignment, found {len(matches)}")
        return
    _append_drift(errors, surface, matches[0], expected)


def collect_version_drift(root: Path = ROOT) -> list[str]:
    root = Path(root)
    version = load_version_contract(root)["version"]
    artifacts = release_artifact_names(version)
    errors: list[str] = []
    for relative in VERSION_TARGETS:
        if not (root / relative).is_file():
            errors.append(f"{relative}: missing version target")
    if errors:
        return errors

    package = _json(root / "app/package.json")
    lock = _json(root / "app/package-lock.json")
    tauri = _json(root / "app/src-tauri/tauri.conf.json")
    release = _json(root / "app/release.manifest.json")
    runtime_manifest = _json(root / "app/src-tauri/resources/javis-runtime-manifest.json")
    tauri_cargo = tomllib.loads((root / "app/src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    bootstrap_cargo = tomllib.loads((root / "installer/bootstrap/Cargo.toml").read_text(encoding="utf-8"))

    _append_drift(errors, "Node package", package.get("version"), version)
    _append_drift(errors, "Node lock", lock.get("version"), version)
    _append_drift(errors, "Node lock root package", lock.get("packages", {}).get("", {}).get("version"), version)
    _append_drift(errors, "Tauri config", tauri.get("version"), version)
    _append_drift(errors, "Tauri Rust crate", tauri_cargo.get("package", {}).get("version"), version)
    _append_drift(errors, "bootstrap Rust crate", bootstrap_cargo.get("package", {}).get("version"), version)
    _append_drift(errors, "Tauri Cargo.lock", _cargo_lock_version(root / "app/src-tauri/Cargo.lock", "javis-app"), version)
    _append_drift(
        errors,
        "bootstrap Cargo.lock",
        _cargo_lock_version(root / "installer/bootstrap/Cargo.lock", "javis-full-installer"),
        version,
    )
    _append_drift(errors, "release manifest", release.get("version"), version)
    _append_drift(errors, "runtime resource manifest", runtime_manifest.get("version"), version)

    sources = {relative: (root / relative).read_text(encoding="utf-8") for relative in TEXT_VERSION_TARGETS}
    assignments = (
        ("Python runtime VERSION", "scripts/javis_release_runtime.py", r'^VERSION\s*=\s*"([^"]+)"\s*$', version),
        (
            "layout artifact directory",
            "scripts/javis_release_layout.py",
            r'^\s*artifact_dir\s*=.*?"(Javis-v[0-9]+\.[0-9]+\.[0-9]+)"\s*$',
            artifacts["artifact_directory"],
        ),
        (
            "layout delivery directory",
            "scripts/javis_release_layout.py",
            r'^\s*delivery_dir\s*=.*?"(Javis-v[0-9]+\.[0-9]+\.[0-9]+-User-Test)"\s*$',
            artifacts["delivery_directory"],
        ),
        (
            "layout package output",
            "scripts/javis_release_layout.py",
            r'^\s*"package_output":\s*str\(artifact_dir\s*/\s*"([^"]+)"\),\s*$',
            artifacts["package"],
        ),
        (
            "layout delivery installer",
            "scripts/javis_release_layout.py",
            r'^\s*"delivery_installer":\s*str\(delivery_dir\s*/\s*"([^"]+)"\),\s*$',
            artifacts["package"],
        ),
        ("installer SETUP_FILENAME", "scripts/javis_full_installer.py", r'^SETUP_FILENAME\s*=\s*"([^"]+)"\s*$', artifacts["package"]),
        (
            "installer SETUP_MANIFEST_FILENAME",
            "scripts/javis_full_installer.py",
            r'^SETUP_MANIFEST_FILENAME\s*=\s*"([^"]+)"\s*$',
            artifacts["setup_manifest"],
        ),
        (
            "installer offline manifest",
            "scripts/javis_full_installer.py",
            r'^\s*\(output_dir\s*/\s*"(Javis-v[0-9]+\.[0-9]+\.[0-9]+-Offline\.manifest\.json)"\)\.write_text\($',
            artifacts["offline_manifest"],
        ),
        (
            "installer manifest version",
            "scripts/javis_full_installer.py",
            r'^\s*"version":\s*"([0-9]+\.[0-9]+\.[0-9]+)",?\s*$',
            version,
        ),
        (
            "finalizer setup",
            "scripts/finalize_javis_release.py",
            r'^\s*setup\s*=\s*artifact\s*/\s*"([^"]+)"\s*$',
            artifacts["package"],
        ),
        (
            "finalizer release manifest",
            "scripts/finalize_javis_release.py",
            r'^\s*\(artifact\s*/\s*"(Javis-v[0-9]+\.[0-9]+\.[0-9]+-Release\.manifest\.json)"\)\.write_text\($',
            artifacts["release_manifest"],
        ),
        (
            "finalizer manifest version",
            "scripts/finalize_javis_release.py",
            r'^\s*"version":\s*"([0-9]+\.[0-9]+\.[0-9]+)",?\s*$',
            version,
        ),
        (
            "PowerShell InnerInstaller",
            "scripts/build_javis_app_v3.ps1",
            r'^\$InnerInstaller\s*=.*?"bundle\\nsis\\([^"]+)"\s*$',
            artifacts["tauri_nsis"],
        ),
        (
            "PowerShell MainInstallerName",
            "scripts/build_javis_app_v3.ps1",
            r'^\$MainInstallerName\s*=\s*"([^"]+)"\s*$',
            artifacts["package"],
        ),
        (
            "PowerShell delivery directory",
            "scripts/build_javis_app_v3.ps1",
            r'^\s*if\s*\(-not\s+\$ResolvedDelivery\.Equals\("D:\\([^"]+)".*$',
            artifacts["delivery_directory"],
        ),
        (
            "installer verification runtime version",
            "scripts/verify_javis_v3_installer.ps1",
            r'^\s*Add-Check\s+"First-start runtime activation"\s+\(\$Version\s+-eq\s+"([^"]+)"\).*$' ,
            version,
        ),
    )
    for surface, relative, pattern, expected in assignments:
        _check_text_assignment(errors, surface=surface, source=sources[relative], pattern=pattern, expected=expected)
    return errors


def _replace_package_version(text: str, version: str) -> str:
    pattern = re.compile(r'(?ms)(^\[package\]\s*.*?^version\s*=\s*")[^"]+("\s*$)')
    updated, count = pattern.subn(rf"\g<1>{version}\g<2>", text, count=1)
    if count != 1:
        raise ValueError("[package] version field missing")
    return updated


def _replace_lock_package_version(text: str, package_name: str, version: str) -> str:
    block_pattern = re.compile(
        r"(?ms)^\[\[package\]\]\s*\n(?:(?!^\[\[package\]\]).)*?"
        + rf'^name = "{re.escape(package_name)}"\s*$'
        + r"(?:(?!^\[\[package\]\]).)*?(?=^\[\[package\]\]|\Z)"
    )
    match = block_pattern.search(text)
    if not match:
        raise ValueError(f"Cargo.lock package missing: {package_name}")
    block = match.group(0)
    updated_block, count = re.subn(
        r'(?m)(^version = ")[^"]+("\s*$)',
        rf"\g<1>{version}\g<2>",
        block,
        count=1,
    )
    if count != 1:
        raise ValueError(f"Cargo.lock package version missing: {package_name}")
    return text[: match.start()] + updated_block + text[match.end() :]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.javis-version-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def version_sync_lock(root: Path) -> Iterator[None]:
    release_dir = Path(root) / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    lock_path = release_dir / LOCK_NAME
    token = uuid.uuid4().hex
    payload = json.dumps({"schema_version": 1, "pid": os.getpid(), "token": token}).encode("utf-8")
    for _ in range(2):
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                existing = json.loads(lock_path.read_text(encoding="utf-8"))
                owner_pid = int(existing.get("pid") or 0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                owner_pid = 0
            if _pid_is_alive(owner_pid):
                raise RuntimeError(f"release version sync is already running (pid={owner_pid})")
            lock_path.unlink(missing_ok=True)
            continue
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            break
    else:
        raise RuntimeError("unable to acquire release version sync lock")
    try:
        yield
    finally:
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            current = {}
        if current.get("token") == token:
            lock_path.unlink(missing_ok=True)


def _journal_path(root: Path) -> Path:
    return Path(root) / "release" / JOURNAL_NAME


def _write_transaction_journal(root: Path, originals: dict[str, bytes]) -> None:
    payload = {
        "schema_version": 1,
        "originals": {
            relative: base64.b64encode(content).decode("ascii")
            for relative, content in originals.items()
        },
    }
    _atomic_write_bytes(
        _journal_path(root),
        (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
    )


def recover_version_transaction(root: Path) -> bool:
    root = Path(root)
    journal_path = _journal_path(root)
    if not journal_path.is_file():
        return False
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal.get("schema_version") != 1 or not isinstance(journal.get("originals"), dict):
        raise ValueError(f"invalid release version transaction journal: {journal_path}")
    for relative, encoded in journal["originals"].items():
        relative_path = Path(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"unsafe release version journal path: {relative}")
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as error:
            raise ValueError(f"release version journal escaped root: {relative}") from error
        _atomic_write_bytes(target, base64.b64decode(str(encoded), validate=True))
    journal_path.unlink()
    for staging in (root / "release").glob(".version-sync-staging-*"):
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)
    return True


def _sync_release_version_unchecked(root: Path, version: str) -> None:
    root = Path(root)
    version = _validate_version(version)
    previous = load_version_contract(root)["version"]

    package_path = root / "app/package.json"
    package = _json(package_path)
    package["version"] = version
    _write_json(package_path, package)

    lock_path = root / "app/package-lock.json"
    lock = _json(lock_path)
    lock["version"] = version
    lock.setdefault("packages", {}).setdefault("", {})["version"] = version
    _write_json(lock_path, lock)

    for relative in (
        "app/src-tauri/tauri.conf.json",
        "app/release.manifest.json",
        "app/src-tauri/resources/javis-runtime-manifest.json",
    ):
        path = root / relative
        value = _json(path)
        value["version"] = version
        _write_json(path, value)

    for relative in TOML_VERSION_TARGETS:
        path = root / relative
        path.write_text(_replace_package_version(path.read_text(encoding="utf-8"), version), encoding="utf-8")

    lock_packages = {
        "app/src-tauri/Cargo.lock": "javis-app",
        "installer/bootstrap/Cargo.lock": "javis-full-installer",
    }
    for relative, package_name in lock_packages.items():
        path = root / relative
        path.write_text(
            _replace_lock_package_version(path.read_text(encoding="utf-8"), package_name, version),
            encoding="utf-8",
        )

    for relative in TEXT_VERSION_TARGETS:
        path = root / relative
        source = path.read_text(encoding="utf-8")
        if previous not in source:
            raise ValueError(f"old version {previous} missing from generated text target: {relative}")
        path.write_text(source.replace(previous, version), encoding="utf-8")

    contract_path = root / "release/version.json"
    contract = load_version_contract(root)
    contract["version"] = version
    _write_json(contract_path, contract)
    drift = collect_version_drift(root)
    if drift:
        raise ValueError("version sync left drift:\n" + "\n".join(drift))


def sync_release_version(root: Path, version: str) -> None:
    root = Path(root)
    tracked = ("release/version.json", *VERSION_TARGETS)
    with version_sync_lock(root):
        recover_version_transaction(root)
        for stale_staging in (root / "release").glob(".version-sync-staging-*"):
            if stale_staging.is_dir():
                shutil.rmtree(stale_staging, ignore_errors=True)
        originals = {relative: (root / relative).read_bytes() for relative in tracked}
        staging_root = Path(tempfile.mkdtemp(prefix=".version-sync-staging-", dir=root / "release"))
        try:
            for relative, content in originals.items():
                staged = staging_root / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(content)
            _sync_release_version_unchecked(staging_root, version)
            rendered = {relative: (staging_root / relative).read_bytes() for relative in tracked}
            _write_transaction_journal(root, originals)
            try:
                for relative, content in rendered.items():
                    _atomic_write_bytes(root / relative, content)
                drift = collect_version_drift(root)
                if drift:
                    raise ValueError("version sync left drift:\n" + "\n".join(drift))
            except BaseException:
                recover_version_transaction(root)
                raise
            else:
                _journal_path(root).unlink(missing_ok=True)
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or verify Javis release versions")
    parser.add_argument("--root", type=Path, default=ROOT)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--set", dest="new_version")
    action.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.new_version:
        sync_release_version(args.root, args.new_version)
    contract = load_version_contract(args.root)
    drift = collect_version_drift(args.root)
    payload = {
        **contract,
        "artifacts": release_artifact_names(contract["version"]),
        "consistent": not drift,
        "drift": drift,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not drift else 2


if __name__ == "__main__":
    raise SystemExit(main())
