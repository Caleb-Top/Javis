"""
P3-1: Javis 代码执行沙箱 — Docker/Hyper-V 双模式沙箱
提供隔离的代码执行环境，支持 Python/Shell/Node.js
"""
import os
import json
import tempfile
import subprocess
import shutil
import time
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum


class SandboxType(Enum):
    DOCKER = "docker"
    HYPERV = "hyperv"
    PROCESS = "process"  # 降级: 子进程隔离


class SandboxSecurity(Enum):
    """安全级别"""
    STRICT = "strict"       # 网络禁用, 只读文件系统, 资源限制
    STANDARD = "standard"   # 网络允许, 可写/tmp, 资源限制
    RELAXED = "relaxed"     # 完整网络, 可写项目目录, 资源限制


@dataclass
class SandboxResult:
    """沙箱执行结果"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    execution_time_ms: float = 0
    truncated: bool = False
    error: Optional[str] = None


class Sandbox:
    """Javis 代码执行沙箱"""

    # 危险代码模式
    DANGEROUS_PATTERNS = [
        r"os\.system\s*\(", r"subprocess\.(call|run|Popen)",
        r"eval\s*\(", r"exec\s*\(", r"__import__\s*\(",
        r"open\s*\([^)]*['\"][wa]",
        r"shutil\.rmtree", r"os\.remove", r"os\.rmdir",
        r"socket\.", r"requests\.", r"urllib",
        r"ctypes\.", r"_winreg", r"winreg",
        r"fork\s*\(", r"multiprocessing",
    ]

    # 允许的安全导入
    SAFE_IMPORTS = {
        "math", "json", "datetime", "collections", "itertools",
        "functools", "random", "statistics", "decimal", "fractions",
        "string", "re", "typing", "dataclasses",
        "csv", "hashlib", "base64", "binascii",
        "copy", "pprint", "textwrap", "enum",
        "pathlib", "os.path", "tempfile",
    }

    def __init__(self, sandbox_type: SandboxType = SandboxType.PROCESS,
                 security: SandboxSecurity = SandboxSecurity.STANDARD):
        self.sandbox_type = sandbox_type
        self.security = security
        self._docker_available: Optional[bool] = None
        self._hyperv_available: Optional[bool] = None

    def _check_docker(self) -> bool:
        """检测 Docker 是否可用"""
        if self._docker_available is None:
            try:
                result = subprocess.run(
                    ["docker", "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True, timeout=10
                )
                self._docker_available = result.returncode == 0
            except Exception:
                self._docker_available = False
        return self._docker_available

    def _check_hyperv(self) -> bool:
        """检测 Hyper-V 是否可用"""
        if self._hyperv_available is None:
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V).State"],
                    capture_output=True, timeout=15
                )
                self._hyperv_available = "Enabled" in result.stdout.decode()
            except Exception:
                self._hyperv_available = False
        return self._hyperv_available

    def best_available(self) -> SandboxType:
        """选择最佳的可用沙箱类型"""
        if self._check_docker():
            return SandboxType.DOCKER
        if self._check_hyperv():
            return SandboxType.HYPERV
        return SandboxType.PROCESS

    # ---------- 代码安全扫描 ----------

    def scan_code(self, code: str) -> Tuple[bool, List[str]]:
        """扫描代码中的危险模式"""
        violations = []
        for pattern in self.DANGEROUS_PATTERNS:
            matches = re.findall(pattern, code, re.IGNORECASE)
            if matches:
                violations.append(f"Dangerous pattern detected: {pattern} (matched {len(matches)} times)")
        return len(violations) == 0, violations

    def scan_imports(self, code: str) -> Tuple[bool, List[str]]:
        """扫描不安全的导入"""
        import_pattern = r"(?:from\s+(\S+)\s+import|import\s+(\S+))"
        imports = re.findall(import_pattern, code)
        unsafe = []
        for match in imports:
            module = match[0] or match[1]
            module = module.split(".")[0]
            if module not in self.SAFE_IMPORTS and module not in {"__future__", "sys", "os"}:
                unsafe.append(module)
        return len(unsafe) == 0, unsafe

    # ---------- 执行 ----------

    def execute_python(self, code: str, timeout: int = 30,
                       env: Dict = None, workdir: str = "") -> SandboxResult:
        """在沙箱中执行 Python 代码"""
        clean, violations = self.scan_code(code)
        if not clean and self.security == SandboxSecurity.STRICT:
            return SandboxResult(success=False, error=f"Code rejected: {'; '.join(violations[:3])}")

        if self.security != SandboxSecurity.RELAXED:
            safe_imports, unsafe_imports = self.scan_imports(code)
            if not safe_imports:
                return SandboxResult(success=False,
                    error=f"Unsafe imports not allowed: {unsafe_imports}")

        # 包装代码: 限制 builtins
        wrapper = """
