from pathlib import Path
import shutil
import subprocess


def _version(cmd: str) -> str:
    exe = shutil.which(cmd)
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        return (out.stdout or out.stderr).strip().splitlines()[0][:120]
    except Exception:
        return ""


def probe_app_environment(root: Path) -> dict:
    root = Path(root)
    return {
        "app_dir_exists": (root / "app").exists(),
        "node": _version("node"),
        "npm": _version("npm"),
        "pnpm": _version("pnpm"),
        "rustc": _version("rustc"),
        "cargo": _version("cargo"),
    }
