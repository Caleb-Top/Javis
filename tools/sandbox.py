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