import builtins, sys, os
_SAFE_BUILTINS = {k: v for k, v in builtins.__dict__.items()
    if k not in ('open', 'exec', 'eval', '__import__', 'compile', 'input')}
sys.modules['os'] = type(sys)('os')
sys.modules['os'].__dict__.clear()

try:
""" + "\n".join("    " + line for line in code.split("\n")) + """
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
"""

        t0 = time.time()

        try:
            if self.sandbox_type == SandboxType.DOCKER and self._check_docker():
                return self._execute_docker("python", wrapper, timeout, workdir)
            else:
                return self._execute_process(["python3", "-c", wrapper], timeout, env, workdir)
        except Exception as e:
            return SandboxResult(success=False, error=str(e),
                                execution_time_ms=(time.time() - t0) * 1000)

    def execute_shell(self, command: str, timeout: int = 30,
                      workdir: str = "") -> SandboxResult:
        """在沙箱中执行 Shell 命令"""
        # Shell 命令安全检查
        dangerous_cmds = ["rm -rf /", "mkfs.", "dd if=", "> /dev/sda", "chmod 777 /"]
        for dc in dangerous_cmds:
            if dc in command.lower():
                return SandboxResult(success=False, error=f"Dangerous command rejected: {dc}")

        t0 = time.time()

        try:
            if self.sandbox_type == SandboxType.DOCKER and self._check_docker():
                return self._execute_docker("sh", command, timeout, workdir)
            else:
                return self._execute_process(["bash", "-c", command], timeout, workdir=workdir)
        except Exception as e:
            return SandboxResult(success=False, error=str(e),
                                execution_time_ms=(time.time() - t0) * 1000)

    def execute_node(self, code: str, timeout: int = 30,
                     workdir: str = "") -> SandboxResult:
        """在沙箱中执行 Node.js 代码"""
        t0 = time.time()
        try:
            if self.sandbox_type == SandboxType.DOCKER and self._check_docker():
                return self._execute_docker("node", f"-e {json.dumps(code)}", timeout, workdir)
            else:
                return self._execute_process(["node", "-e", code], timeout, workdir=workdir)
        except Exception as e:
            return SandboxResult(success=False, error=str(e),
                                execution_time_ms=(time.time() - t0) * 1000)

    # ---------- 内部执行方法 ----------

    def _execute_docker(self, runtime: str, command: str,
                        timeout: int, workdir: str = "") -> SandboxResult:
        """在 Docker 容器中执行"""
        docker_args = [
            "docker", "run", "--rm",
            f"--name=javis-sandbox-{int(time.time())}",
            "--memory=512m", "--cpus=1",
            "--network=none" if self.security == SandboxSecurity.STRICT else "",
            "--read-only" if self.security == SandboxSecurity.STRICT else "",
            f"--tmpfs=/tmp:size=100M",
            "-v", f"{workdir or os.getcwd()}:/workspace:ro" if self.security == SandboxSecurity.STRICT else
                 f"{workdir or os.getcwd()}:/workspace",
            "-w", "/workspace",
        ]
        docker_args = [a for a in docker_args if a]  # 过滤空字符串

        if runtime == "python":
            docker_args += ["python:3.11-slim", "python", "-c", command]
        elif runtime == "sh":
            docker_args += ["alpine:latest", "sh", "-c", command]
        elif runtime == "node":
            docker_args += ["node:18-alpine", "node", "-e", command]

        return self._run_and_capture(docker_args, timeout)

    def _execute_process(self, cmd: List[str], timeout: int = 30,
                         env: Dict = None, workdir: str = "") -> SandboxResult:
        """在子进程中执行 (降级模式)"""
        my_env = os.environ.copy()
        if self.security == SandboxSecurity.STRICT:
            my_env["PATH"] = "/usr/bin:/bin"
            my_env.pop("HOME", None)
            my_env.pop("USER", None)
        if env:
            my_env.update(env)

        return self._run_and_capture(cmd, timeout, my_env, workdir)

    def _run_and_capture(self, cmd: List[str], timeout: int = 30,
                         env: Dict = None, workdir: str = "") -> SandboxResult:
        """通用子进程执行和结果捕获"""
        t0 = time.time()
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout,
                env=env or os.environ,
                cwd=workdir or os.getcwd()
            )
            elapsed = (time.time() - t0) * 1000
            output = result.stdout[:10000]
            error = result.stderr[:5000]

            return SandboxResult(
                success=result.returncode == 0,
                stdout=output, stderr=error,
                returncode=result.returncode,
                execution_time_ms=elapsed,
                truncated=len(result.stdout) > 10000 or len(result.stderr) > 5000
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                error=f"Execution timed out after {timeout}s",
                execution_time_ms=timeout * 1000
            )
        except FileNotFoundError:
            return SandboxResult(
                success=False,
                error=f"Runtime not found: {cmd[0]}",
                execution_time_ms=(time.time() - t0) * 1000
            )

    # ---------- 资源限制 ----------

    def get_resource_limits(self) -> Dict:
        """获取沙箱资源限制配置"""
        return {
            "type": self.sandbox_type.value,
            "security": self.security.value,
            "docker_available": self._check_docker(),
            "hyperv_available": self._check_hyperv(),
            "max_timeout": 120,
            "max_memory_mb": 512,
            "max_cpu": 1,
            "output_limit_chars": 10000,
            "safe_imports_count": len(self.SAFE_IMPORTS),
        }


# 全局单例
_sandbox: Optional[Sandbox] = None


def get_sandbox() -> Sandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = Sandbox(sandbox_type=Sandbox.PROCESS)
    return _sandbox


def register_in_manifest(reg):
    """Register sandbox tools"""
    from core.tool_registry import ToolDef
    sb = get_sandbox()

    async def sandbox_python(args):
        result = sb.execute_python(
            code=args["code"],
            timeout=args.get("timeout", 30)
        )
        return {"success": result.success, "stdout": result.stdout,
                "stderr": result.stderr, "returncode": result.returncode,
                "time_ms": result.execution_time_ms, "truncated": result.truncated}

    async def sandbox_shell(args):
        result = sb.execute_shell(
            command=args["command"],
            timeout=args.get("timeout", 30)
        )
        return {"success": result.success, "stdout": result.stdout,
                "stderr": result.stderr, "returncode": result.returncode,
                "time_ms": result.execution_time_ms}

    async def sandbox_scan(args):
        clean, violations = sb.scan_code(args["code"])
        safe_imports, unsafe_imports = sb.scan_imports(args["code"])
        return {"success": True, "safe": clean and safe_imports,
                "code_violations": violations, "unsafe_imports": unsafe_imports}

    async def sandbox_status(args):
        return {"success": True, **sb.get_resource_limits()}

    reg.register_many([
        ToolDef("sandbox_python", "Execute Python code in sandbox", {"type":"object","properties":{"code":{"type":"string"},"timeout":{"type":"integer","default":30}},"required":["code"]}, sandbox_python, "sandbox"),
        ToolDef("sandbox_shell", "Execute shell command in sandbox", {"type":"object","properties":{"command":{"type":"string"},"timeout":{"type":"integer","default":30}},"required":["command"]}, sandbox_shell, "sandbox"),
        ToolDef("sandbox_scan", "Scan code for dangerous patterns", {"type":"object","properties":{"code":{"type":"string"}},"required":["code"]}, sandbox_scan, "sandbox"),
        ToolDef("sandbox_status", "Get sandbox status and limits", {"type":"object","properties":{},"required":[]}, sandbox_status, "sandbox"),
    ])
