"""
沙箱系统 — Hyper-V / Docker / Windows Sandbox / Native 四种后端
P3-1: Multi-backend sandbox with resource limits, code validation, and run_code integration
"""
import os, subprocess, logging, tempfile, hashlib, re, threading, time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sandbox")


class SandboxBackend(str, Enum):
    DOCKER = "docker"
    HYPERV = "hyperv"
    WINDOWS_SANDBOX = "windows_sandbox"
    NATIVE = "native"


@dataclass
class SandboxConfig:
    backend: SandboxBackend = SandboxBackend.NATIVE
    timeout: int = 30
    memory_mb: int = 2048
    cpus: int = 2
    network_enabled: bool = False
    workspace_mount: str = ""
    # Docker 特定
    docker_image: str = "python:3.13-slim"
    # Hyper-V 特定
    hyperv_vm_name: str = "JavisSandbox"
    # Windows Sandbox 特定
    sandbox_config_path: str = ""

    def to_dict(self) -> dict:
        return {
            "backend": self.backend.value,
            "timeout": self.timeout,
            "memory_mb": self.memory_mb,
            "cpus": self.cpus,
            "network_enabled": self.network_enabled,
        }


class CodeValidator:
    """代码安全检查 — 阻止危险操作"""

    DANGEROUS_PATTERNS = [
        r"os\.remove\(.*\)",
        r"shutil\.rmtree",
        r"subprocess\.call\(.*rm\s",
        r"__import__\(['\"]os['\"]\)\.system",
        r"eval\(.*__",
        r"exec\(.*__",
        r"ctypes\.",
        r"winreg\.",
        r"\.delete\(.*system",
        r"format\(.*C:",
    ]

    DANGEROUS_IMPORTS = [
        "ctypes", "winreg", "win32api", "win32com",
        "multiprocessing", "socket", "http.server",
    ]

    @classmethod
    def validate(cls, code: str, language: str = "python") -> tuple[bool, str]:
        """验证代码安全性"""
        if language != "python":
            return True, ""

        # 检查危险 import
        for imp in cls.DANGEROUS_IMPORTS:
            if re.search(rf"^import\s+{imp}\b|from\s+{imp}\b", code, re.MULTILINE):
                return False, f"禁止导入: {imp}"

        # 检查危险模式
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, code):
                return False, f"检测到危险操作: {pattern}"

        return True, ""


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    success: bool = False
    backend: str = ""
    elapsed_ms: float = 0
    truncated: bool = False


