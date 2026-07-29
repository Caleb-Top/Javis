"""Verify a packaged Javis runtime and render an installation test report."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_REPORT_NAME = "INSTALL-TEST-REPORT.md"


@dataclass
class VerificationReport:
    checks: list[tuple[str, str, str]] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append((name, status, detail))

    @property
    def ok(self) -> bool:
        return all(status in {"PASS", "UNTESTED"} for _, status, _ in self.checks)

    def markdown(self) -> str:
        lines = [
            "# Javis v3.0 Installation Test Report",
            "",
            f"Overall: {'PASS' if self.ok else 'FAIL'}",
            "",
            "| Check | Status | Detail |",
            "|---|---|---|",
        ]
        lines.extend(
            f"| {name} | {status} | {detail.replace('|', '/')} |"
            for name, status, detail in self.checks
        )
        return "\n".join(lines) + "\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(archive: Path, manifest: dict) -> VerificationReport:
    report = VerificationReport()
    expected = str(manifest.get("archive", {}).get("sha256", ""))
    actual = sha256(archive) if Path(archive).is_file() else ""
    report.add("Runtime archive", "PASS" if actual and actual == expected else "FAIL", actual or "missing")
    required = set(manifest.get("required_members", []))
    try:
        with zipfile.ZipFile(archive) as package:
            names = set(package.namelist())
            unsafe = [name for name in names if Path(name).is_absolute() or ".." in Path(name).parts]
        missing = sorted(required - names)
        report.add("Archive paths", "PASS" if not unsafe else "FAIL", "safe" if not unsafe else ", ".join(unsafe[:5]))
        report.add("Complete backend", "PASS" if not missing else "FAIL", "all required members" if not missing else ", ".join(missing))
    except (OSError, zipfile.BadZipFile) as error:
        report.add("Archive structure", "FAIL", str(error))
    return report


def verify_extracted_runtime(runtime_root: Path) -> VerificationReport:
    runtime_root = Path(runtime_root)
    report = VerificationReport()
    python = runtime_root / "python/python.exe"
    app = runtime_root / "app"
    main = app / "main.py"
    report.add("python/python.exe", "PASS" if python.is_file() else "FAIL", str(python))
    report.add("app/main.py", "PASS" if main.is_file() else "FAIL", str(main))
    if python.is_file() and main.is_file():
        env = os.environ.copy()
        env["JAVIS_TEST_MODE"] = "1"
        env["JAVIS_DISABLE_STARTUP_SIDE_EFFECTS"] = "1"
        result = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import fastapi,uvicorn,httpx,faster_whisper,pytesseract,"
                    "edge_tts,av,ctranslate2,win32com.client;"
                    "from core.agent import Agent;print('JAVIS_RUNTIME_OK')"
                ),
            ],
            cwd=app,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        passed = result.returncode == 0 and "JAVIS_RUNTIME_OK" in result.stdout
        report.add("Python backend imports", "PASS" if passed else "FAIL", (result.stderr or result.stdout).strip()[-500:])
    return report


def wait_for_status(port: int, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if payload.get("service") == "javis":
                    return payload
        except Exception:
            time.sleep(0.4)
    return {}


async def _websocket_ping(port: int) -> tuple[dict, dict]:
    import websockets

    async with websockets.connect(f"ws://127.0.0.1:{port}/ws", open_timeout=10) as socket:
        await socket.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))

        await socket.send(json.dumps({
            "type": "tool",
            "payload": {"name": "system_info", "params": {}},
        }))
        tool_result: dict = {}
        while True:
            event = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
            if event.get("type") == "tool_result":
                tool_result = event
            if event.get("type") in {"done", "error"}:
                return pong, tool_result


def verify_packaged_backend(runtime_root: Path, port: int) -> VerificationReport:
    report = VerificationReport()
    python = Path(runtime_root) / "python/python.exe"
    app = Path(runtime_root) / "app"
    env = os.environ.copy()
    for name in ("JAVIS_ROOT", "JAVIS_PYTHON"):
        env.pop(name, None)
    env["PORT"] = str(port)
    process = subprocess.Popen(
        [str(python), "-u", str(app / "main.py")],
        cwd=app,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        status = wait_for_status(port)
        report.add("Packaged backend startup", "PASS" if status else "FAIL", status.get("service", "offline"))
        if status:
            pong, tool_result = asyncio.run(_websocket_ping(port))
            report.add(
                "WebSocket ping/pong",
                "PASS" if pong.get("type") == "pong" else "FAIL",
                json.dumps(pong, ensure_ascii=False)[:300],
            )
            tool_ok = tool_result.get("tool") == "system_info" and tool_result.get("success") is True
            report.add(
                "Packaged system_info tool",
                "PASS" if tool_ok else "FAIL",
                str(tool_result.get("data") or tool_result)[:300],
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Javis v3 release runtime")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path(DEFAULT_REPORT_NAME))
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = verify_archive(args.archive, manifest)
    temp = Path(tempfile.mkdtemp(prefix="javis-v3-verify-"))
    try:
        with zipfile.ZipFile(args.archive) as package:
            package.extractall(temp)
        for check in verify_extracted_runtime(temp).checks:
            report.checks.append(check)
        if report.ok:
            for check in verify_packaged_backend(temp, args.port).checks:
                report.checks.append(check)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report.markdown(), encoding="utf-8")
    print(report.markdown())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
