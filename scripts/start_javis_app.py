"""Start the Javis v3.0 desktop test app or its dependency-free preview.

web/ remains debug. Static fallback entry: app/preview.html.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PREVIEW = ROOT / "app" / "preview.html"
BACKEND_ENTRY = ROOT / "main.py"
BUILT_APP_CANDIDATES = (
    ROOT / "app" / "src-tauri" / "target" / "x86_64-pc-windows-gnu" / "release" / "javis-app.exe",
    ROOT / "app" / "src-tauri" / "target" / "release" / "javis-app.exe",
    ROOT / "app" / "src-tauri" / "target" / "debug" / "javis-app.exe",
)


def find_built_app() -> Path | None:
    return next((path for path in BUILT_APP_CANDIDATES if path.is_file()), None)


def launch_built_app(built_app: Path) -> subprocess.Popen:
    return subprocess.Popen([str(built_app)], cwd=str(ROOT))


def _python_exe() -> str:
    bundled = ROOT / "venv" / "Scripts" / "python.exe"
    if bundled.exists():
        return str(bundled)
    return sys.executable or "python"


def backend_is_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status == 200 and payload.get("service") == "javis"
    except (OSError, UnicodeError, ValueError, urllib.error.URLError):
        return False


def start_backend(port: int) -> subprocess.Popen | None:
    if not BACKEND_ENTRY.exists():
        return None
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    env = os.environ.copy()
    env["PORT"] = str(port)
    return subprocess.Popen(
        [_python_exe(), "-u", str(BACKEND_ENTRY)],
        cwd=str(ROOT),
        env=env,
        creationflags=creationflags,
    )


def open_live_app_preview() -> None:
    if not APP_PREVIEW.exists():
        raise FileNotFoundError(f"Missing App preview: {APP_PREVIEW}")
    webbrowser.open(APP_PREVIEW.as_uri())


def wait_for_backend(port: int, seconds: float = 8.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if backend_is_ready(port):
            return True
        time.sleep(0.4)
    return backend_is_ready(port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch Javis App-first Live shell")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--no-backend", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--preview", action="store_true", help="Force the static App preview")
    args = parser.parse_args(argv)

    if not args.preview:
        built_app = find_built_app()
        if built_app is not None:
            proc = launch_built_app(built_app)
            print("Javis App v3.0 test build: started")
            print(f"Entry: {built_app}")
            print(f"App PID: {proc.pid}")
            return 0

    proc = None
    if not args.no_backend and not backend_is_ready(args.port):
        proc = start_backend(args.port)
        wait_for_backend(args.port)

    if not args.no_open:
        open_live_app_preview()

    state = "ready" if backend_is_ready(args.port) else "preview-only"
    print(f"Javis App Live: {state}")
    print(f"Backend: http://127.0.0.1:{args.port}")
    print(f"Entry: {APP_PREVIEW}")
    if proc:
        print(f"Backend PID: {proc.pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
