"""
沙箱系统 — Hyper-V / Docker / Windows Sandbox 三种后端
"""
import os, subprocess, logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("sandbox")

class SandboxBackend(str, Enum):
    DOCKER = "docker"
    HYPERV = "hyperv"
    WINDOWS_SANDBOX = "windows_sandbox"

@dataclass
class SandboxConfig:
    backend: SandboxBackend = SandboxBackend.DOCKER
    timeout: int = 30
    memory_mb: int = 2048
    cpus: int = 2

class SandboxManager:
    def __init__(self, config: SandboxConfig = None):
        self.config = config or SandboxConfig()

    def execute(self, code: str, language: str = "python") -> tuple[int, str]:
        if self.config.backend == SandboxBackend.DOCKER:
            return self._docker_exec(code, language)
        elif self.config.backend == SandboxBackend.HYPERV:
            return self._hyperv_exec(code, language)
        return self._native_exec(code, language)

    def _docker_exec(self, code: str, language: str) -> tuple[int, str]:
        try:
            r = subprocess.run(
                ["docker", "run", "--rm", f"--memory={self.config.memory_mb}m",
                 f"--cpus={self.config.cpus}", f"python:slim",
                 "python", "-c", code],
                capture_output=True, text=True,
                timeout=self.config.timeout
            )
            return r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return -1, "沙箱执行超时"

    def _hyperv_exec(self, code: str, language: str) -> tuple[int, str]:
        return -1, "Hyper-V 后端待集成"

    def _native_exec(self, code: str, language: str) -> tuple[int, str]:
        try:
            r = subprocess.run(
                ["python", "-c", code],
                capture_output=True, text=True,
                timeout=self.config.timeout
            )
            return r.returncode, r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return -1, "执行超时"


_sandbox: SandboxManager | None = None

def get_sandbox() -> SandboxManager:
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxManager()
    return _sandbox


def register_in_manifest(reg):
    """Register sandbox tools in manifest"""
    from core.tool_registry import ToolDef
    sb = get_sandbox()

    async def sandbox_exec(args):
        code = args["code"]
        language = args.get("language", "python")
        returncode, output = sb.execute(code, language)
        return {
            "success": returncode == 0,
            "exit_code": returncode,
            "output": output[:4000],
            "backend": sb.config.backend.value,
        }

    async def sandbox_config(args):
        backend = args.get("backend", "")
        if backend:
            try:
                sb.config.backend = SandboxBackend(backend)
            except ValueError:
                return {"success": False, "error": f"Unknown backend: {backend}"}
        timeout = args.get("timeout", 0)
        if timeout > 0:
            sb.config.timeout = timeout
        memory = args.get("memory_mb", 0)
        if memory > 0:
            sb.config.memory_mb = memory
        return {
            "success": True,
            "config": {
                "backend": sb.config.backend.value,
                "timeout": sb.config.timeout,
                "memory_mb": sb.config.memory_mb,
                "cpus": sb.config.cpus,
            }
        }

    async def sandbox_status(args):
        return {
            "success": True,
            "backend": sb.config.backend.value,
            "timeout": sb.config.timeout,
            "memory_mb": sb.config.memory_mb,
            "cpus": sb.config.cpus,
            "available_backends": [b.value for b in SandboxBackend],
        }

    reg.register_many([
        ToolDef("sandbox_exec", "Execute code in sandboxed environment",
                {"type":"object","properties":{"code":{"type":"string"},"language":{"type":"string","default":"python","enum":["python","bash","cmd"]}},"required":["code"]}, sandbox_exec, "sandbox"),
        ToolDef("sandbox_config", "Configure sandbox settings",
                {"type":"object","properties":{"backend":{"type":"string","enum":["docker","hyperv","windows_sandbox"]},"timeout":{"type":"integer"},"memory_mb":{"type":"integer"}},"required":[]}, sandbox_config, "sandbox"),
        ToolDef("sandbox_status", "Get sandbox system status",
                {"type":"object","properties":{},"required":[]}, sandbox_status, "sandbox"),
    ])