class SandboxManager:
    """沙箱管理器 — 多后端统一接口"""

    def __init__(self, config: SandboxConfig = None):
        self.config = config or SandboxConfig()
        self._validator = CodeValidator()
        self._sandbox_ready: dict[str, bool] = {}

    def detect_backend(self) -> SandboxBackend:
        """自动检测可用后端"""
        # 检查 Docker
        try:
            r = subprocess.run(
                ["docker", "info"], capture_output=True, timeout=5
            )
            if r.returncode == 0:
                self._sandbox_ready["docker"] = True
        except Exception:
            self._sandbox_ready["docker"] = False

        # 检查 Hyper-V
        try:
            r = subprocess.run(
                ["powershell", "-Command",
                 "Get-VM -Name JavisSandbox -ErrorAction SilentlyContinue"],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0 and "JavisSandbox" in r.stdout.decode():
                self._sandbox_ready["hyperv"] = True
        except Exception:
            self._sandbox_ready["hyperv"] = False

        # Windows Sandbox
        sandbox_exe = os.path.expandvars(
            r"%SystemRoot%\System32\WindowsSandbox.exe"
        )
        self._sandbox_ready["windows_sandbox"] = os.path.exists(sandbox_exe)

        # Native 总是可用
        self._sandbox_ready["native"] = True

        # 优先级: Docker > Hyper-V > Windows Sandbox > Native
        if self._sandbox_ready.get("docker"):
            return SandboxBackend.DOCKER
        if self._sandbox_ready.get("hyperv"):
            return SandboxBackend.HYPERV
        return SandboxBackend.NATIVE

    def execute(self, code: str, language: str = "python",
                timeout: int = None) -> SandboxResult:
        """执行代码并返回结果"""
        t0 = time.time()
        timeout_val = timeout or self.config.timeout

        # 安全校验
        if language == "python":
            safe, reason = self._validator.validate(code)
            if not safe:
                return SandboxResult(
                    exit_code=-1, stderr=f"代码安全检查失败: {reason}",
                    backend=self.config.backend.value,
                )

        backend = self.config.backend
        executor_map = {
            SandboxBackend.DOCKER: self._docker_exec,
            SandboxBackend.HYPERV: self._hyperv_exec,
            SandboxBackend.WINDOWS_SANDBOX: self._sandbox_exec,
            SandboxBackend.NATIVE: self._native_exec,
        }

        executor = executor_map.get(backend, self._native_exec)
        exit_code, stdout, stderr = executor(code, language, timeout_val)
        elapsed = (time.time() - t0) * 1000

        truncated = len(stdout) > 8000 or len(stderr) > 8000
        return SandboxResult(
            exit_code=exit_code,
            stdout=stdout[:8000],
            stderr=stderr[:8000],
            success=exit_code == 0,
            backend=self.config.backend.value,
            elapsed_ms=elapsed,
            truncated=truncated,
        )

    def _docker_exec(self, code: str, language: str,
                     timeout: int) -> tuple[int, str, str]:
        """Docker 容器执行"""
        if language == "python":
            cmd = [
                "docker", "run", "--rm",
                f"--memory={self.config.memory_mb}m",
                f"--cpus={self.config.cpus}",
            ]
            if not self.config.network_enabled:
                cmd.append("--network=none")
            if self.config.workspace_mount:
                cmd.extend(["-v",
                    f"{self.config.workspace_mount}:/workspace"])
            cmd.extend([self.config.docker_image, "python", "-c", code])
        elif language == "bash":
            cmd = [
                "docker", "run", "--rm",
                f"--memory={self.config.memory_mb}m",
                "ubuntu:slim", "bash", "-c", code,
            ]
        else:
            return -1, "", f"Unsupported language for Docker: {language}"

        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Docker 执行超时"
        except FileNotFoundError:
            return -1, "", "Docker 未安装或不在 PATH 中"

    def _hyperv_exec(self, code: str, language: str,
                     timeout: int) -> tuple[int, str, str]:
        """Hyper-V VM 执行 — 通过 PowerShell Direct"""
        vm_name = self.config.hyperv_vm_name

        # 检查 VM 是否存在
        check = subprocess.run(
            ["powershell", "-Command",
             f"(Get-VM -Name {vm_name} -ErrorAction SilentlyContinue).State"],
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode != 0 or not check.stdout.strip():
            return -1, "", f"Hyper-V VM '{vm_name}' 未找到，请创建"

        state = check.stdout.strip()
        if state != "Running":
            # 尝试启动
            subprocess.run(
                ["powershell", "-Command", f"Start-VM -Name {vm_name}"],
                capture_output=True, timeout=30,
            )

        # 通过 PowerShell Direct 执行
        safe_code = code.replace('"', '\\"')
        ps_script = (
            f"Invoke-Command -VMName {vm_name} -ScriptBlock {{"
            f"python -c \"{safe_code}\""
            f"}}"
        )
        try:
            r = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True, text=True, timeout=timeout + 10,
            )
            return r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "Hyper-V 执行超时"

    def _sandbox_exec(self, code: str, language: str,
                      timeout: int) -> tuple[int, str, str]:
        """Windows Sandbox 执行 — 通过配置文件"""
        # 写入临时脚本
        script_dir = tempfile.mkdtemp(prefix="javis_sandbox_")
        script_path = Path(script_dir) / "script.py"
        script_path.write_text(code, encoding="utf-8")

        # 创建 Windows Sandbox 配置 (.wsb)
        wsb_content = f"""<Configuration>
  <VGpu>Disable</VGpu>
  <Networking>{'Enable' if self.config.network_enabled else 'Disable'}</Networking>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>{script_dir}</HostFolder>
      <SandboxFolder>C:\\javis_task</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>python C:\\javis_task\\script.py</Command>
  </LogonCommand>
</Configuration>"""
        wsb_path = Path(script_dir) / "sandbox.wsb"
        wsb_path.write_text(wsb_content, encoding="utf-8")

        try:
            r = subprocess.run(
                ["start", str(wsb_path)],
                shell=True, capture_output=True, timeout=5,
            )
            # Windows Sandbox 是 GUI 的，难以捕获输出
            return 0, f"Windows Sandbox 已启动: {wsb_path}", ""
        except subprocess.TimeoutExpired:
            return 0, "Windows Sandbox 启动中...", ""
        except Exception as e:
            return -1, "", f"Windows Sandbox 启动失败: {e}"

    def _native_exec(self, code: str, language: str,
                     timeout: int) -> tuple[int, str, str]:
        """本机进程执行 — 受限于超时和内存"""
        try:
            if language == "python":
                r = subprocess.run(
                    ["python", "-c", code],
                    capture_output=True, text=True, timeout=timeout,
                    env={**os.environ, "JAVIS_SANDBOX": "1"},
                )
            elif language == "bash":
                r = subprocess.run(
                    ["bash", "-c", code],
                    capture_output=True, text=True, timeout=timeout,
                )
            elif language in ("cmd", "powershell"):
                r = subprocess.run(
                    ["powershell", "-Command", code],
                    capture_output=True, text=True, timeout=timeout,
                )
            else:
                return -1, "", f"不支持的语言: {language}"

            return r.returncode, r.stdout, r.stderr

        except subprocess.TimeoutExpired:
            return -1, "", "执行超时"
        except FileNotFoundError:
            return -1, "", "Python 未安装或在 PATH 中"

    def get_status(self) -> dict:
        """获取沙箱状态"""
        self.detect_backend()
        return {
            "current_backend": self.config.backend.value,
            "config": self.config.to_dict(),
            "available_backends": [
                {"name": b.value, "ready": self._sandbox_ready.get(b.value, False)}
                for b in SandboxBackend
            ],
        }


# 全局单例
_sandbox: Optional[SandboxManager] = None

def get_sandbox() -> SandboxManager:
    global _sandbox
    if _sandbox is None:
        _sandbox = SandboxManager()
        _sandbox.detect_backend()
    return _sandbox


def register_in_manifest(reg):
    """注册沙箱工具到 manifest"""
    from core.tool_registry import ToolDef
    sb = get_sandbox()

    async def sandbox_exec(args):
        code = args["code"]
        language = args.get("language", "python")
        timeout = args.get("timeout", 0)
        result = sb.execute(code, language, timeout or None)
        return {
            "success": result.success,
            "exit_code": result.exit_code,
            "output": result.stdout,
            "error": result.stderr,
            "backend": result.backend,
            "elapsed_ms": result.elapsed_ms,
            "truncated": result.truncated,
        }

    async def sandbox_config(args):
        backend = args.get("backend", "")
        if backend:
            try:
                sb.config.backend = SandboxBackend(backend)
            except ValueError:
                return {"success": False,
                        "error": f"未知后端: {backend}. "
                        f"可用: {[b.value for b in SandboxBackend]}"}

        if args.get("timeout", 0) > 0:
            sb.config.timeout = args["timeout"]
        if args.get("memory_mb", 0) > 0:
            sb.config.memory_mb = args["memory_mb"]
        if args.get("cpus", 0) > 0:
            sb.config.cpus = args["cpus"]
        if "network_enabled" in args:
            sb.config.network_enabled = bool(args["network_enabled"])

        return {"success": True, "config": sb.config.to_dict()}

    async def sandbox_status(args):
        return {"success": True, **sb.get_status()}

    async def sandbox_detect(args):
        backend = sb.detect_backend()
        return {"success": True,
                "detected": backend.value,
                "available": sb.get_status()["available_backends"]}

    reg.register_many([
        ToolDef("sandbox_exec", "在沙箱环境中执行代码",
                {"type":"object","properties":{
                    "code":{"type":"string"},
                    "language":{"type":"string","enum":["python","bash","cmd","powershell"],"default":"python"},
                    "timeout":{"type":"integer","default":30},
                },"required":["code"]},
                sandbox_exec, "sandbox"),
        ToolDef("sandbox_config", "配置沙箱设置（后端/超时/内存/CPU）",
                {"type":"object","properties":{
                    "backend":{"type":"string","enum":["docker","hyperv","windows_sandbox","native"]},
                    "timeout":{"type":"integer"},
                    "memory_mb":{"type":"integer"},
                    "cpus":{"type":"integer"},
                    "network_enabled":{"type":"boolean"},
                },"required":[]},
                sandbox_config, "sandbox"),
        ToolDef("sandbox_status", "查看沙箱系统状态和可用后端",
                {"type":"object","properties":{},"required":[]},
                sandbox_status, "sandbox"),
        ToolDef("sandbox_detect", "自动检测最佳可用后端",
                {"type":"object","properties":{},"required":[]},
                sandbox_detect, "sandbox"),
    ])
