from pathlib import Path


INCLUDE = [
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
]

EXCLUDE = [
    ".git",
    ".claude",
    ".codex",
    ".hermes",
    "_sdk_extract",
    "venv",
    "Lib",
    "python-embed",
    "__pycache__",
    "logs",
    "tmp",
    "uploads",
    "output",
    "train_output",
    "harness_output",
    ".javis_rollback_points",
]

EXTERNAL = [
    "ollama_models",
    "DeepSeek API",
    "CUDA/PyTorch training stack",
    "data",
    "workspace",
    "tools/rust",
    "tools/mingw32",
    "tools/nodejs",
    "tools/ImageMagick",
    "tools/gh",
    "tools/yolo",
    "tools/cvu_data",
    "memory/*.sqlite",
    "memory/sessions",
]


def build_app_package_manifest(root: Path) -> dict:
    return {
        "root": str(Path(root)),
        "include": INCLUDE,
        "exclude": EXCLUDE,
        "external": EXTERNAL,
    }
